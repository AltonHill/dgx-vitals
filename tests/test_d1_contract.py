"""Brief D Part 2 — the runner -> tracker contract test.

Every case runs the REAL runner (dgx-vitals main(), scripted hosts, nothing
executed) into an output root, then feeds that root to the REAL tracker
(tracker_build main(), openpyxl stand-in) and pins what both tools say.
This is the test whose absence let the two vocabularies drift
(ANALYSIS-TRACKER-FOLLOWUPS item 3).

Pinned (Brief D Part 2, and Loki's rulings of 2026-11-16):
  * a required NCCL non-launch is INCOMPLETE on both tools — hgx default
    and dgx --require-nccl (the Amendment 1 "Surprise 2" hazard: the
    tracker must learn the requirement from the FULLLOG header);
  * hgx opt-out -> INCOMPLETE on both, the waiver named on the sheet;
  * dgx opt-out -> HEALTHY on both, no waiver invented;
  * NCCL that ran and failed -> NOT HEALTHY on both;
  * UNREACHABLE -> UNREACHABLE on both, the logged reason on the sheet;
  * an identity-mismatch stop does NOT clear on an unrelated later run and
    DOES clear on a later identity-confirmed run;
  * a KI-1 host -> HEALTHY WITH WARNINGS (regression guard);
  * no runner outcome yields "not a recognized verdict" or "truncated".

Exit codes follow both tools' EXIT_HELP: 0 healthy, 2 not healthy /
incomplete / unreachable, 1 tool error."""

import os
import re
import unittest

from tests import support as S

HGX = "hostname,address\nhgx01,a\n"
DGX = "hostname,address\ngpu01,a\n"
OPT = "NCCL not required (operator opt-out)"
NO_LAUNCH = lambda: S.corpus_block(*S.W09, "sh -c")    # real w09 non-launch
SERIAL_READ = (lambda c: c == "sudo cat /sys/class/dmi/id/product_serial")
BAD_WORDS = ("not a recognized verdict", "truncated",
             "the runner skipped the host")


def nccl_ran_and_failed():
    return S.nccl_ok().replace("# Out of bounds values : 0 OK",
                               "# Out of bounds values : 5 ERROR", 1)


def dead_runner():
    return S.ScriptedRunner([
        ("DGXV-ID-BEGIN", 255,
         "\n[stderr]\nssh: connect to host a port 22: Connection timed out",
         15.0)], default=(255, "", 15.0))


