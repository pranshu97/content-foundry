"""Streamlit human-review dashboard (Ch. 20). Read-mostly; launched via ``content-foundry dashboard``."""

from __future__ import annotations

import sys
from pathlib import Path

# Make the src package importable when run via `streamlit run dashboard/app.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st  # noqa: E402

from content_foundry.config import get_settings  # noqa: E402
from content_foundry.models import JudgeReport  # noqa: E402
from content_foundry.persistence import (  # noqa: E402
    Repository,
    init_db,
    make_engine,
    make_session_factory,
)
from content_foundry.pipeline.artifacts import load_model, run_paths  # noqa: E402


def _repo() -> Repository:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    return Repository(make_session_factory(engine))


def main() -> None:
    st.set_page_config(page_title="Content Foundry — Review", layout="wide")
    st.title("Content Foundry — Human Review Dashboard")
    settings = get_settings()
    repo = _repo()

    runs = repo.list_runs(limit=100)
    st.subheader("Runs")
    st.table(
        [
            {
                "run_id": r.run_id,
                "state": r.state,
                "verdict": r.final_verdict,
                "created_at": r.created_at,
            }
            for r in runs
        ]
    )

    # Compliance panel: drafts pending disclosure.
    pending = repo.pending_disclosure_runs()
    if pending:
        st.subheader("⚠️ Compliance — drafts pending disclosure")
        st.table(
            [
                {
                    "run_id": p.run_id,
                    "video_url": p.video_url,
                    "privacy": p.privacy_status,
                    "status": p.upload_status,
                }
                for p in pending
            ]
        )

    st.subheader("Run detail")
    run_id = st.text_input("run_id")
    if run_id:
        path = run_paths(run_id, settings.output_dir).artifact("judge_report")
        if path.exists():
            jr: JudgeReport = load_model(JudgeReport, path, expected_stage="judge_report")
            st.metric("Verdict", jr.verdict.value)
            st.metric("Weighted total", jr.weighted_total)
            st.metric("Insight", jr.insight_score)
            st.table(
                [
                    {"dimension": d.dimension, "score": d.score, "passed": d.passed}
                    for d in jr.scores
                ]
            )
            video = run_paths(run_id, settings.output_dir).assets / "video.mp4"
            if video.exists():
                st.video(str(video))
        else:
            st.info("No judge_report for this run yet.")


if __name__ == "__main__":
    main()
