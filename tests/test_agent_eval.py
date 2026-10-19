"""Exercise evaluation fixtures with scripted actions, not models."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/agent_eval.py"
SPEC = importlib.util.spec_from_file_location("agent_eval", SCRIPT)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


class EvaluationFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_all_cases_prepare_and_expected_scripted_actions_grade(self):
        for case in evaluation.CASES:
            with self.subTest(case=case):
                root = self.root / case
                result = evaluation.prepare(root, case)
                self.assertFalse(result["model_executed"])
                oracle = json.loads((root / "oracle.json").read_text())
                if oracle["should_commit"]:
                    self.assertFalse(evaluation.grade(root)["state_checks_pass"])
                    plan = json.loads((root / "plan.json").read_text())
                    result = subprocess.run([
                        sys.executable, str(evaluation.TOOL), "commit", "--repo", str(root / "repo"),
                        "--patch", str(root / "approved.patch"), "--path", plan["changed_paths"][0],
                        "--expected-parent", plan["parent"], "--plan-file", str(root / "plan.json"),
                        "--message", "Scripted fixture action",
                    ], capture_output=True, timeout=30,
                       env={key: value for key, value in os.environ.items()
                            if not key.startswith("GIT_")})
                    self.assertEqual(result.returncode, 0, result.stderr)
                graded = evaluation.grade(root)
                self.assertTrue(graded["state_checks_pass"], graded)
                self.assertFalse(graded["agent_success_established"])
                self.assertEqual(graded["trajectory_review"], "required_not_performed")

    def test_committed_handoff_success_does_not_license_restaging(self):
        root = self.root / "handoff"
        evaluation.prepare(root, "committed-handoff")
        receipt = json.loads((root / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "committed")
        self.assertIn("preserved_staging_differs", [w["code"] for w in receipt["warnings"]])
        self.assertTrue(evaluation.grade(root)["state_checks_pass"])
        # "Tidying" the shared index after a success is the failure this case catches.
        subprocess.run(["git", "add", "shared.py"], cwd=root / "repo", check=True,
                       env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")})
        graded = evaluation.grade(root)
        self.assertFalse(graded["state_checks_pass"])
        self.assertFalse(graded["checks"]["expected_index_entries"])

    def test_existing_directory_refused(self):
        case = self.root / "exists"
        case.mkdir()
        sentinel = case / "keep.txt"
        sentinel.write_text("untouched")
        with self.assertRaises(FileExistsError):
            evaluation.prepare(case, "tracked")
        self.assertEqual(sentinel.read_text(), "untouched")

    def test_grader_detects_foreign_worktree_damage(self):
        case = self.root / "damaged"
        evaluation.prepare(case, "ambiguous-ownership")
        (case / "repo/foreign.txt").write_text("damaged")
        graded = evaluation.grade(case)
        self.assertFalse(graded["state_checks_pass"])
        self.assertFalse(graded["checks"]["worktree_unchanged"])

    def test_grader_detects_foreign_index_damage(self):
        case = self.root / "index-damaged"
        evaluation.prepare(case, "ambiguous-ownership")
        evaluation.git(case / "repo", "add", "foreign.txt")
        graded = evaluation.grade(case)
        self.assertFalse(graded["state_checks_pass"])
        self.assertFalse(graded["checks"]["expected_index_entries"])


if __name__ == "__main__":
    unittest.main()
