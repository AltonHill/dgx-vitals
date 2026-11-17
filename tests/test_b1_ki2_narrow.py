"""B1 / P1 — KI-2 is narrowed to its failure signature. A recorded NCCL
failure with real run evidence can never be relabelled by note text; the
genuine not-provisioned case (field corpus) still reads Skipped.

Signature (tracker_build.ki2_applies): NCCL state Failed; the NCCL step's
own FULLLOG trailer note carries the runner's "not installed: ...
(nccl-tests partially provisioned)" classification; and the same run's
NCCL block shows no result line in any section, every test exiting
non-zero, and every test's binary reported missing by mpirun
("Executable: <name>_perf") or exiting 127."""

import os
import unittest

from tests import support as S

# mpirun's not-found report, verbatim from the field corpus
# (wave1-w09-full-pass FULLLOG, NCCL block).
NOT_FOUND = """8 total processes failed to start
--------------------------------------------------------------------------
mpirun was unable to find the specified executable file, and therefore
did not launch the job.  This error was first reported for process
rank 0; it may have occurred for other processes as well.

Node:       fx-gpu
Executable: {name}_perf
--------------------------------------------------------------------------"""

# A test that ran and reported a correctness failure (nccl-tests table).
RAN_OOB = """#       size         count      type   redop    root     time   algbw   busbw #wrong     time   algbw   busbw #wrong
     8388608       2097152     float     sum      -1    100.0   83.9  146.8      3    100.0   83.9  146.8      3
# Out of bounds values : 3 FAILED
# Avg bus bandwidth    : 146.8"""

PARTIAL_NOTE = ("all_reduce: 3 out-of-bounds values — correctness FAILED; "
                "not installed: all_gather, reduce_scatter "
                "(nccl-tests partially provisioned)")


def nccl_body(sections):
    out = []
    for name, text, rc in sections:
        out += [f"===== {name}_perf =====", text, f"[{name}_perf exit {rc}]"]
    return "\n".join(out)


def host_log(host, nccl_out, nccl_note, nccl_state="FAIL"):
    secs = list(S.DGX_SECTIONS[:4]) + [("nccl", nccl_out, nccl_state,
                                        nccl_note)] + list(S.DGX_SECTIONS[4:])
    return S.runner_fulllog(host, secs)


class Ki2Narrowed(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)

    def _row(self, host, notes):
        return S.full_dgx(host, "NOT HEALTHY", notes=notes,
                          NCCL_Tests="Failed")

    def _build(self, host, log, notes="NCCL Tests: " + PARTIAL_NOTE):
        root = S.write_root(os.path.join(self.tmp, "r"), {host: log},
                            rows=[self._row(host, notes)])
        return S.run_tracker(["--vitals", root], self.tmp)

    def test_ran_and_failed_with_partial_note_stays_failed(self):
        # REVIEW-A H2's own example: one test ran and failed correctness,
        # the other two were missing. Pre-B, KI-2 read this Skipped/HEALTHY.
        body = nccl_body([("all_reduce", RAN_OOB, 0),
                          ("all_gather", NOT_FOUND.format(name="all_gather"),
                           134),
                          ("reduce_scatter",
                           NOT_FOUND.format(name="reduce_scatter"), 134)])
        sh = self._build("gpu01", host_log("gpu01", body, PARTIAL_NOTE))
        self.assertEqual(sh.get("gpu01", "NCCL Tests"), "Failed")
        self.assertEqual(sh.get("gpu01", "Overall"), "NOT HEALTHY")
        self.assertNotIn("KI-2", sh.get("gpu01", "Notes"))

    def test_crash_without_missing_binary_report_stays_failed(self):
        # every test exited non-zero, but mpirun never said a binary was
        # missing: a crash is a failure, not a missing install.
        body = nccl_body([("all_reduce", "Segmentation fault", 139),
                          ("all_gather", NOT_FOUND.format(name="all_gather"),
                           134),
                          ("reduce_scatter",
                           NOT_FOUND.format(name="reduce_scatter"), 134)])
        note = ("all_reduce: exit 139; not installed: all_gather, "
                "reduce_scatter (nccl-tests partially provisioned)")
        sh = self._build("gpu02", host_log("gpu02", body, note))
        self.assertEqual(sh.get("gpu02", "NCCL Tests"), "Failed")
        self.assertEqual(sh.get("gpu02", "Overall"), "NOT HEALTHY")

    def test_partial_note_on_another_step_cannot_relabel(self):
        # the "partially provisioned" words live in the row's notes, but
        # the NCCL step's own trailer is a plain correctness failure.
        body = nccl_body([("all_reduce", RAN_OOB, 0)])
        sh = self._build("gpu03", host_log(
            "gpu03", body, "all_reduce: 3 out-of-bounds values — "
                           "correctness FAILED"),
            notes="NCCL Tests: all_reduce: 3 out-of-bounds; operator: "
                  "nccl-tests partially provisioned")
        self.assertEqual(sh.get("gpu03", "NCCL Tests"), "Failed")
        self.assertEqual(sh.get("gpu03", "Overall"), "NOT HEALTHY")

    def test_old_partial_note_does_not_flip_a_later_real_failure(self):
        # wave A: genuine non-launch (KI-2 shape). Wave B: --only nccl
        # re-run that ran and failed. Pre-B the merged notes flipped B.
        missing = nccl_body([(n, NOT_FOUND.format(name=n), 134)
                             for n in ("all_reduce", "all_gather",
                                       "reduce_scatter")])
        wave_a = S.write_root(os.path.join(self.tmp, "a"), {
            "gpu04": host_log("gpu04", missing,
                              "all_reduce: exit 134; not installed: "
                              "all_gather, reduce_scatter (nccl-tests "
                              "partially provisioned)")},
            rows=[self._row("gpu04", "NCCL Tests: all_reduce: exit 134; "
                                     "not installed: all_gather, "
                                     "reduce_scatter (nccl-tests partially "
                                     "provisioned)")])
        ran = nccl_body([("all_reduce", RAN_OOB, 0)])
        wave_b = S.write_root(os.path.join(self.tmp, "b"), {
            "gpu04": S.runner_fulllog("gpu04", [(
                "nccl", ran, "FAIL",
                "all_reduce: 3 out-of-bounds values — correctness FAILED")])},
            rows=[S.vrow("gpu04", "NOT HEALTHY", NCCL_Tests="Failed",
                         notes="NCCL Tests: all_reduce: 3 out-of-bounds "
                               "values — correctness FAILED")])
        sh = S.run_tracker(["--vitals", wave_a, "--vitals", wave_b],
                           self.tmp)
        self.assertEqual(sh.get("gpu04", "NCCL Tests"), "Failed")
        self.assertEqual(sh.get("gpu04", "Overall"), "NOT HEALTHY")

    def test_summary_only_partial_note_without_log_stays_failed(self):
        # no FULLLOG: the non-launch half of the signature is unprovable
        root = S.write_vitals_root(
            os.path.join(self.tmp, "r"),
            [self._row("gpu05", "NCCL Tests: " + PARTIAL_NOTE)],
            fulllogs={"gpu05": None})
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get("gpu05", "NCCL Tests"), "Failed")
        self.assertEqual(sh.get("gpu05", "Overall"), "NOT HEALTHY")

    def test_synthetic_true_case_reads_skipped(self):
        # every binary missing, no result line: the legitimate KI-2 case
        missing = nccl_body([(n, NOT_FOUND.format(name=n), 134)
                             for n in ("all_reduce", "all_gather",
                                       "reduce_scatter")])
        note = ("all_reduce: exit 134; not installed: all_gather, "
                "reduce_scatter (nccl-tests partially provisioned)")
        sh = self._build("gpu06", host_log("gpu06", missing, note),
                         notes="NCCL Tests: " + note)
        self.assertEqual(sh.get("gpu06", "NCCL Tests"), "Skipped")
        self.assertEqual(sh.get("gpu06", "Overall"), "HEALTHY")
        self.assertIn("KI-2", sh.get("gpu06", "Notes"))


