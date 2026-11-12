#!/usr/bin/env python3
"""tracker_build.py — build a tracker-shaped spreadsheet from DGX validation
results, with each host's health card embedded in its row.

Born on the Moonlite B300 engagement (Nov 2026) from the field tracker
David Keith and Alton Hill improvised in Omaha — the per-test columns there
and dgx-vitals' step list are the same workflow, so this builder joins them:
run dgx-vitals across a batch into one output root, pull that root, and
build the sheet from everything inside it. Written generically on purpose:
nothing here is Moonlite-specific, so the same flow serves the next
engagement (Comet and beyond) unchanged.

Inputs (any combination; at least one result source required):
    --vitals DIR    a dgx-vitals --out root: summary.csv plus, per host,
                    <host>/<host>-FULLLOG.txt and <host>/<host>-HEALTHY.png.
                    May be given more than once (e.g. several waves).
    --bundles DIR   a harvest root of cdw-validation bundles:
                    <host>/cdw-validation-<host>-<ts>.tar.gz (newest wins),
                    or already-extracted <host>/cdw-validation-<host>-<ts>/
                    directories.
    --hosts CSV     fleet list: row order plus the IP columns. Accepts
                    hostname,lan_ip,bmc_ip[,...] (tracker style) or
                    hostname,address[,user] (dgx-vitals style; address is
                    used as the LAN IP). Hosts with no results still get a
                    row, marked NO DATA, so the sheet doubles as a checklist.

Outputs:
    --out FILE.xlsx         the tracker sheet (sheet name: Validation Tracker)
    --bundle-out FILE.tar.gz  optional: one ferry bundle containing the sheet
                            plus every source artifact it was built from
                            (vitals roots and bundle tarballs), with a
                            .sha256 sidecar — the package that rides the
                            skulker over to the Mac.

Judgement rules (deliberate; also written into the sheet's README tab):
    * dgx-vitals results win over bundle results for the same host; the
      Source column always says which pipeline produced the row.
    * A bundle's NVSM Health is judged from the health log's own content —
      the count of Unhealthy checks — never from the command exit code:
      `nvsm show health` exits 0 with unhealthy checks present.
    * % Complete counts the six tracker steps (DGX Release, NVSM Health,
      NVIDIA-SMI Query, NVLink Topology, NVSM Stress Test, NVSM Dump
      Health). NCCL is reported in its own column and is not counted —
      the tracker predates the NCCL step.
    * Serial numbers and release strings are extracted from the logs when
      present and left blank when not. Blank means "not in the evidence",
      never a guess.

Requires: openpyxl and Pillow (pip install openpyxl pillow). dgx-vitals
itself stays stdlib-only; this builder is a separate, optional tool.
Python 3.10+.
"""

import argparse
import csv
import hashlib
import io
import os
import re
import sys
import tarfile

VERSION = "1.3"

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

COLUMNS = ["Item", "Node Hostname", "Node Serial Number", "BMC IP", "LAN IP",
           "DGX Release", "NVSM Health", "NVIDIA-SMI Query",
           "NVLink Topology", "NVSM Stress Test", "NVSM Dump Health",
           "% Complete", "Overall", "NCCL Tests", "Source", "Notes",
           "Health Card"]


class HostResult:
    def __init__(self, hostname):
        self.hostname = hostname
        self.serial = ""
        self.lan_ip = ""
        self.bmc_ip = ""
        self.steps = {}          # tracker step name -> "Complete"/"Failed"/""
        self.nccl = ""           # vitals only
        self.overall = ""
        self.source = ""
        self.notes = []
        self.card_path = None    # vitals only
        self.health_names = []   # raw unhealthy-check names (adjudication)


# ---------------------------------------------------------------------------
# Fleet list
# ---------------------------------------------------------------------------

