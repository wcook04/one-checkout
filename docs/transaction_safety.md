# Transaction safety and outcome contract

This describes the integrated runtime, not a proof for every Git version or filesystem.
The unit of success is an explicit patch landed on the captured branch or detached HEAD;
it is not isolation of working files, automatic patch ownership, or proof that
that patch builds. The caller and other writers must cooperate with Git locks.

## State and invariants

Let `B` be the fully qualified captured branch or the detached worktree's `HEAD`,
with its attached/detached mode, `P` its expected parent, `H`
the candidate commit, `I0` the locked original index, `I1` the prepared next
index, and `W0` the original working-file bytes. Git owns ref publication;
one-checkout owns the shared index lock by inode identity.

1. The tree of `H` comes from applying only the supplied patch to `P` through
   a private index. Changed paths must exactly equal the declared paths.
2. No operation intentionally writes working files: `W_after = W0` in the
   absence of writes by another process.
3. A selected index entry is synchronised only when its mode/OID equals `P`
   and it has no assume-unchanged, skip-worktree or intent-to-add flag.
   Otherwise its original staged snapshot and those flags are retained.
   Unselected entries and those flags are also verified before publication.
4. `B` and `P` are rechecked while Git's native prepared transaction protects
   HEAD and its resolved ref when attached. OID equality alone is insufficient:
   two branches, or detached and attached HEAD, can point to the same commit.
5. Once the commit request may have been delivered, an error is not evidence
   that the ref stayed unchanged. Once acknowledged, later errors cannot
   convert the result into a no-commit refusal.
6. A replacement lock is not ours to remove. No recovery rolls a branch back.

## Actual transitions

| Phase and code | Possible repository effect | Failure or interruption disposition |
|---|---|---|
| Validate arguments, root and object policy (`commit_patch`, `_check_object_policy`) | Read configuration and discover paths | Refusal; no ref request. Partial/promisor repositories are refused before object lookup. |
| Acquire `GitLock`; capture `B`, `P`, `I0`; check representation | Own `index.lock` for commit, not plan | Refusal on lock contention, stale parent, unmerged/sparse/split state. Never remove a replacement lock. |
| Apply patch to private index; validate scope; prepare `I1` | Scratch writes; commit mode can add unreachable Git objects | Refusal is still pre-publication, but is not a promise of zero object writes. Plan puts new objects in temporary storage. |
| Create `H` with `commit-tree` | Commit object may exist even if branch never moves; a signer may execute | Bounded Git timeout/cancellation is pre-ref-request. A created object is not a landed commit. |
| Send `start`, `update HEAD H P`, `prepare`; receive preparation acknowledgement | Git holds native ref locks | A prepare failure or branch-identity mismatch is pre-commit-request. Abort where possible; a forcibly stopped Git process can leave its own stale ref locks. |
| Set progress to `REQUESTED` **before** write/flush of `commit` | Request may or may not reach Git | Missing/partial acknowledgement, timeout, I/O error or caught interrupt becomes `landing_uncertain`, with `H` and `ref_update_confirmed: false`. Retain `I0`. |
| Receive `commit: ok` | Git confirms the ref transaction | `ref_update_confirmed: true`; later protocol cleanup failure yields `landed_unsettled`. Do not undo the ref. |
| Verify current branch/index; publish `I1` with owned lock | Shared index changes separately from the branch | Failure or caught interrupt yields `landed_unsettled`. The branch and index are **not** one atomic transaction. |
| Release resources; deliver JSON (`LandingResources`, `main`) | Clean up owned resources; write receipt | Cleanup errors retain the outcome. A lost stdout receipt after landing attempts a recovery receipt on stderr and exits 3, never a false no-commit refusal. |
| Uncatchable kill, SIGTERM, interpreter/host failure, or a narrow uncaught interruption | Depends on last completed operation | No receipt may exist. Inspect state; absence of output proves neither success nor failure. An optional external record may retain evidence; inspection never repairs automatically. |

`committed` means ref acknowledgement and local index settlement completed; it
is not a statement that no other actor subsequently moved the branch.
`landed_unsettled` includes cleanup problems, so it does not necessarily mean the
index is still original. Output-delivery failure retains the transaction status
and exits 3 with best-effort stderr evidence. `landing_uncertain` describes
what the caller knows, not a probability of success.

## Bounded subprocess behaviour

`--git-timeout SECONDS` defaults to 30 and must be finite and positive. Each
Git subprocess and each complete ref acknowledgement has its own deadline;
this is not a 30-second total transaction deadline. The acknowledgement reader
also bounds a process that writes only part of a line.

Timed-out invocations and ordinary helper descendants are stopped using their
own POSIX process group. Preparation/signing timeouts remain pre-publication;
a timeout after request delivery remains uncertain. Process creation,
uninterruptible kernel I/O, a helper which escapes its process group, and
multiple asynchronous interrupts are outside a hard wall-clock guarantee.
The tool is not a sandbox for untrusted Git configuration or signing programs.

## Index and planning boundaries

