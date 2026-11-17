"""Brief C item 8 — run hygiene.

M8  a timed-out step's process group on the host is signalled (TERM,
    then KILL) and verified gone; an unconfirmed kill is reported.
M9  the stress step harvests the file nvsm printed ("Stress Test Log:
    <path>"), not a guessed location; the dump step likewise.
M10 sudo is validated on the first reachable host before the fleet fans
    out; a failure stops the wave having touched one host.
Plus the runner seams the cleanup relies on (PGID tracking line)."""

import os
import unittest

from tests import support as S
from tests.test_rc4_runs import fulllog, step_calls

STRESS_TIMEOUT = ("nvsm stress-test", 124,
                  "Stressing GPU : Multiplying matrices on GPUs\n"
                  "    3% Complete...\n[TIMEOUT after 5400s]", 5400.0)


class TimeoutCleanup(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()

    def _run(self, extra):
        r = S.ScriptedRunner(S.dgx_host_script(extra=extra))
        rc, out, err, root = S.drive_main(self, self.vit, [], {"a": r},
                                          "hostname,address\ngpu01,a\n")
        return rc, r, fulllog(root, "gpu01")

    def test_timed_out_stress_group_is_killed_and_verified(self):
        rc, r, log = self._run([STRESS_TIMEOUT, ("pgrep -g 4242", 1, "", 0.1)])
        kills = r.ran("kill -TERM -- -4242")
        self.assertEqual(len(kills), 1)
        self.assertTrue(kills[0]["sudo"])          # stress ran under sudo
        self.assertEqual(r.ran("kill -KILL"), [])  # TERM sufficed
        self.assertIn("[timeout cleanup: process group 4242 confirmed gone "
                      "after SIGTERM", log)
        self.assertIn("does not prove the service-side test stopped", log)
        self.assertEqual(rc, 2)
        # the dump step still ran after the cleanup
        self.assertTrue(r.ran("nvsm dump health"))

    def test_survivor_gets_sigkill_then_is_reported(self):
        rc, r, log = self._run([STRESS_TIMEOUT, ("pgrep -g 4242", 0, "4243\n",
                                                 0.1)])
        self.assertEqual(len(r.ran("kill -KILL -- -4242")), 1)
        self.assertIn("STILL ALIVE after SIGTERM and SIGKILL", log)
        self.assertIn("timeout cleanup NOT confirmed", log)

    def test_unverifiable_cleanup_is_reported(self):
        _rc, _r, log = self._run([STRESS_TIMEOUT,
                                  ("pgrep -g 4242", 255, "", 0.1)])
        self.assertIn("could NOT verify the group is gone", log)

    def test_no_pgid_captured_is_reported(self):
        r = S.ScriptedRunner(S.dgx_host_script(extra=[STRESS_TIMEOUT]),
                             pgid=None)
        _rc, _o, _e, root = S.drive_main(self, self.vit, [], {"a": r},
                                         "hostname,address\ngpu01,a\n")
        self.assertIn("no process-group id was captured",
                      fulllog(root, "gpu01"))
        self.assertEqual(r.ran("kill -"), [])

    def test_no_cleanup_without_a_timeout(self):
        _rc, r, _log = self._run([])
        self.assertEqual(r.ran("kill "), [])


class Harvest(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()

    def test_stress_log_is_harvested_from_the_printed_path(self):
        r = S.ScriptedRunner(S.dgx_host_script())
        _rc, _o, _e, root = S.drive_main(self, self.vit, [], {"a": r},
                                         "hostname,address\ngpu01,a\n")
        printed = "/var/nvsmlog/nvsm/StressTestLog2026-11-12T01:12:53Z.nvsmlog"
        self.assertIn(printed, r.fetches)              # copy tried first
        self.assertEqual(r.ran("base64 " + printed)[0]["sudo"], True)
        local = os.path.join(root, "gpu01",
                             "StressTestLog2026-11-12T01-12-53Z.nvsmlog")
        with open(local, "rb") as f:
            self.assertEqual(f.read(), b"fake log\n")
        self.assertIn("stress log: StressTestLog2026-11-12T01-12-53Z.nvsmlog",
                      S.read_summary(root)["gpu01"]["Notes"])
        self.assertEqual(r.ran("find "), [])           # no guessed paths

    def test_plain_copy_used_when_it_works(self):
        r = S.ScriptedRunner(S.dgx_host_script())
        r.fetch_error = None
        _rc, _o, _e, root = S.drive_main(self, self.vit, [], {"a": r},
                                         "hostname,address\ngpu01,a\n")
        self.assertEqual(r.ran("base64"), [])
        self.assertIn("copied from /var/nvsmlog/nvsm/", fulllog(root, "gpu01"))

    def test_failed_harvest_is_reported_not_silent(self):
        r = S.ScriptedRunner(S.dgx_host_script(extra=[("base64", 1, "", 0.1)]))
        _rc, _o, _e, root = S.drive_main(self, self.vit, [], {"a": r},
                                         "hostname,address\ngpu01,a\n")
        self.assertIn("[stress log NOT harvested: could not harvest "
                      "/var/nvsmlog/nvsm/", fulllog(root, "gpu01"))

    def test_unsafe_printed_path_is_refused(self):
        r = S.ScriptedRunner([])
        got, detail = self.vit.harvest_printed_file(
            r, "/tmp/x;rm -rf ~", S.tempdir(self))
        self.assertIsNone(got)
        self.assertIn("unsafe", detail)
        self.assertEqual(r.calls + r.fetches, [])


class SudoCanary(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.vit.getpass.getpass = lambda prompt="": "typo"
        self.addCleanup(setattr, self.vit.getpass, "getpass", S.getpass_real)

    def test_wrong_password_costs_one_host(self):
        runners = {a: S.ScriptedRunner(
            [(lambda c: c == "sudo true", 1, "[stderr]\nSorry, try again.",
              0.1)] + S.dgx_host_script(h))
            for a, h in (("a", "gpu01"), ("b", "gpu02"))}
        rc, out, err, root = S.drive_main(
            self, self.vit, ["--sudo-password"], runners,
            "hostname,address\ngpu01,a\ngpu02,b\n")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("sudo canary failed on gpu01", err)
        self.assertEqual([c for c in runners["a"].calls if c["sudo"]][0]["pw"],
                         "typo")
        self.assertEqual(len([c for c in runners["a"].calls if c["sudo"]]), 1)
        self.assertEqual(runners["b"].calls, [])
        for r in runners.values():
            self.assertEqual(step_calls(r), [])
        self.assertFalse(os.path.exists(root))

    def test_unreachable_first_host_moves_the_canary(self):
        dead = S.ScriptedRunner([], default=(255, "", 15.0))
        ok = S.ScriptedRunner(S.dgx_host_script("gpu02"))
        rc, out, _e, _root = S.drive_main(
            self, self.vit, [], {"a": dead, "b": ok},
            "hostname,address\ngpu01,a\ngpu02,b\n")
        self.assertIn("sudo canary: gpu02", out)
        self.assertEqual(rc, 2)      # gpu01 UNREACHABLE


class RunnerSeams(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()

    def test_ssh_runner_tracks_the_remote_process_group(self):
        seen = []
        self.vit._exec = lambda cmd, *a, **k: (seen.append(cmd) or
                                               (0, "", 0.0))
        r = self.vit.SSHRunner("10.0.0.1", user="ops")
        r.run(["nvsm", "stress-test", "--no-prompt"], 60, sudo=True,
              track=True)
        self.assertEqual(seen[0][-1], "echo DGXV-PGID=$$; exec sudo -n nvsm "
                                      "stress-test --no-prompt")
        r.run(["true"], 30)
        self.assertEqual(seen[1][-1], "true")

    def test_local_runner_starts_a_new_session_when_tracking(self):
        seen = []
        self.vit._exec = lambda cmd, *a, **k: (seen.append((cmd, k)) or
                                               (0, "", 0.0))
        self.vit.LocalRunner().run(["nvidia-smi", "-q"], 60, track=True)
        cmd, kw = seen[0]
        self.assertEqual(cmd[:3], ["sh", "-c", 'echo DGXV-PGID=$$; exec "$@"'])
        self.assertTrue(kw["new_session"])

    def test_take_pgid_strips_only_the_tracking_line(self):
        pgid, out = self.vit.take_pgid("DGXV-PGID=77\nline1\nline2\n")
        self.assertEqual((pgid, out), (77, "line1\nline2\n"))
        self.assertEqual(self.vit.take_pgid("plain"), (None, "plain"))


if __name__ == "__main__":
    unittest.main()
