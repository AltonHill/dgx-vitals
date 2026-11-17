"""Brief C amendment 2 — Package A (Loki, 2026-11-16).

"A waived test is absent evidence, and absent evidence is INCOMPLETE at
every layer." A run made with --no-require-nccl reports the host
INCOMPLETE (exit 2, the code every INCOMPLETE host has) when the opt-out
is the sole reason the host would otherwise be HEALTHY. The waiver stays
on the record exactly as before (OVERALL line, summary Notes, FULLLOG
header); nothing errors or blocks.

The waiver binds where NCCL would otherwise be required: the hgx profile
default. On dgx NCCL is not required in the first place, so the flag
waives nothing and the verdict is the same as without it (see
REPORT-C-AMEND2 for this reading).

Guards that must not soften: opt-out + NCCL ran-and-failed is NOT HEALTHY
(test_rc10_amend1.test_c_opt_out_still_never_hides_ran_and_failed, kept);
opt-out + any other FAIL is NOT HEALTHY; runs without the opt-out are
unchanged; --check is unchanged. The tracker-view tests record what the
tracker reads (Brief D gave it the 2.0 semantics; one view converted)."""

import unittest

from tests import support as S
from tests.test_rc4_runs import fulllog, overall_line
from tests.test_rc1_check import check_out

HOSTS_HGX = "hostname,address\nhgx01,a\n"
HOSTS_DGX = "hostname,address\ngpu01,a\n"
OPT = "NCCL not required (operator opt-out)"


def tracker_row(root, host):
    S.install_fake_openpyxl()
    tb = S._load("tracker_build.py")
    rows = tb.read_vitals(root)
    tb.finalize(rows[host])
    return rows[host]


