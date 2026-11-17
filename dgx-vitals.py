#!/usr/bin/env python3
"""dgx-vitals.py — DGX Vitals: DGX / HGX validation runner (Ubuntu 24.04 / DGX OS).

Runs the non-physical validation steps and produces, per host, the
standard evidence set:

    <out>/<hostname>/<hostname>-FULLLOG.txt     full transcript of every step
    <out>/<hostname>/<hostname>-<VERDICT>.png   health card ("vitals chart"),
                                                named for the host verdict:
                                                -HEALTHY, -NOT-HEALTHY,
                                                -INCOMPLETE, -UNREACHABLE,
                                                -DRY-RUN (runners before
                                                v2.0 always wrote -HEALTHY)
    <out>/<hostname>/StressTestLog*.nvsmlog     the stress log nvsm printed
                                                ("Stress Test Log: <path>"),
                                                harvested from that path
    <out>/<hostname>/nvsm-health-*.tar.*        the dump archive nvsm printed
                                                ("written to: <path>")
    <out>/summary.csv                           one row per host, tracker-shaped

Modes:
    --local                 run against this machine (on-box mode)
    --host NAME [--address A]   run against a single host over SSH
    --hosts hosts.csv       fan out over SSH to many hosts. CSV columns:
                            hostname,address[,user][,gpus][,nvlink][,serial]
                            (gpus / nvlink / serial override the profile
                            data for that host; see "Expected hardware").
                            A tracker-style hostname,lan_ip,bmc_ip fleet
                            list also works (SSH connects to lan_ip). The
                            header is optional, but a header must name its
                            column exactly "hostname"; #-comments and blank
                            lines are ignored. tracker_build.py reads its
                            --hosts file with this same parser.
    --check                 read-only preflight: is each host ready to be
                            validated, and if not, exactly why not. Checks
                            SSH reachability, identity, sudo, nvsm, each
                            nccl-tests binary by name, standing alerts, free
                            disk, and clock skew. Writes nothing, changes
                            nothing on any host, runs no test step.
    --profile dgx|hgx|auto  dgx (default): NVSM + nvidia-smi; the NCCL
                            step joins only with --require-nccl or
                            --only nccl (NVSM covers the fabric on DGX).
                            hgx: NCCL + nvidia-smi only. NVSM and
                            /etc/dgx-release are DGX-only, so on HGX the
                            NCCL suite carries the fabric proof and is
                            REQUIRED by default; --no-require-nccl is the
                            explicit opt-out, and its use is printed in the
                            verdict ("NCCL not required (operator
                            opt-out)") on the FULLLOG OVERALL line and in
                            the summary Notes. Fail closed: a waived test
                            is missing evidence, so an opt-out host whose
                            NCCL step was skipped is INCOMPLETE, never
                            HEALTHY; an NCCL test that ran and failed is
                            NOT HEALTHY with or without the opt-out.
                            auto: the step-zero probe decides per host
                            (/etc/dgx-release or the nvsm CLI means dgx;
                            neither means hgx). A failed probe is
                            UNREACHABLE, never a guessed profile.
    --watch --hosts CSV     with --out: contact nothing; render the wave
                            scoreboard from the output root until every
                            host in the CSV is done. A host is done only
                            when its FULLLOG carries its OVERALL line;
                            otherwise it is running (and where), waiting,
                            interrupted, or stalled (no FULLLOG update for
                            longer than the running step's timeout plus
                            10 minutes). When nothing is running and some
                            host is interrupted or stalled it prints WAVE
                            STOPPED and exits 2. --once prints a single
                            look.
    --dry-run               with any validation mode: execute nothing.
                            Prints the plan (exact commands, in order, per
                            host) and writes the same artifacts stamped DRY
                            RUN under <out>/dry-run/ (never at the real
                            artifact paths). Refuses an --out root that
                            already holds real FULLLOGs.

Re-runs and interrupts: a host whose FULLLOG is finished (it carries the
OVERALL line) is left alone and reported SKIPPED; --force redoes it. A
host whose FULLLOG is unfinished (an interrupted or killed run) is redone,
and the partial log is kept beside it as
<hostname>-FULLLOG.interrupted-<UTC>.txt. Ctrl-C stops a wave: no queued
host starts, no running host starts another step, and a step in flight is
cut short and recorded as INTERRUPTED with no verdict (and its process
group on the host is signalled, as after a timeout); no OVERALL line and
no card are written for that host, so the next run of the same command
redoes it. summary.csv is still written for what finished. A second
Ctrl-C exits at once.

Step zero (every real run, every host): before any test step the runner
asks the host who it is (hostname, chassis serial, platform). If the host
cannot be reached the host is UNREACHABLE; if the identity cannot be
confirmed (no hostname, unreadable serial) or does not match the hosts
entry (name, or the serial column when given), the host is stopped as
INCOMPLETE. Either way no test step runs for that host and nothing is
validated under a guessed identity.

Verdicts (per step): PASS only on positive evidence (see verdict()):
    PASS        the step's own success evidence is present
    FAIL        evidence of a fault (including a degraded link, a hollow
                stress run, a test that ran and failed)
    SKIP        the step did not run (tool absent, or nvsm refused to
                stress over uncleared alerts). A skipped REQUIRED step
                (e.g. a required NCCL suite whose binaries are not
                installed) still counts, so the host is INCOMPLETE; so
                does an hgx NCCL step skipped by the operator's opt-out
    INCOMPLETE  the output could not establish either (e.g. an
                unparseable topology matrix)
Verdicts (per host, FULLLOG "OVERALL:" and summary.csv Overall):
    HEALTHY      every counted step PASSed
    NOT HEALTHY  any step FAILed: something was measured and it failed
                 (an NCCL test that ran and failed is NOT HEALTHY)
    INCOMPLETE   no FAIL, but some required step was SKIP or INCOMPLETE,
                 or step zero could not confirm the host's identity.
                 This is the fail-closed verdict for missing evidence:
                 a required NCCL suite that never launched (nccl-tests
                 not installed) is INCOMPLETE, not NOT HEALTHY; so is
                 an hgx host whose NCCL step was skipped by the
                 operator's recorded opt-out (--no-require-nccl): the
                 waiver is on the record, the evidence is still absent
    UNREACHABLE  the step-zero probe could not reach the host; no step
                 results exist (this is not a health verdict)

Expected hardware is data, never a constant: the profile defaults are
DGX B300 = 8 GPUs and HGX B300 = 8 GPUs, both NVLink class NV18 between
every GPU pair (PROFILE_DATA). --gpus / --nvlink override them for the
whole run and the hosts CSV's gpus / nvlink columns override them per
host (4-GPU boxes, 1-2 GPU mezzanine, RTX Pro 6000 builds). The FULLLOG
header records each host's expected count and where it came from.

Exit codes (validation runs and --check; the tracker's language):
    0  every host HEALTHY (--check: every host fully ready)
    2  some host NOT HEALTHY, INCOMPLETE or UNREACHABLE
       (--check: some host not ready); also a refused dry run, and
       --watch reporting WAVE STOPPED
    1  the tool failed or was misused: bad arguments, no hosts, an
       unreadable or malformed hosts file, or the sudo canary failed
       (sudo is validated on the first reachable host before the fleet
       fans out, so a wrong password costs one host, not a wave)
  130  the operator interrupted the run (Ctrl-C); re-run to finish

Stdlib only. Python 3.10+. SSH mode shells out to the system `ssh`/`scp`
(OpenSSH ships with Windows 10/11 and macOS), so no pip installs and no WSL.
Key-based SSH auth is assumed (BatchMode); pass --no-batch to allow
interactive SSH password prompts (single-host runs).

Sudo: steps that need root, and the step-zero chassis-serial read, use
`sudo -n` (passwordless) by default. If the account needs a sudo
password, pass --sudo-password and you will be prompted once (getpass;
held in memory only, never written to disk).

Timeouts: every step runs as the leader of its own process group on the
host. When a step times out the runner signals that group (TERM, then
KILL) and records whether the group is confirmed gone. nvsm runs the
stress test inside its own service, so for the stress step the FULLLOG
also says that a service-side test cannot be confirmed stopped from the
client side.
"""

import argparse
import base64
import csv
import getpass
import os
import re
import shlex
import selectors
import shutil
import struct
import subprocess
import sys
import threading
import time
import zlib
from concurrent.futures import (FIRST_COMPLETED, ThreadPoolExecutor,
                                as_completed, wait)
from datetime import datetime, timezone

VERSION = "2.0"

_PRINT_LOCK = threading.Lock()


def _say(msg):
    with _PRINT_LOCK:
        print(msg, flush=True)

# --------------------------------------------------------------------------
# Tiny 4x5 bitmap font + pure-stdlib PNG writer (for the health card).
# The card replaces a human terminal screenshot, so it renders in a dark
# terminal style. Uppercase only; text is uppercased before rendering.
# --------------------------------------------------------------------------

FONT = {
    "A": (6, 9, 15, 9, 9), "B": (14, 9, 14, 9, 14), "C": (7, 8, 8, 8, 7),
    "D": (14, 9, 9, 9, 14), "E": (15, 8, 14, 8, 15), "F": (15, 8, 14, 8, 8),
    "G": (7, 8, 11, 9, 6), "H": (9, 9, 15, 9, 9), "I": (15, 6, 6, 6, 15),
    "J": (7, 1, 1, 9, 6), "K": (9, 10, 12, 10, 9), "L": (8, 8, 8, 8, 15),
    "M": (9, 15, 15, 9, 9), "N": (9, 13, 11, 9, 9), "O": (6, 9, 9, 9, 6),
    "P": (14, 9, 14, 8, 8), "Q": (6, 9, 9, 10, 5), "R": (14, 9, 14, 10, 9),
    "S": (7, 8, 6, 1, 14), "T": (15, 6, 6, 6, 6), "U": (9, 9, 9, 9, 6),
    "V": (9, 9, 9, 10, 4), "W": (9, 9, 15, 15, 9), "X": (9, 6, 6, 6, 9),
    "Y": (9, 9, 6, 6, 6), "Z": (15, 1, 6, 8, 15),
    "0": (6, 9, 11, 13, 6), "1": (2, 6, 2, 2, 7), "2": (14, 1, 6, 8, 15),
    "3": (14, 1, 6, 1, 14), "4": (2, 6, 10, 15, 2), "5": (15, 8, 14, 1, 14),
    "6": (7, 8, 14, 9, 6), "7": (15, 1, 2, 4, 4), "8": (6, 9, 6, 9, 6),
    "9": (6, 9, 7, 1, 14),
    " ": (0, 0, 0, 0, 0), "-": (0, 0, 15, 0, 0), ".": (0, 0, 0, 0, 6),
    ":": (0, 6, 0, 6, 0), "/": (1, 2, 4, 8, 8), "(": (2, 4, 4, 4, 2),
    ")": (4, 2, 2, 2, 4), "%": (12, 2, 6, 8, 3), "+": (0, 6, 15, 6, 0),
    "_": (0, 0, 0, 0, 15), "'": (6, 6, 0, 0, 0), ",": (0, 0, 0, 6, 4),
    "=": (0, 15, 0, 15, 0), "?": (14, 1, 6, 0, 6),
}


def _png_chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def write_png(path, width, height, pixels):
    """pixels: list of rows, each a list of (r, g, b) tuples."""
    raw = b"".join(
        b"\x00" + b"".join(struct.pack("BBB", *px) for px in row)
        for row in pixels
    )
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += _png_chunk(b"IDAT", zlib.compress(raw, 6))
    png += _png_chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


CARD_COLORS = {
    "fg": (201, 209, 217), "dim": (110, 118, 129), "bg": (13, 17, 23),
    "pass": (63, 185, 80), "fail": (248, 81, 73), "skip": (210, 168, 60),
    "incomplete": (163, 113, 247), "unreachable": (219, 109, 40),
}


def render_health_card(path, hostname, when_iso, rows, overall_text,
                       overall_state, release_line=""):
    """rows: list of (label, state) with state in pass/fail/skip/incomplete.
    overall_state likewise picks the color of the OVERALL line."""
    C = CARD_COLORS
    lines = [(hostname.upper(), C["fg"], 4),
             ("DGX VITALS  " + when_iso, C["dim"], 2)]
    if release_line:
        lines.append((release_line.upper()[:64], C["dim"], 2))
    lines.append(("", C["fg"], 2))
    prefix = {"pass": "PASS  ", "fail": "FAIL  ", "skip": "SKIP  ",
              "incomplete": "INCOMPLETE  "}
    for label, state in rows:
        lines.append((prefix[state] + label.upper(), C[state], 3))
    lines.append(("", C["fg"], 2))
    lines.append(("OVERALL: " + overall_text.upper(), C[overall_state], 4))

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
                glyph = FONT.get(ch, FONT["?"])
                for r in range(5):
                    bits = glyph[r]
                    for cbit in range(4):
                        if bits & (1 << (3 - cbit)):
                            for dy in range(scale):
                                for dx in range(scale):
                                    pixels[y + r * scale + dy][x + cbit * scale + dx] = color
                x += 5 * scale
        y += lh
    write_png(path, width, height, pixels)

# --------------------------------------------------------------------------
# Profile data and verdict states
# --------------------------------------------------------------------------

# Expected hardware per profile. Verdict logic reads these (or a per-host
# override from the hosts CSV / --gpus / --nvlink), never a literal.
# "nvlink" is the set of acceptable `nvidia-smi topo -m` cell classes for
# every off-diagonal GPU<->GPU pair (comma-separated when several).
PROFILE_DATA = {
    "dgx": {"gpus": 8, "nvlink": "NV18", "desc": "DGX B300"},
    "hgx": {"gpus": 8, "nvlink": "NV18", "desc": "HGX B300"},
}

