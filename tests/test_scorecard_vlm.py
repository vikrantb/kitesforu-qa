"""Unit tests for kitesforu_qa.scorecard.vlm — the real photo-vs-illustration VLM callable wired into
axis 3 (VISUAL TRUTH) via ``ScorecardConfig.vlm_fn``.

Every provider call (``classify_frame`` / the OpenAI/Anthropic/Gemini leaves) is exercised via dependency
injection (monkeypatching the internal seams: ``vlm.classify_frame``, ``vlm.extract_frame``,
``vlm._PROVIDER_CHAIN``) — no network, no real ffmpeg, no real API keys, $0 and fast. Covers: photo -> pass,
illustration -> fail, a provider/extraction error -> "unknown" (fail-open, excluded from the fraction, never
a fake pass), the axis-fraction math itself, and the all-beats-unjudged raise that ``axes.py`` turns into an
honest ``score=None``.
"""
from __future__ import annotations

import pytest

from kitesforu_qa.scorecard import vlm

# ── _parse_verdict_json ──────────────────────────────────────────────────────────


def test_parse_verdict_json_plain() -> None:
    verdict, confidence, reason = vlm._parse_verdict_json(
        '{"verdict": "photo", "confidence": 0.92, "reason": "visible sensor noise and real skin texture"}'
    )
    assert verdict == "photo"
    assert confidence == 0.92
    assert "skin texture" in reason


def test_parse_verdict_json_markdown_fenced() -> None:
    text = '```json\n{"verdict": "illustration", "confidence": 0.88, "reason": "flat cel-shaded rendering"}\n```'
    verdict, confidence, _reason = vlm._parse_verdict_json(text)
    assert verdict == "illustration"
    assert confidence == 0.88


def test_parse_verdict_json_unexpected_verdict_raises() -> None:
    with pytest.raises(ValueError, match="unexpected verdict"):
        vlm._parse_verdict_json('{"verdict": "maybe", "confidence": 0.5, "reason": "unsure"}')


def test_parse_verdict_json_missing_confidence_defaults_zero() -> None:
    verdict, confidence, _reason = vlm._parse_verdict_json('{"verdict": "photo", "reason": "looks real"}')
    assert verdict == "photo"
    assert confidence == 0.0


# ── classify_frame — provider chain (fail-open, cost-ordered failover) ───────────


def _fake_provider(name: str, ready: bool, result_or_exc):
    def _ready() -> bool:
        return ready

    def _call(image_path: str):
        if isinstance(result_or_exc, Exception):
            raise result_or_exc
        return result_or_exc

    return (name, _ready, _call)


def test_classify_frame_uses_first_ready_provider(monkeypatch) -> None:
    chain = [
        _fake_provider("gemini", False, ("photo", 0.9, "ok")),
        _fake_provider("openai", True, ("photo", 0.95, "real photo, sensor noise visible")),
        _fake_provider("anthropic", True, ("illustration", 0.5, "should never be reached")),
    ]
    monkeypatch.setattr(vlm, "_PROVIDER_CHAIN", chain)
    verdict, confidence, reason, provider = vlm.classify_frame("fake.png")
    assert provider == "openai"
    assert verdict == "photo"
    assert confidence == 0.95
    assert "sensor noise" in reason


def test_classify_frame_falls_over_to_next_provider_on_failure(monkeypatch) -> None:
    chain = [
        _fake_provider("gemini", True, RuntimeError("gemini quota exceeded")),
        _fake_provider("openai", True, ("illustration", 0.9, "cel-shaded, flat lighting")),
    ]
    monkeypatch.setattr(vlm, "_PROVIDER_CHAIN", chain)
    verdict, confidence, _reason, provider = vlm.classify_frame("fake.png")
    assert provider == "openai"
    assert verdict == "illustration"
    assert confidence == 0.9


def test_classify_frame_raises_when_no_provider_configured(monkeypatch) -> None:
    chain = [
        _fake_provider("gemini", False, ("photo", 0.9, "ok")),
        _fake_provider("openai", False, ("photo", 0.9, "ok")),
    ]
    monkeypatch.setattr(vlm, "_PROVIDER_CHAIN", chain)
    with pytest.raises(RuntimeError, match="no VLM provider configured"):
        vlm.classify_frame("fake.png")


def test_classify_frame_raises_when_every_provider_fails(monkeypatch) -> None:
    chain = [
        _fake_provider("gemini", True, RuntimeError("timeout")),
        _fake_provider("openai", True, RuntimeError("rate limited")),
    ]
    monkeypatch.setattr(vlm, "_PROVIDER_CHAIN", chain)
    with pytest.raises(RuntimeError, match="every available VLM provider failed"):
        vlm.classify_frame("fake.png")


