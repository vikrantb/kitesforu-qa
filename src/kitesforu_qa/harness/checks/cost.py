"""Cost-correctness checks (Claude-main lane) — deterministic, $0. PIN the closed cost loop.

The founder saw TTS reported 18x high (priced as the INTENDED ElevenLabs ~$183/M when the job fell
back to the real inworld ~$10/M). We fixed it (costs.tts_usd_actual = the REAL rendered provider x
chars, surfaced as the emerald 'actual' badge). These checks RECOMPUTE the expected TTS cost from
the persisted tts_segment_logs and compare to what was reported — so the fix can NEVER silently
regress (the harness is the discovery engine: a future change that re-introduces the over-report
fails here).

A RE-VOICED JOB CANNOT BE CHECKED AGAINST ITS LOGS ALONE. The logs keep the take that shipped, with
a $0 row for each line it reused from an earlier pass. When costs.tts.meta.renders counts more
renders than the logs hold billed rows, the record also paid for takes that left no row: a regen's
replaced or discarded take. Then the expected cost is what the record says it billed
(costs.tts_providers, characters per provider) at the real rates, believed only when it holds, on
each provider the logs name, what they show was billed there. That still catches a wrong price, and
a record naming the wrong provider for a take the logs keep. It cannot catch a wrong provider on a
take that left no row, and a record naming a provider with no rate is not checked.
"""
# per-M-char USD rates — mirror kitesforu-workers cost_calculator._TTS_USD_PER_M_CHARS.
from datetime import datetime, timezone

from ..check import check, skip

_TTS_RATE = {"inworld": 10.0, "gemini": 8.0, "openai": 13.0, "google": 16.0, "elevenlabs": 183.30}

# The reconcile path began writing costs.tts_usd_actual ~2026-06-22 03:37 UTC. Jobs created before
# that legitimately lack the field — the cost checks must NOT fire on the historical backlog (the
# fidelity audit found this is the single largest false-positive source).
TTS_RECONCILE_DEPLOY_TS = datetime(2026, 6, 22, 3, 37, tzinfo=timezone.utc)


def _predates_reconcile(art) -> bool:
    ca = art.created_at
    try:
        return ca is not None and ca < TTS_RECONCILE_DEPLOY_TS
    except TypeError:
        return False


def _is_billed(row) -> bool:
    """Did a provider bill for this row? Not a failed row, and not a reused take: no provider was
    called for it, and an earlier pass paid. Mirrors is_billed_render, which kitesforu-workers #3388
    adds to its job_cost_writer."""
    return isinstance(row, dict) and row.get("success") is not False and not row.get("reused")


def _is_reused(row) -> bool:
    """Is this row a take reused from an earlier pass? That pass billed its render, on the provider
    the row names."""
    return isinstance(row, dict) and row.get("success") is not False and bool(row.get("reused"))


# A reused row's text_length is its directed line's length, while the render it reuses was billed
# for the text that provider was sent, which can be shorter: a tag the provider is not sent. So a
# record bears out a reused line when it holds this share of the line's characters.
_LEAST_BILLED_SHARE_OF_A_REUSED_LINE = 0.7


def _priced(chars_by_provider):
    """USD for {provider: chars} at the real rates. None if nothing is priceable."""
    total = 0.0
    seen = False
    for provider, chars in chars_by_provider.items():
        rate = _TTS_RATE.get(provider)
        if rate is None or chars <= 0:
            continue
        total += rate * chars / 1_000_000
        seen = True
    return total if seen else None


def _chars_by_provider(rows):
    chars_by_provider: dict[str, int] = {}
    for r in rows:
        provider = str(r.get("provider") or "").lower()
        chars_by_provider[provider] = chars_by_provider.get(provider, 0) + int(r.get("text_length") or 0)
    return chars_by_provider


def _billed_chars_in_logs(art):
    return _chars_by_provider(r for r in art.tts_segment_logs if _is_billed(r))


def _chars_the_logs_show_billed(art):
    """{provider: the fewest characters the run billed there, by the take the logs keep}: every billed
    row, and each reused line at the least share its render billed."""
    shown = {p: float(c) for p, c in _billed_chars_in_logs(art).items()}
    for p, c in _chars_by_provider(r for r in art.tts_segment_logs if _is_reused(r)).items():
        shown[p] = shown.get(p, 0.0) + c * _LEAST_BILLED_SHARE_OF_A_REUSED_LINE
    return shown


def _expected_tts_from_logs(art):
    """Recompute TTS cost from the REAL provider + chars of each billed row. None if nothing priceable."""
    return _priced(_billed_chars_in_logs(art))


