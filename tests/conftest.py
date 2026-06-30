"""Shared test fixtures: hermetic env, in-memory DB, and Fakes behind every protocol (Ch. 22.3).

No test touches the real network or any vendor SDK.
"""

from __future__ import annotations

import json

import pytest

from career_engine.errors import DataSourceError
from career_engine.models import (
    DataBrief,
    NormalizedSignal,
    Provenance,
    SceneCue,
    Script,
    WordTiming,
    utcnow,
)
from career_engine.providers.base import LLMResponse

# --------------------------------------------------------------------------- env
_BASE_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic",
    "OPENAI_API_KEY": "test-openai",
    "PRIMARY_PROVIDER": "anthropic",
    "FALLBACK_PROVIDER": "openai",
    "ADZUNA_APP_ID": "test-id",
    "ADZUNA_APP_KEY": "test-key",
    "NEWSAPI_KEY": "test-news",
    "ENABLED_SOURCES": "adzuna,layoffs,news",
    "LAYOFFS_FEED_URL": "https://example.com/layoffs.rss",
    "TTS_PROVIDER": "elevenlabs",
    "ELEVENLABS_API_KEY": "test-eleven",
    "IMAGE_PROVIDER": "none",
    "PEXELS_API_KEY": "",
    "RENDER_BACKEND": "ffmpeg",
    "PUBLISH_MODE": "draft",
    "YOUTUBE_PRIVACY_STATUS": "private",
    "NOTIFY_ENABLED": "true",
    "NOTIFIER": "none",
    "JUDGE_MODE": "hybrid",
    "MAX_REVISIONS": "3",
    "MIN_FACTS": "3",
    "LOG_LEVEL": "ERROR",
    "LOG_FORMAT": "console",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    """Hermetic per-test environment pointing storage at a tmp dir."""
    from career_engine.config import reset_settings_cache

    for key, value in _BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output" / "runs"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def settings():
    from career_engine.config import get_settings

    return get_settings()


@pytest.fixture
def repo():
    from career_engine.persistence import Repository, init_db, make_engine, make_session_factory

    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    return Repository(make_session_factory(engine))


# ----------------------------------------------------------------- canned JSON
DEFAULT_SCRIPT_JSON = {
    "title_options": [
        "The Junior Dev Job Is Disappearing (Do This Instead)",
        "Why 'Get an Entry-Level Coding Job' Is 2026's Worst Advice",
    ],
    "hook": "Entry-level coding postings just dropped 31% in a year — the bottom rung is gone.",
    "scenes": [
        {
            "index": 0,
            "narration": "Everyone says just get a junior dev job, but postings fell 31% this year.",
            "on_screen_text": "Junior postings -31% YoY",
            "b_roll_keywords": ["closed door", "job board"],
            "fact_ref": 0,
        },
        {
            "index": 1,
            "narration": "Meanwhile median pay for those roles sits around $112,000 — counterintuitive.",
            "on_screen_text": "$112,000 median",
            "b_roll_keywords": ["salary chart"],
            "fact_ref": 1,
        },
        {
            "index": 2,
            "narration": "So actually target adjacent roles; start by shipping a portfolio this week.",
            "on_screen_text": "Build a portfolio",
            "b_roll_keywords": ["laptop code"],
            "fact_ref": 2,
        },
    ],
    "cta": "Subscribe for data-backed moves the generic channels won't tell you.",
    "description": "Grounded in Adzuna data. Note: this video uses AI-altered/synthetic content.",
    "tags": ["tech careers", "junior developer", "2026 job market"],
    "thumbnail_concept": "Broken ladder, bold text BOTTOM RUNG GONE",
    "word_count": 0,
    "grounded_fact_refs": [0, 1, 2],
    "synthetic_disclosure": True,
}

DEFAULT_JUDGE_JSON = {
    "actionability": {"justification": "Concrete steps.", "evidence": "ship a portfolio", "score_1_5": 4},
    "insight": {"justification": "Reframes entry path.", "evidence": "bottom rung is gone", "score_1_5": 4},
}

GENERIC_SCRIPT_JSON = {
    "title_options": ["How To Succeed In Your Career"],
    "hook": "Want to succeed? You just need to work hard and network more.",
    "scenes": [
        {
            "index": 0,
            "narration": "Work hard and stay positive. Network more and update your resume.",
            "on_screen_text": "Work hard",
            "b_roll_keywords": ["office"],
            "fact_ref": None,
        },
        {
            "index": 1,
            "narration": "Believe in yourself and follow your passion. Hustle every day.",
            "on_screen_text": "Believe",
            "b_roll_keywords": ["sunrise"],
            "fact_ref": None,
        },
    ],
    "cta": "Like and subscribe.",
    "description": "Generic advice. Note: this video uses AI-altered/synthetic content.",
    "tags": ["career"],
    "thumbnail_concept": "Person smiling",
    "word_count": 0,
    "grounded_fact_refs": [],
    "synthetic_disclosure": True,
}


# --------------------------------------------------------------------- Fakes
class FakeLLMProvider:
    """Returns scripted, schema-valid JSON per stage; counts calls for assertions."""

    name = "fake"

    def __init__(self, script_json=None, judge_json=None, *, bad_then_good=False):
        self.calls: list[dict] = []
        self.script_json = script_json if script_json is not None else DEFAULT_SCRIPT_JSON
        self.judge_json = judge_json if judge_json is not None else DEFAULT_JUDGE_JSON
        self._bad_then_good = bad_then_good
        self._served_bad = False

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def complete(self, prompt, *, system=None, temperature=0.7, max_tokens=4096, model=None):
        self.calls.append({"system": system, "prompt": prompt, "model": model})
        system_l = (system or "").lower()
        if "judge" in system_l:
            text = json.dumps(self.judge_json)
        elif self._bad_then_good and not self._served_bad:
            self._served_bad = True
            text = "this is not json"
        else:
            text = self.script_json if isinstance(self.script_json, str) else json.dumps(self.script_json)
        return LLMResponse(text=text, model=model or "fake", provider="fake",
                           prompt_tokens=120, completion_tokens=240)


