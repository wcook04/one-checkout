"""Executable comparison has explicit success and negative-control oracles."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/compare_workflows.py'
SPEC = importlib.util.spec_from_file_location('workflow_comparison', SCRIPT)
assert SPEC and SPEC.loader
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)


class WorkflowComparisonTests(unittest.TestCase):
    def test_comparison_distinguishes_contracts_and_lifecycle_failures(self) -> None:
        result = comparison.comparison(repeats=1)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(len(result['runs']), 8)
        arms = {r['arm']: r for r in result['runs'] if r['scenario'] == 'same_file_ABC'}
        self.assertFalse(arms['git_pathspec']['checks']['selected_commit_contains_only_A'])
        self.assertTrue(arms['git_private_index']['checks']['selected_commit_contains_only_A'])
        self.assertTrue(arms['git_worktree']['checks']['selected_commit_contains_only_A'])
        self.assertFalse(arms['git_worktree']['checks']['original_branch_advanced'])
        self.assertTrue(arms['one_checkout']['checks']['original_branch_advanced'])
        cases = {r['scenario']: r for r in result['handoffs_and_controls']}
        self.assertFalse(cases['plain_commit_handoff']['A_survives_second_commit'])
        self.assertTrue(cases['reviewed_patch_handoff']['A_survives_second_commit'])
        self.assertTrue(cases['reviewed_patch_handoff']['retained_index_still_omits_A'])
        self.assertTrue(cases['candidate_dependency_negative_control']['checks']['candidate_check_fails'])
        policies = {r['scenario']: r for r in result['branch_policy_controls']}
        self.assertEqual(len(policies), 17)
        native = policies['native_hook/primary_feature']
        self.assertTrue(native['ordinary_hook_executed'])
        self.assertNotEqual(native['return_code'], 0)
        bypass = policies['one_checkout_without_admission/primary_feature']
        self.assertEqual(bypass['receipt']['status'], 'committed')
        self.assertFalse(bypass['ordinary_hook_executed'])
        self.assertFalse(bypass['project_metadata_policy_evaluated'])
        denied = policies['project_admission/primary_feature']
        self.assertFalse(denied['admission']['allowed'])
        self.assertFalse(denied['commit_invoked'])
        self.assertEqual(denied['before'], denied['after'])
        for topology in ('primary_detached', 'linked_detached'):
            with self.subTest(topology=topology):
                self.assertNotEqual(policies[f'native_hook/{topology}']['return_code'], 0)
                bypass = policies[f'one_checkout_without_admission/{topology}']
                self.assertEqual(bypass['receipt']['status'], 'committed')
                self.assertEqual(bypass['receipt']['head_mode'], 'detached')
                denied = policies[f'project_admission/{topology}']
                self.assertFalse(denied['admission']['allowed'])
                self.assertFalse(denied['commit_invoked'])
                self.assertEqual(denied['before'], denied['after'])
        for topology in ('primary_main', 'linked_feature'):
            with self.subTest(topology=topology):
                allowed = policies[f'project_admission/{topology}']
                self.assertTrue(allowed['admission']['allowed'])
                self.assertEqual(allowed['receipt']['status'], 'committed')
                self.assertTrue(allowed['receipt']['reviewed_plan_verified'])
                self.assertFalse(allowed['ordinary_hook_executed'])
                switched = policies[f'project_admission/{topology}/same_oid_branch_switch']
                self.assertEqual(switched['return_code'], 2)
                self.assertEqual(switched['receipt']['code'], 'plan_mismatch')
                self.assertEqual(switched['plan']['parent'], switched['before']['head'])
                self.assertNotEqual(switched['plan']['branch'], switched['before']['branch'])
                self.assertEqual(switched['before'], switched['after'])

    def test_repeat_limits_are_checked(self) -> None:
        for value in (-1, 0, 26):
            with self.subTest(repeats=value), self.assertRaises(ValueError):
                comparison.comparison(repeats=value)

    def test_documented_named_policy_refuses_detached_worktrees(self) -> None:
        guide = (SCRIPT.parent.parent / 'docs/choosing_a_workflow.md').read_text()
        opener = "python3 - \"$ONE_CHECKOUT\" \"$REPO\" \"$PATCH\" \"$PLAN\" <<'PY'\n"
        snippet = guide.split(opener, 1)[1].split('\nPY\n', 1)[0]
        with tempfile.TemporaryDirectory(dir=os.environ.get('ONE_CHECKOUT_TEST_TMPDIR')) as raw:
            for topology in ('primary_detached', 'linked_detached'):
                with self.subTest(topology=topology):
                    f = comparison.Fixture(Path(raw) / topology, comparison.TOOL)
                    result = comparison.branch_policy_control(f, 'project_admission', topology)
                    before = comparison._policy_snapshot(f)
                    invocation = f.run([sys.executable, '-c', snippet, str(f.tool), str(f.repo),
                                        str(f.patch), str(f.root / 'reviewed-plan.json')], check=False)
                    self.assertNotEqual(invocation.returncode, 0, invocation.stdout)
                    self.assertIn(b'named branches only', invocation.stderr)
                    self.assertEqual(before, comparison._policy_snapshot(f))
                    self.assertFalse(result['commit_invoked'])

    def test_unknown_arm_is_not_silently_skipped(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get('ONE_CHECKOUT_TEST_TMPDIR')) as raw:
            fixture = comparison.Fixture(Path(raw) / 'fixture', comparison.TOOL)
            with self.assertRaises(ValueError):
                comparison.compare_arm(fixture, 'unknown', mixed=True)


if __name__ == '__main__':
    unittest.main()
