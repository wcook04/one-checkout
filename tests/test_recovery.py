"""Read-only recovery evidence and real caller-death boundaries in disposable Git."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from unittest import mock

import test_one_checkout as fixtures

core = fixtures.core


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.OneCheckoutTests.setUp
    git = fixtures.OneCheckoutTests.git
    patch_for = fixtures.OneCheckoutTests.patch_for
    land = fixtures.OneCheckoutTests.land
    blob = fixtures.OneCheckoutTests.blob
    metadata = fixtures.OneCheckoutTests.metadata

    def record(self):
        return Path(self.temp.name) / 'operation.jsonl'

    def commit_recorded(self):
        return self.land(self.patch_for('shared.py', b'selected\n'), record_file=self.record())

    def events(self):
        return [json.loads(line) for line in self.record().read_bytes().splitlines()]

    def write_events(self, events):
        self.record().write_text(''.join(json.dumps(event) + '\n' for event in events))

    def test_recorded_commit_and_repeated_inspection_are_read_only(self):
        result = self.commit_recorded()
        self.assertEqual(result['status'], 'committed')
        self.assertEqual([e['phase'] for e in self.events()], ['prepared', 'requested', 'confirmed', 'outcome'])
        self.assertEqual(self.record().stat().st_mode & 0o777, 0o600)
        before, evidence = self.metadata(), self.record().read_bytes()
        for _ in range(2):
            observation = core.recover_record(self.record())
            self.assertTrue(observation['recorded_ref_update_confirmed'])
            self.assertTrue(observation['candidate_is_current_branch'])
            self.assertTrue(observation['index_matches_prepared'])
            self.assertFalse(observation['index_matches_original'])
        self.assertEqual(before, self.metadata())
        self.assertEqual(evidence, self.record().read_bytes())

    def test_detached_record_recovery_is_read_only_and_uses_target_fields(self):
        self.git('update-ref', '--no-deref', 'HEAD', self.parent, self.parent)
        result = self.commit_recorded()
        before, evidence = self.metadata(), self.record().read_bytes()
        for _ in range(2):
            observation = core.recover_record(self.record())
            self.assertEqual(observation['head_mode'], 'detached')
            self.assertEqual(observation['observed_target_oid'], result['new_commit'])
            self.assertTrue(observation['candidate_is_current_target'])
            self.assertIsNone(observation['observed_branch_oid'])
            self.assertIsNone(observation['candidate_is_current_branch'])
            self.assertTrue(observation['reflog_mentions_candidate'])
            self.assertTrue(observation['index_matches_prepared'])
        self.assertEqual(self.metadata(), before)
        self.assertEqual(self.record().read_bytes(), evidence)

    def test_detached_record_attachment_leaves_current_target_unknown(self):
        self.git('update-ref', '--no-deref', 'HEAD', self.parent, self.parent)
        result = self.commit_recorded()
        self.git('update-ref', 'refs/heads/saved', result['new_commit'])
        evidence = self.record().read_bytes()
        for branch in ('refs/heads/saved', 'refs/heads/main'):
            with self.subTest(branch=branch):
                self.git('symbolic-ref', 'HEAD', branch)
                before = self.metadata()
                observation = core.recover_record(self.record())
                self.assertIsNone(observation['observed_target_oid'])
                self.assertIsNone(observation['candidate_is_current_target'])
                self.assertIsNone(observation['candidate_is_ancestor'])
                self.assertIsNone(observation['observed_branch_oid'])
                self.assertIsNone(observation['candidate_is_current_branch'])
                self.assertTrue(observation['recorded_ref_update_confirmed'])
                self.assertEqual(self.metadata(), before)
                self.assertEqual(self.record().read_bytes(), evidence)

    def test_detached_record_later_advance_reports_ancestry(self):
        self.git('update-ref', '--no-deref', 'HEAD', self.parent, self.parent)
        result = self.commit_recorded()
        child = self.git('commit-tree', result['tree'], '-p', result['new_commit'],
                         data=b'later detached commit\n').stdout.decode().strip()
        self.git('update-ref', '--no-deref', 'HEAD', child, result['new_commit'])
        before, evidence = self.metadata(), self.record().read_bytes()
        observation = core.recover_record(self.record())
        self.assertEqual(observation['observed_target_oid'], child)
        self.assertFalse(observation['candidate_is_current_target'])
        self.assertTrue(observation['candidate_is_ancestor'])
        self.assertEqual(self.metadata(), before)
        self.assertEqual(self.record().read_bytes(), evidence)

    def test_detached_recovery_does_not_follow_native_HEAD_ABA(self):
        self.git('update-ref', '--no-deref', 'HEAD', self.parent, self.parent)
        result = self.commit_recorded()
        self.git('update-ref', 'refs/heads/saved', result['new_commit'])
        # A separate fixture writer returns detached HEAD to P. During either an
        # unsafe HEAD resolution or a literal-OID lookup, attach at A then detach
        # back to P. Inspection must not label the temporary branch tip current.
        self.git('update-ref', '--no-deref', 'HEAD', self.parent, result['new_commit'])
        run, transitions = core.Git.run, []
        def raced(git, args, **kwargs):
            if args in (['rev-parse', '--verify', 'HEAD^{commit}'], ['cat-file', '-t', self.parent]):
                self.git('symbolic-ref', 'HEAD', 'refs/heads/saved')
                try:
                    observed = run(git, args, **kwargs)
                finally:
                    self.git('update-ref', '--no-deref', 'HEAD', self.parent, result['new_commit'])
                transitions.append(args)
                return observed
            return run(git, args, **kwargs)
        evidence, index = self.record().read_bytes(), (self.repo / '.git/index').read_bytes()
        working = {p.name: p.read_bytes() for p in self.repo.iterdir() if p.is_file()}
        with mock.patch.object(core.Git, 'run', new=raced):
            observation = core.recover_record(self.record())
        self.assertEqual(len(transitions), 1)
        self.assertEqual(observation['observed_target_oid'], self.parent)
        self.assertFalse(observation['candidate_is_current_target'])
        self.assertFalse(observation['candidate_is_ancestor'])
        self.assertTrue(observation['recorded_ref_update_confirmed'])
        self.assertEqual(self.git('symbolic-ref', '--quiet', 'HEAD', check=False).returncode, 1)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(), self.parent)
        self.assertEqual(self.git('rev-parse', 'refs/heads/saved').stdout.decode().strip(), result['new_commit'])
        self.assertEqual(self.record().read_bytes(), evidence)
        self.assertEqual((self.repo / '.git/index').read_bytes(), index)
        self.assertEqual({p.name: p.read_bytes() for p in self.repo.iterdir() if p.is_file()}, working)

    def test_detached_recovery_accepts_regular_literal_HEAD_snapshots(self):
        self.git('update-ref', '--no-deref', 'HEAD', self.parent, self.parent)
        result = self.commit_recorded()
        observed_head = Path(self.temp.name) / 'head-observation'
        path = core.Git.path
        def selected(git, name):
            return observed_head if name == 'HEAD' else path(git, name)
        before, evidence = self.metadata(), self.record().read_bytes()
        for oid in (self.parent, result['new_commit']):
            for ending in (b'', b'\n', b'\r\n'):
                with self.subTest(oid=oid, ending=ending):
                    observed_head.write_bytes(oid.encode() + ending)
                    with mock.patch.object(core.Git, 'path', new=selected):
                        observation = core.recover_record(self.record())
                    self.assertEqual(observation['observed_target_oid'], oid)
                    self.assertEqual(observation['candidate_is_current_target'], oid == result['new_commit'])
        self.assertEqual(self.metadata(), before)
        self.assertEqual(self.record().read_bytes(), evidence)

    def test_detached_recovery_malformed_missing_or_noncommit_HEAD_is_unknown(self):
        self.git('update-ref', '--no-deref', 'HEAD', self.parent, self.parent)
        result = self.commit_recorded()
        observed_head = Path(self.temp.name) / 'head-observation'
        blob = self.git('hash-object', '-w', '--stdin', data=b'not a commit\n').stdout.strip()
        oid = result['new_commit'].encode()
        cases = (None, b'', b'ref: refs/heads/main\n', b'reftable\n', b'broken\n',
                 oid + b'\n\n', oid + b' extra\n', b' ' + oid, oid + b'\r',
                 b'f' * 40, b'f' * 64, blob + b'\n', b'x' * 4096)
        path = core.Git.path
        def selected(git, name):
            return observed_head if name == 'HEAD' else path(git, name)
        before, evidence = self.metadata(), self.record().read_bytes()
        for contents in cases:
            with self.subTest(contents=contents[:80] if contents is not None else None):
                if observed_head.exists():
                    observed_head.unlink()
                if contents is not None:
                    observed_head.write_bytes(contents)
                with mock.patch.object(core.Git, 'path', new=selected):
                    observation = core.recover_record(self.record())
                self.assertIsNone(observation['observed_target_oid'])
                self.assertIsNone(observation['candidate_is_current_target'])
                self.assertIsNone(observation['candidate_is_ancestor'])
        self.assertEqual(self.metadata(), before)
        self.assertEqual(self.record().read_bytes(), evidence)

    def test_detached_recovery_HEAD_symlink_and_fifo_are_not_followed_or_blocked(self):
        self.git('update-ref', '--no-deref', 'HEAD', self.parent, self.parent)
        result = self.commit_recorded()
        target = Path(self.temp.name) / 'literal-head'
        target.write_text(result['new_commit'] + '\n')
        observed_head = Path(self.temp.name) / 'head-observation'
        path, open_file = core.Git.path, core.os.open
        def selected(git, name):
            return observed_head if name == 'HEAD' else path(git, name)
        before, evidence = self.metadata(), self.record().read_bytes()
        for kind in ('symlink', 'fifo'):
            with self.subTest(kind=kind):
                if os.path.lexists(observed_head):
                    observed_head.unlink()
                if kind == 'symlink':
                    observed_head.symlink_to(target)
                else:
                    os.mkfifo(observed_head)
                attempts = []
                def guarded(pathname, flags, *args, **kwargs):
                    if Path(pathname) == observed_head:
                        self.assertTrue(flags & os.O_NOFOLLOW)
                        self.assertTrue(flags & os.O_NONBLOCK)
                        attempts.append(True)
                    return open_file(pathname, flags, *args, **kwargs)
                with mock.patch.object(core.Git, 'path', new=selected), \
                        mock.patch.object(core.os, 'open', new=guarded):
                    observation = core.recover_record(self.record())
                self.assertEqual(attempts, [True])
                self.assertIsNone(observation['observed_target_oid'])
                self.assertIsNone(observation['candidate_is_current_target'])
        self.assertEqual(self.metadata(), before)
        self.assertEqual(self.record().read_bytes(), evidence)

    def test_legacy_named_records_and_inconsistent_detached_modes(self):
        self.commit_recorded()
        events = self.events()
        for event in events:
            event['receipt'].pop('head_mode', None)
        self.write_events(events)
        observation = core.recover_record(self.record())
        self.assertEqual(observation['head_mode'], 'attached')
        self.assertTrue(observation['candidate_is_current_branch'])
        self.assertTrue(observation['candidate_is_current_target'])
        for mode, branch in ((None, 'HEAD'), ('detached', 'HEAD'), ('detached', 'refs/heads/main'),
                             ('attached', 'HEAD'), ([], 'HEAD')):
            with self.subTest(mode=mode, branch=branch):
                altered = json.loads(json.dumps(events))
                altered[-1]['receipt'].update(head_mode=mode, branch=branch)
                self.write_events(altered)
                evidence, before = self.record().read_bytes(), self.metadata()
                with self.assertRaises(core.Refusal) as caught:
                    core.recover_record(self.record())
                self.assertEqual(caught.exception.code, 'invalid_record')
                self.assertEqual(self.record().read_bytes(), evidence)
                self.assertEqual(self.metadata(), before)

    def test_detached_lost_ack_remains_uncertain_while_recovery_observes_head(self):
        self.git('update-ref', '--no-deref', 'HEAD', self.parent, self.parent)
        read_ack, count = core._read_ack, []
        def lost_ack(process, timeout):
            result = read_ack(process, timeout)
            count.append(True)
            return b'' if len(count) == 3 else result
        with mock.patch.object(core, '_read_ack', new=lost_ack):
            result = self.commit_recorded()
        self.assertEqual(result['status'], 'landing_uncertain')
        self.assertFalse(result['ref_update_confirmed'])
        observation = core.recover_record(self.record())
        self.assertFalse(observation['recorded_ref_update_confirmed'])
        self.assertTrue(observation['candidate_is_current_target'])
        self.assertTrue(observation['index_matches_original'])

    def test_detached_post_ack_attachment_is_unsettled_without_rewinding(self):
        self.git('update-ref', '--no-deref', 'HEAD', self.parent, self.parent)
        original = (self.repo / '.git/index').read_bytes()
        working = {p.name: p.read_bytes() for p in self.repo.iterdir() if p.is_file()}
        publish = core._publish_ref
        def attach_after_confirmation(git, branch, candidate, parent, record=None):
            publish(git, branch, candidate, parent, record=record)
            self.git('update-ref', 'refs/heads/after', candidate, '0' * len(parent))
            self.git('symbolic-ref', 'HEAD', 'refs/heads/after')
        with mock.patch.object(core, '_publish_ref', new=attach_after_confirmation):
            result = self.commit_recorded()
        self.assertEqual(result['status'], 'landed_unsettled')
        self.assertTrue(result['ref_update_confirmed'])
        self.assertEqual(result['head_mode'], 'detached')
        self.assertEqual(self.git('rev-parse', 'refs/heads/after').stdout.decode().strip(), result['new_commit'])
        self.assertEqual(self.git('rev-parse', 'refs/heads/main').stdout.decode().strip(), self.parent)
        self.assertEqual(self.git('symbolic-ref', 'HEAD').stdout, b'refs/heads/after\n')
        self.assertEqual((self.repo / '.git/index').read_bytes(), original)
        self.assertEqual({p.name: p.read_bytes() for p in self.repo.iterdir() if p.is_file()}, working)
        before, evidence = self.metadata(), self.record().read_bytes()
        observation = core.recover_record(self.record())
        self.assertTrue(observation['recorded_ref_update_confirmed'])
        self.assertTrue(observation['index_matches_original'])
        self.assertIsNone(observation['observed_target_oid'])
        self.assertIsNone(observation['candidate_is_current_target'])
        self.assertEqual(self.metadata(), before)
        self.assertEqual(self.record().read_bytes(), evidence)

    def test_cli_record_and_recovery_contract(self):
        patch = Path(self.temp.name) / 'selected.patch'
        patch.write_bytes(self.patch_for('shared.py', b'selected\n'))
        command = [sys.executable, '-B', '-I', str(fixtures.SCRIPT)]
        landed = subprocess.run([*command, 'commit', '--repo', str(self.repo), '--patch', str(patch),
                                 '--path', 'shared.py', '--expected-parent', self.parent, '--message', 'selected',
                                 '--record-file', str(self.record())], capture_output=True, timeout=30)
        self.assertEqual(landed.returncode, 0, landed.stderr)
        before = self.metadata()
        observed = subprocess.run([*command, 'recover', '--record-file', str(self.record())],
                                  capture_output=True, timeout=30)
        self.assertEqual(observed.returncode, 0, observed.stderr)
        receipt = json.loads(observed.stdout)
        self.assertEqual(receipt['status'], 'recovery_observed')
        self.assertTrue(receipt['ref_update_confirmed'])
        self.assertFalse(receipt['automatic_retry_allowed'])
        self.assertEqual(receipt['next_action'], 'inspect_recovery_observations')
        self.assertEqual(before, self.metadata())

    def test_uncertainty_survives_cleanup_and_output_failure(self):
        import tempfile
        cleanup = tempfile.TemporaryDirectory.cleanup
        def failed_cleanup(directory):
            cleanup(directory)
            raise OSError('fixture cleanup failed')
        with mock.patch.object(core, '_publish_ref', side_effect=core.RefOutcome('fixture unknown', confirmed=False)), \
                mock.patch.object(tempfile.TemporaryDirectory, 'cleanup', new=failed_cleanup):
            result = self.land(self.patch_for('shared.py', b'selected\n'))
        self.assertEqual(result['status'], 'landing_uncertain')
        self.assertFalse(result['ref_update_confirmed'])
        stderr = io.StringIO()
        class FailedOutput(io.StringIO):
            def write(self, text): raise BrokenPipeError('fixture stdout lost')
        with mock.patch.object(sys, 'stdout', FailedOutput()), mock.patch.object(sys, 'stderr', stderr):
            self.assertEqual(core._report_receipt(result), 3)
        delivered = json.loads(stderr.getvalue())
        self.assertEqual(delivered['status'], 'landing_uncertain')
        self.assertFalse(delivered['ref_update_confirmed'])
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(), self.parent)

    def test_output_interrupt_after_confirmed_commit_preserves_outcome(self):
        result = self.commit_recorded()
        stdout = core._emit_receipt
        stderr = io.StringIO()
        def interrupted(receipt, stream):
            if stream is sys.stdout: raise KeyboardInterrupt('fixture output interrupted')
            return stdout(receipt, stream)
        with mock.patch.object(core, '_emit_receipt', new=interrupted), mock.patch.object(sys, 'stderr', stderr):
            self.assertEqual(core._report_receipt(result), 3)
        delivered = json.loads(stderr.getvalue())
        self.assertEqual(delivered['status'], 'committed')
        self.assertTrue(delivered['ref_update_confirmed'])

    def test_recovery_after_later_commit_records_ancestry_not_retry(self):
        result = self.commit_recorded()
        tree = self.git('rev-parse', 'HEAD^{tree}').stdout.strip().decode()
        child = self.git('commit-tree', tree, '-p', result['new_commit'], data=b'later').stdout.strip().decode()
        self.git('update-ref', 'HEAD', child, result['new_commit'])
        observed = core.recover_record(self.record())
        self.assertTrue(observed['recorded_ref_update_confirmed'])
        self.assertFalse(observed['candidate_is_current_branch'])
        self.assertTrue(observed['candidate_is_ancestor'])
        self.assertFalse(core.caller_receipt(observed)['automatic_retry_allowed'])

    def test_recovery_reports_neither_index_snapshot_after_new_staging(self):
        self.commit_recorded()
        (self.repo / 'foreign.txt').write_text('later stage\n')
        self.git('add', 'foreign.txt')
        observed = core.recover_record(self.record())
        self.assertFalse(observed['index_matches_original'])
        self.assertFalse(observed['index_matches_prepared'])

    def test_truncated_final_event_retains_prior_confirmation(self):
        self.commit_recorded()
        with self.record().open('ab') as stream:
            stream.write(b'{"interrupted":')
        observed = core.recover_record(self.record())
        self.assertTrue(observed['record_truncated'])
        self.assertTrue(observed['recorded_ref_update_confirmed'])

    def test_complete_invalid_or_conflicting_events_refuse(self):
        self.commit_recorded()
        good = self.events()
        for key, value in [('changed_paths', ['foreign.txt']), ('index_before_sha256', '0' * 64),
                           ('index_prepared_sha256', '0' * 64), ('tree', '0' * 40),
                           ('repository_identity', {})]:
            events = json.loads(json.dumps(good))
            events[1]['receipt'][key] = value
            self.write_events(events)
            with self.subTest(key=key), self.assertRaises(core.Refusal) as caught:
                core.recover_record(self.record())
            self.assertEqual(caught.exception.code, 'invalid_record')
        for events in ([good[0], good[2]], [good[0], good[1], good[1]], [good[1]]):
            self.write_events(events)
            with self.assertRaises(core.Refusal):
                core.recover_record(self.record())
        self.write_events(good)
        raw = self.record().read_bytes().replace(b'"phase": "prepared"', b'"phase": "prepared", "phase": "requested"', 1)
        self.record().write_bytes(raw)
        with self.assertRaises(core.Refusal):
            core.recover_record(self.record())

    def test_candidate_actual_tree_and_parent_are_checked(self):
        self.commit_recorded()
        events = self.events()
        for event in events:
            event['receipt']['tree'] = '0' * 40
        self.write_events(events)
        with self.assertRaises(core.Refusal) as caught:
            core.recover_record(self.record())
        self.assertEqual(caught.exception.code, 'record_candidate')

    def test_replaced_repository_at_same_path_is_rejected(self):
        self.commit_recorded()
        old = self.repo.with_name('original-repo')
        self.repo.rename(old)
        subprocess.run(['git', 'clone', '--no-local', str(old), str(self.repo)], check=True, capture_output=True)
        with self.assertRaises(core.Refusal) as caught:
            core.recover_record(self.record())
        self.assertEqual(caught.exception.code, 'record_repository')

    def test_existing_inside_and_symlinked_record_destinations_refuse(self):
        patch = self.patch_for('shared.py', b'selected\n')
        self.record().write_text('prior evidence')
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch, record_file=self.record())
        self.assertEqual(caught.exception.code, 'record_exists')
        self.assertEqual(self.record().read_text(), 'prior evidence')
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch, record_file=self.repo / 'record.jsonl')
        self.assertEqual(caught.exception.code, 'record_location')
        link = Path(self.temp.name) / 'link'
        link.symlink_to(self.repo.parent, target_is_directory=True)
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch, record_file=link / 'other.jsonl')
        self.assertEqual(caught.exception.code, 'record_location')
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(), self.parent)

    def test_recording_is_not_allowed_for_plan(self):
        with self.assertRaises(core.Refusal) as caught:
            self.land(self.patch_for('shared.py', b'selected\n'), dry_run=True, record_file=self.record())
        self.assertEqual(caught.exception.code, 'record_plan')
        self.assertFalse(self.record().exists())

    def test_oversized_record_and_nonregular_input_refuse(self):
        with self.record().open('wb') as stream:
            stream.truncate(core.MAX_RECOVERY_BYTES + 1)
        with self.assertRaises(core.Refusal) as caught:
            core.recover_record(self.record())
        self.assertEqual(caught.exception.code, 'invalid_record')
        self.record().unlink()
        os.mkfifo(self.record())
        with self.assertRaises(core.Refusal):
            core.recover_record(self.record())

    def test_producer_reserves_bounded_record_space_before_request(self):
        patch = self.patch_for('shared.py', b'selected\n')
        plan = self.land(patch, dry_run=True)
        plan['warnings'] = ['x' * (core.MAX_RECOVERY_BYTES // 3)]
        plan['new_commit'] = self.parent
        with core.RecoveryRecord(self.record(), core.Git(self.repo), plan) as record:
            with self.assertRaises(core.Refusal) as caught:
                record.append('prepared')
        self.assertEqual(caught.exception.code, 'record_limit')
        self.assertEqual(self.record().stat().st_size, 0)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(), self.parent)

    def test_final_record_error_keeps_confirmed_operation_and_exit_three(self):
        append = core.RecoveryRecord.append
        def fail(record, phase):
            if phase == 'outcome':
                raise OSError('fixture record failure')
            return append(record, phase)
        with mock.patch.object(core.RecoveryRecord, 'append', new=fail):
            result = self.commit_recorded()
        self.assertEqual(result['status'], 'committed')
        self.assertTrue(result['ref_update_confirmed'])
        self.assertIn('record_error', result)
        with mock.patch.object(sys, 'stdout', io.StringIO()):
            self.assertEqual(core._report_receipt(result), 3)
        self.assertTrue(core.recover_record(self.record())['recorded_ref_update_confirmed'])

    def test_empty_record_interrupt_still_requires_inspection_and_exit_three(self):
        append = core.RecoveryRecord.append
        def interrupted(record, phase):
            if phase == 'outcome': raise KeyboardInterrupt()
            return append(record, phase)
        with mock.patch.object(core.RecoveryRecord, 'append', new=interrupted):
            result = self.commit_recorded()
        self.assertEqual(result['status'], 'committed')
        self.assertTrue(result['ref_update_confirmed'])
        self.assertEqual(result['record_error'], '')
        self.assertEqual(core.caller_receipt(result)['next_action'], 'inspect_recovery_record')
        with mock.patch.object(sys, 'stdout', io.StringIO()):
            self.assertEqual(core._report_receipt(result), 3)
        self.assertTrue(core.recover_record(self.record())['recorded_ref_update_confirmed'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX caller death')
    def test_real_caller_death_at_each_durable_phase(self):
        patch = Path(self.temp.name) / 'selected.patch'
        patch.write_bytes(self.patch_for('shared.py', b'selected\n'))
        worker = Path(self.temp.name) / 'kill_worker.py'
        worker.write_text('''import importlib.util,os,signal,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("killed_core",sys.argv[1]); core=importlib.util.module_from_spec(spec); spec.loader.exec_module(core)
append=core.RecoveryRecord.append
phase=sys.argv[6]
def barrier(record,current):
 append(record,current)
 if current==phase: os.kill(os.getpid(),signal.SIGKILL)
core.RecoveryRecord.append=barrier
core.commit_patch(repo=sys.argv[2],patch=Path(sys.argv[3]).read_bytes(),paths=["shared.py"],expected_parent=sys.argv[4],message="selected",record_file=sys.argv[5])
''')
        original = (self.repo / '.git/index').read_bytes()
        for phase in ('prepared', 'requested', 'confirmed', 'outcome'):
            with self.subTest(phase=phase):
                result = subprocess.run([sys.executable, '-B', '-I', str(worker), str(fixtures.SCRIPT),
                                         str(self.repo), str(patch), self.parent, str(self.record()), phase],
                                        capture_output=True, timeout=30)
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                observation = core.recover_record(self.record())
                self.assertEqual(observation['record_phase'], phase)
                self.assertEqual(observation['recorded_ref_update_confirmed'], phase in ('confirmed', 'outcome'))
                self.assertEqual(observation['candidate_is_current_branch'], phase in ('confirmed', 'outcome'))
                self.assertEqual(observation['index_matches_original'], phase != 'outcome')
                before = self.metadata()
                core.recover_record(self.record())
                self.assertEqual(before, self.metadata())
                # Fixture teardown between trials, after native child's stdin EOF.
                self.git('update-ref', 'HEAD', self.parent)
                (self.repo / '.git/index').write_bytes(original)
                lock = self.repo / '.git/index.lock'
                if lock.exists(): lock.unlink()  # Only this killed fixture's verified caller lock.
                self.record().unlink()

    def test_request_write_fault_preserves_uncertainty_with_or_without_delivery(self):
        popen = subprocess.Popen
        for delivered in (False, True):
            with self.subTest(delivered=delivered):
                class Input:
                    def __init__(self, stream): self.stream = stream
                    def write(self, data):
                        if data == b'commit\n':
                            if delivered:
                                self.stream.write(data); self.stream.flush()
                            raise BrokenPipeError('fixture ambiguous request write')
                        return self.stream.write(data)
                    def flush(self): return self.stream.flush()
                    def close(self): return self.stream.close()
                def launch(args, **kwargs):
                    process = popen(args, **kwargs)
                    if 'update-ref' in args: process.stdin = Input(process.stdin)
                    return process
                before = (self.repo / '.git/index').read_bytes()
                with mock.patch.object(core.subprocess, 'Popen', side_effect=launch):
                    result = self.land(self.patch_for('shared.py', b'selected\n'))
                self.assertEqual(result['status'], 'landing_uncertain')
                self.assertFalse(result['ref_update_confirmed'])
                self.assertEqual((self.repo / '.git/index').read_bytes(), before)
                self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(),
                                 result['new_commit'] if delivered else self.parent)
                self.git('update-ref', 'HEAD', self.parent)


if __name__ == '__main__':
    unittest.main()
