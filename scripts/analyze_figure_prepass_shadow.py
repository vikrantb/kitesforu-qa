#!/usr/bin/env python3
"""Analyze the #122 figure-prepass SHADOW divergence telemetry (the Stage-2 gate).

Reads the ``story_visuals figure_prepass shadow`` compare logs the visuals worker emits
(Cloud Logging, project kitesforu-dev) once ``ENABLE_STORY_VISUALS_FIGURE_PREPASS`` is on,
and aggregates ownership divergence across real jobs. This is the READ-ONLY tool that answers
the Stage-2 question — "does the parallel design's predicted ownership match the real
sequential outcome?" — before Stage 3 (the concurrent flip) is authorized.

Usage:
    python3 analyze_figure_prepass_shadow.py [--hours 24] [--min-beats 8] [--json]

Divergence taxonomy (the reason breakdown matters — not every logged divergence is a REAL one):
  * REAL divergence  — ``actual`` is a concrete engine that is NOT in the predicted candidate
    list. This is a genuine ownership mismatch: the parallel design would (or would not) reach
    an engine the sequential path actually used. THESE gate Stage 3.
  * likely FALSE-POSITIVE (actual=None) — the beat authored no figure (carded / demoted /
    every candidate declined at classify-time). The parallel cascade, using the same classify
    decisions, also produces no figure, so this is reproducible, not a design divergence — but
    the shadow's agree-condition still counts it while ``predicted`` is non-empty (a known
    harness over-count; see the born-short smoke, job 6535f50b). Reported separately so a
    "zero divergence" read is judged on REAL divergences, not carded-beat noise.

Tenet 9: read-only. It only READS Cloud Logging; it never writes to a job or triggers anything.
"""
import argparse
import json
import subprocess
from collections import Counter, defaultdict

PROJECT = "kitesforu-dev"
SERVICE = "kitesforu-worker-visuals"
LOG_MSG = "story_visuals figure_prepass shadow"


def _fetch(hours: int) -> list:
    """Pull the shadow compare-log entries from Cloud Logging (read-only).

    The StructuredLogger writes the WHOLE record as a JSON string into
    ``jsonPayload.message`` — so the filter uses ``:`` (contains), and each entry's
    ``jsonPayload.message`` is JSON-parsed to recover the real fields (the inner
    ``message`` text + ``beats``/``agree``/``diverge``/``examples``/``job_id``)."""
    filt = (
        f'resource.labels.service_name="{SERVICE}" '
        f'AND jsonPayload.message:"{LOG_MSG}"'
    )
    out = subprocess.run(
        ["gcloud", "logging", "read", filt, "--project", PROJECT,
         "--freshness", f"{hours}h", "--limit", "1000", "--format", "json"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise SystemExit(f"gcloud logging read failed:\n{out.stderr[:400]}")
    entries = json.loads(out.stdout or "[]")
    rows = []
    for e in entries:
        raw = (e.get("jsonPayload") or {}).get("message")
        try:
            inner = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (ValueError, TypeError):
            continue
        if not isinstance(inner, dict) or inner.get("message") != LOG_MSG:
            continue
        rows.append({
            "job_id": inner.get("job_id"),
            "beats": int(inner.get("beats") or 0),
            "agree": int(inner.get("agree") or 0),
            "diverge": int(inner.get("diverge") or 0),
            "excluded_lensing": int(inner.get("excluded_lensing") or 0),
            "examples": inner.get("examples") or [],
            "ts": e.get("timestamp"),
        })
    return rows


def _classify_example(ex: dict) -> str:
    """REAL divergence vs likely-false-positive (actual=None)."""
    actual = ex.get("actual")
    predicted = ex.get("predicted") or []
    if actual is None:
        return "fp_actual_none"          # carded / all-declined — reproducible by the parallel design
    if actual not in predicted:
        return "real_actual_not_in_predicted"  # genuine ownership mismatch — gates Stage 3
    return "other"                        # actual in predicted but still logged (shouldn't happen post-fix)


def analyze(rows: list) -> dict:
    jobs = {r["job_id"] for r in rows if r["job_id"]}
    tot = Counter()
    per_job = defaultdict(Counter)
    real_examples, fp_examples = [], []
    reason = Counter()
    for r in rows:
        tot["beats"] += r["beats"]
        tot["agree"] += r["agree"]
        tot["diverge"] += r["diverge"]
        tot["excluded_lensing"] += r["excluded_lensing"]
        per_job[r["job_id"]]["beats"] += r["beats"]
        per_job[r["job_id"]]["diverge"] += r["diverge"]
        for ex in r["examples"]:
            kind = _classify_example(ex)
            reason[kind] += 1
            row = {"job_id": r["job_id"], **ex}
            if kind == "real_actual_not_in_predicted":
                real_examples.append(row)
            elif kind == "fp_actual_none":
                fp_examples.append(row)
    return {
        "jobs": len(jobs),
        "log_events": len(rows),
        "totals": dict(tot),
        "example_reasons": dict(reason),
        "real_divergences": real_examples,
        "false_positive_examples": fp_examples[:20],
        "per_job_diverge": {j: dict(c) for j, c in per_job.items()},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--min-beats", type=int, default=0,
                    help="only report the gate over jobs with >= this many beats (the audit "
                         "warns born-shorts (6-8 beats) can't exercise the cap class)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rows = _fetch(a.hours)
    if a.min_beats:
        rows = [r for r in rows if r["beats"] >= a.min_beats]
    res = analyze(rows)

    if a.json:
        print(json.dumps(res, indent=2, default=str))
        return

    t = res["totals"]
    print(f"=== figure-prepass SHADOW telemetry — last {a.hours}h "
          f"({res['jobs']} jobs, {res['log_events']} shadow events"
          + (f", beats>={a.min_beats}" if a.min_beats else "") + ") ===")
    if not rows:
        print("no shadow logs found — flag off? no jobs run yet? or wrong window.")
        return
    print(f"  beats={t.get('beats',0)}  agree={t.get('agree',0)}  diverge={t.get('diverge',0)}  "
          f"excluded_lensing={t.get('excluded_lensing',0)}")
    r = res["example_reasons"]
    real = r.get("real_actual_not_in_predicted", 0)
    fp = r.get("fp_actual_none", 0)
    print(f"  divergence breakdown: REAL(actual∉predicted)={real}  "
          f"likely-FP(actual=None)={fp}  other={r.get('other',0)}")
    print()
    if real == 0:
        print("  ✅ ZERO REAL divergences (the false-positive actual=None class is reproducible "
              "by the parallel design — see the module docstring).")
        print("     → the Stage-2 gate reads CLEAN on real ownership. Confirm across enough "
              "medium+long jobs before authorizing Stage 3.")
    else:
        print(f"  🔴 {real} REAL divergences — the parallel design would author a DIFFERENT figure "
              "than the sequential path. DO NOT flip Stage 3. Investigate:")
        for ex in res["real_divergences"][:15]:
            print(f"     job {str(ex.get('job_id'))[:8]} beat {ex.get('beat_index')}: "
                  f"actual={ex.get('actual')}  predicted={ex.get('predicted')}")
    if fp:
        print(f"\n  (info) {fp} actual=None divergences suppressed as likely false-positive "
              "(carded/declined beats). If the agree-condition is refined to treat actual=None "
              "as agree, these drop out of the raw 'diverge' count too.")


if __name__ == "__main__":
    main()