PASS, FAIL, SKIP, INCOMPLETE, DRY = "PASS", "FAIL", "SKIP", "INCOMPLETE", "DRY"

# A genuine nvsm stress-test takes ~30 min (corpus: 1827-1858 s). A run
# shorter than this did not stress the host, whatever it printed.
STRESS_FLOOR_S = 1500
STRESS_COMPONENTS = ("GPU", "CPU", "Memory", "Storage", "PCIe")

NCCL_OPT_OUT_NOTE = "NCCL not required (operator opt-out)"

# --------------------------------------------------------------------------
# Shared verdict vocabulary (the runner -> tracker contract)
# --------------------------------------------------------------------------
# tracker_build.py imports every name in this block from this file and
# keeps no copy of its own, so the two tools cannot drift apart again
# (ANALYSIS-TRACKER-FOLLOWUPS item 3). Each word is one the runner prints
# and the tracker parses; tests/test_d1_contract.py drives the real runner
# into the real tracker to pin them.

# Host verdicts: the FULLLOG "OVERALL:" word and the summary.csv Overall.
# HEALTHY WITH WARNINGS is the tracker's adjudicated verdict (a ruled
# false positive); the runner itself never prints it, but both tools
# recognise it so neither ever calls it "not a recognized verdict".
HOST_VERDICTS = ("HEALTHY", "HEALTHY WITH WARNINGS", "INCOMPLETE",
                 "NOT HEALTHY", "UNREACHABLE")
# Labels that are not health verdicts: a dry run, a host the runner left
# alone because its root already holds a finished FULLLOG, and a run the
# operator interrupted (summary.csv only; an interrupted FULLLOG has no
# OVERALL line at all, so the next run redoes the host).
NON_VERDICT_LABELS = ("DRY RUN", "SKIPPED", "INTERRUPTED")
STEP_STATES = (PASS, FAIL, SKIP, INCOMPLETE, DRY)     # FULLLOG trailers
SUMMARY_CELL = {PASS: "Complete", FAIL: "Failed", SKIP: "Skipped",
                INCOMPLETE: "Incomplete", DRY: "Dry Run"}   # summary.csv
# A host stopped at step zero: this line, then "OVERALL: <verdict> (no
# step ran)", then "Reason: <why>". The reason starts with one of the
# STOP_* prefixes, which name the cause.
STOP_MARK = "STOPPED BEFORE STEP 1:"
REASON_PREFIX = "Reason: "
STOP_UNREACHABLE = "step-zero probe failed — "
STOP_MISMATCH = "identity mismatch — "
STOP_UNCONFIRMED = "identity not confirmed — "
# Written by step zero when the host's own report matches its hosts entry.
IDENTITY_OK = "Identity check: OK"
# An operator interrupt (Ctrl-C) mid-run: this line replaces the step's
# trailer and the log gets no OVERALL line (H4: "finished" means the
# FULLLOG has an OVERALL line).
INTERRUPT_MARK = "INTERRUPTED:"
# The FULLLOG "Expected:" header records the run's NCCL requirement:
# "NCCL required (--require-nccl)" / "NCCL required (hgx profile
# default)", NCCL_OPT_OUT_NOTE, or "NCCL not required (dgx: ...)".
EXPECTED_PREFIX = "Expected: "
NCCL_REQUIRED_TEXT = "NCCL required ("

# Health card file name per verdict (REVIEW-A L1: a NOT HEALTHY host's
# card is no longer called -HEALTHY.png). Cards written before v2.0 are
# all <host>-HEALTHY.png whatever the verdict; the tracker still reads
# those (LEGACY_CARD_SUFFIX).
CARD_SUFFIX = {"HEALTHY": "-HEALTHY.png", "NOT HEALTHY": "-NOT-HEALTHY.png",
               "INCOMPLETE": "-INCOMPLETE.png",
               "UNREACHABLE": "-UNREACHABLE.png", "DRY RUN": "-DRY-RUN.png"}
LEGACY_CARD_SUFFIX = "-HEALTHY.png"


def card_filename(hostname, overall):
    """The health card's file name for a host verdict."""
    return hostname + CARD_SUFFIX.get(overall, "-CARD.png")


def card_files(host_dir, hostname):
    """Every health card file present for a host, newest first."""
    names = set(CARD_SUFFIX.values()) | {"-CARD.png"}
    found = []
    try:
        for fn in os.listdir(host_dir):
            if fn.startswith(hostname) and fn[len(hostname):] in names:
                p = os.path.join(host_dir, fn)
                found.append((os.path.getmtime(p), p))
    except OSError:
        return []
    return [p for _m, p in sorted(found, reverse=True)]


def fulllog_finished(text):
    """True when a FULLLOG's text carries its OVERALL line after the
    closing '=' bar — the one definition of a finished log, shared by
    resume, --watch and the tracker (REVIEW-A H4)."""
    lines = (text or "").splitlines()
    return any(ln.startswith("OVERALL: ") and i > 0
               and lines[i - 1] == "=" * 72 for i, ln in enumerate(lines))

# --------------------------------------------------------------------------
# Validation steps
# --------------------------------------------------------------------------


class Step:
    def __init__(self, slug, label, argv, sudo=False, timeout=300):
        self.slug = slug
        self.label = label
        self.argv = argv
        self.sudo = sudo
        self.timeout = timeout

    def shown(self):
        return ("sudo " if self.sudo else "") + shlex.join(self.argv)


def build_steps(profile, stress_timeout, health_timeout=1800, dump_timeout=3600,
                nccl_timeout=2700, nccl_tests=("all_reduce", "all_gather",
                                               "reduce_scatter"),
                nccl_dir="", nccl_mpirun="mpirun", gpus=None,
                force_stress=False, include_nccl=True):
    # Defaults sized from field timings: nvsm show health 15-20 min,
    # stress-test 30-60 min, nvsm dump health ~10+ min — all get headroom.
    # Profiles: dgx = NVSM + smi (DGX Quickstart; DGX nodes anywhere);
    # hgx = NCCL + smi only — NVSM and /etc/dgx-release are DGX-only, so on
    # HGX the NCCL run carries the fabric proof. The NCCL step is included
    # only where it carries proof: always on hgx; on dgx only when the
    # caller asks (include_nccl, driven by --require-nccl / --only nccl).
    # gpus (mpirun -np) comes from the host's expected count; None means
    # the profile default.
    if gpus is None:
        gpus = PROFILE_DATA.get(profile, PROFILE_DATA["dgx"])["gpus"]
    steps = [Step("dgx-release", "DGX Release", ["cat", "/etc/dgx-release"],
                  timeout=60)] if profile == "dgx" else \
            [Step("os-release", "OS Release", ["cat", "/etc/os-release"],
                  timeout=60)]
    if profile == "dgx":
        steps.append(Step("nvsm-health", "NVSM Show Health",
                          ["nvsm", "show", "health"],
                          sudo=True, timeout=health_timeout))
    steps += [
        Step("smi-query", "NVIDIA-SMI Query", ["nvidia-smi", "-q"], timeout=600),
        Step("topo", "NVLink Topology", ["nvidia-smi", "topo", "-m"], timeout=300),
    ]
    if include_nccl:
        steps.append(
            Step("nccl", "NCCL Tests",
                 ["sh", "-c", _nccl_script(nccl_tests, nccl_mpirun,
                                           nccl_dir, gpus)],
                 timeout=nccl_timeout))
    if profile == "dgx":
        # --force-stress appends nvsm's own --force AFTER --no-prompt:
        # `nvsm stress-test --no-prompt --force`. The runner's --force
        # (redo hosts) never reaches nvsm.
        steps += [
            Step("stress", "NVSM Stress Test",
                 ["nvsm", "stress-test", "--no-prompt"]
                 + (["--force"] if force_stress else []),
                 sudo=True, timeout=stress_timeout),
            Step("dump", "NVSM Dump Health", ["nvsm", "dump", "health"],
                 sudo=True, timeout=dump_timeout),
        ]
    return steps


def _nccl_script(tests, mpirun, bin_dir, gpus):
    """One shell line that runs each NCCL perf test in turn, with section
    markers and per-test exit codes the verdict parser can attribute."""
    parts = []
    for name in tests:
        binary = f"{bin_dir}/{name}_perf" if bin_dir else f"{name}_perf"
        parts.append(
            f"echo '===== {name}_perf ====='; "
            f"{mpirun} --allow-run-as-root -np {gpus} {binary} "
            f"-b 8M -e 2G -f 2 -g 1; "
            f"echo '[{name}_perf exit '$?']'")
    return "; ".join(parts)


# --- NCCL evidence (mirrors tracker_build.nccl_evidence) -----------------

_NCCL_SECTION_RE = re.compile(r"(?m)^===== (\S+?)_perf =====\s*$")
_NCCL_NOT_FOUND_RE = re.compile(r"(?m)^Executable:\s*(\S+)\s*$")


def _nccl_data_rows(body):
    rows = []
    for ln in body.splitlines():
        toks = ln.split()
        if len(toks) >= 9 and toks[0].isdigit():
            rows.append(toks)
    return rows


def nccl_evidence(text):
    """Structural read of one NCCL step's output, the same signature the
    tracker uses: per-test sections, exit codes, whether any result line
    exists, and which binaries mpirun reported missing. mpirun's
    'Executable: <name>' report can land in the NEXT test's section
    (stderr placement), so those reports are read across the whole
    output, never per section."""
    parts = _NCCL_SECTION_RE.split(text or "")
    tests = parts[1::2]
    bodies = dict(zip(tests, parts[2::2]))
    exits, ran = {}, {}
    for name in tests:
        b = bodies[name]
        m = re.search(r"\[%s_perf exit (\d+)\]" % re.escape(name), b)
        exits[name] = int(m.group(1)) if m else None
        ran[name] = bool(
            re.search(r"(?m)^#\s*(Out of bounds values|Avg bus bandwidth)", b)
            or _nccl_data_rows(b))
    not_found = set()
    for exe in _NCCL_NOT_FOUND_RE.findall(text or ""):
        base = os.path.basename(exe)
        not_found.add(base[:-5] if base.endswith("_perf") else base)
    missing = [t for t in tests
               if not ran[t] and exits[t] not in (None, 0)
               and (t in not_found or exits[t] == 127)]
    non_launch = bool(tests) and not any(ran.values()) and \
        len(missing) == len(tests)
    return {"tests": tests, "bodies": bodies, "exits": exits, "ran": ran,
            "missing": missing, "non_launch": non_launch}


def _verdict_nccl(text, nccl_required, nccl_min_busbw):
    ev = nccl_evidence(text)
    if not ev["tests"]:
        return FAIL, "no NCCL test sections found in output"
    names = ", ".join(f"{t}_perf" for t in ev["missing"])
    if ev["non_launch"]:
        if nccl_required:
            # Loki's ruling (Brief C amendment 1): a required suite that
            # never launched is missing evidence, not evidence of a fault.
            # The step is SKIP and stays counted, so the host is
            # INCOMPLETE (fail closed), never NOT HEALTHY and never
            # HEALTHY. A suite that ran and failed is FAIL below.
            return SKIP, (f"not installed: {names} — no NCCL test ran; "
                          "NCCL is required for this host, so the host is "
                          "INCOMPLETE (missing evidence, not a fault)")
        return SKIP, f"not installed: {names} — step skipped (not required)"
    fails, peaks = [], {}
    for t in ev["tests"]:
        if t in ev["missing"]:
            continue
        body, ex = ev["bodies"][t], ev["exits"][t]
        if ex is None:
            fails.append(f"{t}: no exit marker (crashed or cut short)")
            continue
        if ex != 0:
            fails.append(f"{t}: exit {ex}")
            continue
        oob = re.search(r"# Out of bounds values\s*:\s*(\d+)", body)
        if not oob:
            fails.append(f"{t}: no 'Out of bounds values' line")
            continue
        if int(oob.group(1)) != 0:
            fails.append(f"{t}: {oob.group(1)} out-of-bounds values"
                         " — correctness FAILED")
            continue
        rows = _nccl_data_rows(body)
        if not rows:
            fails.append(f"{t}: no result rows")
            continue
        peak, wrong = 0.0, False
        for toks in rows:
            if toks[-1].isdigit() and int(toks[-1]) != 0:
                wrong = True
            try:
                peak = max(peak, float(toks[-2]))
            except ValueError:
                pass
        if wrong:
            fails.append(f"{t}: nonzero #wrong — correctness FAILED")
            continue
        peaks[t] = peak
    if ev["missing"]:
        fails.append("not installed: " + names)
    if fails:
        return FAIL, "; ".join(fails)
    if (nccl_min_busbw and peaks.get("all_reduce") is not None
            and peaks["all_reduce"] < nccl_min_busbw):
        return FAIL, (f"all_reduce peak {peaks['all_reduce']:.0f} GB/s "
                      f"below floor {nccl_min_busbw:.0f} GB/s")
    return PASS, "peak busbw GB/s: " + ", ".join(
        f"{k} {v:.0f}" for k, v in peaks.items())


# --- NVLink topology (structural GPU submatrix) ---------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# PCIe-class cells in the nvidia-smi topo legend (no NVLink on the path).
TOPO_PCIE_CLASSES = ("PIX", "PXB", "PHB", "NODE", "SYS", "PCI")


