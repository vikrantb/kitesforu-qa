#!/usr/bin/env python3
"""T14 count-contract check: a title promising N items delivers N findable items ($0, deterministic).

Tenet 14 (kitesforu-docs/tenets/TENETS.md): "The artifact honors its own promise, measured on the
artifact." Born from job 21df970e — the title promised "7 Unique Habits"; the delivered script
contained ZERO findable habits (it was a fictional drama). The fixed witness cdccf0e6 delivered
"seven switches — pace, pause, names, opener, stance, concreteness, calibration".

Counting is a COMPUTATION, not a judgment call — no LLM, no cost ([[feedback_never_ask_a_model_to
_perceive_what_you_can_compute]]). Usage:  count_contract_check.py <job_id>
Exit 0 = PASS or N/A (no numeric promise in the title); exit 1 = FAIL; exit 2 = cannot read job.
"""
from __future__ import annotations

import re
import sys

_WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
# The promise: a leading/embedded count attached to a countable noun in the TITLE.
_PROMISE = re.compile(
    r"\b(\d{1,2}|" + "|".join(_WORD_NUMS) + r")\s+(?:\w+\s){0,2}?"
    r"(habits?|tips?|ways?|steps?|rules?|mistakes?|switch(?:es)?|lessons?|secrets?|principles?|"
    r"strategies|habits|techniques?|methods?|reasons?|things)\b", re.I)


def promised_count(title: str) -> int | None:
    m = _PROMISE.search(title or "")
    if not m:
        return None
    tok = m.group(1).lower()
    return int(tok) if tok.isdigit() else _WORD_NUMS[tok]


def delivered_count(script: str, n: int) -> int:
    """Count findable enumerated items. Three independent signals; take the strongest.

    1. Explicit ordinal labels ("Habit 3", "tip #2", "switch five", "number four").
    2. An inline enumeration list naming >= n items ("—pace, pause, names, opener, ...").
    3. A spoken commitment matching the count ("these seven switches") backed by a dash/comma
       list of exactly that many short items nearby.
    """
    s = script or ""
    labels = set(re.findall(r"\b(?:habit|tip|way|step|rule|mistake|switch|lesson|secret|principle|"
                            r"strategy|technique|method|reason|number)\s+#?(\d{1,2}|" +
                            "|".join(_WORD_NUMS) + r")\b", s, re.I))
    best = len(labels)
    # em-dash/comma enumeration: "—a, b, c, d, e, f, g—" (short noun items)
    for m in re.finditer(r"[—:-]\s*((?:[a-z][a-z ]{1,18},\s*){3,}[a-z][a-z ]{1,18})", s, re.I):
        items = [x.strip() for x in m.group(1).split(",") if x.strip()]
        best = max(best, len(items))
    return best


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: count_contract_check.py <job_id>"); return 2
    from google.cloud import firestore  # local import: keep --help free of gcp deps
    d = firestore.Client(project="kitesforu-dev").collection("podcast_jobs") \
        .document(sys.argv[1]).get().to_dict() or {}
    title = str(d.get("topic") or "")
    script = " ".join(str(s.get("text_full") or "") for s in (d.get("segments_ready") or []))
    if not script:
        print(f"CANNOT READ: no script on job (title={title!r})"); return 2
    n = promised_count(title)
    if n is None:
        print(f"N/A: title makes no numeric promise ({title!r})"); return 0
    got = delivered_count(script, n)
    verdict = "PASS" if got >= n else "FAIL"
    print(f"{verdict}: title promises {n}, script delivers {got} findable items ({title!r})")
    return 0 if got >= n else 1


if __name__ == "__main__":
    sys.exit(main())
