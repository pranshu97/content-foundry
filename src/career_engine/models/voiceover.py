"""VoiceoverAsset artifact — narration audio + word/scene timings (Ch. 10.4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .provenance import Provenance


class WordTiming(BaseModel):
    word: str
    start: float  # seconds
    end: float


class SceneTiming(BaseModel):
    scene_index: int
    start: float
    end: float


class VoiceoverAsset(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["voiceover"] = "voiceover"
    audio_path: str  # assets/narration.mp3
    duration_sec: float
    sample_rate: int
    voice_id: str
    provider: str  # elevenlabs | openai
    word_timings: list[WordTiming] = Field(default_factory=list)
    scene_timings: list[SceneTiming] = Field(default_factory=list)
    provenance: Provenance
