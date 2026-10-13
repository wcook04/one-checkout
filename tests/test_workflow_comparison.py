"""Executable comparison has explicit success and negative-control oracles."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
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

    def test_repeat_limits_are_checked(self) -> None:
        for value in (-1, 0, 26):
            with self.subTest(repeats=value), self.assertRaises(ValueError):
                comparison.comparison(repeats=value)

    def test_unknown_arm_is_not_silently_skipped(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get('ONE_CHECKOUT_TEST_TMPDIR')) as raw:
            fixture = comparison.Fixture(Path(raw) / 'fixture', comparison.TOOL)
            with self.assertRaises(ValueError):
                comparison.compare_arm(fixture, 'unknown', mixed=True)


if __name__ == '__main__':
    unittest.main()
