"""B5 / KI-1 + H1 — an NVSM check is Unhealthy by the status word that
ends its check line (after the dot leader), not by its name's prefix; the
KI-1 waiver applies only when the DIMM part-number check is the sole
Unhealthy check, the step exited 0 and the log's own unhealthy count
agrees; and the Known Issues footer states only facts drawn from the logs.

Fixture health logs are the real w09 bundle log (corpus: ferry tarball
moonlite-validation-bundle.tar.gz → cdw-validation-nesd-oma01c-w09-prod-
hv-01-20261111-174304/04-nvsm-health.log; fixture bundle-w09), with one
check's status word flipped Healthy -> Unhealthy and the summary count
moved to match, where a test says so."""

import os
import re
import shutil
import unittest

from tests import support as S

BUNDLE = "cdw-validation-fx-w09-prod-hv-01-20261111-174304"
HOST = "fx-w09-prod-hv-01"

# Check names observed Healthy in every corpus log, outside the old
# Verify/Check prefix filter (REVIEW-A Q3).
OUT_OF_PATTERN = ["Status of volumes",
                  "Presence of NVSwitch controllers on PCI bus",
                  "Number of logical CPU cores[256]",
                  "Quick health check of GPU using DCGM"]


def real_health_log():
    return S.read_fixture("bundle-w09", HOST, BUNDLE, "04-nvsm-health.log")


def flip(log, name, count=True):
    """Mark check `name` Unhealthy (and bump the unhealthy count)."""
    pat = re.compile(r"(?m)^(" + re.escape(name) + r"\.{3,}\s*)Healthy$")
    out, n = pat.subn(r"\1Unhealthy", log)
    assert n == 1, name
    if count:
        m = re.search(r"(?m)^(\d+) out of (\d+) checks are unhealthy$", out)
        k, tot = int(m.group(1)), m.group(2)
        out = out.replace(m.group(0),
                          f"{k + 1} out of {tot} checks are unhealthy")
    return out


class StructuralParse(unittest.TestCase):

    def setUp(self):
        S.install_fake_openpyxl()
        self.tb = S._load("tracker_build.py")

    def test_real_log_has_exactly_the_dimm_check_unhealthy(self):
        ev = self.tb.health_evidence(real_health_log(), 0)
        self.assertEqual(ev["names"], ["Verify DIMM part number"])
        self.assertEqual(ev["summary_unhealthy"], 1)
        self.assertEqual(ev["summary_total"], 260)
        self.assertGreater(ev["checks"], 200)

    def test_rollup_and_detail_lines_are_not_checks(self):
        names = [n for n, _s in self.tb.parse_health_checks(real_health_log())]
        self.assertNotIn("Overall system status is", " ".join(names))
        for n in names:
            self.assertNotRegex(n, r"out of \d+ checks")

    def test_out_of_pattern_names_classify_by_suffix(self):
        for name in OUT_OF_PATTERN:
            ev = self.tb.health_evidence(flip(real_health_log(), name), 0)
            self.assertIn(name, ev["names"], name)
            self.assertEqual(len(ev["names"]), 2)

    def test_out_of_pattern_names_healthy_as_observed(self):
        checks = dict(self.tb.parse_health_checks(real_health_log()))
        for name in OUT_OF_PATTERN:
            self.assertEqual(checks[name], "Healthy", name)

    def test_corpus_fulllog_unhealthy_names(self):
        # corpus wave1 w08 (fixture wave1-w08-already-running): DIMM plus
        # a PSU consistency check
        text = S.read_fixture("wave1-w08-already-running",
                              "fx-w08-prod-hv-01",
                              "fx-w08-prod-hv-01-FULLLOG.txt")
        self.assertEqual(self.tb.fulllog_health_names(text),
                         ["Verify DIMM part number",
                          "Check PSU Info (Vendor, Model) for Consistency"])


