"""Brief C items 4-7, driven through main() with scripted hosts.

4 (Q7)  hgx implies NCCL required; --no-require-nccl is the loud opt-out,
        printed on the FULLLOG OVERALL line and in the summary Notes
        (amendment 2: the opt-out host is INCOMPLETE, never HEALTHY).
5 (Q11) expected GPU count / NVLink class are data: a 4-GPU host passes
        with 4 via the hosts CSV; the FULLLOG header records the count.
6 (H6/M1) a failed probe is UNREACHABLE: its own runner state, a summary
        row saying so, OVERALL UNREACHABLE, no step results, never hgx.
7 (H5)  step-zero identity: a hostname/serial mismatch or an unreadable
        serial stops the host before any test step."""

import os
import re
import unittest

from tests import support as S

SERIAL_READ = (lambda c: c == "sudo cat /sys/class/dmi/id/product_serial")
TEST_STEP_CMDS = ("cat /etc/", "nvsm", "nvidia-smi", "_perf")


def step_calls(runner):
    return [c for c in runner.calls
            if any(k in c["cmd"] for k in TEST_STEP_CMDS)
            and "DGXV-ID-BEGIN" not in c["cmd"]
            and "product_serial" not in c["cmd"]
            and c["argv"][0] != "base64"]


def fulllog(root, host):
    return S.read_text(os.path.join(root, host, f"{host}-FULLLOG.txt"))


def overall_line(text):
    return next(ln for ln in text.splitlines() if ln.startswith("OVERALL:"))


