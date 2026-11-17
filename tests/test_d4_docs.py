"""Brief D Part 4 — docs truth (REVIEW-A F1-F14) and the quickstart.

Every claim the README, the Operator's Guide and the quickstart make
about either tool must be true of the code: no stale step counts, no
phantom flags, documented defaults equal to the parsers', exit codes as
EXIT_HELP states them, the Guide reachable from the README, and no
customer, individual or operator-environment names."""

import contextlib
import io
import os
import re
import unittest

from tests import support as S
from tests.test_b7_riders import NAMES

DOCS = {"README": "README.md", "GUIDE": "docs/OPERATORS-GUIDE.md",
        "QUICKSTART": "QUICKSTART.md"}


def read(rel):
    return S.read_text(os.path.join(S.REPO, rel))


def runner_help():
    _rc, out, _e = S.run_vitals_main(S.load_vitals(), ["--help"])
    return out


def tracker_help():
    S.install_fake_openpyxl()
    tb = S._load("tracker_build.py")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            tb.main(["--help"])
        except SystemExit:
            pass
    return out.getvalue()


class DocsTruth(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.docs = {k: read(v) for k, v in DOCS.items()}
        cls.rhelp, cls.thelp = runner_help(), tracker_help()

    def test_no_stale_claims(self):
        stale = ["touches nothing", "Touches nothing", "--dump-timeout 1800",
                 "all seven", "full seven checks", "step 3/7", "[6/7]",
                 "exits 1 if any host", "Steps 2, 5, or 6",
                 "partially provisioned", "harvest looks in",
                 "deliberately simple", "excluded from % Complete",
                 "fails it if nccl-tests is missing", "a missing\n  install **Fails**"]
        for name, text in self.docs.items():
            for s in stale:
                self.assertNotIn(s, text, f"{name}: {s!r}")
        flow = read("docs/flowchart.svg")
        for s in ("Touches nothing", "runs all seven", "mimirs"):
            self.assertNotIn(s, flow)

    def test_names_no_one(self):
        for name, text in list(self.docs.items()) + [
                ("hosts.example.csv", read("hosts.example.csv")),
                ("flowchart", read("docs/make_flowchart.py"))]:
            self.assertIsNone(NAMES.search(text), (name, NAMES.search(text)))
            for w in ("mimir", "altonhill", "tailnet", "/home/"):
                self.assertNotIn(w, text.lower(), (name, w))

    def test_guide_and_quickstart_reachable_from_readme(self):
        self.assertIn("docs/OPERATORS-GUIDE.md", self.docs["README"])
        self.assertIn("QUICKSTART.md", self.docs["README"])

    def test_every_documented_flag_exists(self):
        both = self.rhelp + self.thelp
        for name, text in self.docs.items():
            for flag in set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", text)):
                if flag in ("--no-prompt",):          # nvsm's own flag
                    continue
                self.assertIn(flag, both, f"{name}: {flag}")

    def test_documented_defaults_match_the_parser(self):
        vit = S.load_vitals()
        for flag, default in (("--dump-timeout", 3600),
                              ("--health-timeout", 1800),
                              ("--stress-timeout", 5400),
                              ("--nccl-timeout", 2700), ("--jobs", 4)):
            self.assertIn(f"(default {default})",
                          " ".join(self.rhelp.split()), flag)
        guide = " ".join(self.docs["GUIDE"].split())
        for d in ("--health-timeout` (default 1800s)",
                  "--stress-timeout` (5400s)", "--dump-timeout` (3600s)",
                  "--nccl-timeout` (2700s)", "(default 4)"):
            self.assertIn(d, guide)
        self.assertIn("`--dump-timeout 3600`", self.docs["README"])

    def test_step_counts_match_the_code(self):
        vit = S.load_vitals()
        self.assertEqual(len(vit.build_steps("dgx", 60, include_nccl=False)),
                         6)
        self.assertEqual(len(vit.build_steps("dgx", 60)), 7)
        self.assertEqual(len(vit.build_steps("hgx", 60)), 4)
        for name in ("README", "GUIDE"):
            self.assertIn("six", self.docs[name])
        self.assertIn("step 1/6", self.docs["README"])

    def test_exit_codes_as_documented(self):
        guide = " ".join(self.docs["GUIDE"].split())
        self.assertIn("2 when any host is NOT HEALTHY, INCOMPLETE or "
                      "UNREACHABLE", guide)
        self.assertIn("130 when you interrupted it", guide)
        rh = " ".join(self.rhelp.split())
        self.assertIn("130 interrupted by the operator", rh)
        th = " ".join(self.thelp.split())
        self.assertIn("2 any row NOT HEALTHY, INCOMPLETE or UNREACHABLE", th)

    def test_tracker_flags_in_the_guide(self):          # F13
        for f in ("--raw-cards", "--no-banner", "--from-fulllog",
                  "--require-nccl", "--adjudications"):
            self.assertIn(f, self.docs["GUIDE"], f)

    def test_quickstart_covers_the_workflow(self):
        q = self.docs["QUICKSTART"]
        for w in ("--check", "--watch", "tracker_build.py", "INCOMPLETE",
                  "UNREACHABLE", "--no-require-nccl", "NCCL not tested",
                  "Ctrl-C"):
            self.assertIn(w, q, w)
        self.assertLess(len(q.splitlines()), 130)     # one page, roughly


if __name__ == "__main__":
    unittest.main()
