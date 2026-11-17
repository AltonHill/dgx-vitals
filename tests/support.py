"""Hermetic harness for the dgx-vitals / tracker_build suite.

Both tools are loaded by path as fresh modules (never imported by name),
from the repo root or from $DGXV_SRC when set (used to run the suite
against the pre-fix code). Nothing here touches the network:

  * dgx-vitals: `subprocess` on the loaded module is replaced by a stub
    that fails the test if anything tries to execute a command, and the
    two exec helpers are replaced likewise. Only --dry-run (DryRunner)
    and in-process fake runners are ever driven.
  * tracker_build: openpyxl is not a dependency of the suite. A small
    recording stand-in is installed in sys.modules; its Workbook.save()
    writes the sheet's cell values as JSON to the --out path, so tests
    read back exactly what the sheet would contain.

Fixture roots are built in tempdirs from the formats the code parses:
summary.csv (the runner's write_summary columns), <host>/<host>-FULLLOG.txt,
optional <host>/<host>-HEALTHY.png, and extracted cdw-validation bundle dirs.
"""

import contextlib
import getpass as _getpass
import csv
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import types

sys.dont_write_bytecode = True

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.environ.get("DGXV_SRC") or REPO

_counter = [0]
getpass_real = _getpass.getpass


def _load(filename):
    _counter[0] += 1
    path = os.path.join(SRC, filename)
    name = f"dgxv_under_test_{_counter[0]}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# dgx-vitals
# ---------------------------------------------------------------------------

class _NoSubprocess(types.ModuleType):
    """Stand-in for the subprocess module: any use is a test failure."""

    TimeoutExpired = type("TimeoutExpired", (Exception,), {})
    PIPE = -1
    STDOUT = -2

    def __getattr__(self, name):
        def refuse(*a, **k):
            raise AssertionError(
                f"test attempted subprocess.{name}{a!r} — nothing may execute")
        return refuse


def _refuse_exec(*a, **k):
    raise AssertionError(f"test attempted to execute a command: {a!r}")


def load_vitals():
    mod = _load("dgx-vitals.py")
    mod.subprocess = _NoSubprocess("subprocess")
    mod._exec = _refuse_exec
    mod._exec_stream = _refuse_exec
    return mod


