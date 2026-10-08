"""create_verification_job.sh, end to end, with NOTHING sent: kitesforu-qa #175 round 4.

Every run here is either ``--dry-run`` (exits before any request) or the sending path with ``curl``
replaced by a stub on PATH. ``gcloud``, ``wget`` and ``nc`` are stubs that log the call and exit 99,
``sleep`` returns at once, ``API_BASE`` and the proxies point at a dead port, and ``TEST_API_KEY``
is a dummy, so Secret Manager is never asked. The script under test is a COPY whose one changed line
is ``ACK_FILE`` (asserted), pointed at a temp file. The real founder ACK file is never read or
touched.

A purchase is priced by the workers selector and catalog. The ``workers_src`` fixture extracts
kitesforu-workers ``origin/main`` (``src/workers`` + ``config/model_catalog.csv``) once per session.
Those tests SKIP only when no workers checkout is found. When the checkout imports but prices
wrongly, they FAIL.

Offline and $0.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pytest

QA = Path(__file__).resolve().parents[1]
SCRIPTS = QA / "scripts"
SCRIPT = SCRIPTS / "create_verification_job.sh"
_WORKSPACE_DEFAULT = Path("/Users/vikrantbhosale/gitprojects/kitesforu/kitesforu-workers")
DEAD = "http://127.0.0.1:9"

sys.path.insert(0, str(SCRIPTS))
import verification_job  # noqa: E402  (stdlib-only at import)


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------

def _workers_repo() -> Optional[Path]:
    for cand in (os.environ.get("KFU_WORKERS_REPO"), QA.parent / "kitesforu-workers", _WORKSPACE_DEFAULT):
        if cand and (Path(cand) / ".git").exists():
            return Path(cand)
    return None


@pytest.fixture(scope="session")
def workers_src(tmp_path_factory) -> str:
    explicit = os.environ.get("WORKERS_SRC")
    if explicit:
        return explicit
    repo = _workers_repo()
    if repo is None:
        pytest.skip("no kitesforu-workers checkout (set KFU_WORKERS_REPO or WORKERS_SRC): SKIPPED, not "
                    "passed. A purchase cannot be priced without the producer.")
    tree = tmp_path_factory.mktemp("workers-origin-main")
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "origin/main^{commit}"],
                         check=True, capture_output=True, text=True).stdout.strip()
    archive = subprocess.run(["git", "-C", str(repo), "archive", sha, "--", "src/workers",
                              "config/model_catalog.csv"], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
    assert (tree / "config" / "model_catalog.csv").stat().st_size > 10_000, "the extracted catalog is empty"
    return str(tree / "src")


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path)


class Harness:
    """A scratch copy of the script plus stubbed network binaries."""

    def __init__(self, root: Path):
        self.root = root
        self.bin = root / "bin"
        self.bin.mkdir()
        self.log = root / "stub.log"
        self.seq = root / "status.seq"
        self.pos = root / "status.pos"
        self.ack = root / "FOUNDER_SPEND_ACK"
        self.copy = root / "scripts"
        self.copy.mkdir()
        shutil.copy2(SCRIPTS / "verification_job.py", self.copy / "verification_job.py")
        (root / "src" / "kitesforu_qa").mkdir(parents=True)
        shutil.copy2(QA / "src" / "kitesforu_qa" / "job_status.py", root / "src" / "kitesforu_qa" / "job_status.py")
        original = SCRIPT.read_text()
        rewritten = re.sub(r'^ACK_FILE=".*"$', f'ACK_FILE="{self.ack}"', original, flags=re.M)
        changed = [a for a, b in zip(original.splitlines(), rewritten.splitlines()) if a != b]
        assert len(changed) == 1 and changed[0].startswith("ACK_FILE="), changed
        self.script = self.copy / "create_verification_job.sh"
        self.script.write_text(rewritten)
        self._stub("curl", CURL_STUB)
        for name in ("gcloud", "wget", "nc"):
            self._stub(name, f'#!/bin/bash\necho "{name} $*" >> "$STUB_LOG"\nexit 99\n')
        self._stub("sleep", '#!/bin/bash\necho "sleep $*" >> "$STUB_LOG"\nexit 0\n')
        self.statuses([])

    def _stub(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def statuses(self, lines: Sequence[str]) -> None:
        """What successive GET /status calls return: a JSON body, or EXIT<n> for a transport failure."""
        self.seq.write_text("".join(f"{line}\n" for line in lines))
        self.pos.write_text("0")

    def fresh_ack(self) -> None:
        self.ack.write_text("")
        os.utime(self.ack, None)

    def run(self, *args: str, env: Optional[Dict[str, str]] = None, timeout: int = 180) -> subprocess.CompletedProcess:
        full = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": os.environ.get("HOME", ""),
            "TMPDIR": str(self.root),
            "API_BASE": DEAD, "HTTPS_PROXY": DEAD, "HTTP_PROXY": DEAD,
            "GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent", "CLOUDSDK_CONFIG": "/nonexistent",
            "TEST_API_KEY": "dummy-test-key",
            "STUB_LOG": str(self.log), "STUB_STATUS_SEQ": str(self.seq), "STUB_SEQ_POS": str(self.pos),
            "STUB_POST_RESPONSE": '{"job_id": "job_test_1"}',
        }
        full.update(env or {})
        return subprocess.run(["/bin/bash", str(self.script), *args], env=full, capture_output=True,
                              text=True, timeout=timeout)

    def calls(self, prefix: str = "") -> List[List[str]]:
        if not self.log.exists():
            return []
        out = []
        for line in self.log.read_text().splitlines():
            parts = line.split("\x1f") if "\x1f" in line else line.split(" ")
            if parts and parts[0].startswith(prefix):
                out.append(parts)
        return out

    def gets(self) -> int:
        return sum(1 for c in self.calls("curl") if "POST" not in c)


CURL_STUB = r"""#!/bin/bash
{ printf 'curl'; for a in "$@"; do printf '\x1f%s' "$a"; done; printf '\n'; } >> "$STUB_LOG"
for a in "$@"; do
  if [[ "$a" == "POST" ]]; then
    [[ -n "${STUB_POST_EXIT:-}" ]] && exit "$STUB_POST_EXIT"
    printf '%s' "$STUB_POST_RESPONSE"; exit 0
  fi
