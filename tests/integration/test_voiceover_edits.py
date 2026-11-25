from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from content_foundry.agents import voiceover as voiceover_mod
from content_foundry.cli import app
from content_foundry.models import RunState, VoiceoverAsset
from content_foundry.notifications import NullNotifier
from content_foundry.pipeline.orchestrator import Orchestrator
from content_foundry.production.captions import build_srt


@pytest.mark.parametrize("replacement", [False, True])
def test_audio_edit_rerender_refreshes_timing_without_visual_generation(
    settings, sample_signals, fakes, monkeypatch, replacement
):
    duration = 2.0

    def decode(chunks, *, gaps=None):
        return [duration] * len(chunks), b"joined"

    monkeypatch.setattr(voiceover_mod, "_decode_concat", decode)
    provider = fakes.TTS(with_timings=True)
    renderer = fakes.Render()
    orch = Orchestrator(
        settings,
        notifier=NullNotifier(),
        dry_run=True,
        llm_provider=fakes.LLM(),
        sources=[fakes.DataSource("adzuna", sample_signals)],
        tts_provider=provider,
        image_provider=None,
        render_backend=renderer,
    )
    result = orch.run(from_stage="fetch", to_stage="render", niche="tech")
    root = Path(result.artifacts["voiceover"]).parent
    artifact_path = root / "voiceover.json"
    old = VoiceoverAsset.model_validate_json(artifact_path.read_text(encoding="utf-8"))
    visuals_before = Path(result.artifacts["visuals"]).read_bytes()
    assets_before = {
        path: path.read_bytes()
        for path in (root / "assets").rglob("*")
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    }
    calls_before = provider.calls
    captions = root / json.loads(visuals_before)["captions_path"]
    captions.write_text("Creator-edited subtitles", encoding="utf-8")
    orch.run(run_id=result.run_id, from_stage="render", to_stage="render", force=True)
    assert captions.read_text(encoding="utf-8") == "Creator-edited subtitles"
    duration = 3.0
    if replacement:
        (root / old.scene_audio[0].path).write_bytes(b"replacement")
    else:
        edited = orch.run(
            run_id=result.run_id,
            from_stage="voiceover",
            to_stage="voiceover",
            voiceover_scenes={old.scene_audio[0].scene_index},
        )
        assert edited.final_state == RunState.VOICED
        assert provider.calls == calls_before + 1
    with patch.object(renderer, "render", wraps=renderer.render) as render:
        rerendered = orch.run(
            run_id=result.run_id, from_stage="render", to_stage="render", force=True
        )
    assert rerendered.final_state == RunState.RENDERED
    updated = VoiceoverAsset.model_validate_json(artifact_path.read_text(encoding="utf-8"))
    assert updated.duration_sec > old.duration_sec
    assert render.call_args.kwargs["segments"][1].start == updated.scene_timings[1].start
    vis = json.loads(visuals_before)
    assert (root / vis["captions_path"]).read_text(encoding="utf-8") == build_srt(
        updated.word_timings
    )
    assert Path(result.artifacts["visuals"]).read_bytes() == visuals_before
    assert {path: path.read_bytes() for path in assets_before} == assets_before
    assert provider.calls == calls_before + (0 if replacement else 1)
    before = artifact_path.read_bytes()
    with patch.object(voiceover_mod, "_decode_concat") as decoder:
        orch.run(run_id=result.run_id, from_stage="render", to_stage="render", force=True)
        decoder.assert_not_called()
    assert artifact_path.read_bytes() == before


def test_voiceover_cli_scene_selection():
    with patch("content_foundry.cli._run") as run:
        result = CliRunner().invoke(
            app, ["voiceover", "--run-id", "R", "--scene", "0", "--scene", "3"]
        )
        assert result.exit_code == 0, result.output
        assert run.call_args.kwargs["voiceover_scenes"] == {0, 3}
        result = CliRunner().invoke(app, ["voiceover", "--scene", "0"])
        assert result.exit_code == 2
        assert "requires --run-id" in result.output
        assert run.call_count == 1
