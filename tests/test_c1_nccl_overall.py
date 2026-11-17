"""C1 — a Failed NCCL state forces Overall NOT HEALTHY on every path
(no --hosts, --hosts, merged roots, adjudication re-render); NCCL that
was never in the run (no cell) is neutral; NCCL stays out of % Complete."""

import os
import unittest

from tests import support as S


def nccl_failed_host(name="gpu01"):
    return S.full_dgx(name, overall="NOT HEALTHY",
                      notes="NCCL Tests: all_reduce: #wrong 3",
                      NCCL_Tests="Failed")


class NcclFailedForcesNotHealthy(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)
        self.root = S.write_vitals_root(os.path.join(self.tmp, "wave-a"),
                                        [nccl_failed_host()])

    def test_a_with_hosts(self):
        fleet = S.write_fleet(os.path.join(self.tmp, "fleet.csv"), ["gpu01"])
        sh = S.run_tracker(["--hosts", fleet, "--vitals", self.root], self.tmp)
        self.assertEqual(sh.get("gpu01", "Overall"), "NOT HEALTHY")
        self.assertEqual(sh.get("gpu01", "NCCL Tests"), "Failed")

    def test_b_without_hosts(self):
        sh = S.run_tracker(["--vitals", self.root], self.tmp)
        self.assertEqual(sh.get("gpu01", "Overall"), "NOT HEALTHY")

    def test_c_merged_from_two_roots(self):
        # wave B: an --only dump re-run, no NCCL cell at all
        rerun = S.write_vitals_root(
            os.path.join(self.tmp, "wave-b"),
            [S.vrow("gpu01", NVSM_Dump_Health="Complete")])
        sh = S.run_tracker(["--vitals", self.root, "--vitals", rerun],
                           self.tmp)
        self.assertEqual(sh.get("gpu01", "Overall"), "NOT HEALTHY")
        self.assertEqual(sh.get("gpu01", "NCCL Tests"), "Failed")

    def test_c2_failure_arrives_in_second_root(self):
        clean = S.write_vitals_root(os.path.join(self.tmp, "wave-0"),
                                    [S.full_dgx("gpu01")])
        nccl_only = S.write_vitals_root(
            os.path.join(self.tmp, "nccl-rerun"),
            [S.vrow("gpu01", "NOT HEALTHY", NCCL_Tests="Failed")])
        sh = S.run_tracker(["--vitals", clean, "--vitals", nccl_only],
                           self.tmp)
        self.assertEqual(sh.get("gpu01", "Overall"), "NOT HEALTHY")

    def test_d_adjudication_rerender_keeps_nccl_failure(self):
        # an unrelated adjudication clears and recomputes the verdict
        root = S.write_vitals_root(os.path.join(self.tmp, "wave-adj"),
                                   [nccl_failed_host("gpu02")],
                                   cards={"gpu02"})
        adj = S.write_adjudications(
            os.path.join(self.tmp, "adj.csv"),
            [("gpu02", "NVSM Stress Test", "Warned", "ruled benign")])
        sh = S.run_tracker(["--vitals", root, "--adjudications", adj],
                           self.tmp)
        self.assertEqual(sh.get("gpu02", "Overall"), "NOT HEALTHY")
        # the re-rendered card was drawn (and from the same verdict)
        imgs = [p for _a, p in sh.data["Validation Tracker"]["images"]]
        self.assertTrue(any(p.endswith("gpu02-adjudicated.png") for p in imgs))

    def test_pct_complete_still_excludes_nccl(self):
        fleet = S.write_fleet(os.path.join(self.tmp, "fleet.csv"), ["gpu01"])
        sh = S.run_tracker(["--hosts", fleet, "--vitals", self.root], self.tmp)
        self.assertEqual(sh.get("gpu01", "% Complete"), 100)

    def test_all_paths_agree(self):
        fleet = S.write_fleet(os.path.join(self.tmp, "fleet.csv"), ["gpu01"])
        a = S.run_tracker(["--hosts", fleet, "--vitals", self.root], self.tmp)
        b = S.run_tracker(["--vitals", self.root], self.tmp)
        c = S.run_tracker(["--vitals", self.root, "--vitals", self.root],
                          self.tmp)
        got = {x.get("gpu01", "Overall") for x in (a, b, c)}
        self.assertEqual(got, {"NOT HEALTHY"})

    def test_hgx_row_nccl_failed_with_hosts(self):
        # HGX: Release, SMI, Topology, NCCL — NCCL is the only fabric proof
        root = S.write_vitals_root(
            os.path.join(self.tmp, "comet"),
            [S.vrow("hgx01", "NOT HEALTHY", Release="Complete",
                    NVIDIA_SMI_Query="Complete", NVLink_Topology="Complete",
                    NCCL_Tests="Failed")])
        fleet = S.write_fleet(os.path.join(self.tmp, "f.csv"), ["hgx01"])
        sh = S.run_tracker(["--hosts", fleet, "--vitals", root], self.tmp)
        self.assertEqual(sh.get("hgx01", "Overall"), "NOT HEALTHY")


