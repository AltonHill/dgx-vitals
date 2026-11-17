"""Brief C amendment 1 — Loki's rulings.

Ruling 1: a REQUIRED NCCL step that never launched (the nccl_evidence()
non-launch signature: no result lines, every binary missing) is missing
evidence, not evidence of a fault. The step reads Skipped with its
`not installed: ...` note and still counts, so the host is INCOMPLETE —
the fail-closed word for missing evidence — and exits 2, the exit code
every INCOMPLETE host has (the brief's parenthetical "(1)" is the
tool-error code in both tools; see REPORT-C-AMEND1). Guards that must
not soften: NCCL that ran and failed is NOT HEALTHY; a stress/health
non-launch is unchanged; the opt-out is unchanged (amendment 2 later
made the opt-out host INCOMPLETE — see test_rc11_amend2); a non-required
non-launch is unchanged.

Ruling 2: runner VERSION 2.0 on every surface (FULLLOG header,
--version, --help epilog); the tracker stays 1.5."""

import os
import unittest

from tests import support as S
from tests.test_rc4_runs import fulllog, overall_line

HOSTS_HGX = "hostname,address\nhgx01,a\n"
HOSTS_DGX = "hostname,address\ngpu01,a\n"


def ran_and_failed():
    return S.nccl_ok().replace("Out of bounds values : 0",
                               "Out of bounds values : 5", 1)