def parse_topo_gpu_matrix(text):
    """The GPU<->GPU submatrix of `nvidia-smi topo -m`, parsed by
    structure: a header whose leading columns are GPU0..GPUn-1, then one
    row per GPU whose first n cells are its links (diagonal 'X').
    Returns {"n": n, "cells": {(i, j): cell}} or None when no such
    matrix can be read."""
    lines = [_ANSI_RE.sub("", ln) for ln in (text or "").splitlines()]
    for idx, ln in enumerate(lines):
        toks = ln.split()
        if not toks or toks[0] != "GPU0" or (len(toks) > 1 and toks[1] == "X"):
            continue
        n = 0
        while n < len(toks) and toks[n] == f"GPU{n}":
            n += 1
        rows = {}
        for ln2 in lines[idx + 1:]:
            t2 = ln2.split()
            if not t2:
                continue
            m = re.fullmatch(r"GPU(\d+)", t2[0])
            if not m:
                if rows:
                    break
                continue
            i = int(m.group(1))
            if i >= n or i in rows or len(t2) < n + 1:
                return None
            rows[i] = t2[1:n + 1]
        if len(rows) != n:
            return None
        cells = {}
        for i in range(n):
            if rows[i][i] != "X":
                return None
            for j in range(n):
                if i != j:
                    cells[(i, j)] = rows[i][j]
        return {"n": n, "cells": cells}
    return None


def _verdict_topo(text, expected_gpus, nvlink_class):
    if not nvlink_class:
        return INCOMPLETE, ("no expected NVLink class in the profile or "
                            "hosts data — topology cannot be judged")
    m = parse_topo_gpu_matrix(text)
    if m is None:
        return INCOMPLETE, "GPU topology matrix not found or unparseable"
    allowed = {c.strip().upper() for c in nvlink_class.split(",") if c.strip()}
    exp_txt = "/".join(sorted(allowed))
    if m["n"] != expected_gpus:
        return FAIL, (f"topology matrix shows {m['n']} GPUs, expected "
                      f"{expected_gpus}")
    degraded, unknown = [], []
    for (i, j), cell in sorted(m["cells"].items()):
        if i > j:
            continue
        for a, b, c in ((i, j, cell), (j, i, m["cells"][(j, i)])):
            cu = c.upper()
            if cu in allowed:
                continue
            pair = f"GPU{a}-GPU{b} {c}"
            if re.fullmatch(r"NV\d+", cu):
                degraded.append(f"{pair} (NVLink width; expected {exp_txt})")
            elif cu in TOPO_PCIE_CLASSES:
                degraded.append(f"{pair} (PCIe-class; expected {exp_txt})")
            else:
                unknown.append(pair)
    if degraded:
        more = f" (+{len(degraded) - 4} more)" if len(degraded) > 4 else ""
        return FAIL, "degraded link(s): " + "; ".join(degraded[:4]) + more
    if unknown:
        return INCOMPLETE, ("unrecognized topology cell(s): "
                            + "; ".join(unknown[:4]))
    pairs = m["n"] * (m["n"] - 1) // 2
    if not pairs:
        return PASS, f"{m['n']} GPU — no GPU-GPU links to check"
    return PASS, f"{m['n']} GPUs, all {pairs} GPU pairs {exp_txt}"


# --- NVSM health / stress / dump -------------------------------------------

_HEALTH_COUNT_RE = re.compile(
    r"(?m)^\s*(\d+) out of (\d+) checks are "
    r"(healthy|unhealthy|unknown|informational|disabled|skipped)\s*$")
_HEALTH_UNHEALTHY_RE = re.compile(r"(?m)^(\S.*?)\.{3,}\s*Unhealthy\s*$")


def _verdict_health(text):
    counts = {kind: (int(n), int(m))
              for n, m, kind in _HEALTH_COUNT_RE.findall(text)}
    bad = [n.strip() for n in _HEALTH_UNHEALTHY_RE.findall(text)]
    if bad or counts.get("unhealthy", (0, 0))[0] > 0:
        n_bad = counts.get("unhealthy", (len(bad), 0))
        total = f" of {n_bad[1]}" if n_bad[1] else ""
        return FAIL, (f"{n_bad[0]}{total} checks unhealthy"
                      + (": " + "; ".join(bad[:4]) if bad else ""))
    if "healthy" not in counts or "unhealthy" not in counts:
        return INCOMPLETE, ("no 'N out of M checks are healthy/unhealthy' "
                            "summary — health not established")
    if counts.get("unknown", (0, 0))[0] > 0:
        u, m = counts["unknown"]
        return INCOMPLETE, f"{u} of {m} checks unknown"
    h, m = counts["healthy"]
    return PASS, f"{h} of {m} checks healthy, 0 unhealthy"


_STRESS_COMPONENT_RE = re.compile(
    r"(?m)^\s*(%s)\s+(\S+)\s*$" % "|".join(STRESS_COMPONENTS))
STRESS_LOG_RE = re.compile(r"(?m)^Stress Test Log:\s*(/\S+)\s*$")
DUMP_PATH_RE = re.compile(
    r"The output of this command is written to:\s*(/\S+)")


def _verdict_stress(text, dur):
    low = text.lower()
    if "are you sure you want to proceed" in low:
        return FAIL, ("nvsm printed its confirmation prompt and "
                      "exited — the stress test never ran "
                      "(--no-prompt missing)")
    if "stress test is already running" in low:
        return FAIL, ("another stress test was already running on "
                      "this host; nvsm cancelled this request — "
                      "the step did not run")
    if "stress testing will not be done" in low or \
            "have not been cleared" in low:
        return SKIP, ("nvsm refused to run: uncleared system alerts "
                      "on this host — the step did not run "
                      "(re-run with --force-stress to override)")
    comp = {}
    for name, status in _STRESS_COMPONENT_RE.findall(text):
        comp.setdefault(name, status)
    problems = []
    not_pass = [f"{c} {s}" for c, s in comp.items() if s.upper() != "PASS"]
    if not_pass:
        problems.append("component(s) not PASS: " + ", ".join(not_pass))
    absent = [c for c in STRESS_COMPONENTS if c not in comp]
    if absent:
        problems.append("no result line for: " + ", ".join(absent))
    if not re.search(r"\b100% Complete", text):
        problems.append("never reached 100% Complete")
    if dur is None:
        problems.append("wall duration not measured")
    elif dur < STRESS_FLOOR_S:
        problems.append(f"ran {dur:.1f}s, below the {STRESS_FLOOR_S}s "
                        "floor for a genuine stress run")
    if problems:
        return FAIL, "; ".join(problems)
    return PASS, (f"{len(STRESS_COMPONENTS)}/{len(STRESS_COMPONENTS)} "
                  f"components PASS, 100% Complete, {dur:.0f}s")


def verdict(step, rc, out, expected_gpus, nccl_min_busbw=0.0,
            nccl_required=False, dur=None, nvlink_class=None):
    """Return (state, note), state in PASS / FAIL / SKIP / INCOMPLETE.

    PASS is earned only by the step's positive success evidence; absence
    of error text is never enough. The FULLLOG always carries the raw
    output so a human can audit any rule."""
    text = out or ""
    if rc != 0:
        if step.slug == "nccl" and rc == 127 and "=====" not in text:
            return FAIL, ("mpirun or the shell not found — NCCL tests "
                          "could not start on this host")
        return FAIL, f"exit code {rc}"
    if step.slug == "dgx-release":
        if not re.search(r"(?m)^\s*DGX_\w+=", text):
            return FAIL, "no DGX_* release keys in /etc/dgx-release"
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        return PASS, first[:80]
    if step.slug == "os-release":
        pretty = next((ln.split("=", 1)[1].strip().strip('"')
                       for ln in text.splitlines()
                       if ln.startswith("PRETTY_NAME=")), "")
        name = next((ln.split("=", 1)[1].strip().strip('"')
                     for ln in text.splitlines()
                     if ln.startswith("NAME=")), "")
        if not (pretty or name):
            return FAIL, "no NAME= / PRETTY_NAME= in /etc/os-release"
        return PASS, (pretty or name)[:80]
    if step.slug == "nccl":
        return _verdict_nccl(text, nccl_required, nccl_min_busbw)
    if step.slug == "nvsm-health":
        return _verdict_health(text)
    if step.slug == "smi-query":
        m = re.search(r"Attached GPUs\s*:\s*(\d+)", text)
        if not m:
            return INCOMPLETE, "could not parse Attached GPUs"
        n = int(m.group(1))
        if n != expected_gpus:
            return FAIL, f"Attached GPUs = {n}, expected {expected_gpus}"
        return PASS, f"{n} GPUs"
    if step.slug == "topo":
        return _verdict_topo(text, expected_gpus, nvlink_class)
    if step.slug == "stress":
        return _verdict_stress(text, dur)
    if step.slug == "dump":
        m = DUMP_PATH_RE.search(text)
        if not m:
            return INCOMPLETE, ("nvsm dump health printed no archive path "
                                "— the dump is not established")
        return PASS, "archive: " + m.group(1)
    return INCOMPLETE, f"no evidence rule for step '{step.slug}'"

# --------------------------------------------------------------------------
# Runners: local subprocess, SSH via the system ssh/scp, or dry (plan only)
# --------------------------------------------------------------------------

# A tracked step prints this line first, from the shell that is the
# leader of the step's process group on the host; the runner strips it
# from the transcript and uses it to clean up after a timeout.
PGID_MARK = "DGXV-PGID="
_PGID_RE = re.compile(r"(?m)^DGXV-PGID=(\d+)[ \t]*\r?\n?")


class LocalRunner:
    name = "local"
    dry = False

    def run(self, argv, timeout, sudo=False, sudo_password=None, stream=None,
            track=False):
        cmd = list(argv)
        stdin_data = None
        if sudo and os.geteuid() != 0:
            if sudo_password:
                cmd = ["sudo", "-S", "-p", ""] + cmd
                stdin_data = sudo_password + "\n"
            else:
                cmd = ["sudo", "-n"] + cmd
        if track:
            # sh is started as a new session leader, so its pid is the
            # step's process-group id.
            cmd = ["sh", "-c", f'echo {PGID_MARK}$$; exec "$@"', "sh"] + cmd
        return _exec(cmd, timeout, stdin_data, stream, new_session=track,
                     interruptible=track)

    def fetch(self, remote_path, dest_path):
        shutil.copyfile(remote_path, dest_path)


class SSHRunner:
    name = "ssh"
    dry = False

    def __init__(self, address, user=None, batch=True):
        self.address = address
        self.user = user
        self.target = f"{user}@{address}" if user else address
        self.batch = batch
        self.opts = ["-o", "ConnectTimeout=15",
                     "-o", "StrictHostKeyChecking=accept-new"]
        if batch:
            self.opts += ["-o", "BatchMode=yes"]

    def run(self, argv, timeout, sudo=False, sudo_password=None, stream=None,
            track=False):
        remote = list(argv)
        stdin_data = None
        if sudo:
            if sudo_password:
                remote = ["sudo", "-S", "-p", ""] + remote
                stdin_data = sudo_password + "\n"
            else:
                remote = ["sudo", "-n"] + remote
        line = shlex.join(remote)
        if track:
            # sshd starts the remote shell as a session (and process
            # group) leader, so its $$ is the step's process-group id.
            line = f"echo {PGID_MARK}$$; exec {line}"
        cmd = ["ssh"] + self.opts + [self.target, line]
        # In batch mode the ssh client runs in its own session, so the
        # operator's Ctrl-C reaches only this runner, never the step's
        # ssh: an interrupt is recorded as an interrupt, not as a step
        # that FAILed with exit 255 (REVIEW-A H4). --no-batch keeps the
        # terminal (SSH may need it to prompt).
        return _exec(cmd, timeout, stdin_data, stream,
                     new_session=self.batch, interruptible=track)

    def fetch(self, remote_path, dest_path):
        cmd = ["scp"] + self.opts + [f"{self.target}:{remote_path}", dest_path]
        rc, _, err = _exec(cmd, 120, None)
        if rc != 0:
            raise OSError(f"scp failed for {remote_path}: {err.strip()}")


class DryRunner:
    """Wraps a real runner for display purposes; executes nothing."""
    name = "dry-run"
    dry = True

    def __init__(self, inner):
        self.inner = inner

    def run(self, argv, timeout, sudo=False, sudo_password=None, stream=None,
            track=False):
        shown = ("sudo " if sudo else "") + shlex.join(argv)
        text = f"[dry-run] would run: {shown}\n[dry-run] no host was contacted"
        if stream:
            for ln in text.splitlines():
                stream(ln)
        return 0, text, 0.0

    def fetch(self, remote_path, dest_path):
        raise OSError("dry-run: no fetch")


RC_INTERRUPTED = 130
_INTERRUPTED_TEXT = "\n[INTERRUPTED by the operator]"


def _exec(cmd, timeout, stdin_data, stream=None, new_session=False,
          interruptible=False):
    """Run one local command (ssh/scp/sh). An interruptible call (a
    tracked test step) is cut short when the operator interrupts the run
    and returns RC_INTERRUPTED; a Ctrl-C that lands here in the main
    thread also sets the interrupt."""
    if stream is not None:
        return _exec_stream(cmd, timeout, stdin_data, stream,
                            new_session=new_session,
                            interruptible=interruptible)
    start = time.time()
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE if stdin_data
                             is not None else None,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=new_session)
    except FileNotFoundError:
        return 127, f"[command not found: {cmd[0]}]", time.time() - start
    except Exception as e:  # never let one host kill the run
        return 1, f"[runner error: {e}]", time.time() - start
    pending, so, se = stdin_data, "", ""
    try:
        while True:
            left = timeout - (time.time() - start)
            try:
                so, se = p.communicate(
                    input=pending,
                    timeout=max(0.01, min(0.5, left) if interruptible
                                else left))
                break
            except subprocess.TimeoutExpired:
                pending = None
                if time.time() - start >= timeout:
                    p.kill()
                    so, _se = p.communicate()
                    return 124, (so or "") + f"\n[TIMEOUT after {timeout}s]", \
                        time.time() - start
                if interruptible and _INTERRUPT.is_set():
                    p.kill()
                    so, se = p.communicate()
                    return RC_INTERRUPTED, (so or "") + _INTERRUPTED_TEXT, \
                        time.time() - start
    except KeyboardInterrupt:           # a main-thread caller
        _INTERRUPT.set()
        p.kill()
        so, se = p.communicate()
        return RC_INTERRUPTED, (so or "") + _INTERRUPTED_TEXT, \
            time.time() - start
    except Exception as e:  # never let one host kill the run
        p.kill()
        return 1, f"[runner error: {e}]", time.time() - start
    out = (so or "") + (("\n[stderr]\n" + se) if se else "")
    return p.returncode, out, time.time() - start


