#!/usr/bin/env python3
"""Trace a content-generation job into a self-contained interactive HTML DAG.

Usage:
    python3 trace_job.py <job_id> [--out /path/trace.html]
    python3 trace_job.py --from-file job_full.json [--out /path/trace.html]   # offline, $0

Pulls the podcast_jobs doc (Firestore, project kitesforu-dev) — or reads a cached
doc from --from-file — and reconstructs the FULL pipeline as a top-to-bottom,
connected node graph: every stage (timing, cost, status, in/out) wired to its
neighbours (with the audio ‖ visuals fork/join), and every LLM/TTS call it made
(provider, model, tokens, latency, cost, and the ACTUAL system + user prompt and
response). The HTML is fully self-contained (no external JS/CSS/fonts — CSP-safe)
and read-only. It NEVER writes to a job or triggers generation (Tenet 9: debug
tooling has zero cost/latency/quality impact on the user-facing pipeline).

Interaction: hover a node for a summary → click to open its full detail drawer →
click any call / nested object to drill deeper → Esc to step back up the stack.
Everything the pipeline recorded is reachable (down to the raw job doc).
"""
import argparse
import json

# ---- pipeline topology: which stages belong to which phase, in flow order ----
# The vertical spine, top -> bottom. audio & visuals run in PARALLEL (one band, two lanes).
PHASES = [
    ("intake",    "Intake & Plan",         "main",    ["job-initiate", "clarifier", "intake"]),
    ("research",  "Research",               "main",    ["job-research-planner", "job-research-assimilator",
                                                        "job-execute-tools", "research", "tools"]),
    ("architect", "Architect / Blueprint",  "main",    ["job-plan", "pre_tts_plan_gate", "architect", "blueprint"]),
    ("script",    "Script",                 "main",    ["job-script", "script", "content_craft", "outline_relevance",
                                                        "narrative_engagement", "name_covenant", "host2_value_check",
                                                        "recap_loop_check", "pacing_hygiene", "crispness_check",
                                                        "judge_consensus", "wow_critique"]),
    ("audio",     "Audio",                  "audio",   ["sfx_director", "music_director", "job-audio", "audio", "mastering"]),
    ("visuals",   "Visuals",                "visuals", ["visuals", "job-visuals"]),
    ("quality",   "Quality & Publish",      "main",    ["quality_gate", "quality", "release", "publish"]),
]
STAGE_PHASE = {s: pid for pid, _, _, ss in PHASES for s in ss}
PHASE_LANE = {pid: lane for pid, _, lane, _ in PHASES}
# audio & visuals share one vertical band (the parallel fork)
BAND_ORDER = ["intake", "research", "architect", "script", "__parallel__", "quality"]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _root_stage(s):
    s = str(s or "")
    if s in STAGE_PHASE:
        return s
    for known in STAGE_PHASE:
        if s.startswith(known + "."):
            return known
    return s.split(".")[0] if "." in s else s


_FREEFORM_PHASES = [
    ("research",  ("research", "tool", "assimilat")),
    ("architect", ("architect", "blueprint", "angle", "pedagog", "plan")),
    ("script",    ("script", "craft", "dialogue", "judge", "wow", "narrative", "outline")),
    ("audio",     ("tts", "voice", "music", "sfx", "master", "audio")),
    ("visuals",   ("visual", "diagram", "figure", "scene", "editorial", "shot", "render")),
    ("quality",   ("quality", "release", "publish", "qa")),
    ("intake",    ("initiate", "clarif", "intake")),
]


def _phase_from_freeform(s):
    s = str(s or "").lower()
    for phase, kws in _FREEFORM_PHASES:
        if any(k in s for k in kws):
            return phase
    return "script"


def _phase_of(stage):
    root = _root_stage(stage)
    return STAGE_PHASE.get(root) or _phase_from_freeform(stage)


def _pretty(name):
    return str(name).replace("job-", "").replace("_", " ").replace("-", " ").strip().title() or "?"


def _parse_ts(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v) / (1000.0 if v > 1e12 else 1.0)
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _status(sdata, cl):
    if cl.get("success") is False or sdata.get("status") == "failed" or sdata.get("error"):
        return "failed"
    if sdata.get("skipped") or sdata.get("skip_reason"):
        return "skipped"
    if sdata.get("status") in ("running", "in_progress"):
        return "running"
    return "done"


def _summarize_stage(sdata):
    for k in ("summary", "detail", "reason", "route", "outcome", "release_status", "decision"):
        if sdata.get(k):
            return str(sdata[k])[:220]
    keys = [k for k in sdata.keys() if not k.startswith("_")][:6]
    return ", ".join(keys) if keys else "(ran)"


def _jsonable(v):
    """Best-effort convert Firestore values (datetimes, refs) into JSON-safe data."""
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _load_doc(job_id=None, from_file=None):
    if from_file:
        with open(from_file) as f:
            return json.load(f)
    from google.cloud import firestore
    db = firestore.Client(project="kitesforu-dev")
    snap = db.collection("podcast_jobs").document(job_id).get()
    d = snap.to_dict()  # type: ignore[union-attr]
    if not d:
        raise SystemExit(f"job {job_id} not found in podcast_jobs")
    return d


