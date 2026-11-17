"""B3 / C2 (tracker half) — INCOMPLETE is a first-class verdict. Partial
evidence (a missing or skipped step the profile expects, NCCL absent where
required, a truncated log, a profile the evidence does not establish) is
INCOMPLETE: never HEALTHY, never dropped from a denominator, and visibly
distinct in the sheet and the CSV."""

import os
import unittest

from tests import support as S


def only(host, *keys, overall=None):
    secs = [s for s in S.DGX_SECTIONS if s[0] in keys]
    return S.runner_fulllog(host, secs, overall=overall)


class IncompleteVerdict(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)

    def _one(self, host, log, row, *args):
        root = S.write_root(os.path.join(self.tmp, "r"), {host: log},
                            rows=[row])
        return S.run_tracker(["--vitals", root] + list(args), self.tmp)

    def test_only_partial_run_alone_is_incomplete(self):
        # REVIEW-A C2: --only smi-query wrote HEALTHY (1/1) and a green card
        sh = self._one("gpu01", only("gpu01", "smi"),
                       S.vrow("gpu01", NVIDIA_SMI_Query="Complete"))
        self.assertEqual(sh.get("gpu01", "Overall"), "INCOMPLETE")
        self.assertNotEqual(sh.get("gpu01", "% Complete"), 100)

    def test_partial_runs_that_together_cover_the_profile_are_healthy(self):
        a = S.write_root(os.path.join(self.tmp, "a"), {
            "gpu02": only("gpu02", "release", "health", "smi", "topo")},
            rows=[S.vrow("gpu02", Release="Complete",
                         NVSM_Health="Complete", NVIDIA_SMI_Query="Complete",
                         NVLink_Topology="Complete")])
        b = S.write_root(os.path.join(self.tmp, "b"), {
            "gpu02": only("gpu02", "stress", "dump")},
            rows=[S.vrow("gpu02", NVSM_Stress_Test="Complete",
                         NVSM_Dump_Health="Complete")])
        sh = S.run_tracker(["--vitals", a, "--vitals", b], self.tmp)
        self.assertEqual(sh.get("gpu02", "Overall"), "HEALTHY")
        self.assertEqual(sh.get("gpu02", "% Complete"), 100)

    def test_refused_stress_is_incomplete_not_healthy(self):
        # REVIEW-A C2: nvsm refused (uncleared alerts) -> runner SKIP ->
        # runner HEALTHY (5/5). The hour-long test never ran.
        secs = list(S.DGX_SECTIONS)
        secs[4] = ("stress", "Stress testing will not be done", "SKIP",
                   "nvsm refused to run: uncleared system alerts")
        sh = self._one("gpu03", S.runner_fulllog("gpu03", secs),
                       S.full_dgx("gpu03", NVSM_Stress_Test="Skipped"))
        self.assertEqual(sh.get("gpu03", "Overall"), "INCOMPLETE")
        self.assertEqual(sh.get("gpu03", "% Complete"), 83)
        self.assertIn("NVSM Stress Test skipped — required by the dgx "
                      "profile", sh.get("gpu03", "Notes"))

    def test_hgx_without_nccl_is_incomplete(self):
        secs = [("os-release", 'PRETTY_NAME="Ubuntu"', "PASS", "Ubuntu"),
                ("smi", "Attached GPUs : 8", "PASS", "8 GPUs"),
                ("topo", "GPU0 X NV18", "PASS", "")]
        log = S.runner_fulllog("hgx01", secs, platform="hgx (detected)")
        sh = self._one("hgx01", log, S.vrow(
            "hgx01", Release="Complete", NVIDIA_SMI_Query="Complete",
            NVLink_Topology="Complete"))
        self.assertEqual(sh.get("hgx01", "Overall"), "INCOMPLETE")
        self.assertIn("NCCL Tests has no result", sh.get("hgx01", "Notes"))
        self.assertEqual(sh.get("hgx01", "% Complete"), 100)  # 3/3 tracker

    def test_hgx_nccl_skipped_is_incomplete(self):
        secs = [("os-release", 'PRETTY_NAME="Ubuntu"', "PASS", "Ubuntu"),
                ("smi", "Attached GPUs : 8", "PASS", "8 GPUs"),
                ("topo", "GPU0 X NV18", "PASS", ""),
                ("nccl", "", "SKIP", "nccl-tests not installed on this host "
                                     "— step skipped")]
        log = S.runner_fulllog("hgx02", secs, platform="hgx (detected)")
        sh = self._one("hgx02", log, S.vrow(
            "hgx02", Release="Complete", NVIDIA_SMI_Query="Complete",
            NVLink_Topology="Complete", NCCL_Tests="Skipped"))
        self.assertEqual(sh.get("hgx02", "Overall"), "INCOMPLETE")

    def test_dgx_without_nccl_is_complete_unless_required(self):
        log = S.runner_fulllog("gpu04", S.DGX_SECTIONS)
        row = S.full_dgx("gpu04")
        self.assertEqual(self._one("gpu04", log, row).get("gpu04", "Overall"),
                         "HEALTHY")
        sh = self._one("gpu04", log, row, "--require-nccl")
        self.assertEqual(sh.get("gpu04", "Overall"), "INCOMPLETE")
        self.assertIn("NCCL Tests has no result", sh.get("gpu04", "Notes"))

    def test_summary_row_without_fulllog_is_incomplete(self):
        root = S.write_vitals_root(os.path.join(self.tmp, "r"),
                                   [S.full_dgx("gpu05")],
                                   fulllogs={"gpu05": None})
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get("gpu05", "Overall"), "INCOMPLETE")
        self.assertIn("no FULLLOG on disk", sh.get("gpu05", "Notes"))

    def test_log_without_overall_trailer_is_incomplete(self):
        # all six trailers present, cut short before OVERALL
        log = S.runner_fulllog("gpu06", S.DGX_SECTIONS, overall=False)
        sh = self._one("gpu06", log, S.full_dgx("gpu06"))
        self.assertEqual(sh.get("gpu06", "Overall"), "INCOMPLETE")
        self.assertIn("no OVERALL trailer", sh.get("gpu06", "Notes"))

    def test_truncated_corpus_log_without_failure_is_incomplete(self):
        # derived from corpus wave1 w09 (fixture wave1-w09-full-pass): the
        # log cut at the start of step 7/7's output, as a crash or a
        # mid-run copy leaves it; no summary.csv.
        text = S.read_fixture("wave1-w09-full-pass", "fx-w09-prod-hv-01",
                              "fx-w09-prod-hv-01-FULLLOG.txt")
        cut = text[:text.index("[7/7] sudo nvsm dump health")]
        root = S.write_root(os.path.join(self.tmp, "t"),
                            {"fx-w09-prod-hv-01": cut})
        sh = S.run_tracker(["--vitals", root], self.tmp)
        h = "fx-w09-prod-hv-01"
        self.assertEqual(sh.get(h, "Overall"), "INCOMPLETE")
        self.assertEqual(sh.get(h, "NVSM Health"), "Warned")   # KI-1 still
        self.assertEqual(sh.get(h, "NVSM Dump Health"), "")

    def test_profile_not_established_is_incomplete(self):
        # only smi + topo ran and nothing names the platform
        log = S.runner_fulllog("gpu07", [s for s in S.DGX_SECTIONS
                                         if s[0] in ("smi", "topo")],
                               platform="")
        sh = self._one("gpu07", log, S.vrow(
            "gpu07", NVIDIA_SMI_Query="Complete", NVLink_Topology="Complete"))
        self.assertEqual(sh.get("gpu07", "Overall"), "INCOMPLETE")
        self.assertIn("profile (dgx/hgx) not established",
                      sh.get("gpu07", "Notes"))

    def test_recorded_failure_beats_incomplete(self):
        log = S.runner_fulllog("gpu08", [
            ("smi", "Attached GPUs : 7", "FAIL",
             "Attached GPUs = 7, expected 8")])
        sh = self._one("gpu08", log, S.vrow("gpu08", "NOT HEALTHY",
                                            NVIDIA_SMI_Query="Failed"))
        self.assertEqual(sh.get("gpu08", "Overall"), "NOT HEALTHY")
        self.assertIn("Also incomplete", sh.get("gpu08", "Notes"))