class Contract(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)
        self.n = 0

    # -- the two real tools -------------------------------------------------
    def runner(self, argv, runner, hosts, out=None):
        """Real dgx-vitals main() into a fresh (or given) root."""
        self.n += 1
        out = out or os.path.join(self.tmp, f"root{self.n}")
        vit = S.load_vitals()
        rc, o, e, _root = S.drive_main(self, vit, list(argv) + ["--out", out],
                                       {"a": runner}, hosts)
        return rc, out, o + e

    def tracker(self, *roots, extra=()):
        args = []
        for r in roots:
            args += ["--vitals", r]
        out = os.path.join(self.tmp, f"sheet{self.n}")
        os.makedirs(out, exist_ok=True)
        self.n += 1
        return S.run_tracker(args + list(extra), out)

    def overall_line(self, root, host):
        text = S.read_text(os.path.join(root, host, f"{host}-FULLLOG.txt"))
        return next(ln for ln in text.splitlines()
                    if ln.startswith("OVERALL:"))

    def clean(self, sh, host):
        notes = sh.get(host, "Notes")
        for w in BAD_WORDS:
            self.assertNotIn(w, notes)
        return notes

    # -- required non-launch -> INCOMPLETE on both ---------------------------
    def test_required_non_launch_hgx_is_incomplete_on_both(self):
        rc, root, _ = self.runner(["--profile", "hgx"],
                                  S.ScriptedRunner(S.hgx_host_script(
                                      nccl=NO_LAUNCH())), HGX)
        self.assertEqual(rc, 2)
        self.assertEqual(self.overall_line(root, "hgx01"),
                         "OVERALL: INCOMPLETE (3/4 steps passed)")
        sh = self.tracker(root)
        self.assertEqual(sh.get("hgx01", "Overall"), "INCOMPLETE")
        self.assertEqual(sh.rc, 2)
        notes = self.clean(sh, "hgx01")
        self.assertIn("NCCL Tests skipped — required by the hgx profile",
                      notes)

    def test_required_non_launch_dgx_require_nccl_is_incomplete_on_both(self):
        # Amendment 1 "Surprise 2": the dgx profile does not expect NCCL,
        # so only the FULLLOG header ("NCCL required (--require-nccl)")
        # tells the tracker this row must not read HEALTHY.
        script = S.dgx_host_script(extra=[("_perf", 0, NO_LAUNCH(), 30.0)])
        rc, root, _ = self.runner(["--require-nccl"],
                                  S.ScriptedRunner(script), DGX)
        self.assertEqual(rc, 2)
        self.assertEqual(self.overall_line(root, "gpu01"),
                         "OVERALL: INCOMPLETE (6/7 steps passed)")
        sh = self.tracker(root)
        self.assertEqual(sh.get("gpu01", "Overall"), "INCOMPLETE")
        self.assertEqual(sh.rc, 2)
        notes = self.clean(sh, "gpu01")
        self.assertIn("NCCL Tests skipped — required by the run "
                      "(--require-nccl)", notes)

    def test_dgx_required_nccl_stays_required_across_a_later_root(self):
        # a later plain re-run (stress only, no --require-nccl) does not
        # lift the requirement the first run recorded
        script = S.dgx_host_script(extra=[("_perf", 0, NO_LAUNCH(), 30.0)])
        _rc, a, _ = self.runner(["--require-nccl"], S.ScriptedRunner(script),
                                DGX)
        _rc, b, _ = self.runner(["--only", "stress"],
                                S.ScriptedRunner(S.dgx_host_script()), DGX)
        sh = self.tracker(a, b)
        self.assertEqual(sh.get("gpu01", "Overall"), "INCOMPLETE")

    # -- waivers --------------------------------------------------------------
    def test_hgx_opt_out_is_incomplete_with_the_waiver_named(self):
        rc, root, _ = self.runner(["--profile", "hgx", "--no-require-nccl"],
                                  S.ScriptedRunner(S.hgx_host_script(
                                      nccl=NO_LAUNCH())), HGX)
        self.assertEqual(rc, 2)
        self.assertEqual(self.overall_line(root, "hgx01"),
                         f"OVERALL: INCOMPLETE (3/4 steps passed) — {OPT}")
        sh = self.tracker(root)
        self.assertEqual(sh.get("hgx01", "Overall"), "INCOMPLETE")
        self.assertEqual(sh.rc, 2)
        notes = self.clean(sh, "hgx01")
        self.assertIn("waived by the operator's opt-out (--no-require-nccl)",
                      notes)
        # the footer lists the host as not tested, with the waiver
        line = [f for f in sh.footer if f.startswith("NCCL not tested")]
        self.assertTrue(line and "opt-out" in line[0] and "hgx01" in line[0],
                        sh.footer)

    def test_tracker_require_nccl_does_not_let_the_waiver_soften(self):
        _rc, root, _ = self.runner(["--profile", "hgx", "--no-require-nccl"],
                                   S.ScriptedRunner(S.hgx_host_script(
                                       nccl=NO_LAUNCH())), HGX)
        sh = self.tracker(root, extra=["--require-nccl"])
        self.assertEqual(sh.get("hgx01", "Overall"), "INCOMPLETE")
        self.assertIn("which the opt-out does not override",
                      sh.get("hgx01", "Notes"))

    def test_dgx_opt_out_is_healthy_and_no_waiver_is_invented(self):
        rc, root, _ = self.runner(["--no-require-nccl"],
                                  S.ScriptedRunner(S.dgx_host_script()), DGX)
        self.assertEqual(rc, 0)
        self.assertEqual(self.overall_line(root, "gpu01"),
                         f"OVERALL: HEALTHY (6/6 steps passed) — {OPT}")
        sh = self.tracker(root)
        self.assertEqual(sh.get("gpu01", "Overall"), "HEALTHY")
        self.assertEqual(sh.rc, 0)
        notes = self.clean(sh, "gpu01")
        self.assertNotIn("waived", notes)
        self.assertFalse(any(f.startswith("NCCL not tested")
                             for f in sh.footer), sh.footer)

    # -- fault evidence -------------------------------------------------------
    def test_nccl_ran_and_failed_is_not_healthy_on_both(self):
        for argv in (["--profile", "hgx"],
                     ["--profile", "hgx", "--no-require-nccl"]):
            rc, root, _ = self.runner(argv, S.ScriptedRunner(
                S.hgx_host_script(nccl=nccl_ran_and_failed())), HGX)
            self.assertEqual(rc, 2, argv)
            self.assertTrue(self.overall_line(root, "hgx01").startswith(
                "OVERALL: NOT HEALTHY"), argv)
            sh = self.tracker(root)
            self.assertEqual(sh.get("hgx01", "Overall"), "NOT HEALTHY", argv)
            self.assertEqual(sh.get("hgx01", "NCCL Tests"), "Failed")
            self.assertEqual(sh.rc, 2)
            self.clean(sh, "hgx01")

    # -- UNREACHABLE ----------------------------------------------------------
    def test_unreachable_is_unreachable_with_its_logged_reason(self):
        rc, root, _ = self.runner([], dead_runner(), DGX)
        self.assertEqual(rc, 2)
        self.assertEqual(self.overall_line(root, "gpu01"),
                         "OVERALL: UNREACHABLE (no step ran)")
        self.assertTrue(os.path.isfile(os.path.join(
            root, "gpu01", "gpu01-UNREACHABLE.png")))
        sh = self.tracker(root)
        self.assertEqual(sh.get("gpu01", "Overall"), "UNREACHABLE")
        self.assertEqual(sh.rc, 2)
        notes = self.clean(sh, "gpu01")
        self.assertTrue(notes.startswith("UNREACHABLE — step-zero probe "
                                         "failed — probe exit 255"), notes)
        self.assertIn("Connection timed out", notes)
        self.assertNotIn("Rebuilt from FULLLOG", notes)
        self.assertEqual(sh.fill("gpu01", "Overall"), "FCE4D6")
        readme = "\n".join(str(r[0]) for r in sh.data["README"]["rows"]
                           if r and r[0])
        self.assertIn("UNREACHABLE — the runner could not reach the host",
                      readme)

    def test_unreachable_is_replaced_by_a_later_real_run(self):
        _rc, a, _ = self.runner([], dead_runner(), DGX)
        _rc, b, _ = self.runner([], S.ScriptedRunner(S.dgx_host_script()),
                                DGX)
        sh = self.tracker(a, b)
        self.assertEqual(sh.get("gpu01", "Overall"), "HEALTHY")
        self.assertEqual(sh.rc, 0)
        self.assertNotIn("probe", sh.get("gpu01", "Notes"))

    def test_a_later_unreachable_attempt_never_overrides_results(self):
        _rc, a, _ = self.runner([], S.ScriptedRunner(S.dgx_host_script()),
                                DGX)
        _rc, b, _ = self.runner([], dead_runner(), DGX)
        sh = self.tracker(a, b)
        self.assertEqual(sh.get("gpu01", "Overall"), "HEALTHY")
        self.assertNotIn("probe", sh.get("gpu01", "Notes"))

    def test_identity_unconfirmed_is_incomplete_and_provisional(self):
        script = S.dgx_host_script(serial="",
                                   extra=[(SERIAL_READ, 1, "", 0.1)])
        rc, a, _ = self.runner([], S.ScriptedRunner(script), DGX)
        self.assertEqual(rc, 2)
        sh = self.tracker(a)
        self.assertEqual(sh.get("gpu01", "Overall"), "INCOMPLETE")
        self.assertIn("identity not confirmed", self.clean(sh, "gpu01"))
        _rc, b, _ = self.runner([], S.ScriptedRunner(S.dgx_host_script()),
                                DGX)
        sh = self.tracker(a, b)
        self.assertEqual(sh.get("gpu01", "Overall"), "HEALTHY")
        self.assertNotIn("identity", sh.get("gpu01", "Notes"))

    # -- identity mismatch: the one sticky stop (ruling) ----------------------
    MISMATCH_HOSTS = "hostname,address\ngpu07,a\n"

    def mismatch_root(self):
        # the address answers as gpu08: step zero stops "gpu07"
        rc, root, _ = self.runner([], S.ScriptedRunner(
            S.dgx_host_script(hostname="gpu08")), self.MISMATCH_HOSTS)
        self.assertEqual(rc, 2)
        return root

    def confirmed_root(self):
        _rc, root, _ = self.runner([], S.ScriptedRunner(
            S.dgx_host_script(hostname="gpu07")), self.MISMATCH_HOSTS)
        return root

    def test_identity_mismatch_alone(self):
        sh = self.tracker(self.mismatch_root())
        self.assertEqual(sh.get("gpu07", "Overall"), "INCOMPLETE")
        notes = self.clean(sh, "gpu07")
        self.assertIn("identity mismatch — hosts entry 'gpu07' but the host "
                      "reports hostname 'gpu08'", notes)
        self.assertIn("clears only when a later run's own step zero "
                      "confirms the host's identity", notes)

    def test_identity_mismatch_does_not_clear_on_unrelated_later_runs(self):
        a = self.mismatch_root()
        # later: unreachable; identity unconfirmed; a legacy (pre-2.0) run
        # with full passing results but no step zero at all
        _rc, b1, _ = self.runner([], dead_runner(), self.MISMATCH_HOSTS)
        _rc, b2, _ = self.runner([], S.ScriptedRunner(S.dgx_host_script(
            hostname="gpu07", serial="", extra=[(SERIAL_READ, 1, "", 0.1)])),
            self.MISMATCH_HOSTS)
        b3 = os.path.join(self.tmp, "legacy")
        S.write_vitals_root(b3, [S.full_dgx("gpu07")])
        for later in (b1, b2, b3):
            sh = self.tracker(a, later)
            self.assertEqual(sh.get("gpu07", "Overall"), "INCOMPLETE", later)
            self.assertIn("identity mismatch", sh.get("gpu07", "Notes"))
            self.assertEqual(sh.rc, 2)

    def test_identity_mismatch_clears_on_a_later_confirmed_run(self):
        a = self.mismatch_root()
        c = self.confirmed_root()
        sh = self.tracker(a, c)
        self.assertEqual(sh.get("gpu07", "Overall"), "HEALTHY")
        self.assertNotIn("identity mismatch", sh.get("gpu07", "Notes"))
        self.assertEqual(sh.rc, 0)
        # an unrelated run between the two does not change that
        _rc, b, _ = self.runner([], dead_runner(), self.MISMATCH_HOSTS)
        sh = self.tracker(a, b, c)
        self.assertEqual(sh.get("gpu07", "Overall"), "HEALTHY")

    def test_a_mismatch_after_a_confirmed_run_stands(self):
        # order matters: the mismatch is the later evidence
        c = self.confirmed_root()
        a = self.mismatch_root()
        sh = self.tracker(c, a)
        self.assertEqual(sh.get("gpu07", "Overall"), "INCOMPLETE")
        self.assertIn("identity mismatch", sh.get("gpu07", "Notes"))

    # -- regression guards ----------------------------------------------------
    def test_ki1_host_is_healthy_with_warnings(self):
        script = S.dgx_host_script(
            health=S.corpus_block(*S.W09, "sudo nvsm show health"))
        rc, root, _ = self.runner([], S.ScriptedRunner(script), DGX)
        self.assertEqual(rc, 2)                       # runner: health FAIL
        self.assertTrue(self.overall_line(root, "gpu01").startswith(
            "OVERALL: NOT HEALTHY"))
        sh = self.tracker(root)
        self.assertEqual(sh.get("gpu01", "Overall"), "HEALTHY WITH WARNINGS")
        self.assertEqual(sh.get("gpu01", "NVSM Health"), "Warned")
        self.assertEqual(sh.rc, 0)
        self.clean(sh, "gpu01")

    def test_clean_hosts_are_healthy_on_both(self):
        for argv, runner, hosts, host in (
                ([], S.ScriptedRunner(S.dgx_host_script()), DGX, "gpu01"),
                (["--profile", "hgx"], S.ScriptedRunner(S.hgx_host_script()),
                 HGX, "hgx01")):
            rc, root, _ = self.runner(argv, runner, hosts)
            self.assertEqual(rc, 0)
            sh = self.tracker(root)
            self.assertEqual(sh.get(host, "Overall"), "HEALTHY")
            self.assertEqual(sh.rc, 0)
            self.assertEqual(self.clean(sh, host).count("incomplete"), 0)

    def test_every_runner_outcome_is_a_recognised_verdict(self):
        cases = [
            ([], S.dgx_host_script(), DGX, "gpu01", "HEALTHY"),
            ([], S.dgx_host_script(gpus=4), DGX, "gpu01", "NOT HEALTHY"),
            # refused stress (uncleared alerts) -> SKIP -> INCOMPLETE
            ([], S.dgx_host_script(extra=[(
                "nvsm stress-test", 0, "Stress Testing will not be done: "
                "there are system alerts that have not been cleared", 0.7)]),
             DGX, "gpu01", "INCOMPLETE"),
            # legend-only topology -> step INCOMPLETE -> host INCOMPLETE
            ([], S.dgx_host_script(topo="Legend:\n  NV#  = Connection "
                                   "traversing a bonded set of # NVLinks"),
             DGX, "gpu01", "INCOMPLETE"),
            (["--profile", "hgx"], S.hgx_host_script(nccl=NO_LAUNCH()), HGX,
             "hgx01", "INCOMPLETE"),
        ]
        for argv, script, hosts, host, want in cases:
            rc, root, _ = self.runner(argv, S.ScriptedRunner(script), hosts)
            self.assertTrue(self.overall_line(root, host).startswith(
                f"OVERALL: {want}"), (want, self.overall_line(root, host)))
            S.install_fake_openpyxl()
            tb = S._load("tracker_build.py")
            ev = tb.parse_fulllog(S.read_text(os.path.join(
                root, host, f"{host}-FULLLOG.txt")))
            self.assertEqual(ev.problems, [], want)
            sh = self.tracker(root)
            self.assertEqual(sh.get(host, "Overall"), want)
            self.clean(sh, host)
        # the tracker's step cell carries the runner's Incomplete state
        self.assertEqual(sh.rc, 2)

    def test_incomplete_step_state_is_carried_and_replaced(self):
        legend = ("Legend:\n  NV#  = Connection traversing a bonded set of "
                  "# NVLinks")
        _rc, a, _ = self.runner([], S.ScriptedRunner(
            S.dgx_host_script(topo=legend)), DGX)
        sh = self.tracker(a)
        self.assertEqual(sh.get("gpu01", "NVLink Topology"), "Incomplete")
        self.assertIn("NVLink Topology incomplete — GPU topology matrix not "
                      "found or unparseable", sh.get("gpu01", "Notes"))
        # a later --only topo re-run with a real matrix replaces it, and
        # nothing of the provisional state survives on the row
        _rc, b, _ = self.runner(["--only", "topo"], S.ScriptedRunner(
            S.dgx_host_script()), DGX)
        sh = self.tracker(a, b)
        self.assertEqual(sh.get("gpu01", "NVLink Topology"), "Complete")
        self.assertEqual(sh.get("gpu01", "Overall"), "HEALTHY")
        self.assertNotIn("unparseable", sh.get("gpu01", "Notes"))


if __name__ == "__main__":
    unittest.main()