def test_with_retries_retries_up_to_the_cap_then_raises() -> None:
    attempts = {"n": 0}

    def flaky(_path: str):
        attempts["n"] += 1
        raise RuntimeError("flaky")

    with pytest.raises(RuntimeError, match="flaky"):
        vlm._with_retries(flaky, "fake.png")
    assert attempts["n"] == vlm._VLM_MAX_ATTEMPTS


def test_with_retries_succeeds_after_a_transient_failure() -> None:
    attempts = {"n": 0}

    def flaky_then_ok(_path: str):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("transient")
        return ("photo", 0.9, "ok")

    result = vlm._with_retries(flaky_then_ok, "fake.png")
    assert result == ("photo", 0.9, "ok")


# ── judge_one_beat — per-beat fail-open ──────────────────────────────────────────


def test_judge_one_beat_success(monkeypatch) -> None:
    monkeypatch.setattr(vlm, "extract_frame", lambda beat, video_path: ("frame.png", lambda: None))
    monkeypatch.setattr(vlm, "classify_frame", lambda path: ("photo", 0.93, "real texture", "openai"))
    v = vlm.judge_one_beat({"beat_index": 3}, "video.mp4")
    assert v.beat_index == 3
    assert v.verdict == "photo"
    assert v.confidence == 0.93
    assert v.provider == "openai"


def test_judge_one_beat_extraction_failure_is_unknown_not_a_crash(monkeypatch) -> None:
    def _raise_extract(beat, video_path):
        raise RuntimeError("no frame source available")

    monkeypatch.setattr(vlm, "extract_frame", _raise_extract)
    v = vlm.judge_one_beat({"beat_index": 7}, None)
    assert v.beat_index == 7
    assert v.verdict is None
    assert v.confidence is None
    assert "no frame source" in v.reason


def test_judge_one_beat_classify_failure_is_unknown_and_cleanup_still_runs(monkeypatch) -> None:
    cleaned = {"called": False}

    def _cleanup():
        cleaned["called"] = True

    monkeypatch.setattr(vlm, "extract_frame", lambda beat, video_path: ("frame.png", _cleanup))

    def _raise_classify(path):
        raise RuntimeError("every available VLM provider failed: openai: 500")

    monkeypatch.setattr(vlm, "classify_frame", _raise_classify)
    v = vlm.judge_one_beat({"beat_index": 9}, "video.mp4")
    assert v.verdict is None
    assert "every available VLM provider failed" in v.reason
    assert cleaned["called"] is True


# ── photo_vs_illustration_vlm_fn — the aggregate axis math ───────────────────────


def _patch_judge(monkeypatch, verdicts_by_index):
    def _fake_judge(beat, video_path):
        return verdicts_by_index[beat["beat_index"]]

    monkeypatch.setattr(vlm, "judge_one_beat", _fake_judge)


def test_all_photo_scores_100(monkeypatch) -> None:
    _patch_judge(monkeypatch, {
        0: vlm.BeatVerdict(0, "photo", 0.9, "real", "openai"),
        1: vlm.BeatVerdict(1, "photo", 0.95, "real", "openai"),
    })
    context = {"beats": [{"beat_index": 0}, {"beat_index": 1}], "video_path": "v.mp4"}
    score, note = vlm.photo_vs_illustration_vlm_fn([], context)
    assert score == 100.0
    assert "2/2 judged-as-photo" in note


def test_all_illustration_scores_0_below_floor(monkeypatch) -> None:
    _patch_judge(monkeypatch, {
        0: vlm.BeatVerdict(0, "illustration", 0.9, "cel-shaded", "openai"),
        1: vlm.BeatVerdict(1, "illustration", 0.85, "3d-render look", "openai"),
    })
    context = {"beats": [{"beat_index": 0}, {"beat_index": 1}], "video_path": "v.mp4"}
    score, _note = vlm.photo_vs_illustration_vlm_fn([], context)
    assert score == 0.0  # this is the exact "Pixar-labeled-photoreal lie" case — must fail hard


def test_mixed_photo_illustration_fraction(monkeypatch) -> None:
    _patch_judge(monkeypatch, {
        0: vlm.BeatVerdict(0, "photo", 0.9, "real", "openai"),
        1: vlm.BeatVerdict(1, "illustration", 0.8, "stylized", "openai"),
        2: vlm.BeatVerdict(2, "photo", 0.88, "real", "openai"),
        3: vlm.BeatVerdict(3, "illustration", 0.7, "cartoon", "openai"),
    })
    context = {"beats": [{"beat_index": i} for i in range(4)], "video_path": "v.mp4"}
    score, _note = vlm.photo_vs_illustration_vlm_fn([], context)
    assert score == 50.0


