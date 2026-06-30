"""Agent 7 (Publisher) tests: the disclosure gate can never go public unconfirmed (Ch. 13, test #6)."""

from __future__ import annotations

from pathlib import Path

from content_foundry.agents import Publisher
from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.models import Provenance, SceneVisual, VideoAsset, VisualPackage
from content_foundry.providers.youtube import DryRunPublisher


class _FakePub:
    name = "fake"

    def __init__(self, disclosure: bool):
        self._disclosure = disclosure
        self.calls: list = []

    def upload(self, **kwargs) -> str:
        self.calls.append(("upload", kwargs))
        return "vid123"

    def set_thumbnail(self, video_id, thumbnail_path) -> None:
        self.calls.append(("thumb", video_id))

    def set_privacy(self, video_id, privacy_status) -> None:
        self.calls.append(("privacy", privacy_status))

    def try_set_disclosure(self, video_id) -> bool:
        return self._disclosure

    def video_url(self, video_id) -> str:
        return f"https://youtu.be/{video_id}"


def _video() -> VideoAsset:
    return VideoAsset(
        run_id="R", video_path="assets/video.mp4", duration_sec=10.0, resolution="1920x1080",
        fps=30, backend="fake", has_captions=True, file_size_bytes=7,
        provenance=Provenance(produced_by="renderer"),
    )


def _visuals() -> VisualPackage:
    return VisualPackage(
        run_id="R", thumbnail_path="assets/thumbnail.png", thumbnail_text="t",
        captions_path="assets/captions.srt", visual_style="clean", scenes=[],
        provenance=Provenance(produced_by="visuals"),
    )


def test_never_public_without_disclosure(monkeypatch, good_script):
    monkeypatch.setenv("PUBLISH_MODE", "auto")
    monkeypatch.setenv("YOUTUBE_PRIVACY_STATUS", "public")
    monkeypatch.setenv("REQUIRE_MANUAL_DISCLOSURE_BEFORE_PUBLIC", "false")
    reset_settings_cache()
    settings = get_settings()

    result = Publisher(settings, _FakePub(disclosure=False)).run(
        "R", _video(), good_script, _visuals(), run_root=Path(".")
    )
    assert result.privacy_status == "private"  # forced Private
    assert result.upload_status == "pending_manual_disclosure"
    assert result.disclosure_set is False
    assert result.privacy_status != "public"


def test_dry_run_produces_private_draft(settings, good_script):
    result = Publisher(settings, DryRunPublisher()).run(
        "R", _video(), good_script, _visuals(), run_root=Path(".")
    )
    assert result.upload_status == "uploaded"
    assert result.privacy_status == "private"
    assert result.youtube_video_id


def _visuals_with_scenes(duration: float) -> VisualPackage:
    return VisualPackage(
        run_id="R", thumbnail_path="assets/thumbnail.png", thumbnail_text="t",
        captions_path="assets/captions.srt", visual_style="clean",
        scenes=[SceneVisual(scene_index=i, kind="image", path=f"assets/scenes/scene_{i}.png",
                            source="card", prompt_or_query="p", duration_sec=duration)
                for i in range(3)],
        provenance=Provenance(produced_by="visuals"),
    )


def test_seo_optimizes_metadata_and_keeps_disclosure(settings, good_script):
    good_script.title_options = ["Best Career Advice"]  # yearless => optimizer stamps the year
    pub = _FakePub(disclosure=True)
    result = Publisher(settings, pub).run(
        "R", _video(), good_script, _visuals_with_scenes(12.0), run_root=Path(".")
    )
    _, kwargs = next(c for c in pub.calls if c[0] == "upload")
    year = settings.effective_content_year
    assert kwargs["title"] == f"Best Career Advice ({year})"  # time-boxed
    assert all(t == t.lower() for t in kwargs["tags"])  # normalised
    assert "tech careers" in kwargs["tags"]  # niche seeded
    assert "Chapters:" in kwargs["description"]  # 3 x 12s scenes qualify
    assert "synthetic" in kwargs["description"].lower()  # disclosure preserved
    assert result.chosen_title == kwargs["title"]


def test_seo_disabled_uses_raw_metadata(monkeypatch, good_script):
    monkeypatch.setenv("SEO_OPTIMIZE_ENABLED", "false")
    reset_settings_cache()
    settings = get_settings()
    pub = _FakePub(disclosure=True)
    Publisher(settings, pub).run("R", _video(), good_script, _visuals(), run_root=Path("."))
    _, kwargs = next(c for c in pub.calls if c[0] == "upload")
    assert kwargs["title"] == good_script.title_options[0]  # untouched
    assert kwargs["tags"] == good_script.tags
    assert "synthetic" in kwargs["description"].lower()  # disclosure still enforced
