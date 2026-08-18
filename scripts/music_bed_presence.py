#!/usr/bin/env python3
"""Measure the MUSIC/NON-SPEECH LAYER in delivered masters — $0, offline, no LLM, no new job.

Answers "is there actually a music bed in the mix, and how much?" by differencing the delivered
master against the job's own ``audio.speech_only_url`` in speech-QUIET frames.

WHY THIS EXISTS AS A SCRIPT (2026-08-18). T6 was filed as "blocked: needs FLEET VOLUME — the
sub-6dB tail is 6.7% of jobs, manufacturing them costs real money". Both halves were wrong: 317
post-2026-07 job pairs already carry BOTH urls, so the sample existed, and the ladder's own rule #1
is REUSE BEFORE GENERATE. Run this instead of paying for jobs.

⚠️ THE TRAP THIS SCRIPT EXISTS TO PREVENT — it inverted the answer:
the master is loudness-NORMALISED (~-16 LUFS) while ``speech_only`` is NOT. Differencing them raw
understates the bed by ~3.5 dB and produced a "64% of jobs are sub-6dB" DEFICIENCY finding.
Median-centring each signal first flipped that to 0%. **Always remove the offset before
differencing.** Coherence check: median dB must track %>6dB monotonically; if it does not, the
offset is still in there.

⚠️ Also: restrict to jobs >=20s. Short jobs yield <150 frames and noisy medians.
⚠️ Pre-2026-07 jobs are unusable — their masters are absent (see the historical-master backlog item).

USAGE:  python3 scripts/music_bed_presence.py [N]     # N = pairs to sample (default 16)
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from datetime import datetime, timezone

CUTOVER = datetime(2026, 7, 1, tzinfo=timezone.utc)   # masters exist only after this


def _pairs(limit: int, scan: int = 2000):
    from google.cloud import firestore
    db = firestore.Client(project=os.environ.get("GCP_PROJECT_ID", "kitesforu-dev"))
    out = []
    for d in db.collection("podcast_jobs").limit(scan).stream():
        j = d.to_dict() or {}
        m = (j.get("outputs") or {}).get("audio_url")
        s = (j.get("audio") or {}).get("speech_only_url")
        ts = j.get("created_at")
        if not (m and s) or not isinstance(ts, datetime):
            continue
        ts = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        if ts < CUTOVER:
            continue
        out.append((d.id, str(m), str(s)))
        if len(out) >= limit:
            break
    return out


def _pcm(path: str, sr: int = 8000):
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "quiet", "-i", path, "-ac", "1",
                        "-ar", str(sr), "-f", "s16le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype="int16").astype("float32") / 32768.0


def _frames_db(x, sr: int = 8000, win: float = 0.4):
    import numpy as np
    n = int(sr * win); m = len(x) // n
    if m == 0:
        return np.array([])
    f = x[: m * n].reshape(m, n)
    return 20 * np.log10(np.sqrt((f ** 2).mean(axis=1)) + 1e-9)


def main() -> int:
    import numpy as np
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    pairs = _pairs(want)
    print(f"selected {len(pairs)} post-{CUTOVER:%Y-%m} pairs")
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for jid, m, s in pairs:
            a, b = f"{tmp}/{jid[:8]}_m.mp3", f"{tmp}/{jid[:8]}_s.mp3"
            ok = True
            for p, u in ((a, m), (b, s)):
                subprocess.run(["curl", "-sSL", "-m", "120", "-o", p, u], capture_output=True)
                if not os.path.exists(p) or os.path.getsize(p) < 5000:
                    ok = False
                    break
            if not ok:
                continue
            M, S = _pcm(a), _pcm(b)
            n = min(len(M), len(S))
            if n < 8000 * 20:                      # >=20s only
                continue
            dm, ds = _frames_db(M[:n]), _frames_db(S[:n])
            k = min(len(dm), len(ds))
            dm, ds = dm[:k], ds[:k]
            dm = dm - np.median(dm)                # REMOVE THE NORMALISATION OFFSET
            ds = ds - np.median(ds)
            quiet = ds < np.percentile(ds, 25)
            if quiet.sum() < 10:
                continue
            diff = dm[quiet] - ds[quiet]
            rows.append((jid[:8], k, float(np.median(diff)), float((diff > 6.0).mean() * 100)))
    rows.sort(key=lambda r: r[3])
    print(f"\n{'job':10s} {'frames':>7s} {'median dB':>10s} {'% frames >6dB':>14s}")
    for jid, k, med, pct in rows:
        print(f"  {jid:8s} {k:7d} {med:+10.1f} {pct:13.1f}%")
    if rows:
        meds = [r[2] for r in rows]; pcts = [r[3] for r in rows]
        mono = all(x <= y for x, y in zip(meds, meds[1:]))
        print(f"\n  n={len(rows)}  |  reference job (2026-08-09 audit) read 22.2% of frames >6dB")
        print(f"  coherence check (median dB rises with %>6dB): {'PASS' if mono else 'FAIL — offset still present'}")
        print(f"  jobs below a 10%-of-frames bar: {sum(1 for p in pcts if p < 10)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
