"""Brief C riders — docs truth (F14 applied to the runner) and Q6.

The runner's --help and module docstring describe function only (no
customer or individual names) and document --check, the exit codes,
UNREACHABLE and the NCCL opt-out; --help states what --force does with
--no-prompt (nothing: --force never reaches nvsm; --force-stress
appends nvsm's --force after --no-prompt)."""

import unittest

from tests import support as S
from tests.test_b7_riders import NAMES


def help_text(vit):
    _rc, out, _err = S.run_vitals_main(vit, ["--help"])
    return out


class RunnerDocs(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.help = help_text(self.vit)

    def test_docstring_and_help_name_no_one(self):
        self.assertIsNone(NAMES.search(self.vit.__doc__))
        self.assertIsNone(NAMES.search(self.help))
        for word in ("comet", "mimir", "nesd", "tailnet"):
            self.assertNotIn(word, (self.vit.__doc__ + self.help).lower())

    def test_help_documents_the_round(self):
        for word in ("--check", "exit codes", "UNREACHABLE", "INCOMPLETE",
                     "--no-require-nccl", "NCCL not required (operator "
                     "opt-out)", "--gpus", "--nvlink"):
            self.assertIn(word, self.help, word)

    def test_docstring_documents_the_round(self):
        doc = self.vit.__doc__
        for word in ("--check", "Exit codes", "UNREACHABLE", "Step zero",
                     "--no-require-nccl", "PROFILE_DATA", "process group"):
            self.assertIn(word, doc, word)

    def test_force_with_no_prompt_is_stated(self):
        flat = " ".join(self.help.split())
        self.assertIn("--force-stress", flat)
        self.assertIn("nvsm stress-test --no-prompt --force", flat)
        self.assertIn("it is never passed to nvsm", flat)

    def test_force_never_reaches_nvsm_in_the_code(self):
        stress = [s for s in self.vit.build_steps("dgx", 60)
                  if s.slug == "stress"][0]
        self.assertEqual(stress.argv, ["nvsm", "stress-test", "--no-prompt"])
        forced = [s for s in self.vit.build_steps("dgx", 60,
                                                  force_stress=True)
                  if s.slug == "stress"][0]
        self.assertEqual(forced.argv, ["nvsm", "stress-test", "--no-prompt",
                                       "--force"])

    def test_dry_plan_shows_step_zero_and_expectations(self):
        tmp = S.tempdir(self)
        rc, out, err = S.run_vitals_main(
            self.vit, ["--host", "gpu01", "--dry-run", "--out", tmp + "/r"])
        self.assertEqual(rc, 0, err)
        self.assertIn("0. identity probe", out)
        self.assertIn("expects: 8 GPUs (profile default: DGX B300)", out)


if __name__ == "__main__":
    unittest.main()
