#!/usr/bin/env python3
"""Real-Git ref/object/storage matrix in disposable fixtures, never a live repo.

P01's 30-case matrix, made portable for Git versions without --ref-format.
Shares independent raw Git/blob/working-state oracles with stress.py. Runtime
imports and mock barriers are test-only; each competitor is a native Git command.
"""
from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from unittest import mock

_SPEC = importlib.util.spec_from_file_location("backend_oracles", Path(__file__).with_name("stress.py"))
assert _SPEC and _SPEC.loader
stress = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(stress)
HARNESS_SHA256 = stress.digest(Path(__file__).read_bytes())


def configurations():
    for backend in ("files", "reftable"):
        for objects in ("sha1", "sha256"):
            for state in ("ordinary", "packed", "linked"):
                if backend == "reftable" and state == "packed":
                    continue  # Packed loose refs belong to the files backend.
                for case in ("same_file", "switch_before_prepare", "switch_while_prepared"):
                    yield backend, objects, state, case


def initialize(repo: Path, env: dict, backend: str, objects: str, timeout: float = 30) -> str:
    result = stress.git(repo, env, "init", "-q", "-b", "main", "--ref-format=" + backend,
                        "--object-format=" + objects, check=False, timeout=timeout)
    spelling = "explicit_ref_format"
    error = os.fsdecode(result.stderr)
    if result.returncode and backend == "files" and "unknown option" in error and "ref-format" in error:
        # Older Git has one files backend. Do not skip every real case merely
        # because the newer spelling for that existing capability is absent.
        result = stress.git(repo, env, "init", "-q", "-b", "main", "--object-format=" + objects, check=False, timeout=timeout)
        spelling = "legacy_files_backend"
        error = os.fsdecode(result.stderr)
    if result.returncode:
        if "unknown option" in error or "unknown hash" in error or "unsupported" in error:
            raise stress.UnsupportedFixture(error.strip())
        raise RuntimeError("Git initialization failed: " + error)
    observed_objects = stress.git(repo, env, "rev-parse", "--show-object-format", timeout=timeout).stdout.strip().decode()
    observed = stress.git(repo, env, "config", "--get", "extensions.refStorage", check=False, timeout=timeout)
    observed_backend = observed.stdout.strip().decode() if observed.returncode == 0 else "files"
    if observed_objects != objects or observed_backend != backend:
        raise AssertionError("fixture backend/object format differs from request")
    return spelling


