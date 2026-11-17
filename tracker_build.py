#!/usr/bin/env python3
"""tracker_build.py — build a tracker-shaped spreadsheet (and a CSV of the
same rows) from DGX validation evidence, with each host's health card
embedded in its row.

The sheet has one row per host and one column per validation step, the
layout of a field validation tracker. Run dgx-vitals across a batch into
one output root, bring the root back, and build the sheet from everything
inside it. Nothing here is specific to one site or engagement.

Inputs (any combination; at least one result source required):
    --vitals DIR..  dgx-vitals --out root(s): summary.csv plus, per host,
                    <host>/<host>-FULLLOG.txt and the health card
                    <host>/<host>-<VERDICT>.png (runner v2.0; older runners
                    always wrote <host>-HEALTHY.png, which is read too).
                    One flag takes several roots and may be repeated (e.g.
                    several waves); roots are layered in the order given,
                    later over earlier, per step — give them oldest first.
    --bundles DIR   a harvest root of cdw-validation bundles (a foreign
                    harness format): <host>/cdw-validation-<host>-<ts>.tar.gz
                    (newest wins), or already-extracted
                    <host>/cdw-validation-<host>-<ts>/ directories.
    --hosts CSV     fleet list: row order plus the IP columns. Accepts
                    hostname,lan_ip,bmc_ip[,...] (tracker style) or
                    hostname,address[,user] (dgx-vitals style; address is
                    used as the LAN IP). Read with dgx-vitals' own hosts
                    parser: #-comments and blank lines are skipped, and a
                    header must name its column exactly "hostname". Hosts
                    with no results still get a row, marked NO DATA, so the
                    sheet doubles as a checklist.

dgx-vitals.py must sit beside this file: it defines the verdict
vocabulary both tools share (verdict words, step states, stop and
interrupt markers, the opt-out text, card names) and the hosts parser,
and the tracker keeps no copy of its own. Without it the tracker exits 1.

Outputs:
    --out FILE.xlsx         the tracker sheet (sheet name: Validation Tracker)
    FILE.csv                always written next to the sheet: the same rows,
                            columns and verdicts, plus any quarantined input
    --bundle-out FILE.tar.gz  optional: one ferry bundle containing the sheet
                            plus every source artifact it was built from
                            (vitals roots and bundle tarballs), with a
                            .sha256 sidecar.

    A build ends with a block-letter banner in the style of dgx-vitals
    --watch: hosts with results over the sheet's row total, then a tally
    of every row's verdict. --no-banner suppresses it.

    Where an adjudication, or the tracker's own reading of the evidence,
    changes a host's verdict from the one its run recorded, the health
    card embedded in the sheet is re-rendered from the tracker's states
    and exported beside the sheet in <out>_cards/. The raw card in the run
    root is evidence and is never modified. --raw-cards embeds the raw
    cards instead.

Exit codes:
    0   every row is HEALTHY (or HEALTHY WITH WARNINGS) and complete, and
        every input was read in full.
    2   at least one row is NOT HEALTHY, INCOMPLETE or UNREACHABLE, or
        carries no evidence (NO DATA, SKIPPED, DRY RUN).
    1   the tool itself failed or could not fully read its input: a bad
        argument, a missing or unreadable root, a corrupt or unidentifiable
        bundle, quarantined evidence, an adjudication row that matched
        nothing, or a sheet that could not be written. 1 wins over 2.
    No path exits 0 on input it could not fully read.

Judgement rules (deliberate; also written into the sheet's README tab):
    * The tracker speaks the dgx-vitals 2.0 contract. Every OVERALL word
      the runner prints (HEALTHY, HEALTHY WITH WARNINGS, INCOMPLETE, NOT
      HEALTHY, UNREACHABLE) is a recognised verdict, and every step state
      (PASS, FAIL, SKIP, INCOMPLETE, DRY; summary cells Complete, Failed,
      Skipped, Incomplete, Dry Run) a recognised state.
    * Evidence is read from both summary.csv and the FULLLOG trailers
      ("[exit N · Ts · STATE · note]" and "OVERALL: ...").
      When the two disagree about a step, the stricter state is kept and
      the row says so. A recorded failure is never relabelled by note text.
    * summary.csv is stale for a host when (1) the root has no summary.csv,
      (2) summary.csv has no row for a host that has a FULLLOG, or only a
      row with no step results (the runner's SKIPPED placeholder), or
      (3) the host's FULLLOG was modified more than STALE_SLACK_S seconds
      after summary.csv. A stale host is rebuilt from its FULLLOG
      (provenance "rebuilt-from-fulllog"); any stale csv row stays a
      witness, and the stricter of the two wins per step.
    * Each host is held to its profile's expected step set: dgx = the six
      tracker steps; hgx = DGX/OS Release, NVIDIA-SMI Query, NVLink
      Topology and NCCL. NCCL joins the dgx set when the run recorded
      "NCCL required" in its FULLLOG header (runner --require-nccl) or
      this build is given --require-nccl. A missing, skipped or
      Incomplete expected step, an Incomplete step of any kind, a
      truncated, interrupted or non-conforming log, or a profile the
      evidence does not establish makes the host INCOMPLETE. INCOMPLETE
      is never HEALTHY; a recorded failure still reads NOT HEALTHY.
    * Waivers: an hgx host whose NCCL was skipped under the operator's
      opt-out (--no-require-nccl) is INCOMPLETE, and its row names the
      waiver. The waiver is honoured only when the run's own FULLLOG
      records it (Expected: header or OVERALL line), never from note
      text; it travels with that run's NCCL state and never excuses a
      missing NCCL from another root. On dgx, where nothing is required,
      nothing is waived and none is claimed.
    * Step-zero stops (runner v2.0): an UNREACHABLE host is its own row
      label (own tally and colour), its reason read from the FULLLOG's
      Reason: line; it is not a health verdict and exits 2. A host whose
      identity could not be confirmed is INCOMPLETE. Both are
      provisional: any later run with step results, from any root,
      replaces them, and they never override results already on the row.
      An identity mismatch is the exception: it stands, over any
      evidence, until a later run whose own step zero confirmed the
      host's identity.
    * Provisional states: a Skipped or Incomplete step (and its notes) is
      replaced by a later real result for the step from any root; it
      never replaces a real one.
    * % Complete is passed expected tracker steps over the expected
      tracker step count (NCCL is reported in its own column and never in
      the fraction). A skipped or missing step stays in the denominator.
    * Overall weighs NCCL: a Failed NCCL forces NOT HEALTHY. An NCCL that
      neither the profile nor the run requires and that did not run is
      neutral. The Known Issues block carries an "NCCL not tested" line
      naming every host without an NCCL fabric result and why (waived,
      never launched, could not be judged). KI-2 (the never-launched NCCL
      recorded as a failure) applies to logs from runners before v2.0
      only; v2.0 classifies that case itself.
    * When a runner's own Overall string disagrees with the verdict the
      tracker derives from the same step states, the stricter is kept and
      the row names the disagreement.
    * A bundle's NVSM Health is judged from the health log's own content
      (structured check lines ending in a status word, and the log's own
      unhealthy count) — never from the command exit code alone.
    * Every row names its producer and how it was built (Provenance).
      Input that cannot be identified or read is listed in a QUARANTINE
      block, never silently dropped and never guessed into a row.
    * Serial numbers and release strings are extracted from the logs when
      present and left blank when not. Blank means "not in the evidence",
      never a guess.

Requires: openpyxl and Pillow (pip install openpyxl pillow). dgx-vitals
itself stays stdlib-only; this builder is a separate, optional tool.
Python 3.10+.
"""

import argparse
import csv
import datetime
import hashlib
import importlib.util
import io
import os
import re
import sys
import tarfile
import zlib

VERSION = "2.0"     # speaks the dgx-vitals 2.0 verdict contract


def _load_runner():
    """Load dgx-vitals.py, the sibling of this file. The runner defines
    the verdict vocabulary both tools share (HOST_VERDICTS, STEP_STATES,
    SUMMARY_CELL, STOP_MARK, ...) and the one hosts-CSV parser; the
    tracker keeps no copy of either, so the two cannot drift
    (ANALYSIS-TRACKER-FOLLOWUPS item 3). It also supplies the health-card
    font and PNG writer."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "dgx-vitals.py")
    spec = importlib.util.spec_from_file_location("dgx_vitals", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    _V = _load_runner()
except Exception as _e:            # fail closed: no vocabulary, no verdicts
    raise SystemExit(
        f"tracker_build.py: cannot load dgx-vitals.py from beside this file "
        f"({type(_e).__name__}: {_e}). The two tools share one verdict "
        "vocabulary, defined in dgx-vitals.py; keep both files together.")

TRACKER_STEPS = ["DGX Release", "NVSM Health", "NVIDIA-SMI Query",
                 "NVLink Topology", "NVSM Stress Test", "NVSM Dump Health"]

VITALS_TO_TRACKER = {"Release": "DGX Release",
                     "NVSM Health": "NVSM Health",
                     "NVIDIA-SMI Query": "NVIDIA-SMI Query",
                     "NVLink Topology": "NVLink Topology",
                     "NVSM Stress Test": "NVSM Stress Test",
                     "NVSM Dump Health": "NVSM Dump Health"}

BUNDLE_STEP_MAP = {"01": "NVIDIA-SMI Query", "02": "NVLink Topology",
                   "04": "NVSM Health", "05": "NVSM Stress Test",
                   "06": "NVSM Dump Health"}
BUNDLE_EXPECTED_LINES = ("01", "02", "03", "04", "05", "06")

COLUMNS = ["Item", "Node Hostname", "Node Serial Number", "BMC IP", "LAN IP",
           "DGX Release", "NVSM Health", "NVIDIA-SMI Query",
           "NVLink Topology", "NVSM Stress Test", "NVSM Dump Health",
           "% Complete", "Overall", "NCCL Tests", "Source", "Provenance",
           "Notes", "Health Card"]

NCCL = "NCCL Tests"

# Expected step set per profile. % Complete uses the tracker steps of the
# set; NCCL, where expected, gates completeness and Overall only.
PROFILE_STEPS = {
    "dgx": list(TRACKER_STEPS),
    "hgx": ["DGX Release", "NVIDIA-SMI Query", "NVLink Topology", NCCL],
}

REAL_STATES = ("Complete", "Failed", "Warned")   # the step actually ran
# "Incomplete": the step ran but its output could not establish a result
# (runner v2.0). Like Skipped it is not a real state: any later real
# result for the step, from any root, replaces it.

# Verdict severity: higher is stricter. INCOMPLETE is never healthy.
# UNREACHABLE is a row label, not a health verdict (no step ran), so it
# has no severity; it is never healthy and exits 2.
SEVERITY = {"HEALTHY": 0, "HEALTHY WITH WARNINGS": 1, "INCOMPLETE": 2,
            "NOT HEALTHY": 3}
HEALTHY_VERDICTS = ("HEALTHY", "HEALTHY WITH WARNINGS")
# The runner's OVERALL words, imported, never re-typed (shared vocabulary).
HOST_VERDICTS = _V.HOST_VERDICTS
RUNNER_VERDICTS = HOST_VERDICTS + ("DRY RUN",)   # every word a FULLLOG may end on
UNREACHABLE = "UNREACHABLE"

# A FULLLOG modified more than this many seconds after summary.csv makes
# that host's csv row stale. The runner writes summary.csv after every
# FULLLOG, so in an untouched root the csv is always the newer file; the
# slack absorbs copy tools that do not preserve mtimes and FAT/exFAT's
# 2-second timestamp granularity.
STALE_SLACK_S = 2.0

DRY_SUBDIR = "dry-run"   # dgx-vitals' dry-run subdir: never evidence


class HostResult:
    def __init__(self, hostname):
        self.hostname = hostname
        self.serial = ""
        self.lan_ip = ""
        self.bmc_ip = ""
        self.steps = {}          # tracker step name -> "Complete"/"Failed"/""
        self.nccl = ""
        self.overall = ""
        self.source = ""
        self.provenance = []     # producer + how the row was built
        self.profile = ""        # "dgx" / "hgx" / "" (not established)
        self.incomplete = []     # evidence-conformance reasons
        self.floor = ""          # stricter verdict a runner claimed (P2)
        self.notes = []
        self.reasons = []        # set by finalize(): why INCOMPLETE
        self.pct = None
        self.card_path = None    # vitals only
        self.card_verdict = ""   # the OVERALL the card's run recorded
        self.card_dirty = False  # the embedded card needs a re-render
        self.adjudicated = False
        self.health_names = []   # unhealthy-check names (adjudication)
        self.health_ev = None    # NVSM health evidence, same run as state
        self.nccl_ev = None      # NCCL block evidence, same run as state
        # runner 2.0 contract
        self.state_notes = {}    # column -> the runner's note, same run
        #                          as the state (shown while an Incomplete
        #                          state stands; replaced with it)
        self.stop = None         # step-zero stop: {kind, verdict, reason}
        #                          provisional: a later real result
        #                          replaces it (identity mismatch: only a
        #                          later identity-confirmed run)
        self.identity = None     # step zero of the run that supplied the
        #                          row's newest evidence: "ok" / ... / None
        self.nccl_required = False  # some run of this host required NCCL
        #                          (FULLLOG Expected: header)
        self.nccl_waived = False # the NCCL state was waived by that run's
        #                          own recorded operator opt-out


class Ingest:
    """Collects what the build could not read or use. Any entry here
    makes the exit code 1."""

    def __init__(self):
        self.problems = []       # tool-level: unreadable / bad input
        self.quarantine = []     # dicts: host, source, provenance, reason

    def problem(self, msg):
        self.problems.append(msg)
        print(f"error: {msg}", file=sys.stderr)

    def quarantined(self, host, source, provenance, reason):
        self.quarantine.append({"host": host, "source": source,
                                "provenance": provenance, "reason": reason})
        print(f"quarantine: {host or '?'}: {reason}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Fleet list
# ---------------------------------------------------------------------------

def read_fleet(path):
    """Return (ordered hostnames, {hostname: HostResult}) with IPs filled.
    Parsed by dgx-vitals' own hosts parser (one parser for both tools,
    REVIEW-A M11): blank lines and #-comments are skipped, and a header
    without an exact 'hostname' column is refused (HostsFileError)."""
    order, hosts = [], {}
    for e in _V.read_host_entries(path):
        name = e["name"]
        if name in hosts:
            continue
        r = HostResult(name)
        r.lan_ip = e["lan_ip"] or e["address_given"]
        r.bmc_ip = e["bmc_ip"]
        hosts[name] = r
        order.append(name)
    return order, hosts


# ---------------------------------------------------------------------------
# Evidence parsers shared by both pipelines
# ---------------------------------------------------------------------------

_SERIAL_PATTERNS = [r"\[Serial:\s*([A-Za-z0-9]+)\]",
                    r"Chassis Serial Number\s*[:\-]?\s*([A-Za-z0-9]+)",
                    r"Chassis\s+Serial\s*[:\-]\s*([A-Za-z0-9]+)"]


def _plausible_serial(value):
    """A serial is 6+ chars with at least one digit. Guards against the
    blank 'Chassis Serial Number :' fields in nvidia-smi output, whose
    regex capture otherwise lands on the next line's first word."""
    return len(value) >= 6 and any(c.isdigit() for c in value)


