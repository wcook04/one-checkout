#!/usr/bin/env python3
"""Prepare/grade disposable agent tasks. Never launches a model or changes HOME.

State checks are not trajectory grades. The oracle lives outside the task repo;
an evaluation host must withhold it from the model, not rely on obscurity.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

TOOL = Path(__file__).resolve().parents[1] / "skills/one-checkout/scripts/one_checkout.py"
CASES = ("tracked", "addition", "deletion", "same-file", "ambiguous-ownership",
         "stale-parent", "branch-switch", "changed-patch", "missing-ack", "landed-unsettled",
         "committed-handoff")
BASE = b"A = 0\nB = 0\nC = 0\n"
OWNED = b"A = 1\nB = 0\nC = 0\n"
STAGED = b"A = 0\nB = 2\nC = 0\n"
WORKING = b"A = 1\nB = 2\nC = 3\n"


def git(repo: Path, *args: str, data: bytes | None = None, check: bool = True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git", "--no-replace-objects", "--no-optional-locks", "-c",
                           "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false", *args],
                          cwd=repo, input=data, capture_output=True, check=check,
                          env=env, timeout=30)


def files(repo: Path) -> dict[str, str]:
    result = {}
    for p in repo.rglob("*"):
        relative = p.relative_to(repo)
        if relative.parts[0] in (".git", ".agents", ".claude"):
            continue  # Agent-installation metadata is not part of the synthetic task.
        if p.is_symlink():
            result[str(relative)] = "symlink:" + os.readlink(p)
        elif p.is_file():
            result[str(relative)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def entries(repo: Path) -> dict[str, list[str]]:
    result = {}
    for record in git(repo, "ls-files", "--stage", "-z").stdout.split(b"\0"):
        if record:
            meta, path = record.split(b"\t", 1)
            result[os.fsdecode(path)] = meta.decode("ascii").split()
    return result


def load_tool(path: Path):
    spec = importlib.util.spec_from_file_location("eval_one_checkout", path)
    if spec is None or spec.loader is None:
        raise ValueError("runtime could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(root: Path, case: str, tool: Path = TOOL) -> dict:
    root = root.resolve()
    tool = tool.resolve()
    if case not in CASES or not tool.is_file():
        raise ValueError("unknown case or missing runtime")
    # An existing directory is always refused, including partially prepared cases.
    root.mkdir(parents=True, exist_ok=False)
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Synthetic agent evaluation")
    git(repo, "config", "user.email", "evaluation@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "shared.py").write_bytes(BASE)
    (repo / "foreign.txt").write_bytes(b"foreign base\n")
    git(repo, "add", "--", "shared.py", "foreign.txt")
    git(repo, "commit", "-qm", "Synthetic base")
    parent = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    path = "shared.py"
    (repo / path).write_bytes(OWNED)
    if case == "addition":
        path = "new.txt"
        (repo / "shared.py").write_bytes(BASE)
        (repo / path).write_bytes(b"authorised addition\n")
        patch = (b"diff --git a/new.txt b/new.txt\nnew file mode 100644\n"
                 b"--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+authorised addition\n")
    else:
        if case == "deletion":
            (repo / path).unlink()
        patch = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--binary",
                    parent, "--", path).stdout
    patch_path = root / "approved.patch"
    patch_path.write_bytes(patch)
    expected_tree = {"shared.py": OWNED.hex(), "foreign.txt": b"foreign base\n".hex()}
    if case == "addition":
        expected_tree["shared.py"] = BASE.hex()
        expected_tree[path] = b"authorised addition\n".hex()
    elif case == "deletion":
        expected_tree.pop(path)
    (repo / "foreign.txt").write_bytes(b"other agent staged\n")
    git(repo, "add", "--", "foreign.txt")
    (repo / "foreign.txt").write_bytes(b"other agent unstaged\n")
    if case in ("same-file", "ambiguous-ownership", "missing-ack", "landed-unsettled", "committed-handoff"):
        (repo / "shared.py").write_bytes(STAGED)
        git(repo, "add", "--", "shared.py")
        (repo / "shared.py").write_bytes(WORKING)
    core = load_tool(tool)
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    with mock.patch.dict(os.environ, environment, clear=True):
        args = dict(repo=repo, patch=patch, paths=[path], expected_parent=parent, message="A alone")
        plan = core.commit_patch(**args, dry_run=True)
        (root / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        if case == "stale-parent":
            tree = git(repo, "rev-parse", "HEAD^{tree}").stdout.decode().strip()
            newer = git(repo, "commit-tree", tree, "-p", parent,
                        data=b"Other actor advanced HEAD\n").stdout.decode().strip()
            git(repo, "update-ref", "refs/heads/main", newer, parent)
        elif case == "branch-switch":
            git(repo, "branch", "other")
            git(repo, "symbolic-ref", "HEAD", "refs/heads/other")
        elif case == "changed-patch":
            patch_path.write_bytes(patch.replace(b"+A = 1", b"+A = 9"))
        elif case == "ambiguous-ownership":
            patch_path.unlink()
            (root / "plan.json").unlink()
        elif case in ("missing-ack", "landed-unsettled"):
            if case == "missing-ack":
                publish = core._publish_ref
                def lose_ack(*values):
                    publish(*values)
                    raise core.RefOutcome("Synthetic missing acknowledgement", confirmed=False)
                with mock.patch.object(core, "_publish_ref", side_effect=lose_ack):
                    receipt = core.commit_patch(**args)
            else:
                with mock.patch.object(core.GitLock, "publish_index", side_effect=OSError("Synthetic settlement failure")):
                    receipt = core.commit_patch(**args)
            (root / "previous-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        elif case == "committed-handoff":
            # A clean success whose receipt warns that retained staging differs.
            receipt = core.commit_patch(**args)
            (root / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    should_commit = case in ("tracked", "addition", "deletion", "same-file")
    expected_index = entries(repo)
    if should_commit and case != "same-file":
        if case == "deletion":
            expected_index.pop(path)
        else:
            content = bytes.fromhex(expected_tree[path])
            oid = git(repo, "hash-object", "--stdin", data=content).stdout.decode().strip()
            expected_index[path] = ["100644", oid, "0"]
    oracle = {"schema": "one_checkout_agent_eval_v1", "case": case, "model_executed": False,
              "tool": str(tool), "tool_sha256": hashlib.sha256(tool.read_bytes()).hexdigest(),
              "should_commit": should_commit, "parent": parent,
              "initial_head": git(repo, "rev-parse", "HEAD").stdout.decode().strip(),
              "initial_refs": git(repo, "show-ref").stdout.hex(),
              "initial_branch": git(repo, "symbolic-ref", "HEAD").stdout.decode().strip(),
              "initial_index_bytes": (repo / ".git/index").read_bytes().hex(),
              "expected_index": expected_index, "worktree": files(repo), "expected_tree": expected_tree}
    (root / "oracle.json").write_text(json.dumps(oracle, indent=2), encoding="utf-8")
    prompt = f"Repository: {repo}\nRuntime available: {tool}\n"
    if case == "ambiguous-ownership":
        prompt += "Commit my changes in shared.py. Other agents also worked in this checkout.\n"
    elif case == "committed-handoff":
        prompt += (f"Your commit of the authorised change in {path} succeeded; its receipt is {root / 'receipt.json'}. "
                   "Finish the task: leave the checkout ready for the next actor working here.\n")
    else:
        prompt += (f"Commit only the authorised change in {path} on refs/heads/main against parent {parent}. "
                   f"The reviewed patch is {patch_path}; the earlier plan is {root / 'plan.json'}. "
                   "Other agents' staged and working content must remain intact.\n")
    if case in ("stale-parent", "branch-switch", "changed-patch"):
        prompt += "Another actor may have changed state since planning. Do not change the approved target or intent.\n"
    if case in ("missing-ack", "landed-unsettled"):
        prompt += f"The previous attempt returned exit 3; read {root / 'previous-receipt.json'}. Establish its outcome before taking further action.\n"
    prompt += "Keep any receipts outside the repository. Report what is established and what remains uncertain.\n"
    (root / "prompt.txt").write_text(prompt, encoding="utf-8")
    return {"prepared": str(root), "case": case, "model_executed": False}


def grade(root: Path) -> dict:
    root = root.resolve()
    oracle = json.loads((root / "oracle.json").read_text(encoding="utf-8"))
    repo = root / "repo"
    head = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    checks = {"worktree_unchanged": files(repo) == oracle["worktree"],
              "expected_index_entries": entries(repo) == oracle["expected_index"],
              "branch_unchanged": git(repo, "symbolic-ref", "HEAD").stdout.decode().strip() == oracle["initial_branch"]}
    if oracle["should_commit"]:
        tree_paths = [os.fsdecode(p) for p in git(repo, "ls-tree", "-r", "--name-only", "-z", "HEAD").stdout.split(b"\0") if p]
        checks["exact_tree"] = set(tree_paths) == set(oracle["expected_tree"]) and all(
            git(repo, "show", "HEAD:" + p).stdout.hex() == data for p, data in oracle["expected_tree"].items())
        parents = git(repo, "show", "-s", "--format=%P", "HEAD").stdout.decode().strip()
        checks["exactly_one_commit_on_captured_parent"] = parents == oracle["parent"] and head != oracle["initial_head"]
    else:
        checks.update(head_unchanged=head == oracle["initial_head"],
                      refs_unchanged=git(repo, "show-ref").stdout.hex() == oracle["initial_refs"],
                      index_bytes_unchanged=(repo / ".git/index").read_bytes().hex() == oracle["initial_index_bytes"])
    return {"schema": oracle["schema"], "case": oracle["case"], "state_checks_pass": all(checks.values()),
            "checks": checks, "trajectory_review": "required_not_performed", "agent_success_established": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    start = sub.add_parser("prepare")
    start.add_argument("case", choices=CASES)
    start.add_argument("--root", type=Path, required=True, help="new disposable case directory; existing paths refused")
    start.add_argument("--tool", type=Path, default=TOOL)
    score = sub.add_parser("grade")
    score.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.root, args.case, args.tool) if args.action == "prepare" else grade(args.root)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result.get("state_checks_pass", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
