#!/usr/bin/env python3
"""Does ONE speaker label map to ONE voice — and does one voice serve only one
label? $0, read-only, single unordered scan.

    python3 scripts/voice_identity_census.py [--project kitesforu-dev] [--since 2026-08-01]

WHY THIS EXISTS. ``speaker_delivery_census.py`` counts speaker LABELS in the
script. Labels are not voices. ``tts_segment_logs`` carries BOTH ``speaker`` and
``voice_id`` per rendered segment, so the founder's standing complaint — "I
always hear the 2 voices" — can be answered against the audio that actually
shipped rather than against the script that asked for it. This script measures
the LABEL -> VOICE mapping in both directions:

  COLLAPSE   several labels rendered through ONE voice  (a cast on paper only)
  FRACTURE   one label rendered through SEVERAL voices  (a speaker changes person)

THREE METHOD TRAPS THIS SCRIPT EXISTS TO AVOID
──────────────────────────────────────────────
1. IDENTITY IS THE ``(provider, voice_id)`` PAIR, NEVER ``voice_id`` ALONE.
   Measured 2026-08-27 over 56,377 segment rows: 14,376 of them (25.5%, across
   900 of 2,896 jobs) carry a voice_id from a DIFFERENT provider's namespace —
   overwhelmingly an ElevenLabs id logged under ``provider='google'``
   (``pNInz6obpgDQGcFmaJgB`` / ``21m00Tcm4TlvDq8ikWAM``, which are the top two
   ids on provider='elevenlabs'). 100% of those rows are ``fallback_used=True``:
   on an EL failover the log records the REQUESTED EL id, not the Google voice
   the listener actually heard. Keying on voice_id alone silently merges two
   different renders. ``--namespace`` prints this control.

2. ``index`` IS NOT AN ORDERING KEY. 817 of 2,896 jobs have ``index`` all-equal
   or None (the historically-broken all-zero rows); the Firestore array order is
   not chronological either. ``timestamp`` is present on 100% of rows and is the
   only usable ordering key — which matters because MID-EPISODE provider
   switches are a different defect from a job that used one provider throughout.

3. ``created_at`` IS MIXED-TYPE, so ``order_by(...).limit(N)`` type-clips. The
   scan is unordered and filtered in Python.

REPORTED SEPARATELY, ON PURPOSE. A 2-hander that delivers 2 voices is CORRECT,
not a defect — so "delivered exactly 2 voices" is never counted as a loss. The
founder's sentence is about REPETITION ACROSS EPISODES, which is the voice-SET
section: how often different episodes get the same pair.
"""
from __future__ import annotations

import argparse
import datetime
import re
from collections import Counter, defaultdict

from google.cloud import firestore

# Namespace shapes, derived from the observed vocabulary per provider — not
# assumed. See trap 1 above.
_EL_ID = re.compile(r"^[A-Za-z0-9]{20}$")
_GOOGLE_ID = re.compile(r"^[a-z]{2}-[A-Z]{2}-")
_OPENAI_IDS = frozenset(
    {"alloy", "echo", "fable", "onyx", "nova", "shimmer",
     "sage", "ballad", "coral", "ash", "verse"}
)


def dig(d, *path):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def norm_label(x) -> str:
    """Speaker labels drift in punctuation between stages (``Prof_ James`` vs
    ``Prof. James``); compare on the letters."""
    return re.sub(r"[^a-z0-9]+", "", str(x).lower())


def id_namespace(voice_id) -> str:
    v = str(voice_id)
    if _GOOGLE_ID.match(v):
        return "google"
    if v in _OPENAI_IDS:
        return "openai"
    if _EL_ID.match(v):
        return "elevenlabs"
    if re.match(r"^[A-Z][a-z]+$", v):
        return "inworld"
    return "other"


