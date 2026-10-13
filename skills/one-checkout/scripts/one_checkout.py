#!/usr/bin/env python3
"""Commit an explicit patch through a private index in a shared Git checkout.

Working files are never written. Pre-existing staged blobs are preserved, even
on a changed path. Only unflagged changed index entries equal to the captured
parent are synchronized. This is a cooperating-writers tool, not a filesystem sandbox.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from enum import Enum, auto
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import select
import secrets
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Sequence, TextIO

SCHEMA = "one_checkout_receipt_v1"
MAX_RECOVERY_BYTES = 16 * 1024 * 1024
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


class RefProgress(Enum):
    PREPARING = auto()
    REQUESTED = auto()
    CONFIRMED = auto()


def caller_receipt(receipt: dict[str, object]) -> dict[str, object]:
    actions = {"refused": "review_refusal", "planned": "review_patch_and_plan",
               "committed": "done_preserve_other_work", "landing_uncertain": "inspect_branch_and_reflog",
               "landed_unsettled": "inspect_commit_and_index", "recovery_observed": "inspect_recovery_observations"}
    status = receipt["status"]
    action = ("inspect_receipt_delivery" if receipt.get("receipt_delivery") else
              "inspect_recovery_record" if "record_error" in receipt else actions[status])
    return {**receipt, "next_action": action, "automatic_retry_allowed": False,
            "ref_update_confirmed": receipt.get("ref_update_confirmed", status in ("committed", "landed_unsettled"))}


def _emit_receipt(receipt: dict[str, object], stream: TextIO) -> None:
    # Descriptor writes do not leave buffered JSON to fail again during shutdown.
    payload = (json.dumps(caller_receipt(receipt), ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        stream.write(payload.decode("ascii"))
        stream.flush()
        return
    while payload:
        written = os.write(descriptor, payload)
        if written == 0:
            raise OSError("Receipt output made no progress.")
        payload = payload[written:]


def _diagnostic(receipt: dict[str, object]) -> None:
    try:
        _emit_receipt(receipt, sys.stderr)
    except (OSError, UnicodeError, KeyboardInterrupt):
        pass


def _report_receipt(receipt: dict[str, object]) -> int:
    try:
        _emit_receipt(receipt, sys.stdout)
    except (OSError, UnicodeError, KeyboardInterrupt) as error:
        if receipt["status"] == "planned":
            _diagnostic({"schema": SCHEMA, "status": "refused", "code": "receipt_output_failed",
                         "message": "Plan output could not be delivered; no commit was attempted."})
            return 2
        _diagnostic({**receipt, "receipt_delivery": "stderr_fallback", "receipt_output_error": str(error),
                     "recovery": "Inspect the candidate, branch and index before any retry; stdout delivery failed."})
        return 3
    return 3 if receipt["status"] in ("landed_unsettled", "landing_uncertain") or "record_error" in receipt else 0


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _diagnostic({"schema": SCHEMA, "status": "refused", "code": "arguments", "message": message})
        raise SystemExit(2)


class Git:
    def __init__(self, root: Path, *, timeout: float = 30.0) -> None:
        forbidden = sorted(k for k in os.environ if k.startswith("GIT_") and k not in IDENTITY_ENV)
        if forbidden:
            raise Refusal("unsafe_environment", "Unset inherited Git control variables: " + ", ".join(forbidden))
        self.root = root
        self.timeout = timeout
        self.env = dict(os.environ)
        self.env.update(GIT_OPTIONAL_LOCKS="0", GIT_LITERAL_PATHSPECS="1",
                        GIT_NO_LAZY_FETCH="1")
        self.executable = shutil.which("git")
        if not self.executable:
            raise Refusal("git_unavailable", "Git is required.")

    def command(self, args: Sequence[str]) -> list[str]:
        assert self.executable is not None
        return [self.executable, "--no-replace-objects", "--no-optional-locks", "-c", "core.fsmonitor=false",
                "-c", "core.splitIndex=false", "-c", "core.hooksPath=" + os.devnull, *args]

    def run(self, args: Sequence[str], *, env: dict[str, str] | None = None,
            data: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        process = subprocess.Popen(
            self.command(args), cwd=self.root, env={**self.env, **(env or {})},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(input=data, timeout=self.timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            _kill_process_group(process)
            code = "git_timeout" if isinstance(error, subprocess.TimeoutExpired) else "cancelled"
            raise Refusal(code, "Git " + str(args[0]) + " was stopped; inspect the outcome before retrying.") from error
        result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        if check and result.returncode:
            raise Refusal("git_refused", "Git refused " + str(args[0]) + "; inspect the patch or repository state.")
        return result

    def text(self, args: Sequence[str], **kwargs: object) -> str:
        return os.fsdecode(self.run(args, **kwargs).stdout).removesuffix("\n")

    def path(self, name: str) -> Path:
        return Path(self.text(["rev-parse", "--path-format=absolute", "--git-path", name]))


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop only this invocation and its ordinary, non-daemonised helpers (POSIX)."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    finally:
        # Do not wait indefinitely for pipes held by a helper outside our group.
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _read_ack(process: subprocess.Popen[bytes], timeout: float) -> bytes:
    """Bound a whole acknowledgement, including a writer stalled mid-line."""
    assert process.stdout is not None
    fd = process.stdout.fileno()
    deadline = time.monotonic() + timeout
    result = bytearray()
    while len(result) < 64:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise Refusal("git_timeout", "Git ref-transaction acknowledgement timed out.")
        byte = os.read(fd, 1)
        if not byte:
            break
        result.extend(byte)
        if byte == b"\n":
            break
    return bytes(result)


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

    def publish_index(self, content: bytes, target: Path, mode: int, mtime_ns: int) -> None:
        self.require_owned()
        assert self.fd is not None
        os.fchmod(self.fd, mode)
        # Write through the owned descriptor, not a pathname another writer can replace.
        with os.fdopen(os.dup(self.fd), "wb") as stream:
            stream.write(content)
            stream.flush()
            # The index's timestamp is part of Git's racy-clean interpretation.
            os.utime(stream.fileno(), ns=(mtime_ns, mtime_ns))
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
    outcome_receipt: dict[str, object] | None = None

    def __exit__(self, *exc: object) -> bool:
        try:
            return super().__exit__(*exc)
        except (Exception, KeyboardInterrupt) as error:
            if not self.outcome_receipt:
                raise
            self.outcome_receipt.update(
                status=("landing_uncertain" if self.outcome_receipt["status"] == "landing_uncertain"
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


def _index_snapshot(index: Path) -> tuple[bytes, int, int]:
    try:
        info = index.lstat()
    except FileNotFoundError as exc:
        raise Refusal("index_missing", "The checkout has no index; restore its index before landing.") from exc
    if not stat.S_ISREG(info.st_mode):
        raise Refusal("unsupported_index", "The shared Git index must be a regular file.")
    with index.open("rb") as stream:
        captured = os.fstat(stream.fileno())
        content = stream.read()
        after = os.fstat(stream.fileno())
    if ((captured.st_dev, captured.st_ino, captured.st_mtime_ns, captured.st_size)
            != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size)
            or (captured.st_dev, captured.st_ino) != (info.st_dev, info.st_ino)):
        raise Refusal("index_race", "The shared index changed while it was captured.")
    return content, stat.S_IMODE(captured.st_mode), captured.st_mtime_ns


def _index_bytes(index: Path) -> tuple[bytes, int]:
    content, mode, _ = _index_snapshot(index)
    return content, mode


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


# Git's CE_VALID, CE_INTENT_TO_ADD and CE_SKIP_WORKTREE. See read-cache-ll.h.
SEMANTIC_INDEX_FLAGS = 0x60008000
_DEBUG_ENTRY = re.compile(
    rb"  ctime: [0-9]+:[0-9]+\n  mtime: [0-9]+:[0-9]+\n"
    rb"  dev: [0-9]+\tino: [0-9]+\n  uid: [0-9]+\tgid: [0-9]+\n"
    rb"  size: [0-9]+\tflags: ([0-9a-fA-F]+)\n"
)


def _semantic_index_flags(git: Git, env: dict[str, str]) -> dict[str, int]:
    """Read flags Git omits from --stage; refuse unfamiliar debug output.

    Paths are NUL-delimited, so embedded newlines and 'flags:' remain unambiguous.
    --debug is not a stable public format: fail closed rather than guess if it changes.
    """
    data = git.run(["ls-files", "--debug", "-z"], env=env).stdout
    result: dict[str, int] = {}
    offset = 0
    while offset < len(data):
        end = data.find(b"\0", offset)
        match = _DEBUG_ENTRY.match(data, end + 1) if end >= 0 else None
        if match is None:
            raise Refusal("unsupported_index_flags", "Cannot safely read Git index flags on this Git version.")
        flags = int(match[1], 16) & SEMANTIC_INDEX_FLAGS
        if flags:
            result[os.fsdecode(data[offset:end])] = flags
        offset = match.end()
    return result


def _check_object_policy(git: Git) -> None:
    # No implicit fetches. The environment guard is defence in depth, but older
    # supported Git versions may ignore it; refuse promisor configurations for now.
    partial = git.run(["config", "--get", "extensions.partialClone"], check=False)
    promisors = git.run(["config", "--bool", "--get-regexp", r"^remote\..*\.promisor$"], check=False)
    if partial.returncode not in (0, 1) or promisors.returncode not in (0, 1):
        raise Refusal("object_policy", "Cannot establish the repository's object-fetch policy.")
    if partial.returncode == 0 or any(line.endswith(b" true") for line in promisors.stdout.splitlines()):
        raise Refusal("partial_clone", "Partial/promisor repositories are not supported; use a complete local clone.")


def _check_index_kind(git: Git) -> None:
    # Inspect the actual representation, not just its possibly stale config.
    entries = git.run(["ls-files", "--sparse", "--stage", "-z"]).stdout
    if any(record.startswith(b"040000 ") for record in entries.split(b"\0")):
        raise Refusal("sparse_index", "Sparse-directory index entries are not supported by this version.")
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


def _publish_ref(git: Git, branch: str, new_commit: str, parent: str,
                 record: RecoveryRecord | None = None) -> None:
    # Native prepare locks both symbolic HEAD and the branch it resolved. Check
    # that target *while locked*: two branches may legitimately share one OID.
    process = subprocess.Popen(git.command(["update-ref", "-m", "one-checkout: explicit patch", "--stdin"]),
                               cwd=git.root, env=git.env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True, bufsize=0)
    assert process.stdin is not None and process.stdout is not None
    progress = RefProgress.PREPARING
    outcome_error: BaseException | None = None
    try:
        process.stdin.write(f"start\nupdate HEAD {new_commit} {parent}\nprepare\n".encode("ascii"))
        process.stdin.flush()
        if _read_ack(process, git.timeout) != b"start: ok\n" or _read_ack(process, git.timeout) != b"prepare: ok\n":
            raise Refusal("ref_race", "The branch changed or is locked; the patch was not landed.")
        _verify_prepared_head(git, branch, parent)
        if record is not None:
            record.append("requested")  # Durable conservative boundary BEFORE request delivery.
        # Mark before write/flush: a failed flush can still have delivered bytes.
        progress = RefProgress.REQUESTED
        process.stdin.write(b"commit\n")
        process.stdin.flush()
        if _read_ack(process, git.timeout) != b"commit: ok\n":
            raise RefOutcome("Git did not acknowledge the commit request.", confirmed=False)
        progress = RefProgress.CONFIRMED
        if record is not None:
            record.receipt["ref_update_confirmed"] = True
            record.append("confirmed")
    except (Exception, KeyboardInterrupt) as error:
        if progress is RefProgress.PREPARING:
            raise
        outcome_error = error
    finally:
        if progress is RefProgress.PREPARING and process.poll() is None:
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
                returncode = process.wait(timeout=min(5.0, git.timeout))
                if returncode and progress is not RefProgress.PREPARING:
                    outcome_error = outcome_error or RuntimeError("Git exited unsuccessfully after the commit request.")
            except subprocess.TimeoutExpired:
                _kill_process_group(process)
                if progress is not RefProgress.PREPARING:
                    outcome_error = outcome_error or RuntimeError("Git did not finish after the commit request.")
            process.stdout.close()
        except (Exception, KeyboardInterrupt) as error:
            if progress is RefProgress.PREPARING:
                raise
            outcome_error = outcome_error or error
    if outcome_error:
        raise RefOutcome(str(outcome_error), confirmed=progress is RefProgress.CONFIRMED) from outcome_error


def _validate_plan(plan: object) -> dict[str, object]:
    """A saved plan binds intent, not authorship, authorisation or lock ownership."""
    if not isinstance(plan, dict):
        raise Refusal("invalid_plan", "--plan-file must contain one JSON plan object.")
    if plan.get("schema") != SCHEMA or plan.get("status") != "planned" or plan.get("dry_run") is not True:
        raise Refusal("invalid_plan", "Use the successful receipt from plan, not a commit or recovery receipt.")
    for key in ("repository", "branch", "parent", "tree", "patch_sha256"):
        if not isinstance(plan.get(key), str) or not plan[key]:
            raise Refusal("invalid_plan", "Plan is missing a valid " + key + "; generate and review a new plan.")
    if not Path(plan["repository"]).is_absolute() or not plan["branch"].startswith("refs/heads/"):
        raise Refusal("invalid_plan", "Plan must name an absolute repository and a full branch ref.")
    for key in ("parent", "tree"):
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", plan[key]):
            raise Refusal("invalid_plan", "Plan has an invalid " + key + " object ID.")
    if not re.fullmatch(r"[0-9a-f]{64}", plan["patch_sha256"]):
        raise Refusal("invalid_plan", "Plan has an invalid patch digest.")
    paths = plan.get("changed_paths")
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
        raise Refusal("invalid_plan", "Plan must contain exact changed_paths.")
    try:
        if _paths(paths) != paths:
            raise Refusal("invalid_plan", "Plan changed_paths must be sorted and unique.")
    except Refusal as error:
        raise Refusal("invalid_plan", "Plan contains invalid changed_paths.") from error
    return plan


def _match_plan(plan: dict[str, object], actual: dict[str, object]) -> None:
    for key, value in actual.items():
        if plan[key] != value:
            raise Refusal("plan_mismatch", "Reviewed plan differs in " + key
                          + "; stop, inspect and review a new plan. Do not edit the receipt to bypass this check.")


def _read_plan(path: Path) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise Refusal("invalid_plan", "Plan JSON contains a duplicate key: " + key)
            result[key] = value
        return result
    try:
        return _validate_plan(json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise Refusal("invalid_plan", "--plan-file must be a UTF-8 JSON plan receipt.") from error


def _repository_identity(git: Git) -> dict[str, object]:
    paths = {"working_tree": git.root,
             "git_directory": Path(git.text(["rev-parse", "--absolute-git-dir"])),
             "common_directory": Path(git.text(["rev-parse", "--path-format=absolute", "--git-common-dir"]))}
    identity: dict[str, object] = {}
    for name, path in paths.items():
        path = path.resolve(strict=True)
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise Refusal("record_repository", "Cannot identify the repository directories.")
        identity[name] = {"path": str(path), "device": info.st_dev, "inode": info.st_ino}
    return identity


class RecoveryRecord:
    """Opt-in append-only evidence, never a retry token or index backup."""
    def __init__(self, path: Path, git: Git, receipt: dict[str, object]) -> None:
        self.path = Path(os.path.abspath(path))
        if any(parent.is_symlink() for parent in (self.path.parent, *self.path.parent.parents)):
            raise Refusal("record_location", "Recovery-record parent directories must not be symlinks.")
        self.path.parent.resolve(strict=True)
        forbidden = (git.root, git.path("index").parent,
                     Path(git.text(["rev-parse", "--path-format=absolute", "--git-common-dir"])))
        if any(self.path.is_relative_to(root) for root in forbidden):
            raise Refusal("record_location", "Recovery records must be outside the checkout and Git metadata.")
        self.receipt = receipt
        self.receipt["repository_identity"] = _repository_identity(git)
        self.operation_id = secrets.token_hex(16)
        self.fd: int | None = None
        self.identity: tuple[int, int] | None = None
        self.bytes_written = 0

    def __enter__(self) -> RecoveryRecord:
        try:
            self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise Refusal("record_exists", "Use a new recovery-record path; an existing record will not be overwritten.") from error
        info = os.fstat(self.fd)
        self.identity = info.st_dev, info.st_ino
        try:
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def append(self, phase: str) -> None:
        assert self.fd is not None
        info = self.path.lstat()
        if (info.st_dev, info.st_ino) != self.identity or not stat.S_ISREG(info.st_mode):
            raise Refusal("record_changed", "Recovery-record ownership changed; replacement evidence is untouched.")
        event = {"schema": "one_checkout_operation_v1", "operation_id": self.operation_id,
                 "phase": phase, "receipt": self.receipt}
        payload = (json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")
        if ((phase == "prepared" and 4 * len(payload) + 65536 > MAX_RECOVERY_BYTES)
                or self.bytes_written + len(payload) > MAX_RECOVERY_BYTES):
            raise Refusal("record_limit", "Recovery evidence exceeds its bounded format; no new request is delivered by this failure.")
        length = len(payload)
        while payload:
            written = os.write(self.fd, payload)
            if not written:
                raise OSError("Recovery-record write made no progress.")
            payload = payload[written:]
        os.fsync(self.fd)
        self.bytes_written += length

    def __exit__(self, *_: object) -> None:
        if self.fd is not None:
            os.close(self.fd)


def recover_record(path: Path, *, git_timeout: float = 30.0) -> dict[str, object]:
    """Observe a caller-owned record and repository; perform no settlement or retries."""
    if not math.isfinite(git_timeout) or git_timeout <= 0:
        raise Refusal("invalid_timeout", "--git-timeout must be finite and greater than zero.")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise Refusal("invalid_record", "Recovery evidence must be an accessible regular file.") from error
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise Refusal("invalid_record", "Recovery evidence must be a regular file, not a symlink or pipe.")
        data = stream.read(MAX_RECOVERY_BYTES + 1)
    if len(data) > MAX_RECOVERY_BYTES:
        raise Refusal("invalid_record", "Recovery record exceeds the 16 MiB inspection limit.")
    complete = data.splitlines(keepends=True)
    truncated = bool(complete and not complete[-1].endswith(b"\n"))
    if truncated:
        complete.pop()  # An interrupted final append is evidence of uncertainty, not invalid earlier events.
    events: list[dict[str, object]] = []
    previous_phase = -1
    identity = None
    bindings = None
    confirmed = False
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    for line in complete:
        try:
            event = json.loads(line, object_pairs_hook=unique)
            if not isinstance(event, dict) or event.get("schema") != "one_checkout_operation_v1":
                raise ValueError("schema")
            phase = ("prepared", "requested", "confirmed", "outcome").index(event["phase"])
            receipt = event["receipt"]
            if not isinstance(receipt, dict):
                raise ValueError("receipt")
            # Validate the binding fields with the existing saved-plan contract.
            _validate_plan({**receipt, "status": "planned", "dry_run": True})
            if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", receipt["new_commit"]):
                raise ValueError("candidate")
            for key in ("index_before_sha256", "index_prepared_sha256"):
                if not isinstance(receipt.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", receipt[key]):
                    raise ValueError("index binding")
            for key in ("index_before_mtime_ns", "index_prepared_mtime_ns"):
                if type(receipt.get(key)) is not int or receipt[key] < 0:
                    raise ValueError("index timestamp")
            if not isinstance(receipt.get("repository_identity"), dict):
                raise ValueError("repository identity")
            operation = event["operation_id"]
            if not isinstance(operation, str) or not re.fullmatch(r"[0-9a-f]{32}", operation):
                raise ValueError("operation")
            current_bindings = tuple(receipt[key] for key in
                                     ("repository", "branch", "parent", "tree", "patch_sha256", "new_commit",
                                      "changed_paths", "repository_identity", "index_before_sha256", "index_prepared_sha256",
                                      "index_before_mtime_ns", "index_prepared_mtime_ns"))
            if phase != previous_phase + 1:
                raise ValueError("phase order")
            if bindings is not None and (current_bindings != bindings or operation != identity):
                raise ValueError("bindings")
            identity, bindings, previous_phase = operation, current_bindings, phase
            confirmed |= event["phase"] == "confirmed"
            events.append(event)
        except (KeyError, TypeError, ValueError, Refusal) as error:
            raise Refusal("invalid_record", "Recovery record has invalid or conflicting complete events.") from error
    if not events:
        raise Refusal("invalid_record", "No complete prepared event was recovered.")
    receipt = events[-1]["receipt"]
    git = Git(Path(receipt["repository"]), timeout=git_timeout)
    if Path(git.text(["rev-parse", "--show-toplevel"])).resolve() != git.root:
        raise Refusal("record_repository", "The recorded working-tree root no longer resolves to that repository.")
    if _repository_identity(git) != receipt["repository_identity"]:
        raise Refusal("record_repository", "Repository identity changed; the record cannot describe this checkout.")
    _check_object_policy(git)
    candidate, branch = receipt["new_commit"], receipt["branch"]
    observed = git.run(["show-ref", "--verify", "--hash", branch], check=False)
    branch_oid = os.fsdecode(observed.stdout).strip() if observed.returncode == 0 else None
    object_state = git.run(["cat-file", "-t", candidate], check=False)
    available = object_state.returncode == 0 and object_state.stdout.strip() == b"commit"
    if available:
        headers = git.run(["cat-file", "-p", candidate]).stdout.split(b"\n\n", 1)[0].splitlines()
        parents = [line[7:].decode("ascii") for line in headers if line.startswith(b"parent ")]
        trees = [line[5:].decode("ascii") for line in headers if line.startswith(b"tree ")]
        if parents != [receipt["parent"]] or trees != [receipt["tree"]]:
            raise Refusal("record_candidate", "The actual candidate parent/tree differs from the recorded binding.")
    ancestry = git.run(["merge-base", "--is-ancestor", candidate, branch_oid], check=False) if available and branch_oid else None
    reflog = git.run(["reflog", "show", "-n", "1000", "--format=%H", branch], check=False)
    try:
        index_data, _, index_mtime = _index_snapshot(git.path("index"))
        index_observation = {"sha256": hashlib.sha256(index_data).hexdigest(), "mtime_ns": index_mtime}
    except (Refusal, OSError):
        index_observation = None
    return {"schema": SCHEMA, "status": "recovery_observed", "repository": str(git.root),
            "operation_id": identity, "record_phase": events[-1]["phase"], "record_truncated": truncated,
            "branch": branch, "parent": receipt["parent"], "new_commit": candidate,
            "recorded_ref_update_confirmed": confirmed, "ref_update_confirmed": confirmed,
            "candidate_object_available": available, "observed_branch_oid": branch_oid,
            "candidate_is_current_branch": candidate == branch_oid,
            "candidate_is_ancestor": (ancestry.returncode == 0 if ancestry and ancestry.returncode in (0, 1) else None),
            "reflog_mentions_candidate": candidate.encode("ascii") in reflog.stdout.splitlines(),
            "reflog_limit": 1000, "index_observation": index_observation,
            "index_matches_original": (index_observation["sha256"] == receipt["index_before_sha256"] if index_observation else None),
            "index_matches_prepared": (index_observation["sha256"] == receipt["index_prepared_sha256"] if index_observation else None),
            "index_mtime_matches_original": (index_observation["mtime_ns"] == receipt["index_before_mtime_ns"] if index_observation else None),
            "index_mtime_matches_prepared": (index_observation["mtime_ns"] == receipt["index_prepared_mtime_ns"] if index_observation else None),
            "recovery": "These separate reads are observations, not an atomic snapshot or proof of nonlanding. "
                        "Inspect the branch, candidate and index; never retry, rewind or remove locks automatically."}


def commit_patch(*, repo: Path | str, patch: bytes, paths: Sequence[str],
                 expected_parent: str, message: str = "", dry_run: bool = False,
                 git_timeout: float = 30.0,
                 reviewed_plan: dict[str, object] | None = None,
                 record_file: Path | str | None = None) -> dict[str, object]:
    """Plan or land one caller-selected patch. No working-tree file is modified."""
    if not math.isfinite(git_timeout) or git_timeout <= 0:
        raise Refusal("invalid_timeout", "--git-timeout must be finite and greater than zero.")
    declared = _paths(paths)
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", expected_parent):
        raise Refusal("invalid_parent", "--expected-parent must be a full commit object ID.")
    expected_parent = expected_parent.lower()
    if not patch:
        raise Refusal("empty_patch", "Supply a nonempty patch against the captured parent.")
    if not dry_run and not message.strip():
        raise Refusal("message_required", "A commit message is required.")
    if dry_run and record_file is not None:
        raise Refusal("record_plan", "Recovery recording is only available for a real commit.")
    if reviewed_plan is not None:
        reviewed_plan = _validate_plan(reviewed_plan)
        _match_plan(reviewed_plan, {"repository": str(Path(repo).resolve()),
                                   "parent": expected_parent, "changed_paths": declared,
                                   "patch_sha256": hashlib.sha256(patch).hexdigest()})
    git = Git(Path(repo).resolve(), timeout=git_timeout)
    root = git.text(["rev-parse", "--show-toplevel"])
    if Path(root).resolve() != git.root:
        raise Refusal("repository_root_required", "--repo must name the working-tree root.")
    _check_object_policy(git)
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
        if reviewed_plan is not None:
            _match_plan(reviewed_plan, {"branch": branch})
        _check_index_kind(git)
        original, index_mode, index_mtime_ns = _index_snapshot(index)
        original_snapshot = (original, index_mode, index_mtime_ns)
        scratch = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="one-checkout-")))
        private_index = scratch / "patch-index"
        original_index = scratch / "original-index"
        original_index.write_bytes(original)
        os.utime(original_index, ns=(index_mtime_ns, index_mtime_ns))
        shared_env = {"GIT_INDEX_FILE": str(original_index)}
        original_entries = _entries(git, shared_env)
        original_flags = _semantic_index_flags(git, shared_env)
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
        if reviewed_plan is not None:
            _match_plan(reviewed_plan, {"tree": tree})
        synchronize: list[str] = []
        preserve: list[str] = []
        for path in changed:
            clean = original_entries.get(path) == base_entries.get(path) and path not in original_flags
            (synchronize if clean else preserve).append(path)
        def entry_json(entry: tuple[str, str] | None) -> dict[str, str] | None:
            return {"mode": entry[0], "oid": entry[1]} if entry else None
        staging_review = [
            {"path": p, "parent_entry": entry_json(base_entries.get(p)),
             "preserved_index_entry": entry_json(original_entries.get(p)),
             "candidate_entry": entry_json(candidate_entries.get(p)),
             "preserved_flags": original_flags.get(p, 0)}
            for p in preserve if original_entries.get(p) != candidate_entries.get(p) or p in original_flags
        ]
        receipt: dict[str, object] = {
            "schema": SCHEMA, "status": "planned", "dry_run": dry_run,
            "repository": str(git.root), "branch": branch, "parent": parent, "tree": tree,
            "reviewed_plan_verified": reviewed_plan is not None,
            "patch_sha256": hashlib.sha256(patch).hexdigest(), "changed_paths": changed,
            "preserved_staged_paths": preserve, "synchronized_index_paths": synchronize,
            "new_commit": None, "worktree_written": False, "hooks_run": False,
            "staging_review": staging_review,
            "warnings": ([{"code": "preserved_staging_differs",
                           "message": "Retained staged snapshots differ from the candidate. A later plain Git commit "
                                      "can undo landed changes. Review intent against the parent; do not reset or restage automatically."}]
                         if staging_review else []),
        }
        if dry_run:
            return receipt
        # Build the future shared index before advancing the ref. Its foreign
        # entries come from the exact locked snapshot, never from the patch index.
        next_index = scratch / "next-index"
        next_index.write_bytes(original)
        os.utime(next_index, ns=(index_mtime_ns, index_mtime_ns))
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
        if (_entries(git, next_env) != expected_entries
                or _semantic_index_flags(git, next_env) != original_flags):
            raise Refusal("index_validation_failed", "The prepared index did not preserve the declared entries.")
        next_bytes = next_index.read_bytes()
        next_mtime_ns = next_index.stat().st_mtime_ns
        assert index_lock is not None
        index_lock.require_owned()
        if _index_snapshot(index) != original_snapshot:
            raise Refusal("index_race", "The shared index changed despite its lock; no commit was landed.")
        if _head(git) != (branch, parent):
            raise Refusal("head_race", "HEAD changed while preparing the patch; no commit was landed.")
        commit_args = ["commit-tree", tree, "-p", parent]
        signing = git.run(["config", "--bool", "commit.gpgSign"], check=False)
        if (signing.returncode not in (0, 1)
                or signing.returncode == 0 and signing.stdout.strip() not in (b"true", b"false")):
            raise Refusal("signing_policy", "Cannot establish commit.gpgSign; repair the configuration before committing.")
        if signing.stdout.strip() == b"true":
            commit_args.append("-S")
        new_commit = git.text(commit_args, data=message.encode("utf-8"))
        index_lock.require_owned()
        if _index_snapshot(index) != original_snapshot or _head(git) != (branch, parent):
            raise Refusal("pre_land_race", "The checkout changed before landing; no commit was landed.")
        record = None
        if record_file is not None:
            receipt.update(new_commit=new_commit, ref_update_confirmed=False,
                           index_before_sha256=hashlib.sha256(original).hexdigest(),
                           index_before_mtime_ns=index_mtime_ns,
                           index_prepared_mtime_ns=(next_mtime_ns if next_bytes != original else index_mtime_ns),
                           index_prepared_sha256=hashlib.sha256(next_bytes).hexdigest())
            record = stack.enter_context(RecoveryRecord(Path(record_file), git, receipt))
            receipt["record_file"] = str(record.path)
            record.append("prepared")
        try:
            if record is None:
                _publish_ref(git, branch, new_commit, parent)
            else:
                _publish_ref(git, branch, new_commit, parent, record=record)
        except RefOutcome as error:
            receipt.update(new_commit=new_commit,
                           status="landed_unsettled" if error.confirmed else "landing_uncertain",
                           ref_update_confirmed=error.confirmed, ref_error=str(error),
                           recovery="Inspect new_commit and the branch/reflog before any retry; the commit request may have landed.")
            stack.outcome_receipt = receipt
            return receipt
        receipt.update(new_commit=new_commit, status="committed", ref_update_confirmed=True)
        stack.outcome_receipt = receipt
        try:
            index_lock.require_owned()
            if _index_snapshot(index) != original_snapshot:
                raise Refusal("index_race", "The shared index changed after landing.")
            if _head(git) != (branch, new_commit):
                raise Refusal("post_land_ref_race", "The branch changed after landing; its index was not synchronized.")
            if next_bytes != original:
                index_lock.publish_index(next_bytes, index, index_mode, next_mtime_ns)
        except (OSError, Refusal, KeyboardInterrupt) as exc:
            # Never hide a landed commit behind an ordinary failed-commit error,
            # or roll back a ref after another actor may already depend on it.
            receipt.update(status="landed_unsettled", index_error=str(exc),
                           recovery="Inspect new_commit and the shared index before any retry; the commit already exists.")
        if record is not None:
            try:
                record.append("outcome")
            except (OSError, Refusal, KeyboardInterrupt) as error:
                receipt.update(record_error=str(error),
                               recovery="The commit is confirmed. Recording the final outcome failed; inspect branch and index before retrying.")
        return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = JsonArgumentParser(prog="one-checkout", description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "commit"):
        child = sub.add_parser(name, allow_abbrev=False)
        child.add_argument("--repo", type=Path, default=Path.cwd())
        child.add_argument("--patch", type=Path, required=True)
        child.add_argument("--path", action="append", required=True)
        child.add_argument("--expected-parent", required=True)
        child.add_argument("--dry-run", action="store_true")
        child.add_argument("--git-timeout", type=float, default=30.0,
                           help="Seconds per Git invocation or protocol acknowledgement (default: 30).")
        if name == "commit":
            child.add_argument("--plan-file", type=Path,
                               help="bind this commit to a saved, reviewed plan JSON receipt")
            child.add_argument("--record-file", type=Path,
                               help="create append-only recovery evidence at a new path outside the checkout")
        messages = child.add_mutually_exclusive_group()
        messages.add_argument("--message", default="")
        messages.add_argument("--message-file", type=Path)
    recovery = sub.add_parser("recover", allow_abbrev=False, help="read operation evidence without changing Git state")
    recovery.add_argument("--record-file", type=Path, required=True)
    recovery.add_argument("--git-timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "recover":
            return _report_receipt(recover_record(args.record_file, git_timeout=args.git_timeout))
        plan_path = getattr(args, "plan_file", None)
        reviewed_plan = _read_plan(plan_path) if plan_path is not None else None
        message = args.message_file.read_text(encoding="utf-8") if args.message_file else args.message
        receipt = commit_patch(repo=args.repo, patch=args.patch.read_bytes(), paths=args.path,
                               expected_parent=args.expected_parent, message=message,
                               dry_run=args.command == "plan" or args.dry_run, reviewed_plan=reviewed_plan,
                               git_timeout=args.git_timeout, record_file=getattr(args, "record_file", None))
    except (Refusal, OSError, UnicodeError) as exc:
        _diagnostic({"schema": SCHEMA, "status": "refused", "code": getattr(exc, "code", "io_error"),
                     "message": str(exc)})
        return 2
    return _report_receipt(receipt)


if __name__ == "__main__":
    raise SystemExit(main())
