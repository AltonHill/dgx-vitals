"""B2 / H3 + P2 — tracker-only rebuild from FULLLOG trailers when
summary.csv is missing or stale, and the stricter-wins rule (with a note)
whenever csv, trailers and the runner's own Overall string disagree.

Staleness, as implemented (tracker_build.read_vitals): a host is rebuilt
from its FULLLOG when (1) the root has no (readable) summary.csv, (2) the
csv has no row for a host with a FULLLOG, or only a row with no step
results (the runner's SKIPPED placeholder), or (3) the FULLLOG's mtime is
more than STALE_SLACK_S (2 s) after the csv's."""

import os
import unittest

from tests import support as S

FAIL_STRESS = list(S.DGX_SECTIONS[:4]) + [
    ("stress", "Stress test FAILED: 2 GPU errors", "FAIL",
     "stress output mentions fail/error — check FULLLOG"),
    S.DGX_SECTIONS[5]]


class RebuildWhenStale(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)

    def test_missing_summary_rebuilds_from_corpus_logs(self):
        # corpus: validation-output-x-wave2, two v1.9 hosts, summary.csv
        # withheld (fixture xwave2-no-summary). hv-02's stress FAILed,
        # hv-04 is clean apart from the DIMM KI-1 flag.
        root = S.corpus_root("xwave2-no-summary", os.path.join(self.tmp, "x"))
        self.assertFalse(os.path.exists(os.path.join(root, "summary.csv")))
        sh = S.run_tracker(["--vitals", root], self.tmp)
        a, b = "fx-x01-prod-hv-02", "fx-x01-prod-hv-04"
        for h in (a, b):
            self.assertIn("rebuilt-from-fulllog", sh.get(h, "Provenance"))
            self.assertIn("dgx-vitals 1.9", sh.get(h, "Provenance"))
            self.assertEqual(sh.get(h, "% Complete"), 100 if h == b else 83)
        self.assertEqual(sh.get(a, "NVSM Stress Test"), "Failed")
        self.assertEqual(sh.get(a, "Overall"), "NOT HEALTHY")
        self.assertEqual(sh.get(b, "Overall"), "HEALTHY WITH WARNINGS")
        self.assertEqual(sh.rc, 2)

    def test_rebuilt_rows_match_the_runners_own_summary(self):
        # the same two hosts built from their real summary.csv rows
        # (fixture xwave2-summary-rows) give the same verdicts.
        logs = S.corpus_root("xwave2-no-summary", os.path.join(self.tmp, "a"))
        with_csv = os.path.join(self.tmp, "b")
        S.corpus_root("xwave2-no-summary", with_csv)
        import shutil
        shutil.copyfile(os.path.join(S.FIXTURES, "xwave2-summary-rows",
                                     "summary.csv"),
                        os.path.join(with_csv, "summary.csv"))
        a = S.run_tracker(["--vitals", logs], self.tmp, "a.xlsx")
        b = S.run_tracker(["--vitals", with_csv], self.tmp, "b.xlsx")
        self.assertEqual(sorted(a.rows), ["fx-x01-prod-hv-02",
                                          "fx-x01-prod-hv-04"])
        for h in a.rows:
            for col in S.SUMMARY_HEADER[1:8]:
                col = {"Release": "DGX Release"}.get(col, col)
                self.assertEqual(a.get(h, col), b.get(h, col), (h, col))
            self.assertEqual(a.get(h, "Overall"), b.get(h, "Overall"))
            self.assertIn("summary.csv", b.get(h, "Provenance"))

    def test_host_missing_from_csv_is_rebuilt(self):
        root = S.write_root(os.path.join(self.tmp, "r"), {
            "gpu01": S.runner_fulllog("gpu01", S.DGX_SECTIONS),
            "gpu02": S.runner_fulllog("gpu02", FAIL_STRESS)},
            rows=[S.full_dgx("gpu01")])
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertIn("rebuilt-from-fulllog", sh.get("gpu02", "Provenance"))
        self.assertEqual(sh.get("gpu02", "Overall"), "NOT HEALTHY")
        self.assertIn("(summary.csv)", sh.get("gpu01", "Provenance"))

    def test_skipped_placeholder_row_recovers_from_fulllog(self):
        # REVIEW-A H3: a re-run after a lost summary writes SKIPPED rows
        # for finished hosts; the real verdict is in the FULLLOG.
        root = S.write_root(os.path.join(self.tmp, "r"), {
            "gpu03": S.runner_fulllog("gpu03", FAIL_STRESS)},
            rows=[S.vrow("gpu03", "SKIPPED",
                         notes="FULLLOG exists (use --force to redo)")])
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get("gpu03", "Overall"), "NOT HEALTHY")
        self.assertEqual(sh.get("gpu03", "NVSM Stress Test"), "Failed")
        self.assertIn("rebuilt-from-fulllog", sh.get("gpu03", "Provenance"))

    def test_fulllog_newer_than_csv_rebuilds_and_stricter_wins(self):
        # the csv says all six passed; the FULLLOG, rewritten after it
        # (an interrupted --force re-run), records the stress failure.
        root = S.write_root(os.path.join(self.tmp, "r"), {
            "gpu04": S.runner_fulllog("gpu04", FAIL_STRESS)},
            rows=[S.full_dgx("gpu04")], csv_age=-3600)
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertIn("rebuilt-from-fulllog", sh.get("gpu04", "Provenance"))
        self.assertEqual(sh.get("gpu04", "NVSM Stress Test"), "Failed")
        self.assertEqual(sh.get("gpu04", "Overall"), "NOT HEALTHY")
        self.assertIn("NVSM Stress Test: summary.csv says Complete, FULLLOG "
                      "says Failed — stricter kept (Failed)",
                      sh.get("gpu04", "Notes"))

    def test_stale_csv_failure_is_never_relabelled_by_the_log(self):
        # the stale csv recorded a failure the newer log no longer shows
        root = S.write_root(os.path.join(self.tmp, "r"), {
            "gpu05": S.runner_fulllog("gpu05", S.DGX_SECTIONS)},
            rows=[S.full_dgx("gpu05", "NOT HEALTHY",
                             NVSM_Dump_Health="Failed")], csv_age=-3600)
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get("gpu05", "NVSM Dump Health"), "Failed")
        self.assertEqual(sh.get("gpu05", "Overall"), "NOT HEALTHY")

    def test_within_slack_is_not_stale(self):
        # copy tools / FAT timestamps: 1 s newer is inside the 2 s slack
        root = S.write_root(os.path.join(self.tmp, "r"), {
            "gpu06": S.runner_fulllog("gpu06", S.DGX_SECTIONS)},
            rows=[S.full_dgx("gpu06")], csv_age=-1)
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertIn("(summary.csv)", sh.get("gpu06", "Provenance"))
        self.assertEqual(sh.get("gpu06", "Overall"), "HEALTHY")

    def test_just_beyond_slack_is_stale(self):
        root = S.write_root(os.path.join(self.tmp, "r"), {
            "gpu07": S.runner_fulllog("gpu07", S.DGX_SECTIONS)},
            rows=[S.full_dgx("gpu07")], csv_age=-3)
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertIn("rebuilt-from-fulllog", sh.get("gpu07", "Provenance"))

    def test_from_fulllog_flag_forces_rebuild(self):
        root = S.write_root(os.path.join(self.tmp, "r"), {
            "gpu08": S.runner_fulllog("gpu08", S.DGX_SECTIONS)},
            rows=[S.full_dgx("gpu08")])
        sh = S.run_tracker(["--vitals", root, "--from-fulllog"], self.tmp)
        self.assertIn("rebuilt-from-fulllog", sh.get("gpu08", "Provenance"))
        self.assertEqual(sh.get("gpu08", "Overall"), "HEALTHY")

    def test_rebuild_that_cannot_recover_every_step_is_incomplete(self):
        # corpus: y10hv02-run1 — a v1.9 log cut short in step 6/6 (no
        # trailer, no OVERALL). Its stress step recorded FAIL, so the
        # verdict is NOT HEALTHY, and the row says it is also incomplete.
        root = S.corpus_root("y10-truncated", os.path.join(self.tmp, "y"))
        sh = S.run_tracker(["--vitals", root], self.tmp)
        h = "fx-y10-prod-hv-02"
        self.assertIn("rebuilt-from-fulllog", sh.get(h, "Provenance"))
        self.assertEqual(sh.get(h, "NVSM Dump Health"), "")
        self.assertEqual(sh.get(h, "Overall"), "NOT HEALTHY")
        notes = sh.get(h, "Notes")
        self.assertIn("Also incomplete", notes)
        self.assertIn("5 of 6 planned step results", notes)
        self.assertIn("NVSM Dump Health has no result", notes)


