"""The gate that would have prevented the 12th measurement error of 2026-07-30.

`visual.clips` is append-only AND rewritten by later passes, and both continue AFTER
`job.status == "completed"`. Measured that day: job `43107d93` reported clip counts
**17 -> 2 -> 14 -> 16 -> 9** across successive post-completion polls, and `4ad1180e` read
**17/17 unidentified mid-flight but 11/18 identified at rest** — a 100%-vs-39% swing on one job.

I acted on the mid-flight number and it became the founding premise of a backlog item.

These tests drive the wait against SCRIPTED churn — including the real observed sequence — because
the defect is a timing artifact that a live test cannot reproduce on demand.
"""

import pytest

from kitesforu_qa.settled_clips import (
    clips_fingerprint,
    wait_for_settled_clips,
)


class _Clock:
    """Deterministic time so the test suite never actually sleeps."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def _reader(sequence):
    it = iter(sequence)
    last = {"v": []}

    def read():
        try:
            last["v"] = next(it)
        except StopIteration:
            pass
        return last["v"]

    return read


def _clips(n, tag="a"):
    return [{"beat_index": i, "content_hash": f"{tag}{i}"} for i in range(n)]


class TestItWaitsOutRealObservedChurn:
    def test_the_43107d93_sequence_is_not_mistaken_for_settled(self):
        """17 -> 2 -> 14 -> 16 -> 9 then steady: the wait must land on the STEADY value."""
        seq = [_clips(17), _clips(2), _clips(14), _clips(16), _clips(9)] + [_clips(9)] * 12
        c = _Clock()
        r = wait_for_settled_clips(_reader(seq), stable_seconds=20, poll_s=5,
                                   sleep=c.sleep, now=c.now, timeout_s=600)
        assert r.settled is True
        assert len(r.clips) == 9, "must settle on the STEADY array, not an intermediate one"

    def test_a_repeated_count_is_not_stability(self):
        """A later pass can rewrite the array while preserving its length — counting would call this
        settled when the content changed under it."""
        seq = [_clips(5, "a"), _clips(5, "b"), _clips(5, "c")] + [_clips(5, "c")] * 12
        c = _Clock()
        r = wait_for_settled_clips(_reader(seq), stable_seconds=20, poll_s=5,
                                   sleep=c.sleep, now=c.now, timeout_s=600)
        assert r.settled is True
        assert r.clips[0]["content_hash"] == "c0", "must settle on the LAST rewrite, not the first"

    def test_stability_must_span_the_window_not_one_lucky_read(self):
        """Two identical reads 5s apart are NOT proof when the window is 20s."""
        seq = [_clips(3, "a"), _clips(3, "a"), _clips(8, "b")] + [_clips(8, "b")] * 12
        c = _Clock()
        r = wait_for_settled_clips(_reader(seq), stable_seconds=20, poll_s=5,
                                   sleep=c.sleep, now=c.now, timeout_s=600)
        assert len(r.clips) == 8, "the early identical pair must not have ended the wait"


class TestNeverSettledIsAResultNotAnError:
    def test_perpetual_churn_returns_settled_false(self):
        """A job still churning at timeout is a FINDING — never an exception to swallow, and never a
        licence to use the last read as if it were final."""
        seq = [_clips(i % 9 + 1, f"t{i}") for i in range(500)]
        c = _Clock()
        r = wait_for_settled_clips(_reader(seq), stable_seconds=20, poll_s=5,
                                   sleep=c.sleep, now=c.now, timeout_s=100)
        assert r.settled is False
        assert "still changing" in r.reason

    def test_a_reader_error_is_not_stability(self):
        """A transient Firestore blip must not be mistaken for a settled array."""
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 4:
                raise RuntimeError("transient")
            return _clips(6)

        c = _Clock()
        r = wait_for_settled_clips(flaky, stable_seconds=20, poll_s=5,
                                   sleep=c.sleep, now=c.now, timeout_s=600)
        assert r.settled is True and len(r.clips) == 6


class TestTheFingerprint:
    def test_it_is_content_not_length(self):
        assert clips_fingerprint(_clips(5, "a")) != clips_fingerprint(_clips(5, "b"))

    def test_key_order_is_not_a_change(self):
        a = [{"beat_index": 1, "content_hash": "x"}]
        b = [{"content_hash": "x", "beat_index": 1}]
        assert clips_fingerprint(a) == clips_fingerprint(b)

    @pytest.mark.parametrize("bad", [None, 0, "clips", {}])
    def test_non_lists_never_raise(self, bad):
        assert isinstance(clips_fingerprint(bad), str)

    def test_identical_lists_match(self):
        assert clips_fingerprint(_clips(4)) == clips_fingerprint(_clips(4))