done
[[ -n "${STUB_GET_DELAY:-}" ]] && /bin/sleep "$STUB_GET_DELAY"
n=$(cat "$STUB_SEQ_POS"); total=$(wc -l < "$STUB_STATUS_SEQ" | tr -d ' ')
(( total == 0 )) && exit 7
idx=$(( n < total ? n + 1 : total ))
echo $(( n + 1 )) > "$STUB_SEQ_POS"
line=$(sed -n "${idx}p" "$STUB_STATUS_SEQ")
if [[ "$line" == EXIT* ]]; then exit "${line#EXIT}"; fi
printf '%s' "$line"
"""


def snap(status: str, *, wants_visuals: bool = False, video: bool = False, hero: int = 0,
         visual_status: Optional[str] = None, quality_tier: str = "low",
         visual_options: Optional[dict] = None) -> str:
    doc: dict = {"status": status, "wants_visuals": wants_visuals, "quality_tier": quality_tier,
                 "inputs": {"quality_tier": quality_tier}}
    if visual_options is not None:
        doc["inputs"]["visual_options"] = visual_options
    if wants_visuals and (visual_status or video or hero):
        clips = [{"beat_index": i, "modality": "video_hero"} for i in range(hero)]
        clips.append({"beat_index": 99, "modality": "scene_image"})
        doc["visual"] = {"status": visual_status or "done", "clips": clips,
                         **({"video_url": "gs://b/v.mp4"} if video else {})}
        if video:
            doc["video_url"] = "https://storage.googleapis.com/b/v.mp4"
    return json.dumps(doc)


PAID_VO = {"real_images": True, "max_images": 3, "motion_clips": 2}


# ---------------------------------------------------------------------------------------------
# The default requests did not move
# ---------------------------------------------------------------------------------------------

EXPECTED_DEFAULT_BODY = (
    '{"topic": "pipeline verification", "duration_min": 0.167, "style": "Explainer", '
    '"quality_tier": "low", "economy_mode": true, "intro_enabled": false, "allow_premium": false, '
    '"skip_clarifier": true, "language": "en-US", "wants_visuals": false, "visuals_opt_out": true}'
)


def test_the_default_t3_request_is_byte_identical_and_reads_no_repo(harness):
    r = harness.run("--dry-run", env={"KFU_WORKERS_REPO": "/nonexistent"})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == EXPECTED_DEFAULT_BODY
    assert "est=~$0.025" in r.stderr
    assert harness.calls() == [], "a dry run called a network binary"


# ---------------------------------------------------------------------------------------------
# FIX NOW: the estimate is computed FROM the payload
# ---------------------------------------------------------------------------------------------

def _est(stderr: str) -> str:
    m = re.search(r"^Creating verification job: .* est=(.*)$", stderr, flags=re.M)
    assert m, stderr
    return m.group(1)


@pytest.mark.parametrize("tier", ["low", "medium", "high"])
def test_the_estimate_counts_exactly_what_the_body_buys(harness, workers_src, tier):
    r = harness.run("--dry-run", "--tier", tier, "--duration", "2.0", "--motion-clips", "2",
                    env={"WORKERS_SRC": workers_src})
    assert r.returncode == 0, r.stderr
    vo = json.loads(r.stdout)["visual_options"]
    est = _est(r.stderr)
    assert f"{vo['motion_clips']} paid clip(s)" in est
    assert f"up to {vo['max_images']} paid still(s)" in est
    assert f"paid_stills={vo['max_images']} " in r.stderr and f"motion_clips={vo['motion_clips']} " in r.stderr
    # The money is the per-unit rows times what the body orders, and the total is the sum.
    cells = _clip_cells(r.stderr)
    clip_lo, clip_hi = min(cells.values()), max(cells.values())
    m = re.search(r"(\d) paid clip\(s\) \$([\d.]+)(?:-([\d.]+))?", est)
    assert m and abs(float(m.group(2)) - vo["motion_clips"] * clip_lo) < 0.006
    assert abs(float(m.group(3) or m.group(2)) - vo["motion_clips"] * clip_hi) < 0.006
    lo_still, hi_still = _still_rows(r.stderr)
    m = re.search(r"up to (\d) paid still\(s\) \$([\d.]+)-([\d.]+)", est)
    assert m and abs(float(m.group(2)) - vo["max_images"] * lo_still) < 0.0006
    assert abs(float(m.group(3)) - vo["max_images"] * hi_still * 2) < 0.006


def _clip_cells(stderr: str) -> Dict[str, float]:
    cells = {}
    for line in stderr.splitlines():
        for shape, model, secs, usd in re.findall(r"(plain|anchored) (\S+) (\d+)s \$([\d.]+)", line):
            cells[f"{'fal' if 'fal bound' in line else 'nofal'}:{shape}:{model}:{secs}"] = float(usd)
    assert len(cells) == 4, f"expected 4 priced clip cells, got {cells}\n{stderr}"
    return cells


def _still_rows(stderr: str):
    m = re.search(r"stills: \d+ enabled IMAGE rows, (\S+) \$([\d.]+) x1 \.\. (\S+) \$([\d.]+) x2", stderr)
    assert m, stderr
    return float(m.group(2)), float(m.group(4))


def test_the_estimate_moves_when_the_order_moves(harness, workers_src):
    on = harness.run("--dry-run", "--motion-clips", "1", env={"WORKERS_SRC": workers_src})
    off = harness.run("--dry-run", "--motion-clips", "1", "--paid-stills", "off", env={"WORKERS_SRC": workers_src})
    assert on.returncode == 0 and off.returncode == 0, (on.stderr, off.stderr)
    assert json.loads(off.stdout)["visual_options"] == {"real_images": False, "max_images": 0, "motion_clips": 1}
    assert _est(on.stderr) != _est(off.stderr), "the estimate did not move when the body stopped buying stills"
    assert "paid still" not in _est(off.stderr) and "paid_stills=" not in off.stderr
    assert "up to 3 paid still(s)" in _est(on.stderr)


def test_stills_are_counted_once_alongside_legacy_visuals(harness, workers_src):
    r = harness.run("--dry-run", "--tier", "medium", "--visuals", "--motion-clips", "1",
                    env={"WORKERS_SRC": workers_src})
    assert r.returncode == 0, r.stderr
    est = _est(r.stderr)
    assert json.loads(r.stdout)["visual_options"]["max_images"] == 4
    assert est.count("paid still(s)") == 1
    assert "visuals (~$0.10-0.50" not in est, "the legacy visuals band double-books the stills visual_options buys"


# ---------------------------------------------------------------------------------------------
# Addition 1: a clip and a still are priced from the live catalog, rows named
# ---------------------------------------------------------------------------------------------

def _catalog_rows(tree_src: str) -> Dict[str, dict]:
    import csv
    path = Path(tree_src).parent / "config" / "model_catalog.csv"
    with path.open() as f:
        return {r["model_id"].strip(): r for r in csv.DictReader(f) if not r["model_id"].startswith("#")}


def _edit_row(text: str, model_id: str, **changes: str) -> str:
    """Rewrite ONE catalog line, parsed and re-serialized by the csv module, every other line
    byte-identical. With ``as_new_id`` the edited copy is APPENDED and the original kept."""
    import csv
    import io
    lines = text.splitlines(keepends=True)
    header = next(csv.reader([lines[0]]))
    as_new = changes.pop("as_new_id", None)
    for i, line in enumerate(lines):
        if line.startswith(f"{model_id},"):
            cells = next(csv.reader([line]))
            for col, value in changes.items():
                cells[header.index(col)] = value
            if as_new:
                cells[header.index("model_id")] = as_new
                cells[header.index("name")] = as_new
            buf = io.StringIO()
            csv.writer(buf, lineterminator="\n").writerow(cells)
            if as_new:
                return "".join(lines).rstrip("\n") + "\n" + buf.getvalue()
            lines[i] = buf.getvalue()
            return "".join(lines)
    raise AssertionError(f"catalog row {model_id} not found: the row this test edits moved")


def test_every_priced_clip_cell_is_a_catalog_row_times_its_seconds(harness, workers_src):
    r = harness.run("--dry-run", "--motion-clips", "2", env={"WORKERS_SRC": workers_src})
    assert r.returncode == 0, r.stderr
    rows = _catalog_rows(workers_src)
    for key, usd in _clip_cells(r.stderr).items():
        _, _, model, secs = key.split(":")
        row = rows[model]
        assert row["unit_description"].strip() == "per second" and row["enabled"].strip() == "true"
        assert abs(float(row["cost_per_unit"]) * int(secs) - usd) < 0.0006, (key, row["cost_per_unit"])
    # The arm's budget is the producer's own constants, read from its policy.py, never a copy.
    policy = (Path(workers_src) / "workers" / "stages" / "visuals" / "policy.py").read_text()
    cap_m = re.search(r"^_MOTION_CLIP_USD_CAP = ([\d.]+)", policy, flags=re.M)
    secs_m = re.search(r"^_MOTION_CLIP_SECONDS = (\d+)", policy, flags=re.M)
    assert cap_m and secs_m, "the producer's purchased-arm constants moved"
    cap, secs = float(cap_m.group(1)), int(secs_m.group(1))
    assert f"purchased arm (cap ${cap:.2f}, {secs}s)" in r.stderr
    assert all(usd <= cap + 1e-9 for usd in _clip_cells(r.stderr).values()), "a purchased clip above the cap"
    assert "priced from WORKERS_SRC=" in r.stderr


def _tree_with_catalog(tmp_path: Path, workers_src: str, edit) -> str:
    """A tree whose src is the real one (symlinked) and whose catalog is an edited copy. The workers
    loaders find the CSV relative to the module path they were imported from."""
    tree = tmp_path / "edited-tree"
    (tree / "config").mkdir(parents=True)
    os.symlink(workers_src, tree / "src")
    text = (Path(workers_src).parent / "config" / "model_catalog.csv").read_text()
    (tree / "config" / "model_catalog.csv").write_text(edit(text))
    return str(tree / "src")


@pytest.mark.parametrize("cell", ["cheapest", "fal-plain"])
def test_a_catalog_price_edit_moves_the_estimate(harness, workers_src, tmp_path, cell):
    """Cut one winning row's catalog price by a cent. Its cell must follow the catalog. When it is
    the CHEAPEST cell, the per-clip range in the estimate line moves too."""
    base = harness.run("--dry-run", "--motion-clips", "1", env={"WORKERS_SRC": workers_src})
    assert base.returncode == 0, base.stderr
    cells = _clip_cells(base.stderr)
    key = (min(cells, key=lambda k: cells[k]) if cell == "cheapest"
           else next(k for k in cells if k.startswith("fal:plain:")))
    model, secs = key.split(":")[2], int(key.split(":")[3])
    old = float(_catalog_rows(workers_src)[model]["cost_per_unit"])
    new = round(old - 0.01, 4)
    assert new > 0
    edited = _tree_with_catalog(tmp_path, workers_src,
                                lambda text: _edit_row(text, model, cost_per_unit=str(new)))
    moved = harness.run("--dry-run", "--motion-clips", "1", env={"WORKERS_SRC": edited})
    assert moved.returncode == 0, moved.stderr
    assert cells[key] == pytest.approx(old * secs, abs=0.0006)
    assert _clip_cells(moved.stderr)[key] == pytest.approx(new * secs, abs=0.0006), \
        "the clip price did not follow the catalog row"
    if cell == "cheapest":
        assert _est(base.stderr) != _est(moved.stderr), "the estimate's per-clip range ignored the catalog"


def test_stills_are_priced_from_enabled_unretired_image_rows_only(harness, workers_src, tmp_path):
    import datetime
    today = datetime.date.today().isoformat()
    rows = _catalog_rows(workers_src)
    usable = {m: float(r["cost_per_unit"]) for m, r in rows.items()
              if "IMAGE" in r["task_types"].upper() and r["enabled"].strip() == "true"
              and not (r["eol_date"].strip() and r["eol_date"].strip() <= today)}
    r = harness.run("--dry-run", "--motion-clips", "1", env={"WORKERS_SRC": workers_src})
    assert r.returncode == 0, r.stderr
    lo, hi = _still_rows(r.stderr)
    assert lo == min(usable.values()) and hi == max(usable.values())

    # Copies of a REAL enabled image row, so the producer's parser certainly keeps them, priced far
    # above every usable row, one disabled and one retired. Neither may price a still.
    donor = max(usable, key=lambda m: usable[m])

    def add_rows(text: str) -> str:
        text = _edit_row(text, donor, as_new_id="test-disabled-dear-image", cost_per_unit="0.90", enabled="false")
        return _edit_row(text, donor, as_new_id="test-retired-dear-image", cost_per_unit="0.80",
                         eol_date="2020-01-01")
    edited = _tree_with_catalog(tmp_path, workers_src, add_rows)
    r2 = harness.run("--dry-run", "--motion-clips", "1", env={"WORKERS_SRC": edited})
    assert r2.returncode == 0, r2.stderr
    assert _still_rows(r2.stderr) == (lo, hi), "a disabled or retired row priced a still"
    # Positive control: the same copy ENABLED and unretired does reach the still price.
    live = _tree_with_catalog(tmp_path / "control", workers_src,
                              lambda text: _edit_row(text, donor, as_new_id="test-live-dear-image",
                                                     cost_per_unit="0.70"))
    r3 = harness.run("--dry-run", "--motion-clips", "1", env={"WORKERS_SRC": live})
    assert r3.returncode == 0, r3.stderr
    assert _still_rows(r3.stderr)[1] == 0.70, "the control row never reached the pricer, so this test proves nothing"


def test_the_still_quote_drops_disabled_and_retired_rows_itself():
    """The producer's loader serves rows through the routing cache, whose floor already drops disabled
    and retired rows, so the end-to-end test above cannot tell whether this module filters too. It
    must: when that cache fails, ``iter_model_rows`` falls back to the RAW CSV with every row in it
    (workers ``common/pricing.py``). These rows are that fallback's shape. The helpers mirror the
    producer's ``_truthy`` and ``is_past_eol``; the real ones run in the end-to-end test."""
    rows = {
        "flux-schnell": {"model_id": "flux-schnell", "task_types": "IMAGE", "enabled": "true",
                         "cost_per_unit": 0.003, "unit_description": "per image", "eol_date": ""},
        "pro-image": {"model_id": "pro-image", "task_types": "IMAGE", "enabled": "true",
                      "cost_per_unit": 0.134, "unit_description": "per image", "eol_date": "2099-01-01"},
        "disabled-dear": {"model_id": "disabled-dear", "task_types": "IMAGE", "enabled": "false",
                          "cost_per_unit": 0.90, "unit_description": "per image", "eol_date": ""},
        "retired-dear": {"model_id": "retired-dear", "task_types": "IMAGE", "enabled": "true",
                         "cost_per_unit": 0.80, "unit_description": "per image", "eol_date": "2020-01-01"},
        "megapixel-dear": {"model_id": "megapixel-dear", "task_types": "IMAGE", "enabled": "true",
                           "cost_per_unit": 0.70, "unit_description": "per megapixel", "eol_date": ""},
        "an-llm": {"model_id": "an-llm", "task_types": "LLM", "enabled": "true",
                   "cost_per_unit": 5.0, "unit_description": "per 1M tokens", "eol_date": ""},
    }
    w = {"iter_model_rows": lambda: rows,
         "truthy": lambda v: str(v).strip().lower() in {"true", "1", "yes"},
         "is_past_eol": lambda row: bool(row.get("eol_date")) and row["eol_date"] <= "2026-10-08"}
    quote = verification_job.quote_paid_stills(w)
    assert quote.cheapest == ("flux-schnell", 0.003)
    assert quote.dearest == ("pro-image", 0.134), "a disabled, retired or non-per-image row priced a still"
    assert quote.high == pytest.approx(0.268)


