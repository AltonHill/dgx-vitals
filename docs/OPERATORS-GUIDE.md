# DGX Vitals: Operator's Guide

This is the field runbook for validating a fleet of DGX (or HGX) systems
with `dgx-vitals.py` and turning the results into the tracker
spreadsheet with `tracker_build.py`. There are six steps: fill in a hosts
file, preflight, run, watch, check status, and build the sheet. For the
one-page version, see [`QUICKSTART.md`](../QUICKSTART.md).

Per host, the tool produces the following, with no manual SSH sessions
and no hand-typed results:

- `<hostname>-FULLLOG.txt`: the full transcript of every check, with a
  trailer line per check giving its verdict and the reason.
- `<hostname>-<VERDICT>.png`: a health card image named for the verdict
  (`-HEALTHY`, `-NOT-HEALTHY`, `-INCOMPLETE`, `-UNREACHABLE`). Runs from
  before v2.0 named every card `-HEALTHY.png`.
- `StressTestLog*.nvsmlog` and the `nvsm-health-*` dump archive: the
  exact files nvsm printed it wrote, copied off the host.
- One `summary.csv` per run: one row per host, one column per check.

**Prerequisites**

- Python 3.10 or newer on the machine you run from. `dgx-vitals.py`
  uses only the standard library, so there is nothing to pip install.
