"""Regression: copying index bytes must not make racily-clean entries look safe."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("timestamp_demo", ROOT / "skills/one-checkout/scripts/demo.py")
assert SPEC and SPEC.loader
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class IndexTimestampTests(unittest.TestCase):
    def exercise(self, *, synchronise: bool) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as folder:
            f = helper.Fixture(Path(folder))
            # A supported Git setting plus fixed mtimes makes this independent of
            # sub-second filesystem precision and machine speed. No sleep needed.
            f.git("config", "core.trustctime", "false")
            stamp = 1_700_000_000_000_000_000
            shared = f.repo / "shared.py"
            shared.write_bytes(helper.STAGED)
            os.utime(shared, ns=(stamp, stamp))
            f.git("add", "--", "shared.py")
            paths = ["shared.py"]
            if synchronise:
                # Commit a different file whose original index entry equals HEAD;
                # the racily-clean shared file must remain staged and detectable.
                f.patch.write_bytes(f.git("diff", "--binary", f.parent, "--", "foreign.txt"))
                oid = f.git("rev-parse", "HEAD:foreign.txt").decode().strip()
                f.git("update-index", "--cacheinfo", "100644," + oid + ",foreign.txt")
                paths = ["foreign.txt"]
            index = f.repo / ".git/index"
            os.utime(index, ns=(stamp, stamp))
            shared.write_bytes(helper.WORKING)
            os.utime(shared, ns=(stamp, stamp))
            before_blob = f.git("show", ":shared.py")
            # Verify that this is a valid, detectable precondition, not an
            # index already corrupted by the test fixture itself.
            self.assertIn(b"shared.py", f.git("diff-files", "--name-only"))
            args = [sys.executable, "-B", str(helper.TOOL), "commit", "--repo", str(f.repo),
                    "--patch", str(f.patch), "--expected-parent", f.parent,
                    "--message", "Timestamp regression"]
            for path in paths:
                args += ["--path", path]
            f.execute(args)
            self.assertEqual(before_blob, f.git("show", ":shared.py"))
            # Comparing objects bypasses Git's working-file stat optimisation.
            cached_diff = f.git("diff", "--cached", "--no-ext-diff", "--no-textconv", "--", "shared.py")
            blob_diff = f.git("diff", "--no-ext-diff", "--no-textconv", "HEAD:shared.py", ":shared.py")
            def body(diff: bytes) -> bytes:
                return diff[diff.index(b"@@"):]
            self.assertEqual(body(blob_diff), body(cached_diff),
                             "cached diff must describe the stored index, not working bytes")
            self.assertIn(b"shared.py", f.git("diff-files", "--name-only"))
            self.assertEqual(helper.WORKING, shared.read_bytes())
            f.git("add", "--", "shared.py")
            self.assertEqual(helper.WORKING, f.git("show", ":shared.py"))

    def test_preserved_index_retains_racy_timestamp_protection(self) -> None:
        self.exercise(synchronise=False)

    def test_synchronising_another_path_does_not_hide_racy_foreign_edits(self) -> None:
        self.exercise(synchronise=True)


if __name__ == "__main__":
    unittest.main()