class OptOutIsIncomplete(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.none = S.corpus_block(*S.W09, "sh -c")    # real w09 non-launch

    def hgx(self, argv, **kw):
        r = S.ScriptedRunner(S.hgx_host_script(**kw))
        return S.drive_main(self, self.vit, ["--profile", "hgx"] + argv,
                            {"a": r}, HOSTS_HGX)

    def test_hgx_opt_out_non_launch_is_incomplete(self):
        rc, out, err, root = self.hgx(["--no-require-nccl"], nccl=self.none)
        self.assertEqual(rc, 2, out + err)
        log = fulllog(root, "hgx01")
        # verdict and reason on one line
        self.assertEqual(overall_line(log),
                         f"OVERALL: INCOMPLETE (3/4 steps passed) — {OPT}")
        header = [ln for ln in log.splitlines()
                  if ln.startswith("Expected:")][0]
        self.assertTrue(header.endswith(f"· {OPT}"), header)
        self.assertIn("· SKIP · not installed: all_reduce_perf", log)
        row = S.read_summary(root)["hgx01"]
        self.assertEqual(row["Overall"], "INCOMPLETE")
        self.assertEqual(row["NCCL Tests"], "Skipped")
        self.assertIn(OPT, row["Notes"])
        self.assertIn("step skipped (not required)", row["Notes"])

    def test_hgx_opt_out_with_another_fail_is_not_healthy(self):
        # the INCOMPLETE applies only when the opt-out is the sole cause
        rc, _o, _e, root = self.hgx(["--no-require-nccl"], nccl=self.none,
                                    gpus=4)
        self.assertEqual(rc, 2)
        line = overall_line(fulllog(root, "hgx01"))
        self.assertTrue(line.startswith("OVERALL: NOT HEALTHY"), line)
        self.assertTrue(line.endswith(OPT), line)
        self.assertEqual(S.read_summary(root)["hgx01"]["Overall"],
                         "NOT HEALTHY")

    def test_hgx_opt_out_with_nccl_that_ran_and_passed_is_healthy(self):
        # the opt-out is not the reason this host passes: the evidence is
        # present, so the verdict is HEALTHY (the waiver is still printed)
        rc, out, err, root = self.hgx(["--no-require-nccl"])
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(overall_line(fulllog(root, "hgx01")),
                         f"OVERALL: HEALTHY (4/4 steps passed) — {OPT}")

    def test_without_opt_out_unchanged(self):
        # required non-launch: amendment 1's INCOMPLETE, no opt-out wording
        rc, _o, _e, root = self.hgx([], nccl=self.none)
        self.assertEqual(rc, 2)
        log = fulllog(root, "hgx01")
        self.assertEqual(overall_line(log),
                         "OVERALL: INCOMPLETE (3/4 steps passed)")
        self.assertNotIn("opt-out", log)
        # and a clean hgx host is HEALTHY
        rc, _o, _e, root = self.hgx([])
        self.assertEqual(rc, 0)
        self.assertEqual(overall_line(fulllog(root, "hgx01")),
                         "OVERALL: HEALTHY (4/4 steps passed)")

    def test_dgx_opt_out_waives_nothing(self):
        # dgx: NCCL is not required, the step is not planned; the flag
        # changes no verdict (the waiver wording is still printed)
        r = S.ScriptedRunner(S.dgx_host_script())
        rc, out, err, root = S.drive_main(
            self, self.vit, ["--no-require-nccl"], {"a": r}, HOSTS_DGX)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(overall_line(fulllog(root, "gpu01")),
                         f"OVERALL: HEALTHY (6/6 steps passed) — {OPT}")

    def test_check_unchanged_under_opt_out(self):
        # --check is a preflight, not a verdict: missing binaries under the
        # opt-out are information and the host is READY (exit 0)
        r = S.ScriptedRunner([
            ("DGXV-CHECK-BEGIN", 0, check_out(hostname="hgx01", platform="hgx",
                                              bins=()), 0.2),
            ("true", 0, "", 0.1)])
        rc, out, err, _root = S.drive_main(
            self, self.vit, ["--check", "--profile", "hgx",
                             "--no-require-nccl"], {"a": r}, HOSTS_HGX)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("info  nccl-tests", out)
        self.assertIn(f"not installed: all_reduce_perf, all_gather_perf, "
                      f"reduce_scatter_perf ({OPT})", out)
        self.assertIn("hgx01  (ssh -> a)  READY", out)
        self.assertNotIn("NOT READY", out)
        self.assertNotIn("INCOMPLETE", out)


class OptOutDocs(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()

    def test_help_says_incomplete(self):
        _rc, out, _e = S.run_vitals_main(self.vit, ["--help"])
        flat = " ".join(out.split())
        self.assertIn("the NCCL step is skipped by the operator's recorded "
                      "choice, and the host is INCOMPLETE (missing "
                      "evidence), never HEALTHY", flat)
        self.assertIn("skipped by the operator's opt-out "
                      "(--no-require-nccl)", flat)
        self.assertIn("A waived test is missing evidence", flat)
        self.assertIn("NOT HEALTHY with or without the opt-out", flat)
        self.assertNotIn("without counting it", flat)

    def test_docstring_legends(self):
        doc = " ".join(self.vit.__doc__.split())
        self.assertIn("an opt-out host whose NCCL step was skipped is "
                      "INCOMPLETE, never HEALTHY", doc)
        self.assertIn("skipped by the operator's recorded opt-out", doc)


class TrackerViewOfOptOut(unittest.TestCase):
    """The tracker over opt-out runs; records what it reads."""

    def setUp(self):
        self.vit = S.load_vitals()
        self.none = S.corpus_block(*S.W09, "sh -c")

    def test_tracker_view_hgx_opt_out(self):
        r = S.ScriptedRunner(S.hgx_host_script(nccl=self.none))
        _rc, _o, _e, root = S.drive_main(
            self, self.vit, ["--profile", "hgx", "--no-require-nccl"],
            {"a": r}, HOSTS_HGX)
        row = tracker_row(root, "hgx01")
        self.assertEqual(row.overall, "INCOMPLETE")
        # Converted by Brief D Part 1 items 1 and 5: INCOMPLETE is now a
        # recognised verdict (no "not a recognized verdict" reason), and
        # the hgx opt-out row names its waiver, proven from the run's own
        # FULLLOG. Was: ["...is not a recognized verdict",
        # "NCCL Tests skipped — required by the hgx profile"].
        self.assertEqual(row.reasons, [
            "NCCL Tests not tested — waived by the operator's opt-out "
            "(--no-require-nccl), recorded in the run's own FULLLOG; a "
            "waived test is missing evidence"])

    def test_tracker_view_dgx_opt_out(self):
        r = S.ScriptedRunner(S.dgx_host_script())
        _rc, _o, _e, root = S.drive_main(
            self, self.vit, ["--no-require-nccl"], {"a": r}, HOSTS_DGX)
        row = tracker_row(root, "gpu01")
        self.assertEqual(row.overall, "HEALTHY")
        self.assertEqual(row.reasons, [])


if __name__ == "__main__":
    unittest.main()
