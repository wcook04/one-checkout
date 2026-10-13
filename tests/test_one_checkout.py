"""Real Git fixtures for private patch landing; no live checkout is mutated."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "skills/one-checkout/scripts/one_checkout.py"
SPEC = importlib.util.spec_from_file_location("one_checkout", SCRIPT)
assert SPEC and SPEC.loader
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)


class OneCheckoutTests(unittest.TestCase):
    def setUp(self) -> None:
        if not shutil.which("git"):
            self.skipTest("Git is required")
        self.env_patch = mock.patch.dict(os.environ, {
            k: v for k, v in os.environ.items() if not k.startswith("GIT_")
        }, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        base = os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")
        self.temp = tempfile.TemporaryDirectory(prefix="one-checkout-tests-", dir=base)
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgSign", "false")
        self.git("config", "core.hooksPath", str(Path(self.temp.name) / "no-hooks"))
        self.base = "".join(f"line {i:02d}\n" for i in range(25))
        (self.repo / "shared.py").write_text(self.base)
        (self.repo / "foreign.txt").write_text("foreign base\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.parent = self.git("rev-parse", "HEAD").stdout.decode().strip()

    def git(self, *args: str, data: bytes | None = None, check: bool = True,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(["git", *args], cwd=self.repo, input=data,
                              env={**os.environ, **(env or {})}, capture_output=True, check=check)

    def patch_for(self, path: str, content: bytes) -> bytes:
        target = self.repo / path
        previous = target.read_bytes() if target.exists() else None
        target.write_bytes(content)
        try:
            return self.git("diff", "--binary", "--no-ext-diff", "--no-textconv",
                            self.parent, "--", path).stdout
        finally:
            if previous is None:
                target.unlink()
            else:
                target.write_bytes(previous)

    def land(self, patch: bytes, paths: list[str] | None = None, **kwargs: object) -> dict:
        return core.commit_patch(repo=self.repo, patch=patch, paths=paths or ["shared.py"],
                                 expected_parent=self.parent, message="selected patch", **kwargs)

    def blob(self, treeish: str, path: str) -> bytes:
        return self.git("show", f"{treeish}:{path}").stdout

    def metadata(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.repo / ".git")): p.read_bytes()
                for p in (self.repo / ".git").rglob("*") if p.is_file()}

    def test_same_file_foreign_stage_and_unowned_hunks_survive(self) -> None:
        staged_b = self.base.replace("line 12\n", "B staged\n")
        (self.repo / "shared.py").write_text(staged_b)
        (self.repo / "foreign.txt").write_text("foreign staged\n")
        self.git("add", "shared.py", "foreign.txt")
        staged = {p: self.blob("", p) for p in ("shared.py", "foreign.txt")}
        worktree = staged_b.replace("line 01\n", "A selected\n").replace("line 23\n", "C unowned\n")
        (self.repo / "shared.py").write_text(worktree)
        (self.repo / "foreign.txt").write_text("foreign unstaged\n")
        selected = self.base.replace("line 01\n", "A selected\n").encode()
        patch = self.patch_for("shared.py", selected)
        before_files = {p: (self.repo / p).read_bytes() for p in staged}
        receipt = self.land(patch)
        self.assertEqual(receipt["status"], "committed")
        self.assertEqual(receipt["preserved_staged_paths"], ["shared.py"])
        self.assertEqual(self.blob("HEAD", "shared.py"), selected)
        for path in staged:
            self.assertEqual(self.blob("", path), staged[path])
            self.assertEqual((self.repo / path).read_bytes(), before_files[path])
        self.assertEqual(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout,
                         b"shared.py\n")

    def test_clean_owned_index_syncs_without_foreign_stage_contamination(self) -> None:
        (self.repo / "foreign.txt").write_text("foreign staged\n")
        self.git("add", "foreign.txt")
        foreign_blob = self.blob("", "foreign.txt")
        selected = self.base.replace("line 01\n", "selected\n").encode()
        (self.repo / "shared.py").write_bytes(selected + b"unowned tail\n")
        receipt = self.land(self.patch_for("shared.py", selected))
        self.assertEqual(receipt["synchronized_index_paths"], ["shared.py"])
        self.assertEqual(self.blob("", "shared.py"), selected)
        self.assertEqual(self.blob("", "foreign.txt"), foreign_blob)
        self.assertEqual(self.git("diff", "--cached", "--name-only", "-z").stdout, b"foreign.txt\0")
        self.assertEqual((self.repo / "shared.py").read_bytes(), selected + b"unowned tail\n")

    def test_plan_does_not_write_any_repository_metadata(self) -> None:
        patch = self.patch_for("shared.py", self.base.replace("line 01\n", "selected\n").encode())
        before = self.metadata()
        receipt = self.land(patch, dry_run=True)
        self.assertEqual(receipt["status"], "planned")
        self.assertIsNone(receipt["new_commit"])
        self.assertEqual(before, self.metadata())

    def test_stale_parent_refuses_before_object_or_index_writes(self) -> None:
        patch = self.patch_for("shared.py", b"replacement\n")
        (self.repo / "foreign.txt").write_text("new baseline\n")
        self.git("add", "foreign.txt")
        self.git("commit", "-qm", "another commit")
        before = self.metadata()
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch)
        self.assertEqual(caught.exception.code, "stale_parent")
        self.assertEqual(before, self.metadata())

    def test_scope_must_match_exact_changed_paths(self) -> None:
        patch = self.patch_for("shared.py", b"replacement\n")
        before = (self.repo / ".git/index").read_bytes()
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch, ["foreign.txt"])
        self.assertEqual(caught.exception.code, "scope_mismatch")
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.decode().strip(), self.parent)
        self.assertEqual((self.repo / ".git/index").read_bytes(), before)

    def test_replace_refs_cannot_hide_undeclared_parent_tree_changes(self) -> None:
        (self.repo / "foreign.txt").write_text("replacement tree foreign change\n")
        self.git("add", "foreign.txt")
        self.git("commit", "-qm", "replacement commit")
        replacement = self.git("rev-parse", "HEAD").stdout.decode().strip()
        self.git("update-ref", "refs/heads/main", self.parent, replacement)
        self.git("read-tree", self.parent)
        (self.repo / "foreign.txt").write_text("foreign base\n")
        self.git("replace", self.parent, replacement)
        patch = self.patch_for("shared.py", b"selected replacement\n")
        receipt = self.land(patch)
        self.assertEqual(receipt["status"], "committed")
        self.assertEqual(self.git("--no-replace-objects", "diff-tree", "--no-commit-id",
                                  "--name-only", "--no-renames", "-r", "-z",
                                  self.parent, receipt["new_commit"]).stdout, b"shared.py\0")
        self.assertEqual(self.git("--no-replace-objects", "show", "HEAD:foreign.txt").stdout,
                         b"foreign base\n")

    def test_git_path_quoting_roundtrips_exact_names(self) -> None:
        for name in ("tab\tname.txt", "line\nname.txt", "café.txt", 'quote"name.txt', "space name.txt", "--flag.txt"):
            with self.subTest(name=name):
                (self.repo / name).write_text("before\n")
                self.git("add", "--", name)
                self.git("commit", "-qm", "filename baseline")
                self.parent = self.git("rev-parse", "HEAD").stdout.decode().strip()
                patch = self.patch_for(name, b"after\n")
                receipt = self.land(patch, [name])
                self.assertEqual(receipt["changed_paths"], [name])
                self.assertEqual(self.blob("HEAD", name), b"after\n")
                self.assertEqual((self.repo / name).read_bytes(), b"before\n")

    def test_existing_git_locks_are_retained(self) -> None:
        patch = self.patch_for("shared.py", b"replacement\n")
        for lock_name in ("index.lock", "HEAD.lock"):
            lock = self.repo / ".git" / lock_name
            lock.write_bytes(b"another writer")
            try:
                with self.assertRaises(core.Refusal) as caught:
                    self.land(patch)
                self.assertEqual(caught.exception.code, "git_locked")
                self.assertEqual(lock.read_bytes(), b"another writer")
                self.assertEqual(self.git("rev-parse", "HEAD").stdout.decode().strip(), self.parent)
            finally:
                lock.unlink()
            self.assertFalse((self.repo / ".git/index.lock").exists())

    def test_branch_cas_race_does_not_rewind_competing_commit(self) -> None:
        patch = self.patch_for("shared.py", b"replacement\n")
        original = (self.repo / ".git/index").read_bytes()
        tree = self.git("rev-parse", "HEAD^{tree}").stdout.decode().strip()
        other = self.git("commit-tree", tree, "-p", self.parent, data=b"competing commit").stdout.decode().strip()
        publish = core._publish_ref

        def raced(git, branch, new_commit, parent):
            self.git("update-ref", branch, other, parent)
            publish(git, branch, new_commit, parent)

        with mock.patch.object(core, "_publish_ref", side_effect=raced):
            with self.assertRaises(core.Refusal) as caught:
                self.land(patch)
        self.assertEqual(caught.exception.code, "ref_race")
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.decode().strip(), other)
        self.assertEqual((self.repo / ".git/index").read_bytes(), original)

    def test_head_lock_prevents_cooperating_branch_switch(self) -> None:
        self.git("branch", "other")
        patch = self.patch_for("shared.py", b"replacement\n")
        verify = core._verify_prepared_head
        switch_attempts = []

        def intercepted(git, branch, parent):
            switch_attempts.append(self.git("symbolic-ref", "HEAD", "refs/heads/other", check=False).returncode)
            return verify(git, branch, parent)

        with mock.patch.object(core, "_verify_prepared_head", new=intercepted):
            result = self.land(patch)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(len(switch_attempts), 1)
        self.assertNotEqual(switch_attempts[0], 0)
        self.assertEqual(self.git("symbolic-ref", "HEAD").stdout, b"refs/heads/main\n")

    def test_same_oid_branch_switch_before_prepare_refuses(self) -> None:
        self.git("branch", "other")
        patch = self.patch_for("shared.py", b"replacement\n")
        command = core.Git.command

        def switch_before_prepare(git, args):
            result = command(git, args)
            if args[0] == "update-ref":
                self.git("symbolic-ref", "HEAD", "refs/heads/other")
            return result

        original = (self.repo / ".git/index").read_bytes()
        with mock.patch.object(core.Git, "command", new=switch_before_prepare):
            with self.assertRaises(core.Refusal) as caught:
                self.land(patch)
        self.assertEqual(caught.exception.code, "head_race")
        for branch in ("main", "other"):
            self.assertEqual(self.git("rev-parse", branch).stdout.decode().strip(), self.parent)
        self.assertEqual((self.repo / ".git/index").read_bytes(), original)

    def protocol_failure(self, failure: str) -> dict:
        """Consume a real Git success, then inject a lost ack or pipe cleanup error."""
        popen = subprocess.Popen

        class Output:
            def __init__(self, stream):
                self.stream = stream
                self.count = 0

            def readline(self):
                result = self.stream.readline()
                self.count += 1
                return b"" if failure == "ack" and self.count == 3 else result

            def close(self):
                self.stream.close()
                if failure == "cleanup":
                    raise OSError("fixture protocol cleanup failure")

        def intercepted(args, **kwargs):
            process = popen(args, **kwargs)
            if "update-ref" in args:
                process.stdout = Output(process.stdout)
            return process

        patch = self.patch_for("shared.py", b"replacement\n")
        with mock.patch.object(core.subprocess, "Popen", side_effect=intercepted):
            return self.land(patch)

    def test_lost_commit_ack_retains_candidate_and_uncertainty(self) -> None:
        original = (self.repo / ".git/index").read_bytes()
        result = self.protocol_failure("ack")
        self.assertEqual(result["status"], "landing_uncertain")
        self.assertFalse(result["ref_update_confirmed"])
        self.assertEqual(result["new_commit"], self.git("rev-parse", "HEAD").stdout.decode().strip())
        self.assertEqual((self.repo / ".git/index").read_bytes(), original)
        self.assertIn("before any retry", result["recovery"])

    def test_confirmed_commit_protocol_cleanup_retains_landed_receipt(self) -> None:
        original = (self.repo / ".git/index").read_bytes()
        result = self.protocol_failure("cleanup")
        self.assertEqual(result["status"], "landed_unsettled")
        self.assertTrue(result["ref_update_confirmed"])
        self.assertEqual(result["new_commit"], self.git("rev-parse", "HEAD").stdout.decode().strip())
        self.assertEqual((self.repo / ".git/index").read_bytes(), original)

    def test_replaced_lock_is_retained_and_ref_is_not_advanced(self) -> None:
        patch = self.patch_for("shared.py", b"replacement\n")
        run = core.Git.run
        lock = self.repo / ".git/index.lock"

        def replace_lock(git, args, **kwargs):
            result = run(git, args, **kwargs)
            if args[0] == "commit-tree":
                replacement = self.repo / ".git/replacement-lock"
                replacement.write_bytes(b"replacement owner")
                os.replace(replacement, lock)
            return result

        with mock.patch.object(core.Git, "run", new=replace_lock):
            with self.assertRaises(core.Refusal) as caught:
                self.land(patch)
        self.assertEqual(caught.exception.code, "lock_changed")
        self.assertEqual(lock.read_bytes(), b"replacement owner")
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.decode().strip(), self.parent)

    def test_post_cas_index_failure_reports_landed_commit(self) -> None:
        patch = self.patch_for("shared.py", b"replacement\n")
        original = (self.repo / ".git/index").read_bytes()
        with mock.patch.object(core.GitLock, "publish_index", side_effect=OSError("fixture failure")):
            result = self.land(patch)
        self.assertEqual(result["status"], "landed_unsettled")
        self.assertEqual(result["new_commit"], self.git("rev-parse", "HEAD").stdout.decode().strip())
        self.assertEqual((self.repo / ".git/index").read_bytes(), original)
        self.assertIn("already exists", result["recovery"])

    def test_post_cas_ref_advance_leaves_original_index_and_landed_receipt(self) -> None:
        patch = self.patch_for("shared.py", b"replacement\n")
        original = (self.repo / ".git/index").read_bytes()
        publish = core._publish_ref
        advanced = []

        def advance_after_landing(git, branch, new_commit, parent):
            publish(git, branch, new_commit, parent)
            tree = self.git("rev-parse", f"{new_commit}^{{tree}}").stdout.decode().strip()
            other = self.git("commit-tree", tree, "-p", new_commit, data=b"next commit").stdout.decode().strip()
            self.git("update-ref", branch, other, new_commit)
            advanced.append(other)

        with mock.patch.object(core, "_publish_ref", side_effect=advance_after_landing):
            result = self.land(patch)
        self.assertEqual(result["status"], "landed_unsettled")
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.decode().strip(), advanced[0])
        self.assertNotEqual(result["new_commit"], advanced[0])
        self.assertEqual((self.repo / ".git/index").read_bytes(), original)

    def test_post_land_lock_cleanup_failure_retains_landed_receipt(self) -> None:
        patch = self.patch_for("shared.py", b"replacement\n")
        unlink = Path.unlink

        def refused_unlink(path, *args, **kwargs):
            if path == self.repo / ".git/index.lock":
                raise PermissionError("fixture cleanup refused")
            return unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", new=refused_unlink), \
                mock.patch.object(core.GitLock, "publish_index", side_effect=OSError("fixture index publication failed")):
            result = self.land(patch)
        self.assertEqual(result["status"], "landed_unsettled")
        self.assertEqual(result["new_commit"], self.git("rev-parse", "HEAD").stdout.decode().strip())
        self.assertIn("cleanup_error", result)
        self.assertTrue((self.repo / ".git/index.lock").exists())

    def test_post_land_scratch_cleanup_failure_retains_landed_receipt(self) -> None:
        patch = self.patch_for("shared.py", b"replacement\n")
        cleanup = tempfile.TemporaryDirectory.cleanup

        def refused_cleanup(directory):
            cleanup(directory)
            raise OSError("fixture scratch cleanup failed")

        with mock.patch.object(tempfile.TemporaryDirectory, "cleanup", new=refused_cleanup):
            result = self.land(patch)
        self.assertEqual(result["status"], "landed_unsettled")
        self.assertEqual(result["new_commit"], self.git("rev-parse", "HEAD").stdout.decode().strip())
        self.assertIn("cleanup_error", result)

    @unittest.skipIf(os.name == "nt", "POSIX executable-hook fixture")
    def test_configured_reference_transaction_hooks_are_explicitly_disabled(self) -> None:
        hookdir = Path(self.temp.name) / "configured-hooks"
        hookdir.mkdir()
        hook = hookdir / "reference-transaction"
        hook.write_text("#!/bin/sh\nprintf hook-ran > hook-marker\nexit 1\n")
        hook.chmod(0o755)
        self.git("config", "core.hooksPath", str(hookdir))
        patch = self.patch_for("shared.py", b"replacement\n")
        result = self.land(patch)
        self.assertEqual(result["status"], "committed")
        self.assertFalse(result["hooks_run"])
        self.assertFalse((self.repo / "hook-marker").exists())

    def test_binary_patch_and_tracked_deletion(self) -> None:
        binary = self.repo / "image.bin"
        binary.write_bytes(b"\0before\xff")
        self.git("add", "image.bin")
        self.git("commit", "-qm", "binary baseline")
        self.parent = self.git("rev-parse", "HEAD").stdout.decode().strip()
        result = self.land(self.patch_for("image.bin", b"\0after\xfe"), ["image.bin"])
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.blob("HEAD", "image.bin"), b"\0after\xfe")
        self.parent = self.git("rev-parse", "HEAD").stdout.decode().strip()
        data = (self.repo / "foreign.txt").read_bytes()
        (self.repo / "foreign.txt").unlink()
        patch = self.git("diff", "--binary", self.parent, "--", "foreign.txt").stdout
        (self.repo / "foreign.txt").write_bytes(data)
        result = self.land(patch, ["foreign.txt"])
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.git("ls-files", "-z", "--", "foreign.txt").stdout, b"")
        self.assertEqual((self.repo / "foreign.txt").read_bytes(), data)

    def test_new_file_patch_preserves_different_untracked_worktree_bytes(self) -> None:
        (self.repo / "new.txt").write_bytes(b"unowned working bytes\n")
        patch = (b"diff --git a/new.txt b/new.txt\nnew file mode 100644\n"
                 b"--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+selected new file\n")
        result = self.land(patch, ["new.txt"])
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.blob("HEAD", "new.txt"), b"selected new file\n")
        self.assertEqual(self.blob("", "new.txt"), b"selected new file\n")
        self.assertEqual((self.repo / "new.txt").read_bytes(), b"unowned working bytes\n")

    def test_staged_owned_deletion_is_preserved(self) -> None:
        patch = self.patch_for("shared.py", b"selected new content\n")
        self.git("rm", "--cached", "shared.py")
        result = self.land(patch)
        self.assertEqual(result["preserved_staged_paths"], ["shared.py"])
        self.assertEqual(self.blob("HEAD", "shared.py"), b"selected new content\n")
        self.assertEqual(self.git("ls-files", "-z", "--", "shared.py").stdout, b"")
        self.assertEqual((self.repo / "shared.py").read_text(), self.base)

    def test_unmerged_and_sparse_indexes_refuse(self) -> None:
        patch = self.patch_for("shared.py", b"selected new content\n")
        original = (self.repo / ".git/index").read_bytes()
        self.git("config", "core.sparseCheckout", "true")
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch)
        self.assertEqual(caught.exception.code, "sparse_index")
        self.assertEqual((self.repo / ".git/index").read_bytes(), original)
        self.git("config", "core.sparseCheckout", "false")
        oid = self.git("rev-parse", ":shared.py").stdout.strip()
        records = b"0 " + b"0" * len(oid) + b"\tshared.py\n"
        records += b"".join(b"100644 " + oid + b" " + str(stage).encode() + b"\tshared.py\n"
                            for stage in (1, 2, 3))
        self.git("update-index", "--index-info", data=records)
        conflicted = (self.repo / ".git/index").read_bytes()
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch)
        self.assertEqual(caught.exception.code, "unmerged_index")
        self.assertEqual((self.repo / ".git/index").read_bytes(), conflicted)

    def test_invalid_declared_paths_and_environment_refuse_without_writes(self) -> None:
        patch = self.patch_for("shared.py", b"replacement\n")
        before = self.metadata()
        for path in ("../escape", "/absolute", ".", "dir/../file", ".git/config", "dir/", "C:/other"):
            with self.subTest(path=path), self.assertRaises(core.Refusal):
                self.land(patch, [path])
        for variable in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_CONFIG_COUNT", "GIT_EXEC_PATH"):
            with mock.patch.dict(os.environ, {variable: "unexpected"}):
                with self.assertRaises(core.Refusal) as caught:
                    self.land(patch)
                self.assertEqual(caught.exception.code, "unsafe_environment")
        self.assertEqual(before, self.metadata())

    def test_detached_and_split_index_refuse(self) -> None:
        patch = self.patch_for("shared.py", b"replacement\n")
        self.git("checkout", "--detach", "-q")
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch)
        self.assertEqual(caught.exception.code, "named_branch_required")
        self.git("checkout", "main", "-q")
        self.git("update-index", "--split-index")
        with self.assertRaises(core.Refusal) as caught:
            self.land(patch)
        self.assertEqual(caught.exception.code, "split_index")

    def test_cli_json_plan_commit_and_refusal(self) -> None:
        patch_path = Path(self.temp.name) / "change.patch"
        patch_path.write_bytes(self.patch_for("shared.py", b"replacement\n"))
        common = ["--repo", str(self.repo), "--patch", str(patch_path), "--path", "shared.py",
                  "--expected-parent", self.parent]
        plan = subprocess.run([sys.executable, str(SCRIPT), "plan", *common], capture_output=True, check=True)
        self.assertEqual(json.loads(plan.stdout)["status"], "planned")
        committed = subprocess.run([sys.executable, str(SCRIPT), "commit", *common, "--message", "CLI patch"],
                                   capture_output=True, check=True)
        self.assertEqual(json.loads(committed.stdout)["status"], "committed")
        refused = subprocess.run([sys.executable, str(SCRIPT), "commit", *common, "--message", "stale"], capture_output=True)
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(json.loads(refused.stderr)["code"], "stale_parent")
        self.assertEqual(refused.stdout, b"")

    def test_cli_argument_refusals_are_json(self) -> None:
        result = subprocess.run([sys.executable, str(SCRIPT), "commit", "--unknown"], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["code"], "arguments")
        self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