def _billed_chars_in_record(art):
    """{provider: chars} the record says a re-voiced job billed; None when the logs can check the job
    or contradict the record.

    None unless the logs hold rows, the record counts more renders than they hold billed rows, and
    on each provider the logs name the record holds what they show was billed there. A record that
    names the wrong provider for a take the logs keep falls short on the real one, and is not
    believed over them."""
    if not art.tts_segment_logs:
        return None
    renders = ((art.costs.get("tts") or {}).get("meta") or {}).get("renders")
    recorded = art.costs.get("tts_providers")
    billed_rows = sum(1 for r in art.tts_segment_logs if _is_billed(r))
    if not isinstance(renders, int) or renders <= billed_rows or not isinstance(recorded, dict):
        return None
    chars_by_provider = {str(p).lower(): int(c or 0) for p, c in recorded.items()}
    shown = _chars_the_logs_show_billed(art)
    if any(chars_by_provider.get(p, 0) < c for p, c in shown.items()):
        return None
    return chars_by_provider


def _expected_tts(art):
    """(expected USD, what it was priced from): the record on a re-voiced job its logs bear out, the
    logs otherwise. Skips the check when that record names a provider with no rate: the record
    cannot be priced, and the logs cannot check a re-voiced job."""
    recorded = _billed_chars_in_record(art)
    if recorded is None:
        return _expected_tts_from_logs(art), "logs"
    unrated = sorted(p for p in recorded if p not in _TTS_RATE)
    if unrated:
        skip(f"the record names a provider with no rate: {', '.join(unrated)}")
    return _priced(recorded), "the record's billed chars"


@check("cost.tts_actual_present", dimension="cost-correctness", severity="high")
def tts_actual_present(art):
    "costs.tts_usd_actual must be written — its ABSENCE on a rendered job is the streaming-path regression."
    if not art.tts_segment_logs:
        skip("no tts_segment_logs to expect a cost from")
    if _predates_reconcile(art):
        skip("job predates the costs.tts_usd_actual reconcile fix (2026-06-22)")
    val = art.costs.get("tts_usd_actual")
    return val is not None, f"tts_usd_actual={val}"


@check("cost.tts_actual_matches_logs", dimension="cost-correctness", severity="high")
def tts_actual_matches_logs(art):
    "costs.tts_usd_actual must equal the real provider x chars billed (the reconciliation)."
    if _predates_reconcile(art):
        skip("job predates the costs.tts_usd_actual reconcile fix (2026-06-22)")
    expected, source = _expected_tts(art)
    if expected is None:
        skip("no priceable tts_segment_logs")
    val = art.costs.get("tts_usd_actual")
    if val is None:
        return False, "tts_usd_actual missing but priceable logs exist"
    ok = abs(float(val) - expected) <= max(0.001, expected * 0.05)
    return ok, f"reported {val} vs expected {expected:.6f} from {source}"


@check("cost.no_provider_mismatch_overcount", dimension="cost-correctness", severity="critical")
def no_provider_mismatch_overcount(art):
    "The 18x class: reported TTS must not be priced at a provider the job never rendered on."
    expected, _source = _expected_tts(art)
    if expected is None:
        skip("no priceable logs")
    val = art.costs.get("tts_usd_actual")
    if val is None:
        skip("no actual to compare")
    ratio = float(val) / max(expected, 1e-9)
    return float(val) <= expected * 3 + 0.001, f"reported {val} vs real-provider expected {expected:.4f} ({ratio:.1f}x)"


@check("cost.total_present", dimension="cost-correctness", severity="medium")
def total_present(art):
    "A completed job should carry a total cost estimate."
    if not art.costs:
        skip("no costs block")
    t = art.costs.get("total_usd_estimate")
    return t is not None and float(t) >= 0, f"total_usd_estimate={t}"


@check("cost.no_negative", dimension="cost-correctness", severity="high")
def no_negative(art):
    "No cost figure anywhere in the costs block may be negative."
    bad = []

    def walk(d, path=""):
        if isinstance(d, dict):
            for k, v in d.items():
                walk(v, f"{path}.{k}")
        elif isinstance(d, (int, float)) and not isinstance(d, bool) and d < 0:
            bad.append(path.lstrip("."))

    walk(art.costs)
    return len(bad) == 0, (f"negative costs: {bad}" if bad else "no negatives")


@check("cost.tts_providers_present", dimension="cost-correctness", severity="low")
def tts_providers_present(art):
    "costs.tts_providers (the per-provider char breakdown) should accompany tts_usd_actual."
    if art.costs.get("tts_usd_actual") is None:
        skip("no tts_usd_actual")
    return art.costs.get("tts_providers") is not None, f"tts_providers={art.costs.get('tts_providers')}"
