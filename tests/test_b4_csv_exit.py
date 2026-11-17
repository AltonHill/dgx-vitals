"""B4 / Q10 — a real CSV beside the sheet on every run (same columns, same
verdicts) and honest exit codes: 0 every row healthy and complete; 2 any
row NOT HEALTHY / INCOMPLETE / without evidence; 1 the tool failed or
could not fully read its input. No path exits 0 on unread input."""

import os
import unittest

from tests import support as S


class CsvExport(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)

    def test_csv_matches_the_sheet(self):
        root = S.write_vitals_root(os.path.join(self.tmp, "r"), [
            S.full_dgx("gpu01"),
            S.full_dgx("gpu02", "NOT HEALTHY", NVSM_Dump_Health="Failed"),
            S.vrow("gpu03", NVIDIA_SMI_Query="Complete")])
        fleet = S.write_fleet(os.path.join(self.tmp, "f.csv"),
                              ["gpu01", "gpu02", "gpu03", "gpu04"])
        sh = S.run_tracker(["--hosts", fleet, "--vitals", root], self.tmp)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "tracker.csv")))
        self.assertEqual(list(sh.csv[0].keys()), sh.columns)
        self.assertEqual([r["Node Hostname"] for r in sh.csv],
                         ["gpu01", "gpu02", "gpu03", "gpu04"])
        for r in sh.csv:
            for col in sh.columns:
                if col == "Health Card":
                    continue
                want = sh.get(r["Node Hostname"], col)
                self.assertEqual(r[col], "" if want is None else str(want),
                                 (r["Node Hostname"], col))
        self.assertEqual({r["Overall"] for r in sh.csv},
                         {"HEALTHY", "NOT HEALTHY", "INCOMPLETE", "NO DATA"})

    def test_csv_names_cards_without_operator_paths(self):
        root = S.write_vitals_root(os.path.join(self.tmp, "wave-a"),
                                   [S.full_dgx("gpu05")], cards={"gpu05"})
        sh = S.run_tracker(["--vitals", root], self.tmp)
        ref = sh.csv_row("gpu05")["Health Card"]
        self.assertEqual(ref, "wave-a/gpu05/gpu05-HEALTHY.png")
        self.assertNotIn(self.tmp, "".join(str(v) for r in sh.csv
                                           for v in r.values()))


class ExitCodes(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)

    def _vitals(self, rows, **kw):
        return S.write_vitals_root(os.path.join(self.tmp, "r"), rows, **kw)

    def test_all_healthy_is_0(self):
        sh = S.run_tracker(["--vitals", self._vitals([S.full_dgx("a")])],
                           self.tmp)
        self.assertEqual(sh.rc, 0)

    def test_healthy_with_warnings_is_0(self):
        # an adjudicated pass is a pass (see REPORT-B open question)
        adj = S.write_adjudications(os.path.join(self.tmp, "adj.csv"), [
            ("a", "NVSM Health", "Warned", "ruled benign")])
        sh = S.run_tracker(["--vitals", self._vitals([
            S.full_dgx("a", "NOT HEALTHY", NVSM_Health="Failed")]),
            "--adjudications", adj], self.tmp)
        self.assertEqual(sh.get("a", "Overall"), "HEALTHY WITH WARNINGS")
        self.assertEqual(sh.rc, 0)

    def test_not_healthy_is_2(self):
        sh = S.run_tracker(["--vitals", self._vitals([
            S.full_dgx("a"),
            S.full_dgx("b", "NOT HEALTHY", NCCL_Tests="Failed")])], self.tmp)
        self.assertEqual(sh.rc, 2)

    def test_incomplete_is_2(self):
        sh = S.run_tracker(["--vitals", self._vitals([
            S.vrow("a", NVIDIA_SMI_Query="Complete")])], self.tmp)
        self.assertEqual(sh.get("a", "Overall"), "INCOMPLETE")
        self.assertEqual(sh.rc, 2)

    def test_no_data_row_is_2(self):
        fleet = S.write_fleet(os.path.join(self.tmp, "f.csv"), ["a", "z"])
        sh = S.run_tracker(["--hosts", fleet, "--vitals",
                            self._vitals([S.full_dgx("a")])], self.tmp)
        self.assertEqual(sh.get("z", "Overall"), "NO DATA")
        self.assertEqual(sh.rc, 2)

    def test_skipped_label_row_is_2(self):
        sh = S.run_tracker(["--vitals", self._vitals([
            S.vrow("a", "SKIPPED", notes="FULLLOG exists")])], self.tmp)
        self.assertEqual(sh.rc, 2)

    def test_missing_vitals_root_is_1(self):
        good = self._vitals([S.full_dgx("a")])
        sh = S.run_tracker(["--vitals", good, "--vitals",
                            os.path.join(self.tmp, "nope")], self.tmp)
        self.assertEqual(sh.rc, 1)
        self.assertIn("nope", "\n".join(sh.quarantine))

    def test_dry_only_root_is_1(self):
        root = os.path.join(self.tmp, "dry", "dry-run", "gpu01")
        os.makedirs(root)
        with open(os.path.join(root, "gpu01-FULLLOG.txt"), "w") as f:
            f.write(S.fulllog_text("gpu01", dry=True))
        sh = S.run_tracker(["--vitals", os.path.join(self.tmp, "dry")],
                           self.tmp)
        self.assertEqual(sh.rc, 1)

    def test_corrupt_bundle_is_1_and_does_not_crash(self):
        hd = os.path.join(self.tmp, "b", "gpu02")
        os.makedirs(hd)
        with open(os.path.join(hd, "cdw-validation-gpu02-1.tar.gz"),
                  "wb") as f:
            f.write(b"\x1f\x8b\x08\x00" + b"\x00" * 40)   # truncated gzip
        sh = S.run_tracker(["--vitals", self._vitals([S.full_dgx("a")]),
                            "--bundles", os.path.join(self.tmp, "b")],
                           self.tmp)
        self.assertEqual(sh.rc, 1)
        self.assertTrue(any("gpu02" in q and "unreadable" in q
                            for q in sh.quarantine), sh.quarantine)

    def test_unmatched_adjudication_is_1(self):
        adj = S.write_adjudications(os.path.join(self.tmp, "adj.csv"), [
            ("ghost", "NVSM Health", "Warned", "")])
        sh = S.run_tracker(["--vitals", self._vitals([S.full_dgx("a")]),
                            "--adjudications", adj], self.tmp)
        self.assertEqual(sh.rc, 1)

    def test_no_input_argument_is_1(self):
        sh = S.run_tracker([], self.tmp)
        self.assertEqual(sh.rc, 1)

    def test_missing_openpyxl_is_1(self):
        import sys
        root = self._vitals([S.full_dgx("a")])
        S.install_fake_openpyxl()
        tb = S._load("tracker_build.py")
        saved = sys.modules.pop("openpyxl")
        sys.modules["openpyxl"] = None      # import fails
        try:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = tb.main(["--vitals", root, "--no-banner", "--out",
                              os.path.join(self.tmp, "x.xlsx")])
        finally:
            sys.modules["openpyxl"] = saved
        self.assertEqual(rc, 1)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "x.csv")))

    def test_exit_codes_are_documented(self):
        S.install_fake_openpyxl()
        tb = S._load("tracker_build.py")
        for text in (tb.__doc__, tb.EXIT_HELP):
            for code in ("0", "1", "2"):
                self.assertRegex(text, rf"(?m)^\s+{code}\s")
            self.assertIn("INCOMPLETE", text)


if __name__ == "__main__":
    unittest.main()
