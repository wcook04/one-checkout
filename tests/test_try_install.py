"""Both installed runtimes must execute; manifest or --help checks are insufficient."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("try_install_under_test", ROOT / "scripts/try_install.py")
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class InstallDemoTests(unittest.TestCase):
    def test_installed_copies_match_and_run_then_disappear(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as raw:
            with mock.patch.dict(os.environ, {"ONE_CHECKOUT_TEST_TMPDIR": raw}):
                result = module.verify_install()
            self.assertEqual(list(Path(raw).iterdir()), [])
        self.assertEqual(result["status"], "install_demo_passed")
        self.assertEqual([(r["scope"], r["agent"]) for r in result["agents"]],
                         [("project", "codex"), ("project", "claude"),
                          ("user", "codex"), ("user", "claude")])
        self.assertEqual(result["installed_copies_tested"], 4)
        self.assertTrue(result["user_scope_used_isolated_home"])
        self.assertTrue(result["reinstall_unchanged"])
        self.assertTrue(result["source_snapshot_removed_before_checks"])
        self.assertFalse(result["global_install_performed"])
        self.assertFalse(result["live_agent_discovery_tested"])
        for agent in result["agents"]:
            self.assertTrue(agent["hashes_match"])
            self.assertTrue(all(agent["demo_checks"].values()))
            self.assertEqual(agent["doctor_status"], "doctor_passed")

    def test_cli_human_names_the_limit(self) -> None:
        r = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "scripts/try_install.py"), "--human"],
                           capture_output=True, timeout=90)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"No global installation or live Claude Code/Codex session", r.stdout)

    def test_bad_source_is_not_success(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as raw:
            with self.assertRaises(OSError):
                module.verify_install(Path(raw))

    def test_invalid_inventory_is_rejected_before_installation(self) -> None:
        for inventory in (["../outside"], ["/absolute"], ["SKILL.md", "SKILL.md"],
                          ["./SKILL.md"], ["bundle.json"], [3], []):
            with self.subTest(inventory=inventory), tempfile.TemporaryDirectory(
                    dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as raw:
                source = Path(raw) / "skills/one-checkout"
                source.mkdir(parents=True)
                (Path(raw) / "scripts").mkdir()
                shutil.copy2(ROOT / "scripts/install.py", Path(raw) / "scripts/install.py")
                (source / "bundle.json").write_text(json.dumps(
                    {"schema": "one_checkout_bundle_v1", "members": inventory}))
                with self.assertRaises(ValueError):
                    module.verify_install(Path(raw))
                self.assertEqual(set(Path(raw).iterdir()), {Path(raw) / "skills", Path(raw) / "scripts"})

    def test_manifest_must_be_an_object(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as raw:
            source = Path(raw) / "skills/one-checkout"
            source.mkdir(parents=True)
            (Path(raw) / "scripts").mkdir()
            shutil.copy2(ROOT / "scripts/install.py", Path(raw) / "scripts/install.py")
            (source / "bundle.json").write_text("[]")
            with self.assertRaises(ValueError):
                module.verify_install(Path(raw))

    def test_undeclared_original_resource_is_not_hidden_by_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as raw:
            root = Path(raw)
            (root / "scripts").mkdir()
            shutil.copy2(ROOT / "scripts/install.py", root / "scripts/install.py")
            shutil.copytree(ROOT / "skills/one-checkout", root / "skills/one-checkout")
            extra = root / "skills/one-checkout/unlisted-resource.txt"
            extra.write_text("must fail before creating an installation fixture\n")
            with self.assertRaisesRegex(ValueError, "undeclared"):
                module.verify_install(root)
            self.assertEqual(extra.read_text(), "must fail before creating an installation fixture\n")