def run_vitals_main(mod, argv):
    """Run dgx-vitals main() in-process. Returns (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    old = sys.argv
    sys.argv = ["dgx-vitals.py"] + list(argv)
    rc = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = mod.main()
            except SystemExit as e:
                rc = e.code
    finally:
        sys.argv = old
    return rc, out.getvalue(), err.getvalue()


class FakeRunner:
    """A non-dry runner that records calls and contacts nothing."""

    name = "fake"
    dry = False

    def __init__(self, rc=0, out="ok"):
        self.calls = []
        self.rc, self.out = rc, out

    def run(self, argv, timeout, sudo=False, sudo_password=None, stream=None,
            track=False):
        self.calls.append(list(argv))
        return self.rc, self.out, 0.0

    def fetch(self, remote_path, dest_path):
        raise OSError("fake runner: no fetch")


def snapshot(root):
    """{relative path: bytes} for every file under root."""
    snap = {}
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            with open(p, "rb") as f:
                snap[os.path.relpath(p, root)] = f.read()
    return snap


# ---------------------------------------------------------------------------
# openpyxl stand-in
# ---------------------------------------------------------------------------

def _col_letter(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class _Cell:
    def __init__(self, row, col):
        self.row, self.column = row, col
        self.value = None
        self.font = self.fill = self.alignment = None

    @property
    def coordinate(self):
        return f"{_col_letter(self.column)}{self.row}"


class _Dims(dict):
    def __missing__(self, key):
        v = types.SimpleNamespace(height=None, width=None)
        self[key] = v
        return v


class _Sheet:
    def __init__(self, title):
        self.title = title
        self.cells = {}
        self.max_row = 0
        self.images = []
        self.sheet_properties = types.SimpleNamespace(tabColor=None)
        self.row_dimensions = _Dims()
        self.column_dimensions = _Dims()
        self.auto_filter = types.SimpleNamespace(ref=None)
        self.freeze_panes = None

    def cell(self, row, column):
        c = self.cells.get((row, column))
        if c is None:
            c = self.cells[(row, column)] = _Cell(row, column)
            self.max_row = max(self.max_row, row)
        return c

    def append(self, values):
        rn = self.max_row + 1
        self.max_row = rn
        for i, v in enumerate(values, 1):
            self.cell(rn, i).value = v

    def merge_cells(self, **k):
        pass

    def add_image(self, img, anchor):
        self.images.append((anchor, img.path))

    def rows_values(self):
        width = max([c for _r, c in self.cells] or [0])
        return [[(self.cells.get((r, c)).value
                  if (r, c) in self.cells else None)
                 for c in range(1, width + 1)]
                for r in range(1, self.max_row + 1)]


class _Workbook:
    def __init__(self):
        self.sheets = [_Sheet("Sheet")]

    @property
    def active(self):
        return self.sheets[0]

    def create_sheet(self, title):
        s = _Sheet(title)
        self.sheets.append(s)
        return s

    def save(self, path):
        data = {s.title: {"rows": s.rows_values(), "images": s.images,
                          "fills": {f"{r},{c}": getattr(cell.fill,
                                                         "fgColor", None)
                                    for (r, c), cell in s.cells.items()
                                    if cell.fill is not None}}
                for s in self.sheets}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)


class _Image:
    def __init__(self, path):
        self.path = path
        self.width = self.height = None


def install_fake_openpyxl():
    def ns(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        return m

    def kw(*a, **k):
        return types.SimpleNamespace(args=a, **k)

    mods = {
        "openpyxl": ns("openpyxl", Workbook=_Workbook),
        "openpyxl.styles": ns("openpyxl.styles", Alignment=kw, Font=kw,
                              PatternFill=kw),
        "openpyxl.utils": ns("openpyxl.utils", get_column_letter=_col_letter),
        "openpyxl.worksheet": ns("openpyxl.worksheet"),
        "openpyxl.worksheet.table": ns("openpyxl.worksheet.table",
                                       Table=kw, TableStyleInfo=kw),
        "openpyxl.drawing": ns("openpyxl.drawing"),
        "openpyxl.drawing.image": ns("openpyxl.drawing.image", Image=_Image),
    }
    sys.modules.update(mods)


# ---------------------------------------------------------------------------
# tracker_build fixtures
# ---------------------------------------------------------------------------

SUMMARY_HEADER = ["Hostname", "Release", "NVSM Health", "NVIDIA-SMI Query",
                  "NVLink Topology", "NCCL Tests", "NVSM Stress Test",
                  "NVSM Dump Health", "% Complete", "Overall", "Notes"]

DGX_PASS = {"Release": "Complete", "NVSM Health": "Complete",
            "NVIDIA-SMI Query": "Complete", "NVLink Topology": "Complete",
            "NVSM Stress Test": "Complete", "NVSM Dump Health": "Complete"}


def fulllog_text(host, sections=(), dry=False):
    """Minimal FULLLOG in the runner's format: header, then one
    '[k/N] cmd' block per (cmd, output, trailer-state, note)."""
    lines = ["DGX Vitals — FULLLOG" + ("  [DRY RUN]" if dry else ""),
             f"Host: {host}   Mode: ssh   Tool: dgx-vitals.py v1.9",
             "Started: 2026-11-15T10:00:00Z", "=" * 72]
    n = len(sections)
    for i, (cmd, out, state, note) in enumerate(sections, 1):
        lines += [f"\n>>> step {i}/{n}: step — started 2026-11-15T10:00:0{i}Z",
                  f"\n[{i}/{n}] {cmd}", "-" * 72, out,
                  f"[exit 0 · 1.0s · {state}" + (f" · {note}" if note else "")
                  + "]"]
    lines += ["\n" + "=" * 72, "OVERALL: DRY RUN" if dry else "OVERALL: x",
              "Finished: 2026-11-15T11:00:00Z"]
    return "\n".join(lines) + "\n"


# Runner step order and commands per summary column (dgx-vitals
# build_steps / write_summary). Used to give each fixture row the FULLLOG
# a real run would have left beside it.
_RUNNER_ORDER = [("Release", None),
                 ("NVSM Health", "sudo nvsm show health"),
                 ("NVIDIA-SMI Query", "nvidia-smi -q"),
                 ("NVLink Topology", "nvidia-smi topo -m"),
                 ("NCCL Tests", "sh -c 'echo '\"'\"'===== all_reduce_perf "
                                "====='\"'\"'; mpirun --allow-run-as-root "
                                "-np 8 all_reduce_perf -b 8M -e 2G -f 2 -g 1'"),
                 ("NVSM Stress Test", "sudo nvsm stress-test --no-prompt"),
                 ("NVSM Dump Health", "sudo nvsm dump health")]
_TRAILER = {"Complete": "PASS", "Failed": "FAIL", "Skipped": "SKIP",
            "Dry Run": "DRY"}
_DGX_ONLY = ("NVSM Health", "NVSM Stress Test", "NVSM Dump Health")


CMD = {"release": "cat /etc/dgx-release", "os-release": "cat /etc/os-release",
       "health": "sudo nvsm show health", "smi": "nvidia-smi -q",
       "topo": "nvidia-smi topo -m",
       "nccl": "sh -c 'echo '\"'\"'===== all_reduce_perf ====='\"'\"'; "
               "mpirun --allow-run-as-root -np 8 all_reduce_perf -b 8M "
               "-e 2G -f 2 -g 1'",
       "stress": "sudo nvsm stress-test --no-prompt",
       "dump": "sudo nvsm dump health"}


def runner_fulllog(host, sections, overall=None, tool="dgx-vitals.py v1.9",
                   platform="dgx (as requested)", planned=None):
    """A FULLLOG in the runner's exact v1.9 format. sections: list of
    (cmd key or literal command, output, PASS/FAIL/SKIP, note[, exit]).
    overall None: derive it the runner's way; overall False: omit the
    OVERALL/Finished lines (a log cut short). planned: N in the step
    markers (default len(sections)); markers beyond the written sections
    are emitted without a block, as a run cut short mid-step leaves them."""
    n = planned or len(sections)
    lines = ["DGX Vitals — FULLLOG"]
    lines.append(f"Host: {host}   Mode: ssh   Tool: {tool}" if tool
                 else f"Host: {host}   Mode: ssh")
    if platform:
        lines.append(f"Platform: {platform}")
    lines += ["Started: 2026-11-15T10:00:00Z", "=" * 72]
    passed = total = 0
    for i, sec in enumerate(sections, 1):
        cmd, out, state, note = sec[:4]
        rc = sec[4] if len(sec) > 4 else 0
        lines += [f"\n>>> step {i}/{n}: step — started 2026-11-15T10:00:00Z",
                  f"\n[{i}/{n}] {CMD.get(cmd, cmd)}", "-" * 72,
                  out.rstrip(),
                  f"[exit {rc} · 1.0s · {state}" + (f" · {note}" if note
                                                    else "") + "]"]
        passed += state == "PASS"
        total += state in ("PASS", "FAIL")
    for i in range(len(sections) + 1, n + 1):
        lines.append(f"\n>>> step {i}/{n}: step — started "
                     "2026-11-15T11:00:00Z")
    if overall is not False:
        ov = overall or ("HEALTHY" if total and passed == total
                         else "NOT HEALTHY")
        lines += ["\n" + "=" * 72,
                  f"OVERALL: {ov} ({passed}/{total} steps passed)",
                  "Finished: 2026-11-15T11:00:00Z"]
    return "\n".join(lines) + "\n"


DGX_SECTIONS = [("release", 'DGX_NAME="DGX Server"', "PASS",
                 'DGX_NAME="DGX Server"'),
                ("health", "Verify GPUs.......... Healthy\n"
                           "1 out of 1 checks are healthy\n"
                           "0 out of 1 checks are unhealthy", "PASS", ""),
                ("smi", "Attached GPUs : 8", "PASS", "8 GPUs"),
                ("topo", "GPU0 X NV18", "PASS", ""),
                ("stress", "Stress Test Log: /var/x.nvsmlog", "PASS", ""),
                ("dump", "written to: /tmp/nvsm-health-x.tar.xz", "PASS", "")]


def write_root(root, fulllogs, rows=None, csv_age=60):
    """A dgx-vitals root from FULLLOG texts {host: text}; rows (summary
    rows) None: no summary.csv. csv_age: seconds the csv is stamped
    newer (negative: older) than the newest FULLLOG."""
    os.makedirs(root, exist_ok=True)
    newest = 0.0
    for host, text in fulllogs.items():
        hd = os.path.join(root, host)
        os.makedirs(hd, exist_ok=True)
        p = os.path.join(hd, f"{host}-FULLLOG.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        newest = max(newest, os.path.getmtime(p))
    if rows is not None:
        s = os.path.join(root, "summary.csv")
        with open(s, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(SUMMARY_HEADER)
            for r in rows:
                w.writerow([r.get(h, "") for h in SUMMARY_HEADER])
        t = (newest or os.path.getmtime(s)) + csv_age
        os.utime(s, (t, t))
    return root


def fulllog_for_row(row):
    """The FULLLOG a dgx-vitals run leaves for one summary row: one block
    per non-empty step cell, in runner order, and the row's Overall as the
    OVERALL trailer. A row with no step cells (a SKIPPED placeholder) gets
    the minimal step-less log; a DRY RUN row gets a dry log."""
    host = row["Hostname"]
    overall = row.get("Overall", "")
    if overall == "DRY RUN":
        return fulllog_text(host, dry=True)
    cells = [(c, row.get(c, "")) for c, _ in _RUNNER_ORDER
             if row.get(c, "") in _TRAILER]
    if not cells:
        return fulllog_text(host)
    dgx = any(row.get(c) for c in _DGX_ONLY) or not row.get("NCCL Tests")
    lines = ["DGX Vitals — FULLLOG",
             f"Host: {host}   Mode: ssh   Tool: dgx-vitals.py v1.9",
             f"Platform: {'dgx' if dgx else 'hgx'} (as requested)",
             "Started: 2026-11-15T10:00:00Z", "=" * 72]
    n = len(cells)
    cmds = dict(_RUNNER_ORDER)
    cmds["Release"] = "cat /etc/dgx-release" if dgx \
        else "cat /etc/os-release"
    passed = 0
    for i, (col, state) in enumerate(cells, 1):
        out = 'DGX_NAME="DGX Server"' if col == "Release" else "ok"
        note = ' · DGX_NAME="DGX Server"' if col == "Release" \
            and state == "Complete" else ""
        passed += state == "Complete"
        lines += [f"\n>>> step {i}/{n}: {col} — started 2026-11-15T10:00:0{i % 10}Z",
                  f"\n[{i}/{n}] {cmds[col]}", "-" * 72, out,
                  f"[exit 0 · 1.0s · {_TRAILER[state]}{note}]"]
    total = sum(1 for _c, st in cells if st in ("Complete", "Failed"))
    lines += ["\n" + "=" * 72,
              f"OVERALL: {overall} ({passed}/{total} steps passed)",
              "Finished: 2026-11-15T11:00:00Z"]
    return "\n".join(lines) + "\n"


def write_vitals_root(root, rows, fulllogs=None, cards=()):
    """rows: list of dicts keyed by SUMMARY_HEADER (missing keys blank).
    fulllogs: {host: text} (None as a value: no FULLLOG for that host);
    default writes the FULLLOG a real run would leave (fulllog_for_row).
    cards: hosts that get a real PNG card (rendered with dgx-vitals).
    As in the runner, per-host artifacts are written first and summary.csv
    last, so the csv is never older than the logs it summarizes."""
    os.makedirs(root, exist_ok=True)
    fulllogs = fulllogs or {}
    vit = None
    for r in rows:
        host = r["Hostname"]
        hd = os.path.join(root, host)
        os.makedirs(hd, exist_ok=True)
        text = fulllogs[host] if host in fulllogs else fulllog_for_row(r)
        if text is not None:
            with open(os.path.join(hd, f"{host}-FULLLOG.txt"), "w",
                      encoding="utf-8") as f:
                f.write(text)
        if host in cards:
            vit = vit or load_vitals()
            vit.render_health_card(
                os.path.join(hd, f"{host}-HEALTHY.png"), host,
                "2026-11-15T11:00:00Z", [("DGX Release", "pass")],
                r.get("Overall", "HEALTHY"), "pass")
    with open(os.path.join(root, "summary.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(SUMMARY_HEADER)
        for r in rows:
            w.writerow([r.get(h, "") for h in SUMMARY_HEADER])
    return root


def vrow(host, overall="HEALTHY", notes="", **cells):
    """A summary.csv row. Keyword cells use underscores for spaces,
    e.g. NCCL_Tests='Failed', NVSM_Stress_Test='Skipped'."""
    r = {"Hostname": host, "Overall": overall, "Notes": notes}
    for k, v in cells.items():
        r[k.replace("_", " ").replace("NVIDIA SMI", "NVIDIA-SMI")] = v
    return r


def full_dgx(host, overall="HEALTHY", notes="", **over):
    """A complete six-step DGX row, overridable per cell."""
    cells = {k.replace(" ", "_").replace("-", "_"): v
             for k, v in DGX_PASS.items()}
    cells.update(over)
    return vrow(host, overall, notes, **cells)


def write_fleet(path, hosts):
    with open(path, "w", newline="") as f:
        f.write("hostname,lan_ip,bmc_ip\n")
        for i, h in enumerate(hosts, 1):
            f.write(f"{h},10.0.0.{i},10.1.0.{i}\n")
    return path


def write_adjudications(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hostname", "step", "state", "note"])
        for r in rows:
            w.writerow(r)
    return path


def write_bundle_root(root, host, summary_lines, health_log=""):
    bdir = os.path.join(root, host, f"cdw-validation-{host}-20261115T1000")
    os.makedirs(bdir, exist_ok=True)
    with open(os.path.join(bdir, "summary.txt"), "w") as f:
        f.write("\n".join(summary_lines) + "\n")
    if health_log:
        with open(os.path.join(bdir, "04-nvsm-health.log"), "w") as f:
            f.write(health_log)
    return root


def run_tracker(args, out_dir, out_name="tracker.xlsx"):
    """Run tracker_build main() in-process with the openpyxl stand-in.
    Returns a Sheet view of what was saved, with .rc (the exit code; None
    for a pre-exit-code tracker whose main() returns nothing) and .csv
    (the rows of the CSV written beside the sheet, [] if none)."""
    install_fake_openpyxl()
    tb = _load("tracker_build.py")
    out = os.path.join(out_dir, out_name)
    stdout, stderr = io.StringIO(), io.StringIO()
    old = sys.argv
    sys.argv = ["tracker_build.py", "--out", out, "--no-banner"] + list(args)
    rc = None
    try:
        with contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            try:
                rc = tb.main()
            except SystemExit as e:
                rc = e.code
    finally:
        sys.argv = old
    data = {"Validation Tracker": {"rows": [], "images": []}}
    if os.path.isfile(out):
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
    csv_rows = []
    csv_path = os.path.splitext(out)[0] + ".csv"
    if os.path.isfile(csv_path):
        with open(csv_path, newline="", encoding="utf-8") as f:
            csv_rows = list(csv.DictReader(f))
    sh = Sheet(data, tb.COLUMNS, stdout.getvalue(), stderr.getvalue())
    sh.rc, sh.csv, sh.tb = rc, csv_rows, tb
    return sh


class Sheet:
    def __init__(self, data, columns, stdout, stderr):
        self.data, self.columns = data, columns
        self.stdout, self.stderr = stdout, stderr
        rows = data["Validation Tracker"]["rows"]
        self.rows = {}
        self.footer = []
        self.quarantine = []
        section = None
        for row in rows[2:]:
            if not row or all(v in (None, "") for v in row):
                continue
            if row[0] == "KNOWN ISSUES & ADJUDICATIONS":
                section = self.footer
                continue
            if isinstance(row[0], str) and row[0].startswith("QUARANTINE"):
                section = self.quarantine
                continue
            if section is not None:
                section.append(row[0])
            elif len(row) > 1 and row[1]:
                self.rows[row[1]] = dict(zip(columns, row))

    def get(self, host, col):
        return self.rows[host][col]

    def fill(self, host, col):
        """fgColor of a data cell's fill (None when unfilled)."""
        rows = self.data["Validation Tracker"]["rows"]
        c = self.columns.index(col) + 1
        for i, row in enumerate(rows, 1):
            if len(row) > 1 and row[1] == host and i > 2:
                return self.data["Validation Tracker"].get(
                    "fills", {}).get(f"{i},{c}")
        raise KeyError(host)

    def csv_row(self, host):
        for r in self.csv:
            if r["Node Hostname"] == host:
                return r
        raise KeyError(host)

    def all_text(self):
        out = []
        for sheet in self.data.values():
            for row in sheet["rows"]:
                out += [str(v) for v in row if v not in (None, "")]
        return "\n".join(out)


