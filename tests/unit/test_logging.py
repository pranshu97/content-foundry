"""Per-run log-file tee: a structlog sink appends every event to output/runs/<id>/run.log."""

from __future__ import annotations

from content_foundry.logging import configure_logging, get_logger, set_run_log_file


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


def test_a_quiet_console_still_writes_info_events_to_the_run_log(tmp_path, capsys):
    """The bug that hid every silent fallback for months: no run ever produced a run.log.

    The CLI passes a progress reporter, so the orchestrator configured logging at ERROR to stop log
    lines fighting the spinner. structlog's ``wrapper_class`` filters BEFORE the processor chain, so
    that also silenced the file sink -- and since a successful run logs nothing at ERROR, the file
    was never even created. The console level has to be applied at the RENDERER instead.
    """
    configure_logging(level="INFO", fmt="console", console_level="ERROR")
    log_path = tmp_path / "run.log"
    set_run_log_file(str(log_path))
    try:
        get_logger(component="unit").info("silent_fallback", frm="google", to="pollinations")
    finally:
        set_run_log_file(None)
        configure_logging()  # restore the suite's pinned configuration

    assert log_path.exists(), "an INFO event never reached the run log"
    assert "silent_fallback" in log_path.read_text(encoding="utf-8")
    assert "silent_fallback" not in capsys.readouterr().out  # ...while stdout stayed quiet