def find_serial(text):
    for pat in _SERIAL_PATTERNS:
        for m in re.finditer(pat, text or ""):
            if _plausible_serial(m.group(1)):
                return m.group(1)
    return ""


# NVSM health. A check line is structural: the check name, a dot leader
# of three or more dots, then exactly one status word ending the line,
#   "Verify DIMM part number.............................. Unhealthy"
#   "Status of volumes.................................... Healthy"
# A check is Unhealthy when its status word is Unhealthy — whatever its
# name starts with. Rollups ("Overall system status is unhealthy"),
# summary counts and detail lines have no dot leader and never match.
_CHECK_RE = re.compile(
    r"^\s*(?P<name>\S.*?)\s*\.{3,}\s*"
    r"(?P<status>Healthy|Unhealthy|Unknown|Informational|Disabled|Skipped)"
    r"\s*$", re.IGNORECASE)
_HEALTH_COUNT_RE = re.compile(
    r"^\s*(\d+) out of (\d+) checks are unhealthy\s*$",
    re.IGNORECASE | re.MULTILINE)
_HEALTH_VERSION_RE = re.compile(r"^\s*Version\s*:\s*(\S+)\s*$", re.MULTILINE)
_DIMM_MISMATCH_RE = re.compile(
    r"Mismatch in DIMM '([^']+)' part number, expected is '([^']*)' "
    r"found is '([^']*)'")


def parse_health_checks(text):
    """[(check name, status word)] for every structured check line."""
    out = []
    for ln in (text or "").splitlines():
        m = _CHECK_RE.match(ln)
        if m:
            out.append((m.group("name").strip(), m.group("status").title()))
    return out


def unhealthy_names_from(text):
    """Names of the checks whose status word is Unhealthy (by structure,
    not by how the name begins)."""
    return [n for n, s in parse_health_checks(text) if s == "Unhealthy"]


def real_check_names(names):
    """Kept for callers of the old API: names are already structural
    check names (unhealthy_names_from), so nothing is filtered out."""
    return list(names)


def health_evidence(text, exit_code):
    """Everything the KI-1 rule and its footer may claim, drawn from one
    health log. exit_code is the step's own exit status (None unknown)."""
    checks = parse_health_checks(text)
    m = _HEALTH_COUNT_RE.search(text or "")
    v = _HEALTH_VERSION_RE.search(text or "")
    return {
        "names": [n for n, s in checks if s == "Unhealthy"],
        "checks": len(checks),
        "summary_unhealthy": int(m.group(1)) if m else None,
        "summary_total": int(m.group(2)) if m else None,
        "exit": exit_code,
        "version": v.group(1) if v else "",
        "dimm": _DIMM_MISMATCH_RE.findall(text or ""),
    }


def health_verdict(health_log, exit_code):
    """Judge NVSM health from log content (bundles). Returns
    (status, note); status "" means the log cannot be judged."""
    if not health_log:
        return ("Failed", f"health log missing (exit={exit_code})") \
            if exit_code != 0 else ("", "health log missing")
    ev = health_evidence(health_log, exit_code)
    names, total = ev["names"], ev["checks"]
    counted = ev["summary_unhealthy"]
    if names or (counted or 0) > 0:
        n_bad = max(len(names), counted or 0)
        shown = "; ".join(names[:6]) or "names not in structured lines"
        more = f" (+{len(names) - 6} more)" if len(names) > 6 else ""
        return "Failed", (f"NVSM health: {n_bad} of "
                          f"{ev['summary_total'] or total} checks "
                          f"unhealthy — {shown}{more}")
    if exit_code != 0:
        return "Failed", f"nvsm show health exit={exit_code}"
    if not total:
        if "unhealthy" in health_log.lower():
            return "Failed", ("NVSM health: log mentions unhealthy but "
                              "carries no structured check lines")
        return "", "health log carries no check lines"
    if counted is None:
        return "", ("health log has no 'N out of M checks' summary "
                    "(cut short?)")
    return "Complete", f"NVSM health: {total}/{total} checks healthy"


# NCCL. The runner runs each test between "===== <name>_perf =====" markers
# and appends "[<name>_perf exit N]". mpirun reports a binary it could not
# start as "Executable: <name>_perf". Without --tail the runner's stderr
# lands after all stdout (REVIEW-A H2), so those reports are read across
# the whole block, not per section.
_NCCL_SECTION_RE = re.compile(r"(?m)^===== (\S+?)_perf =====\s*$")
_NCCL_NOT_FOUND_RE = re.compile(r"(?m)^Executable:\s*(\S+)\s*$")


def nccl_evidence(body, note):
    """What one NCCL block proves. non_launch is True only when no test
    produced any result line and every test exited non-zero with its
    binary reported missing (mpirun 'Executable:' report, or exit 127)."""
    parts = _NCCL_SECTION_RE.split(body or "")
    tests = parts[1::2]
    bodies = parts[2::2]
    exits, ran = {}, False
    for name, b in zip(tests, bodies):
        m = re.search(r"\[%s_perf exit (\d+)\]" % re.escape(name), b)
        exits[name] = int(m.group(1)) if m else None
        for ln in b.splitlines():
            toks = ln.split()
            if re.match(r"#\s*(Out of bounds values|Avg bus bandwidth)", ln) \
                    or (len(toks) >= 9 and toks[0].isdigit()):
                ran = True
    not_found = set()
    for exe in _NCCL_NOT_FOUND_RE.findall(body or ""):
        base = os.path.basename(exe)
        not_found.add(base[:-5] if base.endswith("_perf") else base)
    non_launch = bool(tests) and not ran and all(
        exits[t] not in (None, 0) and (t in not_found or exits[t] == 127)
        for t in tests)
    return {"tests": tests, "exits": exits, "ran": ran,
            "not_found": sorted(not_found & set(tests)),
            "non_launch": non_launch, "note": note or ""}


# Stress content the runner itself has rejected since v1.8 (the step did
# not run). A recorded PASS over such content is overridden to Failed.
_HOLLOW_STRESS = (
    ("are you sure you want to proceed",
     "nvsm printed its confirmation prompt and exited"),
    ("stress test is already running",
     "another stress test was already running; nvsm cancelled this one"),
)


# ---------------------------------------------------------------------------
# dgx-vitals FULLLOG
# ---------------------------------------------------------------------------

_HEAD_RE = re.compile(r"^\[(\d+)/(\d+)\] (\S.*)$")
_RULE = "-" * 72
_BAR = "=" * 72
_TRAILER_RE = re.compile(
    r"^\[exit (-?\d+) · ([\d.]+)s · (" + "|".join(_V.STEP_STATES)
    + r")(?: · (.*))?\]\s*$")
_MARKER_RE = re.compile(r"^>>> step (\d+)/(\d+): ")
_TOOL_RE = re.compile(r"^Host: (\S+)\s+Mode: \S+\s+Tool: (\S+?) v(\S+)\s*$")
# FULLLOG trailer word -> sheet state; the same map the runner uses for
# its summary.csv cells, so the two witnesses always use one vocabulary.
TRAILER_STATE = dict(_V.SUMMARY_CELL)
CSV_STATES = tuple(_V.SUMMARY_CELL.values())


def cmd_column(cmd):
    """(tracker column, profile hint) for a FULLLOG step command, keyed on
    the command itself rather than any label."""
    c = cmd.strip()
    if c.startswith("sudo "):
        c = c[5:].strip()
    if c == "cat /etc/dgx-release":
        return "DGX Release", "dgx"
    if c == "cat /etc/os-release":
        return "DGX Release", "hgx"
    if c.startswith("nvsm show health"):
        return "NVSM Health", "dgx"
    if c.startswith("nvidia-smi topo -m"):
        return "NVLink Topology", ""
    if c.startswith("nvidia-smi -q"):
        return "NVIDIA-SMI Query", ""
    if "mpirun" in c and "_perf" in c:
        return NCCL, ""
    if c.startswith("nvsm stress-test"):
        return "NVSM Stress Test", "dgx"
    if c.startswith("nvsm dump health"):
        return "NVSM Dump Health", "dgx"
    return None, ""


class FulllogEvidence:
    def __init__(self):
        self.dry = False
        self.host = ""
        self.tool = ""
        self.version = ""
        self.platform = ""
        self.planned = 0
        self.blocks = []         # dicts, in log order
        self.states = {}         # column -> state (after recheck)
        self.notes = {}          # column -> trailer note
        self.overall_raw = None  # text after "OVERALL: ", None if absent
        self.overall = ""        # verdict token
        self.finished = False
        self.release = ""
        self.health_ev = None
        self.nccl_ev = None
        self.rechecks = []       # tracker overrides of recorded PASSes
        self.problems = []       # conformance failures (-> INCOMPLETE)
        self.profile_hints = set()
        # runner 2.0 header and step zero
        self.nccl_required = None    # Expected: header; None = not recorded
        self.waiver_header = False   # Expected: ... NCCL_OPT_OUT_NOTE
        self.waiver_overall = False  # OVERALL: ... — NCCL_OPT_OUT_NOTE
        self.stop = ""               # STOPPED BEFORE STEP 1: <why>
        self.reason = ""             # Reason: <why> (after OVERALL)
        self.identity = None         # step zero: "ok"/"mismatch"/...
        self.interrupted = ""        # INTERRUPTED: <what> (operator Ctrl-C)

    @property
    def producer(self):
        if self.tool in ("dgx-vitals.py", "dgx-vitals"):
            return f"dgx-vitals {self.version}"
        if self.tool:
            return f"foreign: {self.tool} {self.version}".strip()
        return "unknown"


def _stricter_state(a, b):
    """Stricter of two recorded states for one step: Failed beats every
    other state; Incomplete (ran, not judgeable) beats Skipped; a non-run
    (Skipped/blank) beats Complete."""
    rank = {"Failed": 3, "Incomplete": 2.5, "Skipped": 2, "Dry Run": 1,
            "": 1, "Warned": 0.5, "Complete": 0}
    return a if rank.get(a, 1) >= rank.get(b, 1) else b