class IncompleteIsVisibleAndCounted(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)
        logs = {"ok01": S.runner_fulllog("ok01", S.DGX_SECTIONS),
                "inc01": only("inc01", "smi"),
                "bad01": S.runner_fulllog("bad01", [
                    ("smi", "x", "FAIL", "Attached GPUs = 7, expected 8")]
                    + list(S.DGX_SECTIONS))}
        rows = [S.full_dgx("ok01"),
                S.vrow("inc01", NVIDIA_SMI_Query="Complete"),
                S.full_dgx("bad01", "NOT HEALTHY",
                           NVIDIA_SMI_Query="Failed")]
        self.root = S.write_root(os.path.join(self.tmp, "r"), logs, rows)
        self.sh = S.run_tracker(["--vitals", self.root], self.tmp)

    def test_sheet_fill_is_distinct(self):
        fills = {h: self.sh.fill(h, "Overall")
                 for h in ("ok01", "inc01", "bad01")}
        self.assertIsNotNone(fills["inc01"])
        self.assertEqual(len(set(fills.values())), 3, fills)

    def test_csv_carries_incomplete(self):
        r = self.sh.csv_row("inc01")
        self.assertEqual(r["Overall"], "INCOMPLETE")
        self.assertTrue(r["Notes"].startswith("INCOMPLETE — "))

    def test_tally_counts_every_row(self):
        tb = self.sh.tb
        order = list(self.sh.rows)
        hosts = {}
        for h in order:
            r = tb.HostResult(h)
            r.overall = self.sh.get(h, "Overall")
            hosts[h] = r
        tally = tb.verdict_tally(order, hosts)
        self.assertEqual(tally["INCOMPLETE"], 1)
        self.assertEqual(sum(tally.values()), len(order))

    def test_banner_names_incomplete(self):
        import io
        import contextlib
        S.install_fake_openpyxl()
        out = io.StringIO()
        old = list(os.sys.argv)
        os.sys.argv = ["tracker_build.py", "--vitals", self.root, "--out",
                       os.path.join(self.tmp, "banner.xlsx")]
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                S._load("tracker_build.py").main()
        finally:
            os.sys.argv = old
        self.assertIn("1 INCOMPLETE", out.getvalue())
        self.assertIn("1 HEALTHY,", out.getvalue())

    def test_only_run_green_card_is_rerendered_with_tracker_verdict(self):
        root = S.write_root(os.path.join(self.tmp, "c"), {
            "gpu09": only("gpu09", "smi")},
            rows=[S.vrow("gpu09", NVIDIA_SMI_Query="Complete")])
        vit = S.load_vitals()
        vit.render_health_card(os.path.join(root, "gpu09", "gpu09-HEALTHY.png"),
                               "gpu09", "2026-11-15T11:00:00Z",
                               [("NVIDIA-SMI Query", "pass")], "HEALTHY",
                               "pass")
        sh = S.run_tracker(["--vitals", root], self.tmp)
        imgs = [p for _a, p in sh.data["Validation Tracker"]["images"]]
        self.assertTrue(any(p.endswith("gpu09-tracker.png") for p in imgs),
                        imgs)
        raw = os.path.join(root, "gpu09", "gpu09-HEALTHY.png")
        self.assertTrue(os.path.isfile(raw))   # raw evidence untouched


if __name__ == "__main__":
    unittest.main()
