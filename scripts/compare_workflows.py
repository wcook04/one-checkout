#!/usr/bin/env python3
"""Reproduce scoped-commit trade-offs in disposable repositories, without agents.

This is a correctness comparison, not a productivity or competitor benchmark.
Every arm gets an already-selected patch; patch-selection cost is NOT measured.
Run from any directory. No --repo option exists: live repositories are never used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any

TOOL = Path(__file__).resolve().parents[1] / "skills/one-checkout/scripts/one_checkout.py"
BASE = ("A = 0\n" + "# gap\n" * 10 + "B = 0\n" + "# gap\n" * 10 + "C = 0\n").encode()
OWNED = BASE.replace(b"A = 0", b"A = 1")
STAGED = BASE.replace(b"B = 0", b"B = 2")
COMBINED = OWNED.replace(b"B = 0", b"B = 2")
WORKING = COMBINED.replace(b"C = 0", b"C = 3")
ARMS = ("git_pathspec", "git_private_index", "git_worktree", "one_checkout")


class Fixture:
    def __init__(self, root: Path, tool: Path, *, mixed: bool = True) -> None:
        self.root, self.tool = root, tool
        self.repo = root / "repo"
        self.repo.mkdir(parents=True)
        self.commands: list[dict[str, Any]] = []
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GIT_") and k not in ("PYTHONPATH", "PYTHONHOME")}
        home = root / "empty-home"
        home.mkdir()
        self.env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / "config"),
                        GIT_AUTHOR_DATE="2026-10-13T00:00:00+00:00",
                        GIT_COMMITTER_DATE="2026-10-13T00:00:00+00:00")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "One Checkout comparison")
        self.git("config", "user.email", "comparison@example.invalid")
        self.git("config", "commit.gpgSign", "false")
        self.git("config", "core.autocrlf", "false")
        (self.repo / "shared.py").write_bytes(BASE)
        (self.repo / "foreign.txt").write_bytes(b"foreign base\n")
        self.git("add", "--", "shared.py", "foreign.txt")
        self.git("commit", "-q", "-m", "baseline")
        self.parent = self.git("rev-parse", "HEAD").stdout.decode().strip()
        self.patch = root / "owned.patch"
        self.patch.write_bytes(self.make_patch("shared.py", OWNED, self.parent))
        if mixed:
            (self.repo / "shared.py").write_bytes(STAGED)
            self.git("add", "--", "shared.py")
        (self.repo / "foreign.txt").write_bytes(b"foreign staged\n")
        self.git("add", "--", "foreign.txt")
        (self.repo / "shared.py").write_bytes(WORKING if mixed else OWNED)
        (self.repo / "foreign.txt").write_bytes(b"foreign unstaged\n")

    def run(self, argv: list[str], *, data: bytes | None = None,
            cwd: Path | None = None, env: dict[str, str] | None = None,
            check: bool = True) -> subprocess.CompletedProcess[bytes]:
        started = time.perf_counter()
        result = subprocess.run(argv, cwd=cwd or self.repo,
                                env={**self.env, **(env or {})}, input=data,
                                capture_output=True, timeout=30)
        # Paths are normalised in logs; arbitrary external command output is
        # never executed or interpreted as a shell program.
        clean = lambda text: text.replace(str(self.root), "<fixture>").replace(str(self.tool), "<tool>")
        self.commands.append({"argv": [clean(x) for x in argv],
                              "cwd": clean(str(cwd or self.repo)),
                              "environment_overrides": {k: clean(v) for k, v in (env or {}).items()},
                              "stdin_sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
                              "return_code": result.returncode,
                              "seconds": time.perf_counter() - started,
                              "stdout": clean(result.stdout.decode("utf-8", "replace")),
                              "stderr": clean(result.stderr.decode("utf-8", "replace"))})
        if check and result.returncode:
            raise RuntimeError(f"{argv[0]} exited {result.returncode}: "
                               + result.stderr.decode("utf-8", "replace"))
        return result

    def git(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", **kwargs.pop("env", {})}
        return self.run(["git", "--no-replace-objects", "-c", f"core.hooksPath={os.devnull}",
                         "-c", "core.fsmonitor=false", *args], env=env, **kwargs)

    def make_patch(self, name: str, data: bytes, parent: str) -> bytes:
        path = self.repo / name
        previous = path.read_bytes()
        try:
            path.write_bytes(data)
            return self.git("diff", "--binary", "--no-ext-diff", "--no-textconv",
                            parent, "--", name).stdout
        finally:
            path.write_bytes(previous)

    def invoke(self, *, command: str = "commit", parent: str | None = None,
               path: str = "shared.py", check: bool = True) -> subprocess.CompletedProcess[bytes]:
        return self.run([sys.executable, "-B", "-I", str(self.tool), command,
                         "--repo", str(self.repo), "--patch", str(self.patch), "--path", path,
                         "--expected-parent", parent or self.parent,
                         "--message", "selected change"], check=check)

    def blob(self, treeish: str, path: str = "shared.py") -> bytes:
        return self.git("show", f"{treeish}:{path}").stdout

    def snapshot(self) -> dict[str, bytes]:
        return {"index": (self.repo / ".git/index").read_bytes(),
                "shared_index": self.blob(""), "foreign_index": self.blob("", "foreign.txt"),
                "shared_worktree": (self.repo / "shared.py").read_bytes(),
                "foreign_worktree": (self.repo / "foreign.txt").read_bytes()}


def compare_arm(f: Fixture, arm: str, *, mixed: bool) -> dict[str, Any]:
    before = f.snapshot()
    start_commands = len(f.commands)
    started = time.perf_counter()
    receipt = None
    if arm == "git_pathspec":
        f.git("commit", "-q", "-m", "selected path", "--", "shared.py")
        commit = f.git("rev-parse", "HEAD").stdout.decode().strip()
    elif arm == "git_private_index":
        # This intentionally small plumbing baseline has no index settlement,
        # path-scope validation or prepared symbolic-HEAD verification.
        env = {"GIT_INDEX_FILE": str(f.root / "private-index")}
        f.git("read-tree", f.parent, env=env)
        f.git("apply", "--cached", "-", data=f.patch.read_bytes(), env=env)
        tree = f.git("write-tree", env=env).stdout.decode().strip()
        commit = f.git("commit-tree", tree, "-p", f.parent,
                       data=b"selected patch\n").stdout.decode().strip()
        f.git("update-ref", "refs/heads/main", commit, f.parent)
    elif arm == "git_worktree":
        worktree = f.root / "worker"
        f.git("worktree", "add", "-q", "-b", "worker", str(worktree), f.parent)
        f.git("apply", str(f.patch), cwd=worktree)
        f.git("add", "--", "shared.py", cwd=worktree)
        f.git("commit", "-q", "-m", "selected patch", cwd=worktree)
        commit = f.git("rev-parse", "HEAD", cwd=worktree).stdout.decode().strip()
    elif arm == "one_checkout":
        f.invoke(command="plan")
        receipt = json.loads(f.invoke().stdout)
        commit = receipt["new_commit"]
    else:
        raise ValueError(arm)
    operation_seconds = time.perf_counter() - started
    operation_invocations = len(f.commands) - start_commands
    after = f.snapshot()
    checks = {
        "selected_commit_contains_only_A": f.blob(commit) == OWNED,
        "foreign_path_absent_from_commit": f.blob(commit, "foreign.txt") == b"foreign base\n",
        "shared_staged_blob_preserved": before["shared_index"] == after["shared_index"],
        "foreign_staged_blob_preserved": before["foreign_index"] == after["foreign_index"],
        "worktree_bytes_preserved": all(before[x] == after[x] for x in ("shared_worktree", "foreign_worktree")),
        "original_branch_advanced": f.git("rev-parse", "HEAD").stdout.decode().strip() != f.parent,
    }
    expected = {"selected_commit_contains_only_A": not mixed or arm != "git_pathspec",
                "foreign_path_absent_from_commit": True,
                "shared_staged_blob_preserved": arm != "git_pathspec" and (mixed or arm != "one_checkout"),
                "foreign_staged_blob_preserved": True,
                "worktree_bytes_preserved": True,
                "original_branch_advanced": arm != "git_worktree"}
    def readable(state: dict[str, bytes]) -> dict[str, str]:
        return {key: hashlib.sha256(value).hexdigest() if key == "index"
                else value.decode("utf-8") for key, value in state.items()}
    return {"arm": arm, "scenario": "same_file_ABC" if mixed else "independent_files_control",
            "before": readable(before), "after": readable(after),
            "checks": checks, "expected": expected, "oracle_satisfied": checks == expected,
            "operation_seconds": operation_seconds,
            "top_level_invocations": operation_invocations, "commit": commit,
            "receipt": receipt, "commands": f.commands,
            "contract": "isolated branch result, not integrated" if arm == "git_worktree" else "shared branch result"}


def handoff(f: Fixture, *, plain: bool) -> dict[str, Any]:
    first = json.loads(f.invoke().stdout)
    before = f.snapshot()
    # Both commands are read-only. Review the old-parent-relative staged delta,
    # not HEAD-relative diff after A has moved the baseline.
    staged_delta = f.git("diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv",
                         f.parent, "--", "shared.py").stdout
    head_relative = f.git("diff", "--cached", "--", "shared.py").stdout.decode()
    stale = f.invoke(check=False)
    stale_preserved_state = f.snapshot() == before
    if plain:
        f.git("commit", "-q", "-m", "ordinary next commit (negative control)")
    else:
        # Only safe here because the synthetic non-overlap is known and checked.
        # This is not an automatic rebasing rule for user patches.
        f.patch.write_bytes(staged_delta)
        second = json.loads(f.invoke(parent=first["new_commit"]).stdout)
        if second["status"] != "committed":
            raise RuntimeError("explicit second patch did not land")
    final = f.blob("HEAD")
    expected = STAGED if plain else COMBINED
    checks = {"first_commit_only_A": f.blob(first["new_commit"]) == OWNED,
              "stale_parent_refused": stale.returncode == 2 and json.loads(stale.stderr)["code"] == "stale_parent",
              "stale_refusal_preserved_state": stale_preserved_state,
              "expected_second_tree": final == expected,
              "working_bytes_unchanged": (f.repo / "shared.py").read_bytes() == WORKING}
    return {"scenario": "plain_commit_handoff" if plain else "reviewed_patch_handoff",
            "checks": checks, "oracle_satisfied": all(checks.values()),
            "A_survives_second_commit": b"A = 1\n" in final,
            "B_present": b"B = 2\n" in final,
            "retained_index_still_omits_A": b"A = 1\n" not in f.blob(""),
            "head_relative_staged_diff_after_A": head_relative,
            "old_parent_relative_B_patch": staged_delta.decode(),
            "first_receipt": first, "commands": f.commands}


def candidate_dependency(f: Fixture) -> dict[str, Any]:
    # A deliberately dependency-incomplete patch. The working file supplies B,
    # so testing that file cannot prove the committed candidate works.
    patch = f.make_patch("shared.py", BASE + b"print(required_by_A)\n", f.parent)
    f.patch.write_bytes(patch)
    working = BASE + b"required_by_A = 7\nprint(required_by_A)\n"
    (f.repo / "shared.py").write_bytes(working)
    work_result = f.run([sys.executable, "-B", "-I", str(f.repo / "shared.py")], check=False)
    result = json.loads(f.invoke().stdout)
    candidate = f.root / "candidate.py"
    candidate.write_bytes(f.blob(result["new_commit"]))
    candidate_result = f.run([sys.executable, "-B", "-I", str(candidate)], check=False)
    checks = {"worktree_check_passes": work_result.returncode == 0,
              "candidate_check_fails": candidate_result.returncode != 0,
              "candidate_failed_for_missing_dependency": b"NameError" in candidate_result.stderr,
              "scoped_commit_succeeded": result["status"] == "committed",
              "working_bytes_unchanged": (f.repo / "shared.py").read_bytes() == working}
    return {"scenario": "candidate_dependency_negative_control", "checks": checks,
            "oracle_satisfied": all(checks.values()), "commands": f.commands}


def comparison(tool: Path = TOOL, *, repeats: int = 3, seed: int = 7132026) -> dict[str, Any]:
    tool = tool.resolve()
    if not tool.is_file():
        raise ValueError("runtime not found")
    if not 1 <= repeats <= 25:
        raise ValueError("repeats must be between 1 and 25")
    results = []
    rng = random.Random(seed)
    # The context owns every file created. An interrupted run may leave scratch,
    # but this script never cleans outside this one uniquely-created directory.
    with tempfile.TemporaryDirectory(prefix="one-checkout-compare-",
                                     dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as tmp:
        root = Path(tmp).resolve()
        for trial in range(repeats):
            for mixed in (True, False):
                arms = list(ARMS)
                rng.shuffle(arms)
                for arm in arms:
                    fixture_root = root / f"{trial}-{mixed}-{arm}"
                    started = time.perf_counter()
                    f = Fixture(fixture_root, tool, mixed=mixed)
                    setup_seconds = time.perf_counter() - started
                    result = compare_arm(f, arm, mixed=mixed)
                    result.update(trial=trial, fixture_setup_seconds=setup_seconds)
                    results.append(result)
        extras = [handoff(Fixture(root / "handoff-plain", tool), plain=True),
                  handoff(Fixture(root / "handoff-patch", tool), plain=False),
                  candidate_dependency(Fixture(root / "dependency", tool))]
    medians = {f"{scenario}/{arm}": statistics.median(r["operation_seconds"] for r in results
               if r["scenario"] == scenario and r["arm"] == arm)
               for scenario in ("same_file_ABC", "independent_files_control") for arm in ARMS}
    return {"schema": "one_checkout_workflow_comparison_v1", "seed": seed, "repeats": repeats,
            "status": "passed" if all(x["oracle_satisfied"] for x in [*results, *extras]) else "failed",
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "git": subprocess.run(["git", "--version"], check=True, capture_output=True,
                                                  text=True, timeout=10).stdout.strip(),
                            "runtime_sha256": hashlib.sha256(tool.read_bytes()).hexdigest()},
            "measurement_kind": "correctness fixtures with diagnostic local timings",
            "limitations": ["Synthetic preselected patches; no live agents or patch-authoring time.",
                            "Arms perform different operations; One Checkout includes plan plus commit. Timings are not a speed ranking.",
                            "Worktree produces an isolated branch, not an integrated shared-branch result.",
                            "No third-party application was executed; Git commands are not an Agentlocks benchmark.",
                            "Local timings exclude dependency installation and do not measure developer productivity.",
                            "Positive hand-off is a reviewed non-overlapping fixture; index reconciliation remains manual."],
            "median_operation_seconds": medians, "runs": results, "handoffs_and_controls": extras,
            "fixture_removed_on_exit": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", type=Path, default=TOOL)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=7132026)
    args = parser.parse_args()
    try:
        report = comparison(args.tool, repeats=args.repeats, seed=args.seed)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
