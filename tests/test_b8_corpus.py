"""B8 — fixture suite from the real field corpus. Each case names the
corpus file it derives from (see tests/fixtures/corpus/PROVENANCE.md for
paths and sha256) and states its expected verdict. Hostnames carry the
fixture prefix fx- in place of the site prefix."""

import os
import shutil
import unittest

from tests import support as S


class CorpusCases(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)

    def _vitals(self, case, **kw):
        root = S.corpus_root(case, os.path.join(self.tmp, case), **kw)
        return S.run_tracker(["--vitals", root], self.tmp)

    def test_hollow_stress_pass_w08_full(self):
        # corpus: validation-output-full/nesd-oma01c-w08-prod-hv-01 (v1.4).
        # Stress "PASS" in 0.9 s: nvsm echoed its confirmation prompt and
        # exited; the test never ran.
        # Expected: Stress Failed (recorded PASS overridden), NOT HEALTHY.
        sh = self._vitals("full-w08-hollow-stress")
        h = "fx-w08-prod-hv-01"
        self.assertEqual(sh.get(h, "NVSM Stress Test"), "Failed")
        self.assertIn("confirmation prompt", sh.get(h, "Notes"))
        self.assertEqual(sh.get(h, "Overall"), "NOT HEALTHY")
        self.assertIn("dgx-vitals 1.4", sh.get(h, "Provenance"))

    def test_already_running_refusal_w08_wave1(self):
        # corpus: validation-output-wave1/nesd-oma01c-w08-prod-hv-01
        # (v1.5). Stress "PASS" in 1.2 s over nvsm's "Stress test is
        # already running ... User preference is 'cancel'" — the refusal
        # the runner has failed since v1.8.
        # Expected: Stress Failed, NOT HEALTHY.
        sh = self._vitals("wave1-w08-already-running")
        h = "fx-w08-prod-hv-01"
        self.assertEqual(sh.get(h, "NVSM Stress Test"), "Failed")
        self.assertIn("already running", sh.get(h, "Notes"))
        self.assertEqual(sh.get(h, "Overall"), "NOT HEALTHY")

    def test_already_running_refusal_alone_decides_the_verdict(self):
        # same corpus log, health block edited to DIMM-only (as KI-1
        # hosts read) so the stress override is the only thing standing
        # between the host and HEALTHY WITH WARNINGS.
        # Expected: NOT HEALTHY (pre-B: HEALTHY WITH WARNINGS).
        root = S.corpus_root("wave1-w08-already-running",
                             os.path.join(self.tmp, "w"))
        h = "fx-w08-prod-hv-01"
        p = os.path.join(root, h, f"{h}-FULLLOG.txt")
        with open(p) as f:
            s = f.read()
        s = s.replace("Check PSU Info (Vendor, Model) for Consistency"
                      "....................... Unhealthy",
                      "Check PSU Info (Vendor, Model) for Consistency"
                      "....................... Healthy")
        s = s.replace("2 out of 260 checks are unhealthy",
                      "1 out of 260 checks are unhealthy")
        st = os.stat(p)
        with open(p, "w") as f:
            f.write(s)
        os.utime(p, (st.st_atime, st.st_mtime))
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get(h, "NVSM Health"), "Warned")
        self.assertEqual(sh.get(h, "NVSM Stress Test"), "Failed")
        self.assertEqual(sh.get(h, "Overall"), "NOT HEALTHY")

    def test_genuine_full_pass_w09_wave1(self):
        # corpus: validation-output-wave1/nesd-oma01c-w09-prod-hv-01. All
        # seven steps ran (stress 1827 s, dump 2588 s); health fails only
        # on the DIMM part number (KI-1); NCCL binaries absent (KI-2).
        # Expected: HEALTHY WITH WARNINGS, 100 %, NCCL Skipped.
        sh = self._vitals("wave1-w09-full-pass")
        h = "fx-w09-prod-hv-01"
        for col in ("DGX Release", "NVIDIA-SMI Query", "NVLink Topology",
                    "NVSM Stress Test", "NVSM Dump Health"):
            self.assertEqual(sh.get(h, col), "Complete", col)
        self.assertEqual(sh.get(h, "NVSM Health"), "Warned")
        self.assertEqual(sh.get(h, "NCCL Tests"), "Skipped")
        self.assertEqual(sh.get(h, "% Complete"), 100)
        self.assertEqual(sh.get(h, "Overall"), "HEALTHY WITH WARNINGS")
        self.assertEqual(sh.get(h, "Provenance"),
                         "dgx-vitals 1.5 (summary.csv)")
        self.assertEqual(sh.rc, 0)

    def test_ki2_true_case_x10_wave1(self):
        # corpus: validation-output-wave1/nesd-oma01c-x10-prod-hv-01.
        # nccl-tests binaries not found by mpirun for all three tests.
        # Expected: NCCL Skipped (KI-2); dump Failed -> NOT HEALTHY.
        sh = self._vitals("wave1-x10-ki2")
        h = "fx-x10-prod-hv-01"
        self.assertEqual(sh.get(h, "NCCL Tests"), "Skipped")
        self.assertEqual(sh.get(h, "Overall"), "NOT HEALTHY")

    def test_foreign_cdw_validation_bundle(self):
        # corpus: ferry tarball moonlite-validation-bundle.tar.gz ->
        # cdw-validation-nesd-oma01c-w09-prod-hv-01-20261111-174304.
        # Expected: foreign: cdw-validation, HEALTHY WITH WARNINGS (KI-1).
        root = os.path.join(self.tmp, "bundles")
        shutil.copytree(os.path.join(S.FIXTURES, "bundle-w09"), root)
        sh = S.run_tracker(["--bundles", root], self.tmp)
        h = "fx-w09-prod-hv-01"
        self.assertEqual(sh.get(h, "Provenance"), "foreign: cdw-validation")
        self.assertEqual(sh.get(h, "Source"), "cdw-validation bundle")
        self.assertEqual(sh.get(h, "Overall"), "HEALTHY WITH WARNINGS")

    def test_truncated_bundle(self):
        # derived from the same corpus bundle: summary.txt cut after the
        # 04 line (05/06 and the closing count gone).
        # Expected: INCOMPLETE.
        root = os.path.join(self.tmp, "bundles")
        shutil.copytree(os.path.join(S.FIXTURES, "bundle-w09"), root)
        p = os.path.join(root, "fx-w09-prod-hv-01",
                         "cdw-validation-fx-w09-prod-hv-01-20261111-174304",
                         "summary.txt")
        with open(p) as f:
            s = f.read()
        with open(p, "w") as f:
            f.write(s[:s.index("05-nvsm-stress-test")])
        sh = S.run_tracker(["--bundles", root], self.tmp)
        self.assertEqual(sh.get("fx-w09-prod-hv-01", "Overall"), "INCOMPLETE")

    def test_truncated_fulllog_y10_run1(self):
        # corpus: y10hv02-run1/nesd-oma01c-y10-prod-hv-02-FULLLOG.txt, a
        # real v1.9 log cut short in step 6/6. No summary.csv.
        # Expected: rebuilt-from-fulllog; dump has no result; the stress
        # failure it did record makes it NOT HEALTHY, also incomplete.
        sh = self._vitals("y10-truncated")
        h = "fx-y10-prod-hv-02"
        self.assertEqual(sh.get(h, "Overall"), "NOT HEALTHY")
        self.assertIn("Also incomplete", sh.get(h, "Notes"))

    def test_missing_summary_csv_x_wave2(self):
        # corpus: validation-output-x-wave2, hosts x01-hv-02 / x01-hv-04,
        # summary.csv withheld. Expected: both rebuilt-from-fulllog;
        # hv-02 NOT HEALTHY (stress FAIL), hv-04 HEALTHY WITH WARNINGS.
        sh = self._vitals("xwave2-no-summary")
        self.assertEqual(sh.get("fx-x01-prod-hv-02", "Overall"), "NOT HEALTHY")
        self.assertEqual(sh.get("fx-x01-prod-hv-04", "Overall"),
                         "HEALTHY WITH WARNINGS")
        for h in ("fx-x01-prod-hv-02", "fx-x01-prod-hv-04"):
            self.assertIn("rebuilt-from-fulllog", sh.get(h, "Provenance"))


if __name__ == "__main__":
    unittest.main()