FIXTURES = os.path.join(REPO, "tests", "fixtures", "corpus")


def corpus_root(case, dest, hosts=None):
    """Copy fixture root `case` (tests/fixtures/corpus/<case>, see
    PROVENANCE.md there) to dest. FULLLOGs and other files first, then
    summary.csv stamped one minute newer than every other file, as the
    runner leaves a root. hosts: only copy these host folders."""
    import shutil
    src = os.path.join(FIXTURES, case)
    os.makedirs(dest, exist_ok=True)
    newest = 0.0
    for name in sorted(os.listdir(src)):
        p = os.path.join(src, name)
        if os.path.isdir(p):
            if hosts is not None and name not in hosts:
                continue
            shutil.copytree(p, os.path.join(dest, name))
    for dirpath, _d, files in os.walk(dest):
        for fn in files:
            newest = max(newest, os.path.getmtime(os.path.join(dirpath, fn)))
    s = os.path.join(src, "summary.csv")
    if os.path.isfile(s):
        shutil.copyfile(s, os.path.join(dest, "summary.csv"))
        t = (newest or os.path.getmtime(s)) + 60
        os.utime(os.path.join(dest, "summary.csv"), (t, t))
    return dest


def read_fixture(*parts):
    with open(os.path.join(FIXTURES, *parts), encoding="utf-8") as f:
        return f.read()


