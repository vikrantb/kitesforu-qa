#!/usr/bin/env bash
# ONE command: did every shipped innovation actually FIRE on a real job?
#
# WHY THIS EXISTS (founder, 2026-07-29): "we can't create a short or video for every single test"
# and "we should have a way to collectively check logs and see if each innovation we made, made
# into and worked correctly so we avoid reworks."
#
# On 2026-07-29 I shipped 14 visual innovations one at a time, each with its own $0.025 job, and
# TWO of them were silently inert for multiple rounds (#1959 grounding recovery, #1956 grounding
# thread). A per-fix job did not catch that; an unconditional log receipt did. This turns that
# into a batch check: ship N innovations, run ONE job, read which of the N reported.
#
# ── TWO CORRECTIONS TO THIS TOOL (2026-07-30) ────────────────────────────────────────────────
# 1. IT DID NOT CORRELATE. The old version took <job_id>, printed it in the header, and then
#    queried ONLY by service + freshness — so a receipt emitted by a DIFFERENT job in the same
#    window counted as a HIT for yours. That is the exact error class that produced two false
#    headlines on 2026-07-29/30 (a real log line paired with data from another run). A
#    verification tool that can attribute another job's success to your job is worse than no
#    tool. It now queries by `jsonPayload.job_id` FIRST and reports anything it cannot attribute
#    in a SEPARATE, explicitly-untrusted section.
# 2. IT ONLY READ LOGS. Some innovations are only observable on the ARTIFACT (e.g. does
#    `modality_reasons` actually survive to the persisted clip?). Those can never produce a log
#    line, so they were invisible here. There is now a DOC section that reads the job document.
#
# The doc section also prints the coverage/dullness metrics the quality gate COMPUTES BUT DOES
# NOT GATE (beat_delivery_ratio, longest_same_image_s, top-beat time share). On job b153fe70 the
# gate said met=TRUE while 5 of 16 planned beats had a visual and one beat held 65% of the
# runtime on two pictures. Printing them here is deliberate: never ask a human to perceive what
# the pipeline already computed.
#
# Usage:  verify_innovations.sh <job_id> [freshness]
# Cost:   $0 — Cloud Logging reads + one Firestore doc read. Never creates a job.
set -uo pipefail
JOB="${1:?usage: verify_innovations.sh <job_id> [freshness, default 30m]}"
FRESH="${2:-30m}"
PROJ="${GCP_PROJECT:-kitesforu-dev}"
SVC="${SVC:-kitesforu-worker-visuals}"
WORKERS_SRC="${WORKERS_SRC:-/Users/vikrantbhosale/gitprojects/kitesforu/kitesforu-workers/src}"

# marker|human name|what a MISS means
INNOVATIONS=(
"motion grounding stamped|grounded numbers reached the author|#1956/#1961 inert — author has no figures"
"grounding recovered from the job doc|thin-job_state recovery fired|only fires when job_state lacks the corpus (may be legitimately absent)"
"grounding recovery skipped|recovery could not run|BAD: no job_id available at the stamp site"
"adjacency budget applied|anti-monotony cap applied at author time|#1951 inert — expect repeated same-engine runs"
"info_hook_carded|decorative hook replaced by a card|#1938 inert — hook may be a decorative scene"
"motion_decline|a motion beat declined (WITH reason)|no declines = either all succeeded or motion never ran"
"HERO element|prominence guard caught a zero hero|only fires on a bad frame — a MISS here is good"
"motion_intent_demoted|intent demotion fired|#1965 replaced this with a decline; a HIT means stale image"
"timeline JSON unparseable|author output failed to parse|#1964 raised the ceiling; HITs mean it is still truncating"
"data-less compare beat|data-less beat declined to static|#1965 working"
"select_candidates|per-filter drop counter (data router intake)|#1968 inert — cannot tell which filter starves the router"
"data_shape pre-V2 LOCK|data-router receipt WITH shots_in correlation key|#1970 inert — beats= is unpairable again"
)

