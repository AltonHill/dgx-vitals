"""C3 — a dry run touches nothing real.

(a) a dry run into a root holding any non-dry FULLLOG refuses: non-zero
    exit, a message naming the root and the reason, nothing written;
(b) dry-run artifacts live apart from real ones (<out>/dry-run/...);
(c) a real run treats a DRY RUN FULLLOG as absent, never as a skip reason.

Every dgx-vitals call here is --dry-run (DryRunner) or run_host() with an
in-process fake runner; subprocess is stubbed to fail the test if used."""

import os
import unittest

from tests import support as S

DRY_DIR = "dry-run"


def real_fulllog(host):
    return S.fulllog_text(host, [("cat /etc/dgx-release", "DGX OS 7",
                                  "PASS", "DGX OS 7")])


class DryRunIntoEmptyRoot(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)
        self.root = os.path.join(self.tmp, "Results")
        self.vit = S.load_vitals()

    def _dry(self, *extra):
        return S.run_vitals_main(self.vit, ["--host", "gpu01", "--dry-run",
                                            "--out", self.root] + list(extra))

    def test_twice_is_idempotent_and_writes_no_real_artifacts(self):
        rc1, _o, e1 = self._dry()
        first = S.snapshot(self.root)
        rc2, _o, e2 = self._dry()
        second = S.snapshot(self.root)
        self.assertEqual((rc1, rc2), (0, 0), e1 + e2)
        self.assertEqual(sorted(first), sorted(second))
        # nothing at the real artifact paths
        self.assertFalse(os.path.exists(os.path.join(self.root, "summary.csv")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "gpu01")))
        self.assertEqual(os.listdir(self.root), [DRY_DIR])
        # every dry artifact under the dry-run subdir, consistently
        # REVIEW-A L1 (Brief D): the card is named for its verdict, so a
        # dry run's card is -DRY-RUN.png (it was -HEALTHY.png).
        for rel in ("summary.csv", "gpu01/gpu01-FULLLOG.txt",
                    "gpu01/gpu01-DRY-RUN.png"):
            self.assertIn(os.path.join(DRY_DIR, rel), second)
        log = second[os.path.join(DRY_DIR, "gpu01", "gpu01-FULLLOG.txt")]
        self.assertIn(b"[DRY RUN]", log.splitlines()[0])

    def test_multi_host_dry_run(self):
        fleet = os.path.join(self.tmp, "hosts.csv")
        with open(fleet, "w") as f:
            f.write("hostname,address\ngpu01,192.0.2.1\ngpu02,192.0.2.2\n")
        rc, _o, err = S.run_vitals_main(
            self.vit, ["--hosts", fleet, "--dry-run", "--out", self.root])
        self.assertEqual(rc, 0, err)
        self.assertEqual(os.listdir(self.root), [DRY_DIR])
        snap = S.snapshot(self.root)
        for h in ("gpu01", "gpu02"):
            self.assertIn(os.path.join(DRY_DIR, h, f"{h}-FULLLOG.txt"), snap)

    def test_tracker_does_not_ingest_dry_artifacts(self):
        self._dry()
        S.install_fake_openpyxl()
        tb = S._load("tracker_build.py")
        self.assertEqual(tb.read_vitals(self.root), {})

    def test_watch_without_hosts_lists_no_dry_host(self):
        self._dry()
        rc, out, err = S.run_vitals_main(
            self.vit, ["--watch", "--once", "--out", self.root])
        self.assertNotIn("gpu01", out)
        self.assertNotIn(DRY_DIR, out)