class FakeDataSource:
    def __init__(self, name: str, signals: list[NormalizedSignal]):
        self.name = name
        self._signals = signals

    def fetch(self) -> list[NormalizedSignal]:
        return list(self._signals)


class FlakyDataSource:
    def __init__(self, name: str = "flaky"):
        self.name = name

    def fetch(self) -> list[NormalizedSignal]:
        raise DataSourceError("simulated source outage")


class FakeTTS:
    name = "fake-tts"
    sample_rate = 16000

    def __init__(self, with_timings: bool = False):
        self._with_timings = with_timings
        self.calls = 0

    def synthesize(self, text: str):
        self.calls += 1
        audio = b"\x00" * 32
        if not self._with_timings:
            return audio, None
        words = text.split()
        timings = [WordTiming(word=w, start=float(i), end=float(i) + 0.5) for i, w in enumerate(words)]
        return audio, timings


class FakeImageProvider:
    name = "fake-image"

    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str, size: str = "1024x1024") -> bytes:
        self.calls += 1
        # 1x1 PNG.
        import base64

        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )


class FakeBrollClient:
    enabled = True

    def __init__(self, url: str | None = "https://example.com/clip.mp4"):
        self._url = url

    def search(self, query: str) -> str | None:
        return self._url

    def download(self, url: str) -> bytes:
        return b"FAKEVIDEO"


class FakeRenderBackend:
    name = "fake-render"

    def __init__(self):
        self.calls = 0

    def render(self, *, segments, audio_path, captions_path, output_path, resolution, fps,
               burn_captions=True) -> str:
        self.calls += 1
        from pathlib import Path

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"FAKEMP4")
        return output_path


# --------------------------------------------------------------- data fixtures
@pytest.fixture
def sample_signals() -> list[NormalizedSignal]:
    now = utcnow()
    return [
        NormalizedSignal(source="adzuna", kind="posting_trend",
                         title="Open junior developer postings", value="31", unit="% YoY decline",
                         observed_at=now, url="https://adzuna.example/1",
                         raw={"snippet": "junior software engineer postings -31% YoY"}),
        NormalizedSignal(source="adzuna", kind="salary",
                         title="Junior developer", value="$112,000", unit="per year",
                         observed_at=now, url="https://adzuna.example/2"),
        NormalizedSignal(source="layoffs", kind="layoff",
                         title="BigCo cuts 1200 engineering roles", value="1200", unit="employees",
                         observed_at=now, url="https://layoffs.example/3"),
        NormalizedSignal(source="news", kind="news",
                         title="Hiring slows for entry-level tech", value=None, unit=None,
                         observed_at=now, url="https://news.example/4",
                         raw={"snippet": "entry-level tech hiring slows"}),
    ]


@pytest.fixture
def data_brief(sample_signals) -> DataBrief:
    from career_engine.agents import distill

    return DataBrief(
        run_id="TESTRUN",
        niche="tech careers",
        topic_seed="junior developer hiring",
        key_facts=distill.build_key_facts(sample_signals),
        content_angles=distill.build_angles(sample_signals),
        coverage={"adzuna": True, "layoffs": True, "news": True},
        gaps=[],
        provenance=Provenance(produced_by="data_fetcher"),
    )


def _script_from_json(payload: dict, *, run_id="TESTRUN", template_id="contrarian") -> Script:
    scenes = [
        SceneCue(
            index=sc["index"], narration=sc["narration"], on_screen_text=sc.get("on_screen_text"),
            b_roll_keywords=sc.get("b_roll_keywords", []), fact_ref=sc.get("fact_ref"),
        )
        for sc in payload["scenes"]
    ]
    return Script(
        run_id=run_id, template_id=template_id, title_options=payload["title_options"],
        hook=payload["hook"], scenes=scenes, cta=payload["cta"], description=payload["description"],
        tags=payload["tags"], thumbnail_concept=payload["thumbnail_concept"],
        word_count=sum(len(s.narration.split()) for s in scenes),
        grounded_fact_refs=payload["grounded_fact_refs"], synthetic_disclosure=True,
        provenance=Provenance(produced_by="script_generator", model="fake"),
    )


@pytest.fixture
def good_script() -> Script:
    return _script_from_json(DEFAULT_SCRIPT_JSON)


@pytest.fixture
def generic_script() -> Script:
    return _script_from_json(GENERIC_SCRIPT_JSON)


@pytest.fixture
def make_script():
    return _script_from_json


@pytest.fixture
def fakes():
    """A namespace of Fake classes so tests need not import from conftest."""
    import types

    return types.SimpleNamespace(
        LLM=FakeLLMProvider,
        DataSource=FakeDataSource,
        FlakyDataSource=FlakyDataSource,
        TTS=FakeTTS,
        Image=FakeImageProvider,
        Broll=FakeBrollClient,
        Render=FakeRenderBackend,
    )


@pytest.fixture
def script_payload():
    import copy

    return copy.deepcopy(DEFAULT_SCRIPT_JSON)


@pytest.fixture
def generic_payload():
    import copy

    return copy.deepcopy(GENERIC_SCRIPT_JSON)

