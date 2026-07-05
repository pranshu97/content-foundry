"""Pydantic artifact + domain schemas. ``models`` is a leaf package (no internal deps)."""

from __future__ import annotations

from .data_brief import Citation, ContentAngle, DataBrief, KeyFact
from .judge_report import DimensionScore, JudgeReport, Verdict
from .provenance import Provenance, utcnow
from .publish import PublishResult
from .run import Attempt, Run, RunResult, RunState
from .script import SceneCue, Script
from .signals import SIGNAL_KINDS, NormalizedSignal, RawSignal
from .video import VideoAsset
from .visuals import SceneVisual, VisualPackage, VisualShot
from .voiceover import SceneTiming, VoiceoverAsset, WordTiming

__all__ = [
    "Provenance",
    "utcnow",
    "RawSignal",
    "NormalizedSignal",
    "SIGNAL_KINDS",
    "Citation",
    "KeyFact",
    "ContentAngle",
    "DataBrief",
    "SceneCue",
    "Script",
    "Verdict",
    "DimensionScore",
    "JudgeReport",
    "WordTiming",
    "SceneTiming",
    "VoiceoverAsset",
    "SceneVisual",
    "VisualPackage",
    "VisualShot",
    "VideoAsset",
    "PublishResult",
    "RunState",
    "Attempt",
    "Run",
    "RunResult",
]