def parse_fulllog(text):
    """Parse a dgx-vitals FULLLOG into its step trailers and OVERALL.
    Structure only: block heads "[k/N] cmd" followed by a 72-dash rule,
    a trailer "[exit ...]" closing each block, and the OVERALL line after
    the closing '=' bar."""
    ev = FulllogEvidence()
    lines = (text or "").splitlines()
    if lines and "[DRY RUN]" in lines[0]:
        ev.dry = True
    for ln in lines[:12]:
        if ln == _BAR:
            break
        m = _TOOL_RE.match(ln)
        if m:
            ev.host, ev.tool, ev.version = m.groups()
        elif ln.startswith("Platform: "):
            ev.platform = ln.split()[1] if len(ln.split()) > 1 else ""
        elif ln.startswith(_V.EXPECTED_PREFIX):
            # runner 2.0 records the run's NCCL requirement here
            # (Amendment 1 "Surprise 2": the tracker must learn it from
            # the log, or a required dgx NCCL that never launched would
            # read HEALTHY once INCOMPLETE is a recognised verdict).
            ev.nccl_required = _V.NCCL_REQUIRED_TEXT in ln
            ev.waiver_header = _V.NCCL_OPT_OUT_NOTE in ln
    for ln in lines:
        if ln.startswith(_V.STOP_MARK) and not ev.stop:
            ev.stop = ln[len(_V.STOP_MARK):].strip() or "no reason given"
        elif ln.startswith("Identity check: ") and ev.identity is None:
            ev.identity = ln[len("Identity check: "):].split()[0].lower()
        elif ln.startswith(_V.INTERRUPT_MARK) and not ev.interrupted:
            ev.interrupted = ln[len(_V.INTERRUPT_MARK):].strip()
    heads = [i for i, ln in enumerate(lines)
             if _HEAD_RE.match(ln) and i + 1 < len(lines)
             and lines[i + 1] == _RULE]
    ov_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith("OVERALL: ") and i > 0 \
                and lines[i - 1] == _BAR:
            ov_idx = i
            break
    if ov_idx is not None:
        ev.overall_raw = lines[ov_idx][len("OVERALL: "):].strip()
        ev.overall = _verdict_token(ev.overall_raw)
        ev.waiver_overall = ev.overall_raw.endswith(
            "— " + _V.NCCL_OPT_OUT_NOTE)
        ev.finished = any(ln.startswith("Finished: ")
                          for ln in lines[ov_idx + 1:])
        for ln in lines[ov_idx + 1:]:
            if ln.startswith(_V.REASON_PREFIX):
                ev.reason = ln[len(_V.REASON_PREFIX):].strip()
    planned = 0
    for ln in lines:
        m = _MARKER_RE.match(ln)
        if m:
            planned = max(planned, int(m.group(2)))
    for h in heads:
        planned = max(planned, int(_HEAD_RE.match(lines[h]).group(2)))
    ev.planned = planned
    ends = heads[1:] + [ov_idx - 1 if ov_idx is not None else len(lines)]
    for h, end in zip(heads, ends):
        k, n, cmd = _HEAD_RE.match(lines[h]).groups()
        region = lines[h + 2:end]
        t = None
        for j in range(len(region) - 1, -1, -1):
            if _TRAILER_RE.match(region[j]):
                t = j
                break
        col, hint = cmd_column(cmd)
        if hint:
            ev.profile_hints.add(hint)
        blk = {"k": int(k), "n": int(n), "cmd": cmd, "col": col,
               "body": "\n".join(region[:t] if t is not None else region),
               "exit": None, "state": "", "note": ""}
        if t is not None:
            ex, _dur, st, note = _TRAILER_RE.match(region[t]).groups()
            blk.update(exit=int(ex), state=TRAILER_STATE[st],
                       note=(note or "").strip())
        ev.blocks.append(blk)
    done = [b for b in ev.blocks if b["state"]]
    if ev.overall_raw is None and ev.interrupted:
        ev.problems.append("the run was interrupted by the operator and its "
                           "FULLLOG is unfinished (" + ev.interrupted[:120]
                           + ")")
    elif ev.overall_raw is None:
        ev.problems.append("FULLLOG has no OVERALL trailer (the run did not "
                           "finish or the log was cut short)")
    elif ev.overall not in RUNNER_VERDICTS:
        ev.problems.append(f"FULLLOG OVERALL '{ev.overall_raw}' is not a "
                           "recognized verdict")
    if planned and len(done) < planned:
        ev.problems.append(f"FULLLOG holds {len(done)} of {planned} planned "
                           "step results (truncated)")
    if ev.stop:
        # A step-zero stop is a well-formed log with no step results: it
        # contributes no conformance problem (it is provisional evidence,
        # handled as HostResult.stop), unless it is malformed.
        if done:
            ev.problems.append("FULLLOG stopped at step zero but carries "
                               "step results")
        if ev.overall not in ("UNREACHABLE", "INCOMPLETE"):
            ev.problems.append(f"FULLLOG stopped at step zero but its "
                               f"OVERALL reads '{ev.overall_raw}'")
    elif not done and ev.overall_raw is not None and not ev.dry:
        ev.problems.append("FULLLOG carries no step trailers")
    if not ev.tool:
        ev.problems.append("FULLLOG header names no producing tool")
    for b in done:
        col = b["col"]
        if col is None:
            continue
        state = b["state"]
        if col == "NVSM Stress Test" and state == "Complete":
            low = b["body"].lower()
            for sig, why in _HOLLOW_STRESS:
                if sig in low:
                    state = "Failed"
                    ev.rechecks.append(
                        f"NVSM Stress Test: recorded PASS overridden — the "
                        f"stress block shows {why}; the test never ran")
                    break
        prev = ev.states.get(col)
        ev.states[col] = state if prev is None \
            else _stricter_state(prev, state)
        if b["note"]:
            ev.notes[col] = b["note"]
        if col == "DGX Release" and b["state"] == "Complete" and b["note"]:
            ev.release = b["note"]
        if col == "NVSM Health":
            ev.health_ev = health_evidence(b["body"], b["exit"])
        if col == NCCL:
            ev.nccl_ev = nccl_evidence(b["body"], b["note"])
            ev.nccl_ev["version"] = ev.version
            ev.nccl_ev["tool"] = ev.tool
    if ev.platform in ("dgx", "hgx"):
        ev.profile_hints.add(ev.platform)
    return ev


def vitals_release_line(fulllog_text):
    """The dgx-release verdict note (first line of /etc/dgx-release) rides in
    the FULLLOG trailer for the `cat /etc/dgx-release` (or os-release) step."""
    if not fulllog_text:
        return ""
    return parse_fulllog(fulllog_text).release


def fulllog_health_names(fulllog_text):
    """Unhealthy-check names from a dgx-vitals FULLLOG, scoped to the
    `nvsm show health` step block (the topo block carries its own
    'Route Unhealthy' lines, which are not health checks)."""
    ev = parse_fulllog(fulllog_text or "")
    return list(ev.health_ev["names"]) if ev.health_ev else []


def is_dry_fulllog_text(text):
    first = (text or "").split("\n", 1)[0]
    return "[DRY RUN]" in first


# ---------------------------------------------------------------------------
# dgx-vitals ingest
# ---------------------------------------------------------------------------

SUMMARY_STEP_COLS = list(VITALS_TO_TRACKER) + [NCCL]


def _csv_states(row):
    out = {}
    for vcol in SUMMARY_STEP_COLS:
        val = (row.get(vcol) or "").strip()
        if val in CSV_STATES:
            out[VITALS_TO_TRACKER.get(vcol, vcol)] = val
    return out


def _verdict_token(s):
    """The verdict word of an OVERALL / Overall string: what precedes
    the tally "(n/m steps passed)" and any " — <waiver>" suffix."""
    return (s or "").split(" (")[0].split(" — ")[0].strip()


def _stop_kind(reason):
    if reason.startswith(_V.STOP_UNREACHABLE):
        return "unreachable"
    if reason.startswith(_V.STOP_MISMATCH):
        return "identity-mismatch"
    if reason.startswith(_V.STOP_UNCONFIRMED):
        return "identity-unconfirmed"
    return "stopped"


def _is_legacy_runner(version):
    """True for a dgx-vitals log older than v2.0 (or with no version)."""
    try:
        return tuple(int(x) for x in (version or "0").split(".")[:2]) \
            < (2, 0)
    except ValueError:
        return True


def _split_notes(text):
    """summary.csv Notes are the runner's '; '-joined fragments; keep them
    as fragments so a superseded one can be dropped in a merge."""
    return [f.strip() for f in (text or "").split("; ") if f.strip()]


def _host_dirs(root):
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [d for d in names
            if d != DRY_SUBDIR and os.path.isdir(os.path.join(root, d))]


