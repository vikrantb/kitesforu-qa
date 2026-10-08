#!/usr/bin/env python3
"""Which job statuses a qa poller may stop on: ONE definition for every poller in this repo.

WHY THIS FILE EXISTS. The terminal set was hand-typed at each poller and each copy drifted:
* ``scripts/create_verification_job.sh`` enumerated ``completed|failed|failed_qa`` twice, so a
  finished, shareable ``needs_review`` episode burned every poll and then exited 4 saying it was
  "still running" (kitesforu-qa #175 round-2 code critic D1, design D1);
* ``scripts/canary_loop.py`` stopped on ``completed|failed|cancelled``, so a ``needs_review`` or
  ``failed_qa`` canary polled to its 10-minute limit and paged Slack with a STALL diagnosis for an
  episode that had finished (#175 round-3 design NIT-4);
* ``scripts/narration_sync_audit.py`` scored only ``completed|failed_qa|failed``, so its census
  silently dropped every finished ``needs_review`` episode, the held ones (#175 round-2 design D1);
* ``integrations.kitesforu_api.KitesForUClient.wait_for_completion`` (``kqa e2e``) stopped on
  ``completed|complete|done|failed|error|cancelled``, so a held episode polled to its timeout and
  raised, and ``kqa e2e`` called anything but ``completed`` a failed job.

It lives in the package, not in ``scripts/``, because that client is package code. The scripts put
``src`` on their path, and the shell script runs this file directly.

THE SOURCE OF TRUTH is ``kitesforu_schemas.enums.JobStatus``. Its comments say which members are
terminal: ``NEEDS_REVIEW`` / ``FAILED_QA`` "are TERMINAL like completed/failed: the episode
finished rendering (outputs.audio_url + full segments + script are persisted)"; ``CANCELLED`` is
"User cancelled the job"; ``AWAITING_REVIEW`` is "NON-terminal: the approve route flips it back to
RUNNING". The schemas package exports no terminal set, so this module classifies every member
once, and ``tests/test_one_terminal_status_list.py`` fails when the enum gains a member that is
not classified here. A new status cannot silently fall through to "still running".

Offline and $0: no imports beyond the standard library, so a shell script can read it.

    python3 src/kitesforu_qa/job_status.py terminal      # space-separated, for the shell
    python3 src/kitesforu_qa/job_status.py gradeable
"""
from __future__ import annotations

import sys

#: The episode FINISHED rendering: audio, segments and script are persisted, so there is something
#: to grade. ``needs_review`` and ``failed_qa`` are QA holds, not failures of the render.
FINISHED_RENDERING = frozenset({"completed", "needs_review", "failed_qa"})

#: Terminal, and there is nothing to grade: the job failed, or a user cancelled it.
ENDED_WITHOUT_EPISODE = frozenset({"failed", "cancelled"})

#: Every status a poller must stop on.
TERMINAL = FINISHED_RENDERING | ENDED_WITHOUT_EPISODE

#: Still in flight. ``awaiting_review`` is a guided-creation pause that resumes to ``running``.
NON_TERMINAL = frozenset({"queued", "clarifying", "running", "awaiting_review"})

_SETS = {
    "terminal": TERMINAL,
    "gradeable": FINISHED_RENDERING,
    "ended-without-episode": ENDED_WITHOUT_EPISODE,
    "non-terminal": NON_TERMINAL,
}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in _SETS:
        print(f"usage: job_status.py {{{'|'.join(_SETS)}}}", file=sys.stderr)
        return 2
    print(" ".join(sorted(_SETS[argv[0]])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