def _exec_stream(cmd, timeout, stdin_data, stream, new_session=False,
                 interruptible=False):
    """Streaming variant: lines are handed to `stream` as they arrive
    (stderr merged into stdout, terminal-style) while the full text is
    still accumulated for the FULLLOG. Timeout enforced via select."""
    start = time.time()
    try:
        p = subprocess.Popen(cmd,
                             stdin=subprocess.PIPE if stdin_data else None,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT,
                             start_new_session=new_session)
    except FileNotFoundError:
        return 127, f"[command not found: {cmd[0]}]", time.time() - start
    except Exception as e:
        return 1, f"[runner error: {e}]", time.time() - start
    if stdin_data:
        try:
            p.stdin.write(stdin_data.encode())
            p.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    sel = selectors.DefaultSelector()
    sel.register(p.stdout, selectors.EVENT_READ)
    chunks, buf = [], ""
    killed = cut = False
    try:
        while True:
            if time.time() - start > timeout:
                p.kill()
                killed = True
                break
            if interruptible and _INTERRUPT.is_set():
                p.kill()
                cut = True
                break
            if not sel.select(timeout=0.5):
                if p.poll() is not None:
                    break
                continue
            data = os.read(p.stdout.fileno(), 65536)
            if not data:
                break
            text = data.decode(errors="replace")
            chunks.append(text)
            buf += text
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if not line.startswith(PGID_MARK):
                    stream(line)
    except KeyboardInterrupt:           # a main-thread caller
        _INTERRUPT.set()
        p.kill()
        cut = True
    if buf and not buf.startswith(PGID_MARK):
        stream(buf)
    try:
        rc = p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()
        rc = p.wait()
    out = "".join(chunks)
    if killed:
        out += f"\n[TIMEOUT after {timeout}s]"
        rc = 124
    elif cut:
        out += _INTERRUPTED_TEXT
        rc = RC_INTERRUPTED
    return rc, out, time.time() - start


def take_pgid(out):
    """(pgid or None, output with the tracking line removed)."""
    m = _PGID_RE.search(out or "")
    if not m:
        return None, out or ""
    return int(m.group(1)), (out[:m.start()] + out[m.end():])


_sleep = time.sleep   # seam: tests replace it

# Set by main() on the operator's Ctrl-C (REVIEW-A H4). Host workers stop
# before their next step; a step in flight is cut short by _exec (tracked
# steps only) and recorded as interrupted, with no verdict.
_INTERRUPT = threading.Event()


def kill_process_group(runner, pgid, sudo=False, sudo_password=None):
    """After a timeout: signal the step's process group on the host
    (TERM, then KILL) and verify it is gone with `pgrep -g`. Returns
    (confirmed, detail). pgrep exit 1 = no process in the group
    (confirmed gone); 0 = still alive; anything else = cannot verify."""
    def alive():
        rc, _o, _d = runner.run(["pgrep", "-g", str(pgid)], 30)
        return {0: True, 1: False}.get(rc)      # None = cannot verify

    done = []
    for sig, wait in (("TERM", 5.0), ("KILL", 2.0)):
        rc, _o, _d = runner.run(["kill", f"-{sig}", "--", f"-{pgid}"], 30,
                                sudo=sudo, sudo_password=sudo_password)
        done.append(f"SIG{sig} -> rc {rc}")
        _sleep(wait)
        state = alive()
        if state is False:
            return True, (f"process group {pgid} confirmed gone after "
                          f"SIG{sig} ({'; '.join(done)})")
        if state is None:
            return False, (f"process group {pgid}: {'; '.join(done)}; "
                           "could NOT verify the group is gone (pgrep "
                           "unavailable or host unreachable)")
    return False, (f"process group {pgid} STILL ALIVE after SIGTERM and "
                   f"SIGKILL ({'; '.join(done)}) — processes may still be "
                   "running on the host")

# --------------------------------------------------------------------------
# Per-host run
# --------------------------------------------------------------------------


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_SAFE_REMOTE_PATH = re.compile(r"/[A-Za-z0-9_./:+@-]+")


def harvest_printed_file(runner, remote_path, host_dir, sudo_password=None):
    """Fetch the exact file a step printed (nvsm's "Stress Test Log:
    <path>" / dump "written to: <path>"). Tries a plain copy first, then
    a sudo read (nvsm writes its logs root-only). Returns
    (local_name or None, detail)."""
    if not _SAFE_REMOTE_PATH.fullmatch(remote_path or "") or \
            ".." in remote_path.split("/"):
        return None, f"printed path refused as unsafe: {remote_path!r}"
    local = os.path.basename(remote_path).replace(":", "-")
    dest = os.path.join(host_dir, local)
    try:
        runner.fetch(remote_path, dest)
        return local, f"copied from {remote_path}"
    except Exception as e:
        first = str(e) or e.__class__.__name__
    rc, out, _d = runner.run(["base64", remote_path], 900, sudo=True,
                             sudo_password=sudo_password)
    if rc == 0:
        try:
            body = (out or "").split("\n[stderr]\n")[0]
            data = base64.b64decode("".join(
                ln.strip() for ln in body.splitlines()), validate=True)
        except (ValueError, base64.binascii.Error):
            data = None
        if data:
            with open(dest, "wb") as f:
                f.write(data)
            return local, f"read via sudo from {remote_path}"
    return None, (f"could not harvest {remote_path}: copy failed ({first}); "
                  f"sudo read exit {rc}")


def print_plan(hostname, runner_desc, steps, host_dir, expect=None):
    print(f"\n[dry-run] {hostname}  ({runner_desc})")
    print("  0. identity probe: hostname, chassis serial, platform "
          "(read-only; stops the host on mismatch)")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step.shown()}   [timeout {step.timeout}s]")
    if expect:
        print(f"  expects: {expect['gpus']} GPUs ({expect['gpus_src']}), "
              f"NVLink {expect['nvlink']} ({expect['nvlink_src']}); "
              f"NCCL {expect['nccl_text']}")
    print(f"  evidence -> {host_dir}")


# --- Step zero: identity / platform probe ---------------------------------

IDENTITY_CMD = (
    "echo DGXV-ID-BEGIN; "
    "echo HOSTNAME=$(hostname 2>/dev/null); "
    "if [ -f /etc/dgx-release ]; then echo PLATFORM=dgx; "
    "elif command -v nvsm >/dev/null 2>&1; then echo PLATFORM=dgx; "
    "else echo PLATFORM=hgx; fi; "
    "echo PRODUCT=$(cat /sys/class/dmi/id/product_name 2>/dev/null); "
    "echo VENDOR=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null); "
    "echo SERIAL=$(cat /sys/class/dmi/id/product_serial 2>/dev/null); "
    "echo DGXV-ID-END")
SERIAL_PATH = "/sys/class/dmi/id/product_serial"
# Values firmware leaves in the DMI serial when nobody set one: not an
# identity.
_PLACEHOLDER_SERIALS = {"", "0", "none", "n/a", "na", "default string",
                        "to be filled by o.e.m.", "system serial number",
                        "not specified", "0123456789"}


def _kv(out, key):
    for ln in (out or "").splitlines():
        if ln.startswith(key + "="):
            return ln.split("=", 1)[1].strip()
    return None


def probe_identity(runner, sudo_password=None):
    """Ask the host who it is. Returns a dict: reachable, detail,
    hostname, platform, product, vendor, serial, serial_src."""
    ident = {"reachable": False, "detail": "", "hostname": "",
             "platform": "", "product": "", "vendor": "", "serial": "",
             "serial_src": ""}
    try:
        rc, out, _dur = runner.run(["sh", "-c", IDENTITY_CMD], 60)
    except Exception as e:  # pragma: no cover - runners never raise
        ident["detail"] = f"probe raised {e}"
        return ident
    if rc != 0 or "DGXV-ID-END" not in (out or ""):
        last = next((ln.strip() for ln in reversed((out or "").splitlines())
                     if ln.strip()), "")
        ident["detail"] = (f"probe exit {rc}" + (f": {last[:160]}" if last
                                                 else ""))
        return ident
    ident["reachable"] = True
    for key in ("hostname", "platform", "product", "vendor", "serial"):
        ident[key] = _kv(out, key.upper()) or ""
    if ident["serial"].lower() not in _PLACEHOLDER_SERIALS:
        ident["serial_src"] = SERIAL_PATH
    else:
        ident["serial"] = ""
        rc2, out2, _d = runner.run(["cat", SERIAL_PATH], 60, sudo=True,
                                   sudo_password=sudo_password)
        val = next((ln.strip() for ln in (out2 or "").splitlines()
                    if ln.strip()), "") if rc2 == 0 else ""
        if val.lower() not in _PLACEHOLDER_SERIALS:
            ident["serial"], ident["serial_src"] = val, \
                f"{SERIAL_PATH} via sudo"
        else:
            ident["serial_src"] = (f"unreadable ({SERIAL_PATH}: sudo read "
                                   f"exit {rc2})" if rc2 != 0 else
                                   f"unset ({SERIAL_PATH} empty or "
                                   "placeholder)")
    if ident["platform"] not in ("dgx", "hgx"):
        ident["reachable"] = False
        ident["detail"] = "probe answered without a platform line"
    return ident


_IP_RE = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|[0-9a-fA-F:]*:[0-9a-fA-F:.]*)$")


def _short(name):
    return (name or "").strip().lower().split(".")[0]


def check_identity(entry, ident):
    """(status, detail): status "ok", "mismatch" or "unconfirmed".
    The machine's own report is the court: the hosts entry's name must
    match the host's hostname (short names, case-insensitive) unless the
    entry names the host only by IP; a serial column, when given, must
    match the chassis serial; and a chassis serial must be readable."""
    got_h, got_s = ident.get("hostname", ""), ident.get("serial", "")
    if not got_h:
        return "unconfirmed", "host reported no hostname"
    claimed = entry.get("name", "")
    if claimed and not _IP_RE.match(claimed) and \
            _short(claimed) != _short(got_h):
        return "mismatch", (f"hosts entry '{claimed}' but the host "
                            f"reports hostname '{got_h}'")
    if not got_s:
        return "unconfirmed", ("chassis serial not readable — "
                               + (ident.get("serial_src") or "no serial"))
    want_s = (entry.get("serial") or "").strip()
    if want_s and want_s.lower() != got_s.strip().lower():
        return "mismatch", (f"hosts entry serial '{want_s}' but the host "
                            f"reports serial '{got_s}'")
    return "ok", (f"hostname {got_h}, serial {got_s}"
                  + (" (matches hosts entry)" if want_s else
                     " (hosts entry carries no serial)"))


def check_sudo(runner, sudo_password=None):
    """(ok, detail) — one harmless sudo call (`sudo -n true` or
    `sudo -S true`)."""
    rc, out, _d = runner.run(["true"], 30, sudo=True,
                             sudo_password=sudo_password)
    if rc == 0:
        return True, ("sudo -S true succeeded" if sudo_password
                      else "sudo -n true succeeded (passwordless)")
    last = next((ln.strip() for ln in reversed((out or "").splitlines())
                 if ln.strip() and ln.strip() != "[stderr]"), "")
    return False, f"sudo failed (exit {rc})" + (f": {last[:160]}" if last
                                                else "")


# --- Wave watching -------------------------------------------------------
# A tiny block font in the classic banner style ('*' pixels), just the
# glyphs the scoreboard needs: digits, '/', and the WAVE DONE letters.
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


def _fulllog_position(fulllog):
    """Where a running host is, read from its FULLLOG: the last
    '>>> step' marker the runner flushed at step start (v1.9+ logs).
    Older logs have no markers — fall back to the completed-section
    count ('[k/N]' headers are appended as each step finishes)."""
    pos = ""
    done_steps = 0
    total_steps = 0
    try:
        with open(fulllog, errors="replace") as f:
            for ln in f:
                if ln.startswith(">>> step "):
                    pos = ln[len(">>> "):].split(" — started ")[0].strip()
                m = re.match(r"\[(\d+)/(\d+)\] ", ln)
                if m:
                    done_steps = int(m.group(1))
                    total_steps = int(m.group(2))
    except OSError:
        pass
    if pos:
        return pos
    if total_steps:
        return f"{done_steps} of {total_steps} steps done"
    return ""


# A running host flushes its FULLLOG at every step start and end, and the
# step-start marker carries the step's timeout. A log untouched for longer
# than that timeout plus this slack belongs to a run that died (REVIEW-A
# M6: there was no stalled state; a dead host read "running" forever).
STALL_SLACK_S = 600
STALL_DEFAULT_S = 5400      # logs before v2.0 carry no timeout on the marker
_MARKER_TIMEOUT_RE = re.compile(r"^>>> step \d+/\d+: .* · timeout (\d+)s\s*$")


