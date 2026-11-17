"""Brief C item 1 — `--check`: a read-only preflight.

Per host: SSH reachability, identity, sudo, nvsm, each nccl-tests binary
by name, standing alerts, free disk, clock skew (reported; >120 s flagged
loudly; never a gate unless absurd). Writes nothing, runs only read-only
commands, exits 0 only when every host is fully ready, 2 when any is
not, 1 on tool error. Hosts are scripted runners; nothing is contacted."""

import os
import time
import unittest

from tests import support as S

READ_ONLY_HEADS = {"sh", "true", "cat", "nvsm"}


def check_out(hostname="gpu01", platform="dgx", serial="SN-0001",
              bins=("all_reduce_perf", "all_gather_perf",
                    "reduce_scatter_perf"),
              nvsm="/usr/bin/nvsm", mpirun="/usr/bin/mpirun",
              skew=0, tmp_kb=500 * 1024 * 1024):
    lines = ["DGXV-CHECK-BEGIN", f"HOSTNAME={hostname}",
             f"PLATFORM={platform}", "PRODUCT=DGXB300", "VENDOR=NVIDIA",
             f"SERIAL={serial}",
             f"EPOCH={int(time.time() + skew)}", f"NVSM={nvsm}",
             f"MPIRUN={mpirun}"]
    for b in ("all_reduce_perf", "all_gather_perf", "reduce_scatter_perf"):
        lines.append(f"BIN:{b}=" + (f"/opt/nccl-tests/{b}" if b in bins
                                    else ""))
    lines += [f"TMPFREE_KB={tmp_kb}", "DGXV-CHECK-END"]
    return "\n".join(lines) + "\n"


def host(out=None, alerts=(0, ""), sudo=(0, ""), probe_rc=0):
    return S.ScriptedRunner([
        ("DGXV-CHECK-BEGIN", probe_rc, out if out is not None else check_out(),
         0.3),
        ("nvsm show alerts", alerts[0], alerts[1], 1.0),
        ("true", sudo[0], sudo[1], 0.1),
    ])