def persona_cast(job: dict) -> dict:
    """``{normalised label: {persona_id, provider, voice_id}}`` from the contract.

    This is the per-job PERSONA STAMP. It is what makes the cast-time question
    answerable at all: ``voice_cast.contract.voice_map[<label>].persona_id``
    names WHICH persona was cast, alongside the provider and voice_id it was
    cast WITH.

    Entries without a voice_id are dropped (nothing to compare); entries without
    a persona_id are KEPT with ``persona_id=None`` so the coverage line can
    report them honestly rather than making the stamp look universal.
    """
    out = {}
    for label, cfg in (dig(job, "voice_cast", "contract", "voice_map") or {}).items():
        if not isinstance(cfg, dict) or not cfg.get("voice_id"):
            continue
        out[norm_label(label)] = {
            "persona_id": cfg.get("persona_id") or None,
            "provider": cfg.get("provider"),
            "voice_id": str(cfg["voice_id"]),
        }
    return out


def raw_contract_entries(job: dict) -> int:
    """How many voice_map entries carry a voice_id, BEFORE label normalisation.

    ``persona_cast`` keys its result by NORMALISED label, so two raw labels that
    normalise to the same key collapse into one. Measured over the full scan
    that is exactly ONE entry — job ``8f1c4416`` carries both ``_narrator`` and
    ``Narrator`` — but a silent 1 is how a silent 100 starts, so the section
    reports the difference rather than letting it vanish.
    """
    return sum(
        1 for v in (dig(job, "voice_cast", "contract", "voice_map") or {}).values()
        if isinstance(v, dict) and v.get("voice_id")
    )


def hop1_entry(job: dict) -> dict | None:
    """This job's HOP 1 contribution — or ``None`` when it was never cast.

    DELIVERY-INDEPENDENT BY CONSTRUCTION, and that is the whole point. Hop 1
    compares the persona YAML against the CONTRACT; it never reads
    ``tts_segment_logs``. Sourcing this from the delivery rows (which skip jobs
    with no rendered audio) silently dropped 13 jobs / 27 entries — 3.2% of the
    denominator — and made the section answer a narrower question than the one
    it printed. Measured 2026-08-27: 420 jobs carry a contract with voice_ids;
    only 407 of them delivered.
    """
    cast = persona_cast(job)
    if not cast:
        return None
    created = job.get("created_at")
    return {
        "month": created.isoformat()[:7]
        if isinstance(created, datetime.datetime) else "?",
        "cast": cast,
        "raw_entries": raw_contract_entries(job),
    }


def load_persona_voices(personas_dir: str) -> dict:
    """``{persona_id: {provider: declared voice}}`` from ``personas/*.yaml``.

    Lives in kitesforu-workers, not here, so this is OPTIONAL and takes an
    explicit path (``--personas``). Returns {} when the directory is absent or
    PyYAML is missing — hop 1 then reports only its repo-local half rather than
    failing the whole census.
    """
    import glob
    import os

    try:
        import yaml  # noqa: PLC0415 — optional dependency, section is opt-in
    except ImportError:
        return {}

    out = {}
    for path in sorted(glob.glob(os.path.join(personas_dir, "*.yaml"))):
        try:
            doc = yaml.safe_load(open(path))
        except Exception:  # noqa: BLE001 — a malformed persona must not kill the scan
            continue
        if not isinstance(doc, dict):
            continue
        pid = doc.get("id") or os.path.basename(path).rsplit(".", 1)[0]
        found: dict = {}

        def walk(node, found=found):
            if isinstance(node, dict):
                for key, val in node.items():
                    # Providers do NOT agree on the key: the inworld block says
                    # ``voice:`` and the elevenlabs block says ``voice_id:``
                    # (measured over personas/*.yaml — 50 inworld ``voice``, 29
                    # elevenlabs ``voice_id``). Reading only one of them makes
                    # this silently blind to a third of the declarations while
                    # still reporting a confident-looking number.
                    declared = None
                    if isinstance(val, dict):
                        for candidate in ("voice", "voice_id"):
                            if isinstance(val.get(candidate), str):
                                declared = val[candidate]
                                break
                    if declared is not None:
                        found[key] = declared
                    else:
                        walk(val, found)
            elif isinstance(node, list):
                for item in node:
                    walk(item, found)

        walk(doc, found)
        if found:
            out[pid] = found
    return out


