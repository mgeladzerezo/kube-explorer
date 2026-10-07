import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.orm import Session, sessionmaker

from app.api import router
from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.diagnosis.baseline import KeywordBaselineModel
from app.diagnosis.evidence import EvidenceCollector
from app.diagnosis.llm import AnthropicModel, DiagnosisModel
from app.diagnosis.service import Diagnoser
from app.kube.fake import load_capture_tree
from app.kube.reader import ClusterReader
from app.metrics import HTTP_REQUESTS
from app.watch.hub import Hub
from app.watch.manager import WatchManager
from app.watch.view import ClusterView


def build_reader(settings: Settings) -> ClusterReader:
    if settings.kube_mode == "fixture":
        return load_capture_tree(Path(settings.kube_fixture_dir))
    # Imported here so fixture mode and the tests never need cluster credentials.
    from app.kube.real import KubernetesReader

    return KubernetesReader.from_environment(in_cluster=settings.kube_mode == "in-cluster")


def build_model(settings: Settings) -> DiagnosisModel:
    if settings.llm_mode == "fake":
        return KeywordBaselineModel()
    return AnthropicModel(
        client=anthropic.Anthropic(),
        model=settings.anthropic_model,
        max_tokens=settings.anthropic_max_tokens,
        effort=settings.anthropic_effort,
        refusal_fallback=settings.anthropic_refusal_fallback,
    )


def create_app(
    settings: Settings | None = None,
    reader: ClusterReader | None = None,
    model: DiagnosisModel | None = None,
    session_factory: sessionmaker[Session] | None = None,
    start_watchers: bool = True,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager = WatchManager(app.state.reader, app.state.view, settings.watch_timeout_seconds)
        if start_watchers:
            manager.start(asyncio.get_running_loop())
        yield
        manager.stop()

    app = FastAPI(title="kube-explorer", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    hub = Hub()
    app.state.hub = hub
    app.state.view = ClusterView(hub.publish)
    app.state.reader = reader or build_reader(settings)
    app.state.session_factory = session_factory or make_session_factory(
        make_engine(settings.database_url)
    )
    app.state.diagnoser = Diagnoser(
        EvidenceCollector(app.state.reader, settings.log_tail_lines),
        model or build_model(settings),
    )

    @app.middleware("http")
    async def count_requests(request: Request, call_next):
        response = await call_next(request)
        route = request.scope.get("route")
        # The route template, not the raw path, keeps label cardinality bounded.
        path = route.path if route else "unmatched"
        HTTP_REQUESTS.labels(request.method, path, response.status_code).inc()
        return response

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(router)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def app_factory() -> FastAPI:
    return create_app()
