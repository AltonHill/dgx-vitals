"""Brief D Part 3 — run hygiene: REVIEW-A H4, M6, M11, L1.

H4  "finished" means the FULLLOG has an OVERALL line. An unfinished log
    is redone (the partial log is kept, never overwritten); Ctrl-C stops
    the wave — no queued host starts, a step in flight is recorded as
    interrupted with no verdict (never FAIL exit 255), the summary is
    still written, exit 130 — and the next run redoes the host.
M6  --watch: done only on the OVERALL line (a stale card or a dry stub is
    not done); interrupted and stalled states; WAVE STOPPED instead of
    waiting forever; a wrong --out is said out loud.
M11 one hosts/fleet CSV parser for both tools: #-comments skipped by the
    tracker too; lan_ip used as the address; a header without an exact
    'hostname' column is refused, never zero hosts or a host.
L1  the card is named for its verdict; one card per host."""

import os
import threading
import time
import types
import unittest

from tests import support as S

DGX = "hostname,address\ngpu01,a\n"
TWO = "hostname,address\ngpu01,a\ngpu02,b\n"


def fulllog_path(root, host):
    return os.path.join(root, host, f"{host}-FULLLOG.txt")


class ResumeMeansFinished(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.tmp = S.tempdir(self)
        self.root = os.path.join(self.tmp, "Results")

    def test_unfinished_fulllog_is_redone_and_kept(self):
        hd = os.path.join(self.root, "gpu01")
        os.makedirs(hd)
        partial = S.runner_fulllog("gpu01", [("release", "DGX_NAME=x",
                                              "PASS", "x")],
                                   overall=False, planned=6)
        with open(fulllog_path(self.root, "gpu01"), "w") as f:
            f.write(partial)
        r = S.ScriptedRunner(S.dgx_host_script())
        rc, out, err, _ = S.drive_main(self, self.vit, ["--out", self.root],
                                       {"a": r}, DGX)
        self.assertEqual(rc, 0, out + err)
        log = S.read_text(fulllog_path(self.root, "gpu01"))
        self.assertIn("OVERALL: HEALTHY", log)
        kept = [f for f in os.listdir(hd) if ".interrupted-" in f]
        self.assertEqual(len(kept), 1, os.listdir(hd))
        self.assertTrue(kept[0].startswith("gpu01-FULLLOG.interrupted-"))
        self.assertFalse(kept[0].endswith("-FULLLOG.txt"))  # tracker ignores
        self.assertEqual(S.read_text(os.path.join(hd, kept[0])), partial)
        self.assertIn(f"kept as {kept[0]}", log)

    def test_finished_fulllog_is_skipped(self):
        r = S.ScriptedRunner(S.dgx_host_script())
        S.drive_main(self, self.vit, ["--out", self.root], {"a": r}, DGX)
        r2 = S.ScriptedRunner(S.dgx_host_script())
        rc, out, _e, _ = S.drive_main(self, S.load_vitals(),
                                      ["--out", self.root], {"a": r2}, DGX)
        self.assertEqual(S.read_summary(self.root)["gpu01"]["Overall"],
                         "SKIPPED")
        self.assertIn("finished FULLLOG exists",
                      S.read_summary(self.root)["gpu01"]["Notes"])


class CtrlC(unittest.TestCase):
    """The operator's Ctrl-C, delivered where it lands in real life: the
    main thread, while it waits on the host workers."""

    def setUp(self):
        self.vit = S.load_vitals()
        self.tmp = S.tempdir(self)
        self.root = os.path.join(self.tmp, "Results")

    def interrupt_during(self, needle, jobs="1"):
        vit = self.vit
        reached = threading.Event()

        def slow(cmd):
            reached.set()
            vit._INTERRUPT.wait(5)          # the step is "in flight"
            return "partial output"
        script = [(needle, 0, slow, 30.0)] + S.dgx_host_script()
        r1 = S.ScriptedRunner(script)
        r2 = S.ScriptedRunner(S.dgx_host_script(hostname="gpu02",
                                                serial="SN-0002"))
        real_wait = vit.wait
        fired = []

        def fake_wait(fs, timeout=None, return_when=None):
            if not fired and reached.wait(5):
                fired.append(1)
                raise KeyboardInterrupt
            return real_wait(fs, timeout=timeout, return_when=return_when)
        vit.wait = fake_wait
        rc, out, err, _ = S.drive_main(
            self, vit, ["--out", self.root, "--jobs", jobs],
            {"a": r1, "b": r2}, TWO)
        return rc, out, err, r1, r2

    def test_ctrl_c_mid_step_records_an_interrupt_not_a_failure(self):
        rc, out, err, r1, r2 = self.interrupt_during("nvidia-smi -q")
        self.assertEqual(rc, 130, out + err)
        log = S.read_text(fulllog_path(self.root, "gpu01"))
        self.assertIn("INTERRUPTED: operator interrupt (Ctrl-C) during step "
                      "3/6 (NVIDIA-SMI Query)", log)
        self.assertNotIn("OVERALL:", log)                  # unfinished
        self.assertNotIn("[3/6] nvidia-smi -q", log)       # no step block
        self.assertNotIn("FAIL", log.split("INTERRUPTED")[1])
        self.assertIn("partial output", log)
        # the step's process group on the host is cleaned up, and checked
        self.assertTrue(r1.ran("kill -TERM -- -4242"))
        self.assertIn("[interrupt cleanup: process group 4242", log)
        # no step after it ran; no card for an unfinished run
        self.assertEqual(r1.ran("nvidia-smi topo -m"), [])
        self.assertEqual(os.listdir(os.path.join(self.root, "gpu01")),
                         ["gpu01-FULLLOG.txt"])
        # the queued host never started
        self.assertFalse(os.path.exists(os.path.join(self.root, "gpu02")))
        self.assertEqual(r2.calls, [])
        self.assertIn("NOT STARTED (interrupted): gpu02", out)
        # the summary is still written, saying so
        row = S.read_summary(self.root)["gpu01"]
        self.assertEqual(row["Overall"], "INTERRUPTED")
        self.assertEqual(row["Release"], "Complete")
        self.assertEqual(row["NVIDIA-SMI Query"], "")

    def test_the_next_run_redoes_the_interrupted_host(self):
        self.interrupt_during("nvidia-smi -q")
        r1 = S.ScriptedRunner(S.dgx_host_script())
        r2 = S.ScriptedRunner(S.dgx_host_script(hostname="gpu02",
                                                serial="SN-0002"))
        rc, out, err, _ = S.drive_main(self, S.load_vitals(),
                                       ["--out", self.root],
                                       {"a": r1, "b": r2}, TWO)
        self.assertEqual(rc, 0, out + err)
        for h in ("gpu01", "gpu02"):
            self.assertIn("OVERALL: HEALTHY",
                          S.read_text(fulllog_path(self.root, h)))
        self.assertTrue(any(".interrupted-" in f for f in os.listdir(
            os.path.join(self.root, "gpu01"))))

    def test_the_tracker_reads_an_interrupted_root_as_incomplete(self):
        self.interrupt_during("nvidia-smi -q")
        sh = S.run_tracker(["--vitals", self.root], self.tmp)
        self.assertEqual(sh.get("gpu01", "Overall"), "INCOMPLETE")
        self.assertIn("interrupted by the operator", sh.get("gpu01", "Notes"))
        self.assertEqual(sh.rc, 2)


class ExecInterrupt(unittest.TestCase):
    """_exec itself, with a stand-in Popen (nothing executes)."""

    def setUp(self):
        self.vit = S._load("dgx-vitals.py")       # real _exec
        vit = self.vit

        class TimeoutExpired(Exception):
            pass

        class Popen:
            calls = 0

            def __init__(self, cmd, **kw):
                self.kw, self.killed, self.returncode = kw, False, None

            def communicate(self, input=None, timeout=None):
                Popen.calls += 1
                if self.killed:
                    return "so far", ""
                if Popen.calls >= 2:
                    vit._INTERRUPT.set()
                raise TimeoutExpired()

            def kill(self):
                self.killed = True
        self.Popen = Popen
        vit.subprocess = types.SimpleNamespace(
            Popen=Popen, PIPE=-1, STDOUT=-2, TimeoutExpired=TimeoutExpired)

    def test_interruptible_call_is_cut_short(self):
        rc, out, _d = self.vit._exec(["ssh", "x"], 60, None,
                                     new_session=True, interruptible=True)
        self.assertEqual(rc, self.vit.RC_INTERRUPTED)
        self.assertIn("so far", out)
        self.assertIn("[INTERRUPTED by the operator]", out)

    def test_a_ctrl_c_in_the_main_thread_is_an_interrupt(self):
        def boom(self_, input=None, timeout=None):
            raise KeyboardInterrupt
        first = [True]
        orig = self.Popen.communicate

        def comm(self_, input=None, timeout=None):
            if first[0]:
                first[0] = False
                raise KeyboardInterrupt
            return orig(self_, input, timeout)
        self.Popen.communicate = comm
        rc, _out, _d = self.vit._exec(["ssh", "x"], 60, None,
                                      interruptible=True)
        self.assertEqual(rc, self.vit.RC_INTERRUPTED)
        self.assertTrue(self.vit._INTERRUPT.is_set())

    def test_ssh_steps_run_in_their_own_session_and_are_interruptible(self):
        seen = []
        self.vit._exec = lambda cmd, t, si, st=None, **kw: (
            seen.append(kw) or (0, "", 0.0))
        r = self.vit.SSHRunner("a")
        r.run(["nvidia-smi", "-q"], 60, track=True)
        r.run(["true"], 30)
        self.assertEqual(seen[0], {"new_session": True,
                                   "interruptible": True})
        self.assertEqual(seen[1], {"new_session": True,
                                   "interruptible": False})
        self.vit.SSHRunner("a", batch=False).run(["true"], 30)
        self.assertEqual(seen[2]["new_session"], False)   # may need the tty


class WatchStaleness(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.root = os.path.join(S.tempdir(self), "Results")

    def write(self, host, text, age=0):
        hd = os.path.join(self.root, host)
        os.makedirs(hd, exist_ok=True)
        p = fulllog_path(self.root, host)
        with open(p, "w") as f:
            f.write(text)
        if age:
            t = time.time() - age
            os.utime(p, (t, t))
        return hd

    def test_a_stale_card_does_not_make_a_running_host_done(self):
        hd = self.write("gpu01", S.runner_fulllog(
            "gpu01", [("release", "x", "PASS", "x")], overall=False,
            planned=6))
        with open(os.path.join(hd, "gpu01-HEALTHY.png"), "wb") as f:
            f.write(b"old card")
        self.assertEqual(self.vit.wave_state(self.root, "gpu01")[0],
                         "running")

    def test_done_means_the_overall_line(self):
        self.write("gpu01", S.runner_fulllog(
            "gpu01", [("release", "x", "PASS", "x")]))
        st, v = self.vit.wave_state(self.root, "gpu01")
        self.assertEqual(st, "done")
        self.assertTrue(v.startswith("HEALTHY"))

    def test_a_dry_stub_is_not_done(self):
        self.write("gpu01", S.fulllog_text("gpu01", dry=True))
        self.assertEqual(self.vit.wave_state(self.root, "gpu01")[0],
                         "waiting")

    def test_stalled_after_the_step_timeout(self):
        log = S.runner_fulllog("gpu01", [("release", "x", "PASS", "x")],
                               overall=False, planned=6)
        log += ("\n>>> step 2/6: NVSM Show Health — started "
                "2026-11-15T10:00:00Z · timeout 60s\n")
        self.write("gpu01", log, age=60 + self.vit.STALL_SLACK_S + 5)
        st, detail = self.vit.wave_state(self.root, "gpu01")
        self.assertEqual(st, "stalled")
        self.assertIn("step timeout 60s", detail)
        self.write("gpu01", log, age=30)
        self.assertEqual(self.vit.wave_state(self.root, "gpu01")[0],
                         "running")

    def test_interrupted_state_and_wave_stopped(self):
        log = S.runner_fulllog("gpu01", [("release", "x", "PASS", "x")],
                               overall=False, planned=6)
        log += "\nINTERRUPTED: operator interrupt (Ctrl-C) during step 2/6\n"
        self.write("gpu01", log)
        self.assertEqual(self.vit.wave_state(self.root, "gpu01")[0],
                         "interrupted")
        hosts = os.path.join(os.path.dirname(self.root), "h.csv")
        with open(hosts, "w") as f:
            f.write(TWO)
        rc, out, _e = S.run_vitals_main(self.vit, [
            "--watch", "--hosts", hosts, "--out", self.root])
        self.assertEqual(rc, 2)
        self.assertIn("WAVE STOPPED: 1 interrupted, 0 stalled, 1 not "
                      "started", out)

    def test_a_wrong_out_root_is_said_out_loud(self):
        hosts = os.path.join(os.path.dirname(self.root), "h.csv")
        with open(hosts, "w") as f:
            f.write(DGX)
        _rc, out, _e = S.run_vitals_main(self.vit, [
            "--watch", "--once", "--hosts", hosts, "--out",
            self.root + "-typo"])
        self.assertIn("no output root at", out)

    def test_watch_with_no_hosts_is_a_tool_error(self):
        # EXIT_HELP contract (Loki, 2026-11-16): misuse is 1. It was 2.
        rc, _o, err = S.run_vitals_main(self.vit, [
            "--watch", "--once", "--out", self.root + "-none"])
        self.assertEqual(rc, 1)
        self.assertIn("watch: no hosts", err)

    def test_step_markers_carry_the_timeout(self):
        r = S.ScriptedRunner(S.dgx_host_script())
        S.drive_main(self, self.vit, ["--out", self.root], {"a": r}, DGX)
        log = S.read_text(fulllog_path(self.root, "gpu01"))
        self.assertIn("— started ", log)
        self.assertRegex(log, r">>> step 5/6: NVSM Stress Test — started "
                              r"\S+ · timeout 5400s")


class OneHostsParser(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.tmp = S.tempdir(self)

    def csv(self, text):
        p = os.path.join(self.tmp, "fleet.csv")
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_runner_connects_to_lan_ip(self):
        e = self.vit.read_host_entries(self.csv(
            "hostname,lan_ip,bmc_ip\ngpu01,10.0.0.5,10.1.0.5\n"))
        self.assertEqual(e[0]["address"], "10.0.0.5")

    def test_a_header_without_an_exact_hostname_column_is_refused(self):
        p = self.csv("Node Hostname,LAN IP\ngpu01,10.0.0.5\n")
        with self.assertRaises(self.vit.HostsFileError):
            self.vit.read_host_entries(p)
        rc, _o, err = S.run_vitals_main(self.vit, ["--hosts", p,
                                                   "--dry-run"])
        self.assertEqual(rc, 1)
        self.assertIn("no column named exactly 'hostname'", err)

    def test_tracker_uses_the_same_parser(self):
        S.install_fake_openpyxl()
        tb = S._load("tracker_build.py")
        p = self.csv("# master fleet\nhostname,lan_ip,bmc_ip\n\n"
                     "# rack 1\ngpu01,10.0.0.5,10.1.0.5\n")
        order, hosts = tb.read_fleet(p)
        self.assertEqual(order, ["gpu01"])          # no junk '#' rows
        self.assertEqual((hosts["gpu01"].lan_ip, hosts["gpu01"].bmc_ip),
                         ("10.0.0.5", "10.1.0.5"))
        order, hosts = tb.read_fleet(self.csv(
            "hostname,address,user\ngpu02,192.0.2.2,ubuntu\ngpu03\n"))
        self.assertEqual(hosts["gpu02"].lan_ip, "192.0.2.2")
        self.assertEqual(hosts["gpu03"].lan_ip, "")   # no address given

    def test_tracker_refuses_a_bad_fleet_header(self):
        root = os.path.join(self.tmp, "r")
        S.write_vitals_root(root, [S.full_dgx("gpu01")])
        sh = S.run_tracker(["--vitals", root, "--hosts",
                            self.csv("Node Hostname,LAN IP\ngpu01,x\n")],
                           self.tmp)
        self.assertEqual(sh.rc, 1)
        self.assertNotIn("Node Hostname", sh.rows)


class CardNamedForItsVerdict(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.root = os.path.join(S.tempdir(self), "Results")

    def cards(self, host="gpu01"):
        return sorted(f for f in os.listdir(os.path.join(self.root, host))
                      if f.endswith(".png"))

    def test_not_healthy_card_is_not_called_healthy(self):
        r = S.ScriptedRunner(S.dgx_host_script(gpus=4))
        S.drive_main(self, self.vit, ["--out", self.root], {"a": r}, DGX)
        self.assertEqual(self.cards(), ["gpu01-NOT-HEALTHY.png"])

    def test_one_card_per_host_after_a_forced_rerun(self):
        r = S.ScriptedRunner(S.dgx_host_script(gpus=4))
        S.drive_main(self, self.vit, ["--out", self.root], {"a": r}, DGX)
        r = S.ScriptedRunner(S.dgx_host_script())
        S.drive_main(self, S.load_vitals(), ["--out", self.root, "--force"],
                     {"a": r}, DGX)
        self.assertEqual(self.cards(), ["gpu01-HEALTHY.png"])

    def test_tracker_embeds_the_verdict_named_card(self):
        r = S.ScriptedRunner(S.dgx_host_script(gpus=4))
        S.drive_main(self, self.vit, ["--out", self.root], {"a": r}, DGX)
        sh = S.run_tracker(["--vitals", self.root],
                           os.path.dirname(self.root))
        self.assertEqual(sh.csv_row("gpu01")["Health Card"],
                         "Results/gpu01/gpu01-NOT-HEALTHY.png")


if __name__ == "__main__":
    unittest.main()
