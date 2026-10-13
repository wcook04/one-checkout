#!/usr/bin/env python3
"""Verify a same-file scoped commit in a disposable Git repository."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


TOOL = Path(__file__).resolve().parents[1] / "skills/one-checkout/scripts/one_checkout.py"
BASE = b"A = 0\nB = 0\nC = 0\n"
OWNED = b"A = 1\nB = 0\nC = 0\n"
STAGED = b"A = 0\nB = 2\nC = 0\n"
WORKING = b"A = 1\nB = 2\nC = 3\n"


def run(argv: list[str], root: Path) -> bytes:
    # An inherited GIT_DIR or GIT_WORK_TREE must not redirect the fixture.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    if argv[0] == "git":
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    result = subprocess.run(argv, cwd=root, env=env, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"{argv[0]} exited {result.returncode}: "
                           + result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def demo(tool: Path = TOOL) -> dict:
    tool = tool.resolve()
    if not tool.is_file():
        raise ValueError("the bundled runtime is missing")
    with tempfile.TemporaryDirectory(prefix="one-checkout-demo-",
                                     dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as folder:
        root = Path(folder).resolve()
        repo = root / "fixture"
        repo.mkdir()
        def git(*args: str) -> bytes:
            return run(["git", "-c", f"core.hooksPath={os.devnull}",
                        "-c", "core.fsmonitor=false", *args], repo)

        git("init", "-q", "-b", "main")
        git("config", "user.name", "One Checkout demo")
        git("config", "user.email", "demo@example.invalid")
        git("config", "commit.gpgsign", "false")
        git("config", "core.autocrlf", "false")
        shared = repo / "shared.py"
        shared.write_bytes(BASE)
        git("add", "shared.py")
        git("commit", "-q", "-m", "Synthetic baseline")
        parent = git("rev-parse", "HEAD").decode().strip()

        # Capture A alone before B and C enter the shared checkout.
        shared.write_bytes(OWNED)
        patch = root / "a-only.patch"
        patch.write_bytes(git("diff", "--no-ext-diff", "--no-textconv", "--binary",
                              parent, "--", "shared.py"))
        shared.write_bytes(STAGED)
        git("add", "shared.py")
        shared.write_bytes(WORKING)
        staged_before = git("show", ":shared.py")
        working_before = shared.read_bytes()

        arguments = ["--repo", str(repo), "--patch", str(patch),
                     "--path", "shared.py", "--expected-parent", parent]
        planned = json.loads(run([sys.executable, str(tool), "plan", *arguments], repo))
        if planned.get("status") != "planned" or git("rev-parse", "HEAD").decode().strip() != parent:
            raise RuntimeError("planning did not preserve the branch")
        if git("show", ":shared.py") != staged_before or shared.read_bytes() != working_before:
            raise RuntimeError("planning changed the shared index or worktree")
        receipt = json.loads(run([sys.executable, str(tool), "commit", *arguments,
                                  "--message", "Commit A alone"], repo))
        checks = {
            "commit_contains_only_A": git("show", "HEAD:shared.py") == OWNED,
            "staged_B_blob_unchanged": git("show", ":shared.py") == staged_before == STAGED,
            "working_A_B_C_unchanged": shared.read_bytes() == working_before == WORKING,
            "parent_is_captured_parent": git("rev-parse", "HEAD^").decode().strip() == parent,
            "receipt_identifies_commit": receipt.get("status") == "committed"
                and receipt.get("new_commit") == git("rev-parse", "HEAD").decode().strip(),
        }
        if not all(checks.values()):
            raise RuntimeError("demo verification failed: " + json.dumps(checks))
        return {"status": "demo_passed", "checks": checks,
                "commit": receipt["new_commit"], "fixture_removed_on_exit": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", type=Path, default=TOOL,
                        help="runtime to exercise, e.g. an installed skill copy")
    args = parser.parse_args()
    try:
        result = demo(args.tool)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "demo_failed", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