def tempdir(testcase):
    d = tempfile.TemporaryDirectory(prefix="dgxv-a2-")
    testcase.addCleanup(d.cleanup)
    return d.name


# ---------------------------------------------------------------------------
# Brief C: scripted host runner + real step outputs from the corpus
# ---------------------------------------------------------------------------

class ScriptedRunner:
    """A non-dry runner that answers each command from a script and
    contacts nothing. script: [(needle, rc, out, dur)], first match wins;
    needle is a substring of the space-joined argv (prefixed "sudo " for
    a sudo call), or a callable on that string.
    A tracked call gets the PGID line a real runner's shell prints."""

    name = "fake"
    dry = False

    def __init__(self, script, default=(0, "", 0.1), pgid=4242):
        self.script = list(script)
        self.default = default
        self.pgid = pgid
        self.calls = []
        self.fetches = []
        self.fetch_error = OSError("scp: permission denied")

    def run(self, argv, timeout, sudo=False, sudo_password=None, stream=None,
            track=False):
        cmd = " ".join(argv)
        self.calls.append({"argv": list(argv), "cmd": cmd, "sudo": sudo,
                           "pw": sudo_password, "track": track,
                           "timeout": timeout})
        rc, out, dur = self.default
        key = ("sudo " if sudo else "") + cmd     # what the host would see
        for needle, r, o, d in self.script:
            if (needle(key) if callable(needle) else needle in key):
                rc, out, dur = r, (o(cmd) if callable(o) else o), d
                break
        if track and self.pgid is not None:
            out = f"DGXV-PGID={self.pgid}\n" + out
        return rc, out, dur

    def fetch(self, remote_path, dest_path):
        self.fetches.append(remote_path)
        if self.fetch_error:
            raise self.fetch_error
        with open(dest_path, "w") as f:
            f.write("fetched " + remote_path)

    def ran(self, needle):
        return [c for c in self.calls if needle in c["cmd"]]


