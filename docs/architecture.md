# Runtime architecture and outcome boundaries

This is a single-purpose Git command, not a scheduler, worktree manager or
replacement transaction engine. All runtime code remains in
`skills/one-checkout/scripts/one_checkout.py`; the installer copies the explicit `bundle.json` payload, including guidance,
demo and doctor, as one self-contained skill.

## Responsibilities and resource owners

| Responsibility | Code | Boundary |
|---|---|---|
| Controlled Git execution | `Git` | Resolve Git; reject inherited control variables; disable hooks, replacements and optional writes per invocation. |
| Shared-index ownership | `GitLock` | Create `index.lock` exclusively, retain descriptor/inode ownership, publish by rename; never remove a replacement writer's lock. |
| Candidate preparation | `commit_patch`, `_paths`, `_entries` | Apply the explicit patch to the captured parent in a private index; validate the exact path set and future shared entries. |
| Conditional branch update | `_publish_ref`, `_verify_prepared_head` | Use Git's native prepare/commit protocol; verify the named branch and parent while prepared locks are held. |
| Index settlement and cleanup | `commit_patch`, `LandingResources` | Settle separately after ref confirmation; preserve confirmed or uncertain outcomes when later cleanup fails. |
| Receipt delivery | `_report_receipt`, `_emit_receipt`, `main` | Report what happened without treating a broken output stream as a failed commit. |

Keep these boundaries visible inside the file. Extract a module only when it
has independent ownership and enough benefit to justify changing the copied
skill's resource contract. A generic backend, lock service or transaction
framework is not required for this command.

## State transition and knowledge

The ref protocol has three internal states (`RefProgress`). They represent
what this process knows, not what a later `rev-parse HEAD` happens to show.

```text
validate -> capture shared index -> build candidate and next index
         -> native start/update/prepare -> verify named branch + parent
               PREPARING
                   |
          mark REQUESTED before writing/flushing commit
                   |
          receive exact "commit: ok" acknowledgement
                   |
               CONFIRMED -> settle index -> release resources -> deliver JSON
```

Before REQUESTED, refusal means this invocation did not request ref publication.
Creating an unreachable candidate object during commit preparation is not the
same as publishing it. A plan keeps even its candidate objects in scratch.

At REQUESTED, a failed write or flush may have delivered all, some or none of
the request. Without acknowledgement, return `landing_uncertain`, retain the
original shared index and expose the candidate OID. Object existence alone is
not evidence of landing. Tests exercise both actual outcomes with real Git.

At CONFIRMED, a later failure cannot turn the result into refusal or uncertainty.
It can make it `landed_unsettled`. Ref confirmation does not guarantee index
settlement, resource cleanup or successful JSON delivery. Cleanup of an already
uncertain request must also remain uncertain; `LandingResources.outcome_receipt`
therefore deliberately does not imply that the commit landed.

## Conservation of A, B and C

Let P be the expected parent, I the captured index, and T the candidate produced
by applying the caller's patch to P. Let W be the working files. Compare index
entries by mode, object ID and semantic intent flags, including absence, not
by a textual merge. Preserve relevant index timestamps for racy-clean detection.

For every changed path p:

```text
if I[p] == P[p] and no semantic flag: next_index[p] = T[p]
else:           next_index[p] = I[p]
```

Unchanged paths retain their original entries. The partition is one pass over
changed paths; building and validating the complete entry maps still costs
space and time proportional to the repository index.

In the same-file fixture, A is selected, B is another agent's staged snapshot,
and C is an unstaged edit. The commit contains A only; B's staged blob survives;
W still contains A+B+C. Because B's blob may lack A, its diff against the new
HEAD can display an apparent reversal of A. Never tidy that away by resetting,
restaging or replacing the shared index with T. The contract preserves staged
entries, not byte-identical serialisation of every Git index extension.

## What coordinates, and what does not

The shared index lock coordinates Git writers that honour it. Ownership and
snapshot checks detect some writers that bypass it, but this is not an access
control boundary against a malicious or arbitrary filesystem writer. The
native ref transaction protects the selected ref publication; checking only
the old OID would miss switching between two branches with the same OID.

Ref publication and shared-index replacement are separate operations. Another
observer can see the new ref before settlement. Do not claim atomic visibility
of ref plus index, or roll back a published ref after another actor may depend
on it. The worktree is neither locked nor written. Use isolated worktrees when
agents need incompatible filesystem or runtime states.

## CLI outcome contract

The JSON schema remains `one_checkout_receipt_v1`. Status is the main outcome
discriminator; `ref_update_confirmed` is an optional protocol diagnostic, not a
second mandatory status system. In particular, `false` means *unacknowledged*,
not *known not to have landed*.

| Outcome | Exit | Delivery |
|---|---:|---|
| Successful plan or settled commit | 0 | JSON on stdout. |
| Refusal before publication is requested | 2 | Error on stderr, best effort. |
| Plan completed but output failed | 2 | `receipt_output_failed` on stderr, best effort; repository unchanged. |
| Publication requested but unacknowledged | 3 | `landing_uncertain`; inspect candidate, branch and reflog. |
| Confirmed commit with settlement/cleanup failure | 3 | `landed_unsettled`; inspect index as well as ref. |
| Commit/recovery receipt could not reach stdout | 3 | Preserve the actual transaction status; attempt stderr with delivery diagnostics. |

Receipt output is written through the stream descriptor where available, so
buffered interpreter shutdown cannot overwrite an already chosen exit code with
a second failed flush. Stream-only embeddings are explicitly flushed. This is
CLI-only handling; `commit_patch` does not alter process output streams. Both
streams may fail or contain partial records. Missing JSON is never refusal.

Recovery is inspection, not automatic retry. Compare the candidate with the
branch and reflog, including later branch advances; inspect the staged entries,
worktree and reported leftover resources. Neither a present candidate object
nor the fact that current HEAD differs from it alone settles the question.

## Deliberate limits and regression obligations

`--git-timeout` defaults to 30 seconds per Git command or complete protocol
acknowledgement, including partial lines. This is not a total operation deadline.
Ordinary helper descendants are terminated through their owned POSIX process
group; escaped processes and uninterruptible kernel calls are outside that bound.

Optional `--record-file` retains caller-owned append-only phase evidence outside
the checkout. The requested marker precedes sending commit. `recover` inspects
recorded identity, candidate/ref history and index observations without repair.
See [recovery record design](recovery_design.md) and the installed
[recovery guide](../skills/one-checkout/references/recovery.md). No power-loss
atomicity or exactly-once guarantee follows from a record.

For changes, run the full suite, including `tests/test_transaction_safety.py`,
`tests/test_recovery.py` and `tests/test_agent_contract.py`. Check
real commit trees, index entries and working bytes, not just mocked calls.
Retain read-only planning, exact paths, lock-replacement refusal, branch/OID
races, lost acknowledgements, secondary cleanup failures and broken-output
regressions. See [provenance.md](provenance.md) for the underlying Git mechanisms
and relevant prior art, and [release_checks.md](release_checks.md) for installation
checks. Installed-script tests are not evidence of live agent discovery.
