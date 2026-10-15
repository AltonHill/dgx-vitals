#!/usr/bin/env python3
"""dgx-vitals.py — DGX Vitals: DGX validation runner (Ubuntu 24.04 / DGX OS).

Runs the six non-physical DGX validation steps and produces, per host, the
standard evidence set:

    <out>/<hostname>/<hostname>-FULLLOG.txt     full transcript of every step
    <out>/<hostname>/<hostname>-HEALTHY.png     health card ("vitals chart")
    <out>/<hostname>/*.nvsmlog                  stress-test log, harvested
                                                from the host when found
    <out>/summary.csv                           one row per host, tracker-shaped

Modes:
    --local                 run against this machine (on-box mode)
    --host NAME [--address A]   run against a single host over SSH
    --hosts hosts.csv       fan out over SSH to many hosts (CSV columns:
                            hostname,address[,user])
    --profile dgx|hgx       dgx (default): NVSM + NCCL + nvidia-smi.
                            hgx: NCCL + nvidia-smi only — NVSM and
                            /etc/dgx-release are DGX-only, so on HGX the
                            NCCL all-reduce carries the fabric proof.
    --dry-run               with any mode: execute nothing. Prints the plan
                            (exact commands, in order, per host) and writes
                            the same artifacts stamped DRY RUN, so anyone
                            can see exactly what a real run would do.

Stdlib only. Python 3.10+. SSH mode shells out to the system `ssh`/`scp`
(OpenSSH ships with Windows 10/11 and macOS), so no pip installs and no WSL.
Key-based SSH auth is assumed (BatchMode); pass --no-batch to allow
interactive SSH password prompts (single-host runs).

Sudo: steps that need root use `sudo -n` (passwordless) by default. If the
account needs a sudo password, pass --sudo-password and you will be prompted
once (getpass; the password is held in memory only, never written to disk).
"""

import argparse
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

VERSION = "1.4"

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
}


def render_health_card(path, hostname, when_iso, rows, overall_text,
                       overall_state, release_line=""):
    """rows: list of (label, state) with state in pass/fail/skip.
    overall_state likewise picks the color of the OVERALL line."""
    C = CARD_COLORS
    lines = [(hostname.upper(), C["fg"], 4),
             ("DGX VITALS  " + when_iso, C["dim"], 2)]
    if release_line:
        lines.append((release_line.upper()[:64], C["dim"], 2))
    lines.append(("", C["fg"], 2))
    prefix = {"pass": "PASS  ", "fail": "FAIL  ", "skip": "SKIP  "}
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


