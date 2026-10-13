# One Checkout

Commit your patch from a shared Git checkout without pulling someone else's
staged changes into it. One Checkout uses an explicit patch, exact file paths
and an expected parent. It leaves working files alone, including other
changes in the same file.

Python 3.10+ and Git on macOS or Linux, no pip dependencies. From this source directory:

```sh
python3 scripts/demo.py
python3 skills/one-checkout/scripts/one_checkout.py --help
python3 scripts/install.py --agent both
```

Try the demo safely first: it creates a temporary repository, commits A
while preserving staged B and unstaged C in the same file, checks the result,
and removes the fixture. It does not mutate your working repository.

The installer puts the same self-contained skill in `~/.agents/skills` for
Codex and `~/.claude/skills` for Claude Code. Restart the agent session, then
ask it to use `one-checkout`. You can also run the Python script directly.
This repository is prepared locally; no public release or marketplace is
configured.

## Commit a patch

From the repository you are working on, create and inspect your patch against
a recorded parent. Save the patch outside that checkout. If the working file
also contains another person's changes, edit the patch to include only your
own hunks. The example below assumes the selected file contains only the
change you intend to commit.

```sh
expected_parent=$(git rev-parse HEAD)
git diff --binary "$expected_parent" -- src/example.py > /tmp/owned.patch
# Read /tmp/owned.patch before proceeding.

python3 /path/to/one-checkout/skills/one-checkout/scripts/one_checkout.py plan \
  --repo . --patch /tmp/owned.patch --path src/example.py \
  --expected-parent "$expected_parent"

python3 /path/to/one-checkout/skills/one-checkout/scripts/one_checkout.py commit \
  --repo . --patch /tmp/owned.patch --path src/example.py \
  --expected-parent "$expected_parent" --message "Describe the change"
```

Repeat `--path` for each exact file. A directory or glob is refused. The
declared paths must match the paths actually changed by the patch. Additions
must be present in the patch; ordinary `git diff` omits untracked files.
Use `--message-file` for a longer message. `commit --dry-run` is an alias for
`plan`.

The command writes JSON receipts to stdout. Refusals write JSON errors to
stderr and exit with status 2. Exit 3 returns a recovery receipt on stdout:

- `landing_uncertain`, `ref_update_confirmed: false`: the commit request was
  sent but success was not acknowledged. `new_commit` identifies the
  candidate; it does not prove that the branch advanced. The original shared
  index is retained. Inspect the branch and reflog before retrying.
- `landed_unsettled`: the commit is confirmed, but index settlement or cleanup
  failed. A confirmed ref transaction with protocol cleanup failure includes
  `ref_update_confirmed: true`. Inspect the commit, branch and staged state.

Planning does not update the branch or shared index.
Committing builds the candidate using a private index, holds the shared index
lock and prepares Git's native ref transaction. It checks the named branch
and expected parent before committing that transaction; a changed branch or
parent is refused.
Inspect the new parent and regenerate or rebase the patch before a deliberate
retry.

## Three changes in one file

Suppose a tracked file has your change **A**, another agent's staged change
**B**, and its unstaged change **C**. Supply a patch containing A alone.
The commit contains A. B's staged blob remains in the shared index, and the
working file still contains A, B and C.

Because the retained index blob may lack A, `git diff --cached` against the
new HEAD can display B together with an apparent reversal of A. That is the
preserved staged snapshot, not permission to reset it. The other agent must
inspect its next patch. Entries that still match the old parent can be
synchronized to the new commit without altering a foreign staged snapshot.

## Scope and limits

This supports trusted collaborators, a named branch and an ordinary full Git
index. Split, sparse and unmerged indexes are refused. It does not provide
path leases or isolated working files. Use worktrees when collaborators need
different working copies or may edit the same bytes at once.

Git hooks are disabled for each invocation, including commit and
reference-transaction hooks; repository configuration is not changed.
Required tests and policy checks must run separately. Configured
`commit.gpgSign` is honored and may require external signing tooling. The tool
does not push or establish that the code works. A successful plan is a
snapshot; commit rechecks the parent and shared state.

The branch update and shared-index settlement cannot be one Git transaction.
If the branch update succeeds and settlement or cleanup fails, inspect the
receipt, current HEAD, commit tree, staged state and any reported remaining
locks or scratch files. If the ref outcome is uncertain, inspect the branch
and reflog against the candidate `new_commit` to establish whether it landed.
Do not retry the commit or undo other work automatically. A killed process
may leave an uncertain outcome without returning a receipt.

## Installation

```sh
# Inspect destinations without changing them.
python3 scripts/install.py --agent both --dry-run

# Install for one repository instead of your user account.
python3 scripts/install.py --agent both --scope project --project /path/to/repo

# Preserve a different existing copy before installing this version.
python3 scripts/install.py --agent both --backup-existing
```

An identical installed bundle is left alone. A different existing directory
is refused unless `--backup-existing` is explicit. Backups stay outside the
agents' `skills/` directories so they cannot load as duplicate skills. The
installer refuses symlink destinations and reports exact destinations and
backup paths. It changes no agent configuration or hooks.

Remove an installed skill by deleting the `one-checkout` directory printed
by the installer, after checking for local edits. Restore a reported backup
by moving it back into that location. Do not delete the enclosing skills
directory.

The package also carries a root portable `plugin.json` for Codex and a
`.claude-plugin/plugin.json` for Claude Code. To try the local Claude plugin:

```sh
claude plugin validate --strict .
claude --plugin-dir /absolute/path/to/one-checkout
```

Codex plugin installation uses a local marketplace and the desktop app; the
bare-skill installer above avoids that additional setup. See the official
[OpenAI plugin instructions](https://developers.openai.com/plugins/build/plugins),
[OpenAI skill locations](https://learn.chatgpt.com/docs/build-skills) and
[Claude Code skill locations](https://code.claude.com/docs/en/skills).
Providing a manifest does not install or publish the plugin.

## Check the package

```sh
python3 -m unittest discover -s tests -v
python3 scripts/demo.py
python3 scripts/check_layout.py --root .
```

Tests use temporary Git repositories and the standard library. Set `TMPDIR`
to an existing directory with enough space to route runtime scratch there.
`ONE_CHECKOUT_TEST_TMPDIR` overrides the demo and test fixture location.
Hosted CI is configured for Python
3.10 and 3.13 on Ubuntu and Python 3.13 on macOS; a workflow file is not
evidence of a hosted run. Windows is not supported in this version.

## Source map

| Path | Purpose |
|---|---|
| [skills/one-checkout/](skills/one-checkout/) | Complete installable skill, its MIT licence and the runtime script |
| [scripts/](scripts/) | Installer and repository layout checker |
| [tests/](tests/) | Runtime, installer and layout behavior tests |
| [docs/repository_layout.json](docs/repository_layout.json) | Local directory roles and required interfaces |
| [docs/provenance.md](docs/provenance.md) | Development provenance, Git mechanisms and packaging references |
| [docs/release_checks.md](docs/release_checks.md) | Fresh-clone, installed-copy and release validation commands |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | Dependency-free test and layout commands |

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
Code and documentation were prepared with AI assistance under human direction.
The licence is [MIT](LICENSE).
