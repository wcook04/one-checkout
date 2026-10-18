"""P01 regressions; binary-index and raw Git observations are independent oracles."""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import unittest
from unittest import mock

import test_one_checkout as fixtures

core = fixtures.core


def read_disk_entries(path: Path, oid_bytes: int = 20) -> tuple[int, dict[bytes, tuple[int, bytes, int]]]:
    """Read the documented DIRC v2/v3/v4 representation, not runtime helpers.

    This fixture reader only handles full stage-0 indexes and omits cache extensions.
    """
    data = path.read_bytes()
    signature, version, count = struct.unpack_from('!4sII', data)
    assert signature == b'DIRC' and version in (2, 3, 4)
    offset, previous, entries = 12, b'', {}
    for _ in range(count):
        start = offset
        mode = struct.unpack_from('!I', data, start + 24)[0]
        oid = data[start + 40:start + 40 + oid_bytes]
        offset = start + 40 + oid_bytes
        flags = struct.unpack_from('!H', data, offset)[0]
        offset += 2
        if flags & 0x4000:
            flags |= struct.unpack_from('!H', data, offset)[0] << 16
            offset += 2
        if version == 4:
            value = data[offset]; offset += 1
            strip = value & 0x7f
            while value & 0x80:
                value = data[offset]; offset += 1
                strip = ((strip + 1) << 7) | (value & 0x7f)
            end = data.index(0, offset)
            name = previous[:len(previous) - strip] + data[offset:end]
            offset = end + 1
        else:
            end = data.index(0, offset)
            name = data[offset:end]
            offset = start + ((end + 1 - start + 7) // 8) * 8
        assert not (flags & 0x3000), 'fixture expects stage zero'
        entries[name] = (mode, oid, flags & 0x60008000)
        previous = name
    return version, entries


class TransactionSafetyTests(unittest.TestCase):
    # Reuse fixtures without inheriting (and re-counting) the original 27 tests.
    setUp = fixtures.OneCheckoutTests.setUp
    git = fixtures.OneCheckoutTests.git
    patch_for = fixtures.OneCheckoutTests.patch_for
    land = fixtures.OneCheckoutTests.land
    blob = fixtures.OneCheckoutTests.blob
    metadata = fixtures.OneCheckoutTests.metadata

    def test_same_oid_attach_or_detach_before_prepare_refuses(self):
        patch = self.patch_for('shared.py', b'selected\n')
        command = core.Git.command
        for detached in (False, True):
            with self.subTest(detached=detached):
                if detached:
                    self.git('update-ref', '--no-deref', 'HEAD', self.parent, self.parent)
                else:
                    self.git('symbolic-ref', 'HEAD', 'refs/heads/main')
                original = (self.repo / '.git/index').read_bytes()
                working = (self.repo / 'shared.py').read_bytes()
                def raced(git, args):
                    result = command(git, args)
                    if args[0] == 'update-ref':
                        if detached:
                            self.git('symbolic-ref', 'HEAD', 'refs/heads/main')
                        else:
                            self.git('update-ref', '--no-deref', 'HEAD', self.parent, self.parent)
                    return result
                with mock.patch.object(core.Git, 'command', new=raced):
                    with self.assertRaises(core.Refusal) as caught:
                        self.land(patch)
                self.assertEqual(caught.exception.code, 'head_race')
                self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(), self.parent)
                self.assertEqual(self.git('rev-parse', 'refs/heads/main').stdout.decode().strip(), self.parent)
                self.assertEqual((self.repo / '.git/index').read_bytes(), original)
                self.assertEqual((self.repo / 'shared.py').read_bytes(), working)

    def test_prepared_detached_head_blocks_attach_and_direct_update(self):
        patch = self.patch_for('shared.py', b'selected\n')
        self.git('update-ref', '--no-deref', 'HEAD', self.parent, self.parent)
        tree = self.git('rev-parse', 'HEAD^{tree}').stdout.decode().strip()
        other = self.git('commit-tree', tree, '-p', self.parent, data=b'other writer\n').stdout.decode().strip()
        original = (self.repo / '.git/index').read_bytes()
        working = (self.repo / 'shared.py').read_bytes()
        verify, attempts = core._verify_prepared_head, []
        def intercepted(git, branch, parent):
            attempts.append(self.git('symbolic-ref', 'HEAD', 'refs/heads/main', check=False).returncode)
            attempts.append(self.git('update-ref', '--no-deref', 'HEAD', other, parent, check=False).returncode)
            return verify(git, branch, parent)
        with mock.patch.object(core, '_verify_prepared_head', new=intercepted):
            result = self.land(patch)
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(code != 0 for code in attempts))
        self.assertEqual(self.git('symbolic-ref', '--quiet', 'HEAD', check=False).returncode, 1)
        self.assertEqual(self.git('rev-parse', 'refs/heads/main').stdout.decode().strip(), self.parent)
        self.assertEqual((self.repo / 'shared.py').read_bytes(), working)
        self.assertNotEqual((self.repo / '.git/index').read_bytes(), original)

    def test_detached_head_cas_preserves_competing_commit(self):
        patch = self.patch_for('shared.py', b'selected\n')
        self.git('update-ref', '--no-deref', 'HEAD', self.parent, self.parent)
        tree = self.git('rev-parse', 'HEAD^{tree}').stdout.decode().strip()
        other = self.git('commit-tree', tree, '-p', self.parent, data=b'other writer\n').stdout.decode().strip()
        original = (self.repo / '.git/index').read_bytes()
        working = (self.repo / 'shared.py').read_bytes()
        publish = core._publish_ref
        def raced(git, branch, candidate, parent):
            self.git('update-ref', '--no-deref', 'HEAD', other, parent)
            return publish(git, branch, candidate, parent)
        with mock.patch.object(core, '_publish_ref', new=raced):
            with self.assertRaises(core.Refusal) as caught:
                self.land(patch)
        self.assertEqual(caught.exception.code, 'ref_race')
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(), other)
        self.assertEqual(self.git('rev-parse', 'refs/heads/main').stdout.decode().strip(), self.parent)
        self.assertEqual((self.repo / '.git/index').read_bytes(), original)
        self.assertEqual((self.repo / 'shared.py').read_bytes(), working)

    def test_combined_index_read_matches_independent_binary_oracle(self) -> None:
        names = ['tab\tname.txt', 'line\n  size: 0\tflags: 8000\n.txt', 'intent.txt']
        for name in names:
            (self.repo / name).write_text('new\n')
        self.git('add', '--', *names[:2])
        self.git('add', '-N', '--', names[2])
        self.git('update-index', '--assume-unchanged', '--', names[0])
        self.git('update-index', '--skip-worktree', '--', names[1])
        for version in (2, 3, 4):
            with self.subTest(version=version):
                self.git('update-index', '--index-version', str(version))
                disk = read_disk_entries(self.repo / '.git/index')[1]
                git = core.Git(self.repo)
                with mock.patch.object(git, 'run', wraps=git.run) as calls:
                    entries, flags = core._index_state(git, {})
                self.assertEqual(calls.call_count, 1)
                self.assertEqual(entries, {os.fsdecode(p): (format(m, '06o'), oid.hex())
                                           for p, (m, oid, _) in disk.items()})
                self.assertEqual(flags, {os.fsdecode(p): f for p, (_, _, f) in disk.items() if f})

    def test_combined_index_read_refuses_truncation_and_bad_metadata(self) -> None:
        git = core.Git(self.repo)
        valid = git.run(['ls-files', '--stage', '--debug', '-z']).stdout
        malformed = [valid[:-1], valid.replace(b'100644 ', b'', 1),
                     valid.replace(b'100644 ', b'\xff ', 1), valid + b'trailing']
        for data in malformed:
            with self.subTest(data=data):
                result = subprocess.CompletedProcess([], 0, data, b'')
                with mock.patch.object(git, 'run', return_value=result):
                    with self.assertRaises(core.Refusal) as caught:
                        core._index_state(git, {})
                self.assertEqual(caught.exception.code, 'unsupported_index_flags')

    def assert_flag_preserved(self, flag: str) -> None:
        patch = self.patch_for('shared.py', b'selected\n')
        self.git('update-index', flag, 'shared.py')
        before = read_disk_entries(self.repo / '.git/index')[1][b'shared.py']
        files = (self.repo / 'shared.py').read_bytes()
        result = self.land(patch)
        after = read_disk_entries(self.repo / '.git/index')[1][b'shared.py']
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(result['preserved_staged_paths'], ['shared.py'])
        self.assertEqual(before, after)
        self.assertEqual(self.blob('HEAD', 'shared.py'), b'selected\n')
        self.assertEqual((self.repo / 'shared.py').read_bytes(), files)

    def test_selected_assume_unchanged_retained(self) -> None:
        self.assert_flag_preserved('--assume-unchanged')

    def test_selected_skip_worktree_retained(self) -> None:
        self.assert_flag_preserved('--skip-worktree')

    def test_retained_only_index_keeps_original_inode_and_timestamp(self) -> None:
        patch = self.patch_for('shared.py', b'selected\n')
        (self.repo / 'shared.py').write_text('foreign staged\n')
        self.git('add', 'shared.py')
        index = self.repo / '.git/index'
        before, content = index.stat(), index.read_bytes()
        self.land(patch)
        after = index.stat()
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.assertEqual(index.read_bytes(), content)

    def test_intent_to_add_for_tracked_empty_file_retained(self) -> None:
        target = self.repo / 'empty.txt'
        target.write_bytes(b'')
        self.git('add', 'empty.txt'); self.git('commit', '-qm', 'empty base')
        self.parent = self.git('rev-parse', 'HEAD').stdout.decode().strip()
        patch = self.patch_for('empty.txt', b'selected\n')
        self.git('rm', '--cached', 'empty.txt'); self.git('add', '-N', 'empty.txt')
        before = read_disk_entries(self.repo / '.git/index')[1][b'empty.txt']
        self.assertTrue(before[2] & 0x20000000)
        result = self.land(patch, ['empty.txt'])
        self.assertEqual(result['preserved_staged_paths'], ['empty.txt'])
        self.assertEqual(read_disk_entries(self.repo / '.git/index')[1][b'empty.txt'], before)

    def test_unrelated_flags_and_intent_survive_owned_sync(self) -> None:
        patch = self.patch_for('shared.py', b'selected\n')
        self.git('update-index', '--assume-unchanged', 'foreign.txt')
        (self.repo / 'ita.txt').write_text('unowned\n')
        self.git('add', '-N', 'ita.txt')
        before = read_disk_entries(self.repo / '.git/index')[1]
        result = self.land(patch)
        self.assertEqual(result['synchronized_index_paths'], ['shared.py'])
        after = read_disk_entries(self.repo / '.git/index')[1]
        for name in (b'foreign.txt', b'ita.txt'):
            self.assertEqual(before[name], after[name])

    def test_flags_in_index_versions_2_3_4(self) -> None:
        for requested in (2, 3, 4):
            with self.subTest(index_version=requested):
                self.git('update-index', '--no-assume-unchanged', 'shared.py')
                self.git('update-index', '--index-version', str(requested))
                patch = self.patch_for('shared.py', ('selected %d\n' % requested).encode())
                self.git('update-index', '--assume-unchanged', 'shared.py')
                version, before = read_disk_entries(self.repo / '.git/index')
                # Git is allowed to write v2 instead of v3 if no extended flag exists.
                self.assertIn(version, (2, 3) if requested == 3 else (requested,))
                self.land(patch)
                self.assertEqual(read_disk_entries(self.repo / '.git/index')[1][b'shared.py'], before[b'shared.py'])
                self.parent = self.git('rev-parse', 'HEAD').stdout.decode().strip()

    def test_unrecognised_debug_format_fails_closed(self) -> None:
        patch = self.patch_for('shared.py', b'selected\n')
        run = core.Git.run
        def changed(git, args, **kwargs):
            result = run(git, args, **kwargs)
            if '--debug' in args:
                result.stdout = b'shared.py\0unrecognised flags representation\n'
            return result
        with mock.patch.object(core.Git, 'run', new=changed), self.assertRaises(core.Refusal) as caught:
            self.land(patch)
        self.assertEqual(caught.exception.code, 'unsupported_index_flags')
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(), self.parent)

    def test_real_sparse_index_with_disabled_configuration_refuses(self) -> None:
        for name in ('inside/a', 'outside/b'):
            target = self.repo / name; target.parent.mkdir(); target.write_text('base\n')
        self.git('add', '.'); self.git('commit', '-qm', 'sparse base')
        self.parent = self.git('rev-parse', 'HEAD').stdout.decode().strip()
        patch = self.patch_for('shared.py', b'selected\n')
        self.git('sparse-checkout', 'init', '--cone', '--sparse-index')
        self.git('sparse-checkout', 'set', 'inside')
        self.assertIn(b'040000 ', self.git('ls-files', '--sparse', '--stage').stdout)
        self.git('config', 'core.sparseCheckout', 'false')
        original = (self.repo / '.git/index').read_bytes()
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch)
        self.assertEqual(caught.exception.code, 'sparse_index')
        self.assertEqual((self.repo / '.git/index').read_bytes(), original)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(), self.parent)

    def test_real_split_index_with_disabled_configuration_refuses(self) -> None:
        patch = self.patch_for('shared.py', b'selected\n')
        self.git('update-index', '--split-index'); self.git('config', 'core.splitIndex', 'false')
        original = (self.repo / '.git/index').read_bytes()
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch)
        self.assertEqual(caught.exception.code, 'split_index')
        self.assertEqual((self.repo / '.git/index').read_bytes(), original)

    def test_full_index_with_split_configuration_is_allowed(self) -> None:
        patch = self.patch_for('shared.py', b'selected\n')
        self.git('config', 'core.splitIndex', 'true')
        # No command has converted the existing full index.
        self.assertEqual(self.land(patch)['status'], 'committed')

    def clone_fixture(self, *options: str) -> Path:
        target = Path(self.temp.name) / 'clone'
        self.git('clone', *options, self.repo.as_uri(), str(target))
        return target

    def test_partial_clone_refuses_without_metadata_writes_or_fetch(self) -> None:
        patch = self.patch_for('shared.py', b'selected\n')
        self.git('config', 'uploadpack.allowFilter', 'true')
        target = self.clone_fixture('--filter=blob:none', '--no-checkout')
        subprocess.run(['git', 'read-tree', 'HEAD'], cwd=target, check=True)
        self.repo = target
        before = self.metadata()
        run, commands = core.Git.run, []
        def observe(git, args, **kwargs):
            commands.append(args[0]); return run(git, args, **kwargs)
        with mock.patch.object(core.Git, 'run', new=observe), self.assertRaises(core.Refusal) as caught:
            self.land(patch, dry_run=True)
        self.assertEqual(caught.exception.code, 'partial_clone')
        self.assertNotIn('read-tree', commands)
        self.assertNotIn('apply', commands)
        self.assertEqual(before, self.metadata())

    def test_shallow_plan_is_read_only(self) -> None:
        patch = self.patch_for('shared.py', b'selected\n')
        self.repo = self.clone_fixture('--depth=1')
        self.assertEqual(self.git('rev-parse', '--is-shallow-repository').stdout, b'true\n')
        before = self.metadata()
        self.assertEqual(self.land(patch, dry_run=True)['status'], 'planned')
        self.assertEqual(before, self.metadata())

    def test_alternate_object_plan_is_read_only(self) -> None:
        patch = self.patch_for('shared.py', b'selected\n')
        source, source_before = self.repo, self.metadata()
        target = Path(self.temp.name) / 'alternate'
        self.git('clone', '--shared', str(source), str(target))
        self.repo = target
        self.assertTrue((target / '.git/objects/info/alternates').is_file())
        before = self.metadata()
        self.assertEqual(self.land(patch, dry_run=True)['status'], 'planned')
        self.assertEqual(before, self.metadata())
        self.repo = source
        self.assertEqual(source_before, self.metadata())

    def test_missing_local_blob_refuses_without_metadata_writes(self) -> None:
        patch = self.patch_for('shared.py', b'selected\n')
        oid = self.git('rev-parse', 'HEAD:shared.py').stdout.decode().strip()
        (self.repo / '.git/objects' / oid[:2] / oid[2:]).unlink()
        before = self.metadata()
        with self.assertRaises(core.Refusal):
            self.land(patch, dry_run=True)
        self.assertEqual(before, self.metadata())

    def test_partial_ack_line_has_a_deadline(self) -> None:
        shim = Path(self.temp.name) / 'stall.sh'
        shim.write_text("#!/bin/sh\nprintf sta\nsleep 60\n")
        command = core.Git.command
        def stalled(git, args):
            return ['/bin/sh', str(shim)] if args[0] == 'update-ref' else command(git, args)
        patch = self.patch_for('shared.py', b'selected\n')
        before = (self.repo / '.git/index').read_bytes()
        start = time.monotonic()
        with mock.patch.object(core.Git, 'command', new=stalled), self.assertRaises(core.Refusal) as caught:
            self.land(patch, git_timeout=0.5)
        self.assertEqual(caught.exception.code, 'git_timeout')
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual((self.repo / '.git/index').read_bytes(), before)
        self.assertFalse((self.repo / '.git/index.lock').exists())
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(), self.parent)

    def test_stalled_signer_has_a_deadline(self) -> None:
        signer = Path(self.temp.name) / 'signer'
        signer.write_text('#!/bin/sh\nsleep 60\n'); signer.chmod(0o755)
        self.git('config', 'commit.gpgSign', 'true')
        self.git('config', 'gpg.program', str(signer))
        patch = self.patch_for('shared.py', b'selected\n')
        before = (self.repo / '.git/index').read_bytes()
        start = time.monotonic()
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch, git_timeout=0.5)
        self.assertEqual(caught.exception.code, 'git_timeout')
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual((self.repo / '.git/index').read_bytes(), before)
        self.assertFalse((self.repo / '.git/index.lock').exists())
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(), self.parent)

    def test_lost_ack_timeout_after_real_git_commit_is_uncertain(self) -> None:
        relay = Path(self.temp.name) / 'relay.py'
        relay.write_text('''import subprocess,sys,time
p = subprocess.Popen(sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
for _ in range(3):
 p.stdin.write(sys.stdin.buffer.readline())
p.stdin.flush()
for _ in range(2):
 sys.stdout.buffer.write(p.stdout.readline()); sys.stdout.buffer.flush()
p.stdin.write(sys.stdin.buffer.readline()); p.stdin.flush()
assert p.stdout.readline() == b"commit: ok\\n"
time.sleep(60)
''')
        command = core.Git.command
        def stall_ack(git, args):
            real = command(git, args)
            return [sys.executable, '-S', str(relay), *real] if args[0] == 'update-ref' else real
        patch = self.patch_for('shared.py', b'selected\n')
        before = (self.repo / '.git/index').read_bytes()
        with mock.patch.object(core.Git, 'command', new=stall_ack):
            result = self.land(patch, git_timeout=0.5)
        self.assertEqual(result['status'], 'landing_uncertain')
        self.assertFalse(result['ref_update_confirmed'])
        self.assertEqual(result['new_commit'], self.git('rev-parse', 'HEAD').stdout.decode().strip())
        self.assertEqual((self.repo / '.git/index').read_bytes(), before)

    def test_interrupt_after_actual_commit_ack_is_not_a_refusal(self) -> None:
        read, count = core._read_ack, 0
        def interrupt(process, timeout):
            nonlocal count
            value = read(process, timeout); count += 1
            if count == 3:
                raise KeyboardInterrupt()
            return value
        patch = self.patch_for('shared.py', b'selected\n')
        with mock.patch.object(core, '_read_ack', new=interrupt):
            result = self.land(patch)
        self.assertEqual(result['status'], 'landing_uncertain')
        self.assertEqual(result['new_commit'], self.git('rev-parse', 'HEAD').stdout.decode().strip())

    def test_interrupt_during_settlement_retains_confirmation(self) -> None:
        patch = self.patch_for('shared.py', b'selected\n')
        with mock.patch.object(core.GitLock, 'publish_index', side_effect=KeyboardInterrupt()):
            result = self.land(patch)
        self.assertEqual(result['status'], 'landed_unsettled')
        self.assertTrue(result['ref_update_confirmed'])

    def test_invalid_timeouts_refuse(self) -> None:
        patch = self.patch_for('shared.py', b'selected\n')
        for timeout in (0, -1, float('nan'), float('inf')):
            with self.subTest(timeout=timeout), self.assertRaises(core.Refusal) as caught:
                self.land(patch, git_timeout=timeout)
            self.assertEqual(caught.exception.code, 'invalid_timeout')

    def test_invalid_signing_boolean_refuses_before_commit_tree(self) -> None:
        patch = self.patch_for('shared.py', b'selected\n')
        before = (self.repo / '.git/index').read_bytes()
        self.git('config', 'commit.gpgSign', 'not-a-boolean')
        run, commands = core.Git.run, []
        def observe(git, args, **kwargs):
            commands.append(args[0])
            return run(git, args, **kwargs)
        with mock.patch.object(core.Git, 'run', new=observe), self.assertRaises(core.Refusal) as caught:
            self.land(patch)
        self.assertEqual(caught.exception.code, 'signing_policy')
        self.assertNotIn('commit-tree', commands)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(), self.parent)
        self.assertEqual((self.repo / '.git/index').read_bytes(), before)


    def test_stdout_delivery_failure_does_not_refuse_landed_commit(self) -> None:
        selected = b'selected\n'
        patch_path = Path(self.temp.name) / 'selected.patch'
        patch_path.write_bytes(self.patch_for('shared.py', selected))
        stderr = []
        def broken_output(receipt, stream):
            if stream is sys.stderr:
                stderr.append(core.caller_receipt(receipt))
            else:
                raise BrokenPipeError('consumer closed stdout')
        with mock.patch.object(core, '_emit_receipt', side_effect=broken_output):
            code = core.main(['commit', '--repo', str(self.repo), '--patch', str(patch_path),
                              '--path', 'shared.py', '--expected-parent', self.parent,
                              '--message', 'selected'])
        self.assertEqual(code, 3)
        self.assertEqual(stderr[0]['status'], 'committed')
        self.assertTrue(stderr[0]['ref_update_confirmed'])
        self.assertEqual(stderr[0]['receipt_delivery'], 'stderr_fallback')
        self.assertEqual(self.blob('HEAD', 'shared.py'), selected)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(), stderr[0]['new_commit'])

    def test_cleanup_interrupt_retains_confirmed_receipt(self) -> None:
        original = core.GitLock.__exit__
        def interrupted(lock: core.GitLock, *args: object) -> None:
            original(lock, *args)
            raise KeyboardInterrupt('cleanup interrupted')
        with mock.patch.object(core.GitLock, '__exit__', interrupted):
            result = self.land(self.patch_for('shared.py', b'selected\n'))
        self.assertEqual(result['status'], 'landed_unsettled')
        self.assertTrue(result['ref_update_confirmed'])
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.decode().strip(), result['new_commit'])

    def test_resolve_undo_records_survive_unrelated_sync(self) -> None:
        # Populate real REUC data through Git, then compare Git's independent view.
        oids = [self.git('hash-object', '-w', '--stdin', data=value).stdout.strip()
                for value in (b'base\n', b'left\n', b'right\n')]
        self.git('update-index', '--force-remove', 'foreign.txt')
        records = b''.join(b'100644 ' + oid + b' ' + str(stage).encode()
                           + b'\tforeign.txt\n' for stage, oid in enumerate(oids, 1))
        self.git('update-index', '--index-info', data=records)
        self.git('add', 'foreign.txt')  # Resolved stage-0 entry; undo stages remain.
        before = self.git('ls-files', '--resolve-undo', '-z').stdout
        self.assertTrue(before)
        result = self.land(self.patch_for('shared.py', b'selected\n'))
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(self.git('ls-files', '--resolve-undo', '-z').stdout, before)

    def test_no_newline_at_eof_patch_keeps_selected_and_foreign_bytes(self) -> None:
        selected = self.base.replace('line 01\n', 'A selected\n').rstrip('\n').encode()
        patch = self.patch_for('shared.py', selected)
        self.assertIn(b'No newline at end of file', patch)
        staged = self.base.replace('line 12\n', 'B staged\n').encode()
        (self.repo / 'shared.py').write_bytes(staged)
        self.git('add', 'shared.py')
        work = staged + b'C without newline'
        (self.repo / 'shared.py').write_bytes(work)
        result = self.land(patch)
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(self.blob('HEAD', 'shared.py'), selected)
        self.assertEqual(self.blob('', 'shared.py'), staged)
        self.assertEqual((self.repo / 'shared.py').read_bytes(), work)


if __name__ == '__main__':
    unittest.main()
