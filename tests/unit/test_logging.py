"""Per-run log-file tee: a structlog sink appends every event to output/runs/<id>/run.log."""

from __future__ import annotations

from content_foundry.logging import get_logger, set_run_log_file


def test_run_log_file_tees_structured_logs(tmp_path):
    log_path = tmp_path / "run.log"
    set_run_log_file(str(log_path))
    try:
        # Tests pin LOG_LEVEL=ERROR, so log at ERROR to pass the level filter into the sink.
        get_logger(component="unit").error("silent_fallback", frm="google", to="pollinations")
    finally:
        set_run_log_file(None)
    assert log_path.exists()
    body = log_path.read_text(encoding="utf-8")
    assert "silent_fallback" in body and "pollinations" in body  # full event captured as JSON

    # After clearing the path, further logs are NOT written to the file (the tee is off).
    get_logger(component="unit").error("after_clear")
    assert "after_clear" not in log_path.read_text(encoding="utf-8")
