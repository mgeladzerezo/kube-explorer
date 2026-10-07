import asyncio
import contextlib
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.db import session_scope
from app.diagnosis.evidence import Evidence, Target, TargetKind, TargetNotFound
from app.diagnosis.llm import ModelError
from app.diagnosis.redact import RedactionError, redact_text
from app.diagnosis.schema import Diagnosis, DiagnosisParseError
from app.diagnosis.service import Diagnoser
from app.kube.reader import ClusterReader, LogUnavailable
from app.models import DiagnosisRecord, EvalRun
from app.watch.hub import Hub
from app.watch.view import ClusterView

router = APIRouter()

# Sent when a client is dropped for falling behind; the browser reconnects.
WS_TRY_AGAIN_LATER = 1013


def get_session(request: Request) -> Iterator[Session]:
    yield from session_scope(request.app.state.session_factory)


def get_reader(request: Request) -> ClusterReader:
    return request.app.state.reader


def get_diagnoser(request: Request) -> Diagnoser:
    return request.app.state.diagnoser


SessionDep = Annotated[Session, Depends(get_session)]


class DiagnosisOut(BaseModel):
    id: int
    created_at: datetime
    target: Target
    model: str
    diagnosis: Diagnosis
    evidence: Evidence | None = None


class EvalResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scenario_id: str
    expected_category: str
    predicted_category: str | None
    passed: bool
    error: str | None
    latency_ms: int


class EvalRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    finished_at: datetime | None
    model: str
    total: int
    passed: int


class EvalRunDetail(EvalRunOut):
    results: list[EvalResultOut]


class LogOut(BaseModel):
    text: str
    available: bool


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(session: SessionDep) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ready"}


@router.get("/api/cluster")
async def cluster(request: Request) -> dict[str, Any]:
    view: ClusterView = request.app.state.view
    return view.snapshot()


@router.websocket("/ws/cluster")
async def cluster_stream(websocket: WebSocket) -> None:
    hub: Hub = websocket.app.state.hub
    view: ClusterView = websocket.app.state.view
    await websocket.accept()
    # No await between these two lines, so no change can fall between snapshot and stream.
    subscription = hub.subscribe()
    snapshot = view.snapshot()

    async def wait_for_close() -> None:
        with contextlib.suppress(WebSocketDisconnect):
            while True:
                await websocket.receive_text()

    closed = asyncio.create_task(wait_for_close())
    try:
        await websocket.send_json(snapshot)
        while True:
            pending = asyncio.create_task(subscription.next())
            await asyncio.wait({pending, closed}, return_when=asyncio.FIRST_COMPLETED)
            if closed.done():
                pending.cancel()
                return
            message = pending.result()
            if message is None:
                await websocket.close(code=WS_TRY_AGAIN_LATER)
                return
            await websocket.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        return
    finally:
        closed.cancel()
        hub.unsubscribe(subscription)


@router.get("/api/pods/{namespace}/{name}/logs")
def pod_logs(
    namespace: str,
    name: str,
    container: str,
    reader: Annotated[ClusterReader, Depends(get_reader)],
    tail: Annotated[int, Query(ge=1, le=2000)] = 200,
    previous: bool = False,
) -> LogOut:
    try:
        raw = reader.read_log(namespace, name, container, tail, previous)
    except LogUnavailable:
        return LogOut(text="", available=False)
    return LogOut(text=redact_text(raw), available=True)


@router.get("/api/evidence")
def evidence(
    kind: TargetKind,
    namespace: str,
    name: str,
    diagnoser: Annotated[Diagnoser, Depends(get_diagnoser)],
) -> Evidence:
    try:
        return diagnoser.collect(Target(kind=kind, namespace=namespace, name=name))
    except TargetNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/diagnoses", status_code=201)
def create_diagnosis(
    target: Target,
    diagnoser: Annotated[Diagnoser, Depends(get_diagnoser)],
    session: SessionDep,
) -> DiagnosisOut:
    try:
        result = diagnoser.diagnose(target)
    except TargetNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ModelError as exc:
        raise HTTPException(status_code=503 if exc.retryable else 502, detail=str(exc)) from exc
    except DiagnosisParseError as exc:
        raise HTTPException(
            status_code=502, detail=f"The model returned an unusable answer: {exc}"
        ) from exc
    except RedactionError as exc:
        raise HTTPException(
            status_code=500, detail="Evidence failed the redaction check; nothing was sent."
        ) from exc

    record = DiagnosisRecord(
        target_kind=target.kind,
        namespace=target.namespace,
        name=target.name,
        model=result.model,
        top_category=result.diagnosis.top.category.value,
        result=result.diagnosis.model_dump(mode="json"),
    )
    session.add(record)
    session.flush()
    return DiagnosisOut(
        id=record.id,
        created_at=record.created_at,
        target=target,
        model=result.model,
        diagnosis=result.diagnosis,
        evidence=result.evidence,
    )


@router.get("/api/diagnoses")
def list_diagnoses(
    session: SessionDep,
    namespace: str | None = None,
    name: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[DiagnosisOut]:
    query = select(DiagnosisRecord).order_by(DiagnosisRecord.id.desc()).limit(limit)
    if namespace:
        query = query.where(DiagnosisRecord.namespace == namespace)
    if name:
        query = query.where(DiagnosisRecord.name == name)
    return [
        DiagnosisOut(
            id=r.id,
            created_at=r.created_at,
            target=Target(kind=r.target_kind, namespace=r.namespace, name=r.name),
            model=r.model,
            diagnosis=Diagnosis.model_validate(r.result),
        )
        for r in session.scalars(query)
    ]


@router.get("/api/eval/runs")
def list_eval_runs(session: SessionDep) -> list[EvalRunOut]:
    runs = session.scalars(select(EvalRun).order_by(EvalRun.id.desc()).limit(50))
    return [EvalRunOut.model_validate(run) for run in runs]


@router.get("/api/eval/runs/{run_id}")
def get_eval_run(run_id: int, session: SessionDep) -> EvalRunDetail:
    run = session.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="eval run not found")
    return EvalRunDetail.model_validate(run)
