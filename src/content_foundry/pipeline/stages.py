"""Stage registry + start/stop ordering (Ch. 14.2)."""

from __future__ import annotations

STAGES = ["fetch", "generate", "judge", "voiceover", "visuals", "render", "publish"]
PRODUCTION_STAGES = ("voiceover", "visuals", "render", "publish")

# pipeline stage -> artifact key (filename stem family)
STAGE_TO_ARTIFACT = {
    "fetch": "data_brief",
    "generate": "script",
    "judge": "judge_report",
    "voiceover": "voiceover",
    "visuals": "visuals",
    "render": "video",
    "publish": "publish",
}


def validate_stage(name: str) -> str:
    if name not in STAGES:
        raise ValueError(f"Unknown stage {name!r}. Valid: {STAGES}")
    return name


def stages_between(from_stage: str, to_stage: str) -> list[str]:
    validate_stage(from_stage)
    validate_stage(to_stage)
    i, j = STAGES.index(from_stage), STAGES.index(to_stage)
    if i > j:
        raise ValueError(f"from_stage '{from_stage}' is after to_stage '{to_stage}'")
    return STAGES[i : j + 1]
