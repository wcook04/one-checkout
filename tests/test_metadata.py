"""Validate source ownership and real plugin/skill distribution interfaces."""
import json
from pathlib import Path
import re
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from install import snapshot

ROOT = Path(__file__).resolve().parents[1]


class Metadata(unittest.TestCase):
    def test_bundle_is_complete_and_includes_runtime_demo_doctor_and_relative_guides(self):
        payload = snapshot(ROOT / 'skills/one-checkout')
        self.assertTrue({'scripts/one_checkout.py','scripts/demo.py','scripts/doctor.py',
                         'references/patch-selection.md','references/recovery.md'} <= set(payload))
        self.assertEqual(payload['LICENSE'][0], (ROOT / 'LICENSE').read_bytes())

    def test_both_manifests_agree_and_use_actual_skill_discovery_paths(self):
        portable = json.loads((ROOT / 'plugin.json').read_text())
        claude = json.loads((ROOT / '.claude-plugin/plugin.json').read_text())
        self.assertEqual(portable['$schema'], 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json')
        self.assertTrue(set(portable) <= {'$schema','name','version','description','author','homepage','repository','license','keywords','extensions'})
        for key in ('name','version','description','author','homepage','repository','license'):
            self.assertEqual(portable[key], claude[key])
        self.assertEqual(portable['license'], 'MIT')
        self.assertTrue((ROOT / claude['skills'] / 'one-checkout/SKILL.md').is_file())

    def test_versioned_source_and_reporting_routes_match_metadata(self):
        metadata = json.loads((ROOT / 'plugin.json').read_text())
        repository = 'https://github.com/wcook04/one-checkout'
        self.assertEqual(metadata['repository'], repository)
        self.assertEqual(metadata['homepage'], repository)
        self.assertEqual(metadata['author']['url'], 'https://github.com/wcook04')
        clone = 'git clone --branch v' + metadata['version'] + ' --depth 1 ' + repository + '.git'
        for name in ('README.md', 'docs/installation.md'):
            self.assertIn(clone, (ROOT / name).read_text())
        self.assertIn(repository + '/security/advisories/new', (ROOT / 'SECURITY.md').read_text())
        self.assertIn(repository + '/pulls', (ROOT / 'CONTRIBUTING.md').read_text())

    def test_readme_local_file_links_resolve_to_required_interfaces(self):
        required = set(json.loads((ROOT / 'docs/repository_layout.json').read_text())['required_files'])
        for target in re.findall(r'\]\(([^)]+)\)', (ROOT / 'README.md').read_text()):
            if ':' in target or target.startswith('#') or target.endswith('/'):
                continue
            self.assertTrue((ROOT / target).is_file(), target)
            self.assertIn(target, required, target)


if __name__ == '__main__':
    unittest.main()
