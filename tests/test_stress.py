"""Small deterministic corpus; expensive and destructive schedules are opt-in."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import argparse
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("stress", ROOT / "scripts/stress.py")
assert SPEC and SPEC.loader
stress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stress)


@unittest.skipUnless(os.name == "posix" and shutil.which("git"), "requires POSIX and Git")
class StressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oc-stress-unit-", dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tool, _ = stress.freeze_runtime(stress.TOOL, self.root)
        self.counter = 0

    def exercise(self, function, *args, **kwargs):
        self.counter += 1
        try:
            row = function(self.root / str(self.counter), self.tool, *args, **kwargs)
        except stress.UnsupportedFixture as error:
            self.skipTest(str(error))
        self.assertEqual(row["classification"], "pass", row)
        return row

    def test_independent_empty_blob_hash(self):
        self.assertEqual(stress.blob_id(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")
        self.assertEqual(len(stress.blob_id(b"", "sha256")), 64)

    def test_seed_reproduces_models(self):
        self.assertEqual(stress.generated_models(27), stress.generated_models(27))
        self.assertNotEqual(stress.generated_models(27), stress.generated_models(28))

    def test_generated_byte_and_mode_models(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                self.exercise(stress.generated_case, seed)

    def test_raw_cli_argument_roundtrip(self):
        self.exercise(stress.compatibility_case, "cli_portable_names")

    def test_adversarial_cli_filename_capabilities(self):
        for kind in ("cli_unicode_names", "cli_case_names", "cli_byte_names"):
            with self.subTest(capability=kind):
                self.exercise(stress.compatibility_case, kind)

    def test_skips_are_not_claimed_as_runtime_stress(self):
        self.assertEqual(stress.summary_classification([]), "no_cases_exercised")
        self.assertEqual(stress.summary_classification([{"classification": "unsupported"}]), "no_cases_exercised")
        self.assertEqual(stress.summary_classification([{"classification": "pass"}, {"classification": "skip"}]), "pass_with_skips")
        self.assertEqual(stress.summary_classification([{"classification": "harness_error"}]), "attention_required")

    def test_empty_benchmark_reports_zero_samples(self):
        self.assertEqual(stress.benchmark_summary([])["successful_samples"], 0)
        self.assertEqual(stress.benchmark_summary([])["measures"], {})

    def test_runtime_snapshot_retains_executed_bytes_after_source_edit(self):
        source = self.root / "source.py"
        source.write_bytes(b"original runtime bytes\n")
        target_root = self.root / "snapshot"
        target_root.mkdir()
        snapshot, original = stress.freeze_runtime(source, target_root)
        source.write_bytes(b"changed source bytes\n")
        self.assertEqual(snapshot.read_bytes(), original)
        self.assertNotEqual(snapshot.read_bytes(), source.read_bytes())

    def test_uncertain_native_commit_is_attention_and_corruption_still_fails(self):
        fixture = stress.Fixture(self.root / "uncertain", *stress.generated_models(2))
        spec = importlib.util.spec_from_file_location("uncertain_target", self.tool)
        core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core)
        read_ack = core._read_ack

        def lose_native_ack(process, timeout):
            ack = read_ack(process, timeout)
            if ack == b"commit: ok\n":
                raise core.Refusal("git_timeout", "Synthetic acknowledgement lost after native commit")
            return ack

        with mock.patch.dict(os.environ, fixture.env, clear=True), \
                mock.patch.object(tempfile, "tempdir", str(fixture.root)), \
                mock.patch.object(core, "_read_ack", lose_native_ack):
            receipt = core.commit_patch(repo=fixture.repo, patch=fixture.patch.read_bytes(),
                                        paths=fixture.changed, expected_parent=fixture.parent,
                                        message="Synthetic native lost acknowledgement")
        result = {"receipt": receipt, "exit_code": 3}

        def classify():
            oracle = fixture.check(receipt, full=True)
            oracle["checks"]["committed_receipt"] = receipt["status"] == "committed"
            return stress.successful_case_outcome(fixture, result, oracle)

        row = classify()
        self.assertEqual(row["classification"], "operational_uncertainty", row)
        self.assertTrue(row["oracle"]["index_original_bytes_exact"])
        self.assertEqual(stress.summary_classification([row]), "attention_required")
        self.assertEqual(row["worker"]["receipt"]["status"], "landing_uncertain")
        self.assertFalse(row["successful_settlement_checks"]["committed_receipt"])
        index = fixture.repo / ".git/index"
        original_index = index.read_bytes()
        fixture.g("update-index", "--force-remove", "--", "foreign-new")
        self.assertEqual(classify()["classification"], "invariant_failure")
        index.write_bytes(original_index)
        working = fixture.repo / "shared.txt"
        original_working = working.read_bytes()
        working.write_bytes(b"unexpected working bytes\n")
        self.assertEqual(classify()["classification"], "invariant_failure")
        working.write_bytes(original_working)
        candidate = receipt["new_commit"]
        wrong_tree = fixture.g("rev-parse", fixture.parent + "^{tree}").stdout.decode().strip()
        wrong_head = fixture.g("commit-tree", wrong_tree, "-p", fixture.parent,
                               data=b"Synthetic wrong tree").stdout.decode().strip()
        fixture.g("update-ref", "HEAD", wrong_head, candidate)
        self.assertEqual(classify()["classification"], "invariant_failure")
        fixture.g("update-ref", "HEAD", candidate, wrong_head)
        fixture.g("update-ref", "refs/heads/other", candidate)
        fixture.g("symbolic-ref", "HEAD", "refs/heads/other")
        self.assertEqual(classify()["classification"], "invariant_failure")
        fixture.g("symbolic-ref", "HEAD", fixture.before["branch"])
        receipt["ref_update_confirmed"] = True
        self.assertEqual(classify()["classification"], "invariant_failure")
        receipt["ref_update_confirmed"] = False
        self.assertEqual(classify()["classification"], "operational_uncertainty")
        receipt.update(status="landed_unsettled", ref_update_confirmed=True)
        self.assertEqual(classify()["classification"], "operational_uncertainty")
        for path in fixture.changed:
            if fixture.staged.get(path) == fixture.base.get(path):
                entry = fixture.candidate.get(path)
                if entry:
                    fixture.g("update-index", "--add", "--cacheinfo", entry[0],
                              stress.blob_id(entry[1]), path)
                else:
                    fixture.g("update-index", "--force-remove", "--", path)
        settled = classify()
        self.assertEqual(settled["classification"], "operational_uncertainty", settled)
        self.assertFalse(settled["oracle"]["index_original_bytes_exact"])
        fixture.g("update-index", "--assume-unchanged", "--", "foreign-new")
        self.assertEqual(classify()["classification"], "invariant_failure")

    def test_fixture_timeout_is_reported_without_claiming_runtime_exercise(self):
        args = argparse.Namespace(scratch=self.root, tool=self.tool, mode="generated",
                                  output=self.root / "timeout.json", seed=0, seeds=1,
                                  name_profile="portable", timeout=15, progress=False, retain=True)
        with mock.patch.object(stress, "git", side_effect=stress.FixtureGitTimeout("synthetic setup deadline")), \
                mock.patch.object(stress, "Client") as client, \
                mock.patch.object(stress, "WORKER", stress.WORKER):
            result = stress.run(args)
        client.assert_not_called()
        self.assertEqual(result["classification"], "attention_required")
        self.assertEqual(result["runtime_cases_passed"], 0)
        self.assertTrue(all(row["classification"] == "fixture_timeout" for row in result["results"]))

    def test_full_index_versions_and_foreign_flag(self):
        for version in (2, 3, 4):
            with self.subTest(version=version):
                self.exercise(stress.compatibility_case, f"index_v{version}")

    def test_explicit_unsupported_inputs_leave_state_intact(self):
        for case in ("split_index", "sparse_policy", "submodule_edit", "drive_like_posix_name"):
            with self.subTest(case=case):
                self.exercise(stress.compatibility_case, case)

    def test_simultaneous_callers_have_exactly_one_winner(self):
        for callers in (2, 4, 8):
            with self.subTest(callers=callers):
                self.exercise(stress.simultaneous_case, callers)

    def test_real_cli_and_git_add_competitors_cannot_steal_lock(self):
        for barrier in ("snapshot_contention", "settlement_contention"):
            with self.subTest(barrier=barrier):
                self.exercise(stress.schedule_case, barrier)

    def test_external_branch_winner_is_never_rewound(self):
        self.exercise(stress.schedule_case, "branch_wins")

    def test_native_prepared_transaction_blocks_ref_and_head_writers(self):
        self.exercise(stress.schedule_case, "prepared_contention")

    def test_advance_after_acknowledgement_requires_unsettled_receipt(self):
        self.exercise(stress.schedule_case, "post_ack_advance")

    def test_positive_and_negative_controls_do_not_equate_contracts(self):
        for arm in ("one_checkout", "native_path_commit", "fresh_worktree"):
            with self.subTest(arm=arm):
                result = self.exercise(stress.compare_case, arm)
                self.assertEqual(result["checks"]["candidate_tree_exact"], arm != "native_path_commit")
                self.assertEqual(result["original_branch_advanced"], arm != "fresh_worktree")

    def test_many_selected_entries_keep_exact_tree_and_index(self):
        result = self.exercise(stress.benchmark_case, 512, 400, .5)
        self.assertGreater(result["object_growth"]["files"], 0)
        self.assertGreater(result["worker"]["metrics"]["index_lock_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
