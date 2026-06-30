"""Insert a demo run so the dashboard has something to show (Ch. 4.1)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ulid import ULID  # noqa: E402

from career_engine.config import get_settings  # noqa: E402
from career_engine.persistence import (  # noqa: E402
    Repository,
    init_db,
    make_engine,
    make_session_factory,
)


def main() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    repo = Repository(make_session_factory(engine))

    run_id = str(ULID())
    repo.create_run(run_id, "junior developer hiring", "JUDGED")
    attempt_id = str(ULID())
    repo.add_attempt(attempt_id, run_id, 1, "contrarian", False)
    repo.update_attempt(attempt_id, verdict="PASS", insight_score=8.0, weighted_total=8.4)
    repo.record_template_usage(run_id, "contrarian", "The bottom rung is gone.")
    repo.update_run(run_id, state="APPROVED", final_verdict="PASS", approved_attempt_id=attempt_id)
    print(f"Seeded demo run {run_id}")


if __name__ == "__main__":
    main()