def wave_state(out_root, hostname, now=None):
    """(state, detail) for one host in a wave output root. A host is
    "done" only when its FULLLOG carries the OVERALL line (REVIEW-A H4/M6:
    a health card or a dry-run stub no longer counts). Otherwise:
    "interrupted" (the operator stopped the run), "stalled" (no FULLLOG
    update for longer than the running step's timeout plus slack),
    "running" (detail: where it is), or "waiting" (no FULLLOG yet)."""
    host_dir = os.path.join(out_root, hostname)
    fulllog = os.path.join(host_dir, f"{hostname}-FULLLOG.txt")
    if not os.path.exists(fulllog):
        return "waiting", ""
    try:
        with open(fulllog, encoding="utf-8", errors="replace") as f:
            text = f.read()
        mtime = os.path.getmtime(fulllog)
    except OSError:
        return "running", "FULLLOG unreadable"
    if "[DRY RUN]" in text.split("\n", 1)[0]:
        return "waiting", "only a dry-run log — not a result"
    if fulllog_finished(text):
        verdict = ""
        for ln in text.splitlines():
            if ln.startswith("OVERALL:"):
                verdict = ln.split(":", 1)[1].strip()
        return "done", verdict
    pos = _fulllog_position(fulllog)
    if any(ln.startswith(INTERRUPT_MARK) for ln in text.splitlines()):
        return "interrupted", ((pos + "; ") if pos else "") + \
            "re-run the launch command to redo it"
    limit = STALL_DEFAULT_S
    for ln in text.splitlines():
        m = _MARKER_TIMEOUT_RE.match(ln)
        if m:
            limit = int(m.group(1))
    age = (now if now is not None else time.time()) - mtime
    if age > limit + STALL_SLACK_S:
        return "stalled", ((pos + "; ") if pos else "") + (
            f"no FULLLOG update for {age / 60:.0f} min (step timeout "
            f"{limit}s) — the run likely died; re-run the launch command "
            "to redo it")
    return "running", pos


def cmd_watch(args):
    """The wave scoreboard: watch an output root, contact nothing."""
    if args.hosts:
        try:
            names = [n for n, _a, _u in read_hosts(args.hosts)]
        except (OSError, ValueError) as e:
            print(f"watch: hosts file: {e}", file=sys.stderr)
            return 1
    elif os.path.isdir(args.out):
        names = sorted(d for d in os.listdir(args.out)
                       if d != DRY_SUBDIR
                       and os.path.isdir(os.path.join(args.out, d)))
    else:
        names = []
    if not names:
        print("watch: no hosts — pass --hosts CSV for the wave list, or "
              "an --out root that has host folders.", file=sys.stderr)
        return 1        # misuse (EXIT_HELP: 1), not a verdict (it was 2)
    total = len(names)
    while True:
        states = {n: wave_state(args.out, n) for n in names}
        count = {k: sum(1 for s, _v in states.values() if s == k)
                 for k in ("done", "running", "interrupted", "stalled",
                           "waiting")}
        done, running = count["done"], count["running"]
        line = (f"{done} of {total} validated ({running} running, "
                f"{count['waiting']} waiting"
                + "".join(f", {count[k]} {k.upper()}"
                          for k in ("interrupted", "stalled") if count[k])
                + ")")
        hint = ""
        if not os.path.isdir(args.out):
            hint = (f"no output root at {args.out} yet — is --out the "
                    "same root the run writes to?")
        elif count["waiting"] == total:
            hint = (f"no host has a FULLLOG under {args.out} yet — is --out "
                    "the same root the run writes to?")
        if args.once:
            print(line)
            if hint:
                print(hint)
            for n in names:
                s, v = states[n]
                print(f"  {n}: {s}" + (f" — {v}" if v else ""))
            return 0
        tty = sys.stdout.isatty()
        if tty:
            sys.stdout.write("\033[2J\033[H")
        block = render_banner(f"{done} / {total}", scale=2)
        print(_rainbow(block) if tty else block)
        print(f"\n{line}\n")
        if hint:
            print(f"  {hint}\n")
        for n in names:
            s, v = states[n]
            print(f"  {n:<40} {s}" + (f" — {v}" if v else ""))
        if done < total and not running and (count["interrupted"]
                                             or count["stalled"]):
            # Nothing is moving and something will never finish: say so
            # instead of waiting forever.
            print(f"\nWAVE STOPPED: {count['interrupted']} interrupted, "
                  f"{count['stalled']} stalled, {count['waiting']} not "
                  "started. Re-run the launch command: finished hosts are "
                  "skipped, unfinished hosts are redone.")
            return 2
        if done >= total:
            healthy = sum(1 for _s, v in states.values()
                          if v.startswith("HEALTHY"))
            not_healthy = sum(1 for _s, v in states.values()
                              if v.startswith("NOT HEALTHY"))
            incomplete = sum(1 for _s, v in states.values()
                             if v.startswith("INCOMPLETE"))
            unreachable = sum(1 for _s, v in states.values()
                              if v.startswith("UNREACHABLE"))
            tally = f"{healthy} HEALTHY, {not_healthy} NOT HEALTHY"
            if incomplete:
                tally += f", {incomplete} INCOMPLETE"
            if unreachable:
                tally += f", {unreachable} UNREACHABLE"
            rest = total - healthy - not_healthy - incomplete - unreachable
            if rest:
                tally += f", {rest} verdict unread"
            done_block = render_banner("WAVE DONE", scale=1)
            print("\n" + (_rainbow(done_block) if tty else done_block))
            print(tally)
            return 0
        sys.stdout.flush()
        time.sleep(max(1.0, args.interval))


DRY_SUBDIR = "dry-run"   # dry-run artifacts live here, never beside real ones