def backend_case(root: Path, tool: Path, backend: str, objects: str, state: str,
                 case: str, timeout: float = 30) -> dict:
    root.mkdir()
    home = root / "home"
    home.mkdir()
    env = stress.isolated_env(home, root)
    main = root / "main"
    main.mkdir()
    repo = main
    spelling = initialize(main, env, backend, objects, timeout)
    for key, value in {"user.name": "Synthetic matrix", "user.email": "fixture@example.invalid",
                       "commit.gpgSign": "false", "gc.auto": "0", "maintenance.auto": "false"}.items():
        stress.git(main, env, "config", key, value, timeout=timeout)
    base = b"".join(f"line {i:02d}\n".encode() for i in range(25))
    (main / "shared.txt").write_bytes(base)
    stress.git(main, env, "add", "--", "shared.txt", timeout=timeout)
    stress.git(main, env, "commit", "-qm", "Synthetic baseline", timeout=timeout)
    if state == "linked":
        repo = root / "linked"
        stress.git(main, env, "worktree", "add", "-qb", "task", str(repo), timeout=timeout)
    def g(*args, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return stress.git(repo, env, *args, **kwargs)
    def text(*args):
        return os.fsdecode(g(*args).stdout).strip()
    parent, branch = text("rev-parse", "HEAD"), text("symbolic-ref", "HEAD")
    g("branch", "other", parent)
    if state == "packed":
        g("pack-refs", "--all", "--prune")
    selected = base.replace(b"line 01\n", b"A selected\n")
    (repo / "shared.txt").write_bytes(selected)
    patch = g("diff", "--binary", "--no-ext-diff", "--no-textconv", parent, "--", "shared.txt").stdout
    staged = base.replace(b"line 12\n", b"B staged\n")
    (repo / "shared.txt").write_bytes(staged)
    g("add", "--", "shared.txt")
    working = staged.replace(b"line 01\n", b"A selected\n").replace(b"line 23\n", b"C unowned\n")
    (repo / "shared.txt").write_bytes(working)
    index = Path(text("rev-parse", "--path-format=absolute", "--git-path", "index"))
    before_index = index.read_bytes()
    before_working = stress.working_model(repo)
    spec = importlib.util.spec_from_file_location("backend_target", tool)
    assert spec and spec.loader
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    extras = {}
    # tempfile caches its first root; changing TMPDIR alone after argparse has
    # evaluated its default would still put direct-import runtime scratch there.
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(tempfile, "tempdir", str(root)):
        def land():
            options = {"git_timeout": timeout} if "git_timeout" in inspect.signature(core.commit_patch).parameters else {}
            return core.commit_patch(repo=repo, patch=patch, paths=["shared.txt"],
                                     expected_parent=parent, message="Synthetic A only", **options)
        if case == "switch_before_prepare":
            command = core.Git.command
            switched = []
            def switch(self, args):
                if args[0] == "update-ref" and not switched:
                    g("symbolic-ref", "HEAD", "refs/heads/other")
                    switched.append(True)
                return command(self, args)
            with mock.patch.object(core.Git, "command", switch):
                try:
                    land()
                except core.Refusal as error:
                    extras["refusal_code"] = error.code
                else:
                    raise AssertionError("same-OID branch switch was not refused")
            checks = {"branch_identity_refusal": extras["refusal_code"] == "head_race",
                      "both_same_oid_branches_unchanged": text("rev-parse", branch) == parent
                      and text("rev-parse", "refs/heads/other") == parent,
                      "competing_branch_is_current": text("symbolic-ref", "HEAD") == "refs/heads/other"}
            expected_tree = {"shared.txt": ("100644", base)}
        else:
            if case == "switch_while_prepared":
                verifier = core._verify_prepared_head
                def competing(git, br, pa, *args, **kwargs):
                    result = g("symbolic-ref", "HEAD", "refs/heads/other", check=False)
                    extras["competing_symref_returncode"] = result.returncode
                    verifier(git, br, pa, *args, **kwargs)
                with mock.patch.object(core, "_verify_prepared_head", competing):
                    receipt = land()
            else:
                receipt = land()
            checks = {"committed_receipt": receipt["status"] == "committed",
                      "receipt_names_head": receipt["new_commit"] == text("rev-parse", "HEAD"),
                      "exact_parent": text("rev-parse", "HEAD^") == parent,
                      "exact_branch_identity": text("symbolic-ref", "HEAD") == branch}
            if case == "switch_while_prepared":
                checks["native_preparation_blocks_competing_head_writer"] = extras["competing_symref_returncode"] != 0
            expected_tree = {"shared.txt": ("100644", selected)}
    raw_tree = g("ls-tree", "-r", "--full-tree", "-z", "HEAD").stdout
    raw_index = g("ls-files", "--stage", "-z").stdout
    checks.update(tree_exact=stress.read_entries(raw_tree, True) == stress.entry_model(expected_tree, objects),
                  index_entries_exact=stress.read_entries(raw_index) == stress.entry_model({"shared.txt": ("100644", staged)}, objects),
                  index_bytes_unchanged=index.read_bytes() == before_index,
                  working_bytes_and_modes_exact=stress.working_model(repo) == before_working,
                  no_remaining_index_lock=not index.with_name(index.name + ".lock").exists())
    return {"case": case, "classification": "pass" if all(checks.values()) else "invariant_failure",
            "ref_backend": backend, "object_format": objects, "storage_state": state,
            "backend_initialization": spelling, "checks": checks, "parent": parent, "branch": branch,
            "head_after": text("rev-parse", "HEAD"), "raw_tree_sha256": stress.digest(raw_tree),
            "raw_index_sha256": stress.digest(raw_index), "index_before_sha256": stress.digest(before_index),
            "index_after_sha256": stress.digest(index.read_bytes()),
            "gitdir_distinct_from_common": text("rev-parse", "--absolute-git-dir") != text("rev-parse", "--path-format=absolute", "--git-common-dir"),
            "fixture": str(root), **extras}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", type=Path, default=stress.TOOL)
    parser.add_argument("--scratch", type=Path, default=Path(os.environ.get("ONE_CHECKOUT_TEST_TMPDIR", tempfile.gettempdir())))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retain", action="store_true")
    args = parser.parse_args()
    if os.name != "posix" or not shutil.which("git"):
        parser.error("requires POSIX and Git")
    if not args.scratch.is_dir() or not 1 <= args.timeout <= 300:
        parser.error("existing scratch directory and bounded timeout required")
    root = Path(tempfile.mkdtemp(prefix="one-checkout-backends-", dir=args.scratch.resolve()))
    tool, content = stress.freeze_runtime(args.tool.resolve(), root)
    rows = []
    for index, (backend, objects, state, case) in enumerate(configurations()):
        case_root = root / str(index)
        try:
            row = backend_case(case_root, tool, backend, objects, state, case, args.timeout)
        except stress.UnsupportedFixture as error:
            row = {"case": case, "classification": "unsupported", "ref_backend": backend,
                   "object_format": objects, "storage_state": state, "reason": str(error)}
        except Exception as error:
            row = {"case": case, "classification": "harness_error", "ref_backend": backend,
                   "object_format": objects, "storage_state": state, "error": repr(error)}
        rows.append(row)
        report = {"schema": "one_checkout_backend_matrix_v1", "complete": len(rows) == 30,
                  "classification": stress.summary_classification(rows), "runtime_source": str(args.tool.resolve()),
                  "runtime_sha256": stress.digest(content), "harness_sha256": HARNESS_SHA256,
                  "environment": {"git": subprocess.check_output(["git", "--version"], text=True).strip(),
                                  "python": sys.version, "platform": platform.platform(), "scratch": str(args.scratch.resolve())},
                  "counts": {status: sum(r["classification"] == status for r in rows) for status in ("pass", "unsupported", "invariant_failure", "harness_error")},
                  "results": rows, "retained_root": str(root),
                  "limitations": ["Synthetic local Git cases, no network/storage power-loss or live-agent assurance.",
                                  "Same-OID switches use test-only barriers and native Git competitors; not exhaustive scheduler coverage.",
                                  "Unsupported backend capabilities are reported; zero exercised cases never pass."]}
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        if row["classification"] == "pass" and not args.retain:
            shutil.rmtree(case_root)
    print(json.dumps({"output": str(args.output), "classification": report["classification"], "counts": report["counts"]}))
    return 0 if report["classification"] in ("pass", "pass_with_skips") else 1


if __name__ == "__main__":
    raise SystemExit(main())