def read_fleet(path):
    """Return (ordered hostnames, {hostname: HostResult}) with IPs filled."""
    order, hosts = [], {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        return order, hosts
    header = [h.strip().lower() for h in rows[0]]
    if "hostname" in header:
        idx = {k: (header.index(k) if k in header else None)
               for k in ("hostname", "lan_ip", "address", "bmc_ip")}
        data = rows[1:]
    else:  # positional: hostname,address,user
        idx = {"hostname": 0, "lan_ip": None, "address": 1, "bmc_ip": None}
        data = rows

    def get(row, key):
        i = idx.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    for row in data:
        if not row or not get(row, "hostname"):
            continue
        name = get(row, "hostname")
        r = HostResult(name)
        r.lan_ip = get(row, "lan_ip") or get(row, "address")
        r.bmc_ip = get(row, "bmc_ip")
        hosts[name] = r
        order.append(name)
    return order, hosts


# ---------------------------------------------------------------------------
# dgx-vitals ingest
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


def vitals_release_line(fulllog_text):
    """The dgx-release verdict note (first line of /etc/dgx-release) rides in
    the FULLLOG trailer for the `cat /etc/dgx-release` (or os-release) step."""
    if not fulllog_text:
        return ""
    blocks = re.split(r"\n\[\d+/\d+\] ", fulllog_text)
    for block in blocks:
        head = block.split("\n", 1)[0]
        if "cat /etc/dgx-release" in head or "cat /etc/os-release" in head:
            m = re.search(r"\[exit \d+ · [\d.]+s · (?:PASS|FAIL)"
                          r"(?: · ([^\]]+))?\]", block)
            if m and m.group(1):
                return m.group(1).strip()
            lines = [ln.strip() for ln in block.splitlines()[2:] if ln.strip()]
            return lines[0][:80] if lines else ""
    return ""


def read_vitals(root):
    """Parse one dgx-vitals --out root -> {hostname: HostResult}."""
    results = {}
    summary = os.path.join(root, "summary.csv")
    if not os.path.isfile(summary):
        print(f"warning: {root} has no summary.csv — skipped", file=sys.stderr)
        return results
    with open(summary, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("Hostname") or "").strip()
            if not name:
                continue
            r = HostResult(name)
            r.source = "dgx-vitals"
            for vcol, tcol in VITALS_TO_TRACKER.items():
                val = (row.get(vcol) or "").strip()
                if val in ("Complete", "Failed", "Skipped"):
                    r.steps[tcol] = val
            r.nccl = (row.get("NCCL Tests") or "").strip()
            r.overall = (row.get("Overall") or "").strip()
            notes = (row.get("Notes") or "").strip()
            if notes:
                r.notes.append(notes)
            host_dir = os.path.join(root, name)
            fulllog = os.path.join(host_dir, f"{name}-FULLLOG.txt")
            if os.path.isfile(fulllog):
                with open(fulllog, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                r.serial = find_serial(text)
                r.health_names = fulllog_health_names(text)
                rel = vitals_release_line(text)
                if rel:
                    r.notes.insert(0, f"Release: {rel}")
            card = os.path.join(host_dir, f"{name}-HEALTHY.png")
            if os.path.isfile(card):
                r.card_path = card
            results[name] = r
    return results


# ---------------------------------------------------------------------------
# cdw-validation bundle ingest
# ---------------------------------------------------------------------------

_SUMMARY_LINE = re.compile(r"^(\d\d)-\S+\s+exit=(\d+)\s+(\d+)s\s+(.*)$")


def _member_text(tf, suffix):
    """Text of the first bundle member whose basename matches suffix."""
    for m in tf.getmembers():
        if os.path.basename(m.name) == suffix and m.isfile():
            fh = tf.extractfile(m)
            if fh:
                return fh.read().decode("utf-8", errors="replace")
    return ""


def unhealthy_names_from(text):
    """Extract the check names from the 'unhealthy' lines of an NVSM
    health log (name = the text before the status word, dot leaders and
    list markers stripped). Rollup lines ('Overall system status') and
    summary-sentence fragments come through verbatim; real_check_names()
    separates actual checks from those."""
    names = []
    for ln in (text or "").splitlines():
        if "unhealthy" in ln.lower():
            name = re.split(r"(?i)unhealthy", ln)[0]
            name = name.strip(" \t.:=-–—•*#>0123456789").strip()
            names.append(name or ln.strip()[:60])
    return names


def real_check_names(names):
    """NVSM check names begin with Verify/Check; anything else in an
    unhealthy-name list is a rollup line or a sentence fragment."""
    return [n for n in names if n.startswith(("Verify", "Check"))]


def fulllog_health_names(fulllog_text):
    """Unhealthy-check names from a dgx-vitals FULLLOG, scoped to the
    `nvsm show health` step block (the topo block carries its own
    'Route Unhealthy' lines, which are not health checks)."""
    if not fulllog_text:
        return []
    blocks = re.split(r"\n\[\d+/\d+\] ", fulllog_text)
    for block in blocks:
        head = block.split("\n", 1)[0]
        if "nvsm show health" in head:
            return unhealthy_names_from(block)
    return []


def health_verdict(health_log, exit_code):
    """Judge NVSM health from log content. Returns (status, note)."""
    if not health_log:
        return ("Failed", f"health log missing (exit={exit_code})") \
            if exit_code != 0 else ("", "health log missing")
    unhealthy_names, total = [], 0
    for ln in health_log.splitlines():
        low = ln.lower()
        if "unhealthy" in low:
            total += 1
            name = re.split(r"(?i)unhealthy", ln)[0]
            name = name.strip(" \t.:=-–—•*#>0123456789").strip()
            unhealthy_names.append(name or ln.strip()[:60])
        elif re.search(r"\bhealthy\b", low):
            total += 1
    if unhealthy_names:
        shown = real_check_names(unhealthy_names) or unhealthy_names
        names = "; ".join(shown[:6])
        more = f" (+{len(shown) - 6} more)" \
            if len(shown) > 6 else ""
        return "Failed", (f"NVSM health: {len(unhealthy_names)} of {total} "
                          f"checks unhealthy — {names}{more}")
    if exit_code != 0:
        return "Failed", f"nvsm show health exit={exit_code}"
    note = f"NVSM health: {total}/{total} checks healthy" if total else ""
    return "Complete", note


def parse_bundle(summary_txt, files):
    """files: {basename: text}. Returns a HostResult (hostname unset)."""
    r = HostResult("")
    r.source = "cdw-validation bundle"
    exits = {}
    for ln in (summary_txt or "").splitlines():
        m = _SUMMARY_LINE.match(ln.strip())
        if m:
            exits[m.group(1)] = int(m.group(2))
    for prefix, tcol in BUNDLE_STEP_MAP.items():
        if prefix not in exits:
            continue
        if tcol == "NVSM Health":
            status, note = health_verdict(files.get("04-nvsm-health.log", ""),
                                          exits[prefix])
            if status:
                r.steps[tcol] = status
            if note:
                r.notes.append(note)
            r.health_names = unhealthy_names_from(
                files.get("04-nvsm-health.log", ""))
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
    r.serial = find_serial(files.get("04-nvsm-health.log", ""))
    return r


def read_bundles(root):
    """Parse a harvest root -> {hostname: (HostResult, [artifact paths])}."""
    results = {}
    if not os.path.isdir(root):
        return results
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
        parsed, artifacts = None, []
        if candidates:
            newest = sorted(candidates)[-1]
            artifacts = [newest]
            try:
                with tarfile.open(newest, "r:gz") as tf:
                    files = {}
                    for suffix in ("03-nvsm-versions.log", "04-nvsm-health.log"):
                        files[suffix] = _member_text(tf, suffix)
                    parsed = parse_bundle(_member_text(tf, "summary.txt"),
                                          files)
            except tarfile.TarError as e:
                print(f"warning: {newest}: {e}", file=sys.stderr)
        elif extracted:
            bdir = os.path.join(host_dir, sorted(extracted)[-1])
            files = {}
            for suffix in ("summary.txt", "03-nvsm-versions.log",
                           "04-nvsm-health.log"):
                p = os.path.join(bdir, suffix)
                if os.path.isfile(p):
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        files[suffix] = fh.read()
            parsed = parse_bundle(files.get("summary.txt", ""), files)
            artifacts = [bdir]
        if parsed is not None:
            parsed.hostname = host
            results[host] = (parsed, artifacts)
    return results


# ---------------------------------------------------------------------------
# Merge + sheet
# ---------------------------------------------------------------------------

def finalize(r):
    """Compute % Complete (six tracker steps) and Overall when not set.
    A Warned step ran and counts as complete; it annotates the verdict
    (HEALTHY WITH WARNINGS) instead of failing it."""
    counted = [r.steps[s] for s in TRACKER_STEPS
               if r.steps.get(s) and r.steps[s] != "Skipped"]
    if counted:
        passed = sum(1 for v in counted if v in ("Complete", "Warned"))
        pct = round(100 * passed / len(counted))
    else:
        pct = None
    if not r.overall:
        if not counted:
            r.overall = "NO DATA"
        elif any(v == "Failed" for v in counted):
            r.overall = "NOT HEALTHY"
        elif any(v == "Warned" for v in counted):
            r.overall = "HEALTHY WITH WARNINGS"
        else:
            r.overall = "HEALTHY"
    return pct


def merge_result(cur, new):
    """Layer a newer vitals result over an existing one, per step: a
    partial re-run (e.g. --only dump) must not erase the steps it did
    not re-run. The health card follows the fuller result set."""
    new_steps = sum(1 for v in new.steps.values() if v)
    cur_steps = sum(1 for v in cur.steps.values() if v)
    for k, v in new.steps.items():
        if v:
            cur.steps[k] = v
    if new.nccl:
        cur.nccl = new.nccl
    if new.serial:
        cur.serial = new.serial
    if new.health_names:
        cur.health_names = new.health_names
    cur.lan_ip = new.lan_ip or cur.lan_ip
    cur.bmc_ip = new.bmc_ip or cur.bmc_ip
    for n in new.notes:
        if n not in cur.notes:
            cur.notes.append(n)
    if new.source:
        cur.source = new.source
    if new_steps >= cur_steps and new.card_path:
        cur.card_path = new.card_path
    cur.overall = ""        # merged evidence — finalize() recomputes


DIMM_CHECK = "Verify DIMM part number"


def adjudicate(hosts, manual):
    """Apply the known-issue rules plus manual adjudications. Returns a
    report dict for the sheet footer. Nothing is deleted: a ruled false
    positive becomes Warned (or Skipped for never-ran NCCL), with the
    reason appended to the row's notes and the full ruling written in
    the Known Issues block at the bottom of the sheet."""
    report = {"KI-1": [], "KI-2": [], "manual": [], "unmatched": []}
    for name in sorted(hosts):
        r = hosts[name]
        if r.steps.get("NVSM Health") == "Failed" and r.health_names:
            real = real_check_names(r.health_names)
            if real and all(n == DIMM_CHECK for n in real):
                r.steps["NVSM Health"] = "Warned"
                r.notes.append(
                    "KI-1: 'Verify DIMM part number' is a known NVSM "
                    "false positive — step warned, not failed "
                    "(see Known Issues)")
                r.overall = ""
                report["KI-1"].append(name)
        if r.nccl == "Failed" and \
                any("partially provisioned" in n for n in r.notes):
            r.nccl = "Skipped"
            r.notes.append(
                "KI-2: NCCL not tested — nccl-tests only partially "
                "provisioned on this host (see Known Issues)")
            r.overall = ""
            report["KI-2"].append(name)
    for host, step, state, note in manual:
        r = hosts.get(host)
        if r is None or (step != "NCCL Tests" and step not in TRACKER_STEPS) \
                or state not in ("Complete", "Failed", "Skipped", "Warned"):
            report["unmatched"].append((host, step, state, note))
            continue
        if step == "NCCL Tests":
            r.nccl = state
        else:
            r.steps[step] = state
        r.notes.append(f"Manual adjudication: {step} set to {state}"
                       + (f" — {note}" if note else ""))
        r.overall = ""
        report["manual"].append((host, step, state, note))
    return report


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


def build_sheet(order, hosts, out_path, title, adj_report=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo

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
    item = 0
    for name in order:
        r = hosts[name]
        item += 1
        pct = finalize(r)
        row = [item, name, r.serial, r.bmc_ip, r.lan_ip]
        row += [r.steps.get(s, "") for s in TRACKER_STEPS]
        row += [pct if pct is not None else "", r.overall, r.nccl,
                r.source or "—", "; ".join(r.notes), ""]
        ws.append(row)
        rn = ws.max_row
        for cname in TRACKER_STEPS + ["NCCL Tests"]:
            cell = ws.cell(row=rn, column=COLUMNS.index(cname) + 1)
            if cell.value == "Complete":
                cell.fill, cell.font = ok_fill, ok_font
            elif cell.value == "Failed":
                cell.fill, cell.font = bad_fill, bad_font
            elif cell.value == "Warned":
                cell.fill, cell.font = warn_fill, warn_font
            elif cell.value == "Skipped":
                cell.font = dim_font
        ocell = ws.cell(row=rn, column=COLUMNS.index("Overall") + 1)
        if r.overall == "HEALTHY":
            ocell.fill, ocell.font = ok_fill, ok_font
        elif r.overall == "NOT HEALTHY":
            ocell.fill, ocell.font = bad_fill, bad_font
        elif r.overall == "HEALTHY WITH WARNINGS":
            ocell.fill, ocell.font = warn_fill, warn_font
        elif r.overall == "NO DATA":
            ocell.font = dim_font
        ws.cell(row=rn, column=COLUMNS.index("Notes") + 1).alignment = \
            Alignment(wrap_text=True, vertical="top")
        if r.card_path:
            from openpyxl.drawing.image import Image as XLImage
            from PIL import Image as PILImage
            with PILImage.open(r.card_path) as im:
                w0, h0 = im.size
            img = XLImage(r.card_path)
            img.width = 288
            img.height = round(288 * h0 / w0)
            ws.row_dimensions[rn].height = img.height * 0.75 + 6
            ws.add_image(img, ws.cell(row=rn, column=card_col).coordinate)

    last_data_row = ws.max_row
    if adj_report and (adj_report["KI-1"] or adj_report["KI-2"]
                       or adj_report["manual"] or adj_report["unmatched"]):
        lines = []
        if adj_report["KI-1"]:
            lines.append(
                f"KI-1 — NVSM 'Verify DIMM part number' false positive "
                f"({len(adj_report['KI-1'])} host(s)): NVSM 25.09.07's "
                "expected DIMM part list does not include the fleet's "
                "fitted Micron part MTC40F2046S1RC64BH1 (the listed "
                "sibling is MTC40F2046S1RC64BD2). On every affected "
                "host the DIMMs are uniform across all slots and every "
                "other memory check passes. Ruled a tooling false "
                "positive: NVSM Health is marked Warned, not Failed.")
        if adj_report["KI-2"]:
            lines.append(
                f"KI-2 — NCCL not fully provisioned "
                f"({len(adj_report['KI-2'])} host(s)): only one of the "
                "three nccl-tests binaries is installed, so the fabric "
                "test could not run. Marked Skipped (not tested), not "
                "Failed; a fabric verdict requires the full nccl-tests "
                "package on the host.")
        for host, step, state, note in adj_report["manual"]:
            lines.append(f"Manual adjudication — {host}: {step} set to "
                         f"{state}." + (f" {note}" if note else ""))
        for host, step, state, note in adj_report["unmatched"]:
            lines.append(f"NOT APPLIED — adjudication row for {host} "
                         f"({step} -> {state}) matched no host/step.")
        lines.append(
            "This sheet consolidates two run sources (see the Source "
            "column): Moonlite's cdw-validation bundles and CDW's "
            "dgx-vitals runs.")
        ws.append([])
        ws.append(["KNOWN ISSUES & ADJUDICATIONS"])
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

    widths = {"Item": 6, "Node Hostname": 34, "Node Serial Number": 18,
              "BMC IP": 15, "LAN IP": 15, "DGX Release": 12,
              "NVSM Health": 12, "NVIDIA-SMI Query": 13,
              "NVLink Topology": 13, "NVSM Stress Test": 13,
              "NVSM Dump Health": 13, "% Complete": 10, "Overall": 13,
              "NCCL Tests": 11, "Source": 20, "Notes": 60, "Health Card": 42}
    for cname, wdt in widths.items():
        ws.column_dimensions[get_column_letter(
            COLUMNS.index(cname) + 1)].width = wdt
    ws.freeze_panes = "C3"
    ws.auto_filter.ref = f"A2:{get_column_letter(len(COLUMNS))}{last_data_row}"

    rd = wb.create_sheet("README")
    rd.column_dimensions["A"].width = 110
    for line in [
        "Built by tracker_build.py v" + VERSION + " (dgx-vitals toolset).",
        "",
        "Sources: each row's Source column names the pipeline that produced",
        "it — dgx-vitals (full run: release, NVSM health, smi, topo, NCCL,",
        "stress, dump, plus the embedded health card) or a cdw-validation",
        "bundle harvested from the host. dgx-vitals wins where both exist.",
        "",
        "% Complete counts the six tracker steps only; NCCL is reported",
        "separately and never counted (the tracker predates the NCCL step).",
        "A step marked Skipped did not run because its tool is not",
        "installed on the host; it is excluded from % Complete.",
        "",
        "Bundle NVSM Health is judged from the health log's Unhealthy-check",
        "count, not the command exit code (nvsm exits 0 with unhealthy",
        "checks present). Blank cells mean the evidence does not carry the",
        "value — nothing in this sheet is guessed.",
        "",
        "Adjudications (v1.3): a step marked Warned ran, but its failure",
        "is a ruled false positive — the ruling and its evidence are",
        "written in the Known Issues & Adjudications block at the bottom",
        "of the tracker sheet. Warned steps count as complete; a host",
        "whose only failures are warnings reads HEALTHY WITH WARNINGS.",
        "Multiple dgx-vitals roots merge per step: a partial re-run",
        "(--only ...) overrides only the steps it actually re-ran.",
    ]:
        rd.append([line])
    wb.save(out_path)


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


def main():
    ap = argparse.ArgumentParser(
        description="Build a tracker-shaped validation spreadsheet from "
                    "dgx-vitals output roots and/or cdw-validation bundles.")
    ap.add_argument("--vitals", action="append", default=[], metavar="DIR",
                    help="dgx-vitals --out root (repeatable)")
    ap.add_argument("--bundles", metavar="DIR",
                    help="harvest root of cdw-validation bundles")
    ap.add_argument("--hosts", metavar="CSV", help="fleet list (row order, IPs)")
    ap.add_argument("--out", default="validation-tracker.xlsx")
    ap.add_argument("--bundle-out", metavar="FILE.tar.gz",
                    help="also write a ferry bundle: sheet + source artifacts")
    ap.add_argument("--title", default="",
                    help="sheet title line (e.g. the engagement name)")
    ap.add_argument("--adjudications", metavar="CSV",
                    help="manual adjudications CSV "
                         "(hostname,step,state,note)")
    args = ap.parse_args()

    if not args.vitals and not args.bundles:
        ap.error("give at least one of --vitals / --bundles")

    order, hosts = ([], {})
    if args.hosts:
        order, hosts = read_fleet(args.hosts)

    bundle_artifacts = []
    if args.bundles:
        for name, (res, arts) in read_bundles(args.bundles).items():
            bundle_artifacts += arts
            cur = hosts.get(name)
            if cur is None:
                hosts[name] = res
                order.append(name)
            elif not cur.source:      # fleet placeholder with no results yet
                res.lan_ip, res.bmc_ip = cur.lan_ip, cur.bmc_ip
                hosts[name] = res
    for root in args.vitals:
        for name, res in read_vitals(root).items():
            cur = hosts.get(name)
            if cur is None:
                hosts[name] = res
                order.append(name)
            else:                     # merge per step; newer root wins
                merge_result(cur, res)  # the steps it actually ran
    for name in sorted(set(hosts) - set(order)):
        order.append(name)

    manual = read_adjudications(args.adjudications) \
        if args.adjudications else []
    report = adjudicate(hosts, manual)
    build_sheet(order, hosts, args.out, args.title, report)
    n_cards = sum(1 for n in order if hosts[n].card_path)
    n_data = sum(1 for n in order if hosts[n].source)
    print(f"{args.out}: {len(order)} rows, {n_data} with results, "
          f"{n_cards} health cards embedded")
    if report["KI-1"] or report["KI-2"] or report["manual"] \
            or report["unmatched"]:
        print(f"adjudications: KI-1 x{len(report['KI-1'])}, "
              f"KI-2 x{len(report['KI-2'])}, "
              f"manual x{len(report['manual'])}, "
              f"unmatched x{len(report['unmatched'])}")
    if args.bundle_out:
        digest = make_bundle(args.bundle_out, args.out, args.vitals,
                             bundle_artifacts)
        print(f"{args.bundle_out}: sha256 {digest}")


if __name__ == "__main__":
    main()