def is_dry_fulllog(path):
    """True for a FULLLOG a dry run wrote (its first line carries the
    [DRY RUN] stamp). Such a log is not evidence: a real run treats it
    as absent."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "[DRY RUN]" in f.readline()
    except OSError:
        return False


def real_fulllogs(out_root):
    """Relative paths of the non-dry FULLLOGs in an output root
    (<root>/<host>/*-FULLLOG.txt), ignoring the dry-run subdir."""
    found = []
    if not os.path.isdir(out_root):
        return found
    for d in sorted(os.listdir(out_root)):
        hd = os.path.join(out_root, d)
        if d == DRY_SUBDIR or not os.path.isdir(hd):
            continue
        for fn in sorted(os.listdir(hd)):
            p = os.path.join(hd, fn)
            if fn.endswith("-FULLLOG.txt") and os.path.isfile(p) \
                    and not is_dry_fulllog(p):
                found.append(os.path.join(d, fn))
    return found


def stopped_at_step_zero(path):
    """True for a FULLLOG whose host was stopped at step zero
    (UNREACHABLE, identity mismatch/unconfirmed). Such a log holds no
    step evidence, so a later run does not treat it as finished."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return any(ln.startswith(STOP_MARK) for ln in f)
    except OSError:
        return False


_CARD_STATE = {PASS: "pass", FAIL: "fail", SKIP: "skip", DRY: "skip",
               INCOMPLETE: "incomplete"}
_OVERALL_CARD = {"HEALTHY": "pass", "NOT HEALTHY": "fail", "DRY RUN": "skip",
                 "INCOMPLETE": "incomplete", "UNREACHABLE": "unreachable"}


def _legacy_expect(steps, expected_gpus, nccl_required, platform_note):
    """Expectations for an in-process caller that passes a step list
    instead of a plan: the profile is read off the steps."""
    profile = "dgx" if any(s.slug in ("dgx-release", "nvsm-health",
                                      "stress", "dump") for s in steps) \
        else "hgx"
    return {"steps": list(steps), "profile": profile,
            "gpus": expected_gpus, "gpus_src": "caller",
            "nvlink": PROFILE_DATA[profile]["nvlink"],
            "nvlink_src": f"profile default: {PROFILE_DATA[profile]['desc']}",
            "nccl_required": nccl_required, "nccl_waived": False,
            "nccl_text": "required" if nccl_required else "not required",
            "platform_note": platform_note}


def run_host(hostname, runner, out_root, steps, expected_gpus,
             sudo_password=None, force=False, nccl_min_busbw=0.0, tail=False,
             nccl_required=False, platform_note="", plan=None, entry=None):
    """Validate one host. `plan(platform)` (platform = the step-zero
    probe's 'dgx'/'hgx', or None on a dry run) returns the host's
    expectations: steps, profile, gpus/nvlink and their sources, and the
    NCCL requirement. Without a plan, `steps` / `expected_gpus` are used
    as given. `entry` is the hosts-data entry identity is checked
    against (default: just the hostname)."""
    host_dir = os.path.join(out_root, hostname)
    os.makedirs(host_dir, exist_ok=True)
    fulllog_path = os.path.join(host_dir, f"{hostname}-FULLLOG.txt")
    dry = getattr(runner, "dry", False)
    entry = entry or {"name": hostname}
    result = {"hostname": hostname, "steps": {}, "notes": [],
              "overall": "", "release": "", "pct": "", "reason": ""}
    kept_unfinished = None
    if os.path.exists(fulllog_path) and not dry \
            and not is_dry_fulllog(fulllog_path) \
            and not stopped_at_step_zero(fulllog_path):
        try:
            with open(fulllog_path, encoding="utf-8", errors="replace") as f:
                finished = fulllog_finished(f.read())
        except OSError:
            finished = False
        if finished and not force:
            # REVIEW-A H4: "finished" means the FULLLOG has its OVERALL
            # line. Only a finished host is left alone.
            result["overall"] = "SKIPPED"
            result["notes"].append("finished FULLLOG exists (use --force "
                                   "to redo)")
            return result
        if not finished:
            # An unfinished log (an interrupted or killed run) is not a
            # result: the host is redone. The partial log is evidence of
            # what happened, so it is kept under a name no tool reads as
            # a FULLLOG, never overwritten.
            kept_unfinished = (f"{hostname}-FULLLOG.interrupted-"
                               + now_iso().replace(":", "") + ".txt")
            os.replace(fulllog_path, os.path.join(host_dir, kept_unfinished))
    # The card describes the run being made now: an older card (any
    # verdict name) must not sit beside a FULLLOG that is being rewritten
    # (REVIEW-A M6/L1).
    for old_card in card_files(host_dir, hostname):
        try:
            os.remove(old_card)
        except OSError:
            pass

    started = now_iso()
    # ---- step zero: who is this host? -----------------------------------
    ident, id_status, id_detail, stop = None, "", "", None
    if not dry:
        _say(f"  [{hostname}] step 0: identity probe ...")
        ident = probe_identity(runner, sudo_password)
        if not ident["reachable"]:
            stop = ("UNREACHABLE", "step-zero probe failed — "
                    + (ident["detail"] or "no answer"))
        else:
            id_status, id_detail = check_identity(entry, ident)
            if id_status == "mismatch":
                stop = ("INCOMPLETE", "identity mismatch — " + id_detail)
            elif id_status == "unconfirmed":
                stop = ("INCOMPLETE", "identity not confirmed — " + id_detail)
    platform = ident["platform"] if ident and ident["reachable"] else None
    expect = plan(platform) if plan else \
        _legacy_expect(steps, expected_gpus, nccl_required, platform_note)
    steps = expect["steps"]
    waived = expect.get("nccl_waived")
    if waived:
        result["notes"].append(NCCL_OPT_OUT_NOTE)

    transcript = []
    transcript.append("DGX Vitals — FULLLOG" + ("  [DRY RUN]" if dry else ""))
    transcript.append(f"Host: {hostname}   Mode: {runner.name}   Tool: dgx-vitals.py v{VERSION}")
    if stop and stop[0] == "UNREACHABLE":
        transcript.append("Platform: not established (step-zero probe failed)")
    elif expect.get("platform_note"):
        transcript.append(f"Platform: {expect['platform_note']}")
    transcript.append(
        f"Expected: {expect['gpus']} GPUs ({expect['gpus_src']}) · NVLink "
        f"{expect['nvlink']} ({expect['nvlink_src']}) · "
        + ("" if expect["nccl_text"].startswith("NCCL") else "NCCL ")
        + expect["nccl_text"])
    transcript.append(f"Started: {started}")
    if kept_unfinished:
        transcript.append(f"Previous: an unfinished FULLLOG (no OVERALL line) "
                          f"was found and kept as {kept_unfinished}; this "
                          "run redoes the host")
    if dry:
        transcript.append("DRY RUN: no commands were executed and no host was contacted.")
    transcript.append("=" * 72)
    if ident is not None:
        transcript.append(f"\n>>> step 0: identity probe — started {started}")
        if ident["reachable"]:
            transcript.append(
                f"Identity: hostname={ident['hostname'] or '?'} "
                f"serial={ident['serial'] or '?'} ({ident['serial_src']}) "
                f"platform={ident['platform']} product={ident['product'] or '?'} "
                f"vendor={ident['vendor'] or '?'}")
            transcript.append(f"Identity check: {id_status.upper()} — {id_detail}")
        else:
            transcript.append(f"Identity: no answer — {ident['detail']}")

    def flush():
        with open(fulllog_path, "w") as f:
            f.write("\n".join(transcript) + "\n")

    def finish(overall, counted_txt, card_rows):
        result["overall"] = overall
        transcript.append("\n" + "=" * 72)
        transcript.append(f"OVERALL: {overall}{counted_txt}"
                          + (f" — {NCCL_OPT_OUT_NOTE}" if waived else ""))
        if stop:
            transcript.append(f"{REASON_PREFIX}{stop[1]}")
        transcript.append(f"Finished: {now_iso()}")
        flush()
        render_health_card(os.path.join(host_dir,
                                        card_filename(hostname, overall)),
                           hostname, now_iso(), card_rows, overall,
                           _OVERALL_CARD[overall],
                           release_line=result["release"])
        return result

    def interrupted(i, step, out, dur, pgid):
        """Operator interrupt (Ctrl-C): no verdict for the step in
        flight, no OVERALL line, no card — the log stays unfinished, so
        the next run redoes the host (REVIEW-A H4)."""
        where = f"step {i}/{len(steps)} ({step.label})"
        if out is not None:
            transcript.append(f"\n--- {where} interrupted — partial "
                              "output follows ---")
            transcript.append((out or "").rstrip())
        transcript.append(
            f"{INTERRUPT_MARK} operator interrupt (Ctrl-C) "
            + (f"during {where} after {dur:.0f}s" if out is not None
               else f"before {where} started")
            + " — no verdict recorded for it and the run did not finish. "
            "Re-run the same command: an unfinished host is redone.")
        if pgid is not None and not dry:
            ok, detail = kill_process_group(runner, pgid, step.sudo,
                                            sudo_password)
            transcript.append(f"[interrupt cleanup: {detail}]")
            if step.slug == "stress":
                transcript.append(
                    "[interrupt cleanup: nvsm runs the stress test in its "
                    "own service; ending the client's process group does "
                    "not prove the service-side test stopped]")
        flush()
        result["overall"] = "INTERRUPTED"
        result["reason"] = (f"interrupted by the operator at {where}; "
                            "re-run to finish")
        result["notes"].append(result["reason"])
        _say(f"  [{hostname}] INTERRUPTED at {where}")
        return result

    if stop:
        transcript.append(f"{STOP_MARK} {stop[1]}")
        result["notes"].append(stop[1])
        result["reason"] = stop[1]
        _say(f"  [{hostname}] {stop[0]}: {stop[1]}")
        return finish(stop[0], " (no step ran)", [])

    n_pass = 0
    neutral = set()      # skipped steps that do not count (NCCL not
    #                      required and not waived by a binding opt-out)
    unconfirmed_cleanup = []
    for i, step in enumerate(steps, 1):
        if _INTERRUPT.is_set():
            return interrupted(i, step, None, 0.0, None)
        _say(f"  [{hostname}] step {i}/{len(steps)}: {step.label} ...")
        # Position marker, flushed before the step runs: the FULLLOG is
        # the one artifact a watcher can read mid-run, so this line is
        # how --watch knows where a running host is. The step's timeout
        # rides on it, so --watch can tell a long step from a stalled
        # host (REVIEW-A M6).
        transcript.append(f"\n>>> step {i}/{len(steps)}: {step.label} "
                          f"— started {now_iso()} · timeout {step.timeout}s")
        flush()
        stream = (lambda ln: _say(f"  [{hostname}] | {ln}")) if tail else None
        rc, out, dur = runner.run(step.argv, step.timeout,
                                  sudo=step.sudo, sudo_password=sudo_password,
                                  stream=stream, track=not dry)
        pgid, out = take_pgid(out)
        if _INTERRUPT.is_set():
            return interrupted(i, step, out, dur, pgid)
        extra = []
        cleanup_ok = True
        if not dry and rc == 124:
            if pgid is not None:
                cleanup_ok, detail = kill_process_group(
                    runner, pgid, step.sudo, sudo_password)
            else:
                cleanup_ok, detail = False, (
                    "no process-group id was captured — cannot confirm the "
                    "step's processes stopped")
            extra.append(f"[timeout cleanup: {detail}]")
            if step.slug == "stress":
                extra.append("[timeout cleanup: nvsm runs the stress test in "
                             "its own service; ending the client's process "
                             "group does not prove the service-side test "
                             "stopped — a later stress step reports 'already "
                             "running' if it did not]")
            if not cleanup_ok:
                unconfirmed_cleanup.append(step.label)
        if dry:
            state, note = DRY, "dry-run"
        else:
            state, note = verdict(step, rc, out, expect["gpus"],
                                  nccl_min_busbw, expect["nccl_required"],
                                  dur=dur, nvlink_class=expect["nvlink"])
            if not cleanup_ok:
                note += "; timeout cleanup NOT confirmed"
        if state == PASS:
            n_pass += 1
        # Package A (Loki, 2026-11-16): a waived test is absent evidence.
        # An NCCL SKIP under a binding operator opt-out still COUNTS, so
        # the host is INCOMPLETE, never HEALTHY, when the waiver is the
        # sole reason it would pass. A FAIL anywhere is still NOT HEALTHY.
        if state == SKIP and step.slug == "nccl" and \
                not expect["nccl_required"] and \
                not expect.get("nccl_waiver_binds"):
            neutral.add(step.slug)
        result["steps"][step.slug] = (state, note)
        if state == SKIP and note:
            result["notes"].append(f"{step.label}: {note}")
        if step.slug == "dgx-release" and state == PASS:
            result["release"] = note
        # Harvest exactly what the step printed (never a guessed path).
        if not dry and step.slug in ("stress", "dump"):
            rx, what = ((STRESS_LOG_RE, "stress log") if step.slug == "stress"
                        else (DUMP_PATH_RE, "dump archive"))
            m = rx.search(out)
            if m:
                got, detail = harvest_printed_file(runner, m.group(1),
                                                   host_dir, sudo_password)
                if got:
                    extra.append(f"[harvested {what}: {got} — {detail}]")
                    result["notes"].append(f"{what}: {got}")
                else:
                    extra.append(f"[{what} NOT harvested: {detail}]")
                    result["notes"].append(f"{what} not harvested: {detail}")
            else:
                extra.append(f"[{what}: nvsm printed no path — nothing to "
                             "harvest]")
        shown_state = state
        _say(f"  [{hostname}] step {i}/{len(steps)}: {step.label} — "
             f"{shown_state} ({dur:.0f}s)" + (f" — {note}" if note and
                                               state != PASS and not dry
                                               else ""))
        transcript.append(f"\n[{i}/{len(steps)}] {step.shown()}")
        transcript.append("-" * 72)
        transcript.append(out.rstrip())
        transcript.append(f"[exit {rc} · {dur:.1f}s · {shown_state}"
                          + (f" · {note}" if note and not dry else "") + "]")
        transcript.extend(extra)
        flush()  # crash insurance on long runs

    if unconfirmed_cleanup:
        result["notes"].append("timeout cleanup NOT confirmed for: "
                               + ", ".join(unconfirmed_cleanup))
    card_rows = [(s.label, _CARD_STATE[result["steps"][s.slug][0]])
                 for s in steps]
    if dry:
        result["pct"] = ""
        return finish("DRY RUN", "", card_rows)
    states = {slug: st for slug, (st, _n) in result["steps"].items()
              if slug not in neutral}
    counted = len(states)
    result["pct"] = round(100 * n_pass / counted) if counted else 0
    if FAIL in states.values():
        overall = "NOT HEALTHY"
    elif not counted or any(st in (SKIP, INCOMPLETE)
                            for st in states.values()):
        overall = "INCOMPLETE"
        if not counted:
            result["notes"].append("no step ran for this host's profile "
                                   "(check --only)")
    else:
        overall = "HEALTHY"
    return finish(overall, f" ({n_pass}/{counted} steps passed)", card_rows)

# --------------------------------------------------------------------------
# Hosts file, summary, main
# --------------------------------------------------------------------------


class HostsFileError(ValueError):
    pass


HOSTS_COLUMNS = ("hostname", "address", "lan_ip", "bmc_ip", "user", "gpus",
                 "nvlink", "serial")


def read_host_entries(path):
    """One host per line — the one hosts/fleet CSV parser, shared with
    tracker_build.py (REVIEW-A M11). All of these work:

        hostname,address,user,gpus,nvlink,serial   (with this header row;
                                                    any subset, any order)
        hostname,lan_ip,bmc_ip                     (tracker-style fleet
                                                    list: lan_ip is the
                                                    address SSH connects to)
        hostname,address
        hostname                       (address defaults to the hostname)
        10.0.0.51                      (name defaults to the address)

    gpus / nvlink / serial are per-host hardware data (see PROFILE_DATA):
    gpus overrides the expected GPU count, nvlink the expected topology
    class, serial is checked against the host's own chassis serial at
    step zero. The header row is optional (without it columns are
    hostname,address,user); a header must name its hostname column
    exactly "hostname" — a first row that looks like a header but has no
    such column (e.g. "Node Hostname") is refused, never read as zero
    hosts or as a host. Blank lines and #-comments are ignored. Opened
    as utf-8-sig so a file saved by Excel doesn't corrupt the first
    hostname."""
    entries = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return entries
    rows = list(csv.reader(lines))
    first = [h.strip().lower() for h in rows[0]]
    has_header = "hostname" in first
    if not has_header and any(
            c in cell for cell in first
            for c in ("hostname", "address", "lan_ip", "bmc_ip")):
        raise HostsFileError(
            f"{path}: row 1 looks like a header ({', '.join(rows[0])[:80]}) "
            "but has no column named exactly 'hostname' — accepted columns: "
            + ",".join(HOSTS_COLUMNS))
    if has_header:
        idx = {k: (first.index(k) if k in first else None)
               for k in HOSTS_COLUMNS}
        data = rows[1:]
    else:
        idx = {k: None for k in HOSTS_COLUMNS}
        idx.update({"hostname": 0, "address": 1, "user": 2})
        data = rows
    for n, row in enumerate(data, 2 if has_header else 1):
        def get(k):
            i = idx[k]
            return row[i].strip() if i is not None and i < len(row) else ""
        name = get("hostname")
        if not name:
            continue
        gpus = get("gpus")
        if gpus:
            if not gpus.isdigit() or int(gpus) < 1:
                raise HostsFileError(f"{path}: row {n} ({name}): gpus "
                                     f"'{gpus}' is not a positive integer")
            gpus = int(gpus)
        given = get("address")
        entries.append({"name": name,
                        # SSH connects to the address column, else the
                        # fleet list's LAN IP, else the name itself
                        "address": given or get("lan_ip") or name,
                        "address_given": given,
                        "lan_ip": get("lan_ip"), "bmc_ip": get("bmc_ip"),
                        "user": get("user") or None, "gpus": gpus or None,
                        "nvlink": get("nvlink") or None,
                        "serial": get("serial") or None})
    return entries


def read_hosts(path):
    """(hostname, address, user) per host — see read_host_entries."""
    return [(e["name"], e["address"], e["user"])
            for e in read_host_entries(path)]


SUMMARY_COLUMNS = [("Release", ["dgx-release", "os-release"]),
                   ("NVSM Health", ["nvsm-health"]),
                   ("NVIDIA-SMI Query", ["smi-query"]),
                   ("NVLink Topology", ["topo"]),
                   ("NCCL Tests", ["nccl"]),
                   ("NVSM Stress Test", ["stress"]),
                   ("NVSM Dump Health", ["dump"])]
_SUMMARY_CELL = SUMMARY_CELL     # the shared vocabulary


def write_summary(path, results, steps_by_slug):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Hostname"] + [h for h, _ in SUMMARY_COLUMNS]
                   + ["% Complete", "Overall", "Notes"])
        for r in results:
            row = [r["hostname"]]
            for _h, slugs in SUMMARY_COLUMNS:
                cell = ""
                for slug in slugs:
                    if slug in r["steps"]:
                        cell = _SUMMARY_CELL[r["steps"][slug][0]]
                row.append(cell)
            row.append(r.get("pct", ""))
            row.append(r["overall"])
            notes = "; ".join(r["notes"])
            fails = [f"{steps_by_slug[s].label}: {n}" for s, (st, n)
                     in r["steps"].items() if st in (FAIL, INCOMPLETE) and n]
            w.writerow(row + ["; ".join(fails + ([notes] if notes else []))])

# --------------------------------------------------------------------------
# --check: read-only preflight
# --------------------------------------------------------------------------

SKEW_LOUD_S = 120          # flagged loudly; never a gate
SKEW_ABSURD_S = 86400      # a clock a day out is a broken host: not ready
CHECK_MIN_REMOTE_TMP_GB = 2.0   # dgx: nvsm dump writes its archive to /tmp
CHECK_MIN_LOCAL_GB_PER_HOST = 1.0  # harvested logs + dump archives land here


def check_cmd(nccl_tests, nccl_dir, mpirun="mpirun"):
    bins = []
    for t in nccl_tests:
        b = f"{t}_perf"
        if nccl_dir:
            p = shlex.quote(f"{nccl_dir}/{b}")
            bins.append(f"if [ -x {p} ]; then echo BIN:{b}={p}; "
                        f"else echo BIN:{b}=; fi")
        else:
            bins.append(f"echo BIN:{b}=$(command -v {b} 2>/dev/null)")
    return "; ".join(
        ["echo DGXV-CHECK-BEGIN",
         IDENTITY_CMD.replace("echo DGXV-ID-BEGIN; ", "")
                     .replace("; echo DGXV-ID-END", ""),
         "echo EPOCH=$(date +%s)",
         "echo NVSM=$(command -v nvsm 2>/dev/null)",
         f"echo MPIRUN=$(command -v {shlex.quote(mpirun)} 2>/dev/null)"]
        + bins
        + ["echo TMPFREE_KB=$(df -Pk /tmp 2>/dev/null | awk 'NR==2{print $4}')",
           "echo DGXV-CHECK-END"])


def classify_alerts(rc, out):
    """(ok, detail) from `nvsm show alerts`. Ready only when the query
    succeeded and either listed nothing or said so; recognized alert
    records, or output this tool cannot interpret, are not ready."""
    if rc != 0:
        return False, f"alerts query failed (exit {rc})"
    body = (out or "").split("\n[stderr]\n")[0]
    recs = re.findall(r"(?m)^\s*(/\S*/alerts/\S+)\s*$", body)
    ids = re.findall(r"(?mi)^\s*AlertId\s*[=:]\s*(\S+)", body)
    if recs or ids:
        found = ids or recs
        return False, (f"{len(found)} standing alert(s): "
                       + ", ".join(found[:5]))
    if not body.strip() or re.search(r"(?i)\bno (active )?alerts?\b", body):
        return True, "none listed"
    head = " | ".join(ln.strip() for ln in body.splitlines()
                      if ln.strip())[:160]
    return False, ("unrecognized 'nvsm show alerts' output — cannot confirm "
                   f"no standing alerts: {head}")