def build_trace(job_id=None, from_file=None) -> dict:
    d = _load_doc(job_id, from_file)
    job_id = job_id or d.get("job_id") or d.get("id") or (from_file or "job")

    stages_doc = d.get("stages") or {}
    complete_logs = {c.get("stage"): c for c in (d.get("stage_complete_logs") or []) if isinstance(c, dict)}
    input_logs = d.get("stage_input_logs") or {}
    budget = d.get("budget_by_stage") or {}
    costs = d.get("costs") or {}
    milestones = [m for m in (d.get("milestones") or []) if isinstance(m, dict)]

    # ---- every LLM call, FULL fidelity (prompts, response, tokens, cost) ----
    llm_calls = []
    for c in (d.get("llm_call_logs") or []):
        if not isinstance(c, dict):
            continue
        stage = c.get("stage") or c.get("agent") or c.get("purpose") or "?"
        llm_calls.append({
            "kind": "llm",
            "stage": stage,
            "root": _root_stage(stage),
            "phase": _phase_of(stage),
            "purpose": c.get("purpose") or c.get("prompt_source") or "",
            "provider": c.get("provider") or "?",
            "model": c.get("model") or c.get("model_id") or "?",
            "in_tokens": c.get("input_tokens") or c.get("prompt_tokens") or 0,
            "out_tokens": c.get("output_tokens") or 0,
            "cache_read": c.get("cache_read_tokens") or 0,
            "cache_create": c.get("cache_create_tokens") or 0,
            "total_tokens": c.get("total_tokens") or 0,
            "cost": round(_num(c.get("cost")) or 0.0, 6),
            "duration_ms": c.get("duration_ms"),
            "success": bool(c.get("success", True)),
            "error": c.get("error"),
            "escalation": c.get("escalation_level") or 0,
            "attempt": c.get("attempt") or 0,
            "temperature": c.get("temperature"),
            "max_tokens": c.get("max_tokens"),
            "response_format": c.get("response_format"),
            "system_prompt": c.get("system_prompt") or "",
            "user_prompt": c.get("user_prompt") or "",
            "response_preview": c.get("response_preview") or "",
            "system_prompt_gcs": c.get("system_prompt_gcs_path") or "",
            "user_prompt_gcs": c.get("user_prompt_gcs_path") or "",
            "response_gcs": c.get("response_gcs_path") or "",
            "timestamp": str(c.get("timestamp") or "")[:23],
        })

    tts = []
    for t in (d.get("tts_segment_logs") or []):
        if not isinstance(t, dict):
            continue
        tts.append({
            "kind": "tts",
            "stage": "job-audio",
            "root": "job-audio",
            "phase": "audio",
            "purpose": "tts segment",
            "provider": t.get("provider") or "?",
            "model": t.get("voice_id") or t.get("voice") or t.get("model") or "tts",
            "out_tokens": t.get("chars") or t.get("char_count") or 0,
            "cost": round(_num(t.get("cost")) or 0.0, 6),
            "duration_ms": t.get("duration_ms"),
            "success": bool(t.get("success", True)),
            "error": t.get("error"),
            "escalation": 0,
            "text": t.get("text") or t.get("segment_text") or "",
            "raw": _jsonable(t),
        })

    # ---- attach every call to an OWNING stage (no synthetic node per sub-agent) ----
    # a call owns the stage doc named by its root; else the best stage in its phase.
    phase_stage_ids = {}
    for sid in stages_doc:
        phase_stage_ids.setdefault(_phase_of(sid), []).append(sid)

    def _owning_stage(call):
        if call["root"] in stages_doc:
            return call["root"]
        cands = phase_stage_ids.get(call["phase"], [])
        if cands:
            best = None
            for sid in cands:  # longest stage-id that prefixes the call's stage string
                if str(call["stage"]).startswith(sid) and (best is None or len(sid) > len(best)):
                    best = sid
            return best or cands[0]
        return f"__{call['phase']}__"

    calls_by_owner = {}
    for c in llm_calls + tts:
        calls_by_owner.setdefault(_owning_stage(c), []).append(c)

    # ---- stages ----
    stages = []
    for name, sdata in stages_doc.items():
        sdata = sdata if isinstance(sdata, dict) else {"value": sdata}
        cl = complete_logs.get(name, {})
        start = _parse_ts(sdata.get("stage_start") or sdata.get("started_at") or sdata.get("start"))
        end = _parse_ts(sdata.get("completed_at") or sdata.get("stage_end") or sdata.get("end") or cl.get("timestamp"))
        dur_ms = cl.get("duration_ms") or sdata.get("duration_ms")
        if dur_ms is None and start and end:
            dur_ms = int((end - start) * 1000)
        my_calls = calls_by_owner.get(name, [])
        cost = budget.get(name)
        if cost is None:
            cost = round(sum(_num(c.get("cost")) or 0 for c in my_calls), 6) or None
        phase = _phase_of(name)
        stages.append({
            "id": name,
            "name": _pretty(name),
            "phase": phase,
            "lane": PHASE_LANE.get(phase, "main"),
            "start": start,
            "end": end,
            "duration_ms": dur_ms,
            "status": _status(sdata, cl),
            "summary": cl.get("summary") or _summarize_stage(sdata),
            "cost_usd": round(_num(cost) or 0.0, 4),
            "n_calls": len(my_calls),
            "inputs": _jsonable(input_logs.get(name) if isinstance(input_logs, dict) else None)
                      or _jsonable(sdata.get("llm_input") or sdata.get("input")),
            "outputs": _jsonable(cl.get("result") or sdata.get("result") or sdata.get("llm_response") or sdata.get("output")),
            "raw": _jsonable(sdata),
            "calls": my_calls,
        })

    # phases that produced calls but have NO stage doc -> one synthetic node for the phase
    known = {s["id"] for s in stages}
    for owner, cs in calls_by_owner.items():
        if owner in known:
            continue
        phase = _phase_of(owner)
        stages.append({
            "id": owner, "name": _pretty(owner) + " · calls", "phase": phase,
            "lane": PHASE_LANE.get(phase, "main"),
            "start": min([t for t in (_parse_ts(c.get("timestamp")) for c in cs) if t is not None], default=None),
            "end": None, "duration_ms": None, "status": "done",
            "summary": f"{len(cs)} model call(s) recorded in this phase (no stage document)",
            "cost_usd": round(sum(_num(c.get("cost")) or 0 for c in cs), 4),
            "n_calls": len(cs), "inputs": None, "outputs": None, "raw": None, "calls": cs,
        })

    # order stages by start ts, then by phase spine order
    phase_order = {pid: i for i, (pid, _, _, _) in enumerate(PHASES)}
    stages.sort(key=lambda s: (s["start"] if s["start"] else 9e18,
                               phase_order.get(s["phase"], 99), s["name"]))

    # ---- totals ----
    starts = [s["start"] for s in stages if s["start"]]
    ends = [s["end"] for s in stages if s["end"]]
    total_dur = (max(ends) - min(starts)) if starts and ends else None
    total_cost = _num(costs.get("total_usd_actual")) or _num(costs.get("total_usd_estimate")) \
        or round(sum(s["cost_usd"] for s in stages), 4)

    return {
        "job_id": str(job_id),
        "topic": d.get("topic") or d.get("title") or "(untitled)",
        "status": d.get("status"),
        "content_type": d.get("content_type") or (d.get("preferences") or {}).get("_content_type") or "?",
        "format": d.get("format"),
        "created_at": str(d.get("created_at"))[:19],
        "totals": {
            "duration_s": round(total_dur, 1) if total_dur else None,
            "cost_usd": round(_num(total_cost) or 0.0, 4),
            "llm_calls": len(llm_calls),
            "tts_calls": len(tts),
            "stages": len(stages),
        },
        "band_order": BAND_ORDER,
        "phases": [{"id": pid, "name": nm, "lane": lane} for pid, nm, lane, _ in PHASES],
        "stages": stages,
        "milestones": [{"stage": m.get("stage"), "detail": m.get("detail"), "pct": m.get("pct"),
                        "kind": m.get("kind"), "ts": str(m.get("ts"))[:19]} for m in milestones],
        "costs": _jsonable(costs),
        "credit_breakdown": _jsonable(d.get("credit_breakdown")),
        "budget_by_stage": _jsonable(budget),
        "outputs": _jsonable(d.get("outputs")),
        "segment_beat_map": _jsonable(d.get("segment_beat_map")),
        "master_segment_timeline": _jsonable(d.get("master_segment_timeline")),
        # everything else, so nothing is hidden
        "raw_doc": _jsonable({k: v for k, v in d.items()
                              if k not in ("llm_call_logs",)}),  # calls already embedded per-stage
    }


def render_html(trace: dict) -> str:
    data = json.dumps(trace, ensure_ascii=False)
    title = f"Trace — {str(trace['topic'])[:60]}"
    return _HTML.replace("__DATA__", data).replace("__TITLE__", title)


# ============================================================ HTML / JS ====
_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --bg:#0b0f16; --bg2:#0d1117; --panel:#141a22; --panel2:#1a222c; --line:#242e3a; --line2:#30404f;
  --tx:#e6edf3; --mut:#94a3b3; --dim:#63707e;
  --main:#58a6ff; --audio:#bc8cff; --visuals:#f0883e; --quality:#3fb950;
  --acc:#58a6ff; --ok:#3fb950; --warn:#d29922; --crit:#f85149; --edge:#37424f;
}
:root[data-theme="light"]{
  --bg:#eef1f5; --bg2:#f6f8fa; --panel:#fff; --panel2:#f2f5f8; --line:#d5dce4; --line2:#c3cdd7;
  --tx:#1f2733; --mut:#586573; --dim:#8b97a4;
  --main:#0969da; --audio:#8250df; --visuals:#bc4c00; --quality:#1a7f37;
  --acc:#0969da; --ok:#1a7f37; --warn:#9a6700; --crit:#cf222e; --edge:#c3cdd7;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--tx);
  font:13.5px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;overflow:hidden}
