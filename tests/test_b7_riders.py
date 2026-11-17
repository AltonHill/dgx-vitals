"""B7 riders — F14: the tracker's docstring describes function only (no
customer, no individuals, no operator environment). (P2 is pinned in
test_b2_rebuild.RunnerOverallDisagreement, H1 in
test_b5_ki1_suffix.Ki1FooterFromEvidence.)"""

import re
import unittest

from tests import support as S

# "cdw-validation" is the foreign bundle format's own name (REPORT-A2 open
# question 4, still parked); any other CDW mention is a company name.
NAMES = re.compile(r"moonlite|\bCDW\b(?!-validation)|david|keith|alton|\bhill\b|omaha|"
                   r"skulker|the mac\b|engagement \(", re.IGNORECASE)


class DocstringFunctionOnly(unittest.TestCase):

    def setUp(self):
        S.install_fake_openpyxl()
        self.tb = S._load("tracker_build.py")

    def test_module_docstring_names_no_one(self):
        self.assertIsNone(NAMES.search(self.tb.__doc__),
                          NAMES.search(self.tb.__doc__ or ""))

    def test_help_names_no_one(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                self.tb.main(["--help"])
            except SystemExit:
                pass
        self.assertIn("exit codes", out.getvalue())
        self.assertIsNone(NAMES.search(out.getvalue()))

    def test_docstring_still_describes_the_tool(self):
        doc = self.tb.__doc__
        for word in ("--vitals", "--bundles", "--hosts", "--out",
                     "summary.csv", "FULLLOG", "INCOMPLETE",
                     "rebuilt-from-fulllog", "Exit codes"):
            self.assertIn(word, doc)


if __name__ == "__main__":
    unittest.main()