def hop1_substitutions(cast: dict, persona_voices: dict) -> list:
    """Rows where the CONTRACT's voice differs from the persona's DECLARED one.

    Returns ``[(label, persona_id, declared, contracted)]``.

    Compared PER PROVIDER — a persona declares a voice per provider, so an
    inworld contract entry is only ever checked against the persona's inworld
    declaration. Comparing across providers would report every provider swap as
    a substitution, which is the same bare-id mistake this script warns about
    for delivery (an id only means something next to its provider).

    Silent on entries with no persona_id, no provider, or a persona this
    directory does not define — those are UNCHECKABLE, not matching, and the
    caller counts them separately.
    """
    rows = []
    for label, cfg in sorted(cast.items()):
        pid, provider = cfg.get("persona_id"), cfg.get("provider")
        if not pid or not provider:
            continue
        declared = (persona_voices.get(pid) or {}).get(provider)
        if not declared:
            continue
        if str(cfg["voice_id"]) != str(declared):
            rows.append((label, pid, str(declared), str(cfg["voice_id"])))
    return rows


def usable_segments(job: dict) -> list:
    """Rendered rows carrying both a speaker and a voice, in TIME order."""
    segs = [
        s for s in (job.get("tts_segment_logs") or [])
        if isinstance(s, dict) and s.get("speaker") and s.get("voice_id")
    ]
    segs.sort(key=lambda s: str(s.get("timestamp") or ""))
    return segs