echo "════ INNOVATION RECEIPTS — job ${JOB} · ${SVC} · last ${FRESH} ════"
echo
echo "── A. LOG RECEIPTS ATTRIBUTED TO THIS JOB (free-text job-id match, ALL services) ──"
# WHY FREE-TEXT AND NOT jsonPayload.job_id (corrected 2026-07-30, minutes after shipping the
# structured version): this stage emits logs in TWO shapes. Some records are genuine structured
# jsonPayload (the `data_shape_pre_v2` receipt), but StructuredLogger-based loggers emit the whole
# record as a JSON *string*, so `jsonPayload.job_id` DOES NOT EXIST on those lines. Measured on job
# c8b86b3f: `jsonPayload.job_id=` matched 26 lines on ONE service, while a free-text match on the
# same id found 500 across THREE services including 236 on kitesforu-worker-visuals — the exact
# lines this tool exists to read. The structured filter was a silent false-negative generator:
# it reported "0 attributed" for a job that had logged 236 visuals lines.
#
# AND NO SERVICE FILTER: the visuals stage does not only run on kitesforu-worker-visuals. On the
# same job, visuals-authoring logs appeared on worker-visuals AND worker-audio AND worker-tools.
# Hardcoding SVC made the tool blind to whichever service actually did the work.
OWN=$(gcloud logging read "\"${JOB}\"" \
  --project "${PROJ}" --limit 2000 --format='value(jsonPayload.message,textPayload)' \
  --freshness="${FRESH}" 2>/dev/null)

echo -n "  services that logged this job: "
gcloud logging read "\"${JOB}\"" --project "${PROJ}" --limit 2000 \
  --format='value(resource.labels.service_name)' --freshness="${FRESH}" 2>/dev/null \
  | sort | uniq -c | awk '{printf "%s(x%s) ", $2, $1}'
echo

# Everything in the window on the visuals-capable services, regardless of job — used ONLY to
# report what we could NOT attribute, never counted as a pass.
ALL=$(gcloud logging read \
  "resource.labels.service_name=\"${SVC}\" OR resource.labels.service_name=\"kitesforu-worker-audio\"" \
  --project "${PROJ}" --limit 2000 --format='value(jsonPayload.message,textPayload)' \
  --freshness="${FRESH}" 2>/dev/null)

if [ -z "${ALL}" ]; then
  echo "NO LOGS in the window — widen freshness or check the service name."; exit 2
fi
if [ -z "${OWN}" ]; then
  echo "  ⚠️  ZERO log lines mention ${JOB} in the last ${FRESH}."
  echo "      Either the job has not reached the visuals stage yet, or the window is too narrow."
  echo "      Nothing in section A can be attributed to this job."
fi

HITS=0; MISSES=0; UNATTRIB=0
for row in "${INNOVATIONS[@]}"; do
  IFS='|' read -r marker name meaning <<< "$row"
  n=$(printf '%s\n' "${OWN}" | grep -cF "${marker}")
  if [ "${n}" -gt 0 ]; then
    printf '  ✅ %-46s  x%-3s\n' "${name}" "${n}"
    HITS=$((HITS+1))
  else
    m=$(printf '%s\n' "${ALL}"  | grep -cF "${marker}")
    if [ "${m}" -gt 0 ]; then
      printf '  ❓ %-46s  x%-3s  IN WINDOW BUT NOT THIS JOB — do not credit it\n' "${name}" "${m}"
      UNATTRIB=$((UNATTRIB+1))
    else
      printf '  ⬜ %-46s  —    %s\n' "${name}" "${meaning}"
      MISSES=$((MISSES+1))
    fi
  fi
done
echo "────────────────────────────────────────────────────────────────"
echo "  ${HITS} attributed · ${UNATTRIB} in-window-but-unattributed · ${MISSES} silent"

echo
echo "── B. ARTIFACT RECEIPTS (the job document — things no log line can show) ──"
GCP_PROJECT_ID="${PROJ}" PYTHONPATH="${WORKERS_SRC}" python3 - "${JOB}" <<'PY' 2>/dev/null || echo "  (doc read unavailable — check GCP creds / WORKERS_SRC)"
import sys, collections
from workers.common.database import get_firestore_client

job = sys.argv[1]
d = get_firestore_client().collection("podcast_jobs").document(job).get().to_dict() or {}
if not d:
    print(f"  job {job} not found"); raise SystemExit(0)
v = d.get("visual") or {}
clips = [c for c in (v.get("clips") or []) if isinstance(c, dict)]
print(f"  job status={d.get('status')} · visual.status={v.get('status')} · video={v.get('video_status')}")
if not clips:
    print("  NO CLIPS YET — rerun once the visuals stage completes."); raise SystemExit(0)

specs = len(v.get("shot_specs") or [])
beats = {c.get("beat_index") for c in clips}
assets = {str(c.get("asset_uri") or "") for c in clips if str(c.get("asset_uri") or "").strip()}

