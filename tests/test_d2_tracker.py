"""Brief D Part 1 — the tracker learns the 2.0 contract (unit level; the
end-to-end runner->tracker pins are in test_d1_contract).

1  one shared vocabulary, imported from dgx-vitals.py; every OVERALL word
   the runner can print is recognised.
2  Incomplete / UNREACHABLE / stop logs are provisional (non-sticky).
3  UNREACHABLE has its own tally, colour, README line, and its reason
   comes from the FULLLOG's own Reason: line.
4  the run's NCCL requirement is read from the FULLLOG header.
5  a waiver is honoured only as a named INCOMPLETE reason, proven by the
   run's own FULLLOG (never by note text), and never crosses roots.
6  KI-2 is scoped to legacy (pre-2.0) logs; the footer says so, and an
   "NCCL not tested" line lists hosts without an NCCL result.
Version: tracker 2.0, surfaced in --version, --help and the README tab."""

import contextlib
import io
import os
import shutil
import unittest

from tests import support as S

HGX = "hostname,address\nhgx01,a\n"
OPT = "NCCL not required (operator opt-out)"


def load_tb():
    S.install_fake_openpyxl()
    return S._load("tracker_build.py")


class SharedVocabulary(unittest.TestCase):

    def setUp(self):
        self.tb = load_tb()
        self.vit = S.load_vitals()

    def test_vocabulary_comes_from_the_runner(self):
        self.assertEqual(self.tb.HOST_VERDICTS, self.vit.HOST_VERDICTS)
        self.assertEqual(self.tb.TRAILER_STATE, self.vit.SUMMARY_CELL)
        # no second, hand-synced copy of the verdict words in the tracker
        src = S.read_text(os.path.join(S.SRC, "tracker_build.py"))
        self.assertNotIn('RUNNER_VERDICTS = ("HEALTHY", "NOT HEALTHY")', src)
        self.assertNotIn("(PASS|FAIL|SKIP|DRY)", src)

    def test_every_overall_word_is_recognised(self):
        for word in self.vit.HOST_VERDICTS:
            log = S.runner_fulllog("gpu01", [("release", "DGX_NAME=x",
                                              "PASS", "x")], overall=word,
                                   tool="dgx-vitals.py v2.0")
            ev = self.tb.parse_fulllog(log)
            self.assertEqual(ev.overall, word)
            self.assertFalse([p for p in ev.problems
                              if "recognized" in p], (word, ev.problems))
        # the waiver suffix and the tally never leak into the token
        log = S.runner_fulllog("gpu01", [("release", "x", "PASS", "x")],
                               overall="INCOMPLETE",
                               tool="dgx-vitals.py v2.0")
        log = log.replace("OVERALL: INCOMPLETE (1/1 steps passed)",
                          f"OVERALL: INCOMPLETE (3/4 steps passed) — {OPT}")
        ev = self.tb.parse_fulllog(log)
        self.assertEqual(ev.overall, "INCOMPLETE")
        self.assertTrue(ev.waiver_overall)

    def test_incomplete_trailer_and_csv_cell_are_states(self):
        log = S.runner_fulllog("gpu01", [("topo", "Legend:", "INCOMPLETE",
                                          "GPU topology matrix not found "
                                          "or unparseable")],
                               overall="INCOMPLETE", tool="dgx-vitals.py v2.0")
        ev = self.tb.parse_fulllog(log)
        self.assertEqual(ev.states["NVLink Topology"], "Incomplete")
        self.assertEqual(ev.problems, [])
        self.assertEqual(self.tb._csv_states({"NVLink Topology":
                                              "Incomplete"}),
                         {"NVLink Topology": "Incomplete"})

    def test_missing_runner_is_a_tool_error(self):
        tmp = S.tempdir(self)
        shutil.copy(os.path.join(S.SRC, "tracker_build.py"), tmp)
        import subprocess
        import sys
        p = subprocess.run([sys.executable, "-I", "-B",
                            os.path.join(tmp, "tracker_build.py"), "--help"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 1)
        self.assertIn("cannot load dgx-vitals.py", p.stderr)


class Version(unittest.TestCase):

    def setUp(self):
        self.tb = load_tb()

    def run_main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                self.tb.main(argv)
            except SystemExit as e:
                return e.code, out.getvalue()
        return None, out.getvalue()

    def test_version_surfaces(self):
        self.assertEqual(self.tb.VERSION, "2.0")
        rc, out = self.run_main(["--version"])
        self.assertEqual((rc, out.strip()), (0, "tracker_build.py v2.0"))
        _rc, out = self.run_main(["--help"])
        self.assertIn("tracker_build.py v2.0", out)
        self.assertIn("UNREACHABLE", out)


class Provisional(unittest.TestCase):
    """Built from synthetic 2.0 roots (the runner's exact shapes)."""

    def setUp(self):
        self.tb = load_tb()
        self.tmp = S.tempdir(self)
        self.vit = S.load_vitals()

    def run_root(self, argv, script, hosts=HGX, name=None):
        out = os.path.join(self.tmp, name or f"r{len(os.listdir(self.tmp))}")
        S.drive_main(self, S.load_vitals(), list(argv) + ["--out", out],
                     {"a": S.ScriptedRunner(script)}, hosts)
        return out

    def test_stop_log_carries_no_conformance_reason(self):
        dead = S.ScriptedRunner([("DGXV-ID-BEGIN", 255, "ssh: no route",
                                  1.0)], default=(255, "", 1.0))
        out = os.path.join(self.tmp, "dead")
        S.drive_main(self, self.vit, ["--out", out], {"a": dead},
                     "hostname,address\ngpu01,a\n")
        rows = self.tb.read_vitals(out)
        r = rows["gpu01"]
        self.assertEqual(r.incomplete, [])
        self.assertEqual(r.notes, [])
        self.assertEqual(r.stop["kind"], "unreachable")
        self.assertIn("ssh: no route", r.stop["reason"])
        self.tb.finalize(r)
        self.assertEqual(r.overall, "UNREACHABLE")

    def test_waiver_never_crosses_roots(self):
        none = S.corpus_block(*S.W09, "sh -c")
        a = self.run_root(["--profile", "hgx", "--no-require-nccl"],
                          S.hgx_host_script(nccl=none))
        b = self.run_root(["--profile", "hgx"], S.hgx_host_script(nccl=none))
        sh = S.run_tracker(["--vitals", a, "--vitals", b], self.tmp)
        notes = sh.get("hgx01", "Notes")
        self.assertEqual(sh.get("hgx01", "Overall"), "INCOMPLETE")
        self.assertIn("NCCL Tests skipped — required by the hgx profile",
                      notes)
        self.assertNotIn("waived", notes)
        self.assertNotIn(OPT, notes)    # the old root's waiver note left too
        # a later real NCCL result replaces the waived skip outright
        c = self.run_root(["--profile", "hgx", "--only", "nccl"],
                          S.hgx_host_script())
        sh = S.run_tracker(["--vitals", a, "--vitals", c], self.tmp)
        self.assertEqual(sh.get("hgx01", "Overall"), "HEALTHY")
        self.assertNotIn("waived", sh.get("hgx01", "Notes"))

    def test_waiver_is_proven_by_the_log_not_by_note_text(self):
        none = S.corpus_block(*S.W09, "sh -c")
        a = self.run_root(["--profile", "hgx"], S.hgx_host_script(nccl=none))
        # forge only the summary note: the run's own log has no waiver
        p = os.path.join(a, "summary.csv")
        text = S.read_text(p).replace("NCCL Tests: not installed",
                                      f"{OPT}; NCCL Tests: not installed")
        with open(p, "w") as f:
            f.write(text)
        sh = S.run_tracker(["--vitals", a], self.tmp)
        self.assertNotIn("waived", sh.get("hgx01", "Notes"))
        self.assertIn("required by the hgx profile", sh.get("hgx01", "Notes"))

    def test_banner_tallies_unreachable(self):
        r = self.tb.HostResult("gpu01")
        r.source, r.overall = "dgx-vitals", "UNREACHABLE"
        self.assertEqual(self.tb.verdict_tally(["gpu01"], {"gpu01": r})
                         ["UNREACHABLE"], 1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.tb.print_banner(["gpu01"], {"gpu01": r})
        self.assertIn("1 UNREACHABLE", out.getvalue())
        self.assertNotIn("SKIPPED", out.getvalue())

    def test_vitals_takes_several_roots_per_flag(self):
        a = os.path.join(self.tmp, "a")
        b = os.path.join(self.tmp, "b")
        S.write_vitals_root(a, [S.full_dgx("gpu01")])
        S.write_vitals_root(b, [S.full_dgx("gpu02")])
        sh = S.run_tracker(["--vitals", a, b], self.tmp)
        self.assertEqual(sorted(sh.rows), ["gpu01", "gpu02"])


class Ki2LegacyOnly(unittest.TestCase):

    def setUp(self):
        self.tb = load_tb()
        self.block = S.corpus_block("wave1-x10-ki2", "fx-x10-prod-hv-01",
                                    "sh -c")

    def row(self, tool):
        log = S.runner_fulllog(
            "gpu01", [("nccl", self.block, "FAIL",
                       "all_reduce: exit 134; not installed: all_gather, "
                       "reduce_scatter (nccl-tests partially provisioned)",
                       1)], tool=tool)
        ev = self.tb.parse_fulllog(log)
        r = self.tb.HostResult("gpu01")
        r.nccl, r.nccl_ev = "Failed", ev.nccl_ev
        return r

    def test_ki2_applies_to_a_legacy_log(self):
        self.assertTrue(self.tb.ki2_applies(self.row("dgx-vitals.py v1.9")))

    def test_ki2_never_applies_to_a_2_0_log(self):
        self.assertFalse(self.tb.ki2_applies(self.row("dgx-vitals.py v2.0")))

    def test_footers_say_their_scope(self):
        r = self.row("dgx-vitals.py v1.9")
        text = self.tb.ki2_footer([("gpu01", r.nccl_ev)])
        self.assertIn("legacy runner logs (dgx-vitals before v2.0)", text)
        self.assertNotIn("partially provisioned", text)
        r.source, r.profile, r.nccl = "dgx-vitals", "hgx", "Skipped"
        groups = self.tb.nccl_not_tested(["gpu01"], {"gpu01": r})
        self.assertEqual(groups, [("never launched — nccl-tests not "
                                   "installed", ["gpu01"])])
        line = self.tb.nccl_footer(groups)
        self.assertTrue(line.startswith("NCCL not tested (1 host(s))"))


if __name__ == "__main__":
    unittest.main()