class FullRuns(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()

    def test_clean_dgx_host_is_healthy_on_evidence(self):
        r = S.ScriptedRunner(S.dgx_host_script())
        rc, out, err, root = S.drive_main(
            self, self.vit, [], {"a": r}, "hostname,address\ngpu01,a\n")
        self.assertEqual(rc, 0, out + err)
        log = fulllog(root, "gpu01")
        self.assertEqual(overall_line(log),
                         "OVERALL: HEALTHY (6/6 steps passed)")
        row = S.read_summary(root)["gpu01"]
        self.assertEqual(row["Overall"], "HEALTHY")
        self.assertEqual(row["NVSM Stress Test"], "Complete")
        self.assertIn("Expected: 8 GPUs (profile default: DGX B300) · NVLink "
                      "NV18 (profile default: DGX B300)", log)
        # every test step ran tracked (timeout cleanup possible)
        for c in step_calls(r):
            self.assertTrue(c["track"], c["cmd"])
        self.assertNotIn("DGXV-PGID", log)

    def test_tracker_reads_a_new_runner_root_as_healthy(self):
        r = S.ScriptedRunner(S.dgx_host_script())
        _rc, _o, _e, root = S.drive_main(
            self, self.vit, [], {"a": r}, "hostname,address\ngpu01,a\n")
        S.install_fake_openpyxl()
        tb = S._load("tracker_build.py")
        rows = tb.read_vitals(root)
        tb.finalize(rows["gpu01"])
        self.assertEqual(rows["gpu01"].overall, "HEALTHY")

    def test_refused_stress_is_incomplete_not_healthy(self):
        r = S.ScriptedRunner(S.dgx_host_script(extra=[
            ("nvsm stress-test", 0, "Stress testing will not be done "
                                    "because alerts have not been cleared.\n",
             2.0)]))
        rc, _o, _e, root = S.drive_main(
            self, self.vit, [], {"a": r}, "hostname,address\ngpu01,a\n")
        self.assertEqual(rc, 2)
        self.assertEqual(overall_line(fulllog(root, "gpu01")),
                         "OVERALL: INCOMPLETE (5/6 steps passed)")

    def test_unparseable_topology_makes_the_host_incomplete(self):
        r = S.ScriptedRunner(S.dgx_host_script(topo="Legend: NV# = ...\n"))
        rc, _o, _e, root = S.drive_main(
            self, self.vit, [], {"a": r}, "hostname,address\ngpu01,a\n")
        self.assertEqual(rc, 2)
        log = fulllog(root, "gpu01")
        self.assertIn("· INCOMPLETE · GPU topology matrix not found", log)
        self.assertTrue(overall_line(log).startswith("OVERALL: INCOMPLETE"))
        self.assertEqual(S.read_summary(root)["gpu01"]["NVLink Topology"],
                         "Incomplete")


class HgxRequiresNccl(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.none = S.corpus_block(*S.W09, "sh -c")    # no binary installed

    def test_hgx_without_nccl_tests_is_incomplete_by_default(self):
        # Was test_hgx_without_nccl_tests_is_not_healthy_by_default
        # (OVERALL NOT HEALTHY, NCCL cell Failed). Loki's ruling (Brief C
        # amendment 1) changed it: a required NCCL suite that never
        # launched is missing evidence -> INCOMPLETE (exit 2, as every
        # INCOMPLETE), NCCL cell Skipped. Still never HEALTHY.
        r = S.ScriptedRunner(S.hgx_host_script(nccl=self.none))
        rc, _o, _e, root = S.drive_main(
            self, self.vit, ["--profile", "hgx"], {"a": r},
            "hostname,address\nhgx01,a\n")
        self.assertEqual(rc, 2)
        log = fulllog(root, "hgx01")
        self.assertEqual(overall_line(log),
                         "OVERALL: INCOMPLETE (3/4 steps passed)")
        self.assertIn("NCCL required (hgx profile default)", log)
        self.assertIn("not installed: all_reduce_perf, all_gather_perf, "
                      "reduce_scatter_perf", log)
        self.assertEqual(S.read_summary(root)["hgx01"]["NCCL Tests"],
                         "Skipped")

    def test_auto_detected_hgx_also_requires_nccl(self):
        r = S.ScriptedRunner(S.hgx_host_script(nccl=self.none))
        rc, _o, _e, root = S.drive_main(
            self, self.vit, ["--profile", "auto"], {"a": r},
            "hostname,address\nhgx01,a\n")
        self.assertEqual(rc, 2)
        log = fulllog(root, "hgx01")
        self.assertIn("Platform: hgx (detected by step-zero probe)", log)
        # amendment 1: INCOMPLETE (was NOT HEALTHY before the ruling)
        self.assertTrue(overall_line(log).startswith("OVERALL: INCOMPLETE"))

    def test_opt_out_is_printed_in_the_verdict(self):
        # amendment 2 (Package A, Loki 2026-11-16): a waived test is
        # absent evidence, so the opt-out host is INCOMPLETE (exit 2), not
        # HEALTHY (3/3, exit 0) as before; the waiver wording is unchanged
        r = S.ScriptedRunner(S.hgx_host_script(nccl=self.none))
        rc, out, err, root = S.drive_main(
            self, self.vit, ["--profile", "hgx", "--no-require-nccl"],
            {"a": r}, "hostname,address\nhgx01,a\n")
        self.assertEqual(rc, 2, out + err)
        self.assertEqual(overall_line(fulllog(root, "hgx01")),
                         "OVERALL: INCOMPLETE (3/4 steps passed) — NCCL not "
                         "required (operator opt-out)")
        row = S.read_summary(root)["hgx01"]
        self.assertIn("NCCL not required (operator opt-out)", row["Notes"])
        self.assertEqual(row["NCCL Tests"], "Skipped")

    def test_opt_out_never_hides_a_test_that_ran_and_failed(self):
        bad = S.nccl_ok().replace("Out of bounds values : 0",
                                  "Out of bounds values : 5", 1)
        r = S.ScriptedRunner(S.hgx_host_script(nccl=bad))
        rc, _o, _e, root = S.drive_main(
            self, self.vit, ["--profile", "hgx", "--no-require-nccl"],
            {"a": r}, "hostname,address\nhgx01,a\n")
        self.assertEqual(rc, 2)
        self.assertTrue(overall_line(fulllog(root, "hgx01")).startswith(
            "OVERALL: NOT HEALTHY"))

    def test_require_and_opt_out_are_exclusive(self):
        rc, _o, _e, _r = S.drive_main(
            self, self.vit, ["--host", "x", "--require-nccl",
                             "--no-require-nccl"], {})
        self.assertEqual(rc, 1)


class GpuCountIsData(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()

    def test_four_gpu_host_passes_with_four(self):
        r = S.ScriptedRunner(S.hgx_host_script(gpus=4))
        rc, out, err, root = S.drive_main(
            self, self.vit, ["--profile", "hgx"], {"a": r},
            "hostname,address,gpus\nhgx01,a,4\n")
        self.assertEqual(rc, 0, out + err)
        log = fulllog(root, "hgx01")
        self.assertIn("Expected: 4 GPUs (hosts data)", log)
        self.assertIn("4 GPUs, all 6 GPU pairs NV18", log)
        self.assertIn("-np 4", r.ran("_perf")[0]["cmd"])

    def test_same_host_without_the_override_fails(self):
        r = S.ScriptedRunner(S.hgx_host_script(gpus=4))
        rc, _o, _e, root = S.drive_main(
            self, self.vit, ["--profile", "hgx"], {"a": r},
            "hostname,address\nhgx01,a\n")
        self.assertEqual(rc, 2)
        self.assertIn("Attached GPUs = 4, expected 8", fulllog(root, "hgx01"))

    def test_mixed_wave_each_host_its_own_count(self):
        r4 = S.ScriptedRunner(S.hgx_host_script("h4", gpus=4))
        r8 = S.ScriptedRunner(S.hgx_host_script("h8", gpus=8))
        rc, out, err, root = S.drive_main(
            self, self.vit, ["--profile", "hgx"], {"a": r4, "b": r8},
            "hostname,address,gpus\nh4,a,4\nh8,b,\n")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("Expected: 8 GPUs (profile default: HGX B300)",
                      fulllog(root, "h8"))

    def test_bad_gpus_value_is_a_tool_error(self):
        rc, _o, err, _r = S.drive_main(
            self, self.vit, [], {}, "hostname,address,gpus\nh4,a,four\n")
        self.assertEqual(rc, 1)
        self.assertIn("not a positive integer", err)


class Unreachable(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.dead = lambda: S.ScriptedRunner([
            ("DGXV-ID-BEGIN", 255,
             "\n[stderr]\nssh: connect to host a port 22: Connection timed out",
             15.0)], default=(255, "", 15.0))

    def test_probe_failure_is_unreachable_with_no_step_results(self):
        r = self.dead()
        rc, _o, _e, root = S.drive_main(
            self, self.vit, ["--profile", "auto"], {"a": r},
            "hostname,address\ngpu01,a\n")
        self.assertEqual(rc, 2)
        log = fulllog(root, "gpu01")
        self.assertEqual(overall_line(log), "OVERALL: UNREACHABLE (no step ran)")
        self.assertIn("Connection timed out", log)
        self.assertNotIn("hgx", log.split("Reason:")[0].lower())
        self.assertEqual(step_calls(r), [])
        row = S.read_summary(root)["gpu01"]
        self.assertEqual(row["Overall"], "UNREACHABLE")
        for col in ("Release", "NVSM Health", "NVIDIA-SMI Query",
                    "NVLink Topology", "NCCL Tests", "NVSM Stress Test",
                    "NVSM Dump Health"):
            self.assertEqual(row[col], "", col)
        self.assertIn("step-zero probe failed", row["Notes"])

    def test_unreachable_is_not_skipped_on_the_next_run(self):
        hosts = "hostname,address\ngpu01,a\n"
        _rc, _o, _e, root = S.drive_main(self, self.vit, [], {"a": self.dead()},
                                         hosts)
        r = S.ScriptedRunner(S.dgx_host_script())
        rc, out, err, _ = S.drive_main(self, self.vit, ["--out", root],
                                       {"a": r}, hosts)
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(step_calls(r))

    def test_tracker_never_reads_unreachable_as_healthy(self):
        _rc, _o, _e, root = S.drive_main(
            self, self.vit, [], {"a": self.dead()}, "hostname,address\ngpu01,a\n")
        S.install_fake_openpyxl()
        tb = S._load("tracker_build.py")
        rows = tb.read_vitals(root)
        for r in rows.values():
            tb.finalize(r)
            self.assertNotIn(r.overall, ("HEALTHY", "HEALTHY WITH WARNINGS"))


class Identity(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()

    def _one(self, rows, script, *extra):
        r = S.ScriptedRunner(script)
        rc, out, err, root = S.drive_main(self, self.vit, list(extra),
                                          {"a": r}, rows)
        return rc, r, root, out + err

    def test_hostname_mismatch_stops_the_host(self):
        rc, r, root, _ = self._one("hostname,address\ngpu07,a\n",
                                   S.dgx_host_script(hostname="gpu08"))
        self.assertEqual(rc, 2)
        self.assertEqual(step_calls(r), [])
        log = fulllog(root, "gpu07")
        self.assertIn("identity mismatch — hosts entry 'gpu07' but the host "
                      "reports hostname 'gpu08'", log)
        self.assertEqual(overall_line(log), "OVERALL: INCOMPLETE (no step ran)")

    def test_serial_mismatch_stops_the_host(self):
        rc, r, root, _ = self._one("hostname,address,serial\ngpu01,a,SN-9999\n",
                                   S.dgx_host_script(serial="SN-0001"))
        self.assertEqual(rc, 2)
        self.assertEqual(step_calls(r), [])
        self.assertIn("hosts entry serial 'SN-9999' but the host reports "
                      "serial 'SN-0001'", fulllog(root, "gpu01"))

    def test_unreadable_serial_is_unconfirmed(self):
        script = S.dgx_host_script(serial="", extra=[
            (SERIAL_READ, 1, "[stderr]\nsudo: a password is required",
             0.1)])
        rc, r, root, _ = self._one("hostname,address\ngpu01,a\n", script)
        self.assertEqual(rc, 2)
        self.assertEqual(step_calls(r), [])
        self.assertIn("identity not confirmed — chassis serial not readable",
                      fulllog(root, "gpu01"))

    def test_serial_read_via_sudo_and_matching_entry_proceeds(self):
        script = S.dgx_host_script(serial="", extra=[
            (SERIAL_READ, 0, "1954126000062\n", 0.1)])
        rc, r, root, out = self._one(
            "hostname,address,serial\ngpu01.site.example,a,1954126000062\n",
            script)
        self.assertEqual(rc, 0, out)
        log = fulllog(root, "gpu01.site.example")
        self.assertIn("serial=1954126000062 (/sys/class/dmi/id/product_serial "
                      "via sudo)", log)
        self.assertIn("Identity check: OK", log)

    def test_ip_named_entry_is_judged_by_serial(self):
        r = S.ScriptedRunner(S.dgx_host_script(hostname="dgx-a"))
        rc, out, err, _root = S.drive_main(self, self.vit, [],
                                           {"10.0.0.51": r}, "10.0.0.51\n")
        self.assertEqual(rc, 0, out + err)


if __name__ == "__main__":
    unittest.main()
