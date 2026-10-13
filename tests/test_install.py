from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from install import MEMBERS, install_skill


class InstallSkill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        for name in MEMBERS:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("source " + name)
        self.destinations = [self.root / ".agents/skills/one-checkout",
                             self.root / ".claude/skills/one-checkout"]

    def test_both_agents_receive_implementation_and_identical_reinstall_is_noop(self):
        plans = install_skill(self.source, self.destinations)
        self.assertEqual([p["action"] for p in plans], ["install", "install"])
        for destination in self.destinations:
            for name in MEMBERS:
                self.assertEqual((destination / name).read_bytes(), (self.source / name).read_bytes())
        plans = install_skill(self.source, self.destinations)
        self.assertEqual([p["action"] for p in plans], ["unchanged", "unchanged"])

    def test_conflicting_second_destination_refuses_before_first_write(self):
        self.destinations[1].mkdir(parents=True)
        existing = self.destinations[1] / "SKILL.md"
        existing.write_text("custom skill")
        with self.assertRaises(ValueError):
            install_skill(self.source, self.destinations)
        self.assertFalse(self.destinations[0].exists())
        self.assertEqual(existing.read_text(), "custom skill")

    def test_backup_preserves_old_skill_and_extra_files(self):
        destination = self.destinations[0]
        destination.mkdir(parents=True)
        (destination / "custom.txt").write_text("keep this")
        plan = install_skill(self.source, [destination], backup_existing=True)[0]
        self.assertEqual((Path(plan["backup"]) / "custom.txt").read_text(), "keep this")
        self.assertNotEqual(Path(plan["backup"]).parent, destination.parent)
        self.assertTrue((destination / "scripts/one_checkout.py").is_file())

    def test_symlink_is_refused_even_with_backup(self):
        destination = self.destinations[0]
        destination.parent.mkdir(parents=True)
        destination.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            install_skill(self.source, [destination], backup_existing=True)
        self.assertTrue(destination.is_symlink())

    def test_missing_runtime_refuses_both_destinations(self):
        (self.source / "scripts/one_checkout.py").unlink()
        with self.assertRaises(ValueError):
            install_skill(self.source, self.destinations)
        self.assertFalse(any(path.exists() for path in self.destinations))

    def test_existing_regular_file_is_not_replaced(self):
        destination = self.destinations[0]
        destination.parent.mkdir(parents=True)
        destination.write_text("keep this")
        with self.assertRaises(ValueError):
            install_skill(self.source, [destination], backup_existing=True)
        self.assertEqual(destination.read_text(), "keep this")

    def test_dry_run_creates_nothing(self):
        plans = install_skill(self.source, self.destinations, dry_run=True)
        self.assertEqual(len(plans), 2)
        self.assertFalse(any(path.exists() for path in self.destinations))

    def test_failed_copy_restores_explicit_backup(self):
        destination = self.destinations[0]
        destination.mkdir(parents=True)
        (destination / "custom.txt").write_text("keep this")
        with patch("install.shutil.copy2", side_effect=OSError("copy failed")):
            with self.assertRaises(OSError):
                install_skill(self.source, [destination], backup_existing=True)
        self.assertEqual((destination / "custom.txt").read_text(), "keep this")


if __name__ == "__main__":
    unittest.main()