def test_unknown_beats_are_excluded_from_the_fraction_not_counted_as_fail(monkeypatch) -> None:
    _patch_judge(monkeypatch, {
        0: vlm.BeatVerdict(0, "photo", 0.9, "real", "openai"),
        1: vlm.BeatVerdict(1, "photo", 0.9, "real", "openai"),
        2: vlm.BeatVerdict(2, None, None, "extraction failed"),  # excluded, not a "fail"
    })
    context = {"beats": [{"beat_index": i} for i in range(3)], "video_path": "v.mp4"}
    score, note = vlm.photo_vs_illustration_vlm_fn([], context)
    assert score == 100.0  # 2/2 judged, not 2/3
    assert "1 beat(s) unjudged" in note


def test_raises_when_every_beat_is_unjudged_axis_degrades_honestly(monkeypatch) -> None:
    _patch_judge(monkeypatch, {
        0: vlm.BeatVerdict(0, None, None, "provider down"),
        1: vlm.BeatVerdict(1, None, None, "no frame source available"),
    })
    context = {"beats": [{"beat_index": 0}, {"beat_index": 1}], "video_path": "v.mp4"}
    with pytest.raises(RuntimeError, match="could not judge any"):
        vlm.photo_vs_illustration_vlm_fn([], context)


def test_falls_back_to_image_uris_when_no_beats_in_context(monkeypatch) -> None:
    """A minimal injected test double (or a caller that hasn't upgraded to the richer context) still works —
    each image_uri becomes a synthetic beat."""
    calls: list[dict] = []

    def _fake_judge(beat, video_path):
        calls.append(beat)
        return vlm.BeatVerdict(beat["beat_index"], "photo", 0.9, "real", "openai")

    monkeypatch.setattr(vlm, "judge_one_beat", _fake_judge)
    score, _note = vlm.photo_vs_illustration_vlm_fn(["gs://x/a.png", "gs://x/b.png"], {})
    assert score == 100.0
    assert len(calls) == 2
    assert calls[0]["asset_uri"] == "gs://x/a.png"


# ── extract_frame — video-timestamp-first, asset-fallback ───────────────────────


def test_extract_frame_uses_video_path_at_beat_start_ms(monkeypatch, tmp_path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    seen = {}

    def _fake_ffmpeg(src, at_s, out_path):
        seen["src"] = src
        seen["at_s"] = at_s
        with open(out_path, "wb") as f:
            f.write(b"fake-frame")
        return True

    monkeypatch.setattr(vlm, "_ffmpeg_frame", _fake_ffmpeg)
    path, cleanup = vlm.extract_frame({"beat_index": 0, "start_ms": 4500}, str(video))
    try:
        assert seen["src"] == str(video)
        assert seen["at_s"] == pytest.approx(4.5)
        assert path.endswith("frame.png")
    finally:
        cleanup()


def test_extract_frame_falls_back_to_asset_when_no_video_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(vlm, "_download_asset", lambda uri, dest_dir: str(tmp_path / "asset.png"))
    (tmp_path / "asset.png").write_bytes(b"fake")

    seen = {}

    def _fake_ffmpeg(src, at_s, out_path):
        seen["src"] = src
        seen["at_s"] = at_s
        with open(out_path, "wb") as f:
            f.write(b"fake-frame")
        return True

    monkeypatch.setattr(vlm, "_ffmpeg_frame", _fake_ffmpeg)
    path, cleanup = vlm.extract_frame({"beat_index": 0, "asset_uri": "gs://x/y.png"}, None)
    try:
        assert seen["src"] == str(tmp_path / "asset.png")
        assert seen["at_s"] == 0.0  # a static image — grab as-is
    finally:
        cleanup()


def test_extract_frame_raises_when_nothing_available(monkeypatch) -> None:
    monkeypatch.setattr(vlm, "_download_asset", lambda uri, dest_dir: None)
    monkeypatch.setattr(vlm, "_ffmpeg_frame", lambda *a, **k: False)
    with pytest.raises(RuntimeError, match="no frame source available"):
        vlm.extract_frame({"beat_index": 0}, None)


def test_is_image_path() -> None:
    assert vlm._is_image_path("/tmp/x.PNG") is True
    assert vlm._is_image_path("/tmp/x.jpg") is True
    assert vlm._is_image_path("/tmp/x.mp4") is False


# ── provider readiness (env-key gated) ───────────────────────────────────────────


def test_openai_ready_false_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert vlm._openai_ready() is False


def test_anthropic_ready_false_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert vlm._anthropic_ready() is False


def test_gemini_ready_false_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_AI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert vlm._gemini_ready() is False
