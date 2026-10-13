#!/usr/bin/env python3
"""Commit an explicit patch through a private index in a shared Git checkout.

Working files are never written. Pre-existing staged blobs are preserved, even
on a changed path. Only changed index entries equal to the captured parent are
synchronized. This is a cooperating-writers tool, not a filesystem sandbox.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Sequence

SCHEMA = "one_checkout_receipt_v1"
IDENTITY_ENV = {
    "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE",
    "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE",
}


class Refusal(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RefOutcome(RuntimeError):
    """The commit request crossed the irreversible transaction boundary."""
    def __init__(self, message: str, *, confirmed: bool) -> None:
        super().__init__(message)
        self.confirmed = confirmed


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps({"schema": SCHEMA, "status": "refused", "code": "arguments",
                          "message": message}, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)


class Git:
    def __init__(self, root: Path) -> None:
        forbidden = sorted(k for k in os.environ if k.startswith("GIT_") and k not in IDENTITY_ENV)
        if forbidden:
            raise Refusal("unsafe_environment", "Unset inherited Git control variables: " + ", ".join(forbidden))
        self.root = root
        self.env = dict(os.environ)
        self.env.update(GIT_OPTIONAL_LOCKS="0", GIT_LITERAL_PATHSPECS="1")
        self.executable = shutil.which("git")
        if not self.executable:
            raise Refusal("git_unavailable", "Git is required.")

    def command(self, args: Sequence[str]) -> list[str]:
        assert self.executable is not None
        return [self.executable, "--no-replace-objects", "--no-optional-locks", "-c", "core.fsmonitor=false",
                "-c", "core.splitIndex=false", "-c", "core.hooksPath=" + os.devnull, *args]

    def run(self, args: Sequence[str], *, env: dict[str, str] | None = None,
            data: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            self.command(args), cwd=self.root,
            env={**self.env, **(env or {})}, input=data, capture_output=True,
        )
        if check and result.returncode:
            raise Refusal("git_refused", "Git refused " + str(args[0]) + "; inspect the patch or repository state.")
        return result

    def text(self, args: Sequence[str], **kwargs: object) -> str:
        return os.fsdecode(self.run(args, **kwargs).stdout).removesuffix("\n")

    def path(self, name: str) -> Path:
        return Path(self.text(["rev-parse", "--path-format=absolute", "--git-path", name]))


class GitLock:
    """Own one Git lock by inode; never delete a replacement writer's lock."""
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None
        self.identity: tuple[int, int] | None = None
        self.published = False

    def __enter__(self) -> "GitLock":
        try:
            self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise Refusal("git_locked", "A Git writer owns " + self.path.name + "; retry after it finishes.") from exc
        info = os.fstat(self.fd)
        self.identity = (info.st_dev, info.st_ino)
        return self

    def owns_path(self) -> bool:
        try:
            info = self.path.lstat()
            return stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == self.identity
        except FileNotFoundError:
            return False

    def require_owned(self) -> None:
        if not self.owns_path():
            raise Refusal("lock_changed", "Git lock ownership changed; no replacement lock will be removed.")

    def publish_index(self, content: bytes, target: Path, mode: int) -> None:
        self.require_owned()
        assert self.fd is not None
        os.fchmod(self.fd, mode)
        # Write through the owned descriptor, not a pathname another writer can replace.
        with os.fdopen(os.dup(self.fd), "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        self.require_owned()
        os.replace(self.path, target)
        self.published = True

    def __exit__(self, *_: object) -> None:
        try:
            if not self.published and self.owns_path():
                self.path.unlink()
        finally:
            if self.fd is not None:
                os.close(self.fd)


class LandingResources(ExitStack):
    """Cleanup failure must not disguise an already-landed commit as refusal."""
    landed_receipt: dict[str, object] | None = None

    def __exit__(self, *exc: object) -> bool:
        try:
            return super().__exit__(*exc)
        except Exception as error:
            if not self.landed_receipt:
                raise
            self.landed_receipt.update(
                status=("landing_uncertain" if self.landed_receipt["status"] == "landing_uncertain"
                        else "landed_unsettled"), cleanup_error=str(error),
                recovery="Inspect new_commit, the branch/reflog and leftover locks or scratch before any retry.",
            )
            return False


def _paths(values: Sequence[str]) -> list[str]:
    if not values:
        raise Refusal("paths_required", "Declare every changed file with --path.")
    result: list[str] = []
    for value in values:
        parts = value.split("/")
        if (not value or "\0" in value or PurePosixPath(value).is_absolute()
                or re.match(r"^[A-Za-z]:", value)
                or any(p in ("", ".", "..") or p.lower() == ".git" for p in parts)):
            raise Refusal("invalid_path", "Paths must be exact repository-relative files without traversal.")
        result.append(value)
    if len(set(result)) != len(result):
        raise Refusal("duplicate_path", "Declare each file once.")
    return sorted(result)


def _head(git: Git) -> tuple[str, str]:
    branch = git.run(["symbolic-ref", "--quiet", "HEAD"], check=False)
    ref = os.fsdecode(branch.stdout).strip()
    if branch.returncode or not ref.startswith("refs/heads/"):
        raise Refusal("named_branch_required", "Use a named branch with an existing commit.")
    oid = git.run(["rev-parse", "--verify", "HEAD^{commit}"], check=False)
    if oid.returncode:
        raise Refusal("unborn_branch", "Create the initial commit before using one-checkout.")
    return ref, os.fsdecode(oid.stdout).strip()


def _index_bytes(index: Path) -> tuple[bytes, int]:
    try:
        info = index.lstat()
    except FileNotFoundError as exc:
        raise Refusal("index_missing", "The checkout has no index; restore its index before landing.") from exc
    if not stat.S_ISREG(info.st_mode):
        raise Refusal("unsupported_index", "The shared Git index must be a regular file.")
    return index.read_bytes(), stat.S_IMODE(info.st_mode)


def _entries(git: Git, env: dict[str, str]) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    for record in git.run(["ls-files", "--stage", "-z"], env=env).stdout.split(b"\0"):
        if not record:
            continue
        meta, raw_path = record.split(b"\t", 1)
        mode, oid, stage = meta.decode("ascii").split()
        if stage != "0":
            raise Refusal("unmerged_index", "Resolve unmerged index entries before using one-checkout.")
        entries[os.fsdecode(raw_path)] = (mode, oid)
    return entries


def _check_index_kind(git: Git) -> None:
    shared = git.text(["-c", "core.splitIndex=true", "rev-parse", "--shared-index-path"])
    # Git 2.39 reports an all-zero sentinel for ordinary full indexes when
    # splitIndex is enabled only for this read. An actual split pointer is nonzero.
    if shared and not re.fullmatch(r"sharedindex\.(?:0{40}|0{64})", Path(shared).name):
        raise Refusal("split_index", "Split indexes are not supported by this version.")
    sparse = git.run(["config", "--bool", "core.sparseCheckout"], check=False)
    if sparse.stdout.strip() == b"true":
        raise Refusal("sparse_index", "Sparse checkouts are not supported by this version.")


def _verify_prepared_head(git: Git, branch: str, parent: str) -> None:
    if _head(git) != (branch, parent):
        raise Refusal("head_race", "HEAD changed before the ref transaction; the patch was not landed.")


def _publish_ref(git: Git, branch: str, new_commit: str, parent: str) -> None:
    # Native prepare locks both symbolic HEAD and the branch it resolved. Check
    # that target *while locked*: two branches may legitimately share one OID.
    process = subprocess.Popen(git.command(["update-ref", "-m", "one-checkout: explicit patch", "--stdin"]),
                               cwd=git.root, env=git.env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert process.stdin is not None and process.stdout is not None
    committed = False
    commit_requested = False
    outcome_error: Exception | None = None
    try:
        process.stdin.write(f"start\nupdate HEAD {new_commit} {parent}\nprepare\n".encode("ascii"))
        process.stdin.flush()
        if process.stdout.readline() != b"start: ok\n" or process.stdout.readline() != b"prepare: ok\n":
            raise Refusal("ref_race", "The branch changed or is locked; the patch was not landed.")
        _verify_prepared_head(git, branch, parent)
        # Mark before write/flush: a failed flush can still have delivered bytes.
        commit_requested = True
        process.stdin.write(b"commit\n")
        process.stdin.flush()
        if process.stdout.readline() != b"commit: ok\n":
            raise RefOutcome("Git did not acknowledge the commit request.", confirmed=False)
        committed = True
    except Exception as error:
        if not commit_requested:
            raise
        outcome_error = error
    finally:
        if not commit_requested and process.poll() is None:
            try:
                process.stdin.write(b"abort\n")
                process.stdin.flush()
            except OSError:
                pass
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdout.close()
        except Exception as error:
            if not commit_requested:
                raise
            outcome_error = outcome_error or error
    if outcome_error:
        raise RefOutcome(str(outcome_error), confirmed=committed) from outcome_error


def commit_patch(*, repo: Path | str, patch: bytes, paths: Sequence[str],
                 expected_parent: str, message: str = "", dry_run: bool = False) -> dict[str, object]:
    """Plan or land one caller-selected patch. No working-tree file is modified."""
    declared = _paths(paths)
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", expected_parent):
        raise Refusal("invalid_parent", "--expected-parent must be a full commit object ID.")
    expected_parent = expected_parent.lower()
    if not patch:
        raise Refusal("empty_patch", "Supply a nonempty patch against the captured parent.")
    if not dry_run and not message.strip():
        raise Refusal("message_required", "A commit message is required.")
    git = Git(Path(repo).resolve())
    root = git.text(["rev-parse", "--show-toplevel"])
    if Path(root).resolve() != git.root:
        raise Refusal("repository_root_required", "--repo must name the working-tree root.")
    index = git.path("index")
    head_path = git.path("HEAD")
    if not head_path.is_file() or head_path.is_symlink():
        raise Refusal("unsupported_head", "HEAD must be a regular symbolic-ref file.")
    with LandingResources() as stack:
        index_lock = None
        if not dry_run:
            if os.path.lexists(head_path.with_name(head_path.name + ".lock")):
                raise Refusal("git_locked", "A Git writer owns HEAD.lock; retry after it finishes.")
            index_lock = stack.enter_context(GitLock(index.with_name(index.name + ".lock")))
        branch, parent = _head(git)
        if parent != expected_parent:
            raise Refusal("stale_parent", "HEAD differs from the captured parent; review the patch against current HEAD.")
        _check_index_kind(git)
        original, index_mode = _index_bytes(index)
        scratch = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="one-checkout-")))
        private_index = scratch / "patch-index"
        original_index = scratch / "original-index"
        original_index.write_bytes(original)
        shared_env = {"GIT_INDEX_FILE": str(original_index)}
        original_entries = _entries(git, shared_env)
        env = {"GIT_INDEX_FILE": str(private_index)}
        if dry_run:
            objects = scratch / "objects"
            objects.mkdir()
            env.update(GIT_OBJECT_DIRECTORY=str(objects),
                       GIT_ALTERNATE_OBJECT_DIRECTORIES=json.dumps(str(git.path("objects")), ensure_ascii=False))
        git.run(["read-tree", parent], env=env)
        base_entries = _entries(git, env)
        git.run(["apply", "--cached", "--whitespace=nowarn", "-"], env=env, data=patch)
        changed = sorted(os.fsdecode(p) for p in git.run(
            ["diff-index", "--cached", "--no-renames", "--name-only", "-z", parent], env=env,
        ).stdout.split(b"\0") if p)
        if not changed:
            raise Refusal("no_changes", "The patch makes no changes.")
        if changed != declared:
            raise Refusal("scope_mismatch", "Patch paths must exactly equal the declared file paths.")
        candidate_entries = _entries(git, env)
        if any(entry[0] in ("040000", "160000") for p in changed
               for entry in (base_entries.get(p), candidate_entries.get(p)) if entry):
            raise Refusal("unsupported_entry", "Tree and submodule edits are not supported.")
        tree = git.text(["write-tree"], env=env)
        synchronize = [p for p in changed if original_entries.get(p) == base_entries.get(p)]
        preserve = [p for p in changed if p not in synchronize]
        receipt: dict[str, object] = {
            "schema": SCHEMA, "status": "planned", "dry_run": dry_run,
            "branch": branch, "parent": parent, "tree": tree,
            "patch_sha256": hashlib.sha256(patch).hexdigest(), "changed_paths": changed,
            "preserved_staged_paths": preserve, "synchronized_index_paths": synchronize,
            "new_commit": None, "worktree_written": False, "hooks_run": False,
        }
        if dry_run:
            return receipt
        # Build the future shared index before advancing the ref. Its foreign
        # entries come from the exact locked snapshot, never from the patch index.
        next_index = scratch / "next-index"
        next_index.write_bytes(original)
        next_env = {"GIT_INDEX_FILE": str(next_index)}
        zero = "0" * len(parent)
        records = []
        for path in synchronize:
            mode, oid = candidate_entries.get(path, ("0", zero))
            records.append(mode.encode() + b" " + oid.encode() + b"\t" + os.fsencode(path) + b"\0")
        if records:
            git.run(["update-index", "-z", "--index-info"], env=next_env, data=b"".join(records))
        expected_entries = dict(original_entries)
        for path in synchronize:
            if path in candidate_entries:
                expected_entries[path] = candidate_entries[path]
            else:
                expected_entries.pop(path, None)
        if _entries(git, next_env) != expected_entries:
            raise Refusal("index_validation_failed", "The prepared index did not preserve the declared entries.")
        next_bytes = next_index.read_bytes()
        assert index_lock is not None
        index_lock.require_owned()
        if _index_bytes(index)[0] != original:
            raise Refusal("index_race", "The shared index changed despite its lock; no commit was landed.")
        if _head(git) != (branch, parent):
            raise Refusal("head_race", "HEAD changed while preparing the patch; no commit was landed.")
        commit_args = ["commit-tree", tree, "-p", parent]
        signing = git.run(["config", "--bool", "commit.gpgSign"], check=False)
        if signing.stdout.strip() == b"true":
            commit_args.append("-S")
        new_commit = git.text(commit_args, data=message.encode("utf-8"))
        index_lock.require_owned()
        if _index_bytes(index)[0] != original or _head(git) != (branch, parent):
            raise Refusal("pre_land_race", "The checkout changed before landing; no commit was landed.")
        try:
            _publish_ref(git, branch, new_commit, parent)
        except RefOutcome as error:
            receipt.update(new_commit=new_commit,
                           status="landed_unsettled" if error.confirmed else "landing_uncertain",
                           ref_update_confirmed=error.confirmed, ref_error=str(error),
                           recovery="Inspect new_commit and the branch/reflog before any retry; the commit request may have landed.")
            stack.landed_receipt = receipt
            return receipt
        receipt.update(new_commit=new_commit, status="committed")
        stack.landed_receipt = receipt
        try:
            index_lock.require_owned()
            if _index_bytes(index)[0] != original:
                raise Refusal("index_race", "The shared index changed after landing.")
            if _head(git) != (branch, new_commit):
                raise Refusal("post_land_ref_race", "The branch changed after landing; its index was not synchronized.")
            index_lock.publish_index(next_bytes, index, index_mode)
        except (OSError, Refusal) as exc:
            # Never hide a landed commit behind an ordinary failed-commit error,
            # or roll back a ref after another actor may already depend on it.
            receipt.update(status="landed_unsettled", index_error=str(exc),
                           recovery="Inspect new_commit and the shared index before any retry; the commit already exists.")
        return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = JsonArgumentParser(prog="one-checkout", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "commit"):
        child = sub.add_parser(name)
        child.add_argument("--repo", type=Path, default=Path.cwd())
        child.add_argument("--patch", type=Path, required=True)
        child.add_argument("--path", action="append", required=True)
        child.add_argument("--expected-parent", required=True)
        child.add_argument("--dry-run", action="store_true")
        messages = child.add_mutually_exclusive_group()
        messages.add_argument("--message", default="")
        messages.add_argument("--message-file", type=Path)
    args = parser.parse_args(argv)
    try:
        message = args.message_file.read_text(encoding="utf-8") if args.message_file else args.message
        receipt = commit_patch(repo=args.repo, patch=args.patch.read_bytes(), paths=args.path,
                               expected_parent=args.expected_parent, message=message,
                               dry_run=args.command == "plan" or args.dry_run)
        print(json.dumps(receipt, ensure_ascii=True, sort_keys=True))
        return 3 if receipt["status"] in ("landed_unsettled", "landing_uncertain") else 0
    except (Refusal, OSError, UnicodeError) as exc:
        print(json.dumps({"schema": SCHEMA, "status": "refused", "code": getattr(exc, "code", "io_error"),
                          "message": str(exc)}, ensure_ascii=True, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
