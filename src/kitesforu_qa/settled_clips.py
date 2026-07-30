"""Wait until a job's clip array STOPS CHANGING — the gate every visual census was missing.

WHY THIS EXISTS (measured 2026-07-30, and it invalidated a whole investigation).

`visual.clips` is append-only AND rewritten by later passes, and both continue AFTER
`job.status == "completed"`. Two measurements from one afternoon:

  * job `43107d93` reported clip counts **17 -> 2 -> 14 -> 16 -> 9** across successive polls, every
    one of them taken after the job reported completed;
  * job `4ad1180e` read **17 of 17 clips unidentified** mid-flight, and **11 of 18 identified** at
    rest — a 100%-vs-39% swing on the same job.

I acted on the mid-flight number. It became the founding premise of a backlog item and the 12th
measurement error of the day, all of the same family: the reasoning was sound, the input was taken at
the wrong moment.

## Why `job.status` is the wrong gate — and a count check is not enough either

`wait_for_completion` in ``integrations/kitesforu_api`` gates on terminal status, which is provably
too early for visuals ([[feedback_visuals_settle_after_job_completed]]). But polling until the COUNT
stops changing is also insufficient: a later pass can REWRITE the array while preserving its length
(17 -> 16 -> 9 shows both growth and shrinkage, so a count that happens to repeat proves nothing).

So stability is judged on a CONTENT FINGERPRINT of the whole clip list, and it must hold across two
reads separated in time — one identical pair, not one lucky read.

## Contract

Returns an explicit outcome rather than raising, because "never settled" is a real and interesting
result (a job still churning at timeout is itself a finding, not an error to swallow). Pure apart
from the reader callable it is handed, so it is fully testable offline with no Firestore.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["SettledResult", "clips_fingerprint", "wait_for_settled_clips"]

#: Two identical reads this far apart is the default stability proof. Shorter risks catching a pause
#: between passes; the observed churn on `43107d93` spanned tens of seconds.
DEFAULT_STABLE_SECONDS = 20.0
DEFAULT_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class SettledResult:
    """The outcome of waiting. ``settled=False`` means the array was STILL changing at timeout —
    a legitimate finding to report, never a reason to use the last read as if it were final."""

    settled: bool
    clips: list[Any]
    reads: int
    elapsed_s: float
    fingerprint: str
    reason: str


def clips_fingerprint(clips: Any) -> str:
    """A content hash of the whole clip list.

    Deliberately NOT a length: a later pass can rewrite the array while preserving its count, and the
    observed sequence (17 -> 2 -> 14 -> 16 -> 9) both grew and shrank, so a repeated count is not
    evidence of rest. Keys are sorted so dict ordering can never masquerade as a change.
    """
    if not isinstance(clips, list):
        return "not-a-list"
    try:
        return hashlib.sha256(
            json.dumps(clips, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
    except Exception:  # noqa: BLE001 — a fingerprint must never raise on odd content
        return f"unhashable:{len(clips)}"


def wait_for_settled_clips(
    read_clips: Callable[[], Any],
    *,
    stable_seconds: float = DEFAULT_STABLE_SECONDS,
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
    poll_s: float = 5.0,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], float] | None = None,
) -> SettledResult:
    """Poll ``read_clips`` until the clip list is unchanged across ``stable_seconds``.

    ``read_clips`` is injected (rather than a job id + Firestore client) so the whole wait is
    testable offline against a scripted sequence — the churn that motivated this module is exactly
    the thing a live test could not reproduce on demand.

    Never raises on a reader error: a failed read is treated as "not yet stable" and retried, because
    a transient Firestore blip must not be mistaken for a settled array.
    """
    _sleep = sleep or time.sleep
    _now = now or time.monotonic
    start = _now()
    reads = 0
    last_fp: str | None = None
    last_change = start
    clips: list[Any] = []

    while True:
        try:
            raw = read_clips()
            clips = raw if isinstance(raw, list) else []
            fp = clips_fingerprint(clips)
            reads += 1
        except Exception:  # noqa: BLE001 — a blip is not stability
            fp = None  # type: ignore[assignment]

        t = _now()
        if fp is not None:
            if fp != last_fp:
                last_fp, last_change = fp, t
            elif t - last_change >= stable_seconds:
                return SettledResult(
                    settled=True, clips=clips, reads=reads, elapsed_s=t - start,
                    fingerprint=fp, reason=f"unchanged for {t - last_change:.0f}s",
                )

        if t - start >= timeout_s:
            return SettledResult(
                settled=False, clips=clips, reads=reads, elapsed_s=t - start,
                fingerprint=last_fp or "", reason="still changing at timeout",
            )
        _sleep(poll_s)