def corpus_block(case, host, cmd_prefix):
    """The output body of the FULLLOG block whose command starts with
    cmd_prefix (real field output, prefix-scrubbed; see PROVENANCE.md)."""
    text = read_fixture(case, host, f"{host}-FULLLOG.txt")
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^\[\d+/\d+\] (.*)$", ln)
        if m and m.group(1).startswith(cmd_prefix) and \
                i + 1 < len(lines) and lines[i + 1] == "-" * 72:
            body = []
            for ln2 in lines[i + 2:]:
                if ln2.startswith("[exit "):
                    return "\n".join(body)
                body.append(ln2)
    raise KeyError(f"{case}/{host}: no block for {cmd_prefix!r}")


W09 = ("wave1-w09-full-pass", "fx-w09-prod-hv-01")


def healthy_health_block():
    """The real w09 `nvsm show health` output with its one Unhealthy
    check (the DIMM part number) flipped to Healthy and the summary
    counts made consistent — what a clean host prints."""
    t = corpus_block(*W09, "sudo nvsm show health")
    t = re.sub(r"(Verify DIMM part number\.+ )Unhealthy", r"\1Healthy", t)
    t = t.replace("259 out of 260 checks are healthy",
                  "260 out of 260 checks are healthy")
    t = t.replace("1 out of 260 checks are unhealthy",
                  "0 out of 260 checks are unhealthy")
    t = t.replace("Overall system status is unhealthy",
                  "Overall system status is healthy")
    return t


