#!/usr/bin/env python3
"""Paired, disposable runtime measurements; setup and verification timed separately."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "skills/one-checkout/scripts/one_checkout.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample(core, demo, folder: Path, extra_files: int) -> dict:
    start = time.perf_counter()
    fixture = demo.Fixture(folder)
    if extra_files:
        bulk = fixture.repo / "unchanged"
        bulk.mkdir()
        for i in range(extra_files):
            (bulk / f"{i:06d}.txt").write_text("unchanged\n")
        fixture.git("add", "--", "unchanged")
        # Preserve the demo's foreign staging while extending its parent.
        fixture.git("commit", "-q", "--only", "-m", "Unchanged files", "--", "unchanged")
        fixture.parent = fixture.git("rev-parse", "HEAD").decode().strip()
    before = fixture.snapshot()
    metadata = fixture.metadata()
    patch = fixture.patch.read_bytes()
    setup_seconds = time.perf_counter() - start
    run = core.Git.run
    calls = []

    def measured(git, args, **kwargs):
        begin = time.perf_counter()
        try:
            return run(git, args, **kwargs)
        finally:
            calls.append({"command": args[0], "seconds": time.perf_counter() - begin})

    def invoke(**kwargs):
        calls.clear()
        begin = time.perf_counter()
        with mock.patch.dict(os.environ, fixture.env, clear=True), mock.patch.object(core.Git, "run", new=measured):
            result = core.commit_patch(repo=fixture.repo, patch=patch, paths=["shared.py"],
                                       expected_parent=fixture.parent, **kwargs)
        return result, {"seconds": time.perf_counter() - begin,
                        "git_run_calls": len(calls),
                        "git_run_seconds": sum(row["seconds"] for row in calls)}

    plan, plan_cost = invoke(dry_run=True)
    start = time.perf_counter()
    if fixture.metadata() != metadata or fixture.snapshot() != before:
        raise RuntimeError("plan changed repository")
    verification_seconds = time.perf_counter() - start
    receipt, commit_cost = invoke(message="Benchmark selected patch", reviewed_plan=plan)
    start = time.perf_counter()
    after = fixture.snapshot()
    checks = {
        "committed": receipt["status"] == "committed" and receipt["reviewed_plan_verified"],
        "selected_tree": after["head"] == demo.OWNED.decode()
            and after["foreign_head"] == demo.FOREIGN_BASE.decode(),
        "preserved_index_and_worktree": all(before[k] == after[k] for k in
            ("index", "foreign_index", "worktree", "foreign_worktree", "untracked")),
        "parent": fixture.git("rev-parse", "HEAD^").decode().strip() == fixture.parent,
        "exact_scope": fixture.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == b"shared.py\n",
        "unchanged_files": not extra_files or len(fixture.git("ls-tree", "-r", "--name-only", "HEAD", "--", "unchanged").splitlines()) == extra_files,
    }
    if not all(checks.values()):
        raise RuntimeError("benchmark validation failed: " + json.dumps(checks))
    verification_seconds += time.perf_counter() - start
    return {"setup_seconds": setup_seconds, "plan": plan_cost, "commit": commit_cost,
            "verification_seconds": verification_seconds, "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-runtime", type=Path, required=True,
                        help="Runtime from the pinned baseline revision")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--extra-files", type=int, default=0)
    args = parser.parse_args()
    if args.repeats < 1 or args.extra_files < 0:
        parser.error("repeats must be positive and extra-files nonnegative")
    # Like the demo, this is an explicitly disposable fixture environment.
    for key in list(os.environ):
        if key.startswith("GIT_"):
            del os.environ[key]
    demo = load(RUNTIME.with_name("demo.py"), "benchmark_demo")
    paths = {"baseline": args.baseline_runtime.resolve(), "candidate": RUNTIME}
    modules = {key: load(path, "benchmark_" + key) for key, path in paths.items()}
    rows = {key: [] for key in paths}
    with tempfile.TemporaryDirectory(prefix="one-checkout-benchmark-",
                                     dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as temp:
        root = Path(temp).resolve()
        # Alternate order to reduce systematic warm-cache/order bias. No hidden warmups.
        for i in range(args.repeats):
            order = list(paths) if i % 2 == 0 else list(reversed(paths))
            for key in order:
                rows[key].append(sample(modules[key], demo, root / f"{i}-{key}", args.extra_files))
        git_version = demo.subprocess.run(["git", "--version"], capture_output=True, check=True).stdout.decode().strip()
    summary = {key: {phase: statistics.median(row[phase]["seconds"] for row in values)
                     for phase in ("plan", "commit")} for key, values in rows.items()}
    print(json.dumps({"schema": "one_checkout_benchmark_v1", "versions": {
        "python": sys.version.split()[0], "git": git_version, "platform": platform.platform()},
        "runtime_sha256": {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()},
        "extra_files": args.extra_files, "repeats": args.repeats,
        "median_seconds": summary, "samples": rows,
        "limitations": "In-process runtime; excludes interpreter startup, setup, review, tests and cleanup. Git.run timing excludes the separate update-ref protocol process. Local synthetic fixture, not agent productivity."}, indent=2))


if __name__ == "__main__":
    main()