class Check(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()

    def run_check(self, runners, rows, *extra):
        return S.drive_main(self, self.vit, ["--check"] + list(extra),
                            runners, hosts_rows=rows)

    def test_ready_host_exits_0_and_writes_nothing(self):
        r = host()
        rc, out, err, root = self.run_check(
            {"10.0.0.1": r}, "hostname,address\ngpu01,10.0.0.1\n",
            "--require-nccl")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("gpu01  (ssh -> 10.0.0.1)  READY", out)
        self.assertIn("check: 1 of 1 host(s) ready", out)
        self.assertFalse(os.path.exists(root))          # nothing written
        for c in r.calls:                               # nothing changed
            self.assertIn(c["argv"][0], READ_ONLY_HEADS, c["cmd"])
            self.assertFalse(c["track"])
        self.assertEqual(r.ran("stress-test") + r.ran("dump") +
                         r.ran("_perf -b"), [])

    def test_missing_binaries_are_named_individually(self):
        r = S.ScriptedRunner([
            ("DGXV-CHECK-BEGIN", 0, check_out(platform="hgx",
                                              bins=("all_gather_perf",)), 0.2),
            ("true", 0, "", 0.1)])
        rc, out, _e, _root = self.run_check(
            {"10.0.0.2": r}, "hostname,address\ngpu01,10.0.0.2\n",
            "--profile", "hgx")
        self.assertEqual(rc, 2)
        self.assertIn("all_reduce_perf: MISSING", out)
        self.assertIn("all_gather_perf: /opt/nccl-tests/all_gather_perf", out)
        self.assertIn("reduce_scatter_perf: MISSING", out)
        self.assertIn("not installed: all_reduce_perf, reduce_scatter_perf "
                      "(required: hgx profile default)", out)
        self.assertIn("NOT READY: gpu01 (nccl-tests)", out)

    def test_not_ready_reasons_alerts_nvsm_identity_sudo(self):
        runners = {
            "a": host(alerts=(0, "/systems/localhost/alerts/alert0\n"
                                 "Properties:\n    AlertId = NV-DIMM-0001\n")),
            "b": host(out=check_out(hostname="gpu02", nvsm="")),
            "c": host(out=check_out(hostname="someone-else")),
            "d": host(out=check_out(hostname="gpu04"),
                      sudo=(1, "[stderr]\nsudo: a password is required")),
        }
        rows = ("hostname,address\ngpu01,a\ngpu02,b\ngpu03,c\ngpu04,d\n")
        rc, out, _e, _r = self.run_check(runners, rows)
        self.assertEqual(rc, 2)
        self.assertIn("1 standing alert(s): NV-DIMM-0001", out)
        self.assertIn("nvsm not found on PATH", out)
        self.assertIn("hosts entry 'gpu03' but the host reports hostname "
                      "'someone-else'", out)
        self.assertIn("sudo: a password is required", out)
        self.assertIn("check: 0 of 4 host(s) ready", out)

    def test_unrecognized_alert_output_is_not_ready(self):
        r = host(alerts=(0, "something nvsm printed\n"))
        rc, out, _e, _r = self.run_check({"x": r},
                                         "hostname,address\ngpu01,x\n")
        self.assertEqual(rc, 2)
        self.assertIn("cannot confirm no standing alerts", out)

    def test_skew_is_reported_and_flagged_loudly_but_not_a_gate(self):
        r = host(out=check_out(skew=300))
        rc, out, _e, _r = self.run_check({"x": r},
                                         "hostname,address\ngpu01,x\n")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"WARN  clock\s+skew \+(29[89]|30[01]) s")  # date +%s is whole seconds
        self.assertIn("CLOCK SKEW > 120 s", out)

    def test_small_skew_is_ok(self):
        rc, out, _e, _r = self.run_check({"x": host()},
                                         "hostname,address\ngpu01,x\n")
        self.assertRegex(out, r"ok    clock\s+skew [+-][01] s")

    def test_absurd_skew_is_not_ready(self):
        r = host(out=check_out(skew=3 * 86400))
        rc, out, _e, _r = self.run_check({"x": r},
                                         "hostname,address\ngpu01,x\n")
        self.assertEqual(rc, 2)
        self.assertIn("ABSURD CLOCK SKEW", out)

    def test_unreachable_host(self):
        r = host(out="ssh: connect to host x port 22: No route to host\n",
                 probe_rc=255)
        rc, out, _e, _r = self.run_check({"x": r},
                                         "hostname,address\ngpu01,x\n")
        self.assertEqual(rc, 2)
        self.assertIn("gpu01  (ssh -> x)  UNREACHABLE", out)
        self.assertIn("No route to host", out)

    def test_low_host_tmp_disk_is_not_ready_on_dgx(self):
        r = host(out=check_out(tmp_kb=512 * 1024))
        rc, out, _e, _r = self.run_check({"x": r},
                                         "hostname,address\ngpu01,x\n")
        self.assertEqual(rc, 2)
        self.assertIn("host /tmp: 0.5 GiB free", out)

    def test_rejected_password_is_not_retried_fleet_wide(self):
        self.vit.getpass.getpass = lambda prompt="": "wrong"
        self.addCleanup(setattr, self.vit.getpass, "getpass", S.getpass_real)
        bad = (1, "[stderr]\nSorry, try again.\nsudo: 1 incorrect password")
        a, b = host(sudo=bad), host(out=check_out(hostname="gpu02"), sudo=bad)
        rc, out, _e, _r = self.run_check(
            {"a": a, "b": b}, "hostname,address\ngpu01,a\ngpu02,b\n",
            "--sudo-password")
        self.assertEqual(rc, 2)
        self.assertEqual(len([c for c in a.calls if c["sudo"]]), 1)
        self.assertEqual([c for c in b.calls if c["sudo"]], [])
        self.assertIn("rejected on gpu01 (canary)", out)

    def test_tool_errors_exit_1(self):
        rc, _o, err, _r = self.run_check({}, "hostname,address\n")
        self.assertEqual(rc, 1, err)
        rc, _o, _e, _r = S.drive_main(self, self.vit,
                                      ["--check", "--dry-run", "--host", "x"],
                                      {})
        self.assertEqual(rc, 1)
        rc, _o, _e, _r = S.drive_main(self, self.vit, ["--check", "--bogus"],
                                      {})
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