def identity_out(hostname="gpu01", platform="dgx", serial="SN-0001"):
    return ("DGXV-ID-BEGIN\n" f"HOSTNAME={hostname}\nPLATFORM={platform}\n"
            "PRODUCT=DGXB300\nVENDOR=NVIDIA\n" f"SERIAL={serial}\n"
            "DGXV-ID-END\n")


def topo_matrix(n, cell="NV18", overrides=None):
    """An `nvidia-smi topo -m` GPU block in the real layout (tab
    separated, ANSI-underlined header, NIC columns, legend)."""
    overrides = overrides or {}
    hdr = "\t\x1b[4m" + "\t".join(f"GPU{i}" for i in range(n)) + \
        "\tNIC0\tCPU Affinity\tNUMA Affinity\tGPU NUMA ID\x1b[0m"
    rows = [hdr]
    for i in range(n):
        cells = [" X " if i == j else overrides.get((i, j), cell)
                 for j in range(n)]
        rows.append(f"GPU{i}\t" + "\t".join(cells) + "\tPXB\t0-63\t0\t\tN/A")
    rows.append("NIC0\t" + "\t".join("PXB" for _ in range(n)) + "\t X ")
    rows += ["", "Legend:", "", "  X    = Self",
             "  SYS  = Connection traversing PCIe as well as the SMP "
             "interconnect between NUMA nodes (e.g., QPI/UPI)",
             "  NV#  = Connection traversing a bonded set of # NVLinks"]
    return "\n".join(rows)