.mono{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
/* ---------- top bar ---------- */
header{position:fixed;top:0;left:0;right:0;height:56px;z-index:30;display:flex;align-items:center;
  gap:16px;padding:0 18px;background:color-mix(in srgb,var(--bg) 82%,transparent);
  backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
header .title{font-weight:650;font-size:15px;letter-spacing:-.01em;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;max-width:38vw}
header .sub{color:var(--mut);font-size:11.5px;display:flex;gap:12px;flex-wrap:wrap}
header .sub b{color:var(--tx);font-weight:600}
header .spacer{flex:1}
.kpi{display:flex;flex-direction:column;align-items:flex-end;line-height:1.15}
.kpi .v{font-weight:650;font-size:15px}
.kpi .l{color:var(--dim);font-size:9.5px;text-transform:uppercase;letter-spacing:.06em}
.search{background:var(--panel);border:1px solid var(--line);border-radius:8px;color:var(--tx);
  padding:7px 10px;font-size:12.5px;width:180px;outline:none}
.search:focus{border-color:var(--acc)}
.btn{background:var(--panel);border:1px solid var(--line);color:var(--mut);border-radius:8px;
  padding:6px 9px;cursor:pointer;font-size:12px;user-select:none}
.btn:hover{border-color:var(--acc);color:var(--tx)}
/* ---------- graph canvas ---------- */
#stage{position:fixed;inset:56px 0 0 0;overflow:hidden;cursor:grab;background:
  radial-gradient(circle at 50% 0,color-mix(in srgb,var(--main) 5%,transparent),transparent 60%)}
#stage.drag{cursor:grabbing}
svg{width:100%;height:100%;display:block}
.band-label{fill:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.12em;font-weight:600}
.band-rule{stroke:var(--line);stroke-width:1;stroke-dasharray:2 6}
.edge{fill:none;stroke:var(--edge);stroke-width:1.6;opacity:.75}
.edge.fork{stroke-dasharray:5 4}
.edge.hot{stroke:var(--acc);opacity:1;stroke-width:2.4}
.node{cursor:pointer}
.node rect.card{fill:var(--panel);stroke:var(--line2);stroke-width:1.4;rx:12}
.node:hover rect.card{stroke:var(--pc);filter:drop-shadow(0 3px 10px rgba(0,0,0,.35))}
.node.sel rect.card{stroke:var(--pc);stroke-width:2.2;fill:color-mix(in srgb,var(--pc) 10%,var(--panel))}
.node.dim{opacity:.28}
.node rect.stripe{rx:2}
.node .nm{fill:var(--tx);font-weight:600;font-size:13px}
.node .mt{fill:var(--mut);font-size:11px}
.node .mt b{fill:var(--tx);font-weight:600}
.node .badge{fill:var(--pc);font-size:10px;font-weight:600}
.ring{fill:none;stroke-width:2.4}
/* ---------- hover tooltip ---------- */
#tip{position:fixed;z-index:40;pointer-events:none;max-width:320px;background:var(--panel2);
  border:1px solid var(--line2);border-radius:10px;padding:10px 12px;font-size:12px;
  box-shadow:0 8px 28px rgba(0,0,0,.45);opacity:0;transition:opacity .1s;transform:translateY(4px)}
#tip.on{opacity:1;transform:none}
#tip .tt{font-weight:650;font-size:12.5px;margin-bottom:4px}
#tip .tr{color:var(--mut);display:flex;gap:10px;flex-wrap:wrap}
#tip .tr b{color:var(--tx)}
#tip .ts{color:var(--dim);margin-top:6px;font-size:11.5px;line-height:1.45}
/* ---------- drawer ---------- */
#drawer{position:fixed;top:56px;right:0;bottom:0;width:min(560px,92vw);z-index:35;
  background:var(--bg2);border-left:1px solid var(--line);transform:translateX(102%);
  transition:transform .22s cubic-bezier(.4,0,.2,1);display:flex;flex-direction:column}
#drawer.on{transform:none;box-shadow:-18px 0 44px rgba(0,0,0,.4)}
.dbar{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--line);
  background:var(--panel)}
.crumbs{flex:1;display:flex;align-items:center;gap:6px;font-size:12px;color:var(--mut);overflow:hidden}
.crumbs .cr{cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:220px}
.crumbs .cr:hover{color:var(--acc)}
.crumbs .cr.cur{color:var(--tx);font-weight:600}
.crumbs .sep{color:var(--dim)}
.dclose{cursor:pointer;color:var(--mut);border:1px solid var(--line);border-radius:7px;
  width:28px;height:28px;display:grid;place-items:center;font-size:15px}
.dclose:hover{color:var(--tx);border-color:var(--acc)}
.dbody{flex:1;overflow:auto;padding:16px 18px 60px}
.dh{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px}
.dh h2{margin:0;font-size:18px;font-weight:650;letter-spacing:-.01em}
.pill{font-size:10.5px;font-weight:600;padding:2px 8px;border-radius:20px;
  border:1px solid color-mix(in srgb,var(--c) 40%,transparent);color:var(--c);
  background:color-mix(in srgb,var(--c) 14%,transparent)}
.metrics{display:flex;gap:16px;flex-wrap:wrap;color:var(--mut);font-size:12px;margin:10px 0 4px}
.metrics .m b{color:var(--tx);font-weight:650;font-size:14px}
.metrics .m span{display:block;font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim)}
.summary{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:10px 12px;
  margin:12px 0;font-size:12.5px;color:var(--tx)}
.sec{margin-top:18px}
.sec>.st{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);
  margin-bottom:8px;display:flex;justify-content:space-between;align-items:center}
/* calls table */
.calltbl{width:100%;border-collapse:collapse;font-size:12px}
.calltbl th{text-align:left;color:var(--dim);font-size:10px;text-transform:uppercase;
  letter-spacing:.05em;padding:5px 7px;border-bottom:1px solid var(--line)}
.calltbl td{padding:6px 7px;border-bottom:1px solid var(--line);vertical-align:middle}
.calltbl tr{cursor:pointer}
.calltbl tbody tr:hover{background:color-mix(in srgb,var(--acc) 9%,transparent)}
.tag{font-size:9.5px;font-weight:600;padding:1px 6px;border-radius:5px;
  border:1px solid color-mix(in srgb,var(--c) 35%,transparent);color:var(--c);
  background:color-mix(in srgb,var(--c) 13%,transparent)}
/* prompt blocks */
.pb{margin-top:12px}
.pb .pl{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);
  margin-bottom:5px;display:flex;justify-content:space-between}
.pb .pl .gcs{color:var(--dim);text-transform:none;letter-spacing:0;font-size:10.5px}
.pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:11px 12px;margin:0;
  white-space:pre-wrap;word-break:break-word;font:11.5px/1.55 ui-monospace,Menlo,monospace;
  color:var(--tx);max-height:340px;overflow:auto}
/* json tree */
.jt{font:11.5px/1.5 ui-monospace,Menlo,monospace;color:var(--tx)}
.jt .row{padding-left:14px}
.jt .k{color:var(--audio)}
.jt .s{color:var(--quality)}
.jt .n{color:var(--visuals)}
.jt .b{color:var(--main)}
.jt .nul{color:var(--dim)}
.jt .tw{cursor:pointer;user-select:none;color:var(--mut)}
.jt .tw:hover{color:var(--acc)}
.jt .cnt{color:var(--dim)}
.jt .drill{cursor:pointer;color:var(--acc);text-decoration:underline dotted;text-underline-offset:2px}
.empty{color:var(--dim);font-size:12px;padding:6px 0}
/* mini bar */
.mb{height:4px;border-radius:3px;background:var(--panel2);overflow:hidden;margin-top:3px}
.mb>i{display:block;height:100%;background:var(--oc)}
.orow{display:grid;grid-template-columns:1fr auto;gap:1px 10px;align-items:center;margin-bottom:8px;
  cursor:pointer}
