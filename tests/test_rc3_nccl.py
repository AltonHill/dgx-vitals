"""Brief C item 3 (H2, runner half) — NCCL classified from structure.

A non-launch (-> Skipped; when NCCL is required the Skipped step still
counts, so the host is INCOMPLETE — Brief C amendment 1) only when no
test produced a result line, every test exited non-zero, and every
binary was reported missing by mpirun (or exited 127) — with mpirun's
`Executable:` reports read across the whole block, because they land in
the NEXT test's section (the real w09 field block shows exactly that).
A test that ran and failed is Failed. "partially provisioned" is gone;
the trailer states the fact: "not installed: <binary names>"."""

import unittest

from tests import support as S

MISSING = ("8 total processes failed to start\n"
           "mpirun was unable to find the specified executable file, and "
           "therefore\ndid not launch the job.\n"
           "Executable: {b}\n")


class NcclClassification(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.step = [s for s in self.vit.build_steps("hgx", 60)
                     if s.slug == "nccl"][0]
        self.real = S.corpus_block(*S.W09, "sh -c")

    def v(self, out, required=False):
        return self.vit.verdict(self.step, 0, out, 8, 0.0, required, dur=1.0)

    def test_real_displaced_executable_block_is_a_non_launch(self):
        # all_gather's own section shows only "exit 134"; its Executable:
        # report sits in reduce_scatter's section.
        ev = self.vit.nccl_evidence(self.real)
        self.assertTrue(ev["non_launch"])
        st, note = self.v(self.real)
        self.assertEqual(st, "SKIP")
        self.assertIn("not installed: all_reduce_perf, all_gather_perf, "
                      "reduce_scatter_perf", note)

    def test_required_non_launch_is_skipped_with_the_fact(self):
        # Was test_required_non_launch_is_failed_with_the_fact (FAIL).
        # Loki's ruling (Brief C amendment 1) changed it: a required suite
        # that never launched is missing evidence, so the step is SKIP
        # (counted; the host verdict is INCOMPLETE), not FAIL.
        st, note = self.v(self.real, required=True)
        self.assertEqual(st, "SKIP")
        self.assertIn("NCCL is required for this host", note)
        self.assertIn("not installed: all_reduce_perf, all_gather_perf, "
                      "reduce_scatter_perf", note)

    def test_partially_provisioned_is_retired(self):
        for out in (self.real, self._ran_and_failed()):
            for req in (False, True):
                self.assertNotIn("partially provisioned", self.v(out, req)[1])
        with open(S.os.path.join(S.SRC, "dgx-vitals.py"),
                  encoding="utf-8") as f:
            self.assertNotIn("partially provisioned", f.read())

    def _ran_and_failed(self):
        ok = S.nccl_ok(("all_reduce",)).replace("Out of bounds values : 0",
                                                "Out of bounds values : 3")
        return (ok + "\n===== all_gather_perf =====\n"
                + MISSING.format(b="all_gather_perf")
                + "[all_gather_perf exit 134]\n===== reduce_scatter_perf =====\n"
                + MISSING.format(b="reduce_scatter_perf")
                + "[reduce_scatter_perf exit 134]")

    def test_ran_and_failed_is_failed_full_stop(self):
        st, note = self.v(self._ran_and_failed())
        self.assertEqual(st, "FAIL")
        self.assertIn("all_reduce: 3 out-of-bounds values", note)
        self.assertIn("not installed: all_gather_perf, reduce_scatter_perf",
                      note)

    def test_crash_without_missing_report_is_failed(self):
        out = ("===== all_reduce_perf =====\nSegmentation fault\n"
               "[all_reduce_perf exit 139]")
        st, note = self.v(out)
        self.assertEqual(st, "FAIL")
        self.assertIn("all_reduce: exit 139", note)

    def test_some_passed_some_missing_is_failed(self):
        out = (S.nccl_ok(("all_reduce",)) + "\n===== all_gather_perf =====\n"
               "[all_gather_perf exit 127]")
        st, note = self.v(out)
        self.assertEqual(st, "FAIL")
        self.assertIn("not installed: all_gather_perf", note)

    def test_clean_suite_passes(self):
        st, note = self.v(S.nccl_ok())
        self.assertEqual(st, "PASS")
        self.assertIn("all_reduce 470", note)

    def test_runner_and_tracker_agree_on_the_signature(self):
        S.install_fake_openpyxl()
        tb = S._load("tracker_build.py")
        for out in (self.real, self._ran_and_failed(),
                    S.nccl_ok(), "===== all_reduce_perf =====\n"
                                 "[all_reduce_perf exit 139]"):
            self.assertEqual(self.vit.nccl_evidence(out)["non_launch"],
                             tb.nccl_evidence(out, "")["non_launch"], out[:60])


if __name__ == "__main__":
    unittest.main()