def smi_out(n):
    return ("==============NVSMI LOG==============\n\n"
            "Driver Version                            : 580.178.04\n"
            f"Attached GPUs                             : {n}\n")


def nccl_ok(tests=("all_reduce", "all_gather", "reduce_scatter")):
    out = []
    for t in tests:
        out += [f"===== {t}_perf =====",
                "#       size         count      type   redop    root     "
                "time   algbw   busbw #wrong",
                "    2147483648     536870912     float     sum      -1   "
                "9780.2  219.57  470.31      0",
                "# Out of bounds values : 0 OK",
                "# Avg bus bandwidth    : 289.4",
                f"[{t}_perf exit 0]"]
    return "\n".join(out)


def dgx_host_script(hostname="gpu01", serial="SN-0001", gpus=8,
                    health=None, stress_dur=1827.4, topo=None, extra=()):
    """Script for a clean DGX host built from real step outputs."""
    return list(extra) + [
        ("DGXV-ID-BEGIN", 0, identity_out(hostname, "dgx", serial), 0.2),
        ("cat /etc/dgx-release", 0,
         corpus_block(*W09, "cat /etc/dgx-release"), 0.3),
        ("nvsm show health", 0, health or healthy_health_block(), 163.5),
        ("nvidia-smi -q", 0, smi_out(gpus), 1.2),
        ("nvidia-smi topo -m", 0, topo or topo_matrix(gpus), 2.0),
        ("nvsm stress-test", 0,
         corpus_block(*W09, "sudo nvsm stress-test"), stress_dur),
        ("nvsm dump health", 0,
         corpus_block(*W09, "sudo nvsm dump health"), 2588.0),
        ("base64", 0, "ZmFrZSBsb2cK\n", 0.5),
        ("true", 0, "", 0.1),
    ]


