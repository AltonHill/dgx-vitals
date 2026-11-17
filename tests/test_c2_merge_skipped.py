"""C2 (merge half) — in merge_result, Skipped never overwrites Complete or
Failed; a real state may overwrite Skipped. Same rule for the NCCL cell."""

import os
import unittest

from tests import support as S


class MergeThroughTracker(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)

    def _build(self, first, second, hosts=False):
        a = S.write_vitals_root(os.path.join(self.tmp, "wave-a"), [first])
        b = S.write_vitals_root(os.path.join(self.tmp, "wave-b"), [second])
        args = ["--vitals", a, "--vitals", b]
        if hosts:
            args = ["--hosts", S.write_fleet(
                os.path.join(self.tmp, "f.csv"), [first["Hostname"]])] + args
        return S.run_tracker(args, self.tmp)

    def test_refused_rerun_does_not_erase_stress_failure(self):
        wave_a = S.full_dgx("gpu01", "NOT HEALTHY", NVSM_Stress_Test="Failed")
        refused = S.vrow("gpu01", "HEALTHY", NVSM_Stress_Test="Skipped",
                         notes="NVSM Stress Test: nvsm refused — "
                               "uncleared alerts")
        for hosts in (False, True):
            sh = self._build(wave_a, refused, hosts=hosts)
            self.assertEqual(sh.get("gpu01", "NVSM Stress Test"), "Failed")
            self.assertEqual(sh.get("gpu01", "Overall"), "NOT HEALTHY")
            self.assertEqual(sh.get("gpu01", "% Complete"), 83)

    def test_skipped_then_real_complete(self):
        wave_a = S.full_dgx("gpu02", NVSM_Stress_Test="Skipped")
        forced = S.vrow("gpu02", NVSM_Stress_Test="Complete")
        sh = self._build(wave_a, forced)
        self.assertEqual(sh.get("gpu02", "NVSM Stress Test"), "Complete")
        self.assertEqual(sh.get("gpu02", "Overall"), "HEALTHY")
        self.assertEqual(sh.get("gpu02", "% Complete"), 100)

    def test_skipped_then_real_failed(self):
        wave_a = S.full_dgx("gpu03", NVSM_Stress_Test="Skipped")
        forced = S.vrow("gpu03", "NOT HEALTHY", NVSM_Stress_Test="Failed")
        sh = self._build(wave_a, forced)
        self.assertEqual(sh.get("gpu03", "NVSM Stress Test"), "Failed")
        self.assertEqual(sh.get("gpu03", "Overall"), "NOT HEALTHY")

    def test_skipped_does_not_overwrite_complete(self):
        wave_a = S.full_dgx("gpu04")
        refused = S.vrow("gpu04", NVSM_Stress_Test="Skipped")
        sh = self._build(wave_a, refused)
        self.assertEqual(sh.get("gpu04", "NVSM Stress Test"), "Complete")
        self.assertEqual(sh.get("gpu04", "Overall"), "HEALTHY")

    def test_real_still_overwrites_real(self):
        # newer root wins between real states (existing per-step layering)
        wave_a = S.full_dgx("gpu05", "NOT HEALTHY", NVSM_Dump_Health="Failed")
        rerun = S.vrow("gpu05", NVSM_Dump_Health="Complete")
        sh = self._build(wave_a, rerun)
        self.assertEqual(sh.get("gpu05", "NVSM Dump Health"), "Complete")
        self.assertEqual(sh.get("gpu05", "Overall"), "HEALTHY")

    def test_nccl_skipped_does_not_erase_nccl_failure(self):
        wave_a = S.full_dgx("gpu06", "NOT HEALTHY", NCCL_Tests="Failed")
        rerun = S.vrow("gpu06", NCCL_Tests="Skipped")
        sh = self._build(wave_a, rerun)
        self.assertEqual(sh.get("gpu06", "NCCL Tests"), "Failed")
        self.assertEqual(sh.get("gpu06", "Overall"), "NOT HEALTHY")

    def test_nccl_dry_run_cell_does_not_erase_nccl_failure(self):
        # a v1.9 dry-run summary carries 'Dry Run' cells
        wave_a = S.full_dgx("gpu07", "NOT HEALTHY", NCCL_Tests="Failed")
        dry = S.vrow("gpu07", "DRY RUN", NCCL_Tests="Dry Run")
        sh = self._build(wave_a, dry)
        self.assertEqual(sh.get("gpu07", "NCCL Tests"), "Failed")
        self.assertEqual(sh.get("gpu07", "Overall"), "NOT HEALTHY")


class MergeResultUnit(unittest.TestCase):

    def setUp(self):
        S.install_fake_openpyxl()
        self.tb = S._load("tracker_build.py")

    def _hr(self, steps, nccl=""):
        r = self.tb.HostResult("h")
        r.steps = dict(steps)
        r.nccl = nccl
        r.source = "dgx-vitals"
        return r

    def test_rule_table(self):
        cases = [  # (current, new, expected)
            ("Failed", "Skipped", "Failed"),
            ("Complete", "Skipped", "Complete"),
            ("Skipped", "Complete", "Complete"),
            ("Skipped", "Failed", "Failed"),
            ("Failed", "Complete", "Complete"),
            ("Complete", "Failed", "Failed"),
            ("", "Skipped", "Skipped"),
            ("Skipped", "Skipped", "Skipped"),
        ]
        for cur_v, new_v, want in cases:
            cur = self._hr({"NVSM Stress Test": cur_v} if cur_v else {},
                           nccl=cur_v)
            new = self._hr({"NVSM Stress Test": new_v}, nccl=new_v)
            self.tb.merge_result(cur, new)
            self.assertEqual(cur.steps.get("NVSM Stress Test"), want,
                             f"step {cur_v!r} + {new_v!r}")
            self.assertEqual(cur.nccl, want, f"nccl {cur_v!r} + {new_v!r}")


if __name__ == "__main__":
    unittest.main()
