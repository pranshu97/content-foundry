"""Agent 7 (Publisher) tests: the disclosure gate can never go public unconfirmed (Ch. 13, test #6)."""

from __future__ import annotations

from pathlib import Path

from career_engine.agents import Publisher
from career_engine.config import get_settings, reset_settings_cache
from career_engine.models import Provenance, VideoAsset, VisualPackage
from career_engine.providers.youtube import DryRunPublisher


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
