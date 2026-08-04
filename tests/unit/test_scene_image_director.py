"""Scene Image Director (Agent 5.7): the LLM writes a vivid image prompt for a shot that got no stock
B-roll, so the gap is filled with a bespoke, on-topic image instead of a borrowed off-topic clip."""

from __future__ import annotations

from content_foundry.agents.scene_image_director import SceneImageDirector
from content_foundry.config import get_settings, reset_settings_cache


def _settings(monkeypatch, *, enabled: bool = True):
    monkeypatch.setenv("SCENE_IMAGE_DIRECTOR_ENABLED", "true" if enabled else "false")
    reset_settings_cache()
    return get_settings()


def test_scene_image_director_writes_a_prompt_per_shot(monkeypatch, fakes):
    settings = _settings(monkeypatch)
    payload = {
        "shots": [
            {"shot": 0, "prompt": "a warm cinematic close-up of two hands"},
            {"shot": 1, "prompt": "a lone analyst looking uncertain"},
        ]
    }
    llm = fakes.LLM(script_json=payload)
    out = SceneImageDirector(settings, llm).compose(
        shots=[(0, "You shake on the offer."), (1, "Then the doubt creeps in.")],
        narration="You shake on the offer, then the doubt creeps in.",
    )
    assert out[0].startswith("a warm cinematic close-up")
    assert out[1].startswith("a lone analyst")
    assert llm.call_count == 1
    # The scene's narration is handed to the model so the generated image stays on-topic + in-world.
    assert "doubt creeps in" in llm.calls[-1]["system"]


def test_shot_indexes_are_kept_distinct_even_when_lines_repeat(monkeypatch, fakes):
    """Keying by index (not by the beat text it used to use) means two shots can never collapse onto
    a single image just because they share wording."""
    settings = _settings(monkeypatch)
    payload = {
        "shots": [
            {"shot": 2, "prompt": "second framing"},
            {"shot": 5, "prompt": "fifth framing"},
        ]
    }
    out = SceneImageDirector(settings, fakes.LLM(script_json=payload)).compose(
        shots=[(2, "the same line"), (5, "the same line")]
    )
    assert out == {2: "second framing", 5: "fifth framing"}


def test_scene_image_director_ignores_unknown_or_malformed_shot_keys(monkeypatch, fakes):
    settings = _settings(monkeypatch)
    payload = {
        "shots": [
            {"shot": 0, "prompt": "kept"},
            {"shot": 99, "prompt": "not a shot we asked for"},
            {"shot": "nonsense", "prompt": "unparseable index"},
            {"prompt": "no index at all"},
        ]
    }
    out = SceneImageDirector(settings, fakes.LLM(script_json=payload)).compose(
        shots=[(0, "a line")]
    )
    assert out == {0: "kept"}


def test_scene_image_director_empty_on_no_shots_or_unusable_output(monkeypatch, fakes):
    settings = _settings(monkeypatch)
    assert SceneImageDirector(settings, fakes.LLM()).compose(shots=[]) == {}  # nothing to do
    # A shot with no spoken line carries no subject, so there is nothing to illustrate.
    assert SceneImageDirector(settings, fakes.LLM()).compose(shots=[(0, "   ")]) == {}
    # A payload without the expected "shots" shape yields nothing usable (caller falls back).
    out = SceneImageDirector(settings, fakes.LLM(script_json={"unexpected": True})).compose(
        shots=[(0, "a line")]
    )
    assert out == {}


def test_scene_image_director_avoids_compositions_already_used_this_video(monkeypatch, fakes):
    """Each scene is its OWN LLM call with no memory of the others, so without being shown what has
    already been used every scene independently reaches for the same safe shot (one real run opened
    8 of 12 images with "over-the-shoulder...")."""
    settings = _settings(monkeypatch)
    llm = fakes.LLM(script_json={"shots": []})
    SceneImageDirector(settings, llm).compose(
        shots=[(0, "a person working in a modern office")],
        already_used=["An over-the-shoulder shot of a dim minimalist office at dusk"],
    )
    system = llm.calls[-1]["system"]
    assert "An over-the-shoulder shot of a dim minimalist office at dusk" in system
    assert "ALREADY been used" in system
    # And the clichés themselves are named so the model stops defaulting to them.
    assert "over-the-shoulder" in system and "banned" in system.lower()