def test_a_purchase_it_cannot_price_is_refused_before_anything_is_sent(harness, tmp_path):
    empty = tmp_path / "no-workers" / "src"
    empty.mkdir(parents=True)
    harness.fresh_ack()
    r = harness.run("--motion-clips", "1", env={"WORKERS_SRC": str(empty)})
    assert r.returncode == 1 and "Refusing" in r.stderr, r.stderr
    assert r.stdout == "" and harness.calls() == []


def test_without_workers_src_the_script_prices_from_an_origin_main_archive_and_cleans_up(harness):
    repo = _workers_repo()
    if repo is None:
        pytest.skip("no kitesforu-workers checkout: SKIPPED, not passed")
    r = harness.run("--dry-run", "--motion-clips", "1", env={"KFU_WORKERS_REPO": str(repo)})
    assert r.returncode == 0, r.stderr
    assert re.search(r"priced from kitesforu-workers origin/main [0-9a-f]{12} \(\d{4}-\d\d-\d\d\)", r.stderr)
    assert not list(harness.root.glob("kfu-verify-workers.*")), "the temporary workers tree was left behind"


# ---------------------------------------------------------------------------------------------
# FIX NOW (pinned at 9d334fc): the money flag fails CLOSED
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["false", "0", "", "no", "none", "of", " off"])
def test_a_misspelled_paid_stills_value_is_refused(harness, value):
    r = harness.run("--dry-run", "--motion-clips", "1", "--paid-stills", value)
    assert r.returncode == 1 and "must be 'on' or 'off'" in r.stderr, (value, r.stderr)
    assert r.stdout == ""


