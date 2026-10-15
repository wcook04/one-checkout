"""Executable first-run contract, fair Git controls and safe temporary routing."""
from __future__ import annotations
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("demo_under_test", ROOT / "skills/one-checkout/scripts/demo.py")
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = module.demo(compare=True)

    def test_exact_three_views_and_observed_diff(self) -> None:
        r = self.result
        self.assertEqual(r["status"], "demo_passed")
        self.assertTrue(all(r["checks"].values()))
        self.assertEqual(r["after"]["head"], module.OWNED.decode())
        self.assertEqual(r["after"]["index"], module.STAGED.decode())
        self.assertEqual(r["after"]["worktree"], module.WORKING.decode())
        self.assertIn("-A = 1", r["cached_diff"])
        self.assertIn("+A = 0", r["cached_diff"])
        self.assertNotIn("+C = 3", r["cached_diff"])

    def test_controls_include_capable_native_git(self) -> None:
        ordinary, file_only, native = self.result["controls"]
        self.assertEqual(self.result["control_assessment"]["status"], "passed")
        self.assertEqual(self.result["control_assessment"]["mismatches"], [])
        self.assertEqual(self.result["control_assessment"]["expected"], module.CONTROL_EXPECTATIONS)
        self.assertFalse(ordinary["selected_commit"])
        self.assertFalse(ordinary["foreign_staged_file_excluded"])
        self.assertFalse(file_only["selected_commit"])
        self.assertTrue(file_only["foreign_staged_file_excluded"])
        self.assertTrue(native["selected_commit"])
        self.assertTrue(native["shared_index_blob_preserved"])
        self.assertTrue(native["worktree_preserved"])

    def test_declared_false_values_and_complete_control_list_are_required(self) -> None:
        controls = copy.deepcopy(self.result["controls"])
        controls[0]["selected_commit"] = True
        assessment = module.assess_git_controls(controls)
        self.assertEqual(assessment["status"], "failed")
        self.assertIn({"index": 0, "command": "git commit", "field": "selected_commit",
                       "expected": False, "observed": True}, assessment["mismatches"])
        self.assertEqual(module.assess_git_controls(self.result["controls"][:2])["status"], "failed")

    def test_native_control_failure_does_not_hide_primary_or_follow_on_results(self) -> None:
        controls = copy.deepcopy(self.result["controls"])
        controls[2]["selected_commit"] = False  # Synthetic mutation, not an observed Git failure.
        with mock.patch.object(module, "compare_git", return_value=controls):
            result = module.demo(compare=True)
        self.assertEqual(result["status"], "demo_comparison_failed")
        self.assertTrue(all(result["checks"].values()))
        self.assertTrue(all(result["follow_on_control"]["checks"].values()))
        self.assertIn({"index": 2, "command": "native private index", "field": "selected_commit",
                       "expected": True, "observed": False},
                      result["control_assessment"]["mismatches"])
        human = module.render_human(result)
        self.assertIn("GIT CONTROL COMPARISON FAILED", human)
        self.assertNotIn("Native private-index Git also selects A", human)
        output = io.StringIO()
        with mock.patch.object(module, "demo", return_value=result), \
                mock.patch.object(sys, "argv", ["demo.py", "--compare-git"]), \
                contextlib.redirect_stdout(output):
            self.assertEqual(module.main(), 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "demo_comparison_failed")

    def test_follow_on_control_observes_actual_reversal_in_separate_fixture(self) -> None:
        r = self.result
        control = r["follow_on_control"]
        self.assertTrue(all(control["checks"].values()))
        self.assertEqual(control["before_ordinary_commit"]["head"], module.OWNED.decode())
        self.assertEqual(control["after_ordinary_commit"]["head"], module.STAGED.decode())
        self.assertNotEqual(control["selected_commit"], control["ordinary_commit"])
        self.assertNotEqual(control["selected_commit"], r["commit"])
        self.assertEqual(r["after"]["head"], module.OWNED.decode())
        self.assertTrue(r["checks"]["preserved_staging_risk_is_reported"])

    def test_missing_staging_warning_cannot_get_a_demo_pass(self) -> None:
        original_execute = module.Fixture.execute

        def without_warning(fixture, argv, **kwargs):
            result = original_execute(fixture, argv, **kwargs)
            if len(argv) > 3 and argv[3] == "commit" and result.returncode == 0:
                receipt = json.loads(result.stdout)
                receipt["warnings"] = []
                return subprocess.CompletedProcess(result.args, result.returncode,
                    json.dumps(receipt).encode(), result.stderr)
            return result

        with tempfile.TemporaryDirectory(dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as raw:
            with mock.patch.dict(os.environ, {"ONE_CHECKOUT_TEST_TMPDIR": raw}), \
                    mock.patch.object(module.Fixture, "execute", without_warning):
                with self.assertRaisesRegex(RuntimeError, "preserved_staging_risk_is_reported"):
                    module.demo(compare=True)
            self.assertEqual(list(Path(raw).iterdir()), [])

    def test_human_output_has_limits_and_real_refusal(self) -> None:
        text = module.render_human(self.result)
        self.assertIn(self.result["commit"], text)
        self.assertIn("authorship was not inferred", text)
        self.assertIn("Sequential stale-parent", text)
        self.assertIn("not a speed benchmark", text)
        self.assertIn("an ordinary commit of that snapshot can undo A", text)
        self.assertIn("ordinary commit actually reverted A", text)
        self.assertEqual(self.result["refusal"]["exit_code"], 2)

    def test_default_cli_remains_json(self) -> None:
        r = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "scripts/demo.py")],
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout)
        self.assertEqual(result["status"], "demo_passed")
        self.assertNotIn("control_assessment", result)

    def test_missing_tool_fails_without_success_output(self) -> None:
        r = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "scripts/demo.py"),
                            "--tool", str(ROOT / "absent-runtime")], capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertFalse(r.stdout)
        self.assertEqual(json.loads(r.stderr)["status"], "demo_failed")

    def test_inherited_git_controls_cannot_redirect_fixture(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as raw:
            outside = Path(raw) / "outside"
            outside.mkdir()
            marker = outside / "index"
            marker.write_bytes(b"do not touch\n")
            env = dict(os.environ, GIT_DIR=str(outside), GIT_INDEX_FILE=str(marker))
            r = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "scripts/demo.py")],
                               env=env, capture_output=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(marker.read_bytes(), b"do not touch\n")
            self.assertEqual(list(outside.iterdir()), [marker])

    def test_routed_fixtures_are_removed(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as raw:
            with mock.patch.dict(os.environ, {"ONE_CHECKOUT_TEST_TMPDIR": raw}):
                module.demo(compare=True)
            self.assertEqual(list(Path(raw).iterdir()), [])

    def test_failed_runtime_does_not_get_a_pass_and_fixture_is_removed(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as raw:
            root = Path(raw)
            fake = root / "fake.py"
            fake.write_text("import json\nprint(json.dumps({'status': 'planned'}))\n")
            with mock.patch.dict(os.environ, {"ONE_CHECKOUT_TEST_TMPDIR": raw}):
                with self.assertRaises(RuntimeError):
                    module.demo(fake)
            self.assertEqual(list(root.iterdir()), [fake])