def test_composition_signature_captures_the_opening_camera_clause():
    """The repetition lived in the opening clause (camera strategy + subject + setting), so that is
    what later scenes must be shown."""
    from content_foundry.agents.scene_image_director import composition_signature

    sig = composition_signature(
        "An over-the-shoulder cinematic shot of a professional in a dark home office at twilight, "
        "analyzing two side-by-side screens that glow with abstract colour."
    )
    assert sig.startswith("An over-the-shoulder cinematic shot")
    assert "abstract colour" not in sig  # only the opening clause, not the whole prompt
    assert composition_signature("") == ""


def test_scene_image_director_uses_the_video_title_and_description(monkeypatch, fakes):
    """A single spoken line is a fragment; the video's own title/description are what let the model
    make a shot about THIS video (the lever that lifted the thumbnail director)."""
    settings = _settings(monkeypatch)
    llm = fakes.LLM(script_json={"shots": []})
    SceneImageDirector(settings, llm).compose(
        shots=[(0, "A senior IC stacks liquid equity every year.")],
        narration="A senior IC stacks liquid equity every year.",
        title="ML Career vs AI Startup: The Math",
        description="Why senior ML roles beat startup equity in expected value.",
    )
    system = llm.calls[-1]["system"]
    assert "ML Career vs AI Startup: The Math" in system
    assert "beat startup equity in expected value" in system
    assert "{title}" not in system and "{description}" not in system  # render_prompt is a .replace


def test_scene_image_director_prompt_teaches_by_example_and_keeps_the_guardrails(
    monkeypatch, fakes
):
    """A few-shot exemplar OVERRIDES written rules, so the worked example must itself obey the
    face/hand/text bans — otherwise it silently teaches the model to break them."""
    settings = _settings(monkeypatch)
    llm = fakes.LLM(script_json={"shots": []})
    SceneImageDirector(settings, llm).compose(shots=[(0, "hands typing on a keyboard")])
    system = llm.calls[-1]["system"]
    assert "EXAMPLE INPUT" in system and "EXAMPLE OUTPUT" in system
    assert "Do NOT" in system and "reuse its subjects" in system  # example is not a template
    # The example REFRAMES the person/hands beats rather than shooting them head-on.
    assert "No hands in frame" in system and "No people" in system
    # And the constraints themselves survive alongside it.
    for rule in ("NO visible face", "NO close-up of hands", "no paragraphs of text"):
        assert rule in system
    # The house style must not be allowed to demand text on a photographic still.
    assert "IGNORE any part of it asking for text" in system


def test_the_artefact_rule_offers_more_than_paperwork(monkeypatch, fakes):
    """Telling the model to shoot 'the practitioner's artefact' with only document-shaped examples
    (loss curve, training log, confusion matrix, notebook page) collapsed EVERY image in run 0021 to
    a printed sheet on a matte desk — 20 out of 20. The artefact list has to span hardware, places,
    tools and materials, and the paper habit has to be named as a banned default."""
    settings = _settings(monkeypatch)
    llm = fakes.LLM(script_json={"shots": []})
    SceneImageDirector(settings, llm).compose(shots=[(0, "the model serves at low latency")])
    system = llm.calls[-1]["system"]
    for category in ("HARDWARE", "PLACE", "TOOL", "MATERIAL", "SCREEN", "DOCUMENT"):
        assert f"THE {category}" in system, f"the artefact list does not offer {category}"
    assert "THE PRINTED SHEET ON A DESK" in system
    assert "ALSO VARY WHAT KIND OF THING IT IS" in system
    # The exemplar overrides written rules, so it must not itself be a stack of paperwork.
    example = system.split("EXAMPLE OUTPUT", 1)[1].split("</example>", 1)[0].lower()
    assert example.count("flat-lay") <= 1, "the example teaches the flat-lay-of-paper habit"


def test_worked_example_does_not_demonstrate_a_banned_default(monkeypatch, fakes):
    """The exemplar overrides the written rules, so it must not itself model the clichés the prompt
    bans — an example that opens on a twilight silhouette at a walnut desk teaches exactly that."""
    settings = _settings(monkeypatch)
    llm = fakes.LLM(script_json={"shots": []})
    SceneImageDirector(settings, llm).compose(shots=[(0, "a person working in a modern office")])
    system = llm.calls[-1]["system"]
    example = system.split("EXAMPLE OUTPUT", 1)[1].split("</example>", 1)[0].lower()
    for cliche in ("over-the-shoulder", "walnut", "twilight", "at dusk", "in silhouette"):
        assert cliche not in example, f"the worked example demonstrates a banned default: {cliche}"