def test_paid_stills_without_clips_is_refused(harness):
    r = harness.run("--dry-run", "--paid-stills", "off")
    assert r.returncode == 1 and "only applies with --motion-clips" in r.stderr


def test_paid_stills_alone_require_the_ack():
    body = {"quality_tier": "low", "duration_min": 0.167,
            "visual_options": {"real_images": True, "max_images": 3, "motion_clips": 0}}
    needs_ack, reason = verification_job.ack_decision(body)
    assert needs_ack and reason == "paid_stills=3 "


def test_a_t3_body_needs_no_ack():
    assert verification_job.ack_decision(json.loads(EXPECTED_DEFAULT_BODY)) == (False, "")


# ---------------------------------------------------------------------------------------------
# FIX NOW: the wait
# ---------------------------------------------------------------------------------------------

def test_a_transport_failure_is_a_probe_failure_not_the_end_of_the_wait(harness):
    harness.statuses([snap("running"), "EXIT28", snap("completed")])
    r = harness.run("--wait")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "probe failure 1/3" in r.stdout and "transport failure" in r.stdout
    assert "Final status: completed" in r.stdout


def test_three_unreadable_probes_abort_with_exit_3(harness):
    harness.statuses(["EXIT28", "EXIT7", "<html>502</html>"])
    r = harness.run("--wait")
    assert r.returncode == 3 and "ABORTING" in r.stderr


