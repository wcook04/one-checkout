---
name: one-checkout
description: Commit an explicit patch and exact file paths in a shared Git checkout while preserving unrelated staged entries and working files. Use when several trusted agents or people share a checkout and need a scoped commit.
---

# One Checkout

Use the bundled `scripts/one_checkout.py` with Python 3.10+ and Git on macOS
or Linux. Resolve
the script relative to this SKILL.md, then run it with `--repo` pointing at
the target repository. This skill includes its implementation and has no
dependency on the repository it was developed in.

Before committing, inspect the intended diff and current Git state. Write a
patch that contains exactly the change the user authorized. Name each exact
repository-relative file with a repeated `--path`; directories and globs are
not accepted. Record the full expected parent OID once and keep it for both
planning and committing.

```sh
python3 /path/to/this/skill/scripts/one_checkout.py plan \
  --repo /path/to/repository --patch /path/to/owned.patch \
  --path src/example.py --expected-parent FULL_PARENT_OID

python3 /path/to/this/skill/scripts/one_checkout.py commit \
  --repo /path/to/repository --patch /path/to/owned.patch \
  --path src/example.py --expected-parent FULL_PARENT_OID \
  --message "Describe this change"
```

`plan` validates the candidate without updating the branch or shared index.
`commit --dry-run` is the same operation. Inspect the JSON receipt before
calling `commit`; use `--message-file` for a longer commit message.

The patch is applied to a private index seeded from the expected parent.
Working files are not patched or required to match that candidate. This
allows other staged and unstaged hunks in the same file to survive. The
shared index retains a foreign staged blob; after your commit it may show
an apparent reversal of your change relative to the new HEAD. Inspect it,
and do not reset or restage it to make status look clean.

The commit holds the shared index lock and uses Git's prepared ref
transaction to check the named branch and expected parent before landing.
If HEAD advances or the selected branch changes, stop and inspect the new
branch, parent and patch. A refusal is
not permission to substitute the new HEAD and retry automatically. Split,
sparse and unmerged indexes are refused in this version.

Exit 2 is a refusal. Exit 3 returns a recovery receipt on stdout:

- `landing_uncertain` with `ref_update_confirmed: false` means the commit
  request was sent but success was not acknowledged. `new_commit` is a
  candidate, not proof of landing. The original shared index is retained.
- `landed_unsettled` means a confirmed commit has an index-settlement or
  cleanup failure. Confirmed ref transactions with protocol cleanup failure
  include `ref_update_confirmed: true`.

Inspect the branch and reflog against `new_commit`, current HEAD, shared
index and any reported remaining locks or scratch before doing anything
else. Do not rerun the commit or roll back another agent's work. A process
interruption can require the same inspection even without a receipt.

The tool disables Git hooks for its invocations, including commit and
reference-transaction hooks, without modifying repository configuration.
It honors configured `commit.gpgSign`; signing tooling may be required. Run
the project's required checks separately. This tool does not prove tests
passed, claim a path lease, prevent filesystem edits or push a branch.
