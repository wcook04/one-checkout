"""Real lifecycle boundaries, using only HDD-routed disposable directories."""
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('lifecycle_install', Path(__file__).resolve().parents[1] / 'scripts/install.py')
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('ONE_CHECKOUT_TEST_TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / 'source'
        self.members = list(install.MEMBERS)
        for name in self.members:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic ' + name)
            path.chmod(0o640)
        self.manifest()
        self.destinations = [self.root / '.agents/skills/one-checkout', self.root / '.claude/skills/one-checkout']

    def manifest(self):
        (self.source / 'bundle.json').write_text(json.dumps({'schema':'one_checkout_bundle_v1', 'members': self.members}))

    def test_missing_and_undeclared_resources_refuse_before_destination_writes(self):
        (self.source / 'references/new.md').write_text('new resource')
        with self.assertRaisesRegex(ValueError, 'undeclared'):
            install.install_skill(self.source, self.destinations)
        self.members.append('references/new.md')
        self.manifest()
        install.install_skill(self.source, self.destinations)
        for destination in self.destinations:
            self.assertEqual((destination / 'references/new.md').read_text(), 'new resource')
            self.assertTrue((destination / 'bundle.json').is_file())

    def test_manifest_traversal_duplicates_and_nonobject_are_rejected(self):
        for data in [[], {'schema':'one_checkout_bundle_v1','members':['../x']},
                     {'schema':'one_checkout_bundle_v1','members':self.members*2}]:
            (self.source / 'bundle.json').write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                install.install_skill(self.source, self.destinations)
        self.assertFalse(self.destinations[0].exists())

    def test_source_directory_symlink_refuses(self):
        external = self.root / 'external'
        (self.source / 'scripts').rename(external)
        (self.source / 'scripts').symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            install.install_skill(self.source, self.destinations)
        self.assertFalse(self.destinations[0].exists())

    def test_destination_ancestor_symlink_refuses_without_external_writes(self):
        external = self.root / 'external'
        external.mkdir()
        (self.root / '.agents').symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            install.install_skill(self.source, self.destinations)
        self.assertEqual(list(external.iterdir()), [])

    def test_modes_and_bytes_preserved_and_custom_mode_refused(self):
        install.install_skill(self.source, self.destinations)
        for destination in self.destinations:
            self.assertEqual(stat.S_IMODE((destination / 'scripts/one_checkout.py').stat().st_mode), 0o640)
        (self.destinations[0] / 'scripts/one_checkout.py').chmod(0o600)
        with self.assertRaises(ValueError):
            install.install_skill(self.source, self.destinations)

    def test_regular_generated_pyc_is_ignored_but_arbitrary_cache_file_is_not(self):
        cache = self.source / 'scripts/__pycache__'
        cache.mkdir()
        (cache / 'runtime.cpython-310.pyc').write_bytes(b'cache')
        install.install_skill(self.source, self.destinations)
        (cache / 'private.txt').write_text('must not silently omit')
        with self.assertRaises(ValueError):
            install.install_skill(self.source, self.destinations)

    def test_second_host_stage_failure_publishes_neither(self):
        real = install.write_payload
        def fail(payload, stage):
            if '.claude' in str(stage):
                raise OSError('second stage failed')
            real(payload, stage)
        with patch.object(install, 'write_payload', side_effect=fail):
            with self.assertRaises(install.InstallationError) as caught:
                install.install_skill(self.source, self.destinations)
        self.assertIn('second stage', str(caught.exception))
        self.assertFalse(any(p.exists() for p in self.destinations))

    def test_second_publish_failure_reports_first_success_and_restores_custom_copy(self):
        second = self.destinations[1]
        second.mkdir(parents=True)
        (second / 'custom.txt').write_text('preserve')
        real = install.publish
        def fail(stage, destination):
            if destination == second:
                raise OSError('second publish failed')
            real(stage, destination)
        with patch.object(install, 'publish', side_effect=fail):
            with self.assertRaises(install.InstallationError) as caught:
                install.install_skill(self.source, self.destinations, backup_existing=True)
        self.assertEqual([p['status'] for p in caught.exception.plans], ['installed','restored'])
        self.assertEqual((second / 'custom.txt').read_text(), 'preserve')
        self.assertTrue((self.destinations[0] / 'bundle.json').is_file())

    def test_cleanup_error_does_not_hide_original_failure_or_stage_location(self):
        with patch.object(install, 'write_payload', side_effect=OSError('original failure')), \
                patch.object(install.shutil, 'rmtree', side_effect=OSError('cleanup failed')):
            with self.assertRaises(install.InstallationError) as caught:
                install.install_skill(self.source, self.destinations)
        self.assertIn('original failure', str(caught.exception))
        retained = caught.exception.plans[0]['cleanup_errors'][0]['path']
        self.assertTrue(Path(retained).is_dir())

    def test_uninstall_moves_custom_copy_without_needing_source(self):
        install.install_skill(self.source, self.destinations)
        (self.destinations[0] / 'custom.txt').write_text('keep')
        shutil.rmtree(self.source)
        plans = install.change_installations(None, install.plans_for(self.destinations, None, backup_existing=True), dry_run=False)
        self.assertEqual([p['status'] for p in plans], ['removed','removed'])
        self.assertEqual((Path(plans[0]['backup']) / 'custom.txt').read_text(), 'keep')
        self.assertFalse(any(p.exists() for p in self.destinations))
        again = install.change_installations(None, install.plans_for(self.destinations, None, backup_existing=True), dry_run=False)
        self.assertEqual([p['status'] for p in again], ['absent','absent'])

    def test_replaced_installer_lock_is_retained_and_published_state_is_reported(self):
        real = install.publish
        replacement = self.root / 'replacement.lock'
        replacement.write_text('another writer')
        def replace_lock(stage, destination):
            real(stage, destination)
            if destination == self.destinations[1]:
                replacement.replace(self.root / '.agents/one-checkout-install.lock')
        with patch.object(install, 'publish', side_effect=replace_lock):
            with self.assertRaises(install.InstallationError) as caught:
                install.install_skill(self.source, self.destinations)
        self.assertEqual([p['status'] for p in caught.exception.plans], ['installed','installed'])
        self.assertEqual((self.root / '.agents/one-checkout-install.lock').read_text(), 'another writer')

    def test_dirty_git_source_refuses_and_explicit_override_marks_dirty(self):
        env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        def git(*args):
            subprocess.run(['git','-c','core.hooksPath='+os.devnull,'-C',str(self.root),*args], env=env, check=True, capture_output=True)
        git('init','-q'); git('config','user.name','Synthetic fixture'); git('config','user.email','fixture@example.invalid')
        git('add','source'); git('-c','commit.gpgSign=false','commit','-qm','synthetic source')
        self.assertFalse(install.source_identity(self.source, allow_dirty=False)['dirty'])
        (self.source / 'SKILL.md').write_text('changed')
        with self.assertRaises(ValueError):
            install.source_identity(self.source, allow_dirty=False)
        self.assertTrue(install.source_identity(self.source, allow_dirty=True)['dirty'])
        archive = self.root.parent / (self.root.name + '-archive')
        shutil.copytree(self.source, archive)
        self.addCleanup(shutil.rmtree, archive)
        self.assertEqual(install.source_identity(archive, allow_dirty=False)['kind'], 'archive_snapshot')

    def test_git_index_flags_cannot_hide_changed_payload(self):
        env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        def git(*args):
            return subprocess.run(['git','-c','core.hooksPath='+os.devnull,'-C',str(self.root),*args],
                                  env=env, check=True, capture_output=True).stdout
        git('init','-q'); git('config','user.name','Synthetic fixture'); git('config','user.email','fixture@example.invalid')
        git('add','source'); git('-c','commit.gpgSign=false','commit','-qm','synthetic source')
        member = self.source / 'SKILL.md'
        original = member.read_bytes()
        for flag in ('assume-unchanged', 'skip-worktree'):
            with self.subTest(flag=flag):
                git('update-index', '--'+flag, 'source/SKILL.md')
                member.write_bytes(b'hidden changed source')
                self.assertEqual(git('status','--porcelain=v1','--','source'), b'')
                with self.assertRaisesRegex(ValueError, 'uncommitted changes'):
                    install.source_identity(self.source, allow_dirty=False)
                self.assertTrue(install.source_identity(self.source, allow_dirty=True)['dirty'])
                member.write_bytes(original)
                git('update-index', '--no-'+flag, 'source/SKILL.md')
        git('config', 'core.filemode', 'false')
        member.chmod(0o750)
        self.assertEqual(git('status','--porcelain=v1','--','source'), b'')
        with self.assertRaisesRegex(ValueError, 'uncommitted changes'):
            install.source_identity(self.source, allow_dirty=False)
        self.assertTrue(install.source_identity(self.source, allow_dirty=True)['dirty'])

    def test_cli_installs_the_snapshot_used_for_source_identity(self):
        original = (self.source / 'SKILL.md').read_bytes()
        identity = install.source_identity
        def mutate_after_identity(source, **kwargs):
            result = identity(source, **kwargs)
            (source / 'SKILL.md').write_bytes(b'later source bytes')
            return result
        with patch.object(install, 'source_identity', side_effect=mutate_after_identity):
            code, receipt = self.cli('--scope','project','--project',str(self.root))
        self.assertEqual(code, 0)
        self.assertEqual(receipt['source']['kind'], 'archive_snapshot')
        for destination in self.destinations:
            self.assertEqual((destination / 'SKILL.md').read_bytes(), original)

    def cli(self, *args):
        out, error = io.StringIO(), io.StringIO()
        with patch.object(install, 'SOURCE', self.source), patch.object(sys, 'argv', ['install.py', *args]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(error):
            code = install.main()
        return code, json.loads(out.getvalue() or error.getvalue())

    def test_user_profile_is_honored_and_project_scope_ignores_it(self):
        home, profile = self.root / 'home', self.root / 'Claude profile ü'
        home.mkdir()
        with patch.object(Path, 'home', return_value=home), patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(profile)}):
            code, receipt = self.cli('--agent', 'both')
            self.assertEqual(code, 0)
            self.assertTrue((home / '.agents/skills/one-checkout/bundle.json').is_file())
            self.assertTrue((profile / 'skills/one-checkout/bundle.json').is_file())
            project = self.root / 'project'
            project.mkdir()
            code, receipt = self.cli('--agent', 'claude', '--scope', 'project', '--project', str(project))
            self.assertEqual(code, 0)
            self.assertTrue((project / '.claude/skills/one-checkout/bundle.json').is_file())

    def test_raw_project_symlink_and_relative_user_profile_refuse_without_writes(self):
        real = self.root / 'real'
        real.mkdir()
        link = self.root / 'link'
        link.symlink_to(real, target_is_directory=True)
        code, receipt = self.cli('--scope', 'project', '--project', str(link))
        self.assertEqual(code, 2)
        self.assertEqual(list(real.iterdir()), [])
        with patch.object(Path, 'home', return_value=real), patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': 'relative'}):
            code, receipt = self.cli('--scope', 'user')
        self.assertEqual(code, 2)
        self.assertEqual(list(real.iterdir()), [])

    def test_git_identity_failure_is_not_reported_as_source_archive(self):
        (self.root / '.git').mkdir()
        with self.assertRaises(ValueError):
            install.source_identity(self.source, allow_dirty=True)
        with patch.object(install.subprocess, 'run', side_effect=subprocess.TimeoutExpired('git', 10)):
            code, receipt = self.cli('--scope', 'project', '--project', str(self.root))
        self.assertEqual(code, 2)
        self.assertFalse(self.destinations[0].exists())


if __name__ == '__main__':
    unittest.main()