@pytest.mark.parametrize("status,what", [("needs_review", "held for human review"),
                                         ("failed_qa", "failed the automatic QA gate"),
                                         ("completed", "finished")])
def test_a_finished_episode_is_terminal_and_gradeable(harness, status, what):
    harness.statuses([snap("running"), snap(status)])
    r = harness.run("--wait")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert f"Final status: {status} — finished" in r.stdout and what in r.stdout
    assert harness.gets() == 2


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_an_ended_job_is_exit_5_and_never_invites_grading(harness, status):
    harness.statuses([snap("running"), snap(status)])
    r = harness.run("--wait")
    assert r.returncode == 5, (r.returncode, r.stdout, r.stderr)
    assert "nothing to grade" in r.stderr and "Grade it" not in r.stdout


def test_running_out_of_time_is_exit_4_not_a_result(harness):
    harness.statuses([snap("running")])
    r = harness.run("--wait")
    assert r.returncode == 4 and "NOT a result" in r.stderr
    assert harness.gets() == 1800 // 15, "the audio-only wait is bounded at its own budget"


def test_the_reported_elapsed_is_wall_clock(harness):
    harness.statuses([snap("running"), snap("completed")])
    started = time.monotonic()
    r = harness.run("--wait", env={"STUB_GET_DELAY": "1.1"})
    wall = time.monotonic() - started
    assert r.returncode == 0, r.stderr
    m = re.search(r"after (\d+)s\.", r.stdout)
    assert m, r.stdout
    assert 2 <= int(m.group(1)) <= wall + 1, "the elapsed time is not the wall clock (2 probes x 1.1 s, sleeps stubbed)"


