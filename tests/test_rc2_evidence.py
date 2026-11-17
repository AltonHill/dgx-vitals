"""Brief C item 2 (H7) — PASS only on positive structural evidence.

Topology: the GPU submatrix of `nvidia-smi topo -m` parsed by structure
against the expected class from profile data (real B300 output from the
corpus: PASS; a PCIe-class or narrower-NVLink cell: degraded FAIL; no
readable matrix: INCOMPLETE). Stress: 100% Complete + all five component
PASS lines + wall duration >= 1500 s (genuine 1827 s run: PASS; 0.9 s
echoed prompt: FAIL; already-running cancellation: FAIL). The other
inversions (health summary, release keys, dump archive path, NCCL result
rows) are pinned here too. verdict() is a pure function: nothing runs."""

import unittest

from tests import support as S


class Base(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.steps = {s.slug: s for s in
                      self.vit.build_steps("dgx", 60, include_nccl=True)}
        self.steps["os-release"] = self.vit.build_steps(
            "hgx", 60, include_nccl=False)[0]

    def v(self, slug, out, rc=0, gpus=8, dur=1.0, nvlink="NV18",
          required=False):
        return self.vit.verdict(self.steps[slug], rc, out, gpus, 0.0,
                                required, dur=dur, nvlink_class=nvlink)


class Topology(Base):

    def test_real_b300_matrix_passes_structurally(self):
        st, note = self.v("topo", S.corpus_block(*S.W09, "nvidia-smi topo -m"))
        self.assertEqual(st, "PASS")
        self.assertIn("all 28 GPU pairs NV18", note)

    def test_pcie_class_cell_is_degraded_fail(self):
        real = S.corpus_block(*S.W09, "nvidia-smi topo -m")
        # GPU2<->GPU5 drops to a PCIe-class path (both directions)
        rows = real.splitlines()
        for i, ln in enumerate(rows):
            if ln.startswith("GPU2\t"):
                c = ln.split("\t")
                c[6] = "SYS"
                rows[i] = "\t".join(c)
            if ln.startswith("GPU5\t"):
                c = ln.split("\t")
                c[3] = "SYS"
                rows[i] = "\t".join(c)
        st, note = self.v("topo", "\n".join(rows))
        self.assertEqual(st, "FAIL")
        self.assertIn("GPU2-GPU5 SYS (PCIe-class; expected NV18)", note)

    def test_narrower_nvlink_bond_is_degraded_fail(self):
        st, note = self.v("topo", S.topo_matrix(8, overrides={(0, 1): "NV12",
                                                               (1, 0): "NV12"}))
        self.assertEqual(st, "FAIL")
        self.assertIn("NVLink width", note)

    def test_legend_only_output_is_incomplete_not_pass(self):
        # pre-C, "NV" anywhere passed — the legend line alone qualified
        legend = ("Legend:\n\n  X    = Self\n"
                  "  NV#  = Connection traversing a bonded set of # NVLinks\n")
        st, note = self.v("topo", legend)
        self.assertEqual(st, "INCOMPLETE")
        self.assertIn("unparseable", note)

    def test_ragged_matrix_is_incomplete(self):
        bad = S.topo_matrix(8).replace("GPU3\t", "GPU3\tNV18\n", 1)
        self.assertEqual(self.v("topo", bad)[0], "INCOMPLETE")

    def test_unknown_cell_token_is_incomplete(self):
        st, _n = self.v("topo", S.topo_matrix(4, overrides={(0, 2): "??"}),
                        gpus=4)
        self.assertEqual(st, "INCOMPLETE")

    def test_gpu_count_from_data_not_constant(self):
        self.assertEqual(self.v("topo", S.topo_matrix(4), gpus=4)[0], "PASS")
        st, note = self.v("topo", S.topo_matrix(4), gpus=8)
        self.assertEqual(st, "FAIL")
        self.assertIn("shows 4 GPUs, expected 8", note)

    def test_expected_class_is_data(self):
        # a PCIe-only build declares its class in the hosts data
        m = S.topo_matrix(2, cell="NODE")
        self.assertEqual(self.v("topo", m, gpus=2, nvlink="NODE,SYS")[0],
                         "PASS")
        self.assertEqual(self.v("topo", m, gpus=2)[0], "FAIL")
        self.assertEqual(self.v("topo", m, gpus=2, nvlink=None)[0],
                         "INCOMPLETE")


class Stress(Base):

    def test_genuine_1827s_run_passes(self):
        st, note = self.v("stress", S.corpus_block(*S.W09,
                                                   "sudo nvsm stress-test"),
                          dur=1827.4)
        self.assertEqual(st, "PASS")
        self.assertIn("5/5 components PASS, 100% Complete", note)

    def test_hollow_09s_prompt_echo_fails(self):
        out = S.corpus_block("full-w08-hollow-stress", "fx-w08-prod-hv-01",
                             "sudo nvsm stress-test")
        st, note = self.v("stress", out, dur=0.9)
        self.assertEqual(st, "FAIL")
        self.assertIn("confirmation prompt", note)

    def test_already_running_cancellation_fails(self):
        out = S.corpus_block("wave1-w08-already-running", "fx-w08-prod-hv-01",
                             "sudo nvsm stress-test")
        st, note = self.v("stress", out, dur=1.2)
        self.assertEqual(st, "FAIL")
        self.assertIn("already running", note)

    def test_genuine_output_below_floor_fails(self):
        out = S.corpus_block(*S.W09, "sudo nvsm stress-test")
        st, note = self.v("stress", out, dur=900.0)
        self.assertEqual(st, "FAIL")
        self.assertIn("below the 1500s floor", note)

    def test_empty_output_exit_0_fails(self):
        st, note = self.v("stress", "", dur=1900.0)
        self.assertEqual(st, "FAIL")
        self.assertIn("never reached 100% Complete", note)

    def test_component_failed_fails(self):
        out = S.corpus_block("y10-truncated", "fx-y10-prod-hv-02",
                             "sudo nvsm stress-test")
        st, note = self.v("stress", out, dur=1827.3)
        self.assertEqual(st, "FAIL")
        self.assertIn("GPU Failed", note)

    def test_missing_component_line_fails(self):
        out = S.corpus_block(*S.W09, "sudo nvsm stress-test").replace(
            "PCIe           PASS", "")
        st, note = self.v("stress", out, dur=1827.4)
        self.assertEqual(st, "FAIL")
        self.assertIn("no result line for: PCIe", note)

    def test_uncleared_alert_refusal_is_skip(self):
        st, _n = self.v("stress", "Stress testing will not be done.\n")
        self.assertEqual(st, "SKIP")


class Inversions(Base):

    def test_health_requires_the_summary(self):
        self.assertEqual(self.v("nvsm-health", S.healthy_health_block())[0],
                         "PASS")
        self.assertEqual(self.v("nvsm-health", "")[0], "INCOMPLETE")
        self.assertEqual(self.v("nvsm-health", "some foreign text\n")[0],
                         "INCOMPLETE")

    def test_zero_unhealthy_line_is_not_a_failure(self):
        # pre-C, the word "unhealthy" in "0 out of N checks are unhealthy"
        # failed every clean host
        st, note = self.v("nvsm-health", S.healthy_health_block())
        self.assertEqual(st, "PASS")
        self.assertIn("260 of 260 checks healthy", note)

    def test_real_unhealthy_check_fails(self):
        st, note = self.v("nvsm-health", S.corpus_block(
            *S.W09, "sudo nvsm show health"))
        self.assertEqual(st, "FAIL")
        self.assertIn("Verify DIMM part number", note)

    def test_unknown_checks_are_incomplete(self):
        out = S.healthy_health_block().replace(
            "0 out of 260 checks are unknown", "2 out of 260 checks are unknown")
        self.assertEqual(self.v("nvsm-health", out)[0], "INCOMPLETE")

    def test_release_files_need_their_keys(self):
        self.assertEqual(self.v("dgx-release", "garbage\n")[0], "FAIL")
        self.assertEqual(self.v("dgx-release", S.corpus_block(
            *S.W09, "cat /etc/dgx-release"))[0], "PASS")
        self.assertEqual(self.v("os-release", "garbage\n")[0], "FAIL")
        self.assertEqual(self.v("os-release", 'NAME="Ubuntu"\n'),
                         ("PASS", "Ubuntu"))

    def test_dump_needs_its_archive_path(self):
        self.assertEqual(self.v("dump", "Health dump started\n")[0],
                         "INCOMPLETE")
        st, note = self.v("dump", S.corpus_block(*S.W09,
                                                 "sudo nvsm dump health"))
        self.assertEqual(st, "PASS")
        self.assertIn("/tmp/nvsm-health-", note)

    def test_smi_count_unparseable_is_incomplete(self):
        self.assertEqual(self.v("smi-query", "no count here")[0], "INCOMPLETE")

    def test_nccl_section_without_result_rows_fails(self):
        out = ("===== all_reduce_perf =====\n# Out of bounds values : 0 OK\n"
               "[all_reduce_perf exit 0]")
        st, note = self.v("nccl", out)
        self.assertEqual(st, "FAIL")
        self.assertIn("no result rows", note)

    def test_unknown_step_is_never_pass(self):
        step = self.vit.Step("mystery", "Mystery", ["true"])
        self.assertEqual(self.vit.verdict(step, 0, "fine", 8)[0],
                         "INCOMPLETE")

    def test_nonzero_exit_still_fails_first(self):
        st, _n = self.v("stress", S.corpus_block(*S.W09,
                                                 "sudo nvsm stress-test"),
                        rc=1, dur=1827.4)
        self.assertEqual(st, "FAIL")


if __name__ == "__main__":
    unittest.main()
