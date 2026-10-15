#!/usr/bin/env python3
"""Show and verify a selected commit in disposable repositories, not your checkout."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

TOOL = Path(__file__).resolve().with_name("one_checkout.py")
BASE = b"A = 0\nB = 0\nC = 0\n"
OWNED = b"A = 1\nB = 0\nC = 0\n"
STAGED = b"A = 0\nB = 2\nC = 0\n"
WORKING = b"A = 1\nB = 2\nC = 3\n"
FOREIGN_BASE = b"D = 0\n"
FOREIGN_STAGED = b"D = 4\n"
FOREIGN_WORKING = b"D = 4\nE = 5\n"
CONTROL_EXPECTATIONS = {
    "git commit": {"selected_commit": False, "shared_index_blob_preserved": True,
                   "foreign_staged_file_excluded": False, "worktree_preserved": True},
    "git commit --only shared.py": {
        "selected_commit": False, "shared_index_blob_preserved": False,
        "foreign_staged_file_excluded": True, "worktree_preserved": True},
    "native private index": {"selected_commit": True, "shared_index_blob_preserved": True,
                             "foreign_staged_file_excluded": True, "worktree_preserved": True},
}


class Fixture:
    """All writes, including HOME and optional Git controls, stay inside this fixture."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.repo = folder / "fixture"
        self.repo.mkdir(parents=True)
        home = folder / "home"
        home.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_") and key not in ("PYTHONPATH", "PYTHONHOME")}
        self.env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "One Checkout demo")
        self.git("config", "user.email", "demo@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        (self.repo / "shared.py").write_bytes(BASE)
        (self.repo / "foreign.txt").write_bytes(FOREIGN_BASE)
        self.git("add", "shared.py", "foreign.txt")
        self.git("commit", "-q", "-m", "Synthetic baseline")
        self.parent = self.git("rev-parse", "HEAD").decode().strip()
        # This is a supplied-patch experiment, not automatic authorship detection.
        (self.repo / "shared.py").write_bytes(OWNED)
        self.patch = folder / "a-only.patch"
        self.patch.write_bytes(self.git("diff", "--no-ext-diff", "--no-textconv",
                                       "--binary", self.parent, "--", "shared.py"))
        (self.repo / "shared.py").write_bytes(STAGED)
        (self.repo / "foreign.txt").write_bytes(FOREIGN_STAGED)
        self.git("add", "shared.py", "foreign.txt")
        (self.repo / "shared.py").write_bytes(WORKING)
        (self.repo / "foreign.txt").write_bytes(FOREIGN_WORKING)
        (self.repo / "untracked.txt").write_bytes(b"leave this note alone\n")

    def execute(self, argv: list[str], *, data: bytes | None = None,
                extra_env: dict[str, str] | None = None,
                check: bool = True) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(argv, cwd=self.repo, env={**self.env, **(extra_env or {})},
                                input=data, capture_output=True, timeout=30)
        if check and result.returncode:
            raise RuntimeError(f"{argv[0]} exited {result.returncode}: "
                               + result.stderr.decode("utf-8", "replace").strip())
        return result

    def git(self, *args: str, data: bytes | None = None,
            extra_env: dict[str, str] | None = None) -> bytes:
        return self.execute(
            ["git", "--no-replace-objects", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
             "-c", "color.ui=false", *args], data=data,
            extra_env={"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                       **(extra_env or {})}).stdout

    def snapshot(self) -> dict[str, str]:
        return {
            "head": self.git("show", "HEAD:shared.py").decode(),
            "index": self.git("show", ":shared.py").decode(),
            "worktree": (self.repo / "shared.py").read_text(),
            "foreign_head": self.git("show", "HEAD:foreign.txt").decode(),
            "foreign_index": self.git("show", ":foreign.txt").decode(),
            "foreign_worktree": (self.repo / "foreign.txt").read_text(),
            "untracked": (self.repo / "untracked.txt").read_text(),
        }

    def metadata(self) -> dict[str, str]:
        return {str(p.relative_to(self.repo / ".git")): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (self.repo / ".git").rglob("*") if p.is_file()}

    def arguments(self) -> list[str]:
        return ["--repo", str(self.repo), "--patch", str(self.patch),
                "--path", "shared.py", "--expected-parent", self.parent]


def compare_git(root: Path) -> list[dict]:
    """Execute common porcelain and a capable plumbing control, each on a fresh fixture."""
    observations = []
    for name in ("git commit", "git commit --only shared.py", "native private index"):
        fixture = Fixture(root / str(len(observations)))
        before = fixture.snapshot()
        if name == "git commit":
            fixture.git("commit", "-q", "-m", "Commit what is staged")
        elif name == "git commit --only shared.py":
            fixture.git("commit", "-q", "--only", "-m", "Commit selected file", "--", "shared.py")
        else:
            # Positive control: Git already provides the essential primitives.
            # Deliberately NOT a concurrent/recovery-safe replacement for the runtime.
            env = {"GIT_INDEX_FILE": str(fixture.folder / "private-index")}
            fixture.git("read-tree", fixture.parent, extra_env=env)
            fixture.git("apply", "--cached", "-", data=fixture.patch.read_bytes(), extra_env=env)
            tree = fixture.git("write-tree", extra_env=env).decode().strip()
            commit = fixture.git("commit-tree", tree, "-p", fixture.parent,
                                 data=b"Native positive control\n").decode().strip()
            fixture.git("update-ref", "refs/heads/main", commit, fixture.parent)
        after = fixture.snapshot()
        observations.append({
            "command": name, "after": after,
            "selected_commit": after["head"] == OWNED.decode()
                and after["foreign_head"] == FOREIGN_BASE.decode(),
            "shared_index_blob_preserved": after["index"] == before["index"],
            "foreign_staged_file_excluded": after["foreign_head"] == FOREIGN_BASE.decode(),
            "worktree_preserved": all(after[k] == before[k]
                                       for k in ("worktree", "foreign_worktree", "untracked")),
        })
    return observations


def assess_git_controls(observations: list[dict]) -> dict:
    """Check each observed control against its declared outcome, including expected false values."""
    mismatches = []
    if len(observations) != len(CONTROL_EXPECTATIONS):
        mismatches.append({"index": None, "command": None, "field": "control_count",
                           "expected": len(CONTROL_EXPECTATIONS), "observed": len(observations)})
    for index, (command, fields) in enumerate(CONTROL_EXPECTATIONS.items()):
        row = observations[index] if index < len(observations) else {}
        observed_command = row.get("command") if isinstance(row, dict) else None
        if observed_command != command:
            mismatches.append({"index": index, "command": command, "field": "command",
                               "expected": command, "observed": observed_command})
        for field, expected in fields.items():
            observed = row.get(field) if isinstance(row, dict) else None
            if observed is not expected:
                mismatches.append({"index": index, "command": command, "field": field,
                                   "expected": expected, "observed": observed})
    return {"status": "passed" if not mismatches else "failed",
            "expected": {command: fields.copy() for command, fields in CONTROL_EXPECTATIONS.items()},
            "mismatches": mismatches}


def follow_on_commit(root: Path, tool: Path) -> dict:
    """Actually commit the retained snapshot in a separate disposable fixture."""
    fixture = Fixture(root)
    receipt = json.loads(fixture.execute([sys.executable, "-B", str(tool), "commit",
                                           *fixture.arguments(), "--message", "Select A first"]).stdout)
    landed = fixture.snapshot()
    fixture.git("commit", "-q", "-m", "Ordinary follow-on commit of retained staging")
    after = fixture.snapshot()
    checks = {
        "selected_commit_contains_A": landed["head"] == OWNED.decode(),
        "retained_index_differs_from_selected_commit": landed["index"] == STAGED.decode()
            and landed["index"] != landed["head"],
        "ordinary_commit_reverses_A": after["head"] == STAGED.decode(),
        "ordinary_commit_follows_selected_commit":
            fixture.git("rev-parse", "HEAD^").decode().strip() == receipt["new_commit"],
        "working_bytes_unchanged": all(after[k] == landed[k]
            for k in ("worktree", "foreign_worktree", "untracked")),
    }
    if not all(checks.values()):
        raise RuntimeError("follow-on control failed: " + json.dumps(checks))
    return {"selected_commit": receipt["new_commit"],
            "ordinary_commit": fixture.git("rev-parse", "HEAD").decode().strip(),
            "before_ordinary_commit": landed, "after_ordinary_commit": after,
            "checks": checks}


def demo(tool: Path = TOOL, *, compare: bool = False) -> dict:
    tool = tool.resolve()
    if not tool.is_file():
        raise ValueError("the bundled runtime is missing")
    with tempfile.TemporaryDirectory(prefix="one-checkout-demo-",
                                     dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as folder:
        root = Path(folder).resolve()
        fixture = Fixture(root / "selected")
        before = fixture.snapshot()
        metadata_before = fixture.metadata()
        planned = json.loads(fixture.execute([sys.executable, "-B", str(tool), "plan",
                                               *fixture.arguments()]).stdout)
        plan_ok = (planned.get("status") == "planned"
                   and fixture.metadata() == metadata_before and fixture.snapshot() == before)
        if not plan_ok:
            raise RuntimeError("planning changed the fixture or did not return planned")
        receipt = json.loads(fixture.execute([sys.executable, "-B", str(tool), "commit",
                                               *fixture.arguments(), "--message", "Commit A alone"]).stdout)
        after = fixture.snapshot()
        cached_diff = fixture.git("diff", "--cached", "--no-ext-diff", "--no-textconv",
                                  "--", "shared.py").decode()
        object_diff = fixture.git("diff", "--no-ext-diff", "--no-textconv",
                                  "HEAD:shared.py", ":shared.py").decode()
        checks = {
            "commit_contains_only_A": after["head"] == OWNED.decode()
                and after["foreign_head"] == FOREIGN_BASE.decode()
                and fixture.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == b"shared.py\n",
            "staged_B_blob_unchanged": after["index"] == before["index"] == STAGED.decode(),
            "working_A_B_C_unchanged": after["worktree"] == before["worktree"] == WORKING.decode(),
            "foreign_staged_and_working_bytes_unchanged": all(after[k] == before[k]
                for k in ("foreign_index", "foreign_worktree", "untracked")),
            "parent_is_captured_parent": fixture.git("rev-parse", "HEAD^").decode().strip() == fixture.parent,
            "receipt_identifies_commit": receipt.get("status") == "committed"
                and receipt.get("new_commit") == fixture.git("rev-parse", "HEAD").decode().strip(),
            "plan_preserves_repository": plan_ok,
            "cached_diff_matches_stored_blobs": "@@" in cached_diff and
                cached_diff[cached_diff.index("@@"):] == object_diff[object_diff.index("@@"):],
            "working_changes_remain_detectable": b"shared.py" in
                fixture.git("diff-files", "--name-only"),
            "preserved_staging_risk_is_reported":
                any(w.get("code") == "preserved_staging_differs"
                    for w in receipt.get("warnings", []))
                and any(row.get("path") == "shared.py"
                        and row.get("candidate_entry", {}).get("oid") ==
                            fixture.git("rev-parse", "HEAD:shared.py").decode().strip()
                        and row.get("preserved_index_entry", {}).get("oid") ==
                            fixture.git("rev-parse", ":shared.py").decode().strip()
                        and row["candidate_entry"] != row["preserved_index_entry"]
                        for row in receipt.get("staging_review", [])),
        }
        # Sequential rejection, not a claim to simulate concurrent processes.
        settled_metadata = fixture.metadata()
        refused = fixture.execute([sys.executable, "-B", str(tool), "commit", *fixture.arguments(),
                                    "--message", "Must not commit twice"], check=False)
        refusal = json.loads(refused.stderr)
        checks["stale_parent_refused_without_writes"] = (
            refused.returncode == 2 and not refused.stdout and refusal.get("code") == "stale_parent"
            and fixture.metadata() == settled_metadata and fixture.snapshot() == after)
        if not all(checks.values()):
            raise RuntimeError("demo verification failed: " + json.dumps(checks))
        result = {"schema": "one_checkout_demo_v1", "status": "demo_passed", "checks": checks,
                  "commit": receipt["new_commit"], "receipt": receipt,
                  "before": before, "after": after, "patch": fixture.patch.read_text(),
                  "cached_diff": cached_diff,
                  "refusal": {"exit_code": refused.returncode, "receipt": refusal},
                  "versions": {"python": sys.version.split()[0],
                               "git": fixture.git("--version").decode().strip()},
                  "runtime_sha256": hashlib.sha256(tool.read_bytes()).hexdigest(),
                  "fixture_removed_on_exit": True}
        if compare:
            result["controls"] = compare_git(root / "controls")
            result["follow_on_control"] = follow_on_commit(root / "follow-on", tool)
            result["control_assessment"] = assess_git_controls(result["controls"])
            if result["control_assessment"]["status"] != "passed":
                result["status"] = "demo_comparison_failed"
    # Only return success after TemporaryDirectory cleanup completes.
    return result


def render_human(result: dict) -> str:
    def compact(text: str) -> str:
        return "; ".join(text.strip().splitlines())
    lines = ["ONE CHECKOUT: commit A; preserve B's staged snapshot and the working files.",
             "Observed in a disposable fixture. No model or network is needed.",
             "A-only patch was captured before B and C arrived; authorship was not inferred.", "",
             f"{'shared.py':<12} {'Before':<27} After"]
    for key, label in (("head", "HEAD"), ("index", "Index"), ("worktree", "Working file")):
        lines.append(f"{label:<12} {compact(result['before'][key]):<27} {compact(result['after'][key])}")
    lines += ["", "WARNING: REVIEW THE STAGING HANDOFF BEFORE ANOTHER COMMIT",
              "The retained index omits A; an ordinary commit of that snapshot can undo A.",
              "Inspect the staged diff and review the next owned patch; do not reset or restage others' work.",
              "Preserving a snapshot does not rebase it.",
              "For B's owner, review a B-only patch against the new HEAD that preserves A: "
              "https://github.com/wcook04/one-checkout/blob/548c65d3332f74d0f2f77fea06796d6dffe7ea85/docs/staging_handoff.md#deliberate-next-action",
              "", "Actual git diff --cached -- shared.py (against the new HEAD):",
              result["cached_diff"].rstrip(),
              "", "Sequential stale-parent check: exit " + str(result["refusal"]["exit_code"])
              + ", " + result["refusal"]["receipt"]["code"] + "; state unchanged."]
    for name, passed in result["checks"].items():
        lines.append(("PASS " if passed else "FAIL ") + name)
    if "controls" in result:
        lines += ["", "Independent Git controls (same starting fixture; not a speed benchmark):",
                  f"{'Command':<30} {'Committed shared.py':<27} Other staged file excluded"]
        for row in result["controls"]:
            if not isinstance(row, dict):
                lines.append("Invalid control observation: " + repr(row))
                continue
            after = row.get("after")
            head = after.get("head", "<missing>") if isinstance(after, dict) else "<missing>"
            command = str(row.get("command", "<missing>"))
            lines.append(f"{command:<30} {compact(str(head)):<27} "
                         + str(row.get("foreign_staged_file_excluded")).lower())
        assessment = result["control_assessment"]
        lines.append("Git control comparison: " + assessment["status"])
        if assessment["status"] == "passed":
            lines += ["All three Git controls matched their declared outcomes.",
                      "Native private-index Git also selects A and preserves B. Git supplies the mechanism.",
                      "That positive control does not implement the runtime's locking and recovery contract."]
        else:
            lines.append("GIT CONTROL COMPARISON FAILED; primary and follow-on checks are separate.")
            for mismatch in assessment["mismatches"]:
                lines.append(f"Mismatch {mismatch['command'] or 'control list'} / {mismatch['field']}: "
                             f"expected {mismatch['expected']!r}, observed {mismatch['observed']!r}")
            lines.append("The native control does not implement the runtime's locking and recovery contract.")
        follow_on = result["follow_on_control"]
        lines += ["", "Observed next ordinary commit (separate disposable fixture):",
                  "Before: " + compact(follow_on["before_ordinary_commit"]["head"]),
                  "After:  " + compact(follow_on["after_ordinary_commit"]["head"]),
                  "The ordinary commit actually reverted A while recording B; working bytes stayed unchanged.",
                  "This demonstrates the handoff hazard. It does not reconcile another writer's staging."]
    commit_label = "Verified commit: " if result["status"] == "demo_passed" else "Primary verified commit: "
    lines += ["", commit_label + result["commit"],
              "Fixtures removed. Hooks were disabled in the fixture. No agent session was tested."]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", type=Path, default=TOOL, help="runtime to exercise, including an installed copy")
    parser.add_argument("--human", action="store_true", help="show observed before/after states; default is JSON")
    parser.add_argument("--compare-git", action="store_true", help="also run porcelain and private-index Git controls")
    args = parser.parse_args()
    try:
        result = demo(args.tool, compare=args.compare_git)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "demo_failed", "error": str(exc)}), file=sys.stderr)
        return 2
    print(render_human(result) if args.human else json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "demo_passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
