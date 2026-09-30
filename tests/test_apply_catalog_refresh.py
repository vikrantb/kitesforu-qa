"""The price applier: a model read twice applies its NEWEST read, and every stamp names its page.

2026-09-30: the model re-look read the ElevenLabs page again and found a second cut (50 -> 40 per
1M characters for Flash/Turbo). The ground truth already held the 2026-08-27 read (91.65 -> 50).
Applied in file order, the second run refused the first entry ("expected 91.65, catalog holds 40"):
a refusal is a non-zero exit, so the refresh could never be re-run. And every confirmed row was
stamped with a hardcoded Google URL, whatever its provider.
"""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "apply_catalog_refresh.py"
_spec = importlib.util.spec_from_file_location("apply_catalog_refresh", _SCRIPT)
assert _spec and _spec.loader
applier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(applier)

_COLS = ["model_id", "provider", "cost_per_unit", "unit_description", "eol_date", "price_verified", "price_source_url"]
_EL = "https://elevenlabs.io/pricing/api"


def _catalog(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "kitesforu-workers" / "config" / "model_catalog.csv"
    path.parent.mkdir(parents=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in _COLS})
    return path


def _rows(path: Path) -> dict:
    return {r["model_id"]: r for r in csv.DictReader(path.open())}


def _gt(corrections: list[dict], confirmed: list[dict] | None = None) -> dict:
    return {"read_date": "2026-09-30", "price_corrections": corrections,
            "confirmed_correct_no_change": confirmed or [], "lifecycle": []}


_CHAIN = [
    {"model_id": "eleven_flash_v2_5", "old": 91.65, "new": 50.0, "unit": "per 1M characters",
     "source": _EL, "read_date": "2026-08-27"},
    {"model_id": "eleven_flash_v2_5", "old": 50.0, "new": 40.0, "unit": "per 1M characters",
     "source": _EL, "read_date": "2026-09-30"},
]


def test_a_model_read_twice_applies_its_newest_read_and_reruns_clean(tmp_path):
    path = _catalog(tmp_path, [{"model_id": "eleven_flash_v2_5", "cost_per_unit": "50.00",
                                "unit_description": "per 1M characters", "price_verified": "2026-08-27"}])
    changes, refusals = applier.apply_to(path, _gt(_CHAIN), write=True)
    assert refusals == [] and changes == ["eleven_flash_v2_5: 50.0 -> 40.0 (per 1M characters)"]
    row = _rows(path)["eleven_flash_v2_5"]
    assert (row["cost_per_unit"], row["price_verified"]) == ("40.00", "2026-09-30")

    changes, refusals = applier.apply_to(path, _gt(_CHAIN), write=True)
    assert (changes, refusals) == ([], []), "a re-run after the newest read is applied must be clean"


def test_the_newest_read_wins_whatever_the_file_order(tmp_path):
    path = _catalog(tmp_path, [{"model_id": "eleven_flash_v2_5", "cost_per_unit": "50.00",
                                "unit_description": "per 1M characters"}])
    changes, refusals = applier.apply_to(path, _gt(list(reversed(_CHAIN))), write=True)
    assert refusals == [] and _rows(path)["eleven_flash_v2_5"]["cost_per_unit"] == "40.00"


def test_a_stale_newest_read_is_still_refused(tmp_path):
    path = _catalog(tmp_path, [{"model_id": "eleven_flash_v2_5", "cost_per_unit": "45.00",
                                "unit_description": "per 1M characters"}])
    changes, refusals = applier.apply_to(path, _gt(_CHAIN), write=True)
    assert changes == [] and refusals == [
        "eleven_flash_v2_5: expected 50.0, catalog holds 45.00 -- research is stale for this row"]


def test_a_confirmed_row_is_stamped_with_its_own_page_and_date(tmp_path):
    path = _catalog(tmp_path, [
        {"model_id": "eleven_v3", "cost_per_unit": "80.00", "unit_description": "per 1M characters"},
        {"model_id": "veo-3.1-lite-generate-001", "cost_per_unit": "0.05", "unit_description": "per second"},
    ])
    gt = _gt(
        [{"model_id": "veo-3.1-lite-generate-001", "old": 0.05, "new": 0.03, "unit": "per second",
          "source": "https://cloud.google.com/vertex-ai/generative-ai/pricing", "read_date": "2026-09-30"}],
        [{"model_id": "eleven_v3", "value": 80.0, "unit": "per 1M characters", "source": _EL, "read_date": "2026-09-29"}],
    )
    applier.apply_to(path, gt, write=True)
    row = _rows(path)["eleven_v3"]
    assert (row["price_source_url"], row["price_verified"]) == (_EL, "2026-09-29")


def test_the_committed_ground_truth_applies_to_nothing_it_cannot_explain():
    """Every correction's model is unique per read date, and each model's reads chain: a newer
    read's ``old`` is the price an older read left (so history reads as one line per model)."""
    import json

    gt = json.loads((_SCRIPT.parent.parent / "data" / "model_pricing_ground_truth.json").read_text())
    by_model: dict = {}
    for c in gt["price_corrections"]:
        assert "read_date" in c and "source" in c, c["model_id"]
        by_model.setdefault(c["model_id"], []).append(c)
    for mid, reads in by_model.items():
        reads.sort(key=lambda c: c["read_date"])
        dates = [c["read_date"] for c in reads]
        assert len(dates) == len(set(dates)), f"{mid}: two reads on one date"
        for older, newer in zip(reads, reads[1:]):
            assert abs(newer["old"] - older["new"]) < 1e-9, f"{mid}: {older['new']} then {newer['old']}"
    for c in gt["confirmed_correct_no_change"]:
        assert c.get("source", "").startswith("https://"), c["model_id"]


def test_an_absent_catalog_fails_instead_of_skipping(tmp_path, capsys):
    assert applier.main(["--check", "--catalog", str(tmp_path / "nope.csv")]) == 1
    assert "ABSENT" in capsys.readouterr().out


def test_a_lifecycle_date_moves_only_from_the_value_the_research_expected(tmp_path):
    """eol_date drops a row once past, so a stale date is an outage: the 2026-08-27 read would
    have moved gemini-2.5-flash-image from Vertex's 2027-03-15 back to 2026-10-02."""
    path = _catalog(tmp_path, [
        {"model_id": "gemini-2.5-flash-image", "eol_date": "2027-03-15"},
        {"model_id": "gpt-4.1-nano", "eol_date": "2026-12-11"},
        {"model_id": "new-row", "eol_date": ""},
    ])
    gt = _gt([])
    gt["lifecycle"] = [
        {"model_id": "gemini-2.5-flash-image", "eol_date": "2026-10-02"},
        {"model_id": "gpt-4.1-nano", "eol_date": "2026-10-23", "old_eol": "2026-12-11"},
        {"model_id": "new-row", "eol_date": "2027-01-01"},
    ]
    changes, refusals = applier.apply_to(path, gt, write=True)
    rows = _rows(path)
    assert rows["gemini-2.5-flash-image"]["eol_date"] == "2027-03-15"
    assert refusals == ["gemini-2.5-flash-image: eol expected unset, catalog holds 2027-03-15 -- research is stale for this row"]
    assert rows["gpt-4.1-nano"]["eol_date"] == "2026-10-23" and rows["new-row"]["eol_date"] == "2027-01-01"
