"""B6 / Q9 — foreign-bundle conformance gate. Every row names its
producer; partial or non-conforming evidence lands INCOMPLETE; input that
cannot be identified or read is QUARANTINED (sheet block + CSV rows),
never blank, never guessed, never toward healthy."""

import os
import re
import shutil
import tarfile
import unittest

from tests import support as S

BUNDLE = "cdw-validation-fx-w09-prod-hv-01-20261111-174304"
HOST = "fx-w09-prod-hv-01"


def copy_bundle(dest_root, host=HOST, bundle=BUNDLE):
    bdir = os.path.join(dest_root, host, bundle)
    shutil.copytree(os.path.join(S.FIXTURES, "bundle-w09", HOST, BUNDLE),
                    bdir)
    return bdir


def edit(path, fn):
    with open(path) as f:
        s = f.read()
    with open(path, "w") as f:
        f.write(fn(s))


class Provenance(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)

    def test_every_row_names_its_producer(self):
        vit = S.write_vitals_root(os.path.join(self.tmp, "v"),
                                  [S.full_dgx("gpu01")])
        b = os.path.join(self.tmp, "b")
        copy_bundle(b)
        fleet = S.write_fleet(os.path.join(self.tmp, "f.csv"),
                              ["gpu01", HOST, "gpu99"])
        sh = S.run_tracker(["--hosts", fleet, "--vitals", vit,
                            "--bundles", b], self.tmp)
        self.assertEqual(sh.get("gpu01", "Provenance"),
                         "dgx-vitals 1.9 (summary.csv)")
        self.assertEqual(sh.get(HOST, "Provenance"),
                         "foreign: cdw-validation")
        self.assertEqual(sh.get("gpu99", "Provenance"), "—")   # NO DATA
        self.assertEqual(sh.csv_row(HOST)["Provenance"],
                         "foreign: cdw-validation")

    def test_foreign_tool_in_fulllog_is_stamped_foreign(self):
        log = S.runner_fulllog("gpu02", S.DGX_SECTIONS,
                               tool="other-harness v2.0")
        root = S.write_root(os.path.join(self.tmp, "r"), {"gpu02": log},
                            rows=[S.full_dgx("gpu02")])
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get("gpu02", "Provenance"),
                         "foreign: other-harness 2.0 (summary.csv)")

    def test_fulllog_naming_no_tool_is_unknown_and_incomplete(self):
        log = S.runner_fulllog("gpu03", S.DGX_SECTIONS, tool=None)
        root = S.write_root(os.path.join(self.tmp, "r"), {"gpu03": log},
                            rows=[S.full_dgx("gpu03")])
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertTrue(sh.get("gpu03", "Provenance").startswith("unknown"))
        self.assertEqual(sh.get("gpu03", "Overall"), "INCOMPLETE")


