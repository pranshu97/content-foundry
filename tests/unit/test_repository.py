"""Repository: the template-fatigue lookback can exclude a run's OWN rows.

Regression guard for the "re-judging one run fails structural-freshness against itself" bug: a run's
repeated generate/judge attempts must not fill the cross-video fatigue window with its own template
and hook (which would zero freshness and block an otherwise-passing script).
"""


def test_recent_template_usage_excludes_current_run(repo):
    repo.create_run("0001", None, "fetched")
    repo.create_run("0002", None, "fetched")
    repo.record_template_usage("0001", "myth_vs_reality", "Everyone gets this wrong.")
    # Run 0002 iterated twice on the same template + hook (the resume/re-judge scenario).
    repo.record_template_usage("0002", "three_step", "Here is the play.")
    repo.record_template_usage("0002", "three_step", "Here is the play.")

    # Without exclusion the window is dominated by run 0002's own repeats.
    ids = repo.recent_template_ids(5)
    assert len(ids) == 3
    assert ids.count("three_step") == 2

    # Excluding run 0002 hides its own rows, so it never sees itself as fatigued.
    assert repo.recent_template_ids(5, exclude_run_id="0002") == ["myth_vs_reality"]
    assert repo.recent_hooks(5, exclude_run_id="0002") == ["Everyone gets this wrong."]

    # A brand-new run still sees the full cross-video history (no exclusion).
    assert set(repo.recent_template_ids(5, exclude_run_id="0003")) == {
        "myth_vs_reality",
        "three_step",
    }
