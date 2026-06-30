"""Create all database tables (idempotent). See Ch. 5.4."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from career_engine.config import get_settings  # noqa: E402
from career_engine.persistence import init_db, make_engine  # noqa: E402


def main() -> None:
    settings = get_settings()
    init_db(make_engine(settings.database_url))
    print(f"Initialised database at {settings.database_url}")


if __name__ == "__main__":
    main()