def hgx_host_script(hostname="hgx01", serial="SN-H01", gpus=8, nccl=None,
                    extra=()):
    return list(extra) + [
        ("DGXV-ID-BEGIN", 0, identity_out(hostname, "hgx", serial), 0.2),
        ("cat /etc/os-release", 0,
         'PRETTY_NAME="Ubuntu 24.04.4 LTS"\nNAME="Ubuntu"\n', 0.1),
        ("nvidia-smi -q", 0, smi_out(gpus), 1.2),
        ("nvidia-smi topo -m", 0, topo_matrix(gpus), 2.0),
        ("_perf", 0, nccl if nccl is not None else nccl_ok(), 60.0),
        ("true", 0, "", 0.1),
    ]


def drive_main(testcase, vit, argv, runners, hosts_rows=None):
    """Run dgx-vitals main() with SSHRunner replaced by scripted runners
    keyed by address. hosts_rows: CSV text for --hosts (written to a
    tempfile and passed). Returns (rc, out, err, root)."""
    tmp = tempdir(testcase)
    root = os.path.join(tmp, "Results")
    vit._sleep = lambda s: None

    def factory(address, user=None, batch=True):
        r = runners[address]
        r.target = address
        return r
    vit.SSHRunner = factory
    args = list(argv)
    if hosts_rows is not None:
        hp = os.path.join(tmp, "hosts.csv")
        with open(hp, "w") as f:
            f.write(hosts_rows)
        args = ["--hosts", hp] + args
    if "--out" not in args:
        args += ["--out", root]
    rc, out, err = run_vitals_main(vit, args)
    return rc, out, err, root


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_summary(root):
    with open(os.path.join(root, "summary.csv"), newline="",
              encoding="utf-8") as f:
        return {r["Hostname"]: r for r in csv.DictReader(f)}
