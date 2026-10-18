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
BRANCH_POLICY_SOURCE = {
    "url": "https://github.com/southpenlabs/agent-media/pull/153",
    "revision": "34f82b4ab2210fae8ba1d1ef7b9499c58b6156d8",
    "role": "named-branch policy source, not feedback authorship or adoption",
}
# Independently authored fixture for the named-branch subset of that policy.
# It deliberately does not reproduce the upstream hook's detached-rebase case.
BRANCH_POLICY_HOOK = """#!/bin/sh
printf '%s\\n' called >> "$(dirname "$0")/calls"
local_dir=$(git rev-parse --absolute-git-dir) || exit 1
common_dir=$(git rev-parse --path-format=absolute --git-common-dir) || exit 1
[ "$local_dir" != "$common_dir" ] && exit 0
[ "$(git symbolic-ref -q HEAD)" = refs/heads/main ]
"""


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
               path: str = "shared.py", plan_file: Path | None = None,
               check: bool = True) -> subprocess.CompletedProcess[bytes]:
        argv = [sys.executable, "-B", "-I", str(self.tool), command,
                "--repo", str(self.repo), "--patch", str(self.patch), "--path", path,
                "--expected-parent", parent or self.parent,
                "--message", "selected change"]
        if plan_file is not None:
            argv.extend(["--plan-file", str(plan_file)])
        return self.run(argv, check=check)

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


def _policy_snapshot(f: Fixture) -> dict[str, Any]:
    # Linked worktrees have a .git file and their own index in the common repo.
    index = Path(f.git("rev-parse", "--path-format=absolute", "--git-path", "index").stdout.decode().strip())
    return {"head": f.git("rev-parse", "HEAD").stdout.decode().strip(),
            "branch": f.git("symbolic-ref", "HEAD").stdout.decode().strip(),
            "refs": dict(line.split(" ", 1) for line in f.git(
                "for-each-ref", "--format=%(refname) %(objectname)").stdout.decode().splitlines()),
            "index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
            "index_entries": f.git("ls-files", "--stage", "-z").stdout.decode(),
            "working_sha256": {name: hashlib.sha256((f.repo / name).read_bytes()).hexdigest()
                               for name in ("shared.py", "foreign.txt", "untracked.bin")}}


def project_branch_admission(f: Fixture, plan: dict[str, Any]) -> dict[str, Any]:
    """Example caller-owned metadata policy; it never executes a hook or tests code."""
    local = Path(f.git("rev-parse", "--absolute-git-dir").stdout.decode().strip()).resolve(strict=True)
    common = Path(f.git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.decode().strip()).resolve(strict=True)
    repository_matches = Path(plan["repository"]).resolve() == f.repo.resolve()
    allowed = repository_matches and (local != common or plan["branch"] == "refs/heads/main")
    return {"allowed": allowed, "primary_checkout": local == common,
            "repository_matches": repository_matches, "reviewed_branch": plan["branch"],
            "reason": "allowed" if allowed else "project_branch_policy"}