class Ki2CorpusTrueCase(unittest.TestCase):
    """The field hosts KI-2 was written for (Q5)."""

    def setUp(self):
        self.tmp = S.tempdir(self)

    def test_x10_wave1_reads_skipped(self):
        # corpus: validation-output-wave1/nesd-oma01c-x10-prod-hv-01
        # (fixture wave1-x10-ki2). NCCL trailer: "all_reduce: exit 134;
        # not installed: all_gather, reduce_scatter (nccl-tests partially
        # provisioned)"; mpirun reports all three binaries missing.
        root = S.corpus_root("wave1-x10-ki2", os.path.join(self.tmp, "w"))
        sh = S.run_tracker(["--vitals", root], self.tmp)
        h = "fx-x10-prod-hv-01"
        self.assertEqual(sh.get(h, "NCCL Tests"), "Skipped")
        self.assertIn("KI-2", sh.get(h, "Notes"))
        # the host's dump step failed (exit 255): still NOT HEALTHY
        self.assertEqual(sh.get(h, "NVSM Dump Health"), "Failed")
        self.assertEqual(sh.get(h, "Overall"), "NOT HEALTHY")

    def test_w09_wave1_reads_skipped(self):
        # corpus: validation-output-wave1/nesd-oma01c-w09-prod-hv-01
        # (the other stderr placement: all_gather shows only "exit 134")
        root = S.corpus_root("wave1-w09-full-pass",
                             os.path.join(self.tmp, "w"))
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get("fx-w09-prod-hv-01", "NCCL Tests"),
                         "Skipped")

    def test_signature_unit(self):
        S.install_fake_openpyxl()
        tb = S._load("tracker_build.py")
        ev = tb.parse_fulllog(S.read_fixture(
            "wave1-x10-ki2", "fx-x10-prod-hv-01",
            "fx-x10-prod-hv-01-FULLLOG.txt"))
        self.assertTrue(ev.nccl_ev["non_launch"])
        self.assertFalse(ev.nccl_ev["ran"])
        self.assertEqual(ev.nccl_ev["not_found"],
                         ["all_gather", "all_reduce", "reduce_scatter"])

    def test_ki2_footer_states_only_what_the_logs_show(self):
        root = S.corpus_root("wave1-x10-ki2", os.path.join(self.tmp, "w"))
        sh = S.run_tracker(["--vitals", root], self.tmp)
        foot = "\n".join(sh.footer)
        self.assertIn("KI-2", foot)
        self.assertNotIn("only one of the", foot)
        self.assertIn("all_reduce", foot)


if __name__ == "__main__":
    unittest.main()