def test_labels_are_allowed_so_the_real_artefact_can_be_shown(monkeypatch, fakes):
    """Banning readable text outright is what pushed the model into metaphor: a loss curve, a
    confusion matrix and an equation are all unshootable without a few words, so it reached for a
    pressure gauge to mean 'calibrated' and a padlock to mean 'gatekeeper'. Labels are the fix, and
    the exemplar has to demonstrate them or it silently reinstates the ban."""
    settings = _settings(monkeypatch)
    llm = fakes.LLM(script_json={"shots": []})
    SceneImageDirector(settings, llm).compose(shots=[(0, "label smoothing softens the targets")])
    system = llm.calls[-1]["system"]
    assert "VISUAL METAPHOR" in system
    for stand_in in ("pressure gauge", "padlock", "spirit level", "hourglass", "maze"):
        assert stand_in in system, f"the metaphor ban does not name the {stand_in} stand-in"
    example = system.split("EXAMPLE OUTPUT", 1)[1].split("</example>", 1)[0].lower()
    assert "no readable text" not in example  # the old blanket ban, re-taught by example
    assert "label" in example  # and the replacement is actually demonstrated


def test_prompt_makes_relevance_the_hard_constraint(monkeypatch, fakes):
    """Variety rules pushed shots off-topic (a transit hub for a hiring-debrief scene), so relevance
    must outrank them and the repeat-avoidance must change the CAMERA, not the subject."""
    settings = _settings(monkeypatch)
    llm = fakes.LLM(script_json={"shots": []})
    SceneImageDirector(settings, llm).compose(
        shots=[(0, "an engineer at a whiteboard")], already_used=["A wide shot of a server hall"]
    )
    system = llm.calls[-1]["system"]
    assert "RELEVANCE IS THE WHOLE JOB" in system and "MUTED TEST" in system
    assert "BUILD IT FROM A NOUN IN THAT LINE" in system
    # Variety is explicitly demoted to craft so it can no longer drag the subject off-topic.
    assert "VARY THE CRAFT, NOT THE SUBJECT" in system
    assert "solve the repeat with the CAMERA" in system
    # The decorative-infrastructure escape hatch is named and closed.
    assert "DECORATIVE INFRASTRUCTURE" in system
    for stand_in in ("transit hubs", "HVAC", "condensation droplets"):
        assert stand_in in system


def test_each_shot_is_given_only_the_words_spoken_over_it(monkeypatch, fakes):
    """A scene runs 45-90s and carries several claims, so handing the model only the whole narration
    left it decorating the generic beat phrase. Each shot gets ITS slice — and the stock-search beat
    is no longer sent at all, because it dragged images toward generic footage of the domain."""
    settings = _settings(monkeypatch)
    llm = fakes.LLM(script_json={"shots": []})
    SceneImageDirector(settings, llm).compose(
        shots=[
            (0, "Total compensation ranges from $245,000 at L4."),
            (1, "The Bar Raiser holds an absolute veto."),
        ],
        narration="Total compensation ranges from $245,000 at L4. The Bar Raiser holds a veto.",
    )
    system = llm.calls[-1]["system"]
    assert "spoken_while_this_shot_is_on_screen" in system
    assert "Total compensation ranges from $245,000 at L4." in system
    assert "The Bar Raiser holds an absolute veto." in system
    # The spoken line is the subject, and an abstract line has a documented escape hatch.
    assert "ILLUSTRATE THE SPOKEN LINE" in system
    assert "WHEN THE LINE IS ABSTRACT, SHOW THE PRACTITIONER'S ARTEFACT" in system


def test_the_stock_search_beat_is_never_sent_to_the_image_director(monkeypatch, fakes):
    """The beat is written to query a footage library and defaults to generic imagery of the domain
    (a scene about pay bands carried "corporate interview panel room"), so it must not reach the
    image prompt at all — it belongs to the B-roll search only."""
    settings = _settings(monkeypatch)
    llm = fakes.LLM(script_json={"shots": []})
    SceneImageDirector(settings, llm).compose(
        shots=[(0, "Total compensation ranges from $245,000 at L4.")]
    )
    system = llm.calls[-1]["system"]
    assert '"beat"' not in system  # the payload carries a shot NUMBER, never a beat phrase
    assert '"shot": 0' in system