class CdwConformance(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)
        self.root = os.path.join(self.tmp, "b")

    def test_real_bundle_conforms(self):
        # corpus: ferry tarball -> cdw-validation-nesd-oma01c-w09-prod-hv-01
        # -20261111-174304 (fixture bundle-w09). All six commands exit 0;
        # the health log's only unhealthy check is the DIMM part number.
        copy_bundle(self.root)
        sh = S.run_tracker(["--bundles", self.root], self.tmp)
        self.assertEqual(sh.get(HOST, "Overall"), "HEALTHY WITH WARNINGS")
        self.assertEqual(sh.get(HOST, "% Complete"), 100)
        self.assertEqual(sh.rc, 0)

    def test_real_bundle_as_tarball(self):
        bdir = copy_bundle(os.path.join(self.tmp, "src"))
        hd = os.path.join(self.root, HOST)
        os.makedirs(hd)
        with tarfile.open(os.path.join(hd, BUNDLE + ".tar.gz"), "w:gz") as tf:
            tf.add(bdir, arcname=BUNDLE)
        sh = S.run_tracker(["--bundles", self.root], self.tmp)
        self.assertEqual(sh.get(HOST, "Overall"), "HEALTHY WITH WARNINGS")
        self.assertEqual(sh.get(HOST, "Provenance"), "foreign: cdw-validation")

    def test_truncated_bundle_is_incomplete(self):
        # derived from the real bundle: summary.txt cut after the 04 line
        # (no 05/06 lines, no closing 'Failed commands' count)
        bdir = copy_bundle(self.root)
        edit(os.path.join(bdir, "summary.txt"),
             lambda s: s[:s.index("05-nvsm-stress-test")])
        sh = S.run_tracker(["--bundles", self.root], self.tmp)
        self.assertEqual(sh.get(HOST, "Overall"), "INCOMPLETE")
        notes = sh.get(HOST, "Notes")
        self.assertIn("no line for step(s) 05, 06", notes)
        self.assertIn("no closing 'Failed commands' count", notes)
        self.assertEqual(sh.rc, 2)

    def test_missing_health_log_is_incomplete_not_blank_pass(self):
        bdir = copy_bundle(self.root)
        os.remove(os.path.join(bdir, "04-nvsm-health.log"))
        sh = S.run_tracker(["--bundles", self.root], self.tmp)
        self.assertEqual(sh.get(HOST, "NVSM Health"), "")
        self.assertEqual(sh.get(HOST, "Overall"), "INCOMPLETE")
        self.assertIn("health log missing", sh.get(HOST, "Notes"))

    def test_health_log_cut_short_is_incomplete(self):
        bdir = copy_bundle(self.root)
        p = os.path.join(bdir, "04-nvsm-health.log")
        # all checks Healthy so far, log cut before its summary counts:
        # nothing says healthy, nothing says failed -> cannot be judged
        edit(p, lambda s: re.sub(r"(?m)^(Verify DIMM part number\.+ )"
                                 r"Unhealthy$", r"\1Healthy", s)
             [:s.index("Health Summary")])
        sh = S.run_tracker(["--bundles", self.root], self.tmp)
        self.assertEqual(sh.get(HOST, "NVSM Health"), "")
        self.assertEqual(sh.get(HOST, "Overall"), "INCOMPLETE")
        self.assertIn("no 'N out of M checks' summary", sh.get(HOST, "Notes"))

    def test_health_log_cut_short_with_unhealthy_line_is_failed(self):
        # a recorded Unhealthy check stands even in a log cut short
        bdir = copy_bundle(self.root)
        edit(os.path.join(bdir, "04-nvsm-health.log"),
             lambda s: s[:s.index("Health Summary")])
        sh = S.run_tracker(["--bundles", self.root], self.tmp)
        self.assertEqual(sh.get(HOST, "NVSM Health"), "Failed")

    def test_host_named_inside_bundle_must_match_its_folder(self):
        copy_bundle(self.root, host="fx-w09-prod-hv-02")
        sh = S.run_tracker(["--bundles", self.root], self.tmp)
        self.assertEqual(sh.get("fx-w09-prod-hv-02", "Overall"), "INCOMPLETE")
        self.assertIn("names host 'fx-w09-prod-hv-01'",
                      sh.get("fx-w09-prod-hv-02", "Notes"))


class Quarantine(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)
        self.root = os.path.join(self.tmp, "b")

    def test_unidentifiable_folder_is_quarantined_not_guessed(self):
        hd = os.path.join(self.root, "gpu05")
        os.makedirs(hd)
        with open(os.path.join(hd, "results.json"), "w") as f:
            f.write('{"status": "PASS"}')
        fleet = S.write_fleet(os.path.join(self.tmp, "f.csv"), ["gpu05"])
        sh = S.run_tracker(["--hosts", fleet, "--bundles", self.root],
                           self.tmp)
        self.assertEqual(sh.get("gpu05", "Overall"), "NO DATA")
        self.assertIn("Quarantined input", sh.get("gpu05", "Notes"))
        self.assertTrue(any("gpu05" in q and "no recognizable bundle" in q
                            for q in sh.quarantine), sh.quarantine)
        qrows = [r for r in sh.csv if r["Overall"] == "QUARANTINED"]
        self.assertEqual(len(qrows), 1)
        self.assertEqual(qrows[0]["Provenance"], "unknown")
        self.assertEqual(sh.rc, 1)

    def test_named_bundle_without_step_lines_is_unidentified(self):
        bdir = os.path.join(self.root, "gpu06", "cdw-validation-gpu06-1")
        os.makedirs(bdir)
        with open(os.path.join(bdir, "summary.txt"), "w") as f:
            f.write("all good\n")
        sh = S.run_tracker(["--bundles", self.root], self.tmp)
        self.assertNotIn("gpu06", sh.rows)
        self.assertTrue(any("producer not identifiable" in q
                            for q in sh.quarantine))
        self.assertEqual(sh.rc, 1)

    def test_legacy_dry_summary_row_is_quarantined(self):
        root = S.write_vitals_root(os.path.join(self.tmp, "v"), [
            S.full_dgx("gpu07"), S.vrow("gpu08", "DRY RUN",
                                        NCCL_Tests="Dry Run")])
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertNotIn("gpu08", sh.rows)
        self.assertTrue(any("dry-run row" in q for q in sh.quarantine))
        self.assertEqual(sh.rc, 1)

    def test_mismatched_fulllog_name_is_quarantined(self):
        root = S.write_vitals_root(os.path.join(self.tmp, "v"),
                                   [S.full_dgx("gpu09")])
        shutil.copyfile(os.path.join(root, "gpu09", "gpu09-FULLLOG.txt"),
                        os.path.join(root, "gpu09", "gpu10-FULLLOG.txt"))
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertTrue(any("does not match its host folder" in q
                            for q in sh.quarantine))
        self.assertEqual(sh.get("gpu09", "Overall"), "HEALTHY")
        self.assertEqual(sh.rc, 1)


if __name__ == "__main__":
    unittest.main()