def check_host(name, runner, entry, ctx, sudo_password=None,
               sudo_note=None):
    """Read-only readiness report for one host. ctx: profile (dgx/hgx/
    auto), nccl_tests, nccl_dir, require_nccl, no_require_nccl.
    Returns {"name", "state": READY|NOT READY|UNREACHABLE, "items":
    [(status, item, detail)], "sudo_failed": bool}."""
    rep = {"name": name, "state": "UNREACHABLE", "items": [],
           "sudo_failed": False}
    add = lambda st, item, det: rep["items"].append((st, item, det))
    t0 = time.time()
    rc, out, _dur = runner.run(
        ["sh", "-c", check_cmd(ctx["nccl_tests"], ctx["nccl_dir"])], 60)
    t1 = time.time()
    if rc != 0 or "DGXV-CHECK-END" not in (out or ""):
        last = next((ln.strip() for ln in reversed((out or "").splitlines())
                     if ln.strip()), "")
        add("FAIL", "ssh", f"no answer (exit {rc})"
            + (f": {last[:160]}" if last else ""))
        return rep
    add("ok", "ssh", f"connected; remote command ran ({t1 - t0:.1f}s round "
                     "trip)")
    ready = True
    platform = _kv(out, "PLATFORM") or ""
    profile = ctx["profile"] if ctx["profile"] != "auto" else platform
    if profile not in ("dgx", "hgx"):
        add("FAIL", "platform", "probe printed no platform line")
        rep["state"] = "UNREACHABLE"
        return rep
    if platform != profile:
        # the plan would run the wrong profile's steps on this host
        add("FAIL", "platform", f"{profile} requested, but the host probe "
            f"says {platform}")
        ready = False
    else:
        add("ok", "platform", profile + (" (detected)"
                                         if ctx["profile"] == "auto"
                                         else " (as requested; probe agrees)"))

    # sudo (canary-aware: a rejected password is not retried fleet-wide)
    if sudo_note:
        sudo_ok = False
        add("FAIL", "sudo", sudo_note)
    else:
        sudo_ok, det = check_sudo(runner, sudo_password)
        add("ok" if sudo_ok else "FAIL", "sudo", det)
        rep["sudo_failed"] = not sudo_ok
    ready &= sudo_ok

    # identity (the same rule step zero applies)
    ident = {"hostname": _kv(out, "HOSTNAME") or "",
             "serial": _kv(out, "SERIAL") or "", "serial_src": SERIAL_PATH}
    if ident["serial"].lower() in _PLACEHOLDER_SERIALS:
        ident["serial"] = ""
        if sudo_ok:
            rc2, out2, _d = runner.run(["cat", SERIAL_PATH], 60, sudo=True,
                                       sudo_password=sudo_password)
            val = next((ln.strip() for ln in (out2 or "").splitlines()
                        if ln.strip()), "") if rc2 == 0 else ""
            if val.lower() not in _PLACEHOLDER_SERIALS:
                ident["serial"] = val
            ident["serial_src"] = f"{SERIAL_PATH} via sudo (exit {rc2})"
        else:
            ident["serial_src"] = "root-only and sudo unavailable"
    st, det = check_identity(entry, ident)
    add("ok" if st == "ok" else "FAIL", "identity", det)
    ready &= st == "ok"

    # nvsm + standing alerts (dgx)
    nvsm = _kv(out, "NVSM") or ""
    if profile == "dgx":
        add("ok" if nvsm else "FAIL", "nvsm", nvsm or "nvsm not found on PATH")
        ready &= bool(nvsm)
        if nvsm and sudo_ok:
            rc3, out3, _d = runner.run(["nvsm", "show", "alerts"], 120,
                                       sudo=True, sudo_password=sudo_password)
            ok3, det3 = classify_alerts(rc3, out3)
            add("ok" if ok3 else "FAIL", "alerts", det3)
            ready &= ok3
        else:
            add("FAIL", "alerts", "not queried (needs nvsm and sudo)")
            ready = False
    else:
        add("info", "nvsm", (nvsm or "absent") + " (not used on hgx)")
        add("info", "alerts", "not applicable on hgx (no nvsm)")

    # nccl-tests, each binary by name
    required = ctx["require_nccl"] or (profile == "hgx"
                                       and not ctx["no_require_nccl"])
    why = ("required: --require-nccl" if ctx["require_nccl"] else
           "required: hgx profile default" if required else
           NCCL_OPT_OUT_NOTE if ctx["no_require_nccl"] else
           "not required on dgx")
    mpirun = _kv(out, "MPIRUN") or ""
    parts = [f"mpirun: {mpirun or 'MISSING'}"]
    missing = [] if mpirun else ["mpirun"]
    for t in ctx["nccl_tests"]:
        b = f"{t}_perf"
        p = _kv(out, f"BIN:{b}") or ""
        parts.append(f"{b}: {p or 'MISSING'}")
        if not p:
            missing.append(b)
    if missing:
        add("FAIL" if required else "info", "nccl-tests",
            "; ".join(parts) + f" — not installed: {', '.join(missing)} "
            f"({why})")
        ready &= not required
    else:
        add("ok", "nccl-tests", "; ".join(parts) + f" ({why})")

    # free disk on the host's /tmp (dump archive)
    kb = _kv(out, "TMPFREE_KB") or ""
    if kb.isdigit():
        gb = int(kb) / 1024 / 1024
        low = gb < CHECK_MIN_REMOTE_TMP_GB
        gate = low and profile == "dgx"
        add("FAIL" if gate else ("WARN" if low else "ok"), "disk",
            f"host /tmp: {gb:.1f} GiB free"
            + (f" (< {CHECK_MIN_REMOTE_TMP_GB:g} GiB; nvsm dump writes "
               "here)" if low else ""))
        ready &= not gate
    else:
        add("FAIL" if profile == "dgx" else "info", "disk",
            "host /tmp free space not reported")
        ready &= profile != "dgx"

    # clock skew: a report, never a dependency
    ep = _kv(out, "EPOCH") or ""
    if ep.isdigit():
        skew = int(ep) - (t0 + t1) / 2
        err = (t1 - t0) / 2 + 1
        txt = f"skew {skew:+.0f} s (±{err:.0f} s; host minus this machine)"
        if abs(skew) > SKEW_ABSURD_S:
            add("FAIL", "clock", txt + " — ABSURD CLOCK SKEW (> 1 day)")
            ready = False
        elif abs(skew) > SKEW_LOUD_S:
            add("WARN", "clock", txt + f" — CLOCK SKEW > {SKEW_LOUD_S} s "
                "(reported only; the tools do not depend on synced "
                "clocks)")
        else:
            add("ok", "clock", txt)
    else:
        add("WARN", "clock", "host did not report its time")
    rep["state"] = "READY" if ready else "NOT READY"
    return rep


def _free_gb(path):
    p = os.path.abspath(path)
    while not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    try:
        return shutil.disk_usage(p).free / 1024 ** 3, p
    except OSError:
        return None, p


def cmd_check(args, jobs, entries, sudo_password):
    """Run the preflight over every job; print the report; return the
    exit code (0 all ready, 2 any not ready)."""
    ctx = {"profile": args.profile, "nccl_tests": args.nccl_tests_t,
           "nccl_dir": args.nccl_dir, "require_nccl": args.require_nccl,
           "no_require_nccl": args.no_require_nccl}
    reports = {}
    order = [n for n, _r, _d in jobs]
    # Canary: hosts are checked one at a time until one answers; with a
    # sudo password, a rejection there is not retried on the rest.
    sudo_note = None
    rest = list(jobs)
    while rest:
        name, runner, _d = rest.pop(0)
        rep = check_host(name, runner, entries[name], ctx, sudo_password)
        reports[name] = rep
        if rep["state"] != "UNREACHABLE":
            if rep["sudo_failed"] and sudo_password:
                sudo_note = (f"not attempted — the sudo password was "
                             f"rejected on {name} (canary); not retried "
                             "fleet-wide")
            break
    if rest:
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            futs = {ex.submit(check_host, n, r, entries[n], ctx,
                              sudo_password, sudo_note): n
                    for n, r, _d in rest}
            for fut in as_completed(futs):
                reports[futs[fut]] = fut.result()
    free, where = _free_gb(args.out)
    need = CHECK_MIN_LOCAL_GB_PER_HOST * len(jobs)
    local_ok = free is not None and free >= need
    for name, _r, desc in jobs:
        rep = reports[name]
        print(f"\n== {name}  ({desc})  {rep['state']}")
        for st, item, det in rep["items"]:
            print(f"  {st:<5} {item:<11} {det}")
    print("\n== this machine")
    print(f"  {'ok' if local_ok else 'FAIL':<5} {'disk':<11} "
          + (f"{where}: {free:.1f} GiB free for --out "
             f"(need {need:g} GiB: {CHECK_MIN_LOCAL_GB_PER_HOST:g} per host)"
             if free is not None else f"{where}: free space unknown"))
    ready = [n for n in order if reports[n]["state"] == "READY"]
    print(f"\ncheck: {len(ready)} of {len(order)} host(s) ready"
          + ("" if local_ok else "; local output disk NOT ready"))
    for n in order:
        if reports[n]["state"] != "READY":
            why = [f"{i}" for s, i, _d in reports[n]["items"] if s == "FAIL"]
            print(f"  {reports[n]['state']}: {n}"
                  + (f" ({', '.join(why)})" if why else ""))
    return 0 if len(ready) == len(order) and local_ok else 2


def sudo_canary(jobs, sudo_password):
    """M10: validate sudo on the first reachable host before the fleet
    fans out. Returns (host, ok, detail); host None if none answered."""
    for name, runner, _d in jobs:
        rc, _o, _dur = runner.run(["true"], 30)
        if rc != 0:
            continue
        ok, detail = check_sudo(runner, sudo_password)
        return name, ok, detail
    return None, None, "no host answered"


class _Parser(argparse.ArgumentParser):
    """Usage errors exit 1 (tool misuse), never argparse's 2, which
    means "some host not healthy / not ready" here."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


EXIT_HELP = """\
exit codes (validation runs and --check):
  0  every host HEALTHY (--check: every host fully ready)
  2  some host NOT HEALTHY, INCOMPLETE or UNREACHABLE (--check: some host
     not ready); also a dry run refused because --out holds real results,
     and --watch reporting WAVE STOPPED
  1  tool error or misuse: bad arguments, no hosts, unreadable or
     malformed hosts file, or the sudo canary failed on the first
     reachable host
130  interrupted by the operator (Ctrl-C): steps in flight are recorded
     as interrupted with no verdict; re-run the same command to finish

re-runs: a host whose FULLLOG is finished (has its OVERALL line) is
SKIPPED unless --force; an unfinished one (interrupted or killed) is
redone and its partial log kept as <host>-FULLLOG.interrupted-<UTC>.txt.

host verdicts:
  HEALTHY      every counted step passed on positive evidence
  NOT HEALTHY  some step was measured and failed (an NCCL test that ran
               and failed is NOT HEALTHY, with or without the opt-out)
  INCOMPLETE   no failure, but a required step was skipped or could not
               be judged, or step zero could not confirm the host's
               identity (hostname / chassis serial vs the hosts entry).
               Fail closed: missing evidence is INCOMPLETE, never
               HEALTHY -- a required NCCL suite that never launched
               (nccl-tests not installed) is INCOMPLETE, not NOT HEALTHY,
               and so is an hgx host whose NCCL step was skipped by the
               operator's opt-out (--no-require-nccl)
  UNREACHABLE  the step-zero probe could not reach the host; no step ran
  (the health card is named for the verdict: <host>-NOT-HEALTHY.png etc.)

hgx requires NCCL by default; --no-require-nccl waives it and the waiver
is printed in the verdict as "NCCL not required (operator opt-out)".
A waived test is missing evidence: when the NCCL step is skipped by that
recorded choice the host is INCOMPLETE (exit 2), never HEALTHY; a test
that ran and failed is NOT HEALTHY with or without the opt-out.