class Ki1Waiver(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)

    def _bundle(self, health_log, exit04=0):
        root = os.path.join(self.tmp, "b")
        shutil.rmtree(root, ignore_errors=True)
        bdir = os.path.join(root, HOST, BUNDLE)
        shutil.copytree(os.path.join(S.FIXTURES, "bundle-w09", HOST, BUNDLE),
                        bdir)
        with open(os.path.join(bdir, "04-nvsm-health.log"), "w") as f:
            f.write(health_log)
        if exit04:
            p = os.path.join(bdir, "summary.txt")
            with open(p) as f:
                s = f.read().replace("04-nvsm-health           exit=0",
                                     f"04-nvsm-health           exit={exit04}")
            with open(p, "w") as f:
                f.write(s)
        return S.run_tracker(["--bundles", root], self.tmp)

    def test_dimm_only_is_waived(self):
        sh = self._bundle(real_health_log())
        self.assertEqual(sh.get(HOST, "NVSM Health"), "Warned")
        self.assertEqual(sh.get(HOST, "Overall"), "HEALTHY WITH WARNINGS")

    def test_dimm_plus_out_of_pattern_unhealthy_stays_failed(self):
        # pre-B: the extra check was dropped by the Verify/Check filter
        # and the host was waived to HEALTHY WITH WARNINGS.
        for name in OUT_OF_PATTERN:
            sh = self._bundle(flip(real_health_log(), name))
            self.assertEqual(sh.get(HOST, "NVSM Health"), "Failed", name)
            self.assertEqual(sh.get(HOST, "Overall"), "NOT HEALTHY", name)
            self.assertNotIn("KI-1", sh.get(HOST, "Notes"), name)

    def test_count_disagreeing_with_lines_is_not_waived(self):
        # the log says 2 unhealthy but only the DIMM line is structured
        log = real_health_log().replace("1 out of 260 checks are unhealthy",
                                        "2 out of 260 checks are unhealthy")
        sh = self._bundle(log)
        self.assertEqual(sh.get(HOST, "NVSM Health"), "Failed")

    def test_nonzero_exit_is_not_waived(self):
        # REVIEW-A H1: a show health that failed on exit/timeout after
        # printing the DIMM line is not the known false positive
        sh = self._bundle(real_health_log(), exit04=124)
        self.assertEqual(sh.get(HOST, "NVSM Health"), "Failed")

    def test_fulllog_path_same_rule(self):
        # corpus wave1 w08: DIMM + PSU check -> not waived (both prefixes
        # are "Verify"/"Check", so this pins the FULLLOG path, not KI-1's
        # new strictness)
        root = S.corpus_root("wave1-w08-already-running",
                             os.path.join(self.tmp, "w"))
        sh = S.run_tracker(["--vitals", root], self.tmp)
        self.assertEqual(sh.get("fx-w08-prod-hv-01", "NVSM Health"), "Failed")


class Ki1FooterFromEvidence(unittest.TestCase):
    """H1: no NVSM version or DIMM part number compiled into the text."""

    def setUp(self):
        self.tmp = S.tempdir(self)

    def _footer(self, health_log):
        root = os.path.join(self.tmp, "b")
        bdir = os.path.join(root, HOST, BUNDLE)
        shutil.copytree(os.path.join(S.FIXTURES, "bundle-w09", HOST, BUNDLE),
                        bdir)
        with open(os.path.join(bdir, "04-nvsm-health.log"), "w") as f:
            f.write(health_log)
        sh = S.run_tracker(["--bundles", root], self.tmp)
        return "\n".join(sh.footer)

    def test_footer_facts_come_from_the_log(self):
        foot = self._footer(real_health_log())
        self.assertIn("25.09.07", foot)
        self.assertIn("MTC40F2046S1RC64BH1", foot)

    def test_other_site_facts_are_not_the_compiled_ones(self):
        log = (real_health_log()
               .replace("25.09.07", "26.01.02")
               .replace("MTC40F2046S1RC64BH1 2SKK", "ZZ-OTHER-PART")
               .replace("MTC40F2046S1RC64BD2*", "ZZ-LISTED*"))
        foot = self._footer(log)
        self.assertIn("26.01.02", foot)
        self.assertIn("ZZ-OTHER-PART", foot)
        for compiled in ("25.09.07", "MTC40F2046S1RC64BH1",
                         "MTC40F2046S1RC64BD2", "Micron",
                         "every other memory check passes"):
            self.assertNotIn(compiled, foot)

    def test_footer_without_mismatch_detail_claims_none(self):
        log = "\n".join(ln for ln in real_health_log().splitlines()
                        if "Mismatch in DIMM" not in ln)
        foot = self._footer(log)
        self.assertIn("no per-slot mismatch detail", foot)
        self.assertNotIn("uniform", foot)


if __name__ == "__main__":
    unittest.main()
