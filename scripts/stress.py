#!/usr/bin/env python3
"""Bounded synthetic-repository experiments; see docs/stress_testing.md.

No live repository argument exists. All writes and signals target fixtures/children
created by this invocation. Results are JSON, not a claim of universal correctness.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import selectors
import shutil
import signal
import stat
import statistics
import subprocess
import sys
import tempfile
import time

RETURN_BASELINE = "c86a60d633afc477aa27bb89f68f5273dc927495"
ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/one-checkout/scripts/one_checkout.py"
WORKER = Path(__file__).with_name("stress_worker.py")
# Git mode -> raw bytes. For symlinks the bytes are the link target, not its contents.
Model = dict[str, tuple[str, bytes]]
FIXTURE_GIT_TIMEOUT_SECONDS = 60


class UnsupportedFixture(RuntimeError):
    """The filesystem or Git build cannot represent the requested fixture."""


class FixtureGitTimeout(RuntimeError):
    """Fixture construction/observation timed out, independently of the runtime."""


def summary_classification(results: list[dict]) -> str:
    """Skipped capabilities never constitute successful runtime exercises."""
    statuses = [row["classification"] for row in results]
    if any(s not in ("pass", "unsupported", "skip") for s in statuses):
        return "attention_required"
    if "pass" not in statuses:
        return "no_cases_exercised"
    return "pass_with_skips" if any(s != "pass" for s in statuses) else "pass"


def benchmark_summary(results: list[dict]) -> dict:
    rows = [row for row in results if row.get("case") == "benchmark" and row["classification"] == "pass"]
    measures = {}
    for name, values in {
        "invocation_wall_seconds": [row["invocation_wall_seconds"] for row in rows],
        "worker_elapsed_seconds": [row["worker"]["metrics"]["elapsed_seconds"] for row in rows],
        "index_lock_seconds": [row["worker"]["metrics"]["index_lock_seconds"] for row in rows],
    }.items():
        values = [value for value in values if value is not None]
        if values:
            measures[name] = {"samples": len(values), "minimum": min(values),
                              "median": statistics.median(values), "maximum": max(values)}
    return {"successful_samples": len(rows), "measures": measures,
            "interpretation": "Descriptive samples from synthetic repositories; no significance or cross-machine speed claim. Fixture setup is excluded; worker instrumentation and process startup have separate windows."}


def freeze_runtime(source: Path, root: Path) -> tuple[Path, bytes]:
    content = source.read_bytes()
    if source.read_bytes() != content:
        raise ValueError("runtime source changed during snapshot; retry after the source edit completes")
    snapshot = root / "runtime-under-test.py"
    snapshot.write_bytes(content)
    return snapshot, content


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


HARNESS_SHA256 = digest(Path(__file__).read_bytes())


def isolated_env(home: Path, scratch: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / "xdg"), TMPDIR=str(scratch),
               ONE_CHECKOUT_TEST_TMPDIR=str(scratch), LC_ALL="C", PYTHONDONTWRITEBYTECODE="1",
               GIT_AUTHOR_DATE="2000-01-01T00:00:00+00:00",
               GIT_COMMITTER_DATE="2000-01-01T00:00:00+00:00")
    return env


def git(repo: Path, env: dict, *args: str, data: bytes | None = None,
        check: bool = True, timeout: float = FIXTURE_GIT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    # Only fixture/observer calls get these environment controls. The target
    # correctly rejects inherited GIT_* control variables, so its env is separate.
    try:
        result = subprocess.run(["git", "--no-replace-objects", "--literal-pathspecs",
                                 "--no-optional-locks", "-c", "core.hooksPath=" + os.devnull,
                                 "-c", "core.fsmonitor=false", *args], cwd=repo,
                                env={**env, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
                                input=data, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise FixtureGitTimeout(f"fixture Git {args[0]} exceeded {timeout:g}s; runtime outcome is not inferred") from error
    if check and result.returncode:
        raise RuntimeError(f"git {args[0]} exited {result.returncode}: {result.stderr!r}")
    return result


def blob_id(data: bytes, algorithm: str = "sha1") -> str:
    return hashlib.new(algorithm, b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def entry_model(model: Model, algorithm: str = "sha1") -> dict:
    return {os.fsencode(p): (mode, blob_id(data, algorithm)) for p, (mode, data) in model.items()}


def replace_files(repo: Path, before: Model, after: Model) -> None:
    for name in sorted(set(before) | set(after)):
        if before.get(name) == after.get(name):
            continue
        path = repo / name
        if path.exists() or path.is_symlink():
            path.unlink()
        if name not in after:
            continue
        mode, data = after[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            os.symlink(os.fsdecode(data), path)
        else:
            path.write_bytes(data)
            path.chmod(0o755 if mode == "100755" else 0o644)


def working_model(repo: Path) -> Model:
    result = {}
    for root, dirs, files in os.walk(os.fsencode(repo), followlinks=False):
        if root == os.fsencode(repo):
            dirs[:] = [d for d in dirs if d != b".git"]
            files = [f for f in files if f != b".git"]
        links = [d for d in dirs if os.path.islink(os.path.join(root, d))]
        dirs[:] = [d for d in dirs if d not in links]
        for name in files + links:
            path = os.path.join(root, name)
            info = os.lstat(path)
            mode = "120000" if stat.S_ISLNK(info.st_mode) else "100755" if info.st_mode & 0o111 else "100644"
            data = os.readlink(path) if mode == "120000" else Path(os.fsdecode(path)).read_bytes()
            result[os.fsdecode(os.path.relpath(path, os.fsencode(repo)))] = (mode, data)
    return result


def read_entries(raw: bytes, tree: bool = False) -> dict:
    result = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        a, b, c = meta.split()
        if tree:
            if b not in (b"blob", b"commit"):
                raise AssertionError("unexpected tree entry")
            result[path] = (a.decode(), c.decode())
        else:
            if c != b"0":
                raise AssertionError("unmerged oracle index")
            result[path] = (a.decode(), b.decode())
    return result


def summarise(entries: dict, full: bool = False) -> dict:
    rows = [{"path_hex": path.hex(), "mode": mode, "oid": oid}
            for path, (mode, oid) in sorted(entries.items())]
    result = {"entries": len(rows), "sha256": digest(json.dumps(rows, sort_keys=True).encode())}
    if full:
        result["rows"] = rows
    return result


def locks(repo: Path) -> list[dict]:
    result = []
    for path in sorted((repo / ".git").rglob("*.lock")):
        try:
            st = path.lstat()
            result.append({"path": str(path.relative_to(repo)), "device": st.st_dev,
                           "inode": st.st_ino, "bytes": st.st_size,
                           "sha256": digest(path.read_bytes()) if stat.S_ISREG(st.st_mode) else None})
        except FileNotFoundError:
            continue
    return result


class Fixture:
    def __init__(self, root: Path, base: Model, candidate: Model, staged: Model,
                 working: Model, *, seed: int = 0, algorithm: str = "sha1"):
        root.mkdir()
        self.root = root
        self.repo = root / "repo"
        self.repo.mkdir()
        self.home = root / "home"
        self.home.mkdir()
        self.env = isolated_env(self.home, root)
        self.algorithm = algorithm
        self.base, self.candidate, self.staged, self.working = base, candidate, staged, working
        self.seed = seed
        initial = self.g("init", "-q", "-b", "main", "--object-format=" + algorithm, check=False)
        if initial.returncode:
            message = os.fsdecode(initial.stderr)
            if algorithm == "sha256" and any(term in message for term in ("unknown hash", "unsupported", "unknown option")):
                raise UnsupportedFixture("Git cannot initialize this SHA-256 fixture: " + os.fsdecode(initial.stderr))
            raise RuntimeError("Git fixture initialization failed: " + os.fsdecode(initial.stderr))
        for key, value in {"user.name": "Synthetic fixture", "user.email": "fixture@example.invalid",
                           "commit.gpgSign": "false", "core.autocrlf": "false", "core.filemode": "true",
                           "core.ignoreCase": "false", "core.precomposeUnicode": "false",
                           "gc.auto": "0", "maintenance.auto": "false"}.items():
            self.g("config", key, value)
        try:
            replace_files(self.repo, {}, base)
        except OSError as error:
            if error.errno == errno.EILSEQ:
                raise UnsupportedFixture("filesystem rejects requested raw filename bytes (EILSEQ)") from error
            raise
        if working_model(self.repo) != base:
            raise UnsupportedFixture("filesystem cannot represent distinct case/normalisation/byte names or modes")
        self.g("add", "-A")
        self.g("commit", "-qm", "Synthetic baseline")
        self.parent = self.g("rev-parse", "HEAD").stdout.decode().strip()
        replace_files(self.repo, base, candidate)
        # Patch construction must not warm the target's candidate objects. Store
        # these fixture-only blobs in a separate object directory, with base
        # objects available read-only as alternates. The runtime cannot see it.
        patch_objects = root / "patch-objects"
        patch_objects.mkdir()
        patch_env = {**self.env, "GIT_OBJECT_DIRECTORY": str(patch_objects),
                     "GIT_ALTERNATE_OBJECT_DIRECTORIES": json.dumps(str(self.repo / ".git/objects"))}
        git(self.repo, patch_env, "add", "-A")
        self.patch = root / "selected.patch"
        self.patch.write_bytes(git(self.repo, patch_env, "diff", "--cached", "--binary", "--no-renames",
                                   "--no-ext-diff", "--no-textconv", "--src-prefix=a/",
                                   "--dst-prefix=b/", self.parent, "--").stdout)
        self.changed = sorted(p for p in set(base) | set(candidate) if base.get(p) != candidate.get(p))
        replace_files(self.repo, candidate, staged)
        self.g("read-tree", self.parent)
        self.g("add", "-A")
        replace_files(self.repo, staged, working)
        if working_model(self.repo) != working:
            raise UnsupportedFixture("filesystem cannot represent fixture working model")
        self.before = self.observe()
        if self.before["tree"] != entry_model(base, algorithm) or self.before["index"] != entry_model(staged, algorithm):
            raise AssertionError("fixture preparation disagrees with independent model")

    def g(self, *args, **kwargs):
        return git(self.repo, self.env, *args, **kwargs)

    def observe(self) -> dict:
        raw_tree = self.g("ls-tree", "-r", "--full-tree", "-z", "HEAD").stdout
        raw_index = self.g("ls-files", "--stage", "-z").stdout
        debug = self.g("ls-files", "--debug", "-z").stdout
        flags, offset = {}, 0
        while offset < len(debug):
            end = debug.index(b"\0", offset)
            path = debug[offset:end]
            flag_start = debug.index(b"flags: ", end) + len(b"flags: ")
            offset = debug.index(b"\n", flag_start) + 1
            semantic = int(debug[flag_start:offset - 1], 16) & 0x60008000
            if semantic:
                flags[path] = semantic
        return {"head": self.g("rev-parse", "HEAD").stdout.decode().strip(),
                "branch": self.g("symbolic-ref", "--quiet", "HEAD").stdout.decode().strip(),
                "tree": read_entries(raw_tree, True), "index": read_entries(raw_index), "semantic_flags": flags,
                "working": working_model(self.repo), "index_bytes": (self.repo / ".git/index").read_bytes(),
                "locks": locks(self.repo), "raw_tree_sha256": digest(raw_tree), "raw_index_sha256": digest(raw_index)}

    def check(self, receipt: dict, *, settled: bool = True, landed: bool = True,
              expected_head: str | None = None, full: bool = False) -> dict:
        actual = self.observe()
        expected_index = dict(self.staged)
        if settled:
            for path in self.changed:
                if self.staged.get(path) == self.base.get(path):
                    if path in self.candidate:
                        expected_index[path] = self.candidate[path]
                    else:
                        expected_index.pop(path, None)
        expected_tree = self.candidate if landed else self.base
        checks = {"tree_exact": actual["tree"] == entry_model(expected_tree, self.algorithm),
                  "index_entries_exact": actual["index"] == entry_model(expected_index, self.algorithm),
                  "working_bytes_and_modes_exact": actual["working"] == self.working}
        if expected_head is not None:
            checks["head_exact"] = actual["head"] == expected_head
        elif landed:
            checks["receipt_names_head"] = receipt.get("new_commit") == actual["head"]
            parents = self.g("rev-list", "--parents", "-n", "1", actual["head"]).stdout.decode().split()[1:]
            checks["exactly_one_expected_parent"] = parents == [self.parent]
        else:
            checks["head_unchanged"] = actual["head"] == self.parent
        if not settled:
            checks["index_bytes_unchanged"] = actual["index_bytes"] == self.before["index_bytes"]
        return {"checks": checks, "expected_tree": summarise(entry_model(expected_tree, self.algorithm), full),
                "actual_tree": summarise(actual["tree"], full),
                "expected_index": summarise(entry_model(expected_index, self.algorithm), full),
                "actual_index": summarise(actual["index"], full), "head": actual["head"],
                "index_before_sha256": digest(self.before["index_bytes"]),
                "index_bytes_sha256": digest(actual["index_bytes"]), "locks": actual["locks"],
                "raw_tree_sha256": actual["raw_tree_sha256"], "raw_index_sha256": actual["raw_index_sha256"]}

    def other_commit(self, parent: str | None = None) -> str:
        tree = self.g("rev-parse", "HEAD^{tree}").stdout.decode().strip()
        return self.g("commit-tree", tree, "-p", parent or self.parent,
                      data=b"Synthetic external writer").stdout.decode().strip()


def generated_models(seed: int, count: int = 12, *, name_profile: str = "portable") -> tuple[Model, Model, Model, Model]:
    rng = random.Random(seed)
    names = ["shared.txt", "-leading", ":(glob)*", "bracket[1]", "tab\tname", "line\nname",
             "unicode-slot", "normalization-slot", "Case-upper", "case-lower", "raw-slot", "link"]
    if name_profile == "posix-stress":
        names[6:11] = ["café", "cafe\u0301", "Case", "case", os.fsdecode(b"raw-\xff")]
    elif name_profile != "portable":
        raise ValueError("unknown name profile")
    base = {name: ("100644", f"base {i}\n".encode()) for i, name in enumerate(names[:count])}
    base["shared.txt"] = ("100644", b"A=0\nB=0\nC=0\n")
    base["link"] = ("120000", b"shared.txt")
    candidate = dict(base)
    chosen = rng.sample(sorted(base), min(1 + seed % 4, len(base)))
    for path in chosen:
        mode = rng.choice(["100644", "100755", "120000"])
        data = (b"target-" + str(seed).encode()) if mode == "120000" else rng.randbytes(16) + b"\0\n"
        if rng.randrange(4) == 0:
            candidate.pop(path)
        else:
            candidate[path] = (mode, data)
    if seed % 2 == 0:
        candidate["selected-new"] = ("100644", b"selected new\n")
    staged = dict(base)
    for path in sorted(base):
        choice = rng.randrange(4)
        if choice == 0:
            staged.pop(path)
        elif choice == 1:
            staged[path] = ("100755", b"foreign staged " + os.fsencode(path))
    staged["foreign-new"] = ("100644", b"foreign new\n")
    working = dict(staged)
    working["untracked"] = ("100644", b"not owned by selected patch\n")
    for path in chosen:
        working[path] = ("100644", b"working bytes must survive\0\xff")
    return base, candidate, staged, working


def simple_models() -> tuple[Model, Model, Model, Model]:
    base = {"shared.txt": ("100644", b"A=0\nB=0\nC=0\n"), "foreign": ("100644", b"base\n")}
    candidate = {**base, "shared.txt": ("100644", b"A=1\nB=0\nC=0\n")}
    staged = {**base, "shared.txt": ("100644", b"A=0\nB=2\nC=0\n"), "foreign": ("100644", b"staged\n")}
    working = {**staged, "shared.txt": ("100644", b"A=1\nB=2\nC=3\n"), "untracked": ("100644", b"other\n")}
    return base, candidate, staged, working


def settlement_models() -> tuple[Model, Model, Model, Model]:
    """Require a real index update as well as preserving mixed-file staging."""
    base, candidate, staged, working = simple_models()
    base["owned.txt"] = staged["owned.txt"] = ("100644", b"base owned\n")
    candidate["owned.txt"] = working["owned.txt"] = ("100644", b"selected owned\n")
    return base, candidate, staged, working


class Client:
    def __init__(self, fixture: Fixture, tool: Path, gates=(), timeout: float = 15, ident: str = "caller"):
        config = fixture.root / (ident + ".json")
        config.write_text(json.dumps({"tool": str(tool), "repo": str(fixture.repo), "patch": str(fixture.patch),
                                      "parent": fixture.parent, "paths": fixture.changed, "gates": list(gates)}))
        self.command = [sys.executable, "-B", "-I", str(WORKER), str(config)]
        self.stderr_path = fixture.root / (ident + ".stderr")
        self.stderr_file = self.stderr_path.open("wb")
        self.process = subprocess.Popen(self.command, env=fixture.env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.stderr_file, start_new_session=True,
                                        bufsize=0)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = b""
        self.deadline = time.monotonic() + timeout
        self.records: list[dict] = []
        self.closed = False

    def read(self) -> dict:
        while b"\n" not in self.buffer:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise TimeoutError("worker deadline")
            data = os.read(self.process.stdout.fileno(), 65536)
            if not data:
                raise RuntimeError("worker EOF: " + self.stderr_path.read_text(errors="replace"))
            self.buffer += data
        line, self.buffer = self.buffer.split(b"\n", 1)
        event = json.loads(line)
        self.records.append(event)
        return event

    def release(self) -> None:
        self.process.stdin.write(b"continue\n")
        self.process.stdin.flush()

    def finish(self) -> dict:
        result = self.read()
        if result["event"] != "result":
            raise AssertionError("unexpected test barrier: " + repr(result))
        result["exit_code"] = self.process.wait(timeout=max(.01, self.deadline - time.monotonic()))
        result["command"] = self.command
        return result

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        # Only our newly-created session group is signalled; no PID discovery by name.
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()
        self.selector.close()
        self.stderr_file.close()


def run_client(fixture: Fixture, tool: Path, timeout: float = 15, ident: str = "caller") -> dict:
    client = Client(fixture, tool, timeout=timeout, ident=ident)
    try:
        return client.finish()
    finally:
        client.close()


def outcome(oracle: dict, **extra) -> dict:
    return {"classification": "pass" if all(oracle["checks"].values()) else "invariant_failure",
            "oracle": oracle, **extra}


def successful_case_outcome(model: Fixture, result: dict, oracle: dict, **extra) -> dict:
    """Keep conservative operational outcomes distinct from violated invariants.

    This is for cases expecting a successful commit, without a competing ref
    writer. Attention is never success. Native Git observations must establish
    the candidate, original branch, permitted HEAD/index and unchanged working
    bytes; a receipt by itself cannot excuse unexpected state.
    """
    fixture = model
    receipt = result["receipt"]
    status = receipt.get("status")
    if result.get("exit_code") != 3 or status not in ("landing_uncertain", "landed_unsettled"):
        oracle["checks"]["successful_worker_exit"] = result.get("exit_code") == 0
        return outcome(oracle, worker=result, **extra)
    actual = fixture.observe()
    candidate = receipt.get("new_commit")
    valid_oid = (isinstance(candidate, str) and len(candidate) in (40, 64)
                 and all(c in "0123456789abcdef" for c in candidate))
    candidate_tree, candidate_parents = {}, []
    if valid_oid:
        tree = fixture.g("ls-tree", "-r", "--full-tree", "-z", candidate, check=False)
        parents = fixture.g("rev-list", "--parents", "-n", "1", candidate, check=False)
        if tree.returncode == 0:
            candidate_tree = read_entries(tree.stdout, True)
        if parents.returncode == 0:
            candidate_parents = parents.stdout.decode().split()[1:]
    confirmed = status == "landed_unsettled"
    head_permitted = actual["head"] == candidate or (not confirmed and actual["head"] == fixture.parent)
    landed = actual["head"] == candidate
    original_index = (actual["index_bytes"] == fixture.before["index_bytes"]
                      and actual["index"] == fixture.before["index"])
    # An acknowledged commit can report a later cleanup problem after I1 was
    # published. The settled index model still has to pass the ordinary oracle.
    settled_index = (confirmed and oracle["checks"].get("index_entries_exact") is True
                     and actual["semantic_flags"] == fixture.before["semantic_flags"])
    checks = {"truthful_outcome_receipt": receipt.get("ref_update_confirmed") is confirmed,
              "receipt_branch_parent_exact": receipt.get("branch") == fixture.before["branch"] and receipt.get("parent") == fixture.parent,
              "receipt_patch_scope_exact": receipt.get("patch_sha256") == digest(fixture.patch.read_bytes()) and receipt.get("changed_paths") == fixture.changed,
              "candidate_tree_exact": candidate_tree == entry_model(fixture.candidate, fixture.algorithm),
              "candidate_parent_exact": candidate_parents == [fixture.parent],
              "head_permitted": head_permitted,
              "branch_identity_exact": actual["branch"] == fixture.before["branch"],
              "tree_exact": actual["tree"] == entry_model(fixture.candidate if landed else fixture.base, fixture.algorithm),
              "index_permitted": original_index or settled_index,
              "semantic_index_flags_preserved": actual["semantic_flags"] == fixture.before["semantic_flags"],
              "working_bytes_and_modes_exact": actual["working"] == fixture.working,
              "no_remaining_locks": not actual["locks"]}
    for key in ("foreign_extended_flag_preserved", "requested_version_really_exercised", "real_signature_verified", "receipt_path_bytes_roundtrip"):
        if key in oracle["checks"]:
            checks[key] = oracle["checks"][key]
    return {"classification": "operational_uncertainty" if all(checks.values()) else "invariant_failure",
            "oracle": {"checks": checks, "index_original_bytes_exact": original_index,
                       "index_before_sha256": digest(fixture.before["index_bytes"]),
                       "index_after_sha256": digest(actual["index_bytes"]),
                       "actual_index": summarise(actual["index"], True), "head": actual["head"],
                       "branch": actual["branch"]},
            "successful_settlement_checks": oracle["checks"], "worker": result, **extra}


def generated_case(root: Path, tool: Path, seed: int, timeout: float = 15, algorithm="sha1", name_profile="portable") -> dict:
    fixture = Fixture(root, *generated_models(seed, name_profile=name_profile), seed=seed, algorithm=algorithm)
    result = run_client(fixture, tool, timeout)
    oracle = fixture.check(result["receipt"], full=True)
    oracle["checks"]["committed_receipt"] = result["receipt"]["status"] == "committed"
    oracle["checks"]["no_remaining_locks"] = not locks(fixture.repo)
    return successful_case_outcome(fixture, result, oracle, case="generated", seed=seed, algorithm=algorithm, name_profile=name_profile,
                   patch_sha256=digest(fixture.patch.read_bytes()), fixture=str(root))


def schedule_case(root: Path, tool: Path, name: str, timeout: float = 15) -> dict:
    fixture = Fixture(root, *(settlement_models() if name == "settlement_contention" else simple_models()))
    barrier = {"snapshot_contention": "snapshot", "branch_wins": "snapshot",
               "prepared_contention": "prepare", "post_ack_advance": "ack",
               "settlement_contention": "settlement"}[name]
    client = Client(fixture, tool, [barrier], timeout)
    extras = {}
    try:
        event = client.read()
        assert event["event"] == barrier
        held_locks = locks(fixture.repo)
        if name in ("snapshot_contention", "settlement_contention"):
            # These are actual CLI subprocesses, not imported competing callers.
            argv = [sys.executable, "-B", "-I", str(tool), "commit", "--repo", str(fixture.repo),
                    "--patch", str(fixture.patch), "--expected-parent", fixture.parent,
                    "--path", "shared.txt", "--message", "Competing candidate"]
            losers = [subprocess.Popen(argv, env=fixture.env, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, start_new_session=True) for _ in range(3)]
            receipts = []
            try:
                for loser in losers:
                    out, err = loser.communicate(timeout=timeout)
                    receipts.append({"exit_code": loser.returncode, "stdout": out.decode(), "stderr": err.decode()})
            finally:
                for loser in losers:
                    if loser.poll() is None:
                        os.killpg(loser.pid, signal.SIGKILL)
                        loser.wait(timeout=5)
            add = fixture.g("add", "--", "untracked", check=False)
            extras.update(losers=receipts, git_add_exit=add.returncode,
                          lock_identity_preserved=locks(fixture.repo) == held_locks)
        elif name == "branch_wins":
            other = fixture.other_commit()
            fixture.g("update-ref", "refs/heads/main", other, fixture.parent)
            extras["external_commit"] = other
        elif name == "prepared_contention":
            other = fixture.other_commit()
            update = fixture.g("update-ref", "refs/heads/main", other, fixture.parent, check=False)
            switch = fixture.g("symbolic-ref", "HEAD", "refs/heads/other", check=False)
            extras.update(update_ref_exit=update.returncode, symbolic_ref_exit=switch.returncode,
                          lock_identity_preserved=locks(fixture.repo) == held_locks)
        elif name == "post_ack_advance":
            other = fixture.other_commit(event["candidate"])
            fixture.g("update-ref", "refs/heads/main", other, event["candidate"])
            extras["external_commit"] = other
        client.release()
        result = client.finish()
        if name == "branch_wins":
            oracle = fixture.check(result["receipt"], settled=False, landed=False,
                                   expected_head=extras["external_commit"])
            oracle["checks"]["refused_before_landing"] = result["receipt"]["status"] == "refused"
        elif name == "post_ack_advance":
            oracle = fixture.check(result["receipt"], settled=False,
                                   expected_head=extras["external_commit"])
            oracle["checks"]["landed_unsettled_receipt"] = result["receipt"]["status"] == "landed_unsettled"
        else:
            oracle = fixture.check(result["receipt"])
            oracle["checks"]["committed_receipt"] = result["receipt"]["status"] == "committed"
            oracle["checks"]["lock_identity_preserved"] = extras["lock_identity_preserved"]
            if "losers" in extras:
                oracle["checks"]["all_losers_refused"] = all(r["exit_code"] == 2 for r in extras["losers"])
                oracle["checks"]["git_add_refused"] = extras["git_add_exit"] != 0
            else:
                oracle["checks"]["branch_writers_refused"] = extras["update_ref_exit"] != 0 and extras["symbolic_ref_exit"] != 0
        oracle["checks"]["no_remaining_locks"] = not locks(fixture.repo)
        return outcome(oracle, case=name, worker=result, barrier=event, observations=extras, fixture=str(root))
    finally:
        client.close()


def simultaneous_case(root: Path, tool: Path, callers: int = 4, timeout: float = 15) -> dict:
    fixture = Fixture(root, *simple_models())
    clients = [Client(fixture, tool, ["start"], timeout, f"caller-{i}") for i in range(callers)]
    try:
        for client in clients:
            assert client.read()["event"] == "start"
        for client in clients:
            client.release()
        results = [client.finish() for client in clients]
        winners = [r for r in results if r["receipt"]["status"] == "committed"]
        oracle = fixture.check(winners[0]["receipt"] if winners else {})
        oracle["checks"]["exactly_one_winner"] = len(winners) == 1
        oracle["checks"]["losers_safely_refused"] = all(r["receipt"].get("code") in ("git_locked", "stale_parent")
                                                         for r in results if r not in winners)
        oracle["checks"]["one_new_reachable_commit"] = fixture.g("rev-list", "--count", fixture.parent + "..HEAD").stdout.strip() == b"1"
        oracle["checks"]["no_remaining_locks"] = not locks(fixture.repo)
        return outcome(oracle, case="simultaneous", callers=callers, workers=results, fixture=str(root))
    finally:
        for client in clients:
            client.close()


def process_state(pid: int | None) -> str:
    if not pid:
        return "not_started"
    proc = Path(f"/proc/{pid}/stat")
    if sys.platform.startswith("linux"):
        try:
            return "zombie" if proc.read_text().rsplit(")", 1)[1].split()[0] == "Z" else "running"
        except FileNotFoundError:
            return "exited"
    observed = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, timeout=5)
    if observed.returncode == 1 and not observed.stdout.strip() and not observed.stderr.strip():
        return "exited"
    if observed.returncode or observed.stderr.strip() or not observed.stdout.strip():
        return "unknown"
    return "zombie" if observed.stdout.strip().startswith(b"Z") else "running"


def crash_case(root: Path, tool: Path, barrier: str, victim: str = "caller", timeout: float = 15) -> dict:
    fixture = Fixture(root, *(settlement_models() if barrier == "settlement" else simple_models()))
    client = Client(fixture, tool, [barrier], timeout)
    try:
        event = client.read()
        assert event["event"] == barrier
        before_locks = locks(fixture.repo)
        if victim == "caller":
            client.process.kill()  # Real SIGKILL, only the caller; not the Git child.
            client.process.wait(timeout=5)
            wait_until = time.monotonic() + 2
            while process_state(event["ref_pid"]) == "running" and time.monotonic() < wait_until:
                time.sleep(.01)
            result = {"exit_code": client.process.returncode, "receipt": None}
        else:
            assert barrier == "prepare" and event["ref_pid"] is not None
            os.kill(event["ref_pid"], signal.SIGKILL)
            client.release()
            result = client.finish()
        landed = barrier in ("ack", "settlement")
        oracle = fixture.check({"new_commit": event["candidate"]}, landed=landed, settled=False)
        oracle["checks"]["working_state_survives_death"] = oracle["checks"]["working_bytes_and_modes_exact"]
        if victim == "caller":
            oracle["checks"]["real_sigkill_observed"] = result["exit_code"] == -signal.SIGKILL
            oracle["checks"]["owned_index_lock_retained"] = any(r["path"] == ".git/index.lock" for r in oracle["locks"])
        else:
            oracle["checks"]["uncertain_not_success_receipt"] = result["receipt"]["status"] == "landing_uncertain"
            oracle["checks"]["git_owned_ref_locks_retained"] = any(r["path"] != ".git/index.lock" for r in oracle["locks"])
        return outcome(oracle, case="process_death", victim=victim, barrier=event, worker=result,
                       ref_child_state=process_state(event["ref_pid"]), locks_before=before_locks,
                       fixture=str(root), disposition="Observation only: no lock removal, ref rollback or automatic retry.")
    finally:
        client.close()


def benchmark_models(count: int, selected: int, density: float, patch_bytes: int = 32) -> tuple[Model, Model, Model, Model]:
    base = {f"files/{i:06d}": ("100644", b"baseline\n") for i in range(count)}
    candidate, staged, working = dict(base), dict(base), dict(base)
    payload = random.Random(7).randbytes(patch_bytes) + b"\0"
    for path in list(base)[:selected]:
        candidate[path] = ("100644", payload)
    for path in list(base)[selected:][:int((count - selected) * density)]:
        staged[path] = ("100644", b"foreign staged\n")
    working.update(staged)
    working["foreign-untracked"] = ("100644", b"working only\n")
    return base, candidate, staged, working


def object_stats(repo: Path) -> dict:
    paths = [p for p in (repo / ".git/objects").rglob("*") if p.is_file()]
    return {"files": len(paths), "bytes": sum(p.stat().st_size for p in paths)}


def configure_signing(fixture: Fixture, delay: float) -> dict:
    """Use an ephemeral real signer; never load a user's keyring or SSH agent."""
    executable = shutil.which("ssh-keygen")
    if executable:
        key = fixture.root / "synthetic-key"
        subprocess.run([executable, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                       env=fixture.env, capture_output=True, check=True, timeout=15)
        argv = [executable]
        info = {"format": "ssh", "program": "gpg.ssh.program", "argv": argv}
        fixture.g("config", "user.signingKey", str(key))
        allowed = fixture.root / "allowed-signers"
        allowed.write_text("fixture@example.invalid " + key.with_suffix(".pub").read_text())
        fixture.g("config", "gpg.ssh.allowedSignersFile", str(allowed))
    else:
        executable, control = shutil.which("gpg"), shutil.which("gpgconf")
        if not executable or not control:
            raise UnsupportedFixture("requires ssh-keygen or gpg plus gpgconf for real signing")
        home = fixture.root / "g"
        if len(os.fsencode(home)) + len("/S.gpg-agent.extra") >= 108:
            raise UnsupportedFixture("GnuPG socket path too long; use a shorter scratch path")
        home.mkdir(mode=0o700)
        fixture.signer_home = home
        argv = [executable, "--no-options", "--homedir", str(home), "--batch",
                "--pinentry-mode", "loopback", "--passphrase", ""]
        try:
            subprocess.run([*argv, "--quick-generate-key", "Synthetic <fixture@example.invalid>",
                            "ed25519", "sign", "0"], env=fixture.env,
                           capture_output=True, check=True, timeout=15)
        except Exception:
            close_signer(fixture)
            raise
        info = {"format": "openpgp", "program": "gpg.program", "argv": argv}
        fixture.g("config", "user.signingKey", "fixture@example.invalid")
    wrapper = fixture.root / "signer"
    wrapper.write_text("#!" + sys.executable + "\nimport os,sys,time\n"
                       + f"time.sleep({delay!r})\nos.execv({executable!r}, {argv!r} + sys.argv[1:])\n")
    wrapper.chmod(0o700)
    fixture.g("config", "gpg.format", info["format"])
    fixture.g("config", info["program"], str(wrapper))
    fixture.g("config", "commit.gpgSign", "true")
    return info


def close_signer(fixture: Fixture) -> None:
    home = getattr(fixture, "signer_home", None)
    if home is not None:
        # Exact synthetic home only; never --kill all against the user's home.
        subprocess.run([shutil.which("gpgconf"), "--homedir", str(home), "--kill", "gpg-agent"],
                       env=fixture.env, capture_output=True, check=True, timeout=5)


def benchmark_case(root: Path, tool: Path, count: int, selected: int, density: float,
                   patch_bytes: int = 32, signing_delay: float | None = None, timeout: float = 30) -> dict:
    setup_start = time.monotonic()
    fixture = Fixture(root, *benchmark_models(count, selected, density, patch_bytes))
    signing = None
    try:
        if signing_delay is not None:
            signing = configure_signing(fixture, signing_delay)
        setup_elapsed = time.monotonic() - setup_start
        before_objects = object_stats(fixture.repo)
        start = time.monotonic()
        result = run_client(fixture, tool, timeout)
        wall = time.monotonic() - start
        after_objects = object_stats(fixture.repo)
        oracle = fixture.check(result["receipt"])
        oracle["checks"]["committed_receipt"] = result["receipt"]["status"] == "committed"
        if signing:
            oracle["checks"]["real_signature_verified"] = fixture.g("verify-commit", "HEAD", check=False).returncode == 0
        return successful_case_outcome(fixture, result, oracle, case="benchmark", tracked=count, selected=selected, foreign_density=density,
                       payload_bytes=patch_bytes, signing_delay_seconds=signing_delay,
                       signing_format=signing["format"] if signing else None,
                       patch_bytes=fixture.patch.stat().st_size, fixture_setup_seconds=setup_elapsed,
                       invocation_wall_seconds=wall, object_growth={k: after_objects[k] - before_objects[k] for k in before_objects},
                       fixture=str(root))
    finally:
        close_signer(fixture)


def compare_case(root: Path, tool: Path, arm: str, timeout: float = 15) -> dict:
    fixture = Fixture(root, *simple_models())
    setup_seconds = 0.0
    start = time.monotonic()
    if arm == "one_checkout":
        result = run_client(fixture, tool, timeout)
        candidate = result["receipt"]["new_commit"]
    elif arm == "native_path_commit":
        fixture.g("commit", "--only", "-qm", "Selected file, not selected patch", "--", "shared.txt")
        candidate = fixture.g("rev-parse", "HEAD").stdout.decode().strip()
    else:
        worktree = fixture.root / "isolated"
        fixture.g("worktree", "add", "--detach", "--quiet", str(worktree), fixture.parent)
        setup_seconds = time.monotonic() - start
        git(worktree, fixture.env, "apply", "--index", "-", data=fixture.patch.read_bytes())
        git(worktree, fixture.env, "commit", "-qm", "Selected patch in isolated worktree")
        candidate = git(worktree, fixture.env, "rev-parse", "HEAD").stdout.decode().strip()
    elapsed = time.monotonic() - start
    actual_tree = read_entries(fixture.g("ls-tree", "-r", "-z", candidate).stdout, True)
    after = fixture.observe()
    checks = {"candidate_tree_exact": actual_tree == entry_model(fixture.candidate),
              "original_index_entries_unchanged": after["index"] == fixture.before["index"],
              "working_bytes_unchanged": after["working"] == fixture.working}
    # A path commit is an explicit negative control, not a bug in Git.
    expected = all(checks.values()) if arm != "native_path_commit" else not checks["candidate_tree_exact"]
    row = {"case": "workflow_comparison", "arm": arm, "classification": "pass" if expected else "invariant_failure",
            "checks": checks, "elapsed_seconds": elapsed, "worktree_setup_seconds": setup_seconds,
            "original_branch_advanced": after["head"] != fixture.parent, "candidate": candidate,
            "actual_candidate_tree": summarise(actual_tree, True), "fixture": str(root),
            "comparison_limit": "Worktree produces the same selected commit tree but does not land on the original branch. No speed-win claim is valid."}
    if arm == "one_checkout":
        checks["committed_receipt"] = result["receipt"]["status"] == "committed"
        return successful_case_outcome(fixture, result, {"checks": checks},
                                       **{key: value for key, value in row.items() if key != "classification"})
    return row



def compatibility_case(root: Path, tool: Path, kind: str, timeout: float = 15) -> dict:
    if kind.startswith("cli_"):
        name_sets = {
            "cli_portable_names": ["-leading", ":(glob)*", "bracket[1]", "tab\tname", "line\nname"],
            "cli_unicode_names": ["café", "cafe\u0301"],
            "cli_case_names": ["Case", "case"],
            "cli_byte_names": [os.fsdecode(b"raw-\xff")],
        }
        names = name_sets[kind]
        base = {p: ("100644", b"before\n") for p in names}
        candidate = {p: ("100755" if i % 2 else "100644", b"after\0\xff") for i, p in enumerate(names)}
        fixture = Fixture(root, base, candidate, base, base)
        argv = [sys.executable, "-B", "-I", str(tool), "commit", "--repo", str(fixture.repo),
                "--patch", str(fixture.patch), "--expected-parent", fixture.parent, "--message", "Byte names",
                *["--path=" + p for p in names]]
        result = subprocess.run(argv, env=fixture.env, capture_output=True, timeout=timeout)
        receipt = json.loads(result.stdout if result.stdout else result.stderr)
        oracle = fixture.check(receipt)
        oracle["checks"]["cli_succeeded"] = result.returncode == 0
        oracle["checks"]["receipt_path_bytes_roundtrip"] = sorted(os.fsencode(p) for p in receipt.get("changed_paths", [])) == sorted(os.fsencode(p) for p in names)
        return successful_case_outcome(fixture, {"receipt": receipt, "exit_code": result.returncode}, oracle,
                                       case=kind, command=argv, exit_code=result.returncode, receipt=receipt, fixture=str(root))
    fixture = Fixture(root, *simple_models())
    expected_code = None
    if kind.startswith("index_v"):
        fixture.g("update-index", "--index-version=" + kind[-1])
        if kind == "index_v3":
            # Git otherwise writes v2 despite --index-version=3. One unrelated
            # extended flag forces a genuine v3 full index (not sparse-index).
            fixture.g("update-index", "--skip-worktree", "--", "foreign")
    elif kind == "split_index":
        fixture.g("update-index", "--split-index")
        expected_code = "split_index"
    elif kind == "sparse_policy":
        fixture.g("config", "core.sparseCheckout", "true")
        expected_code = "sparse_index"
    elif kind == "submodule_edit":
        fixture.patch.write_bytes(("diff --git a/submodule b/submodule\nnew file mode 160000\n"
                                   "--- /dev/null\n+++ b/submodule\n@@ -0,0 +1 @@\n+Subproject commit "
                                   + fixture.parent + "\n").encode())
        fixture.changed = ["submodule"]
        expected_code = "unsupported_entry"
    elif kind == "drive_like_posix_name":
        fixture.changed = ["C:valid-on-posix"]
        expected_code = "invalid_path"  # Deliberate public path policy, not a claimed Git restriction.
    else:
        raise ValueError(kind)
    fixture.before = fixture.observe()
    observed_index_version = int.from_bytes(fixture.before["index_bytes"][4:8], "big")
    result = run_client(fixture, tool, timeout)
    refused = expected_code is not None
    oracle = fixture.check(result["receipt"], landed=not refused, settled=not refused)
    if kind.startswith("index_v"):
        oracle["checks"]["requested_version_really_exercised"] = observed_index_version == int(kind[-1])
    if kind == "index_v3":
        oracle["checks"]["foreign_extended_flag_preserved"] = fixture.g("ls-files", "-v", "--", "foreign").stdout.startswith(b"S ")
    oracle["checks"]["expected_disposition"] = (result["receipt"].get("code") == expected_code) if refused else result["receipt"]["status"] == "committed"
    if not refused:
        return successful_case_outcome(fixture, result, oracle, case=kind, expected_refusal=expected_code,
                                       index_version_before=observed_index_version, fixture=str(root))
    return outcome(oracle, case=kind, expected_refusal=expected_code, index_version_before=observed_index_version,
                   worker=result, fixture=str(root))


def signer_stall_case(root: Path, tool: Path, timeout: float = 15) -> dict:
    """Block a real signing child, observe, then explicitly release our fixture.

    This is a liveness diagnostic, not an invariant failure or an infinite-run proof.
    The synthetic signer has a finite failsafe and the controller has a deadline.
    """
    fixture = Fixture(root, *simple_models())
    signing = configure_signing(fixture, 0)
    executable, argv = signing["argv"][0], signing["argv"]
    ready, release = root / "signer-ready.json", root / "release-signer"
    wrapper = root / "signer"
    wrapper.write_text("#!" + sys.executable + "\nimport json,os,sys,time\nfrom pathlib import Path\n"
                       + f"Path({str(ready)!r}).write_text(json.dumps(dict(pid=os.getpid(), parent=os.getppid())))\n"
                       + f"deadline=time.monotonic()+{timeout!r}\n"
                       + f"while not Path({str(release)!r}).exists():\n"
                       + "    if time.monotonic()>deadline: raise SystemExit(124)\n    time.sleep(.01)\n"
                       + f"os.execv({executable!r}, {argv!r} + sys.argv[1:])\n")
    client = Client(fixture, tool, timeout=timeout)
    try:
        while not ready.exists():
            if time.monotonic() > client.deadline or client.process.poll() is not None:
                raise TimeoutError("signer did not reach the observation gate")
            time.sleep(.005)
        # Marker was created by the signer (a real Git descendant), not by a mock.
        observed_at = time.monotonic()
        time.sleep(.25)
        before_release = fixture.observe()
        add = fixture.g("add", "--", "untracked", check=False)
        observation = {"caller_still_waiting": client.process.poll() is None,
                       "index_lock_held": any(r["path"] == ".git/index.lock" for r in before_release["locks"]),
                       "branch_unchanged": before_release["head"] == fixture.parent,
                       "index_unchanged": before_release["index_bytes"] == fixture.before["index_bytes"],
                       "real_git_add_blocked": add.returncode != 0,
                       "observation_seconds": time.monotonic() - observed_at,
                       "signing_format": signing["format"], "signer": json.loads(ready.read_text())}
        release.write_text("release our synthetic signer\n")
        result = client.finish()
        oracle = fixture.check(result["receipt"])
        oracle["checks"]["real_signature_verified"] = fixture.g("verify-commit", "HEAD", check=False).returncode == 0
        oracle["checks"]["commits_after_explicit_signer_release"] = result["receipt"]["status"] == "committed"
        observed = all(observation[k] for k in ("caller_still_waiting", "index_lock_held", "branch_unchanged", "index_unchanged", "real_git_add_blocked"))
        return {"case": "controlled_signer_stall", "classification": "controlled_stall" if observed and all(oracle["checks"].values()) else "invariant_failure",
                "expected_fault": True, "observations": observation, "worker": result, "oracle": oracle,
                "fixture": str(root), "resolution": "Controller released the synthetic signer; operation completed. This observation does not test expiry of a production deadline.",
                "interpretation": "Bounded observation of an externally blocked signer; not evidence of an infinite hang or a missing runtime deadline."}
    finally:
        release.touch()
        client.close()
        close_signer(fixture)

def run(args: argparse.Namespace) -> dict:
    global WORKER
    if os.name != "posix":
        return {"schema": "one_checkout_stress_v1", "classification": "unsupported", "reason": "POSIX process/filename fixtures"}
    scratch = args.scratch.resolve()
    if not scratch.is_dir():
        raise ValueError("scratch must be an existing directory")
    if shutil.disk_usage(scratch).free < 512 * 1024 * 1024:
        raise ValueError("need 512 MiB free in scratch; no cleanup is attempted")
    root = Path(tempfile.mkdtemp(prefix="one-checkout-stress-", dir=scratch))
    # One immutable self-contained runtime per run. A shared source checkout can
    # change between cases; hashing it only at closeout would mislabel evidence.
    tool_source = args.tool
    args.tool, runtime_bytes = freeze_runtime(tool_source, root)
    worker_source, worker_bytes = WORKER, WORKER.read_bytes()
    WORKER = root / "stress_worker.py"
    WORKER.write_bytes(worker_bytes)
    results = []

    def attempt(name, call):
        case_root = root / f"{len(results):03d}-{name}"
        started = time.monotonic()
        try:
            row = call(case_root)
        except UnsupportedFixture as error:
            row = {"case": name, "classification": "unsupported", "reason": str(error), "fixture": str(case_root)}
        except FixtureGitTimeout as error:
            row = {"case": name, "classification": "fixture_timeout", "error": str(error), "fixture": str(case_root)}
        except (TimeoutError, subprocess.TimeoutExpired) as error:
            row = {"case": name, "classification": "operational_hang", "error": str(error), "fixture": str(case_root)}
        except Exception as error:
            row = {"case": name, "classification": "harness_error", "type": type(error).__name__,
                   "error": str(error), "fixture": str(case_root)}
        row["case_wall_seconds"] = time.monotonic() - started
        results.append(row)
        # Keep completed evidence even when the whole run is externally stopped.
        args.output.write_text(json.dumps({"schema": "one_checkout_stress_partial_v1",
            "complete": False, "return_baseline_commit": RETURN_BASELINE, "command": sys.argv,
            "runtime_sha256": digest(runtime_bytes), "runtime_source": str(tool_source),
            "harness_sha256": HARNESS_SHA256, "worker_sha256": digest(worker_bytes),
            "results": results, "retained_root": str(root)}, indent=2, sort_keys=True) + "\n")
        if args.progress:
            print(json.dumps({"case": name, "classification": row["classification"]}), file=sys.stderr, flush=True)
        # Preserve all failure/death evidence. Healthy runs have exited children and
        # no surviving locks; removing that entire disposable fixture is optional.
        if not args.retain and row["classification"] == "pass" and name != "death" and case_root.exists():
            if not locks(case_root / "repo"):
                shutil.rmtree(case_root)

    if args.mode in ("generated", "all"):
        for seed in range(args.seed, args.seed + args.seeds):
            attempt("generated", lambda p, s=seed: generated_case(p, args.tool, s, args.timeout, name_profile=args.name_profile))
        attempt("sha256", lambda p: generated_case(p, args.tool, args.seed, args.timeout, "sha256", args.name_profile))
    if args.mode in ("concurrency", "all"):
        for callers in (2, 4, 8):
            attempt("simultaneous", lambda p, n=callers: simultaneous_case(p, args.tool, n, args.timeout))
        for name in ("snapshot_contention", "branch_wins", "prepared_contention", "post_ack_advance", "settlement_contention"):
            attempt(name, lambda p, n=name: schedule_case(p, args.tool, n, args.timeout))
    if args.mode in ("compatibility", "all"):
        for kind in ("cli_portable_names", "cli_unicode_names", "cli_case_names", "cli_byte_names", "index_v2", "index_v3", "index_v4", "split_index", "sparse_policy", "submodule_edit", "drive_like_posix_name"):
            attempt(kind, lambda p, k=kind: compatibility_case(p, args.tool, k, args.timeout))
    if args.mode == "liveness":
        if shutil.which("ssh-keygen") or (shutil.which("gpg") and shutil.which("gpgconf")):
            attempt("signer_stall", lambda p: signer_stall_case(p, args.tool, args.timeout))
        else:
            results.append({"case": "signer_stall", "classification": "skip", "reason": "requires ssh-keygen or gpg plus gpgconf"})
    if args.mode in ("crash", "all"):
        for barrier in ("snapshot", "prepare", "ack", "settlement"):
            attempt("death", lambda p, b=barrier: crash_case(p, args.tool, b, timeout=args.timeout))
        attempt("death", lambda p: crash_case(p, args.tool, "prepare", "git_child", args.timeout))
    if args.mode == "bench":
        for repetition in range(args.repetitions):
            attempt("benchmark", lambda p: benchmark_case(p, args.tool, args.tracked, args.selected,
                    args.density, args.payload_bytes, args.signing_delay, args.timeout))
            results[-1]["repetition"] = repetition
    if args.mode in ("compare", "all"):
        for arm in ("one_checkout", "native_path_commit", "fresh_worktree"):
            attempt("compare", lambda p, a=arm: compare_case(p, args.tool, a, args.timeout))
    statuses = [r["classification"] for r in results]
    return {"schema": "one_checkout_stress_v1", "return_baseline_commit": RETURN_BASELINE,
            "runtime_sha256": digest(runtime_bytes), "runtime_source": str(tool_source), "harness_sha256": HARNESS_SHA256,
            "worker_sha256": digest(worker_bytes), "worker_source": str(worker_source), "command": sys.argv,
            "environment": {"python": sys.version, "git": subprocess.check_output(["git", "--version"]).decode().strip(),
                            "platform": platform.platform(), "machine": platform.machine(), "cpu_count": os.cpu_count(),
                            "scratch": str(scratch), "filesystem_note": "Record mount type externally; timings are environment-specific."},
            "classification": summary_classification(results),
            "runtime_cases_passed": statuses.count("pass"),
            "benchmark_summary": benchmark_summary(results) if args.mode == "bench" else None,
            "unsupported_capabilities": [{"case": row["case"], "reason": row.get("reason")} for row in results if row["classification"] in ("unsupported", "skip")],
            "counts": {s: statuses.count(s) for s in sorted(set(statuses))}, "results": results,
            "retained_root": str(root), "limits": {"worker_timeout_seconds": args.timeout,
                "fixture_git_timeout_seconds": FIXTURE_GIT_TIMEOUT_SECONDS, "max_generated_seeds": 128},
            "limitations": ["Controlled real-process schedules, not fully deterministic OS simulation or exhaustive interleavings.",
                            "SIGKILL is not power-loss testing. No durability, network-filesystem or hostile-writer guarantee.",
                            "RSS reports worker and maximum individual child separately, not aggregate process-tree peak.",
                            "Subprocess count is Python-launched processes; Git's own descendants are not counted.",
                            "No live Claude Code/Codex model session or productivity evaluation."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("generated", "concurrency", "crash", "compatibility", "liveness", "bench", "compare", "all"), default="all")
    parser.add_argument("--tool", type=Path, default=TOOL)
    parser.add_argument("--scratch", type=Path, default=Path(os.environ.get("ONE_CHECKOUT_TEST_TMPDIR", tempfile.gettempdir())))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--seeds", type=int, default=16)
    parser.add_argument("--name-profile", choices=("portable", "posix-stress"), default="portable",
                        help="portable names exercise real models on macOS; POSIX stress probes filesystem capabilities")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--tracked", type=int, default=1000)
    parser.add_argument("--selected", type=int, default=1)
    parser.add_argument("--density", type=float, default=.25)
    parser.add_argument("--payload-bytes", type=int, default=32)
    parser.add_argument("--signing-delay", type=float)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--retain", action="store_true")
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()
    if not (1 <= args.seeds <= 128 and 1 <= args.repetitions <= 15 and 1 <= args.timeout <= 300
            and 1 <= args.selected <= args.tracked <= 100000 and 0 <= args.density <= 1
            and 1 <= args.payload_bytes <= 1048576 and args.selected * args.payload_bytes <= 16 * 1048576
            and (args.signing_delay is None or 0 <= args.signing_delay <= 10)):
        parser.error("parameters exceed bounded experiment limits (including 16 MiB selected payload total)")
    args.tool = args.tool.resolve()
    try:
        result = run(args)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "classification": result["classification"], "counts": result.get("counts", {})}))
    return 0 if result["classification"] in ("pass", "pass_with_skips") else 1


if __name__ == "__main__":
    raise SystemExit(main())
