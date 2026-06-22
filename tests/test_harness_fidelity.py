"""Artifact must read the REAL completed-job doc shape + genre aliases.

Caught by running the harness on real jobs (64b5df17/695dee1e): the script lives under
outputs.script.dialogue (NOT doc["script"]), segments store text under text_preview, and
episode_profile.genre="educational" must receive the explainer checks (genre alias). Before this
fix the harness reported words=0 on every real job (a total false-negative).
"""
from kitesforu_qa.harness import Artifact, checks_for, run_dimension

REAL_SHAPE = {
    "job_id": "r1",
    "status": "completed",
    "episode_profile": {"genre": "educational"},
    "outputs": {"script": {"metadata": {"items_count": 2}, "dialogue": [
        {"text": "A B-tree is a balanced index. For example, imagine a library catalog.", "speaker": "Host1"},
        {"text": "It works by keeping keys sorted so a lookup touches only a few pages.", "speaker": "Host2"},
    ]}},
    "segments_ready": [{"text_preview": "A B-tree is a balanced index..."}, {"text_preview": "It works by..."}],
}


def test_reads_full_script_from_outputs():
    art = Artifact.from_doc(REAL_SHAPE)
    assert art.word_count > 5, "must read outputs.script.dialogue, not the absent doc['script']"
    assert set(art.speakers) == {"Host1", "Host2"}


def test_segment_text_preview_fallback():
    art = Artifact.from_doc({"job_id": "s", "segments_ready": [{"text_preview": "hello world"}]})
    assert "hello world" in art.script_text


def test_educational_genre_gets_explainer_checks():
    ids = {c.id for c in checks_for(genre="educational")}
    assert "explainer.has_example" in ids, "educational must alias to explainer"


def test_silent_host_labels_pass_spoken_leak_fails():
    # Host1/Host2 as SILENT speaker labels (REAL_SHAPE) must NOT fire — they are intentional TTS
    # voice-mapping ids, never heard. Only a label LEAKING into spoken text is the real robotic bug.
    sr_ok = run_dimension(Artifact.from_doc(REAL_SHAPE), "structure")
    assert not any("no_spoken_host_labels" in i for i in sr_ok.issues), sr_ok.issues
    leak = {**REAL_SHAPE, "outputs": {"script": {"dialogue": [
        {"text": "Host2, what do you think about B-trees?", "speaker": "Host1"},
    ]}}}
    sr_bad = run_dimension(Artifact.from_doc(leak), "structure")
    assert any("no_spoken_host_labels" in i for i in sr_bad.issues), sr_bad.issues


def test_visuals_read_nested_compartment():
    # Real jobs nest visuals under doc['visual'] — has_visuals reading top-level visual_clips made
    # the ENTIRE visual battery skip on every real job (false confidence). Found by parallel's loop.
    doc = {"job_id": "v", "visual": {
        "clips": [{"beat_index": 0}, {"beat_index": 1}],
        "captions_vtt": "WEBVTT\n...",
        "video_url": "gs://x.mp4",
        "video_burned_url": "gs://burned.mp4",
    }}
    art = Artifact.from_doc(doc)
    assert art.has_visuals
    assert len(art.visual_clips) == 2
    assert art.captions_vtt.startswith("WEBVTT")
    assert art.video_url == "gs://burned.mp4"  # burned-caption export preferred


def test_no_visuals_when_visual_none():
    # audio-only jobs have doc['visual'] = None — must not crash, has_visuals False
    art = Artifact.from_doc({"job_id": "a", "visual": None})
    assert not art.has_visuals
    assert art.visual_clips == []
    assert art.captions_vtt is None