class RunnerOverallDisagreement(unittest.TestCase):
    """P2: the runner's Overall string vs the derived verdict."""

    def setUp(self):
        self.tmp = S.tempdir(self)

    def test_runner_stricter_than_states_is_kept_and_named(self):
        # every trailer PASS, but the run's own OVERALL says NOT HEALTHY
        log = S.runner_fulllog("gpu10", S.DGX_SECTIONS, overall="NOT HEALTHY")
        root = S.write_root(os.path.join(self.tmp, "r"), {"gpu10": log},
                            rows=[S.full_dgx("gpu10", "NOT HEALTHY")])
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get("gpu10", "Overall"), "NOT HEALTHY")
        self.assertIn("the runner's FULLLOG reads NOT HEALTHY; the step "
                      "states derive HEALTHY — stricter kept (NOT HEALTHY)",
                      sh.get("gpu10", "Notes"))

    def test_states_stricter_than_runner_are_kept_and_named(self):
        log = S.runner_fulllog("gpu11", FAIL_STRESS, overall="HEALTHY")
        root = S.write_root(os.path.join(self.tmp, "r"), {"gpu11": log},
                            rows=[S.full_dgx("gpu11", "HEALTHY",
                                             NVSM_Stress_Test="Failed")])
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get("gpu11", "Overall"), "NOT HEALTHY")
        self.assertIn("reads HEALTHY; the step states derive NOT HEALTHY",
                      sh.get("gpu11", "Notes"))

    def test_csv_and_log_disagree_on_a_step_fresh_csv(self):
        # fresh csv says Complete, the log's trailer says FAIL
        root = S.write_root(os.path.join(self.tmp, "r"), {
            "gpu12": S.runner_fulllog("gpu12", FAIL_STRESS)},
            rows=[S.full_dgx("gpu12")])
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertIn("(summary.csv)", sh.get("gpu12", "Provenance"))
        self.assertEqual(sh.get("gpu12", "NVSM Stress Test"), "Failed")
        self.assertEqual(sh.get("gpu12", "Overall"), "NOT HEALTHY")
        self.assertIn("summary.csv says Complete, FULLLOG says Failed",
                      sh.get("gpu12", "Notes"))

    def test_csv_pass_without_a_log_trailer_is_not_counted(self):
        log = S.runner_fulllog("gpu13", S.DGX_SECTIONS[:5])   # no dump
        root = S.write_root(os.path.join(self.tmp, "r"), {"gpu13": log},
                            rows=[S.full_dgx("gpu13")])
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get("gpu13", "NVSM Dump Health"), "")
        self.assertEqual(sh.get("gpu13", "Overall"), "INCOMPLETE")

    def test_consistent_not_healthy_does_not_block_ki1(self):
        # P2 compares like with like: a run that says NOT HEALTHY because
        # of the DIMM flag agrees with its states, so KI-1 still applies.
        root = S.corpus_root("xwave2-no-summary", os.path.join(self.tmp, "x"),
                             hosts=["fx-x01-prod-hv-04"])
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get("fx-x01-prod-hv-04", "Overall"),
                         "HEALTHY WITH WARNINGS")
        self.assertNotIn("stricter kept", sh.get("fx-x01-prod-hv-04", "Notes"))


if __name__ == "__main__":
    unittest.main()
