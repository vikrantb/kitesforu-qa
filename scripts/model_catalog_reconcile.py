#!/usr/bin/env python3
"""Does the model catalog match what the providers ACTUALLY serve? $0, read-only.

    python3 scripts/model_catalog_reconcile.py [--catalog PATH] [--json]

Answers three questions the catalog cannot answer about itself:
  1. PHANTOM  — a row we carry that the provider no longer lists.
  2. MISSING  — a generation model the provider lists that we do not carry.
  3. STALE    — a row whose price has not been re-verified within --max-age-days.

WHY THIS EXISTS. On 2026-08-28 the catalog was six weeks past its last price verification and
nobody could say so, because "when was this price checked" lived in free text inside the `notes`
column. The same audit found `claude-opus-4-8` live at Anthropic and absent from the catalog.
Both are invisible without a reconciler, and both were found by hand — once.

⚠️ THE TRAP THIS TOOL EXISTS TO PREVENT — read before trusting any zero it prints.
Different model families live behind DIFFERENT APIs. Querying the wrong one returns an empty list
that is indistinguishable from "the model is gone":

    gemini-* (LLM/image)  -> generativelanguage.googleapis.com
    veo-* / imagen-*      -> Vertex AI (aiplatform)            NOT generativelanguage
    gcp-neural2           -> texttospeech.googleapis.com       NOT generativelanguage
                             ...and it needs `x-goog-user-project` or it 403s and returns 0 voices

On 2026-08-28 a single-endpoint probe reported SEVEN Google rows as retired, three of them
`veo-3.1-*` with enabled=true. `eol_date` is an ACTIVE SWITCH — `selector._is_eol` excludes any
row on/before that date — so acting on it would have cut live video capacity. The 2026-08-18
incident was the same shape and cost 60% of image renders.

Therefore: EVERY provider probe here carries a POSITIVE CONTROL, and the tool REFUSES to report
a provider's phantoms if that control fails. A zero you cannot trust is worse than no answer.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import date, datetime

TIMEOUT = 30


def _secret(name: str) -> str | None:
    try:
        out = subprocess.run(
            ["gcloud", "secrets", "versions", "access", "latest",
             f"--secret={name}", "--project=kitesforu-dev"],
            capture_output=True, text=True, timeout=60)
        return out.stdout.strip() or None
    except Exception:
        return None


def _get(url: str, headers: dict) -> dict | list | None:
    """GET via curl, deliberately — NOT urllib.

    urllib on macOS raises CERTIFICATE_VERIFY_FAILED without a configured cert bundle, and this
    function's fail-open `except` turned that into an empty result: every provider probe returned
    zero and the whole reconciler abstained. curl uses the system trust store and works. The
    control below is what surfaced it rather than letting 66 rows read as retired.
    """
    cmd = ["curl", "-sS", "--max-time", str(TIMEOUT)]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT + 10)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return json.loads(out.stdout)
    except Exception:
        return None


# ── Provider probes. Each returns (live_ids, control_ok, note). ────────────────────────────────
# control_ok is the ONLY thing that makes an empty live_ids meaningful.

def probe_anthropic():
    k = _secret("anthropic-api-key")
    if not k:
        return set(), False, "no key"
    d = _get("https://api.anthropic.com/v1/models?limit=100",
             {"x-api-key": k, "anthropic-version": "2023-06-01"})
    ids = {m["id"] for m in (d or {}).get("data", [])}
    # CONTROL: the account must list at least one model at all.
    return ids, bool(ids), f"{len(ids)} listed"


def probe_openai():
    k = _secret("openai-api-key")
    if not k:
        return set(), False, "no key"
    d = _get("https://api.openai.com/v1/models", {"Authorization": f"Bearer {k}"})
    ids = {m["id"] for m in (d or {}).get("data", [])}
    return ids, bool(ids), f"{len(ids)} listed"


def probe_google_genai():
    k = _secret("google-ai-api-key")
    if not k:
        return set(), False, "no key"
    d = _get(f"https://generativelanguage.googleapis.com/v1beta/models?key={k}&pageSize=200", {})
    ids = {m["name"].replace("models/", "") for m in (d or {}).get("models", [])}
    return ids, bool(ids), f"{len(ids)} listed"


def probe_google_tts():
    """Cloud TTS. NEEDS x-goog-user-project — without it this 403s and silently returns zero."""
    try:
        tok = subprocess.run(["gcloud", "auth", "print-access-token"],
                             capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return set(), False, "no token"
    d = _get("https://texttospeech.googleapis.com/v1/voices?languageCode=en-US",
             {"Authorization": f"Bearer {tok}", "x-goog-user-project": "kitesforu-dev"})
    voices = (d or {}).get("voices", [])
    fams = {v["name"].split("-")[2] for v in voices if len(v["name"].split("-")) > 2}
    # CONTROL: en-US must expose SOME voices. Zero here means the call failed, not that Google
    # withdrew every English voice.
    ids = {f"gcp-{f.lower()}" for f in fams}
    return ids, bool(voices), f"{len(voices)} voices, families={sorted(fams)}"


def probe_elevenlabs():
    k = _secret("elevenlabs-api-key")
    if not k:
        return set(), False, "no key"
    d = _get("https://api.elevenlabs.io/v1/models", {"xi-api-key": k})
    ms = d if isinstance(d, list) else (d or {}).get("models", [])
    ids = {m["model_id"] for m in ms}
    return ids, bool(ids), f"{len(ids)} listed"


# Rows whose family this tool CANNOT authoritatively list. Reporting them as phantoms would be
# the exact false positive described in the docstring, so they are declared UNCHECKABLE and
# excluded from the phantom set rather than silently passed.
UNCHECKABLE_PREFIXES = ("veo-", "imagen-", "lyria-")

PROBES = {
    "ANTHROPIC": probe_anthropic,
    "OPENAI": probe_openai,
    "ELEVENLABS": probe_elevenlabs,
}

# Providers with no list API we can reach read-only. Not "clean" — UNKNOWN, and said so.
NO_PROBE = {"FAL", "INWORLD", "HUME", "DEEPINFRA"}


def load_catalog(path: str) -> list[dict]:
    with open(path) as fh:
        return [r for r in csv.DictReader(fh)
                if r.get("model_id") and not r["model_id"].startswith("#")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog",
                    default="../kitesforu-workers/config/model_catalog.csv")
    ap.add_argument("--max-age-days", type=int, default=45,
                    help="a price older than this is STALE")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = load_catalog(args.catalog)
    by_prov: dict[str, list[dict]] = {}
    for r in rows:
        by_prov.setdefault((r["provider"] or "?").upper(), []).append(r)

    report: dict = {"catalog": args.catalog, "rows": len(rows), "providers": {}}
    exit_code = 0

    print(f"catalog: {args.catalog}  rows={len(rows)}\n")

    for prov in sorted(by_prov):
        ours = {r["model_id"] for r in by_prov[prov]}
        enabled = {r["model_id"] for r in by_prov[prov] if (r.get("enabled") or "") == "true"}
        entry = {"rows": len(ours), "enabled": len(enabled)}

        if prov == "GOOGLE":
            genai, ok_a, note_a = probe_google_genai()
            tts, ok_b, note_b = probe_google_tts()
            live, ok = genai | tts, (ok_a and ok_b)
            note = f"genai: {note_a} | tts: {note_b}"
        elif prov in PROBES:
            live, ok, note = PROBES[prov]()
        else:
            live, ok, note = set(), False, "no read-only list API — UNKNOWN, not clean"

        print(f"{prov}  rows={len(ours)} enabled={len(enabled)}")
        print(f"   probe: {note}")

        if not ok:
            print("   ⚠ CONTROL FAILED — refusing to report phantoms for this provider.")
            print("     An empty list here means the probe broke, not that the models are gone.")
            entry["control"] = "FAILED"
            report["providers"][prov] = entry
            print()
            continue

        entry["control"] = "ok"
        checkable = {m for m in ours if not m.startswith(UNCHECKABLE_PREFIXES)}
        skipped = sorted(ours - checkable)
        phantom = sorted(checkable - live)

        if skipped:
            print(f"   ↷ {len(skipped)} row(s) UNCHECKABLE by this tool (different API): "
                  f"{', '.join(skipped[:4])}{'…' if len(skipped) > 4 else ''}")
        if phantom:
            exit_code = 1
            print(f"   ❌ PHANTOM — in catalog, NOT listed by provider ({len(phantom)}):")
            for m in phantom:
                en = "ENABLED" if m in enabled else "disabled"
                print(f"        {m}  [{en}]")
                if m in enabled:
                    print("          ⚠ this row is ENABLED — confirm with a real call before "
                          "touching eol_date")
        else:
            print("   ✅ no phantom rows")

        missing = sorted(live - ours)
        entry.update(phantom=phantom, missing_count=len(missing), unchecked=skipped)
        print(f"   ⚠ at provider, not carried: {len(missing)}")
        report["providers"][prov] = entry
        print()

    # ── Staleness. Reads the structured column if present, else says so. ───────────────────────
    print("PRICE FRESHNESS")
    if "price_verified" not in (rows[0].keys() if rows else {}):
        print("   ⚠ no `price_verified` column — freshness is UNKNOWABLE from the catalog.")
        print("     Verification dates currently live in free text inside `notes`, where no")
        print("     tool can read them. Adding the column is what makes this check possible.")
        report["freshness"] = "no_column"
    else:
        today = date.today()
        stale = []
        for r in rows:
            v = (r.get("price_verified") or "").strip()
            if not v:
                stale.append((r["model_id"], "never"))
                continue
            try:
                age = (today - datetime.strptime(v, "%Y-%m-%d").date()).days
                if age > args.max_age_days:
                    stale.append((r["model_id"], f"{age}d"))
            except ValueError:
                stale.append((r["model_id"], f"unparseable:{v}"))
        report["stale"] = stale
        if stale:
            exit_code = 1
            print(f"   ❌ {len(stale)} row(s) older than {args.max_age_days}d:")
            for m, a in stale[:15]:
                print(f"        {m}  {a}")
        else:
            print(f"   ✅ every row verified within {args.max_age_days}d")

    if args.json:
        print("\n" + json.dumps(report, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