The runtime asks Git for entries and semantic flags rather than implementing
an index writer. `ls-files --debug -z` has no stable public output guarantee.
Its five-line record format is recognised strictly; unfamiliar output is
refused. The tests use an independent binary reader for full index versions
2, 3 and 4, not the runtime helper. This is a compatibility dependency to test
on every supported Git release, not a reason to claim future compatibility.

Preservation is about staged entry mode/OID and the three recognised intent
flags. It is not a byte-identical promise for all index stat/cache extensions
when selected entries need synchronisation. A real resolve-undo (`REUC`)
fixture exercises preservation. Relevant original/prepared index timestamps are
retained so Git can still identify rapid same-size working-file edits. Untracked-cache, fsmonitor cache,
unknown future extensions and every cache-invalidation combination have not
been exhaustively qualified.

Actual sparse-directory entries and nonzero split-index pointers are refused,
even with misleading disabled configuration. The all-zero split pointer is a
sentinel, not proof of a real split index. Sparse checkout configuration is
also refused; a genuine full index with `core.splitIndex=true` is accepted.

Plan is read-only with respect to repository file contents on the executed
fixtures: ordinary complete clones, shallow complete clones and local object
alternates. It creates temporary objects outside the repository. A partial
clone can trigger Git's lazy fetching even during a read: this version
conservatively refuses partial/promisor configurations and additionally sets
`GIT_NO_LAZY_FETCH=1`. Locally complete promisor clones are still refused.
Missing required objects in an ordinary clone cause refusal. Access times,
external tracing, hostile helpers and storage-server side effects are not
covered by the repository-byte checks.

## Recovery comparison

Run `python3 scripts/recovery_demo.py --human` from the source checkout. The
example adapts the existing bundled demo fixture and recovery fault tests; it
uses the current runtime without adding fault controls to the installed tool.
It compares `landing_uncertain` before request delivery and after a real Git
publication with lost acknowledgement, `landed_unsettled` after index publication
fails, and `committed` after both steps finish. A candidate object exists in both
uncertain cases. Each case issues one commit request and inspects its external
phase record twice through the real recovery CLI. Repeated inspection must leave
repository and record bytes unchanged and keep `automatic_retry_allowed=false`.

These observations do not form a reservation scheduler or an atomic snapshot.
The fixture knows where it injected a failure; the ordinary caller does not.
A branch or reflog that does not mention the candidate is not proof that the
operation never landed. The runtime returns `recovery_observed`, and the caller
still needs to reconcile every relevant effect before deciding what to do.
No automatic settlement, rollback, lock removal or retry occurs.

## Evidence map

| Invariant or boundary | Executable evidence |
|---|---|
| A-only tree; B staged and A+B+C working bytes preserved | `tests/test_one_checkout.py::test_same_file_foreign_stage_and_unowned_hunks_survive` |
| Exact scope, stale parent and native ref race | Scope/parent/CAS tests, same-OID branch and attached/detached switches, ordinary and linked detached worktrees |
| Replacement hooks/objects cannot alter protocol assumptions | Original reference-hook and replace-ref regressions |
| Known intent flags and index versions | `tests/test_transaction_safety.py` flag tests and independent `read_disk_entries` oracle |
| Genuine sparse/split representations | New disabled-configuration fixtures and full-index split-config test |
| No implicit origin contact in accepted planning modes | New partial-clone refusal, shallow, alternate and missing-blob tests |
| Bounded partial acknowledgement and signer | New real subprocess timeout fixtures |
| Git actually commits but acknowledgement is lost | `test_lost_ack_timeout_after_real_git_commit_is_uncertain` and original lost-ack test |
| Interruptions after request, during settlement and cleanup | New three interruption tests |
| Receipt pipe failure after real landing | `test_stdout_delivery_failure_does_not_refuse_landed_commit` |
| Resolve-undo and no-newline-at-EOF | New raw-Git REUC and exact-byte EOF tests |

`scripts/check_backends.py` exercises files/reftable refs, SHA-1/SHA-256,
ordinary/linked worktrees and packed files refs using disposable repositories.
Each supported configuration checks same-file preservation, switching between
same-OID branches before prepare, and rejecting a symbolic switch while prepared.
The same cases run from detached HEAD, checking that all named refs remain
unchanged and, in linked worktrees, that the primary HEAD/index/files survive.
Unsupported capabilities are explicit skips; exercising no cases is not success.
Run it with `--scratch /path/to/existing/scratch --output /tmp/backends.json`.
An executed local matrix does not establish other Git versions, network
filesystems or power-loss behaviour. Re-run it on release platforms.

## Primary references

- [Git update-ref protocol](https://git-scm.com/docs/git-update-ref).
- [Git index format](https://git-scm.com/docs/index-format) and
  [ls-files debug warning](https://git-scm.com/docs/git-ls-files).
- Git 2.47.3 [ls-files implementation](https://github.com/git/git/blob/v2.47.3/builtin/ls-files.c)
  and [index flag definitions](https://github.com/git/git/blob/v2.47.3/read-cache-ll.h).
- [Git environment controls](https://git-scm.com/docs/git).
- [Python subprocess timeouts and sessions](https://docs.python.org/3/library/subprocess.html).

References inspected on 13 October 2026. The invariants and experiments above
are one-checkout-specific analysis, not guarantees made by those sources.