def read_vitals(root, ingest=None, force_rebuild=False):
    """Parse one dgx-vitals --out root -> {hostname: HostResult}.

    Each host is built from summary.csv when its row is fresh, or rebuilt
    from its FULLLOG trailers when the row is stale (see STALE_SLACK_S and
    the module docstring); either way the other source, where present, is
    a second witness and the stricter state wins per step."""
    ingest = ingest or Ingest()
    results = {}
    if not os.path.isdir(root):
        ingest.problem(f"{root}: --vitals root is not a directory")
        return results
    rootname = os.path.basename(os.path.normpath(root))
    summary = os.path.join(root, "summary.csv")
    csv_rows, csv_mtime, csv_ok = {}, None, False
    if os.path.isfile(summary):
        try:
            with open(summary, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                fields = reader.fieldnames or []
                if "Hostname" not in fields or not any(
                        c in fields for c in SUMMARY_STEP_COLS):
                    ingest.problem(f"{summary}: not a dgx-vitals summary "
                                   f"(header: {', '.join(fields)[:120]})")
                else:
                    csv_ok = True
                    for row in reader:
                        name = (row.get("Hostname") or "").strip()
                        if name:
                            csv_rows[name] = row
            csv_mtime = os.path.getmtime(summary)
        except (OSError, csv.Error, UnicodeError) as e:
            ingest.problem(f"{summary}: unreadable ({e})")
            csv_rows, csv_ok = {}, False

    fulllogs, n_dry = {}, 0
    for d in _host_dirs(root):
        hd = os.path.join(root, d)
        try:
            files = sorted(os.listdir(hd))
        except OSError as e:
            ingest.problem(f"{hd}: unreadable ({e})")
            continue
        for fn in files:
            if not fn.endswith("-FULLLOG.txt"):
                continue
            p = os.path.join(hd, fn)
            if fn != f"{d}-FULLLOG.txt":
                ingest.quarantined(d, "dgx-vitals", "unknown",
                                   f"{rootname}/{d}/{fn}: FULLLOG name does "
                                   "not match its host folder — not used")
                continue
            fulllogs[d] = p
        if d not in fulllogs and d not in csv_rows and \
                _V.card_files(hd, d):
            ingest.quarantined(d, "dgx-vitals", "unknown",
                               f"{rootname}/{d}/ holds a health card but no "
                               "FULLLOG and no summary row — not used")

    texts = {}
    for host, p in list(fulllogs.items()):
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                texts[host] = fh.read()
        except OSError as e:
            ingest.problem(f"{p}: unreadable ({e})")
            continue
        if is_dry_fulllog_text(texts[host]):
            n_dry += 1
            del texts[host]
            del fulllogs[host]

    hosts = sorted(set(csv_rows) | set(fulllogs))
    if not hosts:
        what = ("only dry-run artifacts" if n_dry else
                "no summary.csv and no FULLLOGs")
        ingest.problem(f"{root}: {what} — nothing to read")
        return results

    for host in hosts:
        row = csv_rows.get(host)
        text = texts.get(host)
        fl = fulllogs.get(host)
        if row is not None and (
                _verdict_token(row.get("Overall")) == "DRY RUN"
                or "Dry Run" in _csv_states(row).values()):
            ingest.quarantined(host, "dgx-vitals", "dgx-vitals (dry run)",
                               f"{rootname}/summary.csv row is a dry-run "
                               "row (legacy v1.9 dry summary) — not evidence")
            row = None
            if text is None:
                continue
        if fl is not None and text is None and row is None:
            continue    # unreadable FULLLOG, already reported
        ev = parse_fulllog(text) if text is not None else None
        csv_states = _csv_states(row) if row is not None else {}

        stale = None
        if ev is not None:
            if force_rebuild:
                stale = "--from-fulllog given"
            elif not os.path.isfile(summary) or not csv_ok:
                stale = "summary.csv absent or unreadable"
            elif row is None:
                stale = "summary.csv has no row for this host"
            elif ev.stop and not csv_states:
                stale = None    # a step-zero stop row has no step results
            elif not csv_states:
                stale = ("summary.csv row carries no step results "
                         "(the runner skipped the host)")
            else:
                try:
                    fl_mtime = os.path.getmtime(fl)
                except OSError:
                    fl_mtime = None
                if fl_mtime is not None and csv_mtime is not None and \
                        fl_mtime > csv_mtime + STALE_SLACK_S:
                    stale = (f"FULLLOG is {fl_mtime - csv_mtime:.0f}s newer "
                             "than summary.csv")

        r = HostResult(host)
        r.source = "dgx-vitals"
        producer = ev.producer if ev is not None \
            else "dgx-vitals (version not recorded)"
        how = "rebuilt-from-fulllog" if stale else "summary.csv"
        r.provenance = [f"{producer} ({how})"]
        if stale:
            r.notes.append(f"Rebuilt from FULLLOG trailers: {stale}")
        if row is not None:
            r.overall = (row.get("Overall") or "").strip()
        if row is not None and not stale:
            r.notes += _split_notes(row.get("Notes"))
        host_dir = os.path.join(root, host)
        cards = _V.card_files(host_dir, host)   # verdict-named or legacy
        if cards:
            r.card_path = cards[0]
            r.card_verdict = ev.overall if ev is not None \
                else _verdict_token(r.overall)
        if ev is not None and ev.stop and not csv_states:
            # Step zero stopped the run: no step results. The stop is
            # provisional evidence carried on its own (never as a note or
            # a conformance reason), so a later run that reaches the host
            # replaces it entirely — except an identity mismatch, which
            # only a later identity-confirmed run clears.
            reason = ev.reason or ev.stop
            r.stop = {"kind": _stop_kind(reason),
                      "verdict": ev.overall if ev.overall in
                      ("UNREACHABLE", "INCOMPLETE") else "INCOMPLETE",
                      "reason": reason}
            r.overall = r.stop["verdict"]
            r.notes = []        # the reason travels in r.stop, not notes
            r.incomplete += ev.problems
            r.identity = ev.identity
            r.serial = find_serial(text)
            if ev.host and ev.host != host:
                r.incomplete.append(f"FULLLOG Host: line names '{ev.host}', "
                                    f"not '{host}'")
            results[host] = r
            continue
        if ev is None:
            r.incomplete.append("FULLLOG unreadable" if fl else
                                "no FULLLOG on disk for this summary row")
            for col, val in csv_states.items():
                if col == NCCL:
                    r.nccl = val
                else:
                    r.steps[col] = val
            if any(r.steps.get(s) for s in ("NVSM Health",
                                            "NVSM Stress Test",
                                            "NVSM Dump Health")):
                r.profile = "dgx"
            results[host] = r
            continue

        # Two witnesses: combine per step, stricter wins, name every
        # disagreement.
        r.incomplete += ev.problems
        if ev.host and ev.host != host:
            r.incomplete.append(f"FULLLOG Host: line names '{ev.host}', "
                                f"not '{host}'")
        r.notes += ev.rechecks
        log_states = dict(ev.states)
        cols = [c for c in TRACKER_STEPS + [NCCL]
                if c in log_states or c in csv_states]
        for col in cols:
            c = csv_states.get(col, "")
            lg = log_states.get(col, "")
            c = "" if c == "Dry Run" else c
            if row is None or not csv_states or c == lg:
                val = lg
            elif not c:
                # the csv is silent on a step the log recorded
                val = lg
                if not stale:
                    r.notes.append(f"{col}: summary.csv blank, FULLLOG "
                                   f"says {lg} — FULLLOG kept")
            elif not lg:
                # the csv claims a result the log has no trailer for: a
                # recorded failure stands; an unevidenced pass does not
                val = "" if c == "Complete" else c
                r.notes.append(f"{col}: summary.csv says {c} but the "
                               "FULLLOG has no result for it"
                               + (" — not counted" if not val else ""))
            else:
                val = _stricter_state(c, lg)
                r.notes.append(
                    f"{col}: summary.csv says {c or 'blank'}, FULLLOG says "
                    f"{lg or 'no result'} — stricter kept "
                    f"({val or 'not counted'})")
            if col == NCCL:
                r.nccl = val
            elif val:
                r.steps[col] = val
        if stale:
            for col in cols:
                note = ev.notes.get(col)
                st = log_states.get(col)
                if note and st in ("Failed", "Skipped"):
                    r.notes.append(f"{col}: {note}")
        for col in cols:
            final = r.nccl if col == NCCL else r.steps.get(col, "")
            if ev.notes.get(col) and final == log_states.get(col):
                r.state_notes[col] = ev.notes[col]
        # An Incomplete step's note is shown in the row's reasons while
        # the state stands (finalize) and goes with it when a later root
        # replaces it, so the runner's copy in summary.csv is not kept as
        # a free-standing note.
        inc = {c for c in cols
               if (r.nccl if c == NCCL else r.steps.get(c)) == "Incomplete"}
        if inc:
            r.notes = [n for n in r.notes
                       if not any(n.startswith(p) for c in inc
                                  for p in _note_prefixes(c))]
        r.nccl_required = bool(ev.nccl_required)
        r.nccl_waived = (ev.waiver_header or ev.waiver_overall) and \
            r.nccl == "Skipped"
        r.identity = ev.identity
        if ev.release:
            r.notes.insert(0, f"Release: {ev.release}")
        r.serial = find_serial(text)
        if ev.health_ev is not None and \
                r.steps.get("NVSM Health") == log_states.get("NVSM Health"):
            r.health_ev = ev.health_ev
            r.health_names = list(ev.health_ev["names"])
        if ev.nccl_ev is not None and r.nccl == log_states.get(NCCL):
            r.nccl_ev = ev.nccl_ev
        hints = set(ev.profile_hints)
        if any(csv_states.get(s) for s in ("NVSM Health", "NVSM Stress Test",
                                           "NVSM Dump Health")):
            hints.add("dgx")
        if len(hints) == 1:
            r.profile = hints.pop()
        elif len(hints) > 1:
            r.incomplete.append("evidence names both dgx and hgx profiles")

        # P2: a runner's Overall string vs the verdict derived from the
        # same states. Stricter kept, disagreement named.
        derived = overall_verdict(r.steps, r.nccl)
        claims = [("FULLLOG", ev.overall)]
        if row is not None and not stale:
            claims.append(("summary.csv", _verdict_token(row.get("Overall"))))
        for src, tok in claims:
            if tok == "INCOMPLETE":
                # A recognised claim, not a conformance problem. The
                # tracker reproduces it from the same states and the
                # run's own header; only a claim it cannot reproduce is
                # kept as a floor (stricter kept), and named.
                own = judge(r)[0]
                if own in HEALTHY_VERDICTS:
                    r.floor = r.floor if SEVERITY.get(r.floor, -1) >= \
                        SEVERITY["INCOMPLETE"] else "INCOMPLETE"
                    r.notes.append(
                        f"Overall: the runner's {src} reads INCOMPLETE; the "
                        f"tracker's reading of the same evidence derives "
                        f"{own} — stricter kept (INCOMPLETE)")
                continue
            if tok not in SEVERITY or derived is None \
                    or tok == derived:
                continue
            if SEVERITY[tok] > SEVERITY[derived]:
                if SEVERITY[tok] > SEVERITY.get(r.floor, -1):
                    r.floor = tok
                r.notes.append(f"Overall: the runner's {src} reads {tok}; "
                               f"the step states derive {derived} — "
                               f"stricter kept ({tok})")
            else:
                r.notes.append(f"Overall: the runner's {src} reads {tok}; "
                               f"the step states derive {derived} — "
                               f"stricter kept ({derived})")
        results[host] = r
    return results


# ---------------------------------------------------------------------------
# cdw-validation bundle ingest (foreign format)
# ---------------------------------------------------------------------------
# Format, as harvested in the field: summary.txt opens with
# "<...> run on <host> at <ts>", lists one line per command
# "NN-slug  exit=N  Ns  command", and closes with "Failed commands: N".
# 03-nvsm-versions.log and 04-nvsm-health.log carry the release and health
# evidence. A bundle is identified by its artifact name and by summary.txt
# carrying at least one step line; complete when all six step lines, the
# closing count and (when step 04 ran) the health log are present.

_SUMMARY_LINE = re.compile(r"^(\d\d)-\S+\s+exit=(\d+)\s+(\d+)s\s+(.*)$")
_SUMMARY_HEAD = re.compile(r"\brun on (\S+) at (\S+)\s*$")
_SUMMARY_FOOT = re.compile(r"^Failed commands:\s*(\d+)\s*$")
BUNDLE_PRODUCER = "foreign: cdw-validation"


def _member_text(tf, suffix):
    """Text of the first bundle member whose basename matches suffix."""
    for m in tf.getmembers():
        if os.path.basename(m.name) == suffix and m.isfile():
            fh = tf.extractfile(m)
            if fh:
                return fh.read().decode("utf-8", errors="replace")
    return ""


def parse_bundle(summary_txt, files):
    """files: {basename: text}. Returns a HostResult (hostname unset), or
    None when summary.txt carries no step line (not identifiable)."""
    r = HostResult("")
    r.source = "cdw-validation bundle"
    r.provenance = [BUNDLE_PRODUCER]
    r.profile = "dgx"
    exits, head_host, footer = {}, "", None
    for ln in (summary_txt or "").splitlines():
        s = ln.strip()
        m = _SUMMARY_LINE.match(s)
        if m:
            exits[m.group(1)] = int(m.group(2))
            continue
        h = _SUMMARY_HEAD.search(s)
        if h and not head_host:
            head_host = h.group(1)
        f = _SUMMARY_FOOT.match(s)
        if f:
            footer = int(f.group(1))
    if not exits:
        return None
    r.bundle_host = head_host
    missing = [p for p in BUNDLE_EXPECTED_LINES if p not in exits]
    if missing:
        r.incomplete.append("bundle summary.txt has no line for step(s) "
                            + ", ".join(missing))
    if footer is None:
        r.incomplete.append("bundle summary.txt has no closing "
                            "'Failed commands' count (cut short)")
    for prefix, tcol in BUNDLE_STEP_MAP.items():
        if prefix not in exits:
            continue
        if tcol == "NVSM Health":
            log = files.get("04-nvsm-health.log", "")
            status, note = health_verdict(log, exits[prefix])
            if status:
                r.steps[tcol] = status
            else:
                r.incomplete.append(f"NVSM Health: {note}")
            if note and status:
                r.notes.append(note)
            r.health_ev = health_evidence(log, exits[prefix]) if log else None
            r.health_names = list(r.health_ev["names"]) if r.health_ev else []
        else:
            r.steps[tcol] = "Complete" if exits[prefix] == 0 else "Failed"
            if exits[prefix] != 0:
                r.notes.append(f"{tcol}: exit={exits[prefix]}")
    versions = files.get("03-nvsm-versions.log", "")
    m = re.search(r"(?im)^.*dgx\s*release.*[:=]\s*(.+)$", versions)
    if m:
        r.steps["DGX Release"] = "Complete"
        r.notes.insert(0, f"Release: {m.group(1).strip()}")
    elif "03" in exits and exits["03"] == 0:
        r.steps["DGX Release"] = "Complete"
    elif "03" in exits:
        r.steps["DGX Release"] = "Failed"
        r.notes.append(f"DGX Release: nvsm show versions exit={exits['03']}")
    r.serial = find_serial(files.get("04-nvsm-health.log", ""))
    return r


def read_bundles(root, ingest=None):
    """Parse a harvest root -> {hostname: (HostResult, [artifact paths])}.
    Unreadable or unidentifiable host folders are quarantined."""
    ingest = ingest or Ingest()
    results = {}
    if not os.path.isdir(root):
        ingest.problem(f"{root}: --bundles root is not a directory")
        return results
    rootname = os.path.basename(os.path.normpath(root))
    for host in sorted(os.listdir(root)):
        host_dir = os.path.join(root, host)
        if not os.path.isdir(host_dir):
            continue
        candidates = []
        for dirpath, _dirs, files in os.walk(host_dir):
            for fn in files:
                if fn.startswith("cdw-validation-") and fn.endswith(".tar.gz"):
                    candidates.append(os.path.join(dirpath, fn))
        extracted = [d for d in os.listdir(host_dir)
                     if d.startswith("cdw-validation-")
                     and os.path.isdir(os.path.join(host_dir, d))]
        parsed, artifacts, label = None, [], ""
        if candidates:
            newest = sorted(candidates)[-1]
            artifacts = [newest]
            label = os.path.relpath(newest, root)
            try:
                with tarfile.open(newest, "r:gz") as tf:
                    files = {}
                    for suffix in ("03-nvsm-versions.log",
                                   "04-nvsm-health.log"):
                        files[suffix] = _member_text(tf, suffix)
                    parsed = parse_bundle(_member_text(tf, "summary.txt"),
                                          files)
            except (tarfile.TarError, EOFError, OSError, zlib.error) as e:
                ingest.quarantined(host, "cdw-validation bundle",
                                   BUNDLE_PRODUCER,
                                   f"{rootname}/{label}: unreadable "
                                   f"bundle ({type(e).__name__}: {e})")
                continue
        elif extracted:
            bdir = os.path.join(host_dir, sorted(extracted)[-1])
            label = os.path.relpath(bdir, root)
            files = {}
            for suffix in ("summary.txt", "03-nvsm-versions.log",
                           "04-nvsm-health.log"):
                p = os.path.join(bdir, suffix)
                if os.path.isfile(p):
                    try:
                        with open(p, encoding="utf-8",
                                  errors="replace") as fh:
                            files[suffix] = fh.read()
                    except OSError as e:
                        ingest.problem(f"{p}: unreadable ({e})")
            parsed = parse_bundle(files.get("summary.txt", ""), files)
            artifacts = [bdir]
        else:
            try:
                listing = ", ".join(sorted(os.listdir(host_dir))[:5])
            except OSError:
                listing = "?"
            ingest.quarantined(host, "unknown", "unknown",
                               f"{rootname}/{host}/: no recognizable bundle "
                               f"(contents: {listing or 'empty'})")
            continue
        if parsed is None:
            ingest.quarantined(host, "unknown", "unknown",
                               f"{rootname}/{label}: summary.txt carries no "
                               "step lines — producer not identifiable")
            continue
        parsed.hostname = host
        bh = getattr(parsed, "bundle_host", "")
        if bh and bh != host:
            parsed.incomplete.append(f"bundle summary.txt names host '{bh}', "
                                     f"filed under '{host}'")
        results[host] = (parsed, artifacts)
    return results


# ---------------------------------------------------------------------------
# Verdicts, merge, adjudication
# ---------------------------------------------------------------------------

def overall_verdict(steps, nccl):
    """The health verdict over a set of recorded states, used on every
    path (single root, --hosts, merged roots, adjudication re-render).
    The six tracker steps and the NCCL state all count; NCCL counts only
    when it actually ran (Complete/Failed/Warned). Completeness is
    finalize()'s business, not this function's. Returns None when there
    is no step evidence at all."""
    states = [steps[s] for s in TRACKER_STEPS
              if steps.get(s) in REAL_STATES]
    if nccl in REAL_STATES:
        states.append(nccl)
    if not states:
        return None
    if "Failed" in states:
        return "NOT HEALTHY"
    if "Warned" in states:
        return "HEALTHY WITH WARNINGS"
    return "HEALTHY"


def expected_steps(profile, require_nccl=False):
    steps = list(PROFILE_STEPS.get(profile) or TRACKER_STEPS)
    if require_nccl and NCCL not in steps:
        steps.append(NCCL)
    return steps


def stop_reason(stop):
    """The row reason for a step-zero stop, saying what clears it."""
    kind, why = stop["kind"], stop["reason"]
    if kind == "identity-mismatch":
        return (f"{why} — step zero stopped the run, so nothing was "
                "validated under this name; this clears only when a later "
                "run's own step zero confirms the host's identity")
    if kind == "unreachable":
        return (f"{why} — the runner could not reach the host and no step "
                "ran; a later run that reaches it replaces this")
    return (f"{why} — step zero stopped the run and no step ran; a later "
            "run with step results replaces this")


def _waiver_binds(r):
    """The run's own recorded operator opt-out waived a test that would
    otherwise be required: an hgx host's Skipped NCCL. On dgx nothing is
    required, so nothing is waived (and no waiver is ever invented)."""
    return r.nccl_waived and r.nccl == "Skipped" and r.profile == "hgx"


def judge(r, require_nccl=False):
    """(verdict, reasons, pct) for a row, without changing it — the one
    verdict rule, used by finalize() and by the runner-claim check.

    Missing evidence is INCOMPLETE; fault evidence is NOT HEALTHY; a
    waiver is honoured only as a named reason, never as a pass. A row
    with no step result keeps the runner's own label (SKIPPED), or its
    step-zero stop (UNREACHABLE / INCOMPLETE), or reads INCOMPLETE (only
    broken evidence) or NO DATA."""
    has_evidence = any(r.steps.get(s) for s in TRACKER_STEPS) or r.nccl
    if not has_evidence:
        reasons = list(dict.fromkeys(r.incomplete)) if r.source else []
        if r.stop:
            return r.stop["verdict"], [stop_reason(r.stop)] + reasons, None
        label = r.overall if r.overall and r.overall not in SEVERITY \
            and r.overall != UNREACHABLE else ""
        if not r.source:
            return "NO DATA", [], None
        return (label or ("INCOMPLETE" if reasons else "NO DATA")), \
            reasons, None
    reasons = list(dict.fromkeys(r.incomplete))
    if r.stop:          # only an identity mismatch outlives evidence
        reasons.append(stop_reason(r.stop))
    profile = r.profile
    if not profile:
        reasons.append("profile (dgx/hgx) not established by the "
                       "evidence — held to the dgx step set")
    run_req = r.nccl_required
    expect = expected_steps(profile or "dgx", require_nccl or run_req)
    for s in expect:
        v = r.nccl if s == NCCL else r.steps.get(s, "")
        if v in REAL_STATES:
            continue
        if v == "Incomplete":
            why = (f"{s} incomplete — "
                   + (r.state_notes.get(s) or "the runner could not judge "
                      "the step from its output"))
        elif v == "Skipped" and s == NCCL and _waiver_binds(r):
            why = ("NCCL Tests not tested — waived by the operator's "
                   "opt-out (--no-require-nccl), recorded in the run's own "
                   "FULLLOG; a waived test is missing evidence")
            if require_nccl:
                why += ("; and this sheet was built with --require-nccl, "
                        "which the opt-out does not override")
        elif v == "Skipped" and s == NCCL and profile != "hgx":
            why = ("NCCL Tests skipped — " + (
                "required by this sheet (--require-nccl)" if require_nccl
                else "required by the run (--require-nccl)"))
        elif v == "Skipped":
            why = f"{s} skipped — required by the {profile or 'dgx'} profile"
        else:
            why = f"{s} has no result"
        reasons.append(why)
    # The runner counts an Incomplete step whether or not the profile
    # expects it (it ran and could not be judged); so does the tracker.
    for s in TRACKER_STEPS + [NCCL]:
        v = r.nccl if s == NCCL else r.steps.get(s, "")
        if s not in expect and v == "Incomplete":
            reasons.append(f"{s} incomplete — " + (
                r.state_notes.get(s) or "the runner could not judge the "
                "step from its output"))
    tracker_expect = [s for s in expect if s != NCCL]
    passed = sum(1 for s in tracker_expect
                 if r.steps.get(s) in ("Complete", "Warned"))
    pct = round(100 * passed / len(tracker_expect)) if tracker_expect \
        else None
    verdict = overall_verdict(r.steps, r.nccl) or "INCOMPLETE"
    if reasons and SEVERITY[verdict] < SEVERITY["INCOMPLETE"]:
        verdict = "INCOMPLETE"
    if r.floor and SEVERITY[r.floor] > SEVERITY[verdict]:
        verdict = r.floor
    return verdict, reasons, pct


def finalize(r, require_nccl=False):
    """Compute % Complete and Overall from the row's states, its profile's
    expected step set (plus any NCCL requirement its runs recorded), its
    conformance reasons, its step-zero stop and any stricter runner claim
    (judge()). Idempotent."""
    r.overall, r.reasons, r.pct = judge(r, require_nccl)
    return r.pct


def row_notes(r):
    """Notes as shown in the sheet and CSV: the completeness verdict's
    reasons first, then the evidence notes."""
    lead = []
    if r.reasons:
        tag = r.overall if r.overall in ("INCOMPLETE", UNREACHABLE) \
            else "Also incomplete"
        lead.append(f"{tag} — " + "; ".join(r.reasons))
    return "; ".join(lead + r.notes)


def _layer_state(cur_v, new_v):
    """One step cell, newer root over older: a real state (the step ran)
    overwrites anything; a non-real one (Skipped, Incomplete, or a stray
    value such as a dry run's 'Dry Run') fills only a cell with no real
    state, so a refused or inconclusive re-run can never erase a Complete
    or Failed, and any real result replaces a provisional one."""
    if not new_v:
        return cur_v
    if new_v in REAL_STATES or cur_v not in REAL_STATES:
        return new_v
    return cur_v


_RUNNER_LABEL = {"DGX Release": ("DGX Release", "OS Release"),
                 "NVSM Health": ("NVSM Health", "NVSM Show Health")}


def _note_prefixes(col):
    """Note fragments that belong to one step (runner and tracker
    wording), so they can leave with a superseded provisional state."""
    labels = _RUNNER_LABEL.get(col, (col,))
    out = tuple(f"{lb}: " for lb in labels)
    if col == NCCL:
        out += (_V.NCCL_OPT_OUT_NOTE,)
    return out


def _has_steps(r):
    return any(r.steps.get(s) for s in TRACKER_STEPS) or bool(r.nccl)


def merge_result(cur, new):
    """Layer a newer result over an existing one, per step: a partial
    re-run (e.g. --only dump) must not erase the steps it did not re-run,
    and a Skipped or Incomplete step (e.g. a refused stress re-run) never
    overwrites a Complete or Failed one (_layer_state). Step evidence
    (health check names, NCCL block, the runner's step note, an NCCL
    waiver) travels with the state it belongs to. Conformance reasons and
    stricter runner claims from every root are kept.

    Provisional evidence is not sticky (runner 2.0 contract): an
    Incomplete or Skipped state and its notes are replaced by a later
    real result for the step, from any root; a step-zero stop
    (UNREACHABLE, identity not confirmed) is replaced by any later run
    with step results and never overrides evidence already on the row.
    The one exception, per ruling: an identity-mismatch stop stays until
    a later run whose own step zero confirmed the host's identity."""
    new_steps = sum(1 for v in new.steps.values() if v)
    cur_steps = sum(1 for v in cur.steps.values() if v)
    cur_had_steps, new_has_steps = _has_steps(cur), _has_steps(new)

    # step-zero stops
    if new.stop:
        if new.stop["kind"] == "identity-mismatch":
            cur.stop = new.stop           # never ignored, even over evidence
        elif cur.stop and cur.stop["kind"] == "identity-mismatch":
            pass                          # only a confirmed run clears it
        elif not cur_had_steps:
            cur.stop = new.stop           # fills an otherwise empty row
        # else: an unreachable / unconfirmed attempt never overrides
        # evidence the row already has
    elif cur.stop:
        if cur.stop["kind"] == "identity-mismatch":
            if new.identity == "ok":
                cur.stop = None           # a later run confirmed identity
        elif new_has_steps or new.identity == "ok":
            cur.stop = None               # a later run reached the host

    # notes of a provisional state (Skipped, Incomplete, none) leave with
    # it when the newer root supplies the step's state
    for col in TRACKER_STEPS + [NCCL]:
        old_v = cur.nccl if col == NCCL else cur.steps.get(col, "")
        new_v = new.nccl if col == NCCL else new.steps.get(col, "")
        if new_v and old_v not in REAL_STATES and \
                _layer_state(old_v, new_v) == new_v:
            pre = _note_prefixes(col)
            cur.notes = [n for n in cur.notes
                         if not any(n.startswith(p) for p in pre)]

    for k, v in new.steps.items():
        cur.steps[k] = _layer_state(cur.steps.get(k, ""), v)
        if v and cur.steps[k] == v:
            # the newer root supplied this state: its evidence comes too
            if k == "NVSM Health":
                cur.health_ev = new.health_ev
                cur.health_names = list(new.health_names)
            if k in new.state_notes:
                cur.state_notes[k] = new.state_notes[k]
            else:
                cur.state_notes.pop(k, None)
    cur.nccl = _layer_state(cur.nccl, new.nccl)
    if new.nccl and cur.nccl == new.nccl:
        cur.nccl_ev = new.nccl_ev
        cur.nccl_waived = new.nccl_waived   # a waiver never crosses roots
        if NCCL in new.state_notes:
            cur.state_notes[NCCL] = new.state_notes[NCCL]
        else:
            cur.state_notes.pop(NCCL, None)
    if new.serial:
        cur.serial = new.serial
    cur.lan_ip = new.lan_ip or cur.lan_ip
    cur.bmc_ip = new.bmc_ip or cur.bmc_ip
    for n in new.notes:
        if n not in cur.notes:
            cur.notes.append(n)
    for p in new.provenance:
        if p not in cur.provenance:
            cur.provenance.append(p)
    for x in new.incomplete:
        if x not in cur.incomplete:
            cur.incomplete.append(x)
    if new.profile:
        if cur.profile and cur.profile != new.profile:
            cur.incomplete.append("evidence names both dgx and hgx profiles")
        cur.profile = cur.profile or new.profile
    # once any run required NCCL for the host, the row requires it: a
    # later run without the flag does not lift the requirement
    cur.nccl_required = cur.nccl_required or new.nccl_required
    if new.identity is not None:
        cur.identity = new.identity
    if new.floor and SEVERITY[new.floor] > SEVERITY.get(cur.floor, -1):
        cur.floor = new.floor
    if new.source:
        cur.source = new.source
    if new.card_path and (new_steps >= cur_steps or not cur.card_path):
        cur.card_path = new.card_path
        cur.card_verdict = new.card_verdict
    cur.overall = ""        # merged evidence — finalize() recomputes


DIMM_CHECK = "Verify DIMM part number"


def ki1_applies(r):
    """KI-1 (NVSM 'Verify DIMM part number' false positive) applies only
    when the health evidence of the same run shows: the step exited 0
    (it failed on content, not on exit/timeout), every structured
    Unhealthy check line is the DIMM part-number check, and the log's own
    'N out of M checks are unhealthy' count agrees with that number."""
    ev = r.health_ev
    if r.steps.get("NVSM Health") != "Failed" or not ev:
        return False
    names = ev["names"]
    return (bool(names) and all(n == DIMM_CHECK for n in names)
            and ev["exit"] == 0
            and ev["summary_unhealthy"] == len(names))


def ki2_applies(r):
    """KI-2 (nccl-tests not provisioned) applies only to the signature
    documented in REPORT-B: the NCCL state is Failed; the NCCL step's own
    trailer note (not any note on the row) carries the runner's
    'not installed: ... (nccl-tests partially provisioned)'
    classification; and the same run's NCCL block proves no test ran —
    no result line in any section, every test exited non-zero, and every
    test's binary is reported missing by mpirun ('Executable: <name>_perf')
    or exited 127. A test that ran and failed can never match.
    Legacy logs only (dgx-vitals before v2.0): since v2.0 the runner
    classifies a non-launch itself and never records it as a failure."""
    ev = r.nccl_ev
    return (r.nccl == "Failed" and ev is not None
            and _is_legacy_runner(ev.get("version"))
            and "not installed:" in ev["note"]
            and "partially provisioned" in ev["note"]
            and ev["non_launch"])


def adjudicate(hosts, manual):
    """Apply the known-issue rules plus manual adjudications. Returns a
    report dict for the sheet footer. Nothing is deleted: a ruled false
    positive becomes Warned (or Skipped for never-ran NCCL), with the
    reason appended to the row's notes and the ruling, with the facts
    drawn from that host's own logs, written in the Known Issues block."""
    report = {"KI-1": [], "KI-2": [], "manual": [], "unmatched": [],
              "ki1_facts": [], "ki2_facts": []}
    for name in sorted(hosts):
        r = hosts[name]
        if ki1_applies(r):
            r.steps["NVSM Health"] = "Warned"
            r.card_dirty = r.adjudicated = True
            r.notes.append(
                "KI-1: 'Verify DIMM part number' is the only unhealthy "
                "check — ruled a known NVSM false positive, step warned, "
                "not failed (see Known Issues)")
            r.overall = ""
            report["KI-1"].append(name)
            report["ki1_facts"].append((name, r.health_ev))
        if ki2_applies(r):
            r.nccl = "Skipped"
            r.card_dirty = r.adjudicated = True
            missing = ", ".join(r.nccl_ev["not_found"]) or "all tests"
            r.notes.append(
                f"KI-2: NCCL not tested — mpirun could not find the "
                f"nccl-tests binaries ({missing}); no test ran "
                "(see Known Issues)")
            r.overall = ""
            report["KI-2"].append(name)
            report["ki2_facts"].append((name, r.nccl_ev))
    for host, step, state, note in manual:
        r = hosts.get(host)
        if r is None or (step != NCCL and step not in TRACKER_STEPS) \
                or state not in ("Complete", "Failed", "Skipped", "Warned"):
            report["unmatched"].append((host, step, state, note))
            continue
        if step == NCCL:
            r.nccl = state
        else:
            r.steps[step] = state
        r.card_dirty = r.adjudicated = True
        r.notes.append(f"Manual adjudication: {step} set to {state}"
                       + (f" — {note}" if note else ""))
        r.overall = ""
        report["manual"].append((host, step, state, note))
    return report


def ki1_footer(facts):
    """KI-1 ruling text, every fact drawn from the affected hosts' logs."""
    versions = sorted({ev["version"] for _h, ev in facts if ev["version"]})
    found = sorted({f for _h, ev in facts for _s, _e, f in ev["dimm"]})
    expected = sorted({e for _h, ev in facts for _s, e, _f in ev["dimm"]})
    uniform = all(len({f for _s, _e, f in ev["dimm"]}) == 1
                  for _h, ev in facts if ev["dimm"])
    parts = [f"KI-1 — NVSM 'Verify DIMM part number' false positive "
             f"({len(facts)} host(s)): on each, 'Verify DIMM part number' "
             "is the only Unhealthy check, the health step exited 0, and "
             "the log's own unhealthy count agrees."]
    if versions:
        parts.append("NVSM version(s) reported in those logs: "
                     + ", ".join(versions) + ".")
    if found:
        parts.append("Part number(s) NVSM reports found: "
                     + ", ".join(found) + "; expected per NVSM: "
                     + ", ".join(expected) + "."
                     + (" On every affected host one part number is "
                        "reported across all mismatched slots." if uniform
                        else ""))
    else:
        parts.append("The logs carry no per-slot mismatch detail.")
    parts.append("Ruled a tooling false positive: NVSM Health is marked "
                 "Warned, not Failed.")
    return " ".join(parts)


def ki2_footer(facts):
    missing = sorted({t for _h, ev in facts for t in ev["not_found"]})
    return (f"KI-2 — legacy runner logs (dgx-vitals before v2.0), "
            f"{len(facts)} host(s): that runner recorded an NCCL step that "
            "never launched as a failure. In each host's NCCL block mpirun "
            "reported it could not find the nccl-tests binaries ("
            + (", ".join(missing) or "none named") + ") and no test "
            "produced a result line, so the fabric test never ran. Marked "
            "Skipped (not tested), not Failed. Runner v2.0 and later "
            "classify this case themselves, so KI-2 never applies to their "
            "logs.")


def _nccl_cause(r):
    """Why a host has no NCCL fabric result, in plain words."""
    ev = r.nccl_ev or {}
    if _waiver_binds(r):
        return "waived by the operator's opt-out (--no-require-nccl)"
    if r.nccl == "Incomplete":
        return "ran but could not be judged"
    if ev.get("non_launch") or "not installed" in \
            (r.state_notes.get(NCCL) or ""):
        return "never launched — nccl-tests not installed"
    if r.nccl == "Skipped":
        return "skipped"
    return "no NCCL result"


def nccl_not_tested(order, hosts, require_nccl=False):
    """[(cause, [hosts])] for every row with no NCCL fabric result where
    one was expected or an NCCL step was attempted. DGX rows whose run
    neither required nor attempted NCCL are not listed (NVSM covers the
    fabric there)."""
    groups = {}
    for name in order:
        r = hosts[name]
        if not r.source or r.nccl in REAL_STATES or r.stop and \
                not _has_steps(r):
            continue
        expected = NCCL in expected_steps(r.profile or "dgx",
                                          require_nccl or r.nccl_required)
        if not r.nccl and not expected:
            continue
        groups.setdefault(_nccl_cause(r), []).append(name)
    return sorted(groups.items())


def nccl_footer(groups):
    n = sum(len(h) for _c, h in groups)
    parts = [f"{cause} ({len(h)}): " + ", ".join(h) for cause, h in groups]
    return (f"NCCL not tested ({n} host(s)) — no NCCL fabric result exists "
            "for these hosts: " + "; ".join(parts) + ". Where the profile "
            "(hgx) or the run (--require-nccl) required NCCL, the host is "
            "INCOMPLETE; on a DGX host whose run did not require it, NVSM "
            "covers the fabric and the host is judged without it. DGX hosts "
            "whose run did not attempt NCCL are not listed.")


def read_adjudications(path):
    """Manual adjudications CSV (header hostname,step,state,note)."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            host = (row.get("hostname") or "").strip()
            step = (row.get("step") or "").strip()
            state = (row.get("state") or "").strip()
            note = (row.get("note") or "").strip()
            if host and step and state:
                rows.append((host, step, state, note))
    return rows

# ---------------------------------------------------------------------------
# Adjudicated health cards
# ---------------------------------------------------------------------------
# The card in a run root is raw evidence and is never modified. Where an
# adjudication, or the tracker's own reading of the evidence, changed a
# host's verdict from the one its run recorded, the sheet embeds a
# re-rendered card drawn from the tracker's step states - same design as
# the runner's card (same font and PNG writer, imported from
# dgx-vitals.py), plus a WARN state and a footer naming why, so the
# correction is visible on the artifact itself. Re-renders are also
# exported to <out>_cards/ beside the sheet.

_CARD_LABEL = {"DGX Release": "DGX Release",
               "NVSM Health": "NVSM Show Health",
               "NVIDIA-SMI Query": "NVIDIA-SMI Query",
               "NVLink Topology": "NVLink Topology",
               "NVSM Stress Test": "NVSM Stress Test",
               "NVSM Dump Health": "NVSM Dump Health"}
_CARD_ORDER = ["DGX Release", "NVSM Health", "NVIDIA-SMI Query",
               "NVLink Topology", "NCCL Tests", "NVSM Stress Test",
               "NVSM Dump Health"]
_CARD_STATE = {"Complete": "pass", "Failed": "fail",
               "Skipped": "skip", "Warned": "warn",
               "Incomplete": "incomplete"}
_CARD_OVERALL = {"HEALTHY": "pass", "HEALTHY WITH WARNINGS": "warn",
                 "NOT HEALTHY": "fail", "NO DATA": "skip",
                 "INCOMPLETE": "incomplete", "UNREACHABLE": "unreachable"}
CARD_FOOTER_ADJUDICATED = "ADJUDICATED - SEE KNOWN ISSUES"
CARD_FOOTER_REJUDGED = "TRACKER VERDICT - SEE NOTES"
_WARN_RGB = (255, 213, 79)

_vitals_mod = _V               # loaded at import (shared vocabulary)
_vitals_mod_tried = True


def _load_vitals_mod():
    """Import dgx-vitals.py (the sibling of this file) for its card font
    and PNG writer. Returns None when unavailable - callers fall back to
    the raw cards."""
    global _vitals_mod, _vitals_mod_tried
    if _vitals_mod_tried:
        return _vitals_mod
    _vitals_mod_tried = True
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "dgx-vitals.py")
    try:
        spec = importlib.util.spec_from_file_location("dgx_vitals", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _vitals_mod = mod
    except Exception:
        _vitals_mod = None
    return _vitals_mod


def render_adjudicated_card(mod, path, r, footer=CARD_FOOTER_ADJUDICATED):
    """Draw r's health card from its adjudicated states, mirroring
    dgx-vitals.render_health_card's layout with a WARN state added.
    The timestamp is the raw card's mtime: the runner writes the card
    as the run finishes, so it stands in for the finish time."""
    C = dict(mod.CARD_COLORS)
    C["warn"] = _WARN_RGB
    when = datetime.datetime.fromtimestamp(
        os.path.getmtime(r.card_path),
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    release = ""
    for n in r.notes:
        if n.startswith("Release: "):
            release = n[len("Release: "):]
            break
    lines = [(r.hostname.upper(), C["fg"], 4),
             ("DGX VITALS  " + when, C["dim"], 2)]
    if release:
        lines.append((release.upper()[:64], C["dim"], 2))
    lines.append(("", C["fg"], 2))
    prefix = {"pass": "PASS  ", "fail": "FAIL  ", "skip": "SKIP  ",
              "warn": "WARN  ", "incomplete": "INCOMPLETE  "}
    for step in _CARD_ORDER:
        val = r.nccl if step == "NCCL Tests" else r.steps.get(step, "")
        state = _CARD_STATE.get(val)
        if not state:
            continue
        label = "NCCL Tests" if step == "NCCL Tests" \
            else _CARD_LABEL[step]
        lines.append((prefix[state] + label.upper(), C[state], 3))
    lines.append(("", C["fg"], 2))
    lines.append(("OVERALL: " + r.overall.upper(),
                  C[_CARD_OVERALL.get(r.overall, "skip")], 4))
    lines.append(("", C["fg"], 2))
    lines.append((footer, C["dim"], 2))

    def text_w(text, scale):
        return len(text) * 5 * scale - scale if text else 0

    pad = 24
    width = max(text_w(t, s) for t, _, s in lines) + pad * 2
    line_h = [(7 * s + 10) if t else 14 for t, _, s in lines]
    height = sum(line_h) + pad * 2
    pixels = [[C["bg"]] * width for _ in range(height)]
    y = pad
    for (text, color, scale), lh in zip(lines, line_h):
        if text:
            x = pad
            for ch in text.upper():
                glyph = mod.FONT.get(ch, mod.FONT["?"])
                for rr in range(5):
                    bits = glyph[rr]
                    for cbit in range(4):
                        if bits & (1 << (3 - cbit)):
                            for dy in range(scale):
                                for dx in range(scale):
                                    pixels[y + rr * scale + dy] \
                                        [x + cbit * scale + dx] = color
                x += 5 * scale
        y += lh
    mod.write_png(path, width, height, pixels)



def provenance_line(sources):
    """Footer provenance, built only from the build's own inputs.
    sources: (vitals roots, bundle root or None), as given on the command
    line; each is named by its basename (the same name the ferry bundle
    files it under), so no operator path reaches a customer-facing sheet
    and no name appears that the inputs did not carry."""
    if not sources:
        return ""
    vitals_roots, bundles_root = sources

    def base(p):
        return os.path.basename(os.path.normpath(p))

    parts = []
    if vitals_roots:
        parts.append("dgx-vitals root(s) "
                     + ", ".join(base(p) for p in vitals_roots))
    if bundles_root:
        parts.append("bundle root " + base(bundles_root))
    if not parts:
        return ""
    return ("Built from (see the Source column per row): "
            + "; ".join(parts) + ".")


QUARANTINE_HEADER = "QUARANTINE — INPUT NOT USED FOR ANY VERDICT"
KNOWN_ISSUES_HEADER = "KNOWN ISSUES & ADJUDICATIONS"


def card_ref(path):
    """A card's location for the CSV: <root>/<host>/<file>, never an
    operator's absolute path."""
    if not path:
        return ""
    parts = os.path.normpath(path).split(os.sep)
    return "/".join(parts[-3:])


def prepare_cards(order, hosts, out_path, raw_cards=False):
    """Decide each row's embedded card: the raw run card, or a re-render
    from the tracker's states where an adjudication or the tracker's own
    verdict differs from the one the card's run recorded. Returns the
    count of re-renders; sets r.embed_path."""
    cards_dir = os.path.splitext(out_path)[0] + "_cards"
    vitals_mod = None
    n = 0
    for name in order:
        r = hosts[name]
        r.embed_path = r.card_path
        if not r.card_path or raw_cards:
            continue
        rejudged = bool(r.card_verdict) and r.overall != r.card_verdict
        if not (r.card_dirty or rejudged):
            continue
        if vitals_mod is None:
            vitals_mod = _load_vitals_mod()
        if vitals_mod is None:
            r.notes.append("card: re-render unavailable (dgx-vitals.py not "
                           "beside the tracker) — raw card embedded")
            continue
        os.makedirs(cards_dir, exist_ok=True)
        suffix = "adjudicated" if r.adjudicated else "tracker"
        path = os.path.join(cards_dir, f"{name}-{suffix}.png")
        try:
            render_adjudicated_card(
                vitals_mod, path, r,
                CARD_FOOTER_ADJUDICATED if r.adjudicated
                else CARD_FOOTER_REJUDGED)
            r.embed_path = path
            n += 1
        except Exception as e:
            r.notes.append(f"card: re-render failed ({e}) — raw card "
                           "embedded")
    return n


def table_rows(order, hosts):
    """The data rows exactly as both the sheet and the CSV carry them."""
    rows = []
    for item, name in enumerate(order, 1):
        r = hosts[name]
        row = [item, name, r.serial, r.bmc_ip, r.lan_ip]
        row += [r.steps.get(s, "") for s in TRACKER_STEPS]
        row += [r.pct if r.pct is not None else "", r.overall, r.nccl,
                r.source or "—", " + ".join(r.provenance) or "—",
                row_notes(r), ""]
        rows.append(row)
    return rows


def quarantine_rows(ingest):
    out = []
    for i, q in enumerate(ingest.quarantine if ingest else [], 1):
        row = [f"Q{i}", q["host"] or "", "", "", ""]
        row += [""] * len(TRACKER_STEPS)
        row += ["", "QUARANTINED", "", q["source"], q["provenance"],
                q["reason"], ""]
        out.append(row)
    return out


def write_csv(path, order, hosts, ingest=None):
    """The sheet's rows as CSV (same columns, same verdicts); the Health
    Card column holds the embedded card's <root>/<host>/<file> path.
    Quarantined input follows as rows whose Overall is QUARANTINED."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for name, row in zip(order, table_rows(order, hosts)):
            r = hosts[name]
            w.writerow(row[:-1] + [card_ref(getattr(r, "embed_path", None)
                                            or r.card_path)])
        for row in quarantine_rows(ingest):
            w.writerow(row)


def build_sheet(order, hosts, out_path, title, adj_report=None,
                raw_cards=False, sources=None, ingest=None,
                require_nccl=False):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo

    for name in order:          # idempotent; main() has already done it
        finalize(hosts[name], require_nccl)
    if any(not hasattr(hosts[n], "embed_path") for n in order):
        prepare_cards(order, hosts, out_path, raw_cards)
    n_adjudicated = sum(1 for n in order if hosts[n].embed_path
                        and hosts[n].embed_path != hosts[n].card_path)

    wb = Workbook()
    ws = wb.active
    ws.title = "Validation Tracker"
    if title:
        ws.sheet_properties.tabColor = "1F4E79"

    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    hdr_font = Font(bold=True, color="FFFFFF")
    ok_fill = PatternFill("solid", fgColor="C6EFCE")
    ok_font = Font(color="006100")
    bad_fill = PatternFill("solid", fgColor="FFC7CE")
    bad_font = Font(color="9C0006")
    warn_fill = PatternFill("solid", fgColor="FFEB9C")
    warn_font = Font(color="9C6500")
    inc_fill = PatternFill("solid", fgColor="D9D2E9")
    inc_font = Font(bold=True, color="3F1F5C")
    unr_fill = PatternFill("solid", fgColor="FCE4D6")
    unr_font = Font(bold=True, color="843C0C")
    dim_font = Font(color="808080", italic=True)

    ws.append([title] if title else ["Validation Tracker"])
    ws.merge_cells(start_row=1, start_column=1, end_row=1,
                   end_column=len(COLUMNS))
    ws.cell(row=1, column=1).font = Font(bold=True, size=13, color="1F4E79")
    ws.append(COLUMNS)
    for c in range(1, len(COLUMNS) + 1):
        cell = ws.cell(row=2, column=c)
        cell.fill = hdr_fill
        cell.font = hdr_font
        cell.alignment = Alignment(vertical="center", wrap_text=True)

    card_col = COLUMNS.index("Health Card") + 1
    n_embedded = 0
    for name, row in zip(order, table_rows(order, hosts)):
        r = hosts[name]
        ws.append(row)
        rn = ws.max_row
        for cname in TRACKER_STEPS + [NCCL]:
            cell = ws.cell(row=rn, column=COLUMNS.index(cname) + 1)
            if cell.value == "Complete":
                cell.fill, cell.font = ok_fill, ok_font
            elif cell.value == "Failed":
                cell.fill, cell.font = bad_fill, bad_font
            elif cell.value == "Warned":
                cell.fill, cell.font = warn_fill, warn_font
            elif cell.value == "Incomplete":
                cell.fill, cell.font = inc_fill, inc_font
            elif cell.value == "Skipped":
                cell.font = dim_font
        ocell = ws.cell(row=rn, column=COLUMNS.index("Overall") + 1)
        if r.overall == "HEALTHY":
            ocell.fill, ocell.font = ok_fill, ok_font
        elif r.overall == "NOT HEALTHY":
            ocell.fill, ocell.font = bad_fill, bad_font
        elif r.overall == "HEALTHY WITH WARNINGS":
            ocell.fill, ocell.font = warn_fill, warn_font
        elif r.overall == "INCOMPLETE":
            ocell.fill, ocell.font = inc_fill, inc_font
        elif r.overall == UNREACHABLE:
            ocell.fill, ocell.font = unr_fill, unr_font
        else:                   # NO DATA, SKIPPED: no evidence
            ocell.font = dim_font
        ws.cell(row=rn, column=COLUMNS.index("Notes") + 1).alignment = \
            Alignment(wrap_text=True, vertical="top")
        embed_path = r.embed_path
        if embed_path:
            from openpyxl.drawing.image import Image as XLImage
            from PIL import Image as PILImage
            with PILImage.open(embed_path) as im:
                w0, h0 = im.size
            img = XLImage(embed_path)
            img.width = 288
            img.height = round(288 * h0 / w0)
            ws.row_dimensions[rn].height = img.height * 0.75 + 6
            ws.add_image(img, ws.cell(row=rn, column=card_col).coordinate)
            n_embedded += 1

    last_data_row = ws.max_row

    def block(header, lines):
        ws.append([])
        ws.append([header])
        hr = ws.max_row
        ws.merge_cells(start_row=hr, start_column=1, end_row=hr,
                       end_column=len(COLUMNS))
        ws.cell(row=hr, column=1).font = Font(bold=True, size=12,
                                              color="1F4E79")
        for text in lines:
            ws.append([text])
            rn = ws.max_row
            ws.merge_cells(start_row=rn, start_column=1, end_row=rn,
                           end_column=len(COLUMNS))
            c = ws.cell(row=rn, column=1)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[rn].height = \
                13.5 * max(1, -(-len(text) // 150))

    if ingest and (ingest.quarantine or ingest.problems):
        qlines = [f"{q['host'] or '?'} — {q['source']} "
                  f"[{q['provenance']}]: {q['reason']}"
                  for q in ingest.quarantine]
        qlines += [f"Input error: {p}" for p in ingest.problems]
        block(QUARANTINE_HEADER, qlines)

    untested = nccl_not_tested(order, hosts, require_nccl)
    if untested or adj_report and (
            adj_report["KI-1"] or adj_report["KI-2"]
            or adj_report["manual"] or adj_report["unmatched"]):
        adj_report = adj_report or {"KI-1": [], "KI-2": [], "manual": [],
                                    "unmatched": []}
        lines = []
        if adj_report["KI-1"]:
            lines.append(ki1_footer(adj_report["ki1_facts"]))
        if adj_report["KI-2"]:
            lines.append(ki2_footer(adj_report["ki2_facts"]))
        if untested:
            lines.append(nccl_footer(untested))
        for host, step, state, note in adj_report["manual"]:
            lines.append(f"Manual adjudication — {host}: {step} set to "
                         f"{state}." + (f" {note}" if note else ""))
        for host, step, state, note in adj_report["unmatched"]:
            lines.append(f"NOT APPLIED — adjudication row for {host} "
                         f"({step} -> {state}) matched no host/step.")
        prov = provenance_line(sources)
        if prov:
            lines.append(prov)
        block(KNOWN_ISSUES_HEADER, lines)

    widths = {"Item": 6, "Node Hostname": 34, "Node Serial Number": 18,
              "BMC IP": 15, "LAN IP": 15, "DGX Release": 12,
              "NVSM Health": 12, "NVIDIA-SMI Query": 13,
              "NVLink Topology": 13, "NVSM Stress Test": 13,
              "NVSM Dump Health": 13, "% Complete": 10, "Overall": 13,
              "NCCL Tests": 11, "Source": 20, "Provenance": 30,
              "Notes": 60, "Health Card": 42}
    for cname, wdt in widths.items():
        ws.column_dimensions[get_column_letter(
            COLUMNS.index(cname) + 1)].width = wdt
    ws.freeze_panes = "C3"
    ws.auto_filter.ref = f"A2:{get_column_letter(len(COLUMNS))}{last_data_row}"

    rd = wb.create_sheet("README")
    rd.column_dimensions["A"].width = 110
    for line in [
        "Built by tracker_build.py v" + VERSION + " (dgx-vitals toolset); it",
        "reads dgx-vitals 2.0 verdicts as well as logs from older runners.",
        "",
        "Sources: each row's Source column names the pipeline that produced",
        "it — dgx-vitals (release, NVSM health, smi, topo, NCCL where run,",
        "stress, dump, plus the embedded health card) or a cdw-validation",
        "bundle harvested from the host. dgx-vitals wins where both exist.",
        "Provenance names the producer and version of the evidence and how",
        "the row was built: from summary.csv, or rebuilt-from-fulllog where",
        "summary.csv was missing or older than the host's log.",
        "",
        "Overall:",
        "  HEALTHY — every step the host's profile expects ran and passed on",
        "    its own evidence.",
        "  HEALTHY WITH WARNINGS — as HEALTHY, except that a step's failure",
        "    is a ruled false positive (Warned; see Known Issues).",
        "  NOT HEALTHY — something was measured and it failed. A Failed NCCL",
        "    makes the host NOT HEALTHY.",
        "  INCOMPLETE — evidence is missing: a step the profile or the run",
        "    requires was skipped, ran but could not be judged (Incomplete),",
        "    or has no result; a log was cut short or interrupted; the",
        "    host's identity could not be confirmed; or the evidence does not",
        "    establish the profile. A test waived by the operator's opt-out",
        "    is missing evidence too, and the row names the waiver.",
        "    INCOMPLETE is never healthy; the Notes say what is missing and",
        "    what clears it.",
        "  UNREACHABLE — the runner could not reach the host (step zero), so",
        "    no step ran and nothing is known about its health. A later run",
        "    that reaches the host replaces this row.",
        "  NO DATA / SKIPPED — no step results: NO DATA is a listed host with",
        "    no evidence; SKIPPED means the runner left the host alone",
        "    because its output folder already held a finished log.",
        "",
        "Step cells: Complete; Failed; Warned (a ruled false positive — it",
        "counts as complete); Skipped (the step did not run: its tool is not",
        "installed, nvsm refused to stress over uncleared alerts, or the",
        "operator waived it); Incomplete (it ran but its output could not",
        "establish a result). Skipped and Incomplete count as not passed.",
        "",
        "% Complete counts the profile's tracker steps (six on DGX); a",
        "skipped, incomplete or missing step stays in the denominator. NCCL",
        "is reported separately and never counted in the fraction. Overall",
        "does weigh NCCL: it is required on HGX, and on DGX when the run used",
        "--require-nccl (recorded in its log) or this sheet was built with",
        "--require-nccl; a required NCCL that did not run makes the host",
        "INCOMPLETE. On DGX otherwise NVSM covers the fabric and a host",
        "without NCCL is judged on the other steps. The 'NCCL not tested'",
        "line under Known Issues lists every host without an NCCL result.",
        "",
        "Where summary.csv and a host's log disagree, or a run's own",
        "Overall disagrees with its step results, the stricter result is",
        "kept and the row's Notes name the disagreement.",
        "",
        "Bundle NVSM Health is judged from the health log's check lines and",
        "its own unhealthy count, not the command exit code (nvsm exits 0",
        "with unhealthy checks present). Blank cells mean the evidence does",
        "not carry the value — nothing in this sheet is guessed.",
        "",
        "Adjudications: a step marked Warned ran, but its failure is a",
        "ruled false positive — the ruling and its evidence are written in",
        "the Known Issues & Adjudications block at the bottom of the",
        "tracker sheet. Warned steps count as complete; a host whose only",
        "failures are warnings reads HEALTHY WITH WARNINGS.",
        "Where an adjudication or the tracker's reading of the evidence",
        "changes a host's verdict, the embedded health card is re-rendered",
        "from the tracker's states (footer: ADJUDICATED or TRACKER VERDICT)",
        "and exported beside the sheet in <out>_cards/; the raw card in the",
        "run root is never modified.",
        "Input that could not be identified or read is listed in the",
        "Quarantine block and used for no verdict.",
        "Multiple dgx-vitals roots merge per step, in the order given: a",
        "partial re-run (--only ...) overrides only the steps it re-ran; a",
        "real result replaces a Skipped or Incomplete one from any root; an",
        "UNREACHABLE or unconfirmed-identity attempt never overrides",
        "results and is replaced by any later run that reaches the host;",
        "an identity mismatch stands until a later run's own identity check",
        "confirms the host.",
    ]:
        rd.append([line])
    wb.save(out_path)
    return n_embedded, n_adjudicated



def make_bundle(out_path, sheet_path, vitals_roots, bundle_artifacts):
    with tarfile.open(out_path, "w:gz") as tf:
        tf.add(sheet_path, arcname=os.path.basename(sheet_path))
        for root in vitals_roots:
            tf.add(root, arcname=os.path.join(
                "vitals", os.path.basename(os.path.normpath(root))))
        for art in bundle_artifacts:
            tf.add(art, arcname=os.path.join("bundles", os.path.basename(art)))
    digest = hashlib.sha256()
    with open(out_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    with open(out_path + ".sha256", "w") as f:
        f.write(f"{digest.hexdigest()}  {os.path.basename(out_path)}\n")
    return digest.hexdigest()


# --- end-of-build banner -------------------------------------------------
# The dgx-vitals --watch block-letter moment, speaking tracker: the tally
# is the sheet's own adjudicated verdicts over all consolidated inputs.
# Font and rainbow lifted from dgx-vitals.py so the two tools match.

_FONT = {
    "0": (" *** ", "*   *", "*  **", "* * *", "**  *", "*   *", " *** "),
    "1": ("  *  ", " **  ", "* *  ", "  *  ", "  *  ", "  *  ", "*****"),
    "2": (" *** ", "*   *", "    *", "   * ", "  *  ", " *   ", "*****"),
    "3": (" *** ", "*   *", "    *", "  ** ", "    *", "*   *", " *** "),
    "4": ("   * ", "  ** ", " * * ", "*  * ", "*****", "   * ", "   * "),
    "5": ("*****", "*    ", "**** ", "    *", "    *", "*   *", " *** "),
    "6": (" *** ", "*   *", "*    ", "**** ", "*   *", "*   *", " *** "),
    "7": ("*****", "    *", "   * ", "  *  ", " *   ", " *   ", " *   "),
    "8": (" *** ", "*   *", "*   *", " *** ", "*   *", "*   *", " *** "),
    "9": (" *** ", "*   *", "*   *", " ****", "    *", "*   *", " *** "),
    "/": ("    *", "   * ", "   * ", "  *  ", " *   ", " *   ", "*    "),
    " ": ("   ", "   ", "   ", "   ", "   ", "   ", "   "),
    "W": ("*   *", "*   *", "*   *", "* * *", "* * *", "** **", "*   *"),
    "A": (" *** ", "*   *", "*   *", "*****", "*   *", "*   *", "*   *"),
    "V": ("*   *", "*   *", "*   *", "*   *", "*   *", " * * ", "  *  "),
    "E": ("*****", "*    ", "*    ", "**** ", "*    ", "*    ", "*****"),
    "D": ("**** ", "*   *", "*   *", "*   *", "*   *", "*   *", "**** "),
    "O": (" *** ", "*   *", "*   *", "*   *", "*   *", "*   *", " *** "),
    "N": ("*   *", "**  *", "* * *", "*  **", "*   *", "*   *", "*   *"),
}


def render_banner(text, scale=1):
    """Render text in block letters ('*' pixels). Scale > 1 doubles the
    pixels each way — the sed/awk doubler trick, built in."""
    rows = [""] * 7
    for ch in text.upper():
        glyph = _FONT.get(ch, _FONT[" "])
        for i in range(7):
            rows[i] += glyph[i] + " "
    if scale > 1:
        big = []
        for row in rows:
            doubled = "".join(c * scale for c in row)
            big.extend([doubled] * scale)
        rows = big
    return "\n".join(r.rstrip() for r in rows)


def _rainbow(block):
    """lolcat-style: paint each row of a text block a hue-stepped color."""
    import colorsys
    out = []
    for i, ln in enumerate(block.splitlines()):
        r, g, b = colorsys.hsv_to_rgb((i * 14 % 360) / 360.0, 0.85, 1.0)
        out.append(f"\033[38;2;{int(r * 255)};{int(g * 255)};"
                   f"{int(b * 255)}m{ln}\033[0m")
    return "\n".join(out)



BANNER_VERDICTS = ("HEALTHY", "HEALTHY WITH WARNINGS", "NOT HEALTHY",
                   "INCOMPLETE", "UNREACHABLE", "NO DATA")


def verdict_tally(order, hosts):
    """Every row lands in exactly one bucket; labels outside the verdict
    set (e.g. SKIPPED) are counted under 'other', never dropped."""
    tally = {k: 0 for k in BANNER_VERDICTS}
    tally["other"] = 0
    for n in order:
        v = hosts[n].overall
        tally[v if v in tally else "other"] += 1
    return tally


def print_banner(order, hosts, n_quarantined=0):
    """End-of-build tally in --watch style: big {with results} / {rows},
    the plain-language count, then the verdict tally over every row.
    When every row carries results, close with the DONE banner."""
    total = len(order)
    n_data = sum(1 for n in order if hosts[n].source)
    tally = verdict_tally(order, hosts)
    tty = sys.stdout.isatty()
    block = render_banner(f"{n_data} / {total}", scale=2)
    print("\n" + (_rainbow(block) if tty else block))
    print(f"\n{n_data} of {total} hosts with results")
    line = (f"{tally['HEALTHY']} HEALTHY, "
            f"{tally['HEALTHY WITH WARNINGS']} HEALTHY WITH WARNINGS, "
            f"{tally['NOT HEALTHY']} NOT HEALTHY, "
            f"{tally['INCOMPLETE']} INCOMPLETE")
    if tally["UNREACHABLE"]:
        line += f", {tally['UNREACHABLE']} UNREACHABLE"
    if tally["NO DATA"]:
        line += f", {tally['NO DATA']} NO DATA"
    if tally["other"]:
        line += f", {tally['other']} without step results (SKIPPED)"
    print(line)
    if n_quarantined:
        print(f"{n_quarantined} input(s) QUARANTINED — see the sheet")
    if total and n_data == total:
        done_block = render_banner("DONE", scale=1)
        print("\n" + (_rainbow(done_block) if tty else done_block))


EXIT_OK, EXIT_TOOL, EXIT_NOT_HEALTHY = 0, 1, 2
EXIT_HELP = """exit codes:
  0  every row HEALTHY (or HEALTHY WITH WARNINGS) and complete; every
     input read in full
  2  any row NOT HEALTHY, INCOMPLETE or UNREACHABLE, or without evidence
     (NO DATA, SKIPPED)
  1  the tool failed or could not fully read its input (bad argument,
     missing/unreadable root, unreadable or malformed fleet CSV, corrupt
     or unidentifiable bundle, quarantined input, unmatched adjudication,
     openpyxl/Pillow missing, sheet not written); 1 wins over 2. No path
     exits 0 on input it could not fully read.

tracker_build.py v""" + VERSION + " (dgx-vitals 2.0 verdict contract)"


class _Parser(argparse.ArgumentParser):
    def error(self, message):           # bad input is exit 1, not 2
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(EXIT_TOOL)


def exit_code(order, hosts, ingest, report):
    if ingest.problems or ingest.quarantine or report["unmatched"] \
            or not order:
        return EXIT_TOOL
    if all(hosts[n].overall in HEALTHY_VERDICTS for n in order):
        return EXIT_OK
    return EXIT_NOT_HEALTHY


def main(argv=None):
    ap = _Parser(
        description="Build a tracker-shaped validation spreadsheet (and a "
                    "CSV of the same rows) from dgx-vitals output roots "
                    "and/or cdw-validation bundles.",
        epilog=EXIT_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vitals", action="extend", nargs="+", default=[],
                    metavar="DIR",
                    help="dgx-vitals --out root(s); repeatable, and one flag "
                         "takes several (a shell glob works). Order matters: "
                         "later roots layer over earlier ones per step, so "
                         "give them oldest first")
    ap.add_argument("--bundles", metavar="DIR",
                    help="harvest root of cdw-validation bundles")
    ap.add_argument("--hosts", metavar="CSV", help="fleet list (row order, IPs)")
    ap.add_argument("--out", default="validation-tracker.xlsx",
                    help="sheet path; the CSV is written beside it "
                         "with a .csv extension")
    ap.add_argument("--bundle-out", metavar="FILE.tar.gz",
                    help="also write a ferry bundle: sheet + source artifacts")
    ap.add_argument("--title", default="",
                    help="sheet title line (e.g. the engagement name)")
    ap.add_argument("--adjudications", metavar="CSV",
                    help="manual adjudications CSV "
                         "(hostname,step,state,note)")
    ap.add_argument("--require-nccl", action="store_true",
                    help="hold dgx hosts to NCCL too (hgx always is): a "
                         "host with no NCCL result is INCOMPLETE")
    ap.add_argument("--from-fulllog", action="store_true",
                    help="rebuild every dgx-vitals host from its FULLLOG "
                         "trailers, using summary.csv only as a second "
                         "witness (stale or missing csv rows are rebuilt "
                         "this way automatically)")
    ap.add_argument("--no-banner", action="store_true",
                    help="skip the end-of-build block-letter tally banner")
    ap.add_argument("--raw-cards", action="store_true",
                    help="embed the raw run cards even where the tracker's "
                         "verdict differs from the run's")
    ap.add_argument("--version", action="version",
                    version=f"tracker_build.py v{VERSION}")
    args = ap.parse_args(argv)

    if not args.vitals and not args.bundles:
        ap.error("give at least one of --vitals / --bundles")
    try:                        # REVIEW-A F13: say so before any parsing
        import openpyxl  # noqa: F401
    except ImportError as e:
        print(f"warning: {e} — the .xlsx sheet needs openpyxl and Pillow "
              "(pip install openpyxl pillow). The build continues and "
              "writes the CSV only, then exits 1.", file=sys.stderr)

    ingest = Ingest()
    order, hosts = ([], {})
    if args.hosts:
        try:
            order, hosts = read_fleet(args.hosts)
        except (OSError, csv.Error, UnicodeError, ValueError) as e:
            ingest.problem(f"{args.hosts}: unreadable fleet CSV ({e})")

    bundle_artifacts = []
    if args.bundles:
        for name, (res, arts) in read_bundles(args.bundles, ingest).items():
            bundle_artifacts += arts
            cur = hosts.get(name)
            if cur is None:
                hosts[name] = res
                order.append(name)
            elif not cur.source:      # fleet placeholder with no results yet
                res.lan_ip, res.bmc_ip = cur.lan_ip, cur.bmc_ip
                hosts[name] = res
            else:
                merge_result(cur, res)
    for root in args.vitals:
        for name, res in read_vitals(root, ingest,
                                     args.from_fulllog).items():
            cur = hosts.get(name)
            if cur is None:
                hosts[name] = res
                order.append(name)
            else:                     # merge per step; newer root wins
                merge_result(cur, res)  # the steps it actually ran
    for name in sorted(set(hosts) - set(order)):
        order.append(name)
    for q in ingest.quarantine:       # say so on the host's own row
        r = hosts.get(q["host"])
        if r is not None:
            r.notes.append(f"Quarantined input for this host: {q['reason']}")

    manual = []
    if args.adjudications:
        try:
            manual = read_adjudications(args.adjudications)
        except (OSError, csv.Error, UnicodeError) as e:
            ingest.problem(f"{args.adjudications}: unreadable ({e})")
    report = adjudicate(hosts, manual)
    for name in order:
        finalize(hosts[name], args.require_nccl)
    prepare_cards(order, hosts, args.out, args.raw_cards)

    csv_path = os.path.splitext(args.out)[0] + ".csv"
    try:
        write_csv(csv_path, order, hosts, ingest)
    except OSError as e:
        ingest.problem(f"{csv_path}: could not write the CSV ({e})")
    try:
        n_embedded, n_adjudicated = build_sheet(
            order, hosts, args.out, args.title, report,
            raw_cards=args.raw_cards, sources=(args.vitals, args.bundles),
            ingest=ingest, require_nccl=args.require_nccl)
    except ImportError as e:
        ingest.problem(f"cannot write {args.out}: {e} "
                       "(pip install openpyxl pillow)")
        n_embedded = n_adjudicated = 0
    except OSError as e:
        ingest.problem(f"cannot write {args.out}: {e}")
        n_embedded = n_adjudicated = 0
    n_data = sum(1 for n in order if hosts[n].source)
    print(f"{args.out}: {len(order)} rows, {n_data} with results, "
          f"{n_embedded} health cards embedded"
          + (f" ({n_adjudicated} re-rendered)" if n_adjudicated else ""))
    print(f"{csv_path}: same rows as CSV")
    if report["KI-1"] or report["KI-2"] or report["manual"] \
            or report["unmatched"]:
        print(f"adjudications: KI-1 x{len(report['KI-1'])}, "
              f"KI-2 x{len(report['KI-2'])}, "
              f"manual x{len(report['manual'])}, "
              f"unmatched x{len(report['unmatched'])}")
    if args.bundle_out:
        try:
            digest = make_bundle(args.bundle_out, args.out, args.vitals,
                                 bundle_artifacts)
            print(f"{args.bundle_out}: sha256 {digest}")
        except OSError as e:
            ingest.problem(f"cannot write {args.bundle_out}: {e}")
    if not args.no_banner:
        print_banner(order, hosts, len(ingest.quarantine))
    rc = exit_code(order, hosts, ingest, report)
    print(f"exit {rc}: " + {EXIT_OK: "every row healthy and complete",
                           EXIT_NOT_HEALTHY: "rows not healthy or "
                                             "incomplete",
                           EXIT_TOOL: "input not fully read — see errors "
                                      "/ quarantine"}[rc],
          file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
