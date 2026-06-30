"""SQLAlchemy 2.0 ORM tables mirroring the Ch. 5 DDL.

Two documented, spec-driven extensions beyond the raw DDL:
* ``template_usage.hook`` — Ch. 9.4 requires the Judge to read "stored hooks" for fatigue shingles.
* ``engine_meta`` — Ch. 25.5 requires the rolling monthly spend total to be persisted in the DB.
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..models import utcnow


def _now_iso() -> str:
    return utcnow().isoformat()


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    topic_seed: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=False)
    final_verdict: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_attempt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_now_iso, onupdate=_now_iso)


class AttemptRow(Base):
    __tablename__ = "attempts"

    attempt_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    template_id: Mapped[str] = mapped_column(String, nullable=False)
    forced_shift: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verdict: Mapped[str | None] = mapped_column(String, nullable=True)
    insight_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    weighted_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_now_iso)

    __table_args__ = (
        Index("idx_attempts_run", "run_id"),
        Index("uq_attempt_number", "run_id", "attempt_number", unique=True),
    )


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("attempts.attempt_id", ondelete="CASCADE"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String, nullable=False)
    schema_version: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    provenance: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_now_iso)

    __table_args__ = (Index("idx_artifacts_run_stage", "run_id", "stage"),)


class RubricScoreRow(Base):
    __tablename__ = "rubric_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_id: Mapped[str] = mapped_column(
        String, ForeignKey("attempts.attempt_id", ondelete="CASCADE"), nullable=False
    )
    dimension: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    passed: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class TemplateUsageRow(Base):
    __tablename__ = "template_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[str] = mapped_column(String, nullable=False)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)  # Ch. 9.4 stored hooks
    used_at: Mapped[str] = mapped_column(String, default=_now_iso)

    __table_args__ = (Index("idx_template_usage_time", "used_at"),)


class SignalCacheRow(Base):
    __tablename__ = "signal_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    fetched_at: Mapped[str] = mapped_column(String, default=_now_iso)
    payload: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("idx_signal_cache_source", "source", "fetched_at"),)


class PublishResultRow(Base):
    __tablename__ = "publish_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("attempts.attempt_id", ondelete="CASCADE"), nullable=True
    )
    youtube_video_id: Mapped[str | None] = mapped_column(String, nullable=True)
    video_url: Mapped[str | None] = mapped_column(String, nullable=True)
    privacy_status: Mapped[str] = mapped_column(String, nullable=False)
    disclosure_set: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    upload_status: Mapped[str] = mapped_column(String, nullable=False)
    published_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (Index("idx_publish_run", "run_id"),)


class EngineMetaRow(Base):
    """Tiny key/value store (Ch. 25.5 monthly-spend persistence)."""

    __tablename__ = "engine_meta"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