.orow:hover .on{color:var(--acc)}
.on{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12px}
.ov{font-size:11.5px}
.ov .sh{color:var(--dim);font-size:10px}
.obar{grid-column:1/-1}
/* zoom controls / legend */
.ctrls{position:fixed;left:16px;bottom:16px;z-index:20;display:flex;gap:6px}
.legend{position:fixed;right:16px;bottom:16px;z-index:20;background:color-mix(in srgb,var(--bg) 80%,transparent);
  border:1px solid var(--line);border-radius:9px;padding:8px 11px;font-size:11px;display:flex;
  gap:12px;backdrop-filter:blur(8px)}
.legend .lg{display:flex;align-items:center;gap:5px;color:var(--mut)}
.legend .dot{width:9px;height:9px;border-radius:3px}
.hintbar{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:19;color:var(--dim);
  font-size:11px;background:color-mix(in srgb,var(--bg) 70%,transparent);padding:5px 12px;border-radius:20px}
@media(max-width:760px){header .title{max-width:44vw}.legend,.hintbar{display:none}}
/* wall-clock timeline (critical path) */
#timeline{position:fixed;left:0;right:0;bottom:0;z-index:25;max-height:46vh;background:var(--bg2);
  border-top:1px solid var(--line2);transform:translateY(102%);transition:transform .22s cubic-bezier(.4,0,.2,1);
  box-shadow:0 -14px 40px rgba(0,0,0,.4);display:flex;flex-direction:column}
#timeline.on{transform:none}
.tlhead{padding:9px 16px;border-bottom:1px solid var(--line);font-size:12.5px;display:flex;align-items:center;gap:12px}
.tlhead .mono{color:var(--mut);font-size:11.5px}
.tlclose{margin-left:auto;cursor:pointer;color:var(--mut);border:1px solid var(--line);border-radius:6px;width:24px;height:24px;display:grid;place-items:center}
.tlclose:hover{color:var(--tx);border-color:var(--acc)}
.tlbody{overflow:auto;padding:10px 16px 16px}
.trow{display:grid;grid-template-columns:150px 1fr 62px;gap:8px;align-items:center;margin-bottom:5px;cursor:pointer}
.trow:hover .tnm{color:var(--acc)}
.tnm{font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:right;color:var(--mut)}
.ttrack{position:relative;height:15px;background:var(--panel2);border-radius:4px}
.ttrack>i{position:absolute;height:100%;border-radius:4px;top:0}
.ttrack>i.crit{outline:1.5px solid var(--tx);outline-offset:-1px}
.tdur{font-size:11px;text-align:right}
.taxis{grid-column:2/3;position:relative;height:14px;color:var(--dim);font-size:9.5px;margin-bottom:4px}
.taxis span{position:absolute;transform:translateX(-50%)}
</style></head><body>
<header>
  <div class="title" id="topic"></div>
  <div class="sub" id="hsub"></div>
  <div class="spacer"></div>
  <input class="search" id="search" placeholder="find stage / provider…" autocomplete="off">
  <div class="kpi"><span class="v" id="kdur"></span><span class="l">wall clock</span></div>
  <div class="kpi"><span class="v" id="kcost"></span><span class="l">cost</span></div>
  <div class="kpi"><span class="v" id="kcalls"></span><span class="l">llm calls</span></div>
  <div class="btn" id="btnTimeline">⏱ timeline</div>
  <div class="btn" id="btnData">Job data</div>
  <div class="btn" onclick="toggleTheme()">◐</div>
</header>
<div id="stage"><svg id="svg"><g id="viewport"></g></svg></div>
<div id="tip"></div>
<div id="timeline">
  <div class="tlhead"><b>Wall-clock timeline</b> <span id="tlmeta" class="mono"></span>
    <span class="tlclose" onclick="toggleTimeline()">✕</span></div>
  <div class="tlbody" id="tlbody"></div>
</div>

<div id="drawer">
  <div class="dbar">
    <div class="crumbs" id="crumbs"></div>
    <div class="dclose" title="Esc" onclick="closeDrawer()">✕</div>
  </div>
  <div class="dbody" id="dbody"></div>
</div>

<div class="ctrls">
  <div class="btn" onclick="zoomBy(1.2)">+</div>
  <div class="btn" onclick="zoomBy(1/1.2)">−</div>
  <div class="btn" onclick="fit()">fit</div>
</div>
<div class="legend" id="legend"></div>
<div class="hintbar">hover a node · click to open · click deeper to drill · Esc to step back</div>