def test_a_visuals_job_is_gradeable_only_once_its_video_is_assembled(harness, workers_src):
    harness.fresh_ack()
    harness.statuses([
        snap("queued", visual_options=PAID_VO),                                   # the read-back
        snap("running", wants_visuals=True),
        snap("completed", wants_visuals=True, visual_status="done", hero=0),      # audio done, no video
        snap("completed", wants_visuals=True, visual_status="done", video=True, hero=1),
    ])
    r = harness.run("--motion-clips", "2", "--wait", env={"WORKERS_SRC": workers_src})
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert harness.gets() == 4, "the wait stopped on AUDIO completion, before the video existed"
    assert "Only 1 of 2 PURCHASED clip(s)" in r.stderr
    sleeps = [c[1] for c in harness.calls("sleep")]
    assert sleeps[-1] == "60", "once the audio is done the wait should poll once a minute"


def test_a_visuals_job_whose_video_never_comes_times_out_as_not_a_result(harness, workers_src):
    harness.fresh_ack()
    harness.statuses([snap("queued", visual_options=PAID_VO),
                      snap("completed", wants_visuals=True, visual_status="done")])
    r = harness.run("--motion-clips", "2", "--wait", env={"WORKERS_SRC": workers_src})
    assert r.returncode == 4, (r.returncode, r.stderr)
    assert "finished its audio" in r.stderr and "video is not assembled" in r.stderr
    assert harness.gets() - 1 == 5400 // 15, "the visuals wait is bounded at its own budget"