# #1971 — modality_reasons must SURVIVE to the clip. Declared in schemas 2.61.0 and, before
# #1971, never populated by any writer: present-but-empty, which reads as "no decision made".
with_reasons = [c for c in clips if isinstance(c.get("modality_reasons"), list) and c["modality_reasons"]]
verdict = "✅" if with_reasons else "⬜"
print(f"  {verdict} #1971 modality_reasons populated on {len(with_reasons)}/{len(clips)} clips")
for c in with_reasons[:4]:
    print(f"       beat {c.get('beat_index')}: {c['modality_reasons']}")
if not with_reasons:
    print("       MISS ⇒ the decision trail is still not reaching the clip; every modality")
    print("       diagnosis stays code-reading instead of artifact-reading.")

# #1971 (second half) — a committed data figure must not be replaced by a generic relimage.
kinds = collections.Counter(str((c.get("diagram_debug") or {}).get("kind") or "-") for c in clips)
print(f"  engines (diagram_debug.kind): {dict(kinds)}")
DATA_KINDS = {"vegalite", "timeline_chronology", "maps", "maps_sequence", "labeled_anatomy"}
data_n = sum(n for k, n in kinds.items() if k in DATA_KINDS)
print(f"       data-engine figures surviving to a clip: {data_n}")

print(f"  render_mode: {dict(collections.Counter(str(c.get('render_mode')) for c in clips))}")
print(f"  status:      {dict(collections.Counter(str(c.get('status')) for c in clips))}")
empty = [c.get("beat_index") for c in clips if not str(c.get("asset_uri") or "").strip()]
if empty:
    print(f"  ⚠️  {len(empty)} clip(s) with EMPTY asset_uri (beats {empty}) — rendered nothing")

# ── The metrics the gate COMPUTES BUT DOES NOT GATE. This is the section that matters.
dist = v.get("distinctness") or {}
total_ms = max((int(c.get("start_ms") or 0) + int(c.get("duration_ms") or 0)) for c in clips)
per = collections.Counter()
for c in clips:
    per[c.get("beat_index")] += int(c.get("duration_ms") or 0)
top_beat, top_ms = (per.most_common(1) or [(None, 0)])[0]
top_share = (top_ms / total_ms) if total_ms else 0

print()
print(f"  COVERAGE  planned shots={specs}  clips={len(clips)}  distinct beats={len(beats)}  distinct assets={len(assets)}")
print(f"            beat_delivery_ratio = {dist.get('beat_delivery_ratio')}   (beats_delivered/{dist.get('beats_planned')})   [NOT GATED]")
print(f"            longest_same_image_s= {dist.get('longest_same_image_s')}                              [NOT GATED]")
print(f"            top1_share={dist.get('top1_share')}  top3_share={dist.get('top3_share')}             [NOT GATED]")
print(f"            busiest beat {top_beat} holds {top_ms/1000:.1f}s of {total_ms/1000:.1f}s = {100*top_share:.0f}%  [NOT GATED]")
print(f"  GATED     required={dist.get('required')} distinct assets · delivered={dist.get('delivered_distinct_assets')} ⇒ met={dist.get('met')}")
print()
flags = []
if isinstance(dist.get("beat_delivery_ratio"), (int, float)) and dist["beat_delivery_ratio"] < 0.6:
    flags.append(f"only {100*dist['beat_delivery_ratio']:.0f}% of planned beats got a visual")
if isinstance(dist.get("longest_same_image_s"), (int, float)) and dist["longest_same_image_s"] > 5:
    flags.append(f"one image holds for {dist['longest_same_image_s']}s")
if top_share > 0.4:
    flags.append(f"one beat owns {100*top_share:.0f}% of the runtime")
if flags:
    print("  🔴 DULLNESS SIGNALS THE GATE IGNORED:")
    for f in flags:
        print(f"       · {f}")
    print("       The gate can say met=TRUE with every one of these true. That is the defect,")
    print("       not the job: `SECONDS_PER_ASSET = 12.0` asks for one new picture every 12s")
    print("       while the Vox reference we are judged against changes every 1.9s.")
else:
    print("  ✅ no ungated dullness signal tripped")
PY

echo
echo "A SILENT innovation is not proof of failure — but it IS proof you cannot claim it works."
echo "An UNATTRIBUTED receipt is not yours. Add a receipt log (with job_id) to anything that matters."