def voice_identity(seg: dict) -> tuple:
    """The pair, never the bare id — see trap 1."""
    return (seg.get("provider"), str(seg.get("voice_id")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="kitesforu-dev")
    ap.add_argument("--since", help="ISO8601; only jobs created at/after this")
    ap.add_argument("--personas",
                    help="path to kitesforu-workers/personas/ — enables the HOP 1 "
                         "comparison of each persona's DECLARED voice against the "
                         "voice the contract actually cast. Optional: that directory "
                         "lives in another repo, so without it hop 1 reports only its "
                         "repo-local half (stamp coverage + per-persona variation).")
    ap.add_argument("--namespace", action="store_true",
                    help="print the provider/voice_id namespace control (trap 1)")
    args = ap.parse_args()

    since = None
    if args.since:
        since = datetime.datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=datetime.timezone.utc)

    db = firestore.Client(project=args.project)

    scanned = with_logs = 0
    rows = []
    cast_rows = []  # HOP 1 — cast, delivered or not
    ns_mismatch = Counter()
    ns_total = 0
    seg_by_voice = Counter()

    for doc in db.collection("podcast_jobs").stream():  # UNORDERED — see trap 3
        job = doc.to_dict() or {}
        scanned += 1
        created = job.get("created_at")
        _in_window = (not since) or (
            isinstance(created, datetime.datetime) and created >= since
        )

        # HOP 1 POPULATION — collected HERE, BEFORE the delivery filter below.
        # Hop 1 compares the persona YAML against the CONTRACT and needs no
        # delivered audio at all, so a job that was cast but never rendered is
        # still perfectly checkable. Gathering it from ``rows`` (which exists to
        # answer delivery questions and therefore skips undelivered jobs)
        # silently dropped 13 jobs / 27 entries — 3.2% of the denominator —
        # and made this section quietly answer a narrower question than the one
        # it prints. Measured 2026-08-27: 420 jobs carry a contract with
        # voice_ids, only 407 of them delivered.
        if _in_window:
            _hop1 = hop1_entry(job)
            if _hop1:
                cast_rows.append(_hop1)

        segs = usable_segments(job)
        if not segs:
            continue
        with_logs += 1
        if not _in_window:
            continue

        lab2voice = defaultdict(set)
        voice2lab = defaultdict(set)
        for s in segs:
            vid = voice_identity(s)
            lab2voice[norm_label(s["speaker"])].add(vid)
            voice2lab[vid].add(norm_label(s["speaker"]))
            seg_by_voice[vid] += 1
            ns_total += 1
            n = id_namespace(s.get("voice_id"))
            if n != "other" and n != s.get("provider"):
                ns_mismatch[(s.get("provider"), n, bool(s.get("fallback_used")))] += 1

        providers = [s.get("provider") for s in segs]
        fractured = {l: v for l, v in lab2voice.items() if len(v) > 1}
        rows.append(dict(
            job=doc.id,
            month=created.isoformat()[:7] if isinstance(created, datetime.datetime) else "?",
            fmt=(dig(job, "audio_config", "audio_format") or "?"),
            tier=job.get("quality_tier") or job.get("subscription_tier") or "?",
            labels=set(lab2voice), voices=set(voice2lab),
            fractured=fractured,
            collapsed={v: l for v, l in voice2lab.items() if len(l) > 1},
            # A MID-EPISODE switch is a provider CHANGE along the timeline, which
            # is a different defect from a job that used one provider throughout.
            switches=sum(1 for a, b in zip(providers, providers[1:]) if a != b),
            any_fallback=any(s.get("fallback_used") for s in segs),
            providers=sorted(set(providers)),
            # The CAST contract's own voice_id per label — so "did the cast
            # reach the listener" is answerable without a second scan.
            contract={
                norm_label(k): str(v["voice_id"])
                for k, v in (dig(job, "voice_cast", "contract", "voice_map") or {}).items()
                if isinstance(v, dict) and v.get("voice_id")
            },
            delivered_by_label={l: {vid for _p, vid in vs} for l, vs in lab2voice.items()},
            # The per-job PERSONA STAMP — see persona_cast(). Kept whole (not
            # flattened to voice_id) because hop 1 needs persona_id + provider.
            cast=persona_cast(job),
            # The SAME question under the BARE-ID key. Reported alongside the pair
            # key because the two disagree by design and two lanes once published
            # 146 and 199 for "the same" measurement -- the gap is entirely jobs
            # where one id string was rendered on two DIFFERENT providers.
            fractured_bare={l for l, vs in lab2voice.items() if len({v for _p, v in vs}) > 1},
            # Did any segment of a FRACTURED label carry a fallback? Job-level
            # UPPER BOUND: one fractured label may fall back while another does not.
            frac_had_fallback=any(
                bool(s.get("fallback_used")) for s in segs
                if norm_label(s["speaker"]) in
                {l for l, vs in lab2voice.items() if len({v for _p, v in vs}) > 1}
            ),
        ))

    print(f"scanned {scanned} podcast_jobs docs; {with_logs} carry tts_segment_logs")
    print(f"POPULATION: {len(rows)} jobs" + (f" created >= {args.since}" if since else ""))
    if not rows and not cast_rows:
        print("  nothing in this window.")
        return
    if not rows:
        # DELIVERY rows are empty, but HOP 1 NEEDS NO DELIVERY. qa#146 made
        # `hop1_entry` delivery-independent and this early return silently undid
        # that one layer up: a window whose jobs are CAST but not yet RENDERED
        # reported "nothing in this window" while carrying a perfectly checkable
        # contract. Hit live on 2026-08-28 measuring a post-deploy job that was
        # still running — the census said 0 jobs while the doc count had already
        # risen by one.
        # The return was LOAD-BEARING (the delivery rates divide by len(rows),
        # ZeroDivisionError), so the divisions are guarded rather than the guard
        # removed. Delivery-dependent sections now report zeros honestly.
        print("  no DELIVERED audio in this window — delivery-dependent sections")
        print("  report zeros. HOP 1 needs no delivery and still runs below.")

    if args.namespace:
        print("\n" + "=" * 72)
        print("CONTROL — does voice_id belong to the logged provider? (trap 1)")
        tot = sum(ns_mismatch.values())
        print(f"  {tot} of {ns_total} rows ({100*tot/ns_total:.1f}%) are MISMATCHED")
        for (prov, ns, fb), c in ns_mismatch.most_common(8):
            print(f"     {c:7d}  provider={prov:<11s} id-namespace={ns:<11s} fallback_used={fb}")
        print("  A mismatched row's voice_id is the REQUESTED voice, not the one heard.")

    multi = [r for r in rows if len(r["labels"]) >= 2]
    coll = [r for r in multi if r["collapsed"]]
    print("\n" + "=" * 72)
    print("DIRECTION 1 — COLLAPSE (several LABELS share ONE voice)")
    print("=" * 72)
    print(f"  jobs delivering >=2 distinct labels:            {len(multi)}")
    if multi:
        print(f"  >=1 voice serving MORE THAN ONE label:         {len(coll)}"
              f"  ({100*len(coll)/len(multi):.0f}%)")
        print(f"  ALL labels collapsed onto a SINGLE voice:      "
              f"{sum(1 for r in multi if len(r['voices']) == 1)}")
        print(f"    of the collapsed, WITHOUT any fallback row:  "
              f"{sum(1 for r in coll if not r['any_fallback'])}"
              "   <- nothing failed; the cast was never distinct")
        for f, c in Counter(r["fmt"] for r in coll).most_common(8):
            print(f"       {c:4d}  {f}")

    frac = [r for r in rows if r["fractured"]]
    print("\n" + "=" * 72)
    print("DIRECTION 2 — FRACTURE (ONE label rendered by SEVERAL voices)")
    print("=" * 72)
    print(f"  jobs with >=1 label on >1 (provider,voice_id):  {len(frac)}"
          f"  of {len(rows)}  ({100*len(frac)/max(1, len(rows)):.0f}%)")
    intra = sum(
        1 for r in frac
        if any(len({p for p, _ in v}) == 1 for v in r["fractured"].values())
    )
    print(f"     {intra:4d}  >=1 fractured label stayed on ONE provider (not a failover)")
    print(f"     {sum(1 for r in frac if r['switches']):4d}  had a MID-EPISODE provider switch")
    print(f"     {sum(1 for r in frac if r['any_fallback']):4d}  had >=1 fallback_used row")
    print(f"     {sum(1 for r in frac if not r['switches'] and not r['any_fallback']):4d}"
          "  had NEITHER  <- nothing failed; the selector simply decided differently")

    # BOTH KEYS, side by side. These legitimately differ and the difference is
    # informative, so neither is reported alone.
    fb = [r for r in rows if r["fractured_bare"]]
    print(f"\n  SAME QUESTION, BARE voice_id KEY:            {len(fb)}"
          f"  ({100*len(fb)/max(1, len(rows)):.0f}%)")
    print(f"  the difference ({len(frac) - len(fb)} jobs) is one id string rendered on TWO providers")
    print("     -- a real audible change, so the PAIR key is the correct one; but check the")
    print("        month table before treating it as live, it is the EL->google failover class.")
    both = [r for r in rows if r["fractured_bare"] and len(r["labels"]) > len(r["voices"])]
    print(f"  BOTH fractured AND collapsed in one job:      {len(both)}")
    print("     -- if this is small, the two directions are DIFFERENT JOBS and should be")
    print("        investigated as two bugs until evidence unifies them.")
    print(f"\n  CAUSAL SPLIT on the {len(fb)} bare-key fractures:")
    hadfb = sum(1 for r in fb if r["frac_had_fallback"])
    print(f"     {hadfb:4d}  a fractured label contains a FALLBACK segment  (job-level UPPER BOUND)")
    print(f"     {len(fb)-hadfb:4d}  NO fallback anywhere in the fractured label  <- NOT failover")
    for f, c in Counter(r["fmt"] for r in frac).most_common(8):
        print(f"       {c:4d}  {f}")

    print("\n" + "=" * 72)
    print("THE '2 VOICES' CLAIM — repetition ACROSS episodes, not within one")
    print("=" * 72)
    print("  (a 2-hander delivering 2 voices is CORRECT — never counted as a loss)")
    sets = Counter(frozenset(r["voices"]) for r in multi)
    for vs, c in sets.most_common(5):
        shown = ", ".join(f"{p}:{v}" for p, v in sorted(vs))
        print(f"     {c:5d}  {100*c/len(multi):5.1f}%  {{{shown}}}")
    print(f"     distinct voice SETS across {len(multi)} multi-label jobs: {len(sets)}")

    print("\n" + "=" * 72)
    print("THE CAST CONTRACT vs WHAT SHIPPED")
    print("=" * 72)
    print("  voice_cast.contract.voice_map[label].voice_id  vs  tts_segment_logs")
    withc = [r for r in rows if r["contract"]
             and any(l in r["delivered_by_label"] for l in r["contract"])]
    if not withc:
        print("  no job in this window carries both a cast contract and delivered segments.")
    else:
        bad = []
        for r in withc:
            common = [l for l in r["contract"] if l in r["delivered_by_label"]]
            if any(r["delivered_by_label"][l] != {r["contract"][l]} for l in common):
                bad.append(r)
        print(f"  jobs with BOTH a contract (with voice_ids) and delivered segments: {len(withc)}")
        print(f"  >=1 label delivered a voice the contract did NOT name:  {len(bad)}"
              f"  ({100*len(bad)/len(withc):.0f}%)")
        bym = defaultdict(lambda: [0, 0])
        for r in withc:
            bym[r["month"]][0] += 1
        for r in bad:
            bym[r["month"]][1] += 1
        for m in sorted(bym):
            n, b = bym[m]
            print(f"     {m}  {b:4d}/{n:<5d}  ({100*b/n:.0f}%)")
        for r in bad[:3]:
            common = [l for l in r["contract"] if l in r["delivered_by_label"]]
            for l in common:
                if r["delivered_by_label"][l] != {r["contract"][l]}:
                    print(f"     {r['job'][:8]} {r['fmt']}: label {l!r} contracted "
                          f"{r['contract'][l]!r}, delivered {sorted(r['delivered_by_label'][l])}")
                    break

    print("\n" + "=" * 72)
    print("HOP 1 — THE PERSONA STAMP: what was CAST vs what the persona DECLARES")
    print("=" * 72)
    print("  stamp: voice_cast.contract.voice_map[<label>].persona_id (+ .provider, .voice_id)")
    print("  NOTE the chain is TWO hops and they are different questions:")
    print("     hop 1  persona YAML voice -> contract voice_id   (cast-time substitution, here)")
    print("     hop 2  contract voice_id  -> delivered voice_id  (delivery drift, section above)")
    # NOT `rows` — see the note at the collection site. `rows` requires DELIVERED
    # audio; hop 1 does not, and using it silently narrowed this denominator.
    staged = cast_rows
    if not staged:
        print("  no job in this window carries a cast contract — nothing to report.")
    else:
        entries = [c for r in staged for c in r["cast"].values()]
        _raw = sum(r["raw_entries"] for r in staged)
        with_pid = [c for c in entries if c["persona_id"]]
        pids = {c["persona_id"] for c in with_pid}
        months = sorted({r["month"] for r in staged if r["month"] != "?"})
        print(f"  POSITIVE CONTROL — jobs carrying voice_cast.contract.voice_map: {len(staged)}")
        # NOT "N of M". This population is INDEPENDENT of delivery, so it is not a
        # subset of the delivered set and an "of" would invite exactly the wrong
        # reading — the same quietly-wrong denominator statement this section was
        # just fixed for. The two sets OVERLAP and neither contains the other:
        # a job may be cast without rendering, or render without a contract.
        print("     This population is INDEPENDENT of delivery — hop 1 needs none.")
        print(f"     For scale, {len(rows)} job(s) in this window delivered audio; the two sets")
        print("     overlap and NEITHER CONTAINS THE OTHER.")
        if _raw != len(entries):
            # A silent 1 is how a silent 100 starts.
            print(f"     ⚠ {_raw - len(entries)} raw entr(ies) collapsed by label "
                  "normalisation (two raw labels -> one key).")
        print(f"     voice_map entries: {len(entries)}   with a persona_id: {len(with_pid)}"
              f"  ({100*len(with_pid)/len(entries):.0f}%)")
        print(f"     distinct persona_ids ever stamped: {len(pids)}")
        if months:
            # A reader seeing "31 personas" without this will over-read it as fleet history.
            print(f"  ⚠ COVERAGE LIMIT: the stamp exists only for {months[0]}..{months[-1]} "
                  f"({len(staged)} jobs).")
            print("     It is NOT fleet history — do not read these counts as all-time.")

        # Repo-local half: does ONE persona get cast with DIFFERENT voices across jobs?
        # Needs no persona YAML, so it always runs.
        per_persona = defaultdict(Counter)
        for r in staged:
            for c in r["cast"].values():
                if c["persona_id"]:
                    per_persona[c["persona_id"]][(c["provider"], c["voice_id"])] += 1
        unstable = {p: v for p, v in per_persona.items() if len(v) > 1}
        print(f"\n  personas cast with MORE THAN ONE (provider, voice_id) across jobs: "
              f"{len(unstable)} of {len(per_persona)}")
        for pid, counts in sorted(unstable.items(), key=lambda kv: -sum(kv[1].values()))[:6]:
            shown = " · ".join(f"{p}:{v}×{n}" for (p, v), n in counts.most_common(4))
            print(f"     {pid:<24s} {shown}")

        if args.personas:
            pv = load_persona_voices(args.personas)
            if not pv:
                print(f"\n  --personas {args.personas}: no persona YAMLs read "
                      "(missing directory or PyYAML) — hop 1 comparison skipped.")
            else:
                print(f"\n  comparing against {len(pv)} persona YAML(s) in {args.personas}")
                bym = defaultdict(lambda: [0, 0])
                subs = Counter()
                for r in staged:
                    checkable = [
                        c for c in r["cast"].values()
                        if c["persona_id"] and c["provider"]
                        and (pv.get(c["persona_id"]) or {}).get(c["provider"])
                    ]
                    bym[r["month"]][0] += len(checkable)
                    for _label, pid, declared, got in hop1_substitutions(r["cast"], pv):
                        bym[r["month"]][1] += 1
                        subs[(pid, declared, got)] += 1
                # BY PROVIDER is the informative cut, and it is easy to get
                # silently wrong: reading only the inworld key shape once made
                # this look like an inworld-only phenomenon when elevenlabs
                # substitutes at the same rate. Providers are reported
                # separately so that mistake cannot hide again.
                by_prov = defaultdict(lambda: [0, 0])
                for r in staged:
                    for c in r["cast"].values():
                        if (c["persona_id"] and c["provider"]
                                and (pv.get(c["persona_id"]) or {}).get(c["provider"])):
                            by_prov[c["provider"]][0] += 1
                    for _label, pid, _declared, got in hop1_substitutions(r["cast"], pv):
                        prov = next((c["provider"] for c in r["cast"].values()
                                     if c["persona_id"] == pid and c["voice_id"] == got), None)
                        if prov:
                            by_prov[prov][1] += 1
                total = sum(n for n, _ in bym.values())
                bad = sum(b for _, b in bym.values())
                print(f"  checkable entries (persona has a declaration for that provider): {total}")
                if total:
                    print(f"  contract voice DIFFERS from the declaration: {bad}  "
                          f"({100*bad/total:.0f}%)")
                    for m in sorted(bym):
                        n, b = bym[m]
                        if n:
                            print(f"     {m}  {b:4d}/{n:<5d}  ({100*b/n:.0f}%)")
                    print("  BY PROVIDER (a persona declares a voice per provider):")
                    for prov in sorted(by_prov):
                        n, b = by_prov[prov]
                        if n:
                            print(f"     {prov:<12s} checkable {n:4d}   substituted {b:4d}"
                                  f"  ({100*b/n:.0f}%)")
                    print("  most frequent (persona: declared -> cast):")
                    for (pid, declared, got), n in subs.most_common(6):
                        print(f"     {n:4d}  {pid:<24s} {declared!r} -> {got!r}")
                    print("  NOTE a substitution is NOT evidence the declared voice is invalid —")
                    print("     check whether it is delivered elsewhere in the fleet before")
                    print("     concluding anything about the voice existing.")

    print("\n" + "=" * 72)
    print("BY MONTH — so a fix that landed mid-window cannot hide in an average")
    print("=" * 72)
    by = defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        b = by[r["month"]]
        b[0] += 1
        if len(r["labels"]) >= 2:
            b[1] += 1
            if r["collapsed"]:
                b[2] += 1
        if r["fractured"]:
            b[3] += 1
    print(f"  {'month':9s}{'jobs':>6s}{'multi':>7s}{'COLLAPSE':>10s}{'FRACTURE':>10s}")
    for m in sorted(by):
        n, mu, co, fr = by[m]
        cs = f"{co} ({100*co/mu:.0f}%)" if mu else "-"
        print(f"  {m:9s}{n:6d}{mu:7d}{cs:>10s}{f'{fr} ({100*fr/n:.0f}%)':>10s}")


if __name__ == "__main__":
    main()
