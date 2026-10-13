---
name: one-checkout
description: Make a user-authorised, patch-scoped commit in a shared Git checkout, preserving other staged and working changes. Use for selected commits, not to infer ownership, isolate concurrent edits, or clean Git status.
---

# One Checkout

Resolve `scripts/one_checkout.py` relative to this file. Requires Python 3.10+
and Git 2.39+ on macOS/Linux. Loading this skill is not permission to commit.

The invocation needs ordinary Git metadata write permission; installation grants
none. Inherited `GIT_*` controls other than author/committer identity are refused.
Inspect an `unsafe_environment` refusal and use a reviewed, command-local
environment; do not rewrite global settings or bypass the host's approval controls.
Claude Code may inject `GIT_EDITOR` into each shell command. If that is the
reported control, its editor preference is unnecessary for an explicit-message
commit: use `env -u GIT_EDITOR python3 "$TOOL" ...` for a newly reviewed invocation.
Changing the launcher environment alone may not remove a host-injected variable.
Other reported controls need their own review; never clear them all blindly.

## Authorise and select

Establish the authorised change, target repository/branch, full parent OID,
and evidence for the A-only patch: a reviewed supplied patch, captured edit
record, or an isolated reconstruction from that parent. A file path, the
current diff, or an instruction to “commit my work” does not establish hunk
ownership. When evidence is missing, ask for scope; do not guess.

Never temporarily remove B/C from shared files to obtain A. For mixed edits,
additions or deletions, read [patch selection](references/patch-selection.md).
Keep the patch and receipts outside the checkout, in a unique private directory.

## Plan, review, commit

Use exact repository-relative files (repeat `--path`; no globs/directories).
Capture `PARENT` once. Keep the successful plan; do not overwrite it during commit.
Use a fresh external record path if retaining interruption evidence.
`TOOL`, `REPO`, `PATCH`, `PLAN`, `RECORD`, `RECEIPT` and `ERROR` below are absolute paths
resolved for this task, not literal placeholder filenames.

```sh
python3 "$TOOL" plan --repo "$REPO" --patch "$PATCH" \
  --path src/example.py --expected-parent "$PARENT" > "$PLAN" 2> "$ERROR"
```

Stop on a non-zero exit. Read the patch and JSON. Check the authorised branch,
parent and exact scope. A successful plan validates a candidate, not ownership
or tests. Only then, with commit authorisation:

```sh
python3 "$TOOL" commit --repo "$REPO" --patch "$PATCH" \
  --path src/example.py --expected-parent "$PARENT" --plan-file "$PLAN" \
  --record-file "$RECORD" --message "Describe the authorised change" > "$RECEIPT" 2> "$ERROR"
```

`--plan-file` binds repository, branch, parent, patch bytes, paths and candidate
tree. It does not reserve the checkout, bind the message, or grant authorisation.
Never edit a plan or substitute a new HEAD to bypass a refusal. Review anew.

## Interpret the result before another action

| Exit / status | Next step |
|---|---|
| 2 / `refused` (stderr) | Read `code`; inspect the cause. No automatic retry. |
| 0 / `planned` | Review patch and plan; no commit has landed. |
| 0 / `committed` | Report `new_commit`. Leave other work alone. |
| 3 / `landing_uncertain` | Candidate is not proof. Inspect the named branch/reflog before any retry. |
| 3 / `landed_unsettled` | Commit landed. Inspect index/cleanup; do not recommit. |
| 3 / `committed` with `receipt_delivery` (stderr) | Stdout failed after success. Inspect the fallback; do not recommit. |

Capture stderr as well as stdout: failed stdout delivery can send the receipt
to stderr without changing its status. For exit 3, interruption, missing JSON
or an unfamiliar status, read
[recovery](references/recovery.md) and stop mutating. No blind retry, rollback,
lock deletion, `reset`, `stash`, `checkout`/`restore`, or restaging to tidy status.
A preserved foreign staged blob can show an inverse of A against the new HEAD;
that is expected, not corruption.

Hooks are disabled; signing configuration is honoured. Run required policy/tests
separately. Working files are never written. Use worktrees for editing isolation;
this tool provides no leases, authorship inference, semantic validation or push.
Split, sparse, unmerged and partial/promisor repositories are refused.
Use `--git-timeout` for bounded per-command/protocol waits; a timeout after a
commit request may still mean landing occurred.

Run `scripts/doctor.py --self-test` relative to this skill to check prerequisites
and the bundled disposable demo. This tests the installed code, not agent efficacy.