class DryRunRefusesRealRoot(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)
        self.root = os.path.join(self.tmp, "Results")
        self.vit = S.load_vitals()

    def _plant(self, host):
        hd = os.path.join(self.root, host)
        os.makedirs(hd, exist_ok=True)
        with open(os.path.join(hd, f"{host}-FULLLOG.txt"), "w",
                  encoding="utf-8") as f:
            f.write(real_fulllog(host))

    def test_refuses_and_leaves_planted_log_byte_identical(self):
        self._plant("gpu01")
        before = S.snapshot(self.root)
        rc, out, err = S.run_vitals_main(
            self.vit, ["--host", "gpu01", "--dry-run", "--out", self.root])
        self.assertNotEqual(rc, 0)
        self.assertNotIn(rc, (None,))
        self.assertIn(self.root, err)
        self.assertIn("real", err.lower())
        self.assertIn("gpu01-FULLLOG.txt", err)
        self.assertEqual(S.snapshot(self.root), before)  # byte-identical,
        # and nothing new (no summary, no dry-run dir)

    def test_refuses_for_a_real_log_of_another_host(self):
        self._plant("gpu99")
        before = S.snapshot(self.root)
        rc, _o, err = S.run_vitals_main(
            self.vit, ["--host", "gpu01", "--dry-run", "--out", self.root])
        self.assertNotEqual(rc, 0)
        self.assertEqual(S.snapshot(self.root), before)

    def test_refusal_comes_before_any_prompt(self):
        self._plant("gpu01")

        def no_prompt(*a, **k):
            raise AssertionError("prompted for a password before refusing")
        self.vit.getpass.getpass = no_prompt
        self.addCleanup(setattr, self.vit.getpass, "getpass",
                        S.getpass_real)
        rc, _o, err = S.run_vitals_main(
            self.vit, ["--host", "gpu01", "--dry-run", "--sudo-password",
                       "--out", self.root])
        self.assertEqual(rc, 2, err)

    def test_legacy_dry_stub_at_real_path_is_not_a_refusal(self):
        hd = os.path.join(self.root, "gpu01")
        os.makedirs(hd)
        with open(os.path.join(hd, "gpu01-FULLLOG.txt"), "w",
                  encoding="utf-8") as f:
            f.write(S.fulllog_text("gpu01", dry=True))
        rc, _o, err = S.run_vitals_main(
            self.vit, ["--host", "gpu01", "--dry-run", "--out", self.root])
        self.assertEqual(rc, 0, err)


class RealRunGuard(unittest.TestCase):
    """The skip guard in run_host, driven with an in-process fake runner."""

    def setUp(self):
        self.tmp = S.tempdir(self)
        self.root = os.path.join(self.tmp, "Results")
        self.vit = S.load_vitals()
        # no harvest (the fake runner has nothing to fetch)
        self.vit.harvest_nvsmlog = lambda *a, **k: []
        self.vit.harvest_dump_archive = lambda *a, **k: []
        self.steps = self.vit.build_steps("hgx", 60, include_nccl=False)[:1]

    def _real_run(self, force=False):
        runner = S.FakeRunner(rc=0, out="NAME=Ubuntu\nVERSION=24.04")
        res = self.vit.run_host("gpu01", runner, self.root, self.steps, 8,
                                force=force)
        return res, runner

    def test_dry_run_then_real_does_not_skip(self):
        rc, _o, err = S.run_vitals_main(
            self.vit, ["--host", "gpu01", "--dry-run", "--out", self.root])
        self.assertEqual(rc, 0, err)
        res, runner = self._real_run()
        self.assertNotEqual(res["overall"], "SKIPPED")
        self.assertTrue(runner.calls)

    def test_legacy_dry_stub_is_treated_as_absent(self):
        hd = os.path.join(self.root, "gpu01")
        os.makedirs(hd)
        with open(os.path.join(hd, "gpu01-FULLLOG.txt"), "w",
                  encoding="utf-8") as f:
            f.write(S.fulllog_text("gpu01", dry=True))
        res, runner = self._real_run()
        self.assertNotEqual(res["overall"], "SKIPPED")
        self.assertTrue(runner.calls)
        with open(os.path.join(hd, "gpu01-FULLLOG.txt"),
                  encoding="utf-8") as f:
            self.assertNotIn("[DRY RUN]", f.readline())

    def test_real_fulllog_still_skips(self):
        hd = os.path.join(self.root, "gpu01")
        os.makedirs(hd)
        p = os.path.join(hd, "gpu01-FULLLOG.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(real_fulllog("gpu01"))
        before = S.snapshot(self.root)
        res, runner = self._real_run()
        self.assertEqual(res["overall"], "SKIPPED")
        self.assertEqual(runner.calls, [])
        self.assertEqual(S.snapshot(self.root), before)


if __name__ == "__main__":
    unittest.main()
