# Select A without disturbing B or C

## The evidence boundary

The caller supplies intent; Git supplies snapshots, not hunk authorship. Record
which user/task authorised A, the repository, the named branch, the full parent
OID and the origin of the selected patch. The tool validates neither that record
nor the user's authority. Preserve the record for review; do not include secrets.

Sufficient evidence can be an explicitly reviewed patch, an edit transcript with
before/after bytes tied to the parent, or a clean reconstruction of the authorised
change from that parent. Timestamps, diff position, “unstaged means mine”, file
ownership, and model confidence are not substitutes. If A depends on B, an A-only
patch may apply yet produce broken software: resolve the dependency and test the
candidate separately. Passing tests in the mixed A+B+C checkout does not validate A.

## Tracked edit with no foreign changes in that file

Only after establishing the whole selected file is authorised, capture a patch
against the recorded parent. Disable display-only diff helpers and use literal paths:

```sh
git --no-replace-objects --literal-pathspecs -C "$REPO" diff \
  --no-ext-diff --no-textconv --binary "$PARENT" -- src/example.py > "$PATCH"
```

Inspect the actual patch, not merely the filename or diff statistics. Do not
refresh PARENT just to make a failing patch apply. A literal filename starting
with `--` can be supplied to the tool as `--path=--example.txt`.

## A, staged B and unstaged C in the same file

If you already captured A alone before other edits arrived, retain that patch.
Otherwise reconstruct A in an independent disposable clone, never by editing the
shared worktree down to A and later “putting B/C back”. Read the parent version,
apply only the authorised transformation there, and review the resulting diff.
An A/B/C diff can be one hunk; deleting whole hunks is not always sufficient.

A practical alternative for complex, binary or closely interleaved edits is an
isolated clone in a unique scratch directory (normal trusted-repository Git rules
still apply). Resolve TOOL/REPO/PARENT first; SCRATCH must be new and empty:

```sh
git clone --no-local --no-checkout -- "$REPO" "$SCRATCH/selection"
git -C "$SCRATCH/selection" -c core.hooksPath=/dev/null checkout --detach "$PARENT"
# Apply only the authorised edit in "$SCRATCH/selection", never in "$REPO".
# Review it there. Then export against the original parent with literal paths.
```

This recipe changes only the disposable clone. It is not a runtime dependency,
and it is not a defence against a malicious repository or configuration. If the
parent is unavailable or the intended change cannot be established, stop.

## Addition

Ordinary `git diff` omits untracked files. In the disposable selection clone,
create the authorised file, stage that exact file **there**, then export:

```sh
git -C "$SCRATCH/selection" add -- src/new.py
git --no-replace-objects --literal-pathspecs -C "$SCRATCH/selection" diff \
  --cached --no-ext-diff --no-textconv --binary "$PARENT" -- src/new.py > "$PATCH"
```

The patch needs a new-file entry; declare `--path src/new.py`. No `git add -N`
or staging helper belongs in the shared checkout merely to build this patch.
An addition need not match an existing untracked working file: one-checkout
commits the selected bytes and leaves that working file untouched.

## Deletion

In the disposable clone, remove only the authorised file and export its deletion
against PARENT. Declare the deleted exact path. The runtime does not delete the
shared working file: if it still exists, it can become untracked after landing.
That is the no-worktree-write guarantee, not an invitation to delete it afterwards.

## Review before landing

Use the same patch, paths and parent for plan and commit. Check the planned named
branch against the authorised target. Save and inspect the successful plan, then
pass it with `commit --plan-file`. The receipt hash is not a signature, an approval
record or automatic authorship evidence. Any changed intent needs a fresh review.
