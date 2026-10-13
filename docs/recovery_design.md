# Operation records and read-only recovery

A killed caller may return no receipt. `commit --record-file` opts into a
caller-owned append-only record outside the checkout and Git metadata.
`recover --record-file` observes that record and the identified repository.
Neither command is a retry service, rollback mechanism or lock cleaner.

## Use

Choose a new file under an existing private physical directory outside the
repository. Do not reuse a record from an earlier attempt:

```sh
python3 "$TOOL" commit --repo "$REPO" --patch "$PATCH" --path src/example.py \
  --expected-parent "$PARENT" --plan-file "$PLAN" \
  --record-file "$ARTIFACTS/operation.jsonl" --message "Describe the change"
python3 "$TOOL" recover --record-file "$ARTIFACTS/operation.jsonl"
```

The record is optional. Direct commits remain supported, and a plan alone
cannot identify a later interrupted attempt. Records contain local identities,
object IDs, patch digests, exact paths and error text: keep them private.
They do not contain a backup of the index or keep a candidate object reachable.

## Evidence ordering

Each JSONL event is flushed to the file before its append is considered complete.
The initial record is created exclusively with private permissions; existing
records are refused. The operation identity and repository/index bindings are
stable across events:

1. `prepared`: candidate and next-index preparation succeeded; no publication
   request has been sent.
2. `requested`: this evidence is persisted **before** any commit request byte is
   sent. A crash immediately afterwards can therefore leave conservative
   uncertainty even if Git never received the request.
3. `confirmed`: this process received Git's complete successful acknowledgement.
4. `outcome`: observed settlement outcome, where that append completes, before
   resource-context cleanup. A later cleanup failure can still change the live receipt.

Failure to record the pre-request boundary prevents the request. Failure after
Git acknowledgement cannot erase that knowledge from the live receipt. A partial
last line can result from interruption; inspection preserves complete earlier
observations and marks truncation. Malformed complete events or inconsistent
bindings are refused rather than guessed through.

## Separate observations

Recovery validates the record and recorded repository identity, then asks Git
about the recorded branch and candidate. Current HEAD alone is insufficient:
a user may have switched branches after the attempt. Observations are separate
reads, not one atomic snapshot.

| Observation | What it establishes | Limit |
|---|---|---|
| Recorded acknowledgement | The trusted local record says Git acknowledged | The branch may since have moved; the record is not signed proof |
| Branch currently equals candidate | Candidate is that branch's current tip | Does not establish which invocation produced it |
| Candidate is reachable from branch | Current history includes it | Does not imply shared-index settlement completed |
| Candidate absent or not reachable | Present evidence is insufficient | Pruning, shallow history or later ref movement can hide a prior landing |
| Reflog mentions candidate | Additional bounded history evidence | Missing/expired reflog is not a negative acknowledgement |
| Index matches original/prepared hash | Current bytes match that recorded snapshot | Does not explain why, imply ownership, or authorise replacement |
| Index differs or is missing | Settlement state needs review | Never permission to restore, reset or steal a lock automatically |

Candidate object existence alone does not prove landing. The branch returning to
the original parent does not prove that landing never happened. A recovery command
that returns successfully has completed inspection; it has not repaired anything.
Inspect errors and preserve uncertainty when evidence is incomplete.

## Failure and durability limits

The record, Git object/ref writes and index publication are different resources.
Fsyncing a record does not make those resources one atomic transaction, guarantee
power-loss survival, or provide exactly-once semantics. Process-kill experiments
are not power-failure tests. A moved/replaced repository or malformed record is
refused; relocation is a deliberate operator matter, not path guessing.

Ordinary Git invocations and complete acknowledgements have deadlines. An escaped
helper or uninterruptible OS call remains outside a hard wall-clock guarantee.
No record is guaranteed if a caller dies before the first complete append.
Retain stdout/stderr and the optional record, then inspect before choosing any
new authorised action. Never infer safe retry from an old tip or absent receipt.

## Review and tests

Runtime tests exercise phase failures, interruption, malformed/truncated records,
repository binding, separate index observations and read-only repeated inspection.
The process harness adds real Git races/deaths on disposable repositories. Run
[the transaction tests](transaction_safety.md) and
[stress tests](stress_testing.md) after changing record ordering or ref handling.
Broader filesystem, Git backend and power-loss claims require their own evidence.

[Jujutsu operation history](https://docs.jj-vcs.dev/latest/operation-log/) and
[GitButler's timeline](https://docs.gitbutler.com/features/timeline) are relevant
prior art for inspecting operations separately from ordinary commit history.
No implementation code from those projects is copied. See [provenance](provenance.md).