def branch_policy_control(f: Fixture, mode: str, topology: str, *, switch_branch: bool = False) -> dict[str, Any]:
    if mode not in ("native_hook", "one_checkout_without_admission", "project_admission"):
        raise ValueError(mode)
    if topology == "primary_feature":
        f.git("switch", "-q", "-c", "feature")
    elif topology == "linked_feature":
        linked = f.root / "linked"
        f.git("worktree", "add", "-q", "-b", "feature", str(linked), f.parent)
        f.repo = linked
        (linked / "shared.py").write_bytes(STAGED)
        (linked / "foreign.txt").write_bytes(b"foreign staged\n")
        f.git("add", "--", "shared.py", "foreign.txt")
        (linked / "shared.py").write_bytes(WORKING)
        (linked / "foreign.txt").write_bytes(b"foreign unstaged\n")
    elif topology != "primary_main":
        raise ValueError(topology)
    (f.repo / "untracked.bin").write_bytes(b"untracked\0fixture\n")
    hooks = f.root / "policy-hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text(BRANCH_POLICY_HOOK, encoding="utf-8")
    hook.chmod(0o755)
    f.git("config", "core.hooksPath", str(hooks))
    before = _policy_snapshot(f)
    plan = admission = receipt = None
    checks: dict[str, bool] = {}
    if mode == "native_hook":
        # Fixture.git normally disables hooks; this arm explicitly enables only
        # this trusted disposable hook. No live project's hooks are executed.
        operation = f.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
                           "-c", f"core.hooksPath={hooks}", "commit", "-q", "-m", "ordinary staged commit"],
                          env={"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}, check=False)
    else:
        plan_result = f.invoke(command="plan")
        plan = json.loads(plan_result.stdout)
        plan_path = f.root / "reviewed-plan.json"
        plan_path.write_bytes(plan_result.stdout)
        checks["planning_preserves_repository"] = _policy_snapshot(f) == before
        if mode == "project_admission":
            admission = project_branch_admission(f, plan)
        if switch_branch:
            if not admission or not admission["allowed"]:
                raise RuntimeError("branch-switch control needs an admitted plan")
            f.git("branch", "different", f.parent)
            f.git("symbolic-ref", "HEAD", "refs/heads/different")
            before = _policy_snapshot(f)
            checks["switch_preserved_parent_oid"] = before["head"] == plan["parent"]
        operation = (f.invoke(plan_file=plan_path, check=False)
                     if admission is None or admission["allowed"] else None)
        if operation is not None:
            receipt = json.loads(operation.stdout if operation.returncode == 0 else operation.stderr)
    after = _policy_snapshot(f)
    hook_calls = (hooks / "calls").read_text().splitlines() if (hooks / "calls").exists() else []
    committed = operation is not None and operation.returncode == 0
    expected_commit = not switch_branch and (topology != "primary_feature" or mode == "one_checkout_without_admission")
    checks.update({"outcome_matches_policy_control": committed == expected_commit,
                   "ordinary_hook_execution_matches_arm": len(hook_calls) == (1 if mode == "native_hook" else 0),
                   "index_entries_preserved": after["index_entries"] == before["index_entries"],
                   "working_and_untracked_bytes_preserved": after["working_sha256"] == before["working_sha256"]})
    if committed:
        target = before["branch"]
        checks.update({"captured_parent_used": f.git("rev-parse", "HEAD^").stdout.decode().strip() == f.parent,
                       "expected_commit_contents": f.blob("HEAD") == (STAGED if mode == "native_hook" else OWNED),
                       "expected_foreign_contents": f.blob("HEAD", "foreign.txt") == (
                           b"foreign staged\n" if mode == "native_hook" else b"foreign base\n"),
                       "only_target_ref_advanced": after["refs"] == {**before["refs"], target: after["head"]},
                       "branch_unchanged": after["branch"] == target})
    else:
        # An ordinary refused commit may refresh index stat data. Its entries
        # must survive; our admission/runtime refusals preserve raw bytes too.
        checks["refusal_preserves_refs_and_head"] = all(after[key] == before[key] for key in ("refs", "head", "branch"))
        if mode != "native_hook":
            checks["refusal_preserves_index_bytes"] = after["index_sha256"] == before["index_sha256"]
        if switch_branch:
            checks["saved_plan_refuses_changed_branch"] = (receipt is not None and receipt["code"] == "plan_mismatch"
                                                          and "branch" in receipt["message"])
    return {"scenario": f"{mode}/{topology}" + ("/same_oid_branch_switch" if switch_branch else ""),
            "policy_source": BRANCH_POLICY_SOURCE, "mode": mode, "topology": topology,
            "ordinary_hook_executed": bool(hook_calls), "project_metadata_policy_evaluated": admission is not None,
            "commit_invoked": operation is not None, "return_code": operation.returncode if operation else None,
            "plan": plan, "admission": admission, "receipt": receipt, "before": before, "after": after,
            "checks": checks, "oracle_satisfied": all(checks.values()), "commands": f.commands}


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
        policy_controls = [branch_policy_control(Fixture(root / f"policy-{mode}-{topology}", tool), mode, topology)
                           for mode in ("native_hook", "one_checkout_without_admission", "project_admission")
                           for topology in ("primary_main", "primary_feature", "linked_feature")]
        policy_controls.extend(branch_policy_control(Fixture(root / f"policy-switch-{topology}", tool),
                                                     "project_admission", topology, switch_branch=True)
                               for topology in ("primary_main", "linked_feature"))
    medians = {f"{scenario}/{arm}": statistics.median(r["operation_seconds"] for r in results
               if r["scenario"] == scenario and r["arm"] == arm)
               for scenario in ("same_file_ABC", "independent_files_control") for arm in ARMS}
    return {"schema": "one_checkout_workflow_comparison_v1", "seed": seed, "repeats": repeats,
            "status": "passed" if all(x["oracle_satisfied"] for x in [*results, *extras, *policy_controls]) else "failed",
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
                            "Positive hand-off is a reviewed non-overlapping fixture; index reconciliation remains manual.",
                            "Branch policy controls use an independently authored named-branch subset of PR153; not its complete hook or rebase behavior.",
                            "Project admission is caller-owned metadata policy, not generic hook execution, code validation or hostile-user access control."],
            "median_operation_seconds": medians, "runs": results, "handoffs_and_controls": extras,
            "branch_policy_controls": policy_controls,
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