def build_steps(profile, stress_timeout, health_timeout=1800, dump_timeout=1800,
                nccl_timeout=2700, nccl_tests=("all_reduce", "all_gather",
                                               "reduce_scatter"),
                nccl_dir="", nccl_mpirun="mpirun", gpus=8):
    # Defaults sized from field timings: nvsm show health 15-20 min,
    # stress-test 30-60 min, nvsm dump health ~10+ min — all get headroom.
    # Profiles: dgx = NVSM + NCCL + smi (DGX Quickstart; DGX nodes anywhere);
    # hgx = NCCL + smi only — NVSM and /etc/dgx-release are DGX-only, so on
    # HGX the NCCL run carries the fabric proof.
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
        Step("nccl", "NCCL Tests",
             ["sh", "-c", _nccl_script(nccl_tests, nccl_mpirun, nccl_dir, gpus)],
             timeout=nccl_timeout),
    ]
    if profile == "dgx":
        steps += [
            Step("stress", "NVSM Stress Test", ["nvsm", "stress-test"],
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


def verdict(step, rc, out, expected_gpus, nccl_min_busbw=0.0):
    """Return (ok, note). Conservative rules; the FULLLOG always carries the
    raw output so a human can override any heuristic."""
    text = out or ""
    low = text.lower()
    if rc != 0:
        if step.slug == "nccl" and rc == 127 and "=====" not in out:
            return False, ("mpirun or all_reduce_perf not found — "
                           "NCCL tests not provisioned on this host")
        return False, f"exit code {rc}"
    if step.slug == "dgx-release":
        if not text.strip():
            return False, "empty /etc/dgx-release"
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        return True, first[:80]
    if step.slug == "os-release":
        if not text.strip():
            return False, "empty /etc/os-release"
        pretty = next((ln.split("=", 1)[1].strip().strip('"')
                       for ln in text.splitlines()
                       if ln.startswith("PRETTY_NAME=")), "")
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        return True, (pretty or first)[:80]
    if step.slug == "nccl":
        sections = re.split(r"===== (\w+_perf) =====", text)
        if len(sections) < 3:
            return False, "no NCCL test sections found in output"
        fails, peaks = [], {}
        for i in range(1, len(sections), 2):
            name, body = sections[i], sections[i + 1]
            short = name.replace("_perf", "")
            ex = re.search(r"\[%s exit (\d+)\]" % re.escape(name), body)
            if not ex:
                fails.append(f"{short}: no result (missing or crashed)")
                continue
            if int(ex.group(1)) != 0:
                fails.append(f"{short}: exit {ex.group(1)}"
                             + (" — not provisioned" if ex.group(1) == "127"
                                else ""))
                continue
            oob = re.search(r"# Out of bounds values\s*:\s*(\d+)", body)
            if not oob:
                fails.append(f"{short}: unparseable output")
                continue
            if int(oob.group(1)) != 0:
                fails.append(f"{short}: {oob.group(1)} out-of-bounds values"
                             " — correctness FAILED")
                continue
            peak, wrong = 0.0, False
            for ln in body.splitlines():
                toks = ln.split()
                if len(toks) >= 9 and toks[0].isdigit():
                    if toks[-1].isdigit() and int(toks[-1]) != 0:
                        wrong = True
                    try:
                        peak = max(peak, float(toks[-2]))
                    except ValueError:
                        pass
            if wrong:
                fails.append(f"{short}: nonzero #wrong — correctness FAILED")
                continue
            peaks[short] = peak
        if fails:
            return False, "; ".join(fails)
        if (nccl_min_busbw and peaks.get("all_reduce") is not None
                and peaks["all_reduce"] < nccl_min_busbw):
            return False, (f"all_reduce peak {peaks['all_reduce']:.0f} GB/s "
                           f"below floor {nccl_min_busbw:.0f} GB/s")
        return True, "peak busbw GB/s: " + ", ".join(
            f"{k} {v:.0f}" for k, v in peaks.items())
    if step.slug == "nvsm-health":
        bad = [w for w in ("unhealthy", "critical", "fatal") if w in low]
        if bad:
            return False, "output mentions: " + ", ".join(bad)
        return True, ""
    if step.slug == "smi-query":
        m = re.search(r"Attached GPUs\s*:\s*(\d+)", text)
        if not m:
            return False, "could not parse Attached GPUs"
        n = int(m.group(1))
        if n != expected_gpus:
            return False, f"Attached GPUs = {n}, expected {expected_gpus}"
        return True, f"{n} GPUs"
    if step.slug == "topo":
        if "NV" not in text:
            return False, "no NVLink links in topology matrix"
        return True, ""
    if step.slug == "stress":
        bad = [w for w in ("fail", "error") if w in low]
        if bad and "0 fail" not in low and "no error" not in low:
            return False, "stress output mentions fail/error — check FULLLOG"
        return True, ""
    return True, ""

# --------------------------------------------------------------------------
# Runners: local subprocess, SSH via the system ssh/scp, or dry (plan only)
# --------------------------------------------------------------------------


class LocalRunner:
    name = "local"
    dry = False

    def run(self, argv, timeout, sudo=False, sudo_password=None, stream=None):
        cmd = list(argv)
        stdin_data = None
        if sudo and os.geteuid() != 0:
            if sudo_password:
                cmd = ["sudo", "-S", "-p", ""] + cmd
                stdin_data = sudo_password + "\n"
            else:
                cmd = ["sudo", "-n"] + cmd
        return _exec(cmd, timeout, stdin_data, stream)

    def fetch(self, remote_path, dest_path):
        shutil.copyfile(remote_path, dest_path)


class SSHRunner:
    name = "ssh"
    dry = False

    def __init__(self, address, user=None, batch=True):
        self.address = address
        self.user = user
        self.target = f"{user}@{address}" if user else address
        self.opts = ["-o", "ConnectTimeout=15",
                     "-o", "StrictHostKeyChecking=accept-new"]
        if batch:
            self.opts += ["-o", "BatchMode=yes"]

    def run(self, argv, timeout, sudo=False, sudo_password=None, stream=None):
        remote = list(argv)
        stdin_data = None
        if sudo:
            if sudo_password:
                remote = ["sudo", "-S", "-p", ""] + remote
                stdin_data = sudo_password + "\n"
            else:
                remote = ["sudo", "-n"] + remote
        cmd = ["ssh"] + self.opts + [self.target, shlex.join(remote)]
        return _exec(cmd, timeout, stdin_data, stream)

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

    def run(self, argv, timeout, sudo=False, sudo_password=None, stream=None):
        shown = ("sudo " if sudo else "") + shlex.join(argv)
        text = f"[dry-run] would run: {shown}\n[dry-run] no host was contacted"
        if stream:
            for ln in text.splitlines():
                stream(ln)
        return 0, text, 0.0

    def fetch(self, remote_path, dest_path):
        raise OSError("dry-run: no fetch")


def _exec(cmd, timeout, stdin_data, stream=None):
    if stream is not None:
        return _exec_stream(cmd, timeout, stdin_data, stream)
    start = time.time()
    try:
        p = subprocess.run(cmd, input=stdin_data, capture_output=True,
                           text=True, timeout=timeout)
        out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")
        return p.returncode, out, time.time() - start
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode(errors="replace")
               if isinstance(e.stdout, bytes) else (e.stdout or ""))
        return 124, out + f"\n[TIMEOUT after {timeout}s]", time.time() - start
    except FileNotFoundError:
        return 127, f"[command not found: {cmd[0]}]", time.time() - start
    except Exception as e:  # never let one host kill the run
        return 1, f"[runner error: {e}]", time.time() - start


def _exec_stream(cmd, timeout, stdin_data, stream):
    """Streaming variant: lines are handed to `stream` as they arrive
    (stderr merged into stdout, terminal-style) while the full text is
    still accumulated for the FULLLOG. Timeout enforced via select."""
    start = time.time()
    try:
        p = subprocess.Popen(cmd,
                             stdin=subprocess.PIPE if stdin_data else None,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
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
    killed = False
    while True:
        if time.time() - start > timeout:
            p.kill()
            killed = True
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
            stream(line)
    if buf:
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
    return rc, out, time.time() - start

# --------------------------------------------------------------------------
# Per-host run
# --------------------------------------------------------------------------


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def harvest_nvsmlog(runner, run_start_epoch, host_dir):
    """Best-effort: fetch any *.nvsmlog created on the host since the run
    started (the NVSM stress test writes StressTestLog*.nvsmlog)."""
    got = []
    find_cmd = ["sh", "-c",
                "find ~ /var/log /tmp -name '*.nvsmlog' -newermt '@%d' -print 2>/dev/null"
                % int(run_start_epoch)]
    rc, out, _ = runner.run(find_cmd, 120)
    if rc != 0:
        return got
    for path in [ln.strip() for ln in out.splitlines() if ln.strip().endswith(".nvsmlog")][:5]:
        dest = os.path.join(host_dir, os.path.basename(path))
        try:
            runner.fetch(path, dest)
            got.append(os.path.basename(path))
        except Exception:
            pass
    return got


def print_plan(hostname, runner_desc, steps, host_dir):
    print(f"\n[dry-run] {hostname}  ({runner_desc})")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step.shown()}   [timeout {step.timeout}s]")
    print(f"  evidence -> {host_dir}")


def run_host(hostname, runner, out_root, steps, expected_gpus,
             sudo_password=None, force=False, nccl_min_busbw=0.0, tail=False):
    host_dir = os.path.join(out_root, hostname)
    os.makedirs(host_dir, exist_ok=True)
    fulllog_path = os.path.join(host_dir, f"{hostname}-FULLLOG.txt")
    dry = getattr(runner, "dry", False)
    result = {"hostname": hostname, "steps": {}, "notes": [],
              "overall": "", "release": ""}
    if os.path.exists(fulllog_path) and not force and not dry:
        result["overall"] = "SKIPPED"
        result["notes"].append("FULLLOG exists (use --force to redo)")
        return result

    run_start = time.time()
    transcript = []
    transcript.append("DGX Vitals — FULLLOG" + ("  [DRY RUN]" if dry else ""))
    transcript.append(f"Host: {hostname}   Mode: {runner.name}   Tool: dgx-vitals.py v{VERSION}")
    transcript.append(f"Started: {now_iso()}")
    if dry:
        transcript.append("DRY RUN: no commands were executed and no host was contacted.")
    transcript.append("=" * 72)

    def flush():
        with open(fulllog_path, "w") as f:
            f.write("\n".join(transcript) + "\n")

    n_pass = 0
    for i, step in enumerate(steps, 1):
        _say(f"  [{hostname}] step {i}/{len(steps)}: {step.label} ...")
        stream = (lambda ln: _say(f"  [{hostname}] | {ln}")) if tail else None
        rc, out, dur = runner.run(step.argv, step.timeout,
                                  sudo=step.sudo, sudo_password=sudo_password,
                                  stream=stream)
        if dry:
            ok, note = None, "dry-run"
        else:
            ok, note = verdict(step, rc, out, expected_gpus, nccl_min_busbw)
        n_pass += 1 if ok else 0
        result["steps"][step.slug] = (ok, note)
        if step.slug == "dgx-release" and ok:
            result["release"] = note
        state = "DRY" if ok is None else ("PASS" if ok else "FAIL")
        _say(f"  [{hostname}] step {i}/{len(steps)}: {step.label} — "
             f"{state} ({dur:.0f}s)")
        transcript.append(f"\n[{i}/{len(steps)}] {step.shown()}")
        transcript.append("-" * 72)
        transcript.append(out.rstrip())
        transcript.append(f"[exit {rc} · {dur:.1f}s · {state}"
                          + (f" · {note}" if note and not dry else "") + "]")
        flush()  # crash insurance on long runs

    if not dry:
        logs = harvest_nvsmlog(runner, run_start, host_dir)
        if logs:
            result["notes"].append("stress log: " + ", ".join(logs))
            transcript.append(f"\n[harvested stress log(s): {', '.join(logs)}]")

    total = len(steps)
    if dry:
        result["overall"] = "DRY RUN"
        result["pct"] = ""
    else:
        result["pct"] = round(100 * n_pass / total) if total else 0
        result["overall"] = "HEALTHY" if n_pass == total else "NOT HEALTHY"
    transcript.append("\n" + "=" * 72)
    transcript.append(f"OVERALL: {result['overall']}"
                      + ("" if dry else f" ({n_pass}/{total} steps passed)"))
    transcript.append(f"Finished: {now_iso()}")
    flush()

    rows = [(s.label, "skip" if result["steps"][s.slug][0] is None
             else ("pass" if result["steps"][s.slug][0] else "fail"))
            for s in steps]
    overall_state = {"HEALTHY": "pass", "NOT HEALTHY": "fail",
                     "DRY RUN": "skip"}[result["overall"]]
    render_health_card(os.path.join(host_dir, f"{hostname}-HEALTHY.png"),
                       hostname, now_iso(), rows,
                       result["overall"], overall_state,
                       release_line=result["release"])
    return result

# --------------------------------------------------------------------------
# Hosts file, summary, main
# --------------------------------------------------------------------------


def read_hosts(path):
    """One host per line. All of these work:

        hostname,address,user          (with this header row)
        hostname,address
        hostname                       (address defaults to the hostname)
        10.0.0.51                      (name defaults to the address)

    The header row is optional; blank lines and #-comments are ignored.
    Opened as utf-8-sig so a file saved by Excel doesn't corrupt the
    first hostname."""
    hosts = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return hosts
    has_header = "hostname" in lines[0].lower()
    rows = list(csv.reader(lines))
    if has_header:
        header = [h.strip().lower() for h in rows[0]]
        idx = {k: (header.index(k) if k in header else None)
               for k in ("hostname", "address", "user")}
        data = rows[1:]
    else:
        idx = {"hostname": 0, "address": 1, "user": 2}
        data = rows
    for row in data:
        def get(k):
            i = idx[k]
            return row[i].strip() if i is not None and i < len(row) else ""
        name = get("hostname")
        addr = get("address") or name
        user = get("user") or None
        if name:
            hosts.append((name, addr, user))
    return hosts


SUMMARY_COLUMNS = [("Release", ["dgx-release", "os-release"]),
                   ("NVSM Health", ["nvsm-health"]),
                   ("NVIDIA-SMI Query", ["smi-query"]),
                   ("NVLink Topology", ["topo"]),
                   ("NCCL Tests", ["nccl"]),
                   ("NVSM Stress Test", ["stress"]),
                   ("NVSM Dump Health", ["dump"])]


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
                        ok = r["steps"][slug][0]
                        cell = ("Dry Run" if ok is None else
                                ("Complete" if ok else "Failed"))
                row.append(cell)
            row.append(r.get("pct", ""))
            row.append(r["overall"])
            notes = "; ".join(r["notes"])
            fails = [f"{steps_by_slug[s].label}: {n}" for s, (ok, n)
                     in r["steps"].items() if ok is False and n]
            w.writerow(row + ["; ".join(fails + ([notes] if notes else []))])


def main():
    ap = argparse.ArgumentParser(description="DGX Vitals — DGX validation runner.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--local", action="store_true", help="validate this machine")
    mode.add_argument("--host", metavar="NAME", help="validate one host over SSH")
    mode.add_argument("--hosts", metavar="CSV", help="validate many hosts over SSH")
    ap.add_argument("--address", help="address for --host (default: the name itself)")
    ap.add_argument("--user", help="SSH user (overrides CSV user column)")
    ap.add_argument("--out", default="validation-output", help="output root dir")
    ap.add_argument("--jobs", type=int, default=4, help="parallel SSH workers")
    ap.add_argument("--gpus", type=int, default=8, help="expected GPU count")
    ap.add_argument("--profile", choices=["dgx", "hgx"], default="dgx",
                    help="dgx = NVSM + NCCL + smi (default); "
                         "hgx = NCCL + smi only (no NVSM on HGX)")
    ap.add_argument("--nccl-tests", default="all_reduce,all_gather,reduce_scatter",
                    help="comma-separated NCCL tests to run as the suite")
    ap.add_argument("--nccl-dir", default="",
                    help="directory holding the *_perf binaries "
                         "(default: find them on the PATH)")
    ap.add_argument("--nccl-timeout", type=int, default=2700,
                    help="seconds allowed for the whole NCCL suite")
    ap.add_argument("--nccl-min-busbw", type=float, default=0.0,
                    help="optional GB/s floor for the all-reduce peak bus "
                         "bandwidth (0 = correctness gate only)")
    ap.add_argument("--stress-timeout", type=int, default=5400,
                    help="seconds allowed for nvsm stress-test (default 5400)")
    ap.add_argument("--health-timeout", type=int, default=1800,
                    help="seconds allowed for nvsm show health (default 1800)")
    ap.add_argument("--dump-timeout", type=int, default=1800,
                    help="seconds allowed for nvsm dump health (default 1800)")
    ap.add_argument("--only", help="comma-separated step slugs to run "
                                   "(dgx-release,os-release,nvsm-health,smi-query,topo,nccl,stress,dump)")
    ap.add_argument("--force", action="store_true",
                    help="redo hosts that already have a FULLLOG")
    ap.add_argument("--tail", action="store_true",
                    help="stream each step's output live, prefixed by "
                         "hostname (best for one host or a few)")
    ap.add_argument("--dry-run", action="store_true",
                    help="execute nothing; print the plan and write DRY RUN artifacts")
    ap.add_argument("--sudo-password", action="store_true",
                    help="prompt once for a sudo password (held in memory only)")
    ap.add_argument("--no-batch", action="store_true",
                    help="allow interactive SSH prompts (single-host runs)")
    args = ap.parse_args()

    sudo_password = getpass.getpass("Sudo password (blank = passwordless): ") \
        if args.sudo_password else None
    if sudo_password == "":
        sudo_password = None

    nccl_tests = tuple(t.strip() for t in args.nccl_tests.split(",") if t.strip())
    steps = build_steps(args.profile, args.stress_timeout, args.health_timeout,
                        args.dump_timeout, args.nccl_timeout, nccl_tests,
                        args.nccl_dir, gpus=args.gpus)
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        steps = [s for s in steps if s.slug in wanted]
        if not steps:
            print("No steps matched --only.", file=sys.stderr)
            return 2
    steps_by_slug = {}
    for _p in ("dgx", "hgx"):
        for _s in build_steps(_p, args.stress_timeout, args.health_timeout,
                              args.dump_timeout, args.nccl_timeout, nccl_tests,
                              args.nccl_dir, gpus=args.gpus):
            steps_by_slug[_s.slug] = _s

    os.makedirs(args.out, exist_ok=True)
    jobs = []  # (hostname, runner, description)
    if args.local:
        import socket
        jobs.append((socket.gethostname(), LocalRunner(), "this machine"))
    elif args.host:
        r = SSHRunner(args.address or args.host, user=args.user,
                      batch=not args.no_batch)
        jobs.append((args.host, r, f"ssh -> {r.target}"))
    else:
        for name, addr, user in read_hosts(args.hosts):
            r = SSHRunner(addr, user=args.user or user, batch=not args.no_batch)
            jobs.append((name, r, f"ssh -> {r.target}"))

    if args.dry_run:
        jobs = [(n, DryRunner(r), d) for n, r, d in jobs]
        for name, _, desc in jobs:
            print_plan(name, desc, steps, os.path.join(args.out, name))

    print(f"\nValidating {len(jobs)} host(s); output -> {args.out}")
    results = []
    if len(jobs) == 1:
        name, runner, _ = jobs[0]
        results.append(run_host(name, runner, args.out, steps, args.gpus,
                                sudo_password, args.force, args.nccl_min_busbw,
                                args.tail))
    else:
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            futs = {ex.submit(run_host, name, runner, args.out, steps,
                              args.gpus, sudo_password, args.force,
                              args.nccl_min_busbw, args.tail): name
                    for name, runner, _ in jobs}
            for fut in as_completed(futs):
                r = fut.result()
                results.append(r)
                print(f"  [{r['overall']}] {r['hostname']}")
    results.sort(key=lambda r: r["hostname"])
    summary = os.path.join(args.out, "summary.csv")
    write_summary(summary, results, steps_by_slug)
    bad = [r["hostname"] for r in results if r["overall"] == "NOT HEALTHY"]
    print(f"Done. Summary: {summary}")
    if bad:
        print("NOT HEALTHY: " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
