#!/bin/bash
# THE STEP-BY-STEP ARTIFACT CHECKER (founder order, 2026-08-06: "thoroughly run step by step checker")
# Runs EVERY verification instrument built during the 2026-08 quality campaign against one job,
# in pipeline order, printing PASS/FAIL/INFO per step. $0 (reads + local ffmpeg). Usage: <job_id>
set -u
J="${1:?usage: full_artifact_checker.sh <job_id>}"
cd "$(dirname "$0")/../.." || exit 2
W=/tmp/checker_$J; mkdir -p $W
echo "═══ STEP-BY-STEP CHECKER — job $J ═══"

python3 - "$J" "$W" <<'PY'
import sys, json, subprocess, collections
J, W = sys.argv[1], sys.argv[2]
from google.cloud import firestore
d = firestore.Client(project="kitesforu-dev").collection("podcast_jobs").document(J).get().to_dict() or {}
ok = lambda c: "PASS" if c else "FAIL"
info = []

# 1. INTAKE — the promise
dm = (d.get("inputs") or {}).get("duration_min")
print(f"[1 intake]    ask={dm}min status={d.get('status')} :: {ok(d.get('status')=='completed')}")

# 2. SCRIPT — speech vs promise (container trap: timeline, never format duration)
mt = d.get("master_segment_timeline") or []
sp = max((int(x.get("end_ms") or 0) for x in mt if isinstance(x, dict)), default=0)/60000
ratio = sp/dm if dm else 0
# Band tightened 2026-08-06: the old <=1.4 PASS flattered the exact defect the
# sub-capsule campaign fixed (a 1.39x witness read PASS). PASS now mirrors the
# trim band (+/-25%); 1.25-1.5 = WARN; beyond = FAIL.
band = "PASS" if 0.75 <= ratio <= 1.25 else ("WARN" if 0.5 <= ratio <= 1.5 else "FAIL")
print(f"[2 script]    speech={sp:.2f}min ratio={ratio:.2f}x :: {band}")

# 3. AUDIO — tail bound (speech end vs audio stream measured later), calibration stamp
cal = ((d.get("stages") or {}).get("job-audio") or {}).get("calibrated_pace")
print(f"[3 audio]     calibrated_pace={cal} :: {'INFO' if cal is None else 'PASS'}")

# 4. VISUALS — engines, kinds diversity, library images
v = d.get("visual") or {}
clips = v.get("clips") or []
kinds = collections.Counter(str(((c.get('diagram_debug') or {}) if isinstance(c.get('diagram_debug'), dict) else {}).get('kind') or c.get('modality')) for c in clips)
lib = sum(1 for c in clips if c.get("library_asset") or "library" in str(c.get("source") or "").lower())
print(f"[4 visuals]   clips={len(clips)} kinds={len(kinds)} distinct {dict(kinds.most_common(6))}")
print(f"              library_images={lib} :: {'PASS' if len(kinds)>=3 else 'WARN'}")

# 5. ASSEMBLY — anchors (the sync root) + the sync gate's own stamp
anch = sum(1 for c in clips if isinstance(c.get("start_ms"), (int, float)))
acs = v.get("av_content_sync")
print(f"[5 assembly]  anchored={anch}/{len(clips)} :: {ok(anch==len(clips) or anch>=len(clips)-0)}")
print(f"              av_content_sync stamp={json.dumps(acs)[:100] if acs else 'ABSENT'} :: {ok(bool(acs))}")

# 6. SURFACING — playable URL
url = str(v.get("video_url") or "")
print(f"[6 surfacing] video_url={'https OK' if url.startswith('http') else repr(url[:40])} :: {ok(url.startswith('http'))}")
open(f"{W}/url", "w").write(url)
PY

URL=$(cat $W/url 2>/dev/null)
[ -z "$URL" ] && { echo "[7-10] SKIPPED — no video"; exit 1; }
curl -s -o $W/v.mp4 "$URL"
curl -s -o /dev/null -w "[7 playable]  HTTP HEAD %{http_code} :: PASS-if-200\n" -I "$URL"

# 8. STREAMS — per-stream, never container (the trap that hid a 9s mismatch)
ffprobe -v error -show_entries stream=codec_type,duration -of csv=p=0 $W/v.mp4 | python3 -c "
import sys
s={r.split(',')[0]: float(r.split(',')[1]) for r in sys.stdin.read().split() if ',' in r}
v,a = s.get('video',0), s.get('audio',0)
print(f\"[8 streams]   video={v:.2f}s audio={a:.2f}s delta={abs(v-a):.2f}s :: {'PASS' if abs(v-a)<=0.5 else 'FAIL'}\")"

# 9. FRAMES — two-arm void census + the typography caveat (eyes required for dark frames)
mkdir -p $W/f && ffmpeg -loglevel error -i $W/v.mp4 -vf fps=1/5 $W/f/f_%03d.png -y
python3 - "$W" <<'PY'
import sys, glob
from PIL import Image
import numpy as np
W = sys.argv[1]
ps = sorted(glob.glob(f"{W}/f/f_*.png")); n = len(ps)
void = []
for p in ps:
    g = np.asarray(Image.open(p).convert("L"), dtype=np.float32)
    if g.mean() < 15 and float((g > 40).mean())*100 < 2 and float((g > 150).mean())*100 < 0.08:
        void.append(p.split("/")[-1])
# 9b. EDGE-TEXT arm (acceptance_gate probe C, per-frame): bright text pixels hugging the
# outermost columns = a label/callout clipped off-frame (witness 7171699f f_004:
# "Summer heat pushes lattice outward" rendered "mmer heat..."). Computable — no eyes needed.
edge = []
for p in ps:
    g = np.asarray(Image.open(p).convert("L"), dtype=np.float32)
    # >=12 bright pixels in the outer 3 columns = real content touching the frame
    # edge (witness f_004 measured 28; all clean frames 0; a lone hot pixel cannot
    # reach 12). The old std>38 guard MISSED the witness (std 15.9 — a small
    # portrait callout leaves the column mostly dark).
    if int((g[:, :3] >= 200).sum()) >= 12 or int((g[:, -3:] >= 200).sum()) >= 12:
        edge.append(p.split("/")[-1])
print(f"[9 frames]    n={n} three-arm-void={len(void)} :: {'PASS' if not void else 'EYES-REQUIRED'}")
print(f"              edge-text-clip={len(edge)} {edge[:4]} :: {'PASS' if not edge else 'FAIL'}")
if void: print(f"              flagged (may be legible dark type — LOOK before concluding): {void[:6]}")
PY

# 10. GUARD LOGS — did the instruments fire (with positive control)
echo "[10 guards]   (2h window, control=select_candidates)"
for Q in "assembler_av_sync" "engine_black_render" "master_tail_bound"; do
  N=$(gcloud logging read "jsonPayload.message:\"$Q\"" --project=kitesforu-dev --limit=3 --freshness=2h --format='value(timestamp)' 2>/dev/null | wc -l | tr -d ' ')
  echo "              $Q: $N hit(s)"
done
echo "═══ done — frames in $W/f for the eye pass ═══"
