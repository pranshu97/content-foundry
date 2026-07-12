"""Repository — CRUD + queries over the run/attempt/artifact tables (Ch. 5.3)."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..models import utcnow
from .schema import (
    ArtifactRow,
    AttemptRow,
    EngineMetaRow,
    PublishResultRow,
    RubricScoreRow,
    RunRow,
    SignalCacheRow,
    TemplateUsageRow,
)


class Repository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    # ------------------------------------------------------------------ runs
    def create_run(self, run_id: str, topic_seed: str | None, state: str) -> None:
        with self._sf() as s:
            s.add(RunRow(run_id=run_id, topic_seed=topic_seed, state=state))
            s.commit()

    def get_run(self, run_id: str) -> RunRow | None:
        with self._sf() as s:
            return s.get(RunRow, run_id)

    def update_run(self, run_id: str, **fields: object) -> None:
        with self._sf() as s:
            run = s.get(RunRow, run_id)
            if run is None:
                return
            for key, value in fields.items():
                setattr(run, key, value)
            run.updated_at = utcnow().isoformat()
            s.commit()

    def list_runs(self, limit: int = 20, state: str | None = None) -> list[RunRow]:
        with self._sf() as s:
            stmt = select(RunRow).order_by(RunRow.created_at.desc())
            if state:
                stmt = stmt.where(RunRow.state == state)
            return list(s.scalars(stmt.limit(limit)))

    # --------------------------------------------------------------- attempts
    def add_attempt(
        self, attempt_id: str, run_id: str, attempt_number: int, template_id: str, forced_shift: bool
    ) -> None:
        with self._sf() as s:
            s.add(
                AttemptRow(
                    attempt_id=attempt_id,
                    run_id=run_id,
                    attempt_number=attempt_number,
                    template_id=template_id,
                    forced_shift=int(forced_shift),
                )
            )
            s.commit()

    def update_attempt(self, attempt_id: str, **fields: object) -> None:
        with self._sf() as s:
            row = s.get(AttemptRow, attempt_id)
            if row is None:
                return
            for key, value in fields.items():
                setattr(row, key, value)
            s.commit()

    def get_attempts(self, run_id: str) -> list[AttemptRow]:
        with self._sf() as s:
            stmt = select(AttemptRow).where(AttemptRow.run_id == run_id).order_by(
                AttemptRow.attempt_number
            )
            return list(s.scalars(stmt))

    # -------------------------------------------------------------- artifacts
    def add_artifact(
        self,
        *,
        artifact_id: str,
        run_id: str,
        attempt_id: str | None,
        stage: str,
        schema_version: str,
        path: str,
        content_hash: str,
        provenance: dict | None,
    ) -> None:
        with self._sf() as s:
            s.add(
                ArtifactRow(
                    artifact_id=artifact_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    stage=stage,
                    schema_version=schema_version,
                    path=path,
                    content_hash=content_hash,
                    provenance=json.dumps(provenance) if provenance else None,
                )
            )
            s.commit()

    def latest_artifact(self, run_id: str, stage: str) -> ArtifactRow | None:
        with self._sf() as s:
            stmt = (
                select(ArtifactRow)
                .where(ArtifactRow.run_id == run_id, ArtifactRow.stage == stage)
                .order_by(ArtifactRow.created_at.desc())
                .limit(1)
            )
            return s.scalars(stmt).first()

    # ----------------------------------------------------------- rubric scores
    def add_rubric_scores(self, attempt_id: str, scores: list[dict]) -> None:
        with self._sf() as s:
            for sc in scores:
                s.add(
                    RubricScoreRow(
                        attempt_id=attempt_id,
                        dimension=sc["dimension"],
                        score=sc["score"],
                        weight=sc["weight"],
                        passed=int(sc["passed"]),
                        comment=sc.get("comment"),
                    )
                )
            s.commit()

    # -------------------------------------------------------- template usage
    def record_template_usage(self, run_id: str, template_id: str, hook: str | None) -> None:
        with self._sf() as s:
            s.add(TemplateUsageRow(run_id=run_id, template_id=template_id, hook=hook))
            s.commit()

    def recent_template_usage(
        self, lookback: int, *, exclude_run_id: str | None = None
    ) -> list[TemplateUsageRow]:
        """Most-recent-first template usage rows from the last ``lookback`` records. Pass
        ``exclude_run_id`` to skip a run's OWN rows — template/hook fatigue is a CROSS-video signal,
        so iterating or re-judging a single run must never fail structural-freshness against itself."""
        with self._sf() as s:
            stmt = select(TemplateUsageRow).order_by(TemplateUsageRow.used_at.desc())
            if exclude_run_id is not None:
                stmt = stmt.where(TemplateUsageRow.run_id != exclude_run_id)
            return list(s.scalars(stmt.limit(lookback)))

    def recent_template_ids(self, lookback: int, *, exclude_run_id: str | None = None) -> list[str]:
        return [
            r.template_id
            for r in self.recent_template_usage(lookback, exclude_run_id=exclude_run_id)
        ]

    def recent_hooks(self, lookback: int, *, exclude_run_id: str | None = None) -> list[str]:
        return [
            r.hook
            for r in self.recent_template_usage(lookback, exclude_run_id=exclude_run_id)
            if r.hook
        ]

    # --------------------------------------------------------- signal cache
    def get_cached_signals(self, source: str, ttl_min: int) -> list[dict] | None:
        cutoff = (utcnow() - timedelta(minutes=ttl_min)).isoformat()
        with self._sf() as s:
            stmt = (
                select(SignalCacheRow)
                .where(SignalCacheRow.source == source, SignalCacheRow.fetched_at >= cutoff)
                .order_by(SignalCacheRow.fetched_at.desc())
                .limit(1)
            )
            row = s.scalars(stmt).first()
            return json.loads(row.payload) if row else None

    def put_cached_signals(self, source: str, payload: list[dict]) -> None:
        with self._sf() as s:
            s.add(SignalCacheRow(source=source, payload=json.dumps(payload)))
            s.commit()

    # ------------------------------------------------------- publish results
    def add_publish_result(
        self,
        *,
        run_id: str,
        attempt_id: str | None,
        youtube_video_id: str | None,
        video_url: str | None,
        privacy_status: str,
        disclosure_set: bool,
        upload_status: str,
        published_at: str | None,
    ) -> None:
        with self._sf() as s:
            s.add(
                PublishResultRow(
                    run_id=run_id,
                    attempt_id=attempt_id,
                    youtube_video_id=youtube_video_id,
                    video_url=video_url,
                    privacy_status=privacy_status,
                    disclosure_set=int(disclosure_set),
                    upload_status=upload_status,
                    published_at=published_at,
                )
            )
            s.commit()

    def pending_disclosure_runs(self) -> list[PublishResultRow]:
        with self._sf() as s:
            stmt = select(PublishResultRow).where(
                (PublishResultRow.disclosure_set == 0)
                | (PublishResultRow.upload_status == "pending_manual_disclosure")
            )
            return list(s.scalars(stmt))

    # --------------------------------------------------------------- meta kv
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._sf() as s:
            row = s.get(EngineMetaRow, key)
            return row.value if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._sf() as s:
            row = s.get(EngineMetaRow, key)
            if row:
                row.value = value
            else:
                s.add(EngineMetaRow(key=key, value=value))
            s.commit()
