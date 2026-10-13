"""Caller-contract regression tests; these are not live model evaluations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import test_one_checkout as fixture

core = fixture.core


class AgentContract(unittest.TestCase):
    # Reuse fixture helpers, not its test methods (avoid counting tests twice).
    setUp = fixture.OneCheckoutTests.setUp
    git = fixture.OneCheckoutTests.git
    patch_for = fixture.OneCheckoutTests.patch_for
    land = fixture.OneCheckoutTests.land
    blob = fixture.OneCheckoutTests.blob
    metadata = fixture.OneCheckoutTests.metadata

    def selected(self):
        return self.patch_for("shared.py", self.base.replace("line 01\n", "A selected\n").encode())

    def cli(self, command, *extra):
        patch = Path(self.temp.name) / "selected.patch"
        patch.write_bytes(self.selected())
        return subprocess.run([sys.executable, str(fixture.SCRIPT), command,
                               "--repo", str(self.repo), "--patch", str(patch),
                               "--path", "shared.py", "--expected-parent", self.parent,
                               *extra], capture_output=True, timeout=30)

    def test_cli_reviewed_plan_roundtrip(self):
        planned = self.cli("plan")
        self.assertEqual(planned.returncode, 0)
        plan = Path(self.temp.name) / "plan.json"
        plan.write_bytes(planned.stdout)
        result = self.cli("commit", "--plan-file", str(plan), "--message", "A alone")
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertTrue(receipt["reviewed_plan_verified"])
        self.assertTrue(receipt["ref_update_confirmed"])
        self.assertFalse(receipt["automatic_retry_allowed"])
        self.assertEqual(receipt["next_action"], "done_preserve_other_work")

    def test_same_oid_branch_switch_between_calls_refuses(self):
        patch = self.selected()
        plan = self.land(patch, dry_run=True)
        self.git("branch", "other")
        self.git("symbolic-ref", "HEAD", "refs/heads/other")
        before = self.metadata()
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch, reviewed_plan=plan)
        self.assertEqual(caught.exception.code, "plan_mismatch")
        self.assertIn("branch", str(caught.exception))
        self.assertEqual(self.metadata(), before)

    def test_changed_patch_between_calls_refuses_before_writes(self):
        plan = self.land(self.selected(), dry_run=True)
        before = self.metadata()
        with self.assertRaises(core.Refusal) as caught:
            self.land(self.patch_for("shared.py", b"different patch\n"), reviewed_plan=plan)
        self.assertEqual(caught.exception.code, "plan_mismatch")
        self.assertIn("patch_sha256", str(caught.exception))
        self.assertEqual(self.metadata(), before)

    def test_changed_declared_paths_refuses(self):
        patch = self.selected()
        plan = self.land(patch, dry_run=True)
        before = self.metadata()
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch, ["foreign.txt"], reviewed_plan=plan)
        self.assertEqual(caught.exception.code, "plan_mismatch")
        self.assertEqual(self.metadata(), before)

    def test_plan_for_different_repository_refuses(self):
        patch = self.selected()
        plan = self.land(patch, dry_run=True)
        before = self.metadata()
        with self.assertRaises(core.Refusal) as caught:
            core.commit_patch(repo=self.repo.parent, patch=patch, paths=["shared.py"],
                              expected_parent=self.parent, message="A", reviewed_plan=plan)
        self.assertEqual(caught.exception.code, "plan_mismatch")
        self.assertIn("repository", str(caught.exception))
        self.assertEqual(self.metadata(), before)

    def test_changed_tree_binding_refuses_without_branch_or_index_change(self):
        patch = self.selected()
        plan = self.land(patch, dry_run=True)
        plan["tree"] = "0" * 40  # Deliberately corrupted receipt, not a race simulation.
        original = (self.repo / ".git/index").read_bytes()
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch, reviewed_plan=plan)
        self.assertEqual(caught.exception.code, "plan_mismatch")
        self.assertIn("tree", str(caught.exception))
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.decode().strip(), self.parent)
        self.assertEqual((self.repo / ".git/index").read_bytes(), original)

    def test_foreign_staging_after_plan_is_rechecked_and_preserved(self):
        patch = self.selected()
        plan = self.land(patch, dry_run=True)
        staged = self.base.replace("line 12\n", "B staged\n")
        (self.repo / "shared.py").write_text(staged)
        self.git("add", "shared.py")
        working = staged.replace("line 01\n", "A selected\n").replace("line 23\n", "C unstaged\n")
        (self.repo / "shared.py").write_text(working)
        result = self.land(patch, reviewed_plan=plan)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(result["preserved_staged_paths"], ["shared.py"])
        self.assertEqual(self.blob("", "shared.py"), staged.encode())
        self.assertEqual((self.repo / "shared.py").read_text(), working)
        self.assertEqual(self.blob("HEAD", "shared.py"), self.base.replace("line 01\n", "A selected\n").encode())

    def test_plan_is_not_replay_or_idempotency_token(self):
        patch = self.selected()
        plan = self.land(patch, dry_run=True)
        self.land(patch, reviewed_plan=plan)
        before = self.metadata()
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch, reviewed_plan=plan)
        self.assertEqual(caught.exception.code, "stale_parent")
        self.assertEqual(self.metadata(), before)

    def test_invalid_plan_shapes_refuse_without_writes(self):
        good = self.land(self.selected(), dry_run=True)
        invalid = [None, [], {}, {**good, "status": "committed"}, {**good, "dry_run": False},
                   {**good, "repository": "relative"}, {**good, "parent": "short"},
                   {**good, "branch": "main"}, {**good, "tree": "not-an-object"},
                   {**good, "patch_sha256": "x" * 64}, {**good, "changed_paths": [False]},
                   {**good, "changed_paths": ["../outside"]}, {**good, "changed_paths": []},
                   {k: v for k, v in good.items() if k != "repository"}]
        before = self.metadata()
        for plan in invalid:
            with self.subTest(plan=plan), self.assertRaises(core.Refusal) as caught:
                core._validate_plan(plan)
            self.assertEqual(caught.exception.code, "invalid_plan")
        self.assertEqual(self.metadata(), before)

    def test_cli_invalid_plan_json_is_structured(self):
        path = Path(self.temp.name) / "invalid.json"
        for data in (b'{', b'[]', b'{"schema": 1, "schema": 2}', b'\xff'):
            path.write_bytes(data)
            result = self.cli("commit", "--plan-file", str(path), "--message", "A")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            receipt = json.loads(result.stderr)
            self.assertEqual(receipt["code"], "invalid_plan")
            self.assertEqual(receipt["next_action"], "review_refusal")
            self.assertFalse(receipt["automatic_retry_allowed"])

    def test_cli_disallows_abbreviated_options(self):
        result = self.cli("plan", "--expected-p", self.parent)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["code"], "arguments")

    def test_cli_bound_dry_run_does_not_mutate_metadata(self):
        plan = Path(self.temp.name) / "plan.json"
        plan.write_bytes(self.cli("plan").stdout)
        before = self.metadata()
        result = self.cli("commit", "--dry-run", "--plan-file", str(plan))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "planned")
        self.assertTrue(json.loads(result.stdout)["reviewed_plan_verified"])
        self.assertEqual(self.metadata(), before)

    def broken_pipe(self, command, *, unbuffered=False, close_stderr=False):
        patch = Path(self.temp.name) / "selected.patch"
        patch.write_bytes(self.selected())
        read, write = os.pipe()
        os.close(read)
        env = dict(os.environ)
        env.pop("PYTHONUNBUFFERED", None)
        if unbuffered:
            env["PYTHONUNBUFFERED"] = "1"
        try:
            return subprocess.run(
                [sys.executable, str(fixture.SCRIPT), command, "--repo", str(self.repo),
                 "--patch", str(patch), "--path", "shared.py", "--expected-parent", self.parent,
                 "--message", "A alone"], stdout=write,
                stderr=write if close_stderr else subprocess.PIPE, env=env, timeout=30)
        finally:
            os.close(write)

    def assert_landed_with_fallback(self, result):
        self.assertEqual(result.returncode, 3, result.stderr)
        receipt = json.loads(result.stderr)
        self.assertEqual(receipt["status"], "committed")
        self.assertEqual(receipt["receipt_delivery"], "stderr_fallback")
        self.assertEqual(receipt["next_action"], "inspect_receipt_delivery")
        self.assertTrue(receipt["ref_update_confirmed"])
        self.assertFalse(receipt["automatic_retry_allowed"])
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.decode().strip(), receipt["new_commit"])
        self.assertNotEqual(receipt["new_commit"], self.parent)
        self.assertEqual(self.blob("HEAD", "shared.py"), self.base.replace("line 01\n", "A selected\n").encode())

    def test_closed_stdout_buffered_does_not_misreport_refusal(self):
        self.assert_landed_with_fallback(self.broken_pipe("commit"))

    def test_closed_stdout_unbuffered_does_not_misreport_refusal(self):
        self.assert_landed_with_fallback(self.broken_pipe("commit", unbuffered=True))

    def test_closed_both_output_streams_after_landing_still_exits_three(self):
        result = self.broken_pipe("commit", close_stderr=True)
        self.assertEqual(result.returncode, 3)
        self.assertNotEqual(self.git("rev-parse", "HEAD").stdout.decode().strip(), self.parent)

    def test_closed_plan_output_refuses_without_repository_changes(self):
        before = self.metadata()
        result = self.broken_pipe("plan")
        self.assertEqual(result.returncode, 2)
        receipt = json.loads(result.stderr)
        self.assertEqual(receipt["status"], "refused")
        self.assertEqual(receipt["code"], "receipt_output_failed")
        self.assertFalse(receipt["ref_update_confirmed"])
        self.assertEqual(self.metadata(), before)

    def test_cli_action_table_does_not_treat_candidate_as_confirmation(self):
        for status, confirmed, action in (
            ("refused", False, "review_refusal"),
            ("planned", False, "review_patch_and_plan"),
            ("committed", True, "done_preserve_other_work"),
            ("landing_uncertain", False, "inspect_branch_and_reflog"),
            ("landed_unsettled", True, "inspect_commit_and_index"),
        ):
            with self.subTest(status=status):
                result = core.caller_receipt({"status": status, "new_commit": "c" * 40})
                self.assertEqual(result["ref_update_confirmed"], confirmed)
                self.assertEqual(result["next_action"], action)
                self.assertFalse(result["automatic_retry_allowed"])


if __name__ == "__main__":
    unittest.main()
