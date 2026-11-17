#!/usr/bin/env python3
"""DGX Vitals run flowchart — SVG/PNG generator (house style).
Source of docs/flowchart.svg + docs/flowchart.png, written next to this
script. The PNG needs cairosvg or rsvg-convert (maintainer tool only; field
users never run this)."""
import os
import shutil
import subprocess
W, H = 1240, 1048
BG = "#0d1117"; BOX = "#161b22"; INNER = "#21262d"; LINE = "#8b949e"; TXT = "#e6edf3"
SUB = "#9da7b3"; ACC = "#58a6ff"; GRN = "#3fb950"; RED = "#f85149"; AMB = "#d2a83c"
F = "DejaVu Sans, sans-serif"
o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="{F}">']
o.append(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
o.append('<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#8b949e"/></marker></defs>')


def t(x, y, s, size=15, fill=TXT, anchor="middle", weight="normal"):
    o.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" font-weight="{weight}">{s}</text>')


def box(x, y, w, h, stroke=LINE, fill=BOX, r=10):
    o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>')


def arrow(x, y1, y2):
    o.append(f'<line x1="{x}" y1="{y1}" x2="{x}" y2="{y2-2}" stroke="{LINE}" stroke-width="2" marker-end="url(#ar)"/>')


CX = 620
t(CX, 50, "DGX Vitals — how a run works", 30, TXT, weight="bold")
t(CX, 80, "Checks by profile — DGX or HGX · One evidence folder per host · Fail closed: missing evidence is never HEALTHY.", 15, SUB)
box(470, 102, 300, 46, stroke=ACC, r=23)
t(CX, 131, "You run dgx-vitals.py", 17, TXT, weight="bold")
arrow(CX, 148, 170)
box(240, 172, 760, 58, stroke=AMB)
t(CX, 197, "FIRST — preflight with  --check  (read-only: runs no test, writes nothing)", 16, AMB, weight="bold")
t(CX, 219, "Per host: SSH, identity, sudo, nvsm, nccl-tests binaries, alerts, disk, clock. Fix what is NOT READY.", 13.5, SUB)
arrow(CX, 230, 252)
box(240, 254, 760, 96)
t(CX, 280, "Pick how you run it", 16, TXT, weight="bold")
for x, lines in [(256, ["MANY HOSTS", "laptop + hosts.csv file"]),
                 (500, ["ONE HOST", "laptop + --host flag"]),
                 (744, ["ON THE BOX ITSELF", "copy script over, --local"])]:
    box(x, 292, 240, 44, stroke=LINE, fill=INNER, r=8)
    t(x + 120, 310, lines[0], 13, ACC, weight="bold")
    t(x + 120, 328, lines[1], 12, SUB)
arrow(CX, 350, 372)
box(240, 374, 760, 314, stroke=ACC)
t(CX, 402, "For each host — in parallel, and safe to re-run", 16, TXT, weight="bold")
t(CX, 424, "Step zero checks identity first. DGX runs six checks (seven with --require-nccl); HGX four. FULLLOG is written as it goes.", 12.5, SUB)
steps = [("Release check — DGX software version (OS version on HGX)", None),
         ("sudo nvsm show health — overall system health (DGX only)", None),
         ("nvidia-smi -q — full GPU report (expected GPU count)", None),
         ("nvidia-smi topo -m — every GPU pair at the expected NVLink class", None),
         ("NCCL test suite — required on HGX; on DGX with --require-nccl",
          "all-reduce · all-gather · reduce-scatter — a test that ran and failed fails it"),
         ("sudo nvsm stress-test — the long one, 30–60 min (DGX only)", None),
         ("sudo nvsm dump health — final health snapshot (DGX only)", None)]
y = 438
for i, (s, sub) in enumerate(steps, 1):
    h = 46 if sub else 28
    box(264, y, 712, h, stroke="none", fill=INNER, r=6)
    t(284, y + 19, f"{i}", 15, ACC, weight="bold")
    t(308, y + 19, s, 14, TXT, anchor="start")
    if sub:
        t(308, y + 38, sub, 11.5, SUB, anchor="start")
    y += h + 4
arrow(CX, 688, 710)
PUR = "#a371f7"; ORA = "#db6d28"
for x, col, head, sub in [(240, GRN, "HEALTHY", "every check passed on evidence"),
                          (432, RED, "NOT HEALTHY", "something measured failed"),
                          (624, PUR, "INCOMPLETE", "evidence missing — reason given"),
                          (816, ORA, "UNREACHABLE", "step zero could not reach it")]:
    box(x, 712, 184, 74, stroke=col)
    t(x + 92, 740, head, 15, col, weight="bold")
    t(x + 92, 764, sub, 11, SUB)
arrow(CX, 786, 808)
box(240, 810, 760, 50)
t(CX, 831, "A card named for the verdict (&lt;host&gt;-NOT-HEALTHY.png …) + the stress log", 14.5, TXT)
t(CX, 851, "and dump archive nvsm printed, copied into the host's folder.", 14.5, TXT)
arrow(CX, 860, 882)
box(240, 884, 760, 50)
t(CX, 905, "One row per host lands in summary.csv — shaped like the tracker:", 14.5, TXT)
t(CX, 925, "Complete / Failed / Skipped / Incomplete per check, plus % Complete.", 14.5, TXT)
arrow(CX, 934, 956)
box(400, 958, 440, 46, stroke=GRN, r=23)
t(CX, 987, "Evidence ready to file", 17, GRN, weight="bold")
o.append("</svg>")
svg = "\n".join(o)
HERE = os.path.dirname(os.path.abspath(__file__))
svg_path = os.path.join(HERE, "flowchart.svg")
png_path = os.path.join(HERE, "flowchart.png")
with open(svg_path, "w") as f:
    f.write(svg + "\n")
try:
    import cairosvg
    cairosvg.svg2png(bytestring=svg.encode(), write_to=png_path, scale=2)
except ImportError:
    if not shutil.which("rsvg-convert"):
        raise SystemExit("flowchart.svg written; install cairosvg or "
                         "rsvg-convert to render flowchart.png")
    subprocess.run(["rsvg-convert", "-z", "2", "-o", png_path, svg_path],
                   check=True)
print("flowchart regenerated (step zero, verdict-named cards, four verdicts)")
