# Measuring verification cost

[Penny’s public question](https://thecolony.ai/post/64ff24a2-a9b1-4c54-af68-daacaad0fd7b)
prompted measurement of verification cost; Will then requested investigation of
safe optimisations. This is inspiration credit for the question, not a claim
that Penny diagnosed a bug, wrote code or ran this experiment. Existing
[contributor credits](../CONTRIBUTING.md#feedback-contributions) are preserved.

## What changed

The original and prepared private-index snapshots each need entries (mode, object
ID and stage) and semantic flags (assume-unchanged, intent-to-add and skip-worktree).
Previously, each snapshot used separate `git ls-files --stage -z` and
`git ls-files --debug -z` processes. A combined
`git ls-files --stage --debug -z` reads both in one traversal.

All entry and flag comparisons remain. Unmerged entries and unfamiliar debug
output still refuse. The change does not alter parent/branch checks, lock
ownership, index snapshots and timestamps, hook policy, signing, native ref
transactions, recovery or receipt delivery. It does not cache mutable shared
state or skip post-publication checks. Both maps still cover the entire index.
Git's debug output remains an unstable-format dependency, handled by refusing
unrecognised output. The regression tests compare it with an independent binary
index reader across supported index versions and unusual filenames.

On the synthetic foreign-staging fixture, `Git.run` calls decrease from **19 to
18 per plan**, and **30 to 28 per commit**. Commit also has one separate
`update-ref` protocol process in both versions. Counts vary with the operation:
clean selected entries need `update-index`, and recovery recording needs identity
queries. This reduction is deterministic; wall-clock benefit depends on Git,
index size, storage and competing host work.

## Reproduce

Run from a source checkout, with Python 3.10+ and Git 2.39+:

```sh
scratch=$(mktemp -d)
git show 32df8f5a107f2e6cd568b8681f18a330ffead74a:skills/one-checkout/scripts/one_checkout.py > "$scratch/baseline.py"
python3 scripts/benchmark.py --baseline-runtime "$scratch/baseline.py" --repeats 5 > "$scratch/small.json"
python3 scripts/benchmark.py --baseline-runtime "$scratch/baseline.py" --repeats 5 --extra-files 2000 > "$scratch/larger.json"
```

The baseline must be present in the local Git history. The script alternates
baseline/candidate order on fresh disposable fixtures, prints runtime SHA-256s,
versions, every sample and medians, and checks the selected tree, preserved
staging/worktree, exact scope, parent and read-only plan. It times setup and
verification separately from in-process plan/commit calls. `Git.run` timings
exclude the separate ref-protocol process; total commit timing includes it.
Interpreter startup, human review, project tests and cleanup are outside the
runtime intervals. A completed JSON result follows successful fixture cleanup.

Use a quiet host; avoid running the suite or other benchmarks simultaneously.
`--extra-files` adds unchanged tracked files, not selected changes. This is a
synthetic runtime experiment, not a benchmark of agent productivity or evidence
that all verification overhead has been isolated.

## What a receiving auditor can repeat

Reproduction depends on the auditor's access and runtime. Record the source
revision, baseline availability, Python/Git versions, platform, selected fixture
size, repeat count and elapsed time with the result. A receiver without the
baseline blob cannot reproduce the paired comparison from candidate timings
alone. A source review or an existing report can still be useful, but name it
as such rather than a fresh execution.

The existing result fields have different scopes:

| Evidence | What the receiver can inspect | Limit |
|---|---|---|
| `runtime_sha256`, `versions`, `repeats`, `extra_files` | Which runtimes, host and requested sample population the report names | Declarations do not authenticate execution or guarantee access to those bytes. |
| `samples.<baseline/candidate>[].checks` | Six post-commit assertions recorded for each completed sample | `unchanged_files` is true without exercising a bulk-file case when `extra_files` is zero. Report that case as not exercised. |
| `samples.<baseline/candidate>[].verification_seconds` | Time spent on the explicit read-only-plan and post-commit verification | It excludes human review, project tests and the other intervals described above. |
| `median_seconds` | Summary of the completed runtime samples | It is not a budget, a promised receiver latency or the cost of every check. |

If access, runtime setup or a latency budget prevents a requested check, record
**not run** with the reason, the intended check and the evidence still missing.
A check that executed and failed is **failed**, not skipped. A deliberately
omitted check needs an explicit decision and a concrete condition for revisiting
it; name a date only when someone has actually agreed to one. Keep this note
beside the result, not in a success summary. The benchmark has no skip/resume
ledger or timeout option: it raises on a validation failure and emits its final
JSON only after all requested samples and fixture cleanup succeed. An empty or
partial stdout file after interruption is not a passing report, and must not
be assigned zero cost or folded into the medians.

A useful interpretation control is to compare `--extra-files 0` with a positive
value: the zero case may pass `checks.unchanged_files`, but only the positive
case exercises that assertion's file-count branch. Similarly, a receiving
auditor who cannot obtain the pinned baseline should mark the paired replay
not run, rather than treating candidate-only results as equivalent evidence.
These are reporting checks; they add no skip mechanism and do not weaken the
runtime's mandatory verification.

[Moth Light's comment in Penny's verification-cost thread](https://thecolony.ai/post/64ff24a2-a9b1-4c54-af68-daacaad0fd7b)
(comment `4d8ab9e9-4496-4f11-9a39-be347b6e621c`) prompted this receiving-auditor
access and omitted-check guidance. This credits a conceptual contribution, not
a reported One Checkout run, a diagnosed runtime defect, code authorship or
endorsement. Penny's original question and Will's optimisation request retain
their distinct credit above.

## Observations and limits

The first profile on macOS 15.6.1 arm64, Python 3.14.7 and Apple Git 2.39.5
measured a baseline commit at 0.443 seconds: its 30 `Git.run` calls consumed
0.421 seconds. This suggested reducing redundant subprocesses and index scans.
The remaining time includes Python, temporary-index handling and the separate
ref protocol. The disposable full demo also includes setup, snapshots, refusal
checks, native-Git comparisons and a follow-on commit; its total is not the
runtime's verification cost.

Seven paired samples per fixture, using `/usr/bin/git` directly and an empty
fixture HOME for each runtime, produced these medians (seconds):

| Unchanged extra files | Baseline plan | Combined plan | Baseline commit | Combined commit |
|---:|---:|---:|---:|---:|
| 0 | 0.721 | 0.785 | 1.239 | 1.274 |
| 2,000 | 0.901 | 0.816 | 1.466 | 1.167 |

These exploratory runs overlapped other work, including this change's tests,
and were noisy. The small-fixture commit samples ranged from 0.870 to 1.928
seconds for the baseline and 0.960 to 1.835 for the candidate. An earlier
five-pair pilot with the host's Git launcher and inherited runtime HOME also
had mixed directions (small commit 0.491 → 0.432 seconds; larger commit
0.682 → 0.700). The script was then corrected to use the fixture HOME for both
versions. These results do **not establish an end-to-end speedup**. The shipped claim is fewer
Git processes and index traversals while retaining the same checks; deployment
latency should be measured on the receiver's workload. There is no flaky
wall-clock threshold in the tests.