def test_the_visuals_budget_follows_wants_visuals_not_motion_clips(harness):
    """A `--visuals` T4 that buys no clips is a visuals job too. Its video arrives at poll 125, past
    the audio-only bound of 120 polls, and the wait must still be there for it (#175 round-2 cost
    NIT-2: the bound was sized on --motion-clips, so this job got the audio-only bound)."""
    harness.fresh_ack()
    harness.statuses([snap("queued", quality_tier="medium")]
                     + [snap("running", wants_visuals=True)] * 124
                     + [snap("completed", wants_visuals=True, visual_status="done", video=True)])
    r = harness.run("--tier", "medium", "--visuals", "--wait")
    assert r.returncode == 0, (r.returncode, r.stderr[-400:])
    assert harness.gets() == 1 + 125


def test_failed_visuals_on_a_visuals_job_is_exit_5(harness, workers_src):
    harness.fresh_ack()
    harness.statuses([snap("queued", visual_options=PAID_VO),
                      snap("completed", wants_visuals=True, visual_status="failed")])
    r = harness.run("--motion-clips", "2", "--wait", env={"WORKERS_SRC": workers_src})
    assert r.returncode == 5 and "VISUALS FAILED" in r.stderr


# ---------------------------------------------------------------------------------------------
# Addition 2: the free-tier clamp is read back
# ---------------------------------------------------------------------------------------------

def test_a_purchase_the_api_did_not_store_fails_loudly(harness, workers_src):
    harness.fresh_ack()
    harness.statuses([snap("queued")])   # a free subscription: visual_options dropped
    r = harness.run("--motion-clips", "2", env={"WORKERS_SRC": workers_src})
    assert r.returncode == 6, (r.returncode, r.stderr)
    assert "visual_options.motion_clips: requested 2, stored 0" in r.stderr
    assert "visual_options.max_images: requested 3, stored 0" in r.stderr
    assert "job_id=job_test_1" in r.stdout, "the operator must be told the job WAS created"


def test_a_clamped_quality_tier_fails_loudly(harness):
    harness.fresh_ack()
    harness.statuses([snap("queued", quality_tier="medium")])
    r = harness.run("--tier", "high")
    assert r.returncode == 6 and 'quality_tier: requested high, stored "medium"' in r.stderr


def test_a_purchase_the_api_stored_whole_passes_the_read_back(harness, workers_src):
    harness.fresh_ack()
    harness.statuses([snap("queued", visual_options=PAID_VO)])
    r = harness.run("--motion-clips", "2", env={"WORKERS_SRC": workers_src})
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "read back: the api stored the requested" in r.stderr


def test_an_unreadable_read_back_is_exit_7_and_says_the_job_exists(harness, workers_src):
    harness.fresh_ack()
    harness.statuses(["EXIT28", '{"detail": "Invalid authentication credentials"}', "EXIT7"])
    r = harness.run("--motion-clips", "2", env={"WORKERS_SRC": workers_src})
    assert r.returncode == 7 and "WAS created" in r.stderr


