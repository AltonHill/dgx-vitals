# Quickstart: validating a wave on site

This is one page for the teammate running a validation wave. The full
runbook is [`docs/OPERATORS-GUIDE.md`](docs/OPERATORS-GUIDE.md).

**You need:** Python 3.10+, `dgx-vitals.py` and `tracker_build.py` in
one folder (they must stay together), SSH to the hosts as a user whose
sudo works, and `pip install openpyxl pillow` for the sheet. On a
password site, open SSH masters first (Guide 3.1).

## 1. Make the wave's hosts file

```csv
hostname,address,user
dgx-01,192.0.2.51,ubuntu
dgx-02,192.0.2.52,ubuntu
```

Use the tracker's names in `hostname`. Each host is asked its own name
and serial before anything runs. Use the OS (LAN) address, not the BMC.
A `hostname,lan_ip,bmc_ip` fleet list also works.

## 2. Preflight (read-only)

```bash
python3 dgx-vitals.py --hosts wave-a.csv --sudo-password --check
```

Every host should say **READY**. Fix any NOT READY line first:
unreachable, sudo, a hostname/serial mismatch, missing nvsm, standing
alerts (stress will refuse to run), missing nccl-tests binaries where
NCCL is required, or low disk. On HGX add `--profile hgx`.

## 3. Run

```bash
python3 dgx-vitals.py --hosts wave-a.csv --sudo-password --jobs 8 \
    --out results-wave-a
```

A DGX host runs six checks (stress takes 30–60 min). Watch from a second
terminal:

```bash
python3 dgx-vitals.py --watch --hosts wave-a.csv --out results-wave-a
```

**Need to stop?** Press Ctrl-C once. In-flight steps are recorded as
*interrupted*, not failed. To resume, run the same command again:
finished hosts are skipped and unfinished ones are redone. Don't add
`--force` unless you mean to redo finished hosts.

## 4. Read the verdicts

The last lines list every host that is not HEALTHY. Each host's reason
is in its FULLLOG trailer lines and the summary Notes.

| Verdict | What it asks of you |
|---|---|
| **HEALTHY** | Nothing. File it. |
| **NOT HEALTHY** | Something measured failed. Open the FULLLOG and read the failing check's trailer. Escalate per the engagement. |
| **INCOMPLETE** | Evidence is missing, not a fault. Fix the stated cause and re-run that host (small CSV, fresh `--out`, `--only <step>` if just one check). Common causes: stress refused over uncleared alerts (adjudicate them, then `--only stress --force-stress`); nccl-tests not installed where NCCL is required; "identity mismatch" (the address answered as another host: fix the CSV); serial unreadable (sudo). |
| **UNREACHABLE** | Nothing ran, and this says nothing about health. Fix SSH or the network (or re-open the masters) and run the host again. |

The health card in each host folder is named for its verdict
(`dgx-01-NOT-HEALTHY.png`).

## 5. Build the sheet

```bash
python3 tracker_build.py --vitals results-wave-a results-rerun \
    --hosts fleet.csv --title "<Engagement> — Validation Tracker" \
    --out tracker.xlsx
```

List roots **oldest first**. A re-run replaces only the checks it ran.
A real result replaces an earlier Skipped/Incomplete/UNREACHABLE one. An
identity mismatch only clears when a later run confirms the host. Hosts
with no results show **NO DATA**. Exit 0 means every row is healthy and
complete, 2 means rows need attention, and 1 means some input could not
be read (check the Quarantine block). Read the **Known Issues** block at
the bottom of the sheet: ruled false positives (KI-1, DIMM part number)
and the **NCCL not tested** list are there.

## NCCL: waived or untested

- **HGX requires NCCL.** If nccl-tests is not installed, the host is
  INCOMPLETE. Install the binaries and re-run `--only nccl`.
- **Waiving it** (`--no-require-nccl`, HGX only) needs the customer's
  agreement. The waiver is printed on the verdict and named on the
  sheet, and the host still reads **INCOMPLETE**: a waived test is
  missing evidence, never a pass. Don't use the waiver to turn a sheet
  green. Tell the customer which hosts have no NCCL result (the NCCL not
  tested line).
- **On DGX**, NCCL runs only with `--require-nccl` (when the contract
  requires it). Then the same rules apply. Without the flag, NVSM covers
  the fabric and no NCCL is fine.
- A NCCL test that **ran and failed** is NOT HEALTHY, waiver or not.