<script>
const T = __DATA__;
const PC = {main:'var(--main)',audio:'var(--audio)',visuals:'var(--visuals)',quality:'var(--quality)'};
const SC = {done:'var(--ok)',failed:'var(--crit)',skipped:'var(--dim)',running:'var(--warn)'};
const KC = {llm:'var(--main)',tts:'var(--audio)'};
const esc = s => (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmtMs = m => m==null?'—':(m<1000?Math.round(m)+'ms':(m/1000).toFixed(m<10000?1:0)+'s');
const fmtUsd = v => (v==null||v===0)?(v===0?'$0':'—'):'$'+(Math.abs(v)<0.01?(+v).toFixed(4):(+v).toFixed(2));
const fmtTok = t => t==null?'—':(t>=1000?(t/1000).toFixed(1)+'k':t);

/* ---- header ---- */
topic.textContent = T.topic;
// routing + cost knobs (manifest: content_purpose branches the whole DAG; quality_tier is the
// load-bearing cost knob; allow_premium is the premium source of truth) — pulled from raw_doc
const RD = T.raw_doc || {};
const purpose = RD.content_purpose, qt = RD.quality_tier, prem = RD.allow_premium;
hsub.innerHTML = `<span><b>${esc(T.format||T.content_type||'?')}</b></span>`
  + (purpose?`<span>purpose <b>${esc(purpose)}</b></span>`:'')
  + (qt?`<span>tier <b>${esc(qt)}</b></span>`:'')
  + (prem===true?`<span><b style="color:var(--visuals)">premium</b></span>`:'')
  + `<span>status <b>${esc(T.status||'?')}</b></span>`
  + `<span class="mono">${esc(T.job_id)}</span>`
  + (T.created_at&&T.created_at!=='None'?`<span>${esc(T.created_at)}</span>`:'');
kdur.textContent = T.totals.duration_s?T.totals.duration_s+'s':'—';
kcost.textContent = '$'+(+T.totals.cost_usd).toFixed(2);
kcalls.textContent = T.totals.llm_calls + (T.totals.tts_calls?('+'+T.totals.tts_calls+'tts'):'');
legend.innerHTML = ['main','audio','visuals','quality'].map(p=>
  `<div class="lg"><span class="dot" style="background:${PC[p]}"></span>${p}</div>`).join('');

/* ============ layered layout: top -> bottom, parallel audio|visuals ============ */
const NW=190, NH=62, HGAP=30, VGAP=26, PERROW=6, TOP=64, BANDGAP=66;
const stageById = Object.fromEntries(T.stages.map(s=>[s.id,s]));
// group stages by band
const bands = T.band_order.map(bid=>{
  if(bid==='__parallel__'){
    const audio=T.stages.filter(s=>s.lane==='audio').sort(byTime);
    const visuals=T.stages.filter(s=>s.lane==='visuals').sort(byTime);
    return {id:'__parallel__', clusters:[audio,visuals].filter(c=>c.length)};
  }
  const ss=T.stages.filter(s=>s.phase===bid && s.lane==='main').sort(byTime);
  return {id:bid, clusters: ss.length?[ss]:[]};
}).filter(b=>b.clusters.length);
function byTime(a,b){ return (a.start||9e18)-(b.start||9e18) || a.name.localeCompare(b.name); }

// a cluster lays out as a grid (max PERROW columns)
const clCols = c => Math.min(PERROW, c.length);
const clW = c => clCols(c)*(NW+HGAP)-HGAP;
const clH = c => Math.ceil(c.length/clCols(c))*(NH+VGAP)-VGAP;
const laneGapFor = b => b.clusters.length>1?HGAP*2.4:0;
const bandRowW = b => b.clusters.reduce((a,c)=>a+clW(c),0)+laneGapFor(b)*(b.clusters.length-1);

const CW = Math.max(960, Math.max(...bands.map(bandRowW)) + 160);

// assign positions band by band, cluster grids centered
let y=TOP; const bandY={};
bands.forEach(b=>{
  bandY[b.id]=y;
  const laneGap=laneGapFor(b);
  let x=(CW-bandRowW(b))/2, maxH=0;
  b.clusters.forEach(c=>{
    const cols=clCols(c);
    c.forEach((s,i)=>{ s._x=x+(i%cols)*(NW+HGAP); s._y=y+Math.floor(i/cols)*(NH+VGAP); });
    x+=clW(c)+laneGap; maxH=Math.max(maxH,clH(c));
  });
  y += maxH + BANDGAP;
});
const CH = y + 24;

// edges: intra-cluster sequential + inter-band sinks->sources (fork/join)
const edges=[];
for(let i=0;i<bands.length;i++){
  const b=bands[i];
  b.clusters.forEach(c=>{ for(let j=0;j<c.length-1;j++) edges.push([c[j],c[j+1],'seq']); });
  if(i<bands.length-1){
    const nb=bands[i+1];
    const sinks=b.clusters.map(c=>c[c.length-1]);
    const sources=nb.clusters.map(c=>c[0]);
    const fork = b.clusters.length<nb.clusters.length || nb.clusters.length>1 && b.id!=='__parallel__';
    sinks.forEach(sk=>sources.forEach(sr=>edges.push([sk,sr, (nb.clusters.length>1||b.clusters.length>1)?'fork':'spine'])));
  }
}

/* ---- render svg ---- */
const NS='http://www.w3.org/2000/svg';
const svg=document.getElementById('svg'), vp=document.getElementById('viewport');
svg.setAttribute('viewBox',`0 0 ${CW} ${CH}`);
function el(n,a,p){const e=document.createElementNS(NS,n);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e;}

// band labels + rules + per-phase roll-up (where the time & money go)
bands.forEach(b=>{
  const nm = b.id==='__parallel__' ? 'Audio ‖ Visuals'
    : (T.phases.find(p=>p.id===b.id)||{}).name || b.id;
  const ss=b.clusters.flat();
  const bCost=ss.reduce((a,s)=>a+(s.cost_usd||0),0);
  const bCalls=ss.reduce((a,s)=>a+(s.n_calls||0),0);
  const bDur=Math.max(0,...ss.map(s=>s.duration_ms||0)); // band wall-clock ~ slowest lane member
  el('line',{x1:20,y1:bandY[b.id]-22,x2:CW-20,y2:bandY[b.id]-22,class:'band-rule'},vp);
  el('text',{x:22,y:bandY[b.id]-27,class:'band-label'},vp).textContent=nm;
  const roll=[bDur?fmtMs(bDur):null, bCost?fmtUsd(bCost):null, bCalls?bCalls+' calls':null].filter(Boolean).join('   ');
  if(roll){const rt=el('text',{x:CW-22,y:bandY[b.id]-27,'text-anchor':'end','font-size':10.5,fill:'var(--dim)',class:'mono'},vp);rt.textContent=roll;}
});
// edges
const edgeEls=[];
edges.forEach(([a,c,kind])=>{
  const x1=a._x+NW/2, y1=a._y+NH, x2=c._x+NW/2, y2=c._y;
  const my=(y1+y2)/2;
  const d=`M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`;
  const p=el('path',{d,class:'edge'+(kind==='fork'?' fork':'')},vp);
  edgeEls.push({p,a:a.id,c:c.id});
});
// duplicate pretty-names -> show raw id to disambiguate
const nameCount={}; T.stages.forEach(s=>nameCount[s.name]=(nameCount[s.name]||0)+1);
// nodes
T.stages.forEach(s=>{
  const dup=nameCount[s.name]>1;
  const g=el('g',{class:'node','data-id':s.id,transform:`translate(${s._x},${s._y})`,style:`--pc:${PC[s.lane]||'var(--main)'}`},vp);
  el('rect',{class:'card',width:NW,height:NH,rx:12,x:0,y:0},g);
  el('rect',{class:'stripe',x:0,y:0,width:5,height:NH,rx:2,fill:PC[s.lane]||'var(--main)'},g);
  // status ring
  el('circle',{class:'ring',cx:NW-15,cy:16,r:5,stroke:SC[s.status]||'var(--dim)'},g);
  // warning marker: any failed call (crit) or escalation (warn) in this stage
  const nFail=(s.calls||[]).filter(c=>!c.success).length;
  const nEsc=(s.calls||[]).filter(c=>c.escalation>0).length;
  if(nFail||nEsc){const wt=el('text',{x:NW-27,y:20,'text-anchor':'end','font-size':10,'font-weight':700,fill:nFail?'var(--crit)':'var(--warn)'},g);wt.textContent=`⚠${nFail||nEsc}`;}
  const nm=el('text',{class:'nm',x:15,y:20},g); nm.textContent = clip(s.name,22);
  if(dup){const idt=el('text',{x:15,y:33,'font-size':9.5,fill:'var(--dim)',class:'mono'},g);idt.textContent=clip(s.id,26);}
  const mt=el('text',{class:'mt',x:15,y:dup?46:41},g);
  mt.innerHTML=`<tspan>${fmtMs(s.duration_ms)}</tspan>`
    + (s.cost_usd?`<tspan dx="10" style="fill:var(--tx);font-weight:600">${fmtUsd(s.cost_usd)}</tspan>`:'');
  if(s.n_calls){const bd=el('text',{class:'badge',x:15,y:dup?58:56},g);bd.textContent=`${s.n_calls} call${s.n_calls>1?'s':''}`;}
  // cost intensity bar along the bottom
  if(s.cost_usd>0){
    const w=Math.min(NW-30,(NW-30)*s.cost_usd/(Math.max(...T.stages.map(x=>x.cost_usd||0))||1));
    el('rect',{x:NW-15-w,y:NH-6,width:w,height:3,rx:1.5,fill:PC[s.lane],opacity:.55},g);
  }
  g.addEventListener('click',e=>{e.stopPropagation();openStage(s.id);});
  g.addEventListener('mousemove',e=>showTip(s,e));
  g.addEventListener('mouseleave',hideTip);
});
function clip(s,n){s=String(s);return s.length>n?s.slice(0,n-1)+'…':s;}

/* ---- pan / zoom ---- */
let vx=0,vy=0,scale=1;
function apply(){vp.setAttribute('transform',`translate(${vx},${vy}) scale(${scale})`);}
function fit(){
  const r=document.getElementById('stage').getBoundingClientRect();
  const s=Math.min((r.width-48)/CW,(r.height-48)/CH);
  scale=Math.max(0.2,Math.min(1.15,s));
  vx=(r.width-CW*scale)/2;
  vy=(CH*scale < r.height-40) ? (r.height-CH*scale)/2 : 24;
  apply();
}
function zoomBy(f){const r=document.getElementById('stage').getBoundingClientRect();
  const cx=r.width/2,cy=r.height/2; vx=cx-(cx-vx)*f; vy=cy-(cy-vy)*f; scale*=f; apply();}
const st=document.getElementById('stage');
st.addEventListener('wheel',e=>{e.preventDefault();const r=st.getBoundingClientRect();
  const mx=e.clientX-r.left,my=e.clientY-r.top;const f=e.deltaY<0?1.1:1/1.1;
  vx=mx-(mx-vx)*f;vy=my-(my-vy)*f;scale=Math.max(0.15,Math.min(3,scale*f));apply();},{passive:false});
let dragging=false,lx,ly;
st.addEventListener('mousedown',e=>{if(e.target.closest('.node'))return;dragging=true;lx=e.clientX;ly=e.clientY;st.classList.add('drag');});
window.addEventListener('mousemove',e=>{if(!dragging)return;vx+=e.clientX-lx;vy+=e.clientY-ly;lx=e.clientX;ly=e.clientY;apply();});
window.addEventListener('mouseup',()=>{dragging=false;st.classList.remove('drag');});

/* ---- hover tooltip ---- */
const tip=document.getElementById('tip');
function showTip(s,e){
  const provs={};(s.calls||[]).forEach(c=>provs[c.provider]=(provs[c.provider]||0)+1);
  tip.innerHTML=`<div class="tt">${esc(s.name)}</div>`
   +`<div class="tr"><span>${esc(s.phase)}</span><span>·</span><span>${esc(s.status)}</span>`
   +`<span>·</span><span><b>${fmtMs(s.duration_ms)}</b></span>`
   +(s.cost_usd?`<span><b>${fmtUsd(s.cost_usd)}</b></span>`:'')
   +(s.n_calls?`<span><b>${s.n_calls}</b> calls</span>`:'')+`</div>`
   +(Object.keys(provs).length?`<div class="tr" style="margin-top:4px">${Object.entries(provs).map(([p,n])=>`<span>${esc(p)}·${n}</span>`).join('')}</div>`:'')
   +(s.summary?`<div class="ts">${esc(clip(s.summary,180))}</div>`:'');
  tip.classList.add('on');
  const pad=14,w=tip.offsetWidth,h=tip.offsetHeight;
  let x=e.clientX+pad,yy=e.clientY+pad;
  if(x+w>innerWidth-8)x=e.clientX-w-pad; if(yy+h>innerHeight-8)yy=e.clientY-h-pad;
  tip.style.left=x+'px';tip.style.top=yy+'px';
}
function hideTip(){tip.classList.remove('on');}

/* ================= drawer + drill stack ================= */
let stack=[]; // each: {title, render:()->html}
const drawer=document.getElementById('drawer'), dbody=document.getElementById('dbody');
function pushView(v){stack.push(v);renderDrawer();}
function popTo(i){stack=stack.slice(0,i+1);renderDrawer();}
function closeDrawer(){stack=[];drawer.classList.remove('on');
  document.querySelectorAll('.node.sel').forEach(n=>n.classList.remove('sel'));hideTip();}
function renderDrawer(){
  if(!stack.length){closeDrawer();return;}
  drawer.classList.add('on');
  const cr=document.getElementById('crumbs');
  cr.innerHTML=stack.map((v,i)=>`<span class="cr ${i===stack.length-1?'cur':''}" onclick="popTo(${i})">${esc(v.title)}</span>`
    +(i<stack.length-1?'<span class="sep">▸</span>':'')).join('');
  dbody.innerHTML=stack[stack.length-1].render();
  dbody.scrollTop=0;
}
function openStage(id){
  document.querySelectorAll('.node').forEach(n=>n.classList.toggle('sel',n.dataset.id===id));
  hideTip(); highlightEdges(id);
  stack=[{title:stageById[id].name, render:()=>stageView(id)}]; renderDrawer();
}
function highlightEdges(id){edgeEls.forEach(e=>e.p.classList.toggle('hot',e.a===id||e.c===id));}

function stageView(id){
  const s=stageById[id];
  const provs={};(s.calls||[]).forEach(c=>provs[c.provider]=(provs[c.provider]||0)+1);
  let h=`<div class="dh"><h2>${esc(s.name)}</h2>`
   +`<span class="pill" style="--c:${SC[s.status]}">${esc(s.status)}</span>`
   +`<span class="pill" style="--c:${PC[s.lane]}">${esc(s.phase)}</span></div>`
   +`<div class="metrics">`
   +`<div class="m"><b>${fmtMs(s.duration_ms)}</b><span>duration</span></div>`
   +`<div class="m"><b>${fmtUsd(s.cost_usd)}</b><span>cost</span></div>`
   +`<div class="m"><b>${s.n_calls}</b><span>model calls</span></div>`
   +(Object.keys(provs).length?`<div class="m"><b>${esc(Object.keys(provs).join(', '))}</b><span>providers</span></div>`:'')
   +`</div>`;
  if(s.summary) h+=`<div class="summary">${esc(s.summary)}</div>`;
  // calls
  if((s.calls||[]).length){
    // orchestra summary: group by purpose (legible when a stage fires 100s of cheap calls)
    if(s.calls.length>8){
      const gp={};
      s.calls.forEach(c=>{const k=c.purpose||c.model||'call';const g=gp[k]||(gp[k]={n:0,tok:0,cost:0,ms:0,fail:0});g.n++;g.tok+=c.out_tokens||0;g.cost+=c.cost||0;g.ms+=c.duration_ms||0;if(!c.success)g.fail++;});
      const rows=Object.entries(gp).sort((a,b)=>b[1].n-a[1].n);
      h+=`<div class="sec"><div class="st">Orchestra <span class="mono" style="color:var(--dim)">${rows.length} kinds · ${s.calls.length} calls</span></div>`
       +rows.map(([k,g])=>`<div class="orow" style="cursor:default">
          <span class="on">${esc(k)}${g.fail?` <span class="tag" style="--c:var(--crit)">${g.fail} fail</span>`:''}</span>
          <span class="ov mono"><b>×${g.n}</b> <span class="sh">${fmtTok(g.tok)} tok${g.cost?' · '+fmtUsd(g.cost):''}</span></span>
          <div class="obar mb" style="--oc:${PC[s.lane]}"><i style="width:${Math.max(4,100*g.n/rows[0][1].n)}%"></i></div></div>`).join('')
       +`</div>`;
    }
    h+=`<div class="sec"><div class="st">Model calls <span class="mono" style="color:var(--dim)">${s.calls.length}</span></div>`
     +`<table class="calltbl"><thead><tr><th></th><th>provider · model</th><th>purpose</th><th>tok</th><th>ms</th><th>$</th></tr></thead><tbody>`
     +s.calls.map((c,i)=>`<tr onclick="openCall('${esc(id)}',${i})">
        <td><span class="tag" style="--c:${KC[c.kind]}">${c.kind}</span></td>
        <td class="mono">${esc(c.provider)}<br><span style="color:var(--dim)">${esc(clip(c.model,22))}</span></td>
        <td>${esc(clip(c.purpose||'',26))}${c.escalation?` <span class="tag" style="--c:var(--warn)">esc${c.escalation}</span>`:''}${c.success?'':' <span class="tag" style="--c:var(--crit)">fail</span>'}</td>
        <td class="mono">${fmtTok(c.out_tokens)}</td><td class="mono">${fmtMs(c.duration_ms)}</td>
        <td class="mono">${c.cost?fmtUsd(c.cost):'—'}</td></tr>`).join('')
     +`</tbody></table></div>`;
  }
  // input / output / raw
  h+=ioSection('Input →', s.inputs, id, 'inputs');
  h+=ioSection('→ Output', s.outputs, id, 'outputs');
  if(s.raw) h+=`<div class="sec"><div class="st">Raw stage record</div>${jsonTree(s.raw,id+':raw')}</div>`;
  return h;
}
function ioSection(label,val,id,key){
  if(val==null) return `<div class="sec"><div class="st">${esc(label)}</div><div class="empty">not captured</div></div>`;
  return `<div class="sec"><div class="st">${esc(label)}</div>${jsonTree(val,id+':'+key)}</div>`;
}

function openCall(stageId,i){
  const c=stageById[stageId].calls[i];
  pushView({title:(c.provider||'call')+' · '+clip(c.model,14), render:()=>callView(stageId,i)});
}
function callView(stageId,i){
  const c=stageById[stageId].calls[i];
  let h=`<div class="dh"><h2>${esc(c.purpose||c.kind)}</h2>`
   +`<span class="pill" style="--c:${KC[c.kind]}">${esc(c.kind)}</span>`
   +(c.success?'':`<span class="pill" style="--c:var(--crit)">failed</span>`)
   +(c.escalation?`<span class="pill" style="--c:var(--warn)">escalation ${c.escalation}</span>`:'')+`</div>`
   +`<div class="metrics">`
   +`<div class="m"><b>${esc(c.provider)}</b><span>provider</span></div>`
   +`<div class="m"><b>${esc(clip(c.model,18))}</b><span>model</span></div>`
   +`<div class="m"><b>${fmtMs(c.duration_ms)}</b><span>latency</span></div>`
   +`<div class="m"><b>${c.cost?fmtUsd(c.cost):'—'}</b><span>cost</span></div>`
   +`</div>`
   +`<div class="metrics">`
   +`<div class="m"><b>${fmtTok(c.in_tokens)}</b><span>in tok</span></div>`
   +`<div class="m"><b>${fmtTok(c.out_tokens)}</b><span>out tok</span></div>`
   +(c.cache_read?`<div class="m"><b>${fmtTok(c.cache_read)}</b><span>cache rd</span></div>`:'')
   +(c.temperature!=null?`<div class="m"><b>${esc(c.temperature)}</b><span>temp</span></div>`:'')
   +(c.max_tokens?`<div class="m"><b>${fmtTok(c.max_tokens)}</b><span>max tok</span></div>`:'')
   +(c.timestamp?`<div class="m"><b class="mono" style="font-size:11px">${esc(c.timestamp.slice(11,19)||c.timestamp)}</b><span>at</span></div>`:'')
   +`</div>`;
  if(c.error) h+=`<div class="summary" style="border-color:var(--crit);color:var(--crit)">${esc(c.error)}</div>`;
  if(c.kind==='tts'){
    if(c.text) h+=promptBlock('Spoken text',c.text,'');
    if(c.raw) h+=`<div class="sec"><div class="st">Raw segment</div>${jsonTree(c.raw,stageId+':tts'+i)}</div>`;
    return h;
  }
  if(c.system_prompt) h+=promptBlock('System prompt',c.system_prompt,c.system_prompt_gcs);
  if(c.user_prompt) h+=promptBlock('User prompt',c.user_prompt,c.user_prompt_gcs);
  h+=promptBlock('Response'+(c.response_preview&&c.response_gcs?' (preview)':''),
      c.response_preview||'(no preview captured)',c.response_gcs);
  return h;
}
function promptBlock(label,text,gcs){
  return `<div class="pb"><div class="pl"><span>${esc(label)} <span class="mono" style="color:var(--dim)">${text?text.length.toLocaleString()+' ch':''}</span></span>`
    +(gcs?`<span class="gcs mono" title="full text in GCS">${esc(gcs.replace('gs://',''))}</span>`:'')
    +`</div><pre class="pre">${esc(text)}</pre></div>`;
}

/* ---- generic collapsible JSON tree; nested objects drill into their own view ---- */
const _blobs={}; let _bid=0;
function jsonTree(v,path){
  return `<div class="jt">${jtNode(v,null,path,0)}</div>`;
}
function jtNode(v,key,path,depth){
  const kh = key==null?'':`<span class="k">${esc(key)}</span>: `;
  if(v===null) return `<div class="row">${kh}<span class="nul">null</span></div>`;
  if(typeof v==='number') return `<div class="row">${kh}<span class="n">${v}</span></div>`;
  if(typeof v==='boolean') return `<div class="row">${kh}<span class="b">${v}</span></div>`;
  if(typeof v==='string'){
    const big=v.length>140;
    const disp=big?esc(v.slice(0,140))+'…':esc(v);
    return `<div class="row">${kh}<span class="s">"${disp}"</span>${big?` <span class="cnt">(${v.length})</span>`:''}</div>`;
  }
  const isArr=Array.isArray(v), keys=isArr?v.map((_,i)=>i):Object.keys(v);
  const n=keys.length;
  if(n===0) return `<div class="row">${kh}<span class="cnt">${isArr?'[]':'{}'}</span></div>`;
  const id='jt'+(_bid++); _blobs[id]={v,path};
  const open = depth<1 && n<=12;
  const head=`<span class="tw" onclick="tw('${id}')"><span id="tg_${id}">${open?'▾':'▸'}</span> ${kh}<span class="cnt">${isArr?'['+n+']':'{'+n+'}'}</span></span>`;
  // when large, offer a "focus" drill into its own drawer level
  const focus = n>12 ? ` <span class="drill" onclick="drill('${id}')">focus ↗</span>` : '';
  let kids='';
  keys.forEach(k=>{ kids+=jtNode(isArr?v[k]:v[k],isArr?('['+k+']'):k,path+'/'+k,depth+1); });
  return `<div class="row">${head}${focus}<div id="ch_${id}" style="display:${open?'block':'none'}">${kids}</div></div>`;
}
function tw(id){const c=document.getElementById('ch_'+id),g=document.getElementById('tg_'+id);
  const o=c.style.display==='none';c.style.display=o?'block':'none';g.textContent=o?'▾':'▸';}
function drill(id){const b=_blobs[id];if(!b)return;
  pushView({title:b.path.split('/').pop()||'data', render:()=>`<div class="sec">${jsonTree(b.v,b.path)}</div>`});}

/* ---- job-level data view ---- */
document.getElementById('btnData').addEventListener('click',()=>{
  stack=[{title:'Job data', render:jobDataView}]; renderDrawer();
});
function jobDataView(){
  let h=`<div class="dh"><h2>Job data</h2></div>`;
  // Creation & routing — the intake spine the pipeline grounds on (manifest §A/§B):
  // idea -> outline -> episode profile -> creative intent, + the cost/routing knobs.
  const RD=T.raw_doc||{};
  const spine=[
    ['idea (smart_create_context)', RD.smart_create_context],
    ['plan outline (smart_create_outline)', RD.smart_create_outline],
    ['episode_profile', RD.episode_profile],
    ['creative_intent', RD.creative_intent],
    ['angle_brief', RD.angle_brief],
    ['scenario_tailoring', RD.scenario_tailoring],
    ['content_spec', RD.content_spec],
    ['personalization', RD.personalization],
    ['credit_breakdown', RD.credit_breakdown],
  ].filter(([,v])=>v!=null && !(typeof v==='object'&&!Object.keys(v||{}).length));
  if(spine.length){
    h+=`<div class="sec"><div class="st">Creation &amp; routing <span class="mono" style="color:var(--dim)">what the pipeline grounds on</span></div>`;
    spine.forEach(([nm,v])=>{ h+=`<div style="margin-bottom:10px"><div class="pl" style="margin-bottom:4px;color:var(--mut);font-size:10.5px;text-transform:uppercase;letter-spacing:.05em">${esc(nm)}</div>${jsonTree(v,'spine/'+nm)}</div>`; });
    h+=`</div>`;
  }
  // optimization ranking
  h+=`<div class="sec"><div class="st">Costliest stages <span class="mono" style="color:var(--dim)">$${(+T.totals.cost_usd).toFixed(2)}</span></div>${rank('cost_usd','var(--visuals)',fmtUsd)}</div>`;
  h+=`<div class="sec"><div class="st">Slowest stages <span class="mono" style="color:var(--dim)">${T.totals.duration_s||'?'}s</span></div>${rank('duration_ms','var(--main)',fmtMs)}</div>`;
  // spend by provider · model — the cost/token hogs across the whole job
  h+=modelSpend();
  if((T.milestones||[]).length)
    h+=`<div class="sec"><div class="st">Milestones</div>${jsonTree(T.milestones,'milestones')}</div>`;
  const blocks=[['costs',T.costs],['credit_breakdown',T.credit_breakdown],['budget_by_stage',T.budget_by_stage],
    ['outputs',T.outputs],['segment_beat_map',T.segment_beat_map],['master_segment_timeline',T.master_segment_timeline],
    ['raw_doc (everything else)',T.raw_doc]];
  blocks.forEach(([nm,val])=>{ if(val!=null && !(typeof val==='object'&&!Object.keys(val).length))
    h+=`<div class="sec"><div class="st">${esc(nm)}</div>${jsonTree(val,nm)}</div>`; });
  return h;
}
function rank(metric,color,fmt){
  const rows=T.stages.filter(s=>(s[metric]||0)>0).sort((a,b)=>(b[metric]||0)-(a[metric]||0)).slice(0,8);
  if(!rows.length) return `<div class="empty">no data</div>`;
  const tot=T.stages.reduce((a,s)=>a+(s[metric]||0),0)||1, mx=rows[0][metric]||1;
  return rows.map(s=>`<div class="orow" onclick="openStage('${esc(s.id)}')">
     <span class="on">${esc(s.name)}</span>
     <span class="ov mono">${fmt(s[metric])} <span class="sh">${Math.round(100*s[metric]/tot)}%</span></span>
     <div class="obar mb" style="--oc:${color}"><i style="width:${Math.max(4,100*s[metric]/mx)}%"></i></div></div>`).join('');
}
// aggregate every call across the job by provider · model — the token / $ hogs
function modelSpend(){
  const g={};
  T.stages.forEach(s=>(s.calls||[]).forEach(c=>{
    const k=(c.provider||'?')+' · '+(c.model||'?');
    const e=g[k]||(g[k]={n:0,inTok:0,outTok:0,cost:0,fail:0});
    e.n++; e.inTok+=c.in_tokens||0; e.outTok+=c.out_tokens||0; e.cost+=c.cost||0; if(!c.success)e.fail++;
  }));
  const rows=Object.entries(g).sort((a,b)=>(b[1].cost-a[1].cost)||(b[1].outTok-a[1].outTok));
  if(!rows.length) return '';
  const totCost=rows.reduce((a,[,e])=>a+e.cost,0), totTok=rows.reduce((a,[,e])=>a+e.outTok,0);
  const mx=Math.max(...rows.map(([,e])=>e.cost||e.outTok||0))||1;
  const body=rows.map(([k,e])=>`<div class="orow" style="cursor:default">
     <span class="on">${esc(k)}${e.fail?` <span class="tag" style="--c:var(--crit)">${e.fail} fail</span>`:''}</span>
     <span class="ov mono"><b>×${e.n}</b> <span class="sh">${fmtTok(e.outTok)} out${e.cost?' · '+fmtUsd(e.cost):''}</span></span>
     <div class="obar mb" style="--oc:var(--llm)"><i style="width:${Math.max(4,100*(e.cost||e.outTok||0)/mx)}%"></i></div></div>`).join('');
  return `<div class="sec"><div class="st">Spend by provider · model <span class="mono" style="color:var(--dim)">${rows.length} · ${fmtTok(totTok)} out tok${totCost?' · '+fmtUsd(totCost):''}</span></div>${body}</div>`;
}

/* ---- search ---- */
document.getElementById('search').addEventListener('input',e=>{
  const q=e.target.value.trim().toLowerCase();
  document.querySelectorAll('.node').forEach(n=>{
    const s=stageById[n.dataset.id];
    const hit=!q || s.name.toLowerCase().includes(q) || s.phase.includes(q)
      || (s.calls||[]).some(c=>(c.provider+' '+c.model+' '+(c.purpose||'')).toLowerCase().includes(q));
    n.classList.toggle('dim', !!q && !hit);
  });
});

/* ---- keyboard ---- */
window.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    if(document.activeElement===document.getElementById('search')){document.getElementById('search').blur();return;}
    if(stack.length>1){stack.pop();renderDrawer();}
    else if(stack.length===1){closeDrawer();}
  }
  if(e.key==='f'&&!/input/i.test(document.activeElement.tagName)){fit();}
});
function toggleTheme(){const r=document.documentElement;
  r.setAttribute('data-theme',r.getAttribute('data-theme')==='light'?'dark':'light');}

