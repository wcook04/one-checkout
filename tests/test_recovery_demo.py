"""The public comparison must distinguish effects and fail a broken retry guard."""
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
SPEC = importlib.util.spec_from_file_location("recovery_comparison", ROOT / "scripts/recovery_demo.py")
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class RecoveryComparison(unittest.TestCase):
    def test_real_cli_distinguishes_same_uncertain_status_with_different_effects(self):
        result = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "scripts/recovery_demo.py")],
                                capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        cases = {c["case"]: c for c in report["cases"]}
        before, after = cases["request_not_delivered"], cases["acknowledgement_lost"]
        self.assertEqual(before["receipt"]["status"], "landing_uncertain")
        self.assertEqual(after["receipt"]["status"], "landing_uncertain")
        self.assertFalse(before["recovery"]["candidate_is_current_branch"])
        self.assertTrue(after["recovery"]["candidate_is_current_branch"])
        unsettled = cases["index_settlement_failed"]
        self.assertEqual(unsettled["receipt"]["status"], "landed_unsettled")
        self.assertTrue(unsettled["recovery"]["candidate_is_current_branch"])
        self.assertTrue(unsettled["recovery"]["index_matches_original"])
        self.assertTrue(cases["settled"]["recovery"]["index_matches_prepared"])
        for case in cases.values():
            self.assertEqual(case["commit_requests"], 1)
            self.assertFalse(case["receipt"]["automatic_retry_allowed"])
            self.assertFalse(case["recovery"]["automatic_retry_allowed"])
            self.assertTrue(case["checks"]["repeated_recovery_does_not_mutate_repository_or_record"])

    def test_broken_retry_guard_fails_instead_of_returning_demo_passed(self):
        original_load = module.load
        def load(name, path):
            core = original_load(name, path)
            if name == "recovery_demo_core":
                receipt = core.caller_receipt
                core.caller_receipt = lambda result: {**receipt(result), "automatic_retry_allowed": True}
            return core
        with tempfile.TemporaryDirectory(dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as raw:
            with mock.patch.object(module, "load", side_effect=load), \
                    mock.patch.dict(os.environ, {"ONE_CHECKOUT_TEST_TMPDIR": raw}):
                with self.assertRaisesRegex(RuntimeError, "automatic_retry_disallowed"):
                    module.demo()
            self.assertEqual(list(Path(raw).iterdir()), [])

    def test_human_comparison_discloses_injected_and_non_atomic_scope(self):
        result = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "scripts/recovery_demo.py"), "--human"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("acknowledgement_lost", result.stdout)
        self.assertIn("automatic_retry_allowed=false", result.stdout)
        self.assertIn("Sequential injected faults", result.stdout)
        self.assertIn("does not prove nonlanding", result.stdout)


if __name__ == "__main__":
    unittest.main()
