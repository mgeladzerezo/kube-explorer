from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class DiagnosisRecord(Base):
    __tablename__ = "diagnoses"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    target_kind: Mapped[str] = mapped_column(String(32))
    namespace: Mapped[str] = mapped_column(String(253), index=True)
    name: Mapped[str] = mapped_column(String(253))
    model: Mapped[str] = mapped_column(String(64))
    top_category: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model: Mapped[str] = mapped_column(String(64))
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)

    results: Mapped[list["EvalResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="EvalResult.scenario_id"
    )


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True)
    scenario_id: Mapped[str] = mapped_column(String(64))
    expected_category: Mapped[str] = mapped_column(String(64))
    predicted_category: Mapped[str | None] = mapped_column(String(64))
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    diagnosis: Mapped[dict | None] = mapped_column(JSON)

    run: Mapped[EvalRun] = relationship(back_populates="results")
