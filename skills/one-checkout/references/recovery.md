# Receipts and recovery: observe first

This is a cooperating-writers command, not an exactly-once service. Keep stdout,
stderr and the process exit status separately. Exit status alone is insufficient:
3 means a commit may have landed or is confirmed landed, including when only
receipt delivery failed. A missing/truncated
receipt or killed process must be treated as unknown, not as a safe refusal.

| Receipt | What is established | Safe next action | Forbidden shortcut |
|---|---|---|---|
| `refused` (exit 2, stderr) | This call reported refusal, not a confirmed landing. Unreachable candidate objects may exist. | Inspect `code`. For locks, coordinate with the writer. For stale parent/plan mismatch, inspect and review a new proposal. | Change the parent automatically; delete a lock; infer that an earlier interrupted call never landed. |
| `planned` (exit 0, stdout) | A candidate passed the plan checks. Branch/index were not updated. Its tree may exist only in discarded temporary object storage. | Review the patch and bindings; commit with `--plan-file` only when authorised. | Treat plan as permission, a reservation, or a persistent commit/tree object. |
| `committed` (exit 0, stdout) | This call confirmed landing and completed its normal settlement/cleanup. | Report `new_commit`, preserve foreign work. | “Clean” the index with reset/restage; assume the branch can never advance again. |
| `committed` with `receipt_delivery: stderr_fallback` (exit 3, stderr) | Landing and normal settlement succeeded; stdout receipt delivery failed. | Inspect the fallback and `new_commit`; do not recommit. `next_action` is `inspect_receipt_delivery`. | Relabel completion as refusal or assume index settlement failed. |
| `landing_uncertain` (exit 3, stdout) | Request crossed the commit boundary without confirmation. Candidate SHA alone proves nothing about ref history. | Inspect candidate, the receipt's named branch, ancestry and reflog. | Blind retry, automatic rollback or “not HEAD means not committed”. |
| `landed_unsettled` (exit 3, stdout) | Landing was confirmed; index settlement or cleanup did not complete normally. | Inspect the known commit, branch, index and reported errors. Coordinate any repair. | Recommit A, rewind the branch, or remove locks indiscriminately. |

CLI receipts add `next_action`, `automatic_retry_allowed: false` and an explicit
`ref_update_confirmed`. False means no acknowledgement, **not** proof of failure.
The Python API retains its original receipt fields; CLI hints are computed after
cleanup so they describe the final reported status.

## Bounded read-only inspection

Resolve REPO from the saved plan/receipt and validate that PARENT/CANDIDATE are full
hex object IDs and BRANCH is the full `refs/heads/...` value from that receipt.
Do not interpolate untrusted JSON into `eval`. Inspect the named branch, not only
whatever branch is currently checked out. These commands do not repair anything:

```sh
git --no-replace-objects --no-optional-locks -C "$REPO" symbolic-ref --quiet HEAD
git --no-replace-objects --no-optional-locks -C "$REPO" rev-parse --verify "$BRANCH"
git --no-replace-objects --no-optional-locks -C "$REPO" show \
  --no-patch --format=raw "$CANDIDATE"
git --no-replace-objects --no-optional-locks -C "$REPO" reflog show \
  -n 30 --format='%H %gD %gs' "$BRANCH"
git --no-replace-objects --no-optional-locks -C "$REPO" ls-files --stage -z
git --no-replace-objects --no-optional-locks -C "$REPO" diff \
  --cached --no-ext-diff --no-textconv --binary
```

Compare the candidate's parent and tree to the saved receipt, and check the exact
selected diff. If the branch has advanced, `git merge-base --is-ancestor CANDIDATE
BRANCH` can establish reachability. Exit 0 means reachable; exit 1 means not an
ancestor in the currently visible graph; other exits mean the check failed.
Reachability and reflog presence help establish history, but absence does not prove
non-landing: history may have moved again, the reflog may be missing/truncated, or
the repository may be shallow. These reads are not one atomic snapshot. If evidence
is contradictory or incomplete, retain the uncertainty and ask the coordinating
operator. Do not use `fsck`, garbage collection, reset, stash or retries as repair.

## Why the index can appear to undo A

Before landing: HEAD = base; index = base+B; working file = base+A+B+C.
After landing: HEAD = base+A; index remains base+B; working file is unchanged.
Consequently `git diff --cached` compares base+A with base+B and can display
minus-A plus-B. Preserving B's staged blob caused that result deliberately.
The next writer must select its patch against the new parent; it must not blindly
commit the retained index or restage the entire mixed file.

`preserved_staged_paths` lists selected paths whose foreign index entries were
retained. It is not a complete inventory of all other staged files. Index entries
that matched the old parent are synchronised to the new commit. A plan's index
classification may differ from a later commit because another writer staged work;
the commit rechecks the current locked snapshot instead of freezing the plan's index.

## Optional operation record

For an authorised commit, `--record-file /absolute/private/operation.jsonl`
creates new append-only phase evidence outside the checkout and Git metadata.
Its parent must already exist and have no symlink ancestors. Temporary-directory
paths can contain platform aliases, so resolve the existing private directory
you own before choosing a fresh record filename. For example:

```sh
RECORD_DIR=$(python3 - <<'PY'
from pathlib import Path
import tempfile

print(Path(tempfile.mkdtemp(prefix="one-checkout-record-")).resolve(strict=True))
PY
)
RECORD="$RECORD_DIR/operation.jsonl"
```

Inspect the resolved directory and confirm it is outside the checkout and Git
metadata before committing. This creates a new private directory; it does not
make an arbitrary existing destination safe. Do not remove symlinks or weaken
the record-location guard to clear a refusal.

Use a fresh path; an existing record is refused. The `requested` observation
is persisted before request delivery. After interruption, run:

```sh
python3 "$TOOL" recover --record-file "$RECORD"
```

Inspection validates record/repository identity and reports candidate/ref/index
observations separately. It changes neither repository nor record, and never
repairs, retries or deletes locks. Current-tip equality and ancestry are evidence;
absence is not proof of non-landing. Records are local evidence, not signatures,
index backups or power-loss/exactly-once guarantees. Keep their paths and errors
private. An incomplete final append is marked; malformed complete events refuse.

Without a record, the saved plan identifies intent but not necessarily the
created commit SHA. Compare branch history with parent/tree/patch evidence;
identical content alone does not identify a unique attempt. Preserve ambiguity
rather than scanning and guessing a retry target.

## Lost output is separate from a failed transaction

A closed stdout pipe can occur after Git has completed. The CLI attempts the same
transaction status on stderr and exits 3; it never calls a known landing a
refusal because printing failed. An uncertain or unsettled status remains so
if its output also fails. Both streams can be unavailable, or a process can die
before either is written. No receipt means insufficient evidence, not safe retry.
An optional record can retain earlier phase evidence; it is not an exactly-once
recovery service and may be incomplete after interruption.
