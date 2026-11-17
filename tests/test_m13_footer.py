"""M13 — no customer names compiled into the sheet. An adjudicated build's
footer carries a provenance line built from its inputs (root and bundle
basenames) and no name that did not come from those inputs."""

import os
import re
import unittest

from tests import support as S

COMPILED_NAMES = re.compile(r"moonlite|\bCDW\b|CDW's", re.IGNORECASE)


class AdjudicatedFooter(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)
        self.adj = S.write_adjudications(
            os.path.join(self.tmp, "adj.csv"),
            [("gpu01", "NVSM Stress Test", "Warned", "ruled benign")])

    def _vitals(self, name):
        return S.write_vitals_root(os.path.join(self.tmp, name),
                                   [S.full_dgx("gpu01")])

    def _bundles(self, name):
        return S.write_bundle_root(
            os.path.join(self.tmp, name), "gpu09",
            ["01-nvidia-smi exit=0 12s ok", "02-topo exit=0 3s ok"])

    def _footer_text(self, sh):
        self.assertTrue(sh.footer, "adjudicated build has a footer block")
        return "\n".join(sh.footer)

    def test_vitals_only_footer_names_no_customer(self):
        root = self._vitals("wave-a")
        sh = S.run_tracker(["--vitals", root, "--adjudications", self.adj],
                           self.tmp)
        foot = self._footer_text(sh)
        self.assertIsNone(COMPILED_NAMES.search(foot), foot)
        self.assertIn("wave-a", foot)
        # no bundle root given: provenance must not claim bundles
        self.assertNotIn("bundle", foot.lower())

    def test_vitals_and_bundles_provenance_from_inputs(self):
        root = self._vitals("wave-a")
        bundles = self._bundles("harvest-1")
        sh = S.run_tracker(["--vitals", root, "--bundles", bundles,
                            "--adjudications", self.adj], self.tmp)
        foot = self._footer_text(sh)
        self.assertIsNone(COMPILED_NAMES.search(foot), foot)
        self.assertIn("wave-a", foot)
        self.assertIn("harvest-1", foot)

    def test_names_that_come_from_inputs_are_allowed(self):
        root = self._vitals("moonlite-wave1")
        sh = S.run_tracker(["--vitals", root, "--adjudications", self.adj],
                           self.tmp)
        foot = self._footer_text(sh)
        self.assertIn("moonlite-wave1", foot)
        stripped = foot.replace("moonlite-wave1", "")
        self.assertIsNone(COMPILED_NAMES.search(stripped), foot)

    def test_operator_paths_do_not_leak(self):
        root = self._vitals("wave-a")
        sh = S.run_tracker(["--vitals", root + os.sep,
                            "--adjudications", self.adj], self.tmp)
        foot = self._footer_text(sh)
        self.assertNotIn(self.tmp, foot)

    def test_whole_sheet_names_no_customer(self):
        root = self._vitals("wave-a")
        sh = S.run_tracker(["--vitals", root, "--adjudications", self.adj],
                           self.tmp)
        self.assertNotRegex(sh.all_text().lower(), "moonlite")

    def test_no_adjudication_no_footer(self):
        root = self._vitals("wave-a")
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.footer, [])


if __name__ == "__main__":
    unittest.main()