/* ---- wall-clock timeline (critical path & concurrency) ---- */
function renderTimeline(){
  const tl=T.stages.filter(s=>s.start && s.duration_ms).sort((a,b)=>a.start-b.start);
  const body=document.getElementById('tlbody'), meta=document.getElementById('tlmeta');
  if(!tl.length){body.innerHTML='<div class="empty" style="padding:8px">No per-stage wall-clock timestamps captured for this job.</div>';meta.textContent='';return;}
  const t0=Math.min(...tl.map(s=>s.start)), t1=Math.max(...tl.map(s=>s.end||s.start));
  const span=Math.max(1,t1-t0);
  const critMs=Math.max(...tl.map(s=>s.duration_ms||0));
  meta.textContent=`${tl.length} timed stages · span ${fmtMs(span*1000)} · slowest ${fmtMs(critMs)}`;
  // axis ticks (0, 25, 50, 75, 100%)
  let axis='<div class="taxis">'+[0,.25,.5,.75,1].map(f=>`<span style="left:${f*100}%">${fmtMs(f*span*1000)}</span>`).join('')+'</div>';
  const rows=tl.map(s=>{
    const L=100*(s.start-t0)/span, W=Math.max(0.8,100*(s.duration_ms/1000)/span);
    const crit=(s.duration_ms===critMs)?' crit':'';
    return `<div class="trow" onclick="openStage('${esc(s.id)}');toggleTimeline()">
      <span class="tnm">${esc(s.name)}${s.lane!=='main'?` <span style="color:${PC[s.lane]}">·${s.lane[0]}</span>`:''}</span>
      <div class="ttrack"><i class="${crit}" style="left:${L}%;width:${W}%;background:${PC[s.lane]||'var(--main)'};opacity:.9"></i></div>
      <span class="tdur mono">${fmtMs(s.duration_ms)}</span></div>`;
  }).join('');
  body.innerHTML=`<div class="trow" style="cursor:default"><span></span>${axis}<span></span></div>`+rows;
}
function toggleTimeline(){
  const el=document.getElementById('timeline'), on=el.classList.toggle('on');
  if(on) renderTimeline();
}
document.getElementById('btnTimeline').addEventListener('click',toggleTimeline);

fit();
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("job_id", nargs="?", help="podcast_jobs document id")
    ap.add_argument("--from-file", help="read a cached job doc JSON instead of Firestore ($0, offline)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if not a.job_id and not a.from_file:
        ap.error("provide a job_id or --from-file")
    trace = build_trace(job_id=a.job_id, from_file=a.from_file)
    html = render_html(trace)
    out = a.out or f"/tmp/trace_{trace['job_id'][:8]}.html"
    with open(out, "w") as f:
        f.write(html)
    t = trace["totals"]
    print(f"stages={t['stages']} llm_calls={t['llm_calls']} tts={t['tts_calls']} "
          f"cost=${t['cost_usd']} dur={t['duration_s']}s")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
