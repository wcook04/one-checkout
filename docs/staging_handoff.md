# Understand a preserved staged snapshot

The index stores a complete staged snapshot, not an edit labelled with an
agent's ownership. That distinction remains after a successful commit.

For a same-file example, let the base contain A=0, B=0, C=0. Before landing,
the index contains A=0, B=2, C=0 and the working file contains A=1, B=2, C=3.
An explicit A-only patch creates a commit containing A=1, B=0, C=0.
One Checkout retains the original staged blob and all working bytes.

| View | A | B | C |
|---|---:|---:|---:|
| Captured parent | 0 | 0 | 0 |
| New committed tree | 1 | 0 | 0 |
| Preserved index | 0 | 2 | 0 |
| Unchanged working file | 1 | 2 | 3 |

An ordinary next commit of that index records A=0 again. This is a real
follow-on reversal, not merely a confusing status display. The same-file
preservation contract is useful only when its next step is understood.

## Receipt review

For each changed path whose retained index entry differs from the candidate,
`staging_review` records `path`, `parent_entry`, `preserved_index_entry` and
`candidate_entry`, plus `preserved_flags`. An entry contains the Git mode and object ID; a missing
entry is JSON `null`. The warning code is `preserved_staging_differs`.
No source-file contents are copied into this metadata.

These are snapshot comparisons, not proof of authorship, a predicted merge
conflict or proof that the patch is correct. An already staged candidate without
semantic flags needs no warning even when it appears in `preserved_staged_paths`.
Preserved assume-unchanged, skip-worktree or intent-to-add flags also require review.
Unrelated staged paths are not an inventory in `staging_review`.

Planning returns prospective metadata. Candidate objects created only in a
plan's temporary object store need not remain readable afterwards. A plan
is not a reservation: commit rechecks state and returns its own receipt.
Consumers of `one_checkout_receipt_v1` should tolerate additive fields.

## Deliberate next action

Keep the first receipt and the original patch. Inspect current HEAD and
the preserved snapshots. To understand B's captured delta, compare against
the receipt's **old parent**, not just the new HEAD:

```sh
git diff --cached --binary OLD_PARENT_OID -- path/to/file
```

That command is evidence to review, not a universal second-patch generator.
The staged delta may itself contain multiple owners' edits. Capture a
reviewed B patch against an explicitly chosen current parent, preserving
A and any dependencies it needs. Run required checks on that exact candidate.
Only then make a deliberate second call to One Checkout.

Never refresh `--expected-parent` blindly after a refusal. Never run reset,
checkout, restore, broad add or automatic restaging to make the status look
clean. Those operations can discard or reassign another collaborator's
intent. Even after the positive synthetic A-then-B demonstration, the
preserved index can still omit A: reconciliation is not implemented here.

## Index timestamps are part of correctness

Retaining an entry's mode and object ID is necessary but not sufficient.
Git also uses the index file's timestamp when deciding whether cached
filesystem metadata can be trusted. Copying the same bytes to a newer index
can invalidate its racy-clean protection. [S05 in prior_art.json]

The runtime retains the captured timestamp on copied indexes and on the
published index. This is conservative: Git may recheck retained entries
rather than assume that working bytes match. It does not change their blob
IDs, modes or the working files. Tests cover wholly retained entries,
partially synchronised indexes, unselected racy entries, read-only planning
and failure to set the publication timestamp.

The fix is not a proof for every filesystem or Git backend. Re-run the
regressions across the supported platform/version matrix before release.
