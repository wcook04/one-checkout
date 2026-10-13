#!/usr/bin/env python3
"""Install or reversibly remove the complete skill for Codex and Claude Code."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

SOURCE = Path(__file__).resolve().parents[1] / 'skills' / 'one-checkout'
MANIFEST = 'bundle.json'
MEMBERS = ('SKILL.md', 'LICENSE', 'scripts/one_checkout.py', 'scripts/demo.py',
           'scripts/doctor.py', 'references/patch-selection.md', 'references/recovery.md')


class InstallationError(OSError):
    def __init__(self, message: str, plans: list[dict]):
        super().__init__(message)
        self.plans = plans


def physical(path: Path) -> None:
    """Reject stable symlink ancestry; cooperating writers remain an assumption."""
    current = path.absolute()
    for part in reversed((current, *current.parents)):
        if part.is_symlink():
            raise ValueError(f'symlinked installation path: {part}')


def inventory(root: Path) -> set[str]:
    physical(root)
    if not root.is_dir():
        raise ValueError(f'skill is not a directory: {root}')
    found = set()
    def failed(error):
        raise error
    for folder, directories, files in os.walk(root, followlinks=False, onerror=failed):
        base = Path(folder)
        for name in directories:
            if (base / name).is_symlink():
                raise ValueError(f'symlinked skill directory: {base / name}')
        for name in files:
            path = base / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError(f'skill member is not regular: {path}')
            if name == '.DS_Store' or ('__pycache__' in path.relative_to(root).parts and path.suffix == '.pyc'):
                continue
            found.add(path.relative_to(root).as_posix())
    return found


def snapshot(source: Path) -> dict[str, tuple[bytes, int]]:
    actual = inventory(source)
    raw = (source / MANIFEST).read_bytes()
    manifest = json.loads(raw)
    names = manifest.get('members') if isinstance(manifest, dict) else None
    if not isinstance(manifest, dict) or manifest.get('schema') != 'one_checkout_bundle_v1' or not isinstance(names, list):
        raise ValueError('invalid skill bundle manifest')
    for name in names:
        if (not isinstance(name, str) or not name or '\\' in name or ':' in name
                or any(ord(c) < 32 for c in name) or PurePosixPath(name).is_absolute()
                or any(p in ('', '.', '..', '.git') for p in name.split('/')) or name == MANIFEST):
            raise ValueError('invalid skill bundle member')
    if len(names) != len(set(names)) or not {'SKILL.md', 'LICENSE', 'scripts/one_checkout.py'} <= set(names):
        raise ValueError('duplicate or missing essential skill members')
    expected = set(names) | {MANIFEST}
    if actual != expected:
        raise ValueError(f'skill inventory mismatch: missing={sorted(expected-actual)}, undeclared={sorted(actual-expected)}')
    payload = {}
    for name in sorted(expected):
        path = source / name
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode):
            raise ValueError(f'source changed while reading: {name}')
        payload[name] = (data, stat.S_IMODE(after.st_mode))
    if payload[MANIFEST][0] != raw or any((source / n).read_bytes() != d for n, (d, _) in payload.items()):
        raise ValueError('source changed while taking snapshot')
    return payload


def matches(payload: dict, destination: Path) -> bool:
    try:
        if inventory(destination) != set(payload):
            return False
        return all((destination / n).read_bytes() == data
                   and stat.S_IMODE((destination / n).stat().st_mode) == mode
                   for n, (data, mode) in payload.items())
    except (OSError, ValueError):
        return False


def same_bundle(source: Path, destination: Path) -> bool:
    return matches(snapshot(source), destination)


class InstallLock:
    def __init__(self, root: Path, errors: list):
        self.path = root / 'one-checkout-install.lock'
        self.fd = None
        self.errors = errors
    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        info = os.fstat(self.fd)
        self.identity = (info.st_dev, info.st_ino)
        return self
    def __exit__(self, *_):
        try:
            info = self.path.lstat()
            if (info.st_dev, info.st_ino) != self.identity:
                raise OSError(f'installer lock replaced; retained: {self.path}')
            self.path.unlink()
        except OSError as error:
            self.errors.append({'path': str(self.path), 'error': str(error)})
        finally:
            try:
                os.close(self.fd)
            except OSError as error:
                self.errors.append({'path': str(self.path), 'error': str(error)})


def plans_for(destinations: list[Path], payload: dict | None, *, backup_existing: bool) -> list[dict]:
    if len(set(map(str, destinations))) != len(destinations):
        raise ValueError('duplicate installation destination')
    plans = []
    for destination in destinations:
        physical(destination)
        exists = destination.exists()
        if exists and not destination.is_dir():
            raise ValueError(f'destination is not a directory: {destination}')
        unchanged = exists and payload is not None and matches(payload, destination)
        if exists and payload is not None and not unchanged and not backup_existing:
            raise ValueError(f'destination differs: {destination}; inspect it before using --backup-existing')
        action = ('remove' if exists else 'absent') if payload is None else ('unchanged' if unchanged else 'backup-and-install' if exists else 'install')
        plans.append({'destination': str(destination), 'action': action, 'status': 'planned'})
    return plans


def write_payload(payload: dict, stage: Path) -> None:
    for name, (data, mode) in payload.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
    if not matches(payload, stage):
        raise ValueError('staged installation differs from source snapshot')


def publish(stage: Path, destination: Path) -> None:
    # Exclusive reservation prevents replacing a directory created after preflight.
    destination.mkdir()
    identity = destination.stat()
    try:
        current = destination.lstat()
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            raise OSError('destination reservation changed')
        stage.rename(destination)
    except BaseException:
        current = destination.lstat()
        if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
            destination.rmdir()
        raise


def change_installations(payload: dict | None, plans: list[dict], *, dry_run: bool) -> list[dict]:
    if dry_run:
        return plans
    stages = []
    cleanup_errors = []
    try:
        with ExitStack() as locks:
            for root in sorted({Path(p['destination']).parent.parent for p in plans if p['action'] not in ('unchanged', 'absent')}, key=str):
                physical(root)
                root.mkdir(parents=True, exist_ok=True)
                locks.enter_context(InstallLock(root, cleanup_errors))
            # Stage every host before changing any installed copy.
            for plan in plans:
                if plan['action'] in ('unchanged', 'absent', 'remove'):
                    continue
                root = Path(plan['destination']).parent.parent
                stage = Path(tempfile.mkdtemp(prefix='one-checkout-stage-', dir=root))
                stages.append(stage)
                plan['stage'] = str(stage)
                write_payload(payload, stage)
            for plan in plans:
                destination = Path(plan['destination'])
                physical(destination)
                if plan['action'] == 'unchanged':
                    if not matches(payload, destination):
                        raise ValueError(f'unchanged destination changed: {destination}')
                    plan['status'] = 'unchanged'
                    continue
                if plan['action'] == 'absent':
                    if destination.exists():
                        raise ValueError(f'absent destination appeared: {destination}')
                    plan['status'] = 'absent'
                    continue
                backup = None
                if plan['action'] in ('backup-and-install', 'remove'):
                    if not destination.is_dir():
                        raise ValueError(f'destination changed before backup: {destination}')
                    backup_root = destination.parent.parent / 'one-checkout-backups'
                    physical(backup_root)
                    backup_root.mkdir(exist_ok=True)
                    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                    backup = backup_root / f'{stamp}-{uuid.uuid4().hex}'
                    destination.rename(backup)
                    plan.update(backup=str(backup), status='backed_up')
                if plan['action'] == 'remove':
                    plan['status'] = 'removed'
                    continue
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    publish(Path(plan['stage']), destination)
                    plan['status'] = 'installed'
                    plan.pop('stage')
                except BaseException:
                    if backup is not None and not os.path.lexists(destination):
                        try:
                            backup.rename(destination)
                            plan['status'] = 'restored'
                        except OSError as recovery:
                            plan.update(status='backup_retained', recovery_error=str(recovery))
                    raise
    except (OSError, ValueError) as error:
        failure = error
    else:
        failure = None
    for stage in stages:
        if stage.exists():
            try:
                shutil.rmtree(stage)
            except OSError as error:
                cleanup_errors.append({'path': str(stage), 'error': str(error)})
    if failure is not None or cleanup_errors:
        if cleanup_errors:
            for plan in plans:
                plan['cleanup_errors'] = cleanup_errors
        raise InstallationError(str(failure or 'stage cleanup failed'), plans)
    return plans


def install_skill(source: Path, destinations: list[Path], *, backup_existing: bool = False, dry_run: bool = False) -> list[dict]:
    return install_payload(snapshot(source), destinations, backup_existing=backup_existing, dry_run=dry_run)


def install_payload(payload: dict, destinations: list[Path], *, backup_existing: bool = False, dry_run: bool = False) -> list[dict]:
    plans = plans_for(destinations, payload, backup_existing=backup_existing)
    for plan in plans:
        plan['files'] = {name: {'sha256': hashlib.sha256(data).hexdigest(), 'mode': oct(mode)}
                         for name, (data, mode) in payload.items()}
    return change_installations(payload, plans, dry_run=dry_run)


def source_identity(source: Path, *, allow_dirty: bool, payload: dict | None = None) -> dict:
    marker = False
    for parent in (source, *source.parents):
        try:
            (parent / '.git').lstat()
        except FileNotFoundError:
            continue
        marker = True
        break
    if not marker:
        return {'kind': 'archive_snapshot', 'commit': None, 'dirty': None}
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    argv = ['git', '--no-optional-locks', '--no-replace-objects', '--literal-pathspecs', '-c', 'core.fsmonitor=false', '-C', str(source)]
    result = subprocess.run(argv + ['rev-parse', '--show-toplevel'], env=env, capture_output=True, timeout=10)
    if result.returncode:
        raise ValueError('Git could not establish the source checkout identity')
    root = Path(os.fsdecode(result.stdout).removesuffix('\n'))
    relative = source.relative_to(root).as_posix()
    argv[-1] = str(root)
    state = subprocess.run(argv + ['status', '--porcelain=v1', '--untracked-files=all', '--', relative], env=env, capture_output=True, check=True, timeout=10)
    dirty = bool(state.stdout)
    head = subprocess.run(argv + ['rev-parse', 'HEAD'], env=env, capture_output=True, check=True, timeout=10)
    commit = os.fsdecode(head.stdout).strip()
    tree = subprocess.run(argv + ['ls-tree', '-r', '-z', commit, '--', relative], env=env, capture_output=True, check=True, timeout=10)
    entries = {}
    for record in tree.stdout.split(b'\0'):
        if record:
            metadata, path = record.split(b'\t', 1)
            entries[os.fsdecode(path)] = tuple(metadata.split(b' '))
    # Index flags and core.filemode can suppress status changes. Compare the exact
    # captured bytes and Git executable bit with HEAD, independently of status.
    payload = snapshot(source) if payload is None else payload
    for name, (data, mode) in payload.items():
        blob = subprocess.run(argv + ['hash-object', '--stdin'], input=data, env=env, capture_output=True, check=True, timeout=10)
        expected = (b'100755' if mode & 0o100 else b'100644', b'blob', blob.stdout.strip())
        if entries.get((PurePosixPath(relative) / name).as_posix()) != expected:
            dirty = True
    if dirty and not allow_dirty:
        raise ValueError('skill source has uncommitted changes; review it before using --allow-dirty-source')
    return {'kind': 'git_snapshot', 'commit': commit, 'dirty': dirty}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', choices=('both', 'codex', 'claude'), default='both')
    parser.add_argument('--scope', choices=('user', 'project'), default='user')
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--backup-existing', action='store_true')
    parser.add_argument('--allow-dirty-source', action='store_true')
    parser.add_argument('--uninstall', action='store_true', help='move the installed skill into a recoverable backup')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    raw_base = Path.home() if args.scope == 'user' else args.project
    try:
        physical(raw_base)
    except (OSError, ValueError) as error:
        print(json.dumps({'ok': False, 'error': str(error), 'skills': []}), file=sys.stderr)
        return 2
    base = raw_base.resolve()
    if not base.is_dir():
        parser.error('installation base must be an existing directory')
    agents = ('codex', 'claude') if args.agent == 'both' else (args.agent,)
    roots = {'codex': '.agents', 'claude': '.claude'}
    destinations = [base / roots[a] / 'skills/one-checkout' for a in agents]
    if args.scope == 'user' and 'claude' in agents and os.environ.get('CLAUDE_CONFIG_DIR'):
        profile = Path(os.environ['CLAUDE_CONFIG_DIR'])
        if not profile.is_absolute():
            print(json.dumps({'ok': False, 'error': 'CLAUDE_CONFIG_DIR must be absolute', 'skills': []}), file=sys.stderr)
            return 2
        destinations[agents.index('claude')] = profile / 'skills/one-checkout'
    try:
        if args.uninstall:
            identity = None
            plans = change_installations(None, plans_for(destinations, None, backup_existing=True), dry_run=args.dry_run)
        else:
            payload = snapshot(SOURCE)
            identity = source_identity(SOURCE, allow_dirty=args.allow_dirty_source, payload=payload)
            plans = install_payload(payload, destinations, backup_existing=args.backup_existing, dry_run=args.dry_run)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'ok': False, 'error': str(error), 'skills': getattr(error, 'plans', [])}), file=sys.stderr)
        return 2
    receipt = {'ok': True, 'dry_run': args.dry_run, 'source': identity,
               'agent_discovery_verified': False, 'skills': plans}
    try:
        print(json.dumps(receipt, indent=2), flush=True)
    except BrokenPipeError:
        receipt['output_error'] = 'stdout_failed; installation outcomes retained'
        print(json.dumps(receipt), file=sys.stderr)
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