- `tracker_build.py` additionally needs `pip install openpyxl pillow`,
  and must sit in the same folder as `dgx-vitals.py` (it imports the
  runner's verdict vocabulary).
- OpenSSH (`ssh` / `scp`) on your PATH. The tool shells out to the system
  SSH, so your `~/.ssh/config` applies to its connections.
- SSH access to every target host, as a user whose sudo works. The NVSM
  steps run under sudo, and step zero reads the chassis serial with sudo
  on both profiles. If you cannot SSH in by hand, fix that first: the
  tool uses the same access you do.
- Linux or macOS for `--tail` and the password-site recipe below. On
  Windows, run without `--tail`.

---

## Step 1: Fill in the hosts CSV

One CSV drives a run:

```csv
hostname,address,user
dgx-01,192.0.2.51,ubuntu
dgx-02,192.0.2.52,ubuntu
```

- **hostname**: the node's name. It names the evidence folder and the
  row in the tracker, so use the name the install tracker uses. Step
  zero checks it against the name the machine itself reports. A
  mismatch stops that host before any check runs (see Snags).
- **address**: where SSH connects. This is the host's OS (LAN) address
  or a resolvable name, *not* the BMC address: the BMC cannot run these
  checks.
- **user**: the SSH/sudo account on the hosts.
- Optional columns, any order: `gpus` (expected GPU count for that
  host, when it is not the profile's 8), `nvlink` (expected
  `nvidia-smi topo -m` class, default `NV18`), `serial` (expected chassis
  serial, checked at step zero).

Details that matter:

- A **tracker-style fleet list** (`hostname,lan_ip,bmc_ip`) works as a
  hosts file too. The tool connects to `lan_ip`. Both tools read hosts
  files with the same parser.
- The header row is optional, but a header must call its hostname column
  exactly `hostname`. A header like `Node Hostname,…` is refused with an
  error (exit 1) rather than read as zero hosts. Rows with just
  `hostname,address` or just a hostname also parse (the address then
  defaults to the hostname). Blank lines and `#` comments are ignored.
- **Keep a master fleet CSV** with every host in it. That file is the
  fleet list you later hand to `tracker_build.py` (Step 6).
- **Slice a wave per run.** A run does not have to be the whole fleet.
  Copy the rows for the subset you are validating into a small CSV of
  its own (`hosts-wave-a.csv`) and run against that. Each wave gets its
  own CSV and its own output folder.
- **Line endings: LF only** if a shell loop will read the file (Step
  2.1). A CRLF file glues a carriage return onto the last field, and a
  loop then tries to log in as `ubuntu\r`. Python's CSV reader tolerates
  CRLF; the shell does not. Check with `grep -c $'\r' hosts-wave-a.csv`:
  the answer should be 0.

## Step 2: Preflight with `--check`

Before an hour of stress per host, ask every host whether it is ready:

```bash
python3 dgx-vitals.py --hosts hosts-wave-a.csv --profile dgx \
    --sudo-password --check
```

`--check` is read-only: it runs no test step, changes nothing on any
host and writes nothing. Per host it reports READY or NOT READY, line by
line: SSH reachability, platform, sudo, identity (hostname and chassis
serial against your CSV), nvsm, standing NVSM alerts (stress refuses to
run over them), each nccl-tests binary by name, free disk, and clock
skew. It exits 0 only if every host is READY.

With `--sudo-password`, the password is tried on the first host that
answers. If it is rejected there, the other hosts are not tried, so a
typo costs one host and no lockouts. Fix every NOT READY line before you
launch, or launch knowing what those hosts will report.

## Step 3: Run the command

### 3.1 Password sites: open SSH masters first

By default the tool connects with SSH **BatchMode** on, so SSH never
stops to ask a password mid-run. On sites where the hosts take a
**password** (no key auth), the connections must be authenticated
before you launch. Use SSH connection sharing.

Add a block like this to `~/.ssh/config`. The `Host` pattern must match
what is in your CSV's address column: if the column holds names rather
than IPs, list those names, or use `Host *`.

```
Host 192.0.2.*
    User ubuntu
    PreferredAuthentications password
    PubkeyAuthentication no
    ControlMaster auto
    ControlPath ~/.ssh/cm/%C
    ControlPersist 8h
```

Create the socket directory once with `mkdir -p ~/.ssh/cm` and
`chmod 700 ~/.ssh/cm`.

Then open one master connection per host in the wave CSV, typing each
password once. This loop skips the header, blank lines and comments,
handles rows without a user, and does not drop a last line that has no
newline:

```bash
while IFS=, read -r name addr user _rest || [ -n "$name" ]; do
  case "$name" in ''|'#'*|hostname) continue ;; esac
  addr=${addr:-$name}
  ssh -MNf ${user:+"$user@"}"$addr"
done < hosts-wave-a.csv
```

Every later SSH to those hosts, yours and the tool's, rides the master
for the `ControlPersist` window (8 hours above). If a run suddenly
reports every host UNREACHABLE, the masters have expired. Re-open them
the same way. With key-based SSH, skip this section.

*Single password-only host, no master:* pass `--no-batch` to let SSH
prompt interactively. That is fine for one host; do not run a fleet that
way.

### 3.2 The launch line, flag by flag

```bash
python3 dgx-vitals.py --hosts hosts-wave-a.csv --profile dgx \
    --sudo-password --jobs 8 --tail --out validation-output-wave-a
```

- `--hosts CSV`: the wave CSV from Step 1. The alternatives are
  `--host NAME [--address A] [--user U] [--serial S]` for one host and
  `--local` for the machine you are sitting on.
- `--profile dgx|hgx|auto`: which checks each host gets.
  - `dgx` (default): **six checks**: DGX Release, NVSM Show Health,
    NVIDIA-SMI Query, NVLink Topology, NVSM Stress Test, NVSM Dump
    Health. The run prints `step 1/6` … `step 6/6`. With
    `--require-nccl`, NCCL Tests joins as the fifth of **seven**.
  - `hgx`: **four checks**: OS Release, NVIDIA-SMI Query, NVLink
    Topology, NCCL Tests. NVSM does not exist on OEM HGX systems, so on
    HGX the NCCL run carries the fabric proof and is **required**.
  - `auto`: step zero asks each host (`/etc/dgx-release` or the `nvsm`
    CLI means dgx, neither means hgx) and builds its check list to
    match. If step zero cannot reach a host, the host is UNREACHABLE:
    no profile is guessed.
- `--sudo-password`: prompt once, up front, for the hosts' sudo
  password. It is held in memory only, never written to disk. Without
  it, sudo uses `sudo -n` (passwordless). Pressing Enter at the prompt
  also selects passwordless.
- `--jobs N`: how many hosts to validate in parallel (default 4).
- `--tail`: stream each step's output live (Step 4). Best with one host
  or a few; not available on Windows.
- `--out DIR`: the output root. **Use a fresh root per wave**. Each run
  writes its own `summary.csv` into its root. The sheet build (Step 6)
  merges roots, not the folder.

Also available: `--gpus N` / `--nvlink CLASS` (expected GPU count and
NVLink class for every host, when not the profile's 8 / NV18; per host
via the CSV columns), `--nccl-dir DIR`, `--nccl-tests all_reduce,...`,
`--nccl-min-busbw GBPS`, and the timeouts `--health-timeout` (default
1800s), `--stress-timeout` (5400s), `--dump-timeout` (3600s) and
`--nccl-timeout` (2700s).

**Before a run (optional):** `--dry-run` prints the exact commands per
host and writes sample artifacts stamped DRY RUN under `<out>/dry-run/`.
It never writes beside real results and contacts no host. It refuses an
`--out` root that already holds real results.

**Interrupting and resuming.** Ctrl-C stops the wave cleanly: no queued
host starts, no running host starts another step, and the step in flight
is cut short and recorded in the FULLLOG as `INTERRUPTED:` with no
verdict, never as a failure. That step's process group on the host is
signalled, as after a timeout. The summary is written for what finished,
and the run exits 130. A second Ctrl-C exits immediately. **To resume,
launch the same command again.** A host whose FULLLOG is *finished*
(it has its `OVERALL:` line) is left alone, and its summary row reads
`SKIPPED` ("finished FULLLOG exists"). A host whose FULLLOG is
unfinished is redone, and the partial log is kept beside it as
`<host>-FULLLOG.interrupted-<UTC>.txt`. `--force` also redoes finished
hosts. Use it deliberately.

**Exit codes:** 0 when every host is HEALTHY; 2 when any host is NOT
HEALTHY, INCOMPLETE or UNREACHABLE (those hosts are listed on the last
lines); 1 for a tool error (bad flags, a malformed hosts file, or a
sudo password rejected on the first host); 130 when you interrupted it.

### 3.3 Re-runs: one step, a subset of hosts

Re-runs are targeted: a small CSV with just the hosts that need it,
`--only` with just the step, and a fresh output root.

Step slugs for `--only`: `dgx-release`, `os-release`, `nvsm-health`,
`smi-query`, `topo`, `nccl`, `stress`, `dump`.

To redo the stress test on three hosts, with NVSM's alert refusal
overridden (see Snags for when this is legitimate):

```bash
python3 dgx-vitals.py --hosts rerun-stress.csv --profile dgx \
    --only stress --force-stress --sudo-password --jobs 3 \
    --out validation-output-rerun-stress
```

- `--force-stress` appends NVSM's own `--force` (`nvsm stress-test
  --no-prompt --force`), overriding its refusal to run while system
  alerts are uncleared. Use it only when the alerts are known and
  adjudicated. Without it, a refusal is scored **Skipped**. The step
  did not run, so the host is INCOMPLETE.
- `--require-nccl` **adds the NCCL step on DGX and requires it.** If
  nccl-tests is not installed, the step is Skipped with the missing
  binaries named, and the host is **INCOMPLETE** (missing evidence, not
  a fault). A test that ran and failed is **NOT HEALTHY**. On HGX, NCCL
  is already required.
- `--no-require-nccl` is the **HGX opt-out**, for an engagement that has
  agreed to validate without NCCL. The NCCL step still runs if it can:
  if it passes, the host can be HEALTHY, and if it ran and failed, the
  host is NOT HEALTHY. If it is skipped (nccl-tests not installed), the
  waiver is printed on the host's verdict line and in the summary. A
  waived test is still missing evidence, so the host reads
  **INCOMPLETE** (never HEALTHY), and the sheet names the waiver. On DGX
  the flag waives nothing.

## Step 4: Watch the logs

With `--tail`, every line a check prints streams to your terminal as
it happens, prefixed with the host:

```
  [dgx-01] step 3/6: NVIDIA-SMI Query ...
  [dgx-01] | Attached GPUs         : 8
  [dgx-01] step 3/6: NVIDIA-SMI Query — PASS (41s)
```

Without `--tail` you still get the announce and verdict lines. Any
verdict other than PASS is printed with its reason, for example
`step 5/6: NVSM Stress Test — SKIP (1s) — nvsm refused to run: uncleared
system alerts on this host …`.

Everything is also written, verbatim, to the host's FULLLOG
(`<out>/<hostname>/<hostname>-FULLLOG.txt`). Each check appears with
the exact command, its full output, and a trailer line:

```
[5/6] sudo nvsm stress-test --no-prompt
...
[exit 0 · 1827.4s · PASS · 5/5 components PASS, 100% Complete, 1827s]
```

The FULLLOG is flushed at the start and end of every step, so a run in
progress can be read live and a crashed run leaves a usable log. A log
only counts as finished once it carries its `OVERALL:` line. The health
card is written last, with the host's verdict in its name.

PASS needs positive evidence. Absence of error text is never enough. If
a verdict surprises you, the FULLLOG is the court of appeal: the raw
output is always there, and Step 6's adjudications exist to overrule a
known false positive on the record.

## Step 5: View status with the `--watch` scoreboard

From a second terminal, while a wave runs (or after it finishes):

```bash
python3 dgx-vitals.py --watch --hosts hosts-wave-a.csv \
    --out validation-output-wave-a
```

`--watch` contacts nothing. It reads the output root, takes its host
list from the wave CSV, and classifies each host:

- **done**: the FULLLOG carries its `OVERALL:` line (the verdict is
  shown). A health card on its own does not count.
- **running**: a FULLLOG exists without `OVERALL:` and was updated
  within the running step's timeout. The board shows which step.
- **interrupted**: the run was stopped with Ctrl-C. Re-launch to redo it.
- **stalled**: no FULLLOG update for longer than the running step's
  timeout plus 10 minutes. The run probably died. Re-launch to redo it.
- **waiting**: no log yet.

It shows the `{done} / {total}` count in big block letters, refreshes
every 30 seconds (`--interval 10` for faster), and lists every host.
When the last host completes, it prints WAVE DONE with the tally and
exits 0. If nothing is running any more but some host is interrupted
or stalled, it prints **WAVE STOPPED** and exits 2 instead of waiting
forever. If no host has a log under `--out`, it says so: check that the
path matches the run's `--out`. `--once` prints a single plain-text
snapshot for scripts.

## Step 6: Generate the spreadsheet

`tracker_build.py` consolidates one or more run roots (plus, optionally,
results harvested outside dgx-vitals) into the tracker-shaped workbook
and a CSV of the same rows. The workbook has one row per host, the
per-check columns, each host's health card embedded, and a README tab
documenting how every value was judged.

```bash
python3 tracker_build.py \
    --vitals validation-output-wave-a validation-output-rerun-stress \
    --bundles /path/to/bundle-harvest \
    --hosts hosts-fleet.csv \
    --title "<Engagement> — Validation Tracker" \
    --out validation-tracker.xlsx \
    --bundle-out validation-bundle.tar.gz
```

- `--vitals DIR [DIR ...]`: dgx-vitals output roots. One flag takes
  several, and it may be repeated. **Give roots oldest first, re-run
  roots last**, because they merge **per check in the order given**:
  - a later root overrides only the checks it actually ran;
  - a real result replaces a Skipped or Incomplete one from any root;
  - a Skipped or Incomplete re-run never erases a real result;
  - an UNREACHABLE attempt never overrides results;
  - an identity mismatch stands until a later run whose own step zero
    confirmed the host.
- `--bundles DIR`: optional. A harvest root of harvested validation
  bundles, one per host, produced outside dgx-vitals
  (`<host>/cdw-validation-<host>-<timestamp>.tar.gz`, newest per host).
- `--hosts CSV`: the fleet list from Step 1. It sets the row order and
  fills the BMC IP / LAN IP columns. Hosts with no results get a **NO
  DATA** row, so the sheet doubles as the checklist of what is left.
- `--require-nccl`: hold DGX hosts to NCCL too. A run that used
  `--require-nccl` is held to it anyway: the requirement is read from
  its log.
- `--adjudications CSV`: one-off rulings (6.2).
- `--title "..."`: the sheet's title line. The sheet is customer-facing,
  so keep it generic.
- `--out FILE.xlsx`: the workbook (default `validation-tracker.xlsx`).
  The CSV is written beside it. Without openpyxl the build warns up
  front, writes the CSV only, and exits 1.
- `--bundle-out FILE.tar.gz`: also write a ferry bundle (the sheet plus
  every source artifact) with a `.sha256` sidecar.
- `--raw-cards`: embed the runs' own health cards even where the
  tracker's verdict differs. By default such a row gets a re-rendered
  card in `<out>_cards/`, footed ADJUDICATED or TRACKER VERDICT.
- `--from-fulllog`: rebuild every host from its FULLLOG, using
  summary.csv only as a second witness. This happens automatically when
  summary.csv is missing or stale.
- `--no-banner`: skip the end-of-build block-letter tally.

The build prints the row count, the rows with results, the cards
embedded, the adjudication counts, and a tally of every verdict. Exit
codes: 0 every row healthy and complete; 2 any row NOT HEALTHY,
INCOMPLETE, UNREACHABLE or without evidence; 1 when any input could not
be fully read (also anything quarantined or an adjudication that matched
nothing).

### 6.1 Reading the sheet

- **Source / Provenance**: which pipeline produced the row, the
  producer's version, and whether the row came from summary.csv or was
  rebuilt from the FULLLOG.
- **Step cells**:
  - `Complete`: passed on its evidence.
  - `Failed`: measured and failed.
  - `Warned`: failed, but ruled a known false positive. Counts as
    complete.
  - `Skipped`: the step did not run. The causes are: its tool is not
    installed (nccl-tests), nvsm refused to stress over uncleared
    alerts, or the operator waived it (`--no-require-nccl`).
  - `Incomplete`: it ran, but its output could not establish a result
    (for example an unreadable topology matrix).

  A required Skipped or Incomplete step makes the host INCOMPLETE.
  Note that **`SKIPPED` in the Overall column is a different thing**: it
  means the runner left the host alone because its folder already held
  a finished log.
- **% Complete**: passed checks over the profile's checks (six on DGX;
  Warned counts as passed). A skipped, incomplete or missing check stays
  in the denominator. NCCL has its own column and is never in the
  fraction, but it does count in Overall.
- **Overall**:
  - `HEALTHY`.
  - `HEALTHY WITH WARNINGS`: no failures, at least one Warned check.
  - `NOT HEALTHY`: something failed. A failed NCCL counts.
  - `INCOMPLETE`: evidence is missing. The Notes start with what is
    missing and what clears it. Fill: purple.
  - `UNREACHABLE`: step zero could not reach the host and nothing ran.
    The Notes give the logged reason. A later run that reaches the host
    replaces it. Fill: orange.
  - `NO DATA` or `SKIPPED`: no step results.
- **Notes**: the reasons first, then the evidence trail: release
  strings, failing-check names, and every adjudication that touched the
  row. Blank cells anywhere mean "not in the evidence", never a guess.

### 6.2 Known issues and adjudications: ruled, not deleted

A known false positive is never silently erased and never left sitting
red. It is ruled, with the ruling and its evidence written into the
sheet's **Known Issues & Adjudications** block below the last data row.
The block holds:

- **KI-1: NVSM `Verify DIMM part number` false positive.** If a host's
  *only* unhealthy NVSM check is `Verify DIMM part number`, the health
  step exited 0, and the log's own unhealthy count agrees, NVSM Health
  becomes **Warned**. Every fact in the ruling text (NVSM version, the
  part numbers NVSM reports) is drawn from those hosts' own logs. Any
  other unhealthy check keeps the step Failed.
- **KI-2: legacy logs only.** Runners before v2.0 recorded an NCCL step
  that never launched (mpirun could not find the nccl-tests binaries)
  as Failed. In those logs only, the step is relabelled **Skipped (not
  tested)**. Runner v2.0 and later classify this case themselves, so
  KI-2 never applies to their logs.
- **NCCL not tested.** This lists every host without an NCCL fabric
  result, grouped by cause: waived by the operator's opt-out, never
  launched (nccl-tests not installed), or could not be judged. Where the
  profile or the run required NCCL, those hosts are INCOMPLETE. DGX
  hosts whose run did not attempt NCCL are not listed, because NVSM
  covers the fabric there.

One-off rulings go in a CSV passed with `--adjudications`:

```csv
hostname,step,state,note
dgx-07,NVSM Stress Test,Complete,Original run collided with a stress already running service-side; clean re-run passed — ruling recorded by the operator
```

- `step` is one of the six tracker step names or `NCCL Tests`;
  `state` is `Complete`, `Failed`, `Skipped`, or `Warned`.
- A row that matches no host or no valid step is reported in the
  footer as NOT APPLIED, and the build exits 1.

## Common snags

- **Every host UNREACHABLE within seconds.** That is transport, not the
  hosts: SSH connects time out at 15 seconds, so a dead site network, a
  down router, expired SSH masters (3.1) or hosts powered off read as an
  instant all-host UNREACHABLE. No check ran. Check reachability (`nc -z
  <address> 22`, your tunnel), fix the path and re-launch. UNREACHABLE
  hosts are always redone.
- **INCOMPLETE: "identity mismatch".** The address in your CSV answered
  with a different hostname (or a different serial than your `serial`
  column). Nothing was validated under the wrong name. Fix the CSV row
  and re-run that host. The sheet keeps the mismatch until a later run
  confirms the identity.
- **INCOMPLETE: "identity not confirmed … serial not readable".** The
  account's sudo did not work for the serial read. Use
  `--sudo-password`, or fix sudo on the host.
- **"stress test is already running".** NVSM runs stress tests
  service-side: closing your terminal, a timeout or a killed client does
  not stop one. A second request is cancelled by NVSM, and the step is
  scored **Failed** with a note that it did not run. Wait for the
  running test to finish (30–60 minutes), then re-run just the stress
  step (3.3).
- **Stress refuses: "system alerts that have not been cleared".** NVSM
  will not stress a host with latched alerts, including the alert the
  KI-1 DIMM flag raises. The refusal is **Skipped** and the host is
  INCOMPLETE. `--check` lists the alerts beforehand. Once they are
  adjudicated, re-run with `--force-stress` (3.3).
- **`nvsm dump health` seems to hang at high percentages.** The tail of a
  dump is a full system report collection and is slow on large systems.
  That is why the default timeout is 3600s. Give it time, and check the
  host before launching a second dump.
- **Never pattern-kill `nvsm` processes on a host.** The string matches
  the host's own management daemons, which must keep running. If you
  must clear an orphaned client, find the exact PIDs of that command
  tree with `ps` and kill those.
- **NCCL Skipped.** The step names the missing binaries (`not
  installed: all_reduce_perf, …`). Where NCCL is required (HGX, or
  `--require-nccl`) the host is INCOMPLETE until the binaries are
  installed and the step is re-run. Where it is not required (DGX
  default) the skip is neutral. If some tests ran and others were
  missing, the step is **Failed**, because a partly installed suite is
  not a passing one.
- **Steps that need sudo fail instantly.** The account's sudo needs a
  password and you launched without `--sudo-password`, or the password
  was wrong. A wrong password stops the run before any host is
  validated ("sudo canary failed"). Relaunch with the flag; it prompts
  once.
- **A host shows SKIPPED in summary.csv.** It already had a finished
  FULLLOG in that output root, so the run left it alone. Use a fresh
  `--out` root for a genuinely new run, or `--force` to redo it.
- **`hosts file: … no column named exactly 'hostname'`.** The header row
  of your CSV calls the column something else. Rename it `hostname`.
