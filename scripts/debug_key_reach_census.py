#!/usr/bin/env python3
"""Which keys do the DEBUG components read that no real job document contains?

WHY THIS EXISTS (2026-08-28). `components/debug/QualityGateCard.tsx` read `data.metrics` from the
day it shipped (2026-04-21, #542). No writer ever produced that key — both persist sites SPREAD the
metrics one level up (`quality_gate_runner.py:391`, `streaming_gate_regen.py:1589`). Measured over
88 jobs / 14 days: `metrics` truthy on **0/88**, `final_verdict` **0/88**.

It went unnoticed for FOUR MONTHS because the card still RENDERED — a sibling condition kept it
alive — so the symptom was a header with badges and no numbers. **A component with nothing in it
and a working component look identical.** Nothing errored, so nothing was reported. Worse, all 11
of its unit tests fed the fictional nested shape and passed: coverage that PROTECTED the bug.

This is the doc-key sibling of `kitesforu-workers/scripts/check_mechanism_reach.py` (which finds
LOG PROBES that never fire) and `check_no_new_orphans.py` (unreached FUNCTIONS). Same family —
EXISTENCE was checked, REACH never was — different data source, hence a separate instrument rather
than a branch inside those.

METHOD
  1. For each component fed a DIRECT doc slice, extract every `data.<key>` read.
  2. Walk N real job documents and collect the keys present AT THAT EXACT SLICE.
  3. A read key absent from the slice on EVERY document is a DEAD READ.

⚠️ THE FIRST DRAFT SEARCHED "ANY DEPTH" AND COULD NOT CATCH ITS OWN MOTIVATING BUG. `metrics`
exists at `stages.content_craft.metrics` and `final_verdict` at
`stages.job-audio.qa.duration_enforcement.final_verdict` — different stages — so an any-depth walk
FOUND both and cleared them, while `stages.quality_gate.metrics` was 0/88 the whole time. A scan
that cannot detect the defect it was built for is worse than none: it reads as coverage.

SO THIS SCANS SLICES, NOT THE WHOLE DOC, and only components the debug page hands a direct slice
(`<QualityGateCard data={debugInfo.quality_gate} />`). Components fed a CONSTRUCTED literal
(`data={{ ... }}`, e.g. DecisionsStrip, CastPanel, RegenTrailDiff, StageInputOutputCard) are
deliberately OUT OF SCOPE and listed as such: their props are assembled by the page, so a key
missing from the job doc says nothing about whether the component is broken. Precision over
coverage — the uncovered components are NAMED rather than silently skipped.

A ZERO IS NOT AUTOMATICALLY A BUG. A key can be legitimately rare (an error branch, a content type
we seldom produce). That is what the baseline is for: every accepted zero carries a written reason,
the same discipline `check_mechanism_reach.py` uses.

    python3 debug_key_reach_census.py                 # report
    python3 debug_key_reach_census.py --days 30       # wider window
    python3 debug_key_reach_census.py --check         # exit 1 on a NEW dead read (CI)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPONENTS = ROOT / "kitesforu-frontend" / "components" / "debug"
BASELINE = pathlib.Path(__file__).resolve().parent / "debug_key_reach_baseline.json"

#: Components the debug page hands a DIRECT slice of the job document, and the path of that slice.
#: Read off `app/debug/[jobId]/page.tsx`. Keep in step with it by hand — a stale entry shows up as
#: the slice-hit control failing, not as a silent pass.
SLICES: dict[str, tuple[str, ...]] = {
    "QualityGateCard.tsx": ("stages", "quality_gate"),
    "ComposerLogPanel.tsx": ("stages", "job-script", "composer_log"),
}

#: Components fed a CONSTRUCTED literal by the page (`data={{ … }}`). Their props are assembled in
#: the page, so a key absent from the job doc proves nothing about them. Named, not silently
#: dropped — if one is ever switched to a direct slice, move it into SLICES above.
OUT_OF_SCOPE = (
    "DecisionsStrip.tsx", "CastPanel.tsx", "RegenTrailDiff.tsx", "StageInputOutputCard.tsx",
    "FlowCard.tsx",
)

# `data.foo` / `data?.foo` — how these cards read their slice. The identifier is captured WHOLE;
# truncating it is a real bug I shipped in the first draft: `[a-z_][a-z0-9_]*` matched `data.ttsCall`
# as "tts" and `data.startedAt` as "started", inventing two dead keys that do not exist.
READ = re.compile(r"\bdata\??\.([A-Za-z_][A-Za-z0-9_]*)")

# Reads that are NOT job-doc keys: JS built-ins.
NOT_DOC_KEYS = {
    "map", "length", "filter", "find", "some", "every", "forEach", "slice", "join",
    "toString", "valueOf", "then", "catch", "push", "reduce", "sort", "keys", "values",
}

#: THE PRECISION FILTER, and the reason this scan can be trusted when it accuses.
#: Firestore job documents are snake_case throughout; React-local prop shapes are camelCase. So a
#: read containing an uppercase letter is a LOCAL field, not a doc key, and must never be reported
#: as dead. Measured on the first draft: this one rule removes 13 of 24 accusations, all of them
#: `FlowCard.tsx`, whose `data: FlowCardData` is a local flow-node shape and not a job-doc slice.
SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")


def keys_read() -> dict[str, set[str]]:
    """`data.<key>` reads per IN-SCOPE component."""
    if not COMPONENTS.is_dir():
        sys.exit(f"components dir not found: {COMPONENTS}")
    out: dict[str, set[str]] = {c: set() for c in SLICES}
    for comp in SLICES:
        f = COMPONENTS / comp
        if not f.exists():
            sys.exit(f"declared component missing: {f} — SLICES is stale")
        for m in READ.finditer(f.read_text()):
            k = m.group(1)
            if k not in NOT_DOC_KEYS and SNAKE.match(k):
                out[comp].add(k)
    return out


def slice_of(doc: dict, path: tuple[str, ...]):
    """Walk a declared slice path; None when the slice is absent on this doc."""
    cur = doc
    for seg in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
    return cur


def keys_present(days: int, limit: int) -> tuple[dict[str, set[str]], dict[str, set[str]], int, int]:
    """(keys present, keys ever non-null) per declared slice, plus doc and slice-hit counts."""
    from google.cloud import firestore

    db = firestore.Client(project="kitesforu-dev")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    docs = list(
        db.collection("podcast_jobs").where("created_at", ">=", since).limit(limit).stream()
    )
    # PRESENT and NON-NULL are tracked separately — they are OPPOSITE diagnoses. A key that is
    # ABSENT means the producer never writes it (the consumer reads a fiction). A key that is
    # PRESENT BUT ALWAYS NULL means the producer declares it and never fills it. Conflating them
    # is a real bug I shipped in the first draft: `composer_log.total_static_section_chars` is on
    # 32/32 jobs with value None, and a truthiness filter reported it as a DEAD READ — sending the
    # next reader to fix the frontend when the writer is what is empty.
    present: dict[str, set[str]] = {c: set() for c in SLICES}
    nonnull: dict[str, set[str]] = {c: set() for c in SLICES}
    hits = 0
    for d in docs:
        o = d.to_dict() or {}
        for comp, path in SLICES.items():
            sl = slice_of(o, path)
            if isinstance(sl, dict):
                hits += 1
                for k, v in sl.items():
                    present[comp].add(k)
                    if v not in (None, "", [], {}):
                        nonnull[comp].add(k)
    return present, nonnull, len(docs), hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--check", action="store_true", help="exit 1 on a dead read not in the baseline")
    ap.add_argument("--update", action="store_true", help="rewrite the baseline from this run")
    args = ap.parse_args()

    reads = keys_read()
    present, nonnull, ndocs, hits = keys_present(args.days, args.limit)

    # THE CONTROL. A slice path that never resolves makes EVERY key under it look dead — the exact
    # false positive that turns a scan into a liar. Refuse to report rather than accuse wrongly.
    if ndocs == 0 or hits == 0:
        print(f"❌ CONTROL FAILED: {ndocs} docs, {hits} slice hits. SLICES is stale or the query is wrong.")
        print("   Refusing to report — an unresolved slice makes every read look dead.")
        return 2
    for comp in SLICES:
        if not present[comp]:
            print(f"❌ CONTROL FAILED: slice {'.'.join(SLICES[comp])} resolved to no keys on any doc.")
            return 2

    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    dead: dict[str, list[str]] = {}       # the consumer reads a key the producer never writes
    always_null: dict[str, list[str]] = {}  # the producer writes the key and never a value
    for comp in SLICES:
        for k in sorted(reads[comp] - present[comp]):
            dead.setdefault(k, []).append(comp)
        for k in sorted((reads[comp] & present[comp]) - nonnull[comp]):
            always_null.setdefault(k, []).append(comp)

    print(f"job docs walked : {ndocs} ({args.days} d), {hits} slice hits")
    for comp, path in SLICES.items():
        print(f"  {comp:<24} slice={'.'.join(path):<34} reads={len(reads[comp]):>3} present={len(present[comp]):>3}")
    print(f"\nOUT OF SCOPE (page-constructed props, not judgeable here): {', '.join(OUT_OF_SCOPE)}")
    print(f"\nDEAD READS (key absent — the consumer reads a fiction): {len(dead)}")

    new = {k: v for k, v in dead.items() if k not in base}
    for k in sorted(dead):
        why = base.get(k, {}).get("reason", "")
        print(f"  {'NEW ' if k in new else '    '}{k:<30} read by {dead[k]}{('  — ' + why) if why else ''}")

    print(f"\nALWAYS NULL (key written, value never set — the WRITER is the defect): {len(always_null)}")
    for k in sorted(always_null):
        why = base.get(k, {}).get("reason", "")
        print(f"  {'NEW ' if k not in base else '    '}{k:<30} read by {always_null[k]}{('  — ' + why) if why else ''}")
    new.update({k: v for k, v in always_null.items() if k not in base})

    if args.update:
        dead = {**dead, **always_null}
        BASELINE.write_text(json.dumps(
            {k: {"read_by": v, "reason": base.get(k, {}).get("reason", "UNREVIEWED")}
             for k, v in dead.items()}, indent=2, sort_keys=True) + "\n")
        print(f"\nbaseline written: {len(dead)} entries")
        return 0

    if args.check and new:
        print(f"\n❌ {len(new)} NEW dead read(s): {sorted(new)}")
        print("   Either the component reads the wrong key, or the writer stopped emitting it.")
        return 1
    print(f"\n{'✅ no NEW dead reads' if args.check else 'report only (pass --check to gate)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
