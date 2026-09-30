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


def test_apply_rewrites_only_the_changed_record_and_keeps_every_other_byte(tmp_path):
    """A DictWriter rewrite padded each `#` comment with trailing commas and re-serialised every
    row it did not change: one price change was a 22-line diff over 11 records (2026-09-30)."""
    path = tmp_path / "kitesforu-workers" / "config" / "model_catalog.csv"
    path.parent.mkdir(parents=True)
    header = ",".join(_COLS + ["notes"])
    before = (
        f"{header}\n"
        "eleven_v3,ELEVENLABS,100.00,per 1M characters,,2026-08-27,https://x,\"v3, expressive\"\n"
        "# a comment, with a comma, kept as written\n"
        "veo-3.1-lite-generate-001,GOOGLE,0.05,per second,,2026-09-07,https://y,\"0.05/sec, with audio\"\n"
    )
    before = before.replace("\n", "\r\n")  # the real catalog is CRLF: every line ending must survive
    path.write_bytes(before.encode())
    gt = _gt([{"model_id": "eleven_v3", "old": 100.0, "new": 80.0, "unit": "per 1M characters",
               "source": _EL, "read_date": "2026-09-30", "why_we_were_wrong": "Provider price cut."}])
    changes, refusals = applier.apply_to(path, gt, write=True)
    assert refusals == [] and len(changes) == 1
    after = path.read_bytes().decode().splitlines(keepends=True)
    kept = before.splitlines(keepends=True)
    assert [after[0], after[2], after[3]] == [kept[0], kept[2], kept[3]], "only the eleven_v3 record may change"
    row = _rows(path)["eleven_v3"]
    assert row["cost_per_unit"] == "80.00"
    assert row["notes"].startswith("v3, expressive || 2026-09-30 PRICE CORRECTED 100.0 -> 80.0 (per 1M characters): Provider price cut.")


def test_a_field_holding_a_unicode_line_separator_survives_a_rewrite(tmp_path):
    """str.splitlines breaks on \\u2028; csv does not, and writes such a field UNQUOTED. The touched
    record was cut in two and its tail survived as a stray record (#178 critic)."""
    path = tmp_path / "kitesforu-workers" / "config" / "model_catalog.csv"
    path.parent.mkdir(parents=True)
    header = ",".join(_COLS + ["notes"])
    body = f"{header}\r\neleven_v3,ELEVENLABS,100.00,per 1M characters,,2026-08-27,https://x,a\u2028b\r\n"
    path.write_bytes(body.encode())
    gt = _gt([{"model_id": "eleven_v3", "old": 100.0, "new": 80.0, "unit": "per 1M characters",
               "source": _EL, "read_date": "2026-09-30"}])
    changes, refusals = applier.apply_to(path, gt, write=True)
    assert refusals == [] and len(changes) == 1
    rows = list(csv.reader(path.open(newline="")))
    assert len(rows) == 2 and len(rows[1]) == len(rows[0])
    assert rows[1][2] == "80.00" and rows[1][6] == _EL
    # the cut tail ("b") re-attaches after the stamp when the record is split: check the whole field
    assert rows[1][7].startswith("a\u2028b || 2026-09-30 PRICE CORRECTED") and rows[1][7].endswith(f"Source: {_EL}")


def test_a_provenance_stamp_never_moves_backwards(tmp_path):
    """The stamp is written only when some row changes, so another row changes in the same run."""
    path = _catalog(tmp_path, [
        {"model_id": "eleven_v3", "cost_per_unit": "80.00", "unit_description": "per 1M characters",
         "price_verified": "2026-09-30", "price_source_url": "https://newer"},
        {"model_id": "veo-3.1-lite-generate-001", "cost_per_unit": "0.05", "unit_description": "per second"},
    ])
    gt = _gt([{"model_id": "veo-3.1-lite-generate-001", "old": 0.05, "new": 0.03, "unit": "per second",
               "source": "https://cloud.google.com/vertex-ai/generative-ai/pricing", "read_date": "2026-09-30"}],
             [{"model_id": "eleven_v3", "value": 80.0, "unit": "per 1M characters",
               "source": "https://older", "read_date": "2026-08-27"}])
    applier.apply_to(path, gt, write=True)
    row = _rows(path)["eleven_v3"]
    assert (row["price_verified"], row["price_source_url"]) == ("2026-09-30", "https://newer")


def test_a_sub_cent_price_keeps_its_digits_and_reruns_clean(tmp_path):
    path = _catalog(tmp_path, [{"model_id": "flux-schnell", "cost_per_unit": "0.003",
                                "unit_description": "per image"}])
    gt = _gt([{"model_id": "flux-schnell", "old": 0.003, "new": 0.0025, "unit": "per image",
               "source": "https://fal.ai/pricing", "read_date": "2026-09-30"}])
    changes, refusals = applier.apply_to(path, gt, write=True)
    assert refusals == [] and _rows(path)["flux-schnell"]["cost_per_unit"] == "0.0025"
    assert applier.apply_to(path, gt, write=True) == ([], [])
    assert applier._price_text(40.0) == "40.00" and applier._price_text(0.0336) == "0.0336"


def test_a_duplicated_model_id_is_refused_and_left_untouched(tmp_path):
    path = _catalog(tmp_path, [
        {"model_id": "eleven_v3", "cost_per_unit": "100.00", "unit_description": "per 1M characters"},
        {"model_id": "eleven_v3", "cost_per_unit": "90.00", "unit_description": "per 1M characters"},
    ])
    gt = _gt([{"model_id": "eleven_v3", "old": 100.0, "new": 80.0, "unit": "per 1M characters",
               "source": _EL, "read_date": "2026-09-30"}])
    before = path.read_bytes()
    changes, refusals = applier.apply_to(path, gt, write=True)
    assert changes == [] and refusals == ["eleven_v3: appears 2 times in the catalog -- refusing to write it"]
    assert path.read_bytes() == before


def test_two_reads_on_the_newest_date_are_refused_but_an_older_tie_is_history(tmp_path):
    path = _catalog(tmp_path, [{"model_id": "eleven_flash_v2_5", "cost_per_unit": "50.00",
                                "unit_description": "per 1M characters"}])
    tie = [dict(_CHAIN[1]), dict(_CHAIN[1], new=35.0)]
    _, refusals = applier.apply_to(path, _gt(tie), write=False)
    assert refusals == ["eleven_flash_v2_5: two reads dated 2026-09-30 -- the newest read is ambiguous"]
    older_tie = [dict(_CHAIN[0]), dict(_CHAIN[0], new=49.0), _CHAIN[1]]
    changes, refusals = applier.apply_to(path, _gt(older_tie), write=False)
    assert refusals == [] and changes == ["eleven_flash_v2_5: 50.0 -> 40.0 (per 1M characters)"]


def test_a_lifecycle_entry_for_an_absent_model_is_refused_not_skipped(tmp_path):
    path = _catalog(tmp_path, [{"model_id": "gpt-4.1-nano", "eol_date": ""}])
    gt = _gt([])
    gt["lifecycle"] = [{"model_id": "gpt-9-nope", "eol_date": "2027-01-01"}]
    assert applier.apply_to(path, gt, write=False)[1] == ["gpt-9-nope: not in kitesforu-workers"]
