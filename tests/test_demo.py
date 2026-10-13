"""Executable first-run contract, fair Git controls and safe temporary routing."""
from __future__ import annotations
import importlib.util
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
        self.assertFalse(ordinary["selected_commit"])
        self.assertFalse(ordinary["foreign_staged_file_excluded"])
        self.assertFalse(file_only["selected_commit"])
        self.assertTrue(file_only["foreign_staged_file_excluded"])
        self.assertTrue(native["selected_commit"])
        self.assertTrue(native["shared_index_blob_preserved"])
        self.assertTrue(native["worktree_preserved"])

    def test_human_output_has_limits_and_real_refusal(self) -> None:
        text = module.render_human(self.result)
        self.assertIn(self.result["commit"], text)
        self.assertIn("authorship was not inferred", text)
        self.assertIn("Sequential stale-parent", text)
        self.assertIn("not a speed benchmark", text)
        self.assertIn("an ordinary commit of that snapshot can undo A", text)
        self.assertEqual(self.result["refusal"]["exit_code"], 2)

    def test_default_cli_remains_json(self) -> None:
        r = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "scripts/demo.py")],
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"], "demo_passed")

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
                module.demo()
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