class VerdictDerivedNotCopied(unittest.TestCase):
    """One verdict path: Overall comes from the states, not from whichever
    string the runner happened to write."""

    def setUp(self):
        self.tmp = S.tempdir(self)

    def test_inconsistent_runner_overall_is_not_trusted(self):
        root = S.write_vitals_root(
            os.path.join(self.tmp, "r"),
            [S.full_dgx("gpu08", "HEALTHY", NCCL_Tests="Failed")])
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get("gpu08", "Overall"), "NOT HEALTHY")

    def test_row_without_step_evidence_keeps_runner_label(self):
        root = S.write_vitals_root(
            os.path.join(self.tmp, "r"),
            [S.vrow("gpu09", "SKIPPED",
                    notes="FULLLOG exists (use --force to redo)")])
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get("gpu09", "Overall"), "SKIPPED")

    def test_fleet_host_without_results_is_no_data(self):
        root = S.write_vitals_root(os.path.join(self.tmp, "r"),
                                   [S.full_dgx("gpu10")])
        fleet = S.write_fleet(os.path.join(self.tmp, "f.csv"),
                              ["gpu10", "gpu11"])
        sh = S.run_tracker(["--hosts", fleet, "--vitals", root], self.tmp)
        self.assertEqual(sh.get("gpu11", "Overall"), "NO DATA")
        self.assertEqual(sh.get("gpu10", "Overall"), "HEALTHY")


class NcclNeutralOrPassing(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)

    def _both(self, row):
        root = S.write_vitals_root(os.path.join(self.tmp, "r"), [row])
        fleet = S.write_fleet(os.path.join(self.tmp, "f.csv"),
                              [row["Hostname"]])
        return (S.run_tracker(["--hosts", fleet, "--vitals", root], self.tmp),
                S.run_tracker(["--vitals", root], self.tmp))

    def test_nccl_passed_unchanged(self):
        for sh in self._both(S.full_dgx("gpu03", NCCL_Tests="Complete")):
            self.assertEqual(sh.get("gpu03", "Overall"), "HEALTHY")
            self.assertEqual(sh.get("gpu03", "% Complete"), 100)

    def test_no_nccl_cell_is_neutral(self):
        for sh in self._both(S.full_dgx("gpu04")):
            self.assertEqual(sh.get("gpu04", "NCCL Tests"), "")
            self.assertEqual(sh.get("gpu04", "Overall"), "HEALTHY")
            self.assertEqual(sh.get("gpu04", "% Complete"), 100)

    def test_nccl_skipped_not_required_is_neutral(self):
        # runner skip (nccl-tests absent, not required): verdict unchanged
        for sh in self._both(S.full_dgx("gpu05", NCCL_Tests="Skipped")):
            self.assertEqual(sh.get("gpu05", "Overall"), "HEALTHY")

    def test_nccl_failed_with_other_failure_still_not_healthy(self):
        for sh in self._both(S.full_dgx("gpu06", "NOT HEALTHY",
                                        NCCL_Tests="Failed",
                                        NVSM_Dump_Health="Failed")):
            self.assertEqual(sh.get("gpu06", "Overall"), "NOT HEALTHY")

    def test_ki2_note_text_cannot_relabel_a_real_failure(self):
        # P1 (Brief B): this pin was A2's test_ki2_still_relabels_partial_
        # install, which held the unsafe behaviour (Failed + any note
        # saying "partially provisioned" read Skipped / HEALTHY). Inverted:
        # the row's note says partially provisioned, but the run left no
        # evidence that the binaries were missing, so the recorded
        # failure stands.
        row = S.full_dgx("gpu07", "NOT HEALTHY", NCCL_Tests="Failed",
                         notes="NCCL Tests: nccl-tests partially "
                               "provisioned (1 of 3 binaries)")
        for sh in self._both(row):
            self.assertEqual(sh.get("gpu07", "NCCL Tests"), "Failed")
            self.assertEqual(sh.get("gpu07", "Overall"), "NOT HEALTHY")

if __name__ == "__main__":
    unittest.main()