class RequiredNcclNonLaunch(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.none = S.corpus_block(*S.W09, "sh -c")    # real w09 non-launch

    # (a) required + non-launch -> INCOMPLETE
    def test_a_required_non_launch_is_incomplete_hgx(self):
        r = S.ScriptedRunner(S.hgx_host_script(nccl=self.none))
        rc, out, err, root = S.drive_main(
            self, self.vit, ["--profile", "hgx"], {"a": r}, HOSTS_HGX)
        self.assertEqual(rc, 2, out + err)      # INCOMPLETE's exit code
        log = fulllog(root, "hgx01")
        self.assertEqual(overall_line(log),
                         "OVERALL: INCOMPLETE (3/4 steps passed)")
        self.assertNotIn("NOT HEALTHY", log)
        self.assertIn("· SKIP · not installed: all_reduce_perf, "
                      "all_gather_perf, reduce_scatter_perf", log)
        row = S.read_summary(root)["hgx01"]
        self.assertEqual(row["Overall"], "INCOMPLETE")
        self.assertEqual(row["NCCL Tests"], "Skipped")
        self.assertIn("not installed: all_reduce_perf", row["Notes"])

    def test_a_required_non_launch_is_incomplete_dgx_require_nccl(self):
        r = S.ScriptedRunner(S.dgx_host_script(
            extra=[("_perf", 0, self.none, 3.0)]))
        rc, out, err, root = S.drive_main(
            self, self.vit, ["--require-nccl"], {"a": r}, HOSTS_DGX)
        self.assertEqual(rc, 2, out + err)
        self.assertEqual(overall_line(fulllog(root, "gpu01")),
                         "OVERALL: INCOMPLETE (6/7 steps passed)")

    def test_a_mpirun_absent_is_the_same_non_launch(self):
        # with mpirun missing every section exits 127: same signature
        none = "\n".join(f"===== {t}_perf =====\nsh: 1: mpirun: not found\n"
                         f"[{t}_perf exit 127]"
                         for t in ("all_reduce", "all_gather",
                                   "reduce_scatter"))
        r = S.ScriptedRunner(S.hgx_host_script(nccl=none))
        rc, _o, _e, root = S.drive_main(
            self, self.vit, ["--profile", "hgx"], {"a": r}, HOSTS_HGX)
        self.assertEqual(rc, 2)
        self.assertTrue(overall_line(fulllog(root, "hgx01")).startswith(
            "OVERALL: INCOMPLETE"))

    def test_a_floor_is_solely_nccl_another_fail_still_not_healthy(self):
        # the softening applies only when the non-launch is the sole cause
        r = S.ScriptedRunner(S.hgx_host_script(nccl=self.none, gpus=4))
        rc, _o, _e, root = S.drive_main(
            self, self.vit, ["--profile", "hgx"], {"a": r}, HOSTS_HGX)
        self.assertEqual(rc, 2)
        self.assertTrue(overall_line(fulllog(root, "hgx01")).startswith(
            "OVERALL: NOT HEALTHY"))

    # (b) required + ran-and-failed -> NOT HEALTHY (guard: not softened)
    def test_b_required_ran_and_failed_is_not_healthy(self):
        r = S.ScriptedRunner(S.hgx_host_script(nccl=ran_and_failed()))
        rc, _o, _e, root = S.drive_main(
            self, self.vit, ["--profile", "hgx"], {"a": r}, HOSTS_HGX)
        self.assertEqual(rc, 2)
        log = fulllog(root, "hgx01")
        self.assertTrue(overall_line(log).startswith("OVERALL: NOT HEALTHY"))
        self.assertIn("· FAIL · all_reduce: 5 out-of-bounds values", log)
        self.assertEqual(S.read_summary(root)["hgx01"]["NCCL Tests"],
                         "Failed")

    def test_b_partial_launch_with_a_missing_binary_is_not_healthy(self):
        # one test ran; not the non-launch signature -> FAIL as before
        out = (S.nccl_ok(("all_reduce",)) + "\n===== all_gather_perf =====\n"
               "[all_gather_perf exit 127]")
        r = S.ScriptedRunner(S.hgx_host_script(nccl=out))
        rc, _o, _e, root = S.drive_main(
            self, self.vit, ["--profile", "hgx"], {"a": r}, HOSTS_HGX)
        self.assertEqual(rc, 2)
        self.assertTrue(overall_line(fulllog(root, "hgx01")).startswith(
            "OVERALL: NOT HEALTHY"))

    # (c) opt-out -> note unchanged; verdict INCOMPLETE.
    # Converted by Brief C amendment 2 (Package A, Loki 2026-11-16: "a
    # waived test is absent evidence, and absent evidence is INCOMPLETE at
    # every layer"). Was: OVERALL HEALTHY (3/3), exit 0. The waiver is
    # still on the record on the OVERALL line, in Notes and in the header.
    def test_c_opt_out_unchanged(self):
        r = S.ScriptedRunner(S.hgx_host_script(nccl=self.none))
        rc, out, err, root = S.drive_main(
            self, self.vit, ["--profile", "hgx", "--no-require-nccl"],
            {"a": r}, HOSTS_HGX)
        self.assertEqual(rc, 2, out + err)      # INCOMPLETE's exit code
        log = fulllog(root, "hgx01")
        self.assertEqual(overall_line(log),
                         "OVERALL: INCOMPLETE (3/4 steps passed) — NCCL not "
                         "required (operator opt-out)")
        self.assertIn("· NCCL not required (operator opt-out)\n", log)
        self.assertNotIn("HEALTHY", overall_line(log))
        row = S.read_summary(root)["hgx01"]
        self.assertEqual(row["Overall"], "INCOMPLETE")
        self.assertIn("NCCL not required (operator opt-out)", row["Notes"])
        self.assertIn("step skipped (not required)", row["Notes"])
        self.assertEqual(row["NCCL Tests"], "Skipped")

    def test_c_opt_out_still_never_hides_ran_and_failed(self):
        r = S.ScriptedRunner(S.hgx_host_script(nccl=ran_and_failed()))
        rc, _o, _e, root = S.drive_main(
            self, self.vit, ["--profile", "hgx", "--no-require-nccl"],
            {"a": r}, HOSTS_HGX)
        self.assertEqual(rc, 2)
        self.assertTrue(overall_line(fulllog(root, "hgx01")).startswith(
            "OVERALL: NOT HEALTHY"))

    def test_not_required_non_launch_unchanged(self):
        # dgx, NCCL joined by --only nccl without --require-nccl: neutral
        step = [s for s in self.vit.build_steps("hgx", 60)
                if s.slug == "nccl"][0]
        st, note = self.vit.verdict(step, 0, self.none, 8, 0.0, False,
                                    dur=1.0)
        self.assertEqual(st, "SKIP")
        self.assertIn("step skipped (not required)", note)
        self.assertNotIn("INCOMPLETE", note)

    # stress / health non-launch: unchanged (NCCL-specific amendment)
    def test_health_non_launch_unchanged(self):
        r = S.ScriptedRunner(S.dgx_host_script(extra=[
            ("nvsm show health", 127, "sudo: nvsm: command not found\n",
             0.1)]))
        rc, _o, _e, root = S.drive_main(self, self.vit, [], {"a": r},
                                        HOSTS_DGX)
        self.assertEqual(rc, 2)
        log = fulllog(root, "gpu01")
        self.assertIn("· FAIL · exit code 127", log)
        self.assertTrue(overall_line(log).startswith("OVERALL: NOT HEALTHY"))

    def test_stress_non_launch_unchanged(self):
        r = S.ScriptedRunner(S.dgx_host_script(extra=[
            ("nvsm stress-test", 127, "sudo: nvsm: command not found\n",
             0.1)]))
        rc, _o, _e, root = S.drive_main(self, self.vit, [], {"a": r},
                                        HOSTS_DGX)
        self.assertEqual(rc, 2)
        self.assertTrue(overall_line(fulllog(root, "gpu01")).startswith(
            "OVERALL: NOT HEALTHY"))

    def test_tracker_never_reads_it_healthy(self):
        # tracker untouched: it must still not read the new row HEALTHY
        r = S.ScriptedRunner(S.hgx_host_script(nccl=self.none))
        _rc, _o, _e, root = S.drive_main(
            self, self.vit, ["--profile", "hgx"], {"a": r}, HOSTS_HGX)
        S.install_fake_openpyxl()
        tb = S._load("tracker_build.py")
        rows = tb.read_vitals(root)
        tb.finalize(rows["hgx01"])
        self.assertEqual(rows["hgx01"].overall, "INCOMPLETE")


class Version20(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()

    # (d) every runner version surface reads 2.0
    def test_d_version_constant(self):
        self.assertEqual(self.vit.VERSION, "2.0")

    def test_d_version_flag(self):
        rc, out, _err = S.run_vitals_main(self.vit, ["--version"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "dgx-vitals.py v2.0")

    def test_d_help_epilog(self):
        _rc, out, _err = S.run_vitals_main(self.vit, ["--help"])
        self.assertIn("dgx-vitals.py v2.0", out)
        self.assertNotIn("1.9", out)

    def test_d_fulllog_header(self):
        r = S.ScriptedRunner(S.dgx_host_script())
        _rc, _o, _e, root = S.drive_main(self, self.vit, [], {"a": r},
                                         HOSTS_DGX)
        self.assertIn("Tool: dgx-vitals.py v2.0", fulllog(root, "gpu01"))

    def test_d_tracker_version(self):
        # Converted by Brief D ("Version"): the tracker now speaks the 2.0
        # verdict contract and is VERSION "2.0" (it was pinned at "1.5"
        # while Brief C left the tracker untouched).
        S.install_fake_openpyxl()
        self.assertEqual(S._load("tracker_build.py").VERSION, "2.0")


if __name__ == "__main__":
    unittest.main()