def test_a_t3_run_does_no_read_back(harness):
    harness.statuses([snap("queued")])
    r = harness.run()
    assert r.returncode == 0 and harness.gets() == 0


# ---------------------------------------------------------------------------------------------
# The rehearsal is the request
# ---------------------------------------------------------------------------------------------

def test_the_dry_run_prints_the_headers_curl_sends_in_the_same_order(harness):
    obo = ("--on-behalf-of", "user_abc123", "--on-behalf-of-email", "ops team@example.com")
    dry = harness.run("--dry-run", *obo)
    assert dry.returncode == 0, dry.stderr
    printed = re.findall(r"^  -H '(.*)'$", dry.stderr, flags=re.M)
    sent = harness.run(*obo)
    assert sent.returncode == 0, sent.stderr
    post = [c for c in harness.calls("curl") if "POST" in c][0]
    curl_headers = [post[i + 1] for i, a in enumerate(post) if a == "-H"]
    assert [h.split(":")[0] for h in printed] == [h.split(":")[0] for h in curl_headers]
    assert printed[0] == "Authorization: Bearer <TEST_API_KEY redacted>"
    assert printed[1:] == curl_headers[1:]
    assert json.loads(dry.stdout) == json.loads(post[post.index("-d") + 1])


def test_an_email_owner_is_rejected_before_any_owner_line_reassures_about_it(harness):
    r = harness.run("--dry-run", "--on-behalf-of", "founder@example.com")
    assert r.returncode == 2 and "expects a CLERK USER ID" in r.stderr
    assert "OWNER: founder@example.com" not in r.stderr, "the rehearsal reassured about a value it then rejected"


def test_a_non_numeric_duration_is_refused_up_front(harness):
    r = harness.run("--dry-run", "--duration", "abc", "--motion-clips", "1")
    assert r.returncode == 1 and "--duration must be a number" in r.stderr
    assert "0s episode" not in r.stderr and r.stdout == ""


def test_a_post_transport_failure_says_the_job_may_exist(harness):
    r = harness.run(env={"STUB_POST_EXIT": "28"})
    assert r.returncode == 1 and "MAY have been created" in r.stderr


def test_the_dry_run_sends_nothing_and_the_sending_path_is_the_control(harness, workers_src):
    for args in (("--dry-run",), ("--dry-run", "--motion-clips", "3", "--tier", "high"),
                 ("--dry-run", "--on-behalf-of", "user_x", "--short", "--duration", "1.0")):
        r = harness.run(*args, env={"WORKERS_SRC": workers_src})
        assert r.returncode == 0, r.stderr
    assert harness.calls() == [], "a dry run invoked curl, gcloud, wget, nc or sleep"
    harness.run()   # positive control: the same stubs DO record a sending run
    assert len([c for c in harness.calls("curl") if "POST" in c]) == 1


# ---------------------------------------------------------------------------------------------
# --help and the clip warning
# ---------------------------------------------------------------------------------------------

def test_help_documents_what_the_script_does(harness):
    r = harness.run("--help")
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert not lines[0].startswith("#!"), "the shebang is not help"
    text = r.stdout
    for needle in ("--paid-stills", "--on-behalf-of", "--dry-run", "Exit:", "Auth:", "03-money.md"):
        assert needle in text, needle
    assert "test-cost-ladder.md" not in SCRIPT.read_text(), "the script cites a rules file that does not exist"
    caveat = next(i for i, line in enumerate(lines) if "short-form craft" in line)
    assert "--short" in lines[caveat - 1], "the short-form caveat is attached to the wrong example"


@pytest.mark.parametrize("clips,duration,warns", [("1", "0.167", True), ("1", "0.3", False),
                                                  ("2", "0.3", True), ("3", "2.0", False)])
def test_the_clip_warning_fires_when_clips_cover_over_half_the_episode(harness, workers_src, clips, duration, warns):
    r = harness.run("--dry-run", "--motion-clips", clips, "--duration", duration, env={"WORKERS_SRC": workers_src})
    assert r.returncode == 0, r.stderr
    assert ("more than half of it" in r.stderr) is warns, r.stderr


# ---------------------------------------------------------------------------------------------
# The record the cost round needs
# ---------------------------------------------------------------------------------------------

def test_the_cost_changelog_records_this_change():
    text = (QA / "COST_CHANGELOG.md").read_text()
    first = text.split("\n## ", 2)[1]
    assert first.startswith("2026-10-08"), first[:80]
    for needle in ("scripts/create_verification_job.sh", "scripts/verification_job.py",
                   "Pricing-page implication"):
        assert needle in first, needle
