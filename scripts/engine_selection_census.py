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


# SPLIT BY SURFACE, ALWAYS. Blending them hides the severe case: measured 2026-08-03, a 120-job
# sample was 92 shorts and 28 episodes, and the blended flowchart share (42.2%) UNDERSTATED
# episodes, where it is 73.6% and 21 of 29 engines never fire. The surface marker is
# `format == "short_video"` — NOT `is_short` / `aspect_ratio`, which is what I guessed first, and
# which silently labelled all 120 jobs "episode".
by_surface: dict = {}
kinds, jobs_with_visuals = Counter(), 0
for snap in jobs:
    doc = snap.to_dict() or {}
    surface = "short_video" if doc.get("format") == "short_video" else "episode"
    found = []
    _diagram_kinds(doc, found)
    entry = by_surface.setdefault(surface, {"jobs": 0, "beats": Counter()})
    entry["jobs"] += 1
    entry["beats"].update(found)
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
def reached(name, in_kinds=None):
    """Did this engine draw anything? `in_kinds` scopes the question to one surface.

    Compares against the GENERALIST floor (0.4) rather than >0, because `_MermaidEngine` fits any
    string at 0.4 — an "some engine fits it" test passes for every name including nonsense.
    """
    in_kinds = kinds if in_kinds is None else in_kinds
    if name in in_kinds:
        return True
    eng = [e for e in _REGISTRY if e.name == name]
    return any(e.fit(k) > 0.4 for e in eng for k in in_kinds)

for _surf, _info in sorted(by_surface.items()):
    _b = _info["beats"]
    _tot = sum(_b.values())
    print(f"\n--- {_surf}: {_info['jobs']} jobs, {_tot} diagram beats")
    for _k, _n in _b.most_common(6):
        print(f"    {_k:22} {_n:5} {100.0 * _n / max(_tot, 1):5.1f}%")
    _dead = sorted(a for a in advertised if not reached(a, _b))
    print(f"    NEVER rendered on this surface: {len(_dead)} of {len(advertised)}")

dead = sorted(a for a in advertised if not reached(a))
print(f"\nADVERTISED to the director: {len(advertised)}")
print(f"NEVER rendered in these {len(jobs)} jobs: {len(dead)}")
for d_ in dead:
    print("   ", d_)
