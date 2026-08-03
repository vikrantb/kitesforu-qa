"""Which of the ADVERTISED visual engines does the pipeline ever actually render? $0, read-only.

    python3 scripts/engine_selection_census.py [--limit 120]

WHY THIS EXISTS (measured 2026-08-03, 118 jobs / 892 diagram beats over 11 days):

    15 of the 29 engines advertised to the live episode director had NEVER been rendered.
    flowchart (42.2%) + concept_mermaid (12.3%) were 54.5% of every diagram beat drawn.

That was found the expensive way. I built the `portrait_wall` engine, deployed it, confirmed it
was dispatchable and listed FIFTH in the 7090-char palette the director sees — and a real job
(b7b8edc7) authored 18 diagram beats and chose it ZERO times. Only then did I ask the general
question, and my engine turned out to be instance 16 of a 15-member class that already existed.

An engine advertised but never selected is worse than absent: it costs prompt tokens on every
director call, returns nothing, and looks correctly wired from every angle except this one.

TENET 9 CLEAN: consumes only what jobs already wrote (`diagram_spec.kind` on rendered shots).
No generation, no provider call, no mutation — it cannot change a job.
"""
import argparse
import re
from collections import Counter

from google.cloud import firestore
from workers.stages.visuals.diagram.factory import _REGISTRY
from workers.stages.visuals.director.director import _registry_palette_section

advertised = set(re.findall(r"^\s*-\s*([a-z_0-9]+):", _registry_palette_section(), re.M))

db = firestore.Client()
_ap = argparse.ArgumentParser()
_ap.add_argument("--limit", type=int, default=120, help="how many recent jobs to scan")
_args = _ap.parse_args()

jobs = list(
    db.collection("podcast_jobs")
    .order_by("created_at", direction="DESCENDING")
    .limit(_args.limit)
    .stream()
)

# Always print the WINDOW. A census with no date range invites "engine X is dead" when the real
# claim is only ever "engine X did not fire in these N jobs".
_dates = [v for v in ((s.to_dict() or {}).get("created_at") for s in jobs) if hasattr(v, "strftime")]
if _dates:
    print(f"window: {min(_dates):%Y-%m-%d} -> {max(_dates):%Y-%m-%d}")

def _diagram_kinds(node, out):
    """Collect every rendered `diagram_spec.kind` under `node`. Defined at module scope so it
    binds no loop variable (ruff B023) — the closure version silently shared `found` across jobs."""
    if isinstance(node, dict):
        ds = node.get("diagram_spec")
        if isinstance(ds, dict) and ds.get("kind"):
            out.append(str(ds["kind"]))
        for v in node.values():
            _diagram_kinds(v, out)
    elif isinstance(node, list):
        for v in node:
            _diagram_kinds(v, out)


kinds, jobs_with_visuals = Counter(), 0
for snap in jobs:
    found = []
    _diagram_kinds(snap.to_dict() or {}, found)
    if found:
        jobs_with_visuals += 1
        kinds.update(found)

print(f"jobs scanned: {len(jobs)}   with rendered diagram specs: {jobs_with_visuals}")
print(f"total diagram beats: {sum(kinds.values())}\n")
print("RENDERED (what the pipeline actually produces):")
for k, n in kinds.most_common():
    pct = 100.0 * n / max(sum(kinds.values()), 1)
    print(f"  {k:24} {n:5}  {pct:5.1f}%")

# An advertised engine is "reached" if its own name OR any kind it fits appears.
def reached(name):
    if name in kinds:
        return True
    eng = [e for e in _REGISTRY if e.name == name]
    return any(e.fit(k) > 0.4 for e in eng for k in kinds)

dead = sorted(a for a in advertised if not reached(a))
print(f"\nADVERTISED to the director: {len(advertised)}")
print(f"NEVER rendered in these {len(jobs)} jobs: {len(dead)}")
for d_ in dead:
    print("   ", d_)