dgx-vitals.py v""" + VERSION + "\n"


def main():
    ap = _Parser(description="DGX Vitals — DGX / HGX validation runner.",
                 epilog=EXIT_HELP,
                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=False)
    mode.add_argument("--local", action="store_true", help="validate this machine")
    mode.add_argument("--host", metavar="NAME", help="validate one host over SSH")
    mode.add_argument("--hosts", metavar="CSV",
                      help="validate many hosts over SSH (columns: hostname,"
                           "address[,user][,gpus][,nvlink][,serial], or a "
                           "hostname,lan_ip,bmc_ip fleet list)")
    ap.add_argument("--check", action="store_true",
                    help="read-only preflight for the selected host(s): SSH, "
                         "identity, sudo, nvsm, each nccl-tests binary, "
                         "standing alerts, free disk, clock skew. Writes "
                         "nothing, changes nothing, runs no test step; exit "
                         "0 only if every host is fully ready")
    ap.add_argument("--watch", action="store_true",
                    help="watch a wave output root (--out, with --hosts CSV "
                         "for the wave list) and show live progress; "
                         "contacts no hosts. Done = the FULLLOG has its "
                         "OVERALL line; also shows interrupted and stalled "
                         "hosts")
    ap.add_argument("--address", help="address for --host (default: the name itself)")
    ap.add_argument("--serial", help="expected chassis serial for --host "
                                     "(checked at step zero)")
    ap.add_argument("--user", help="SSH user (overrides CSV user column)")
    ap.add_argument("--out", default="validation-output", help="output root dir")
    ap.add_argument("--jobs", type=int, default=4, help="parallel SSH workers (default 4)")
    ap.add_argument("--gpus", type=int, default=None,
                    help="expected GPU count for every host (default: the "
                         "profile data, 8 for DGX B300 / HGX B300; a hosts "
                         "CSV 'gpus' column overrides it per host)")
    ap.add_argument("--nvlink", default=None,
                    help="expected nvidia-smi topo class for every GPU pair, "
                         "e.g. NV18 (default: profile data; a hosts CSV "
                         "'nvlink' column overrides it per host; several "
                         "acceptable classes comma-separated)")
    ap.add_argument("--profile", choices=["dgx", "hgx", "auto"],
                    default="dgx",
                    help="dgx = NVSM + smi, NCCL only with --require-nccl "
                         "(default); hgx = NCCL (required) + smi, no NVSM; "
                         "auto = the step-zero probe picks per host (a "
                         "failed probe is UNREACHABLE)")
    ap.add_argument("--nccl-tests", default="all_reduce,all_gather,reduce_scatter",
                    help="comma-separated NCCL tests to run as the suite")
    ap.add_argument("--nccl-dir", default="",
                    help="directory holding the *_perf binaries "
                         "(default: find them on the PATH)")
    ap.add_argument("--nccl-timeout", type=int, default=2700,
                    help="seconds allowed for the whole NCCL suite "
                         "(default 2700)")
    ap.add_argument("--nccl-min-busbw", type=float, default=0.0,
                    help="optional GB/s floor for the all-reduce peak bus "
                         "bandwidth (0 = correctness gate only)")
    ap.add_argument("--stress-timeout", type=int, default=5400,
                    help="seconds allowed for nvsm stress-test (default 5400)")
    ap.add_argument("--force-stress", action="store_true",
                    help="append nvsm's own --force after --no-prompt "
                         "(`nvsm stress-test --no-prompt --force`), "
                         "overriding nvsm's refusal to run while system "
                         "alerts are uncleared; use when the alerts are "
                         "known/adjudicated. Independent of --force")
    ap.add_argument("--health-timeout", type=int, default=1800,
                    help="seconds allowed for nvsm show health (default 1800)")
    ap.add_argument("--dump-timeout", type=int, default=3600,
                    help="seconds allowed for nvsm dump health (default 3600)")
    ap.add_argument("--only", help="comma-separated step slugs to run "
                                   "(dgx-release,os-release,nvsm-health,smi-query,topo,nccl,stress,dump)")
    ap.add_argument("--force", action="store_true",
                    help="redo hosts that already have a finished FULLLOG "
                         "(unfinished ones are always redone). Runner "
                         "only: it is never passed to nvsm, so --force does "
                         "not change the stress command (that is "
                         "--force-stress); `nvsm stress-test --no-prompt` "
                         "is the same with or without it")
    ap.add_argument("--tail", action="store_true",
                    help="stream each step's output live, prefixed by "
                         "hostname (best for one host or a few)")
    ap.add_argument("--dry-run", action="store_true",
                    help="execute nothing; print the plan and write DRY RUN "
                         "artifacts under <out>/dry-run/ (refuses a root "
                         "that already holds real FULLLOGs)")
    ap.add_argument("--interval", type=float, default=30,
                    help="seconds between --watch refreshes (default 30)")
    ap.add_argument("--once", action="store_true",
                    help="with --watch: print the wave state once and exit")
    ap.add_argument("--sudo-password", action="store_true",
                    help="prompt once for a sudo password (held in memory only)")
    ap.add_argument("--no-batch", action="store_true",
                    help="allow interactive SSH prompts (single-host runs)")
    nccl = ap.add_mutually_exclusive_group()
    nccl.add_argument("--require-nccl", action="store_true",
                      help="require the NCCL step: when nccl-tests is "
                           "not installed the step is skipped and the host "
                           "is INCOMPLETE (missing evidence; a test that "
                           "ran and failed is NOT HEALTHY). On dgx it also adds the NCCL "
                           "step, which is otherwise omitted; on hgx it is "
                           "already the default")
    nccl.add_argument("--no-require-nccl", action="store_true",
                      help="hgx opt-out: the NCCL step is skipped by the "
                           "operator's recorded choice, and the host is "
                           "INCOMPLETE (missing evidence), never HEALTHY; "
                           "a test that ran and failed is still NOT "
                           "HEALTHY. Loud by design: every verdict then "
                           "carries 'NCCL not required (operator "
                           "opt-out)'")
    ap.add_argument("--version", action="version",
                    version=f"dgx-vitals.py v{VERSION}")
    args = ap.parse_args()

    _INTERRUPT.clear()
    if args.watch:
        try:
            return cmd_watch(args)
        except KeyboardInterrupt:
            print("\nwatch stopped")
            return RC_INTERRUPTED
    if not (args.local or args.host or args.hosts):
        ap.error("one of --local, --host, --hosts or --watch is required")
    if args.check and args.dry_run:
        ap.error("--check is already read-only; it does not combine with "
                 "--dry-run")

    # A dry run never shares a root with real evidence: refuse before
    # anything is prompted, created or written. Its own artifacts go
    # under <out>/dry-run/, so it can never overwrite a real FULLLOG,
    # card or summary, and a later real run never mistakes them for
    # results.
    out_root = args.out
    if args.dry_run:
        real = real_fulllogs(args.out)
        if real:
            print(f"dry-run refused: {args.out} already holds real "
                  f"(non-dry-run) results — {len(real)} FULLLOG(s), e.g. "
                  f"{os.path.join(args.out, real[0])}. A dry run must not "
                  "share a root with real evidence; point --out at an "
                  "empty scratch root.", file=sys.stderr)
            return 2
        out_root = os.path.join(args.out, DRY_SUBDIR)

    nccl_tests = tuple(t.strip() for t in args.nccl_tests.split(",") if t.strip())
    args.nccl_tests_t = nccl_tests
    only = {s.strip() for s in args.only.split(",")} if args.only else set()

    # Hosts data (entries carry per-host gpus / nvlink / serial).
    if args.local:
        import socket
        entries = [{"name": socket.gethostname(), "address": None,
                    "user": None, "gpus": None, "nvlink": None,
                    "serial": None}]
    elif args.host:
        entries = [{"name": args.host, "address": args.address or args.host,
                    "user": args.user, "gpus": None, "nvlink": None,
                    "serial": args.serial}]
    else:
        try:
            entries = read_host_entries(args.hosts)
        except (OSError, HostsFileError) as e:
            print(f"hosts file: {e}", file=sys.stderr)
            return 1
    if not entries:
        print("No hosts to validate (empty hosts file?).", file=sys.stderr)
        return 1
    jobs = []  # (hostname, runner, description)
    for e in entries:
        if args.local:
            jobs.append((e["name"], LocalRunner(), "this machine"))
        else:
            r = SSHRunner(e["address"], user=args.user or e["user"],
                          batch=not args.no_batch)
            jobs.append((e["name"], r, f"ssh -> {r.target}"))
    by_name = {e["name"]: e for e in entries}

    def mksteps(profile, gpus):
        want_nccl = profile == "hgx" or args.require_nccl or "nccl" in only
        st = build_steps(profile, args.stress_timeout, args.health_timeout,
                         args.dump_timeout, args.nccl_timeout, nccl_tests,
                         args.nccl_dir, gpus=gpus,
                         force_stress=args.force_stress,
                         include_nccl=want_nccl)
        if only:
            st = [s for s in st if s.slug in only]
        return st

    profiles = [args.profile] if args.profile != "auto" else ["dgx", "hgx"]
    if not any(mksteps(p, None) for p in profiles):
        print("No steps matched --only.", file=sys.stderr)
        return 1

    def make_plan(e):
        def plan(platform):
            if args.profile != "auto":
                prof = args.profile
                note = f"{prof} (as requested)" + (
                    f"; step-zero probe says {platform}"
                    if platform and platform != prof else "")
            elif platform:
                prof, note = platform, f"{platform} (detected by step-zero probe)"
            else:
                prof, note = "dgx", "auto (dry run — dgx plan shown)"
            pdata = PROFILE_DATA[prof]
            if e.get("gpus"):
                gpus, gsrc = e["gpus"], "hosts data"
            elif args.gpus:
                gpus, gsrc = args.gpus, "--gpus"
            else:
                gpus, gsrc = pdata["gpus"], f"profile default: {pdata['desc']}"
            if e.get("nvlink"):
                nvl, nsrc = e["nvlink"], "hosts data"
            elif args.nvlink:
                nvl, nsrc = args.nvlink, "--nvlink"
            else:
                nvl, nsrc = pdata["nvlink"], f"profile default: {pdata['desc']}"
            req = bool(args.require_nccl or (prof == "hgx"
                                             and not args.no_require_nccl))
            steps = mksteps(prof, gpus)
            planned = any(s.slug == "nccl" for s in steps)
            text = ("required (--require-nccl)" if args.require_nccl else
                    "required (hgx profile default)" if req else
                    NCCL_OPT_OUT_NOTE if args.no_require_nccl else
                    "not required (dgx: NVSM covers the fabric)")
            if not planned:
                text += "; step not planned"
            return {"steps": steps, "profile": prof, "gpus": gpus,
                    "gpus_src": gsrc, "nvlink": nvl, "nvlink_src": nsrc,
                    "nccl_required": req,
                    "nccl_waived": bool(args.no_require_nccl),
                    # the waiver binds only where NCCL would otherwise be
                    # required (hgx default); on dgx it waives nothing
                    "nccl_waiver_binds": bool(args.no_require_nccl
                                              and prof == "hgx"),
                    "nccl_text": text, "platform_note": note}
        return plan

    sudo_password = getpass.getpass("Sudo password (blank = passwordless): ") \
        if args.sudo_password else None
    if sudo_password == "":
        sudo_password = None

    if args.check:
        return cmd_check(args, jobs, by_name, sudo_password)

    steps_by_slug = {}
    for _p in ("dgx", "hgx"):
        for _s in build_steps(_p, args.stress_timeout, args.health_timeout,
                              args.dump_timeout, args.nccl_timeout, nccl_tests,
                              args.nccl_dir, force_stress=args.force_stress):
            steps_by_slug[_s.slug] = _s

    if args.dry_run:
        jobs = [(n, DryRunner(r), d) for n, r, d in jobs]
        for name, _, desc in jobs:
            exp = make_plan(by_name[name])(None)
            print_plan(name, desc, exp["steps"],
                       os.path.join(out_root, name), exp)
    else:
        # M10: sudo once, first — before the fleet fans out.
        host, ok, detail = sudo_canary(jobs, sudo_password)
        if ok is False:
            print(f"sudo canary failed on {host}: {detail}. Stopping before "
                  "any host is validated — a fleet-wide sudo failure "
                  "(e.g. a mistyped password) would otherwise be repeated "
                  "on every host.", file=sys.stderr)
            return 1
        if host:
            print(f"  sudo canary: {host} — {detail}")

    os.makedirs(out_root, exist_ok=True)
    print(f"\nValidating {len(jobs)} host(s); output -> {out_root}")

    def one(name, runner):
        return run_host(name, runner, out_root, [], None, sudo_password,
                        args.force, args.nccl_min_busbw, args.tail,
                        plan=make_plan(by_name[name]), entry=by_name[name])

    # Every host runs in a worker thread, so the operator's Ctrl-C always
    # lands here, in the main thread (REVIEW-A H4): it stops the wave —
    # no queued host starts, no running host starts another step, a step
    # in flight is cut short and recorded as interrupted with no verdict
    # (its host's FULLLOG gets no OVERALL line, so the next run redoes
    # it) — and the summary is still written for what did finish.
    results, interrupted, not_started = [], False, []
    ex = ThreadPoolExecutor(max_workers=max(1, args.jobs))
    futs = {ex.submit(one, name, runner): name for name, runner, _ in jobs}
    pending = set(futs)
    while pending:
        try:
            done, pending = wait(pending, timeout=0.5,
                                 return_when=FIRST_COMPLETED)
        except KeyboardInterrupt:
            if interrupted:
                print("\nSecond interrupt: exiting now without waiting. "
                      "Unfinished hosts keep FULLLOGs with no OVERALL line "
                      "and are redone by the next run.", file=sys.stderr)
                os._exit(RC_INTERRUPTED)
            interrupted = True
            _INTERRUPT.set()
            for f in pending:
                if f.cancel():
                    not_started.append(futs[f])
            print("\nInterrupt: stopping the wave. No new host or step "
                  "starts; steps in flight are cut short and recorded as "
                  "interrupted (Ctrl-C again to exit at once).",
                  file=sys.stderr)
            continue
        for f in done:
            if f.cancelled():
                continue
            r = f.result()
            results.append(r)
            if len(jobs) > 1:
                print(f"  [{r['overall']}] {r['hostname']}"
                      + (f" — {r['reason']}" if r.get("reason") else ""))
    ex.shutdown(wait=True)
    results.sort(key=lambda r: r["hostname"])
    summary = os.path.join(out_root, "summary.csv")
    write_summary(summary, results, steps_by_slug)
    print(f"Done. Summary: {summary}" if not interrupted else
          f"Interrupted. Summary of what finished: {summary}")
    bad = {}
    for r in results:
        if r["overall"] in ("NOT HEALTHY", "INCOMPLETE", "UNREACHABLE",
                            "INTERRUPTED"):
            bad.setdefault(r["overall"], []).append(r["hostname"])
    for verdict_word, names in bad.items():
        print(f"{verdict_word}: " + ", ".join(names))
    if not_started:
        print("NOT STARTED (interrupted): " + ", ".join(sorted(not_started)))
    if interrupted:
        print("Re-run the same command to finish: finished hosts are "
              "skipped, unfinished hosts are redone.")
        return RC_INTERRUPTED
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
