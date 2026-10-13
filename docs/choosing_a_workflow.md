# Choose the workflow, not the slogan

Research checked on 13 October 2026. This is a bounded comparison, not a
claim that One Checkout is novel or better than every alternative. Source
pins, licences observed, and verification limits are in [prior_art.json](prior_art.json).

## When One Checkout earns its place

Use it when a cooperating harness already has an authorised, parent-relative
patch, must land it on an ordinary named branch, and must retain another
collaborator's staged snapshot without writing the shared working files.
Patch selection, authorisation and project validation are prerequisites,
not features supplied by this command.

A shared expensive development environment can make this useful, but this
comparison does not measure build-cache or environment savings. Preventing
concurrent edits to the same bytes still requires coordination outside the tool.

Prefer simpler Git porcelain for one owner or independently owned files.
Prefer worktrees for independent working directories, incompatible dependencies,
different revisions, untrusted code execution or isolation of edits. A
worktree alone is not a security sandbox or complete runtime isolation.
Prefer an existing workspace product when its branch assignment, review and
recovery model is what the team actually needs.

## Closest comparisons

| Choice | Established overlap | What has not been established here |
|---|---|---|
| Git pathspec commit | Excludes unrelated indexed paths; selects working-file contents on the named path. [S01] | A pathspec is not a same-file A-only patch selector. |
| Git private-index plumbing | Can create an A-only commit and leave shared staging alone. [S02-S04; executable comparison] | A short recipe is not the complete lock, scope, named-branch and recovery contract. |
| Git worktrees / Worktrunk | Separate working trees and parallel-agent workflow management. [S08] | Worktrunk was not executed; a raw Git worktree result has not been integrated into the original branch. |
| Agentlocks | The inspected helper leases paths/index and uses path-scoped `git add` and `git commit`. [S07] | No patch/expected-parent interface was found in that helper; this is not a claim about every interface or future version. |
| GitButler | One workspace for parallel agents; explicit file or hunk selection and target branches. [S11-S12] | Equivalence to this tool's exact ordinary-index preservation and recovery contract needs an executed adapter. |
| Foremerge | Intent/conflict coordination and fingerprint-bound validation/acceptance. [S09-S10] | It was inspected, not benchmarked; it addresses more than this local patch-landing operation. |

GitButler is material prior art. Do not advertise “first shared-checkout
partial commits”. The observed main-repository licence is FSL-1.1-MIT,
not this project's MIT licence. No GitButler implementation code is copied.
[S13] Other source links are attribution and comparison, not endorsement.

## Reproduce the narrow comparison

From the source root, with Git and Python:

```sh
python3 scripts/compare_workflows.py --repeats 3 > ../one-checkout-comparison.json
```

All work happens in disposable repositories. `--tool` can select a copied
One Checkout runtime. `ONE_CHECKOUT_TEST_TMPDIR` and `TMPDIR` select scratch
locations. There is no live-repository argument. The four arms receive an
already selected patch or selected file; no model is asked to infer ownership.

The mixed fixture has A, staged B and unstaged C in one file. A second
independent-files fixture prevents a misleading claim that pathspec Git
commits always contaminate unrelated staging. Additional controls exercise
an ordinary follow-on commit, a reviewed second patch, a stale parent and
a dependency-incomplete candidate.

`status: passed` means the stated observations matched their oracles. It
does **not** mean every workflow met the isolation requirement: the ordinary
pathspec arm deliberately commits A+B+C in the mixed-file control. The
negative candidate control deliberately records a broken candidate while
its mixed working file runs successfully.

The worktree arm produces an isolated branch, not a merged shared-branch
result. Recorded timings are local operation timings, excluding fixture
creation and patch selection; they are not comparable productivity scores.
Agentlocks, Worktrunk, GitButler and Foremerge binaries are not invoked.

## The hand-off is part of the contract

After A lands, a retained B snapshot can still omit A. An ordinary next
`git commit` can undo A. Read [staging_handoff.md](staging_handoff.md).
Do not hide the cost of resolving this state when evaluating the workflow.

For automatic systems, test the exact candidate tree, not the shared
working directory. A future validation adapter should bind its result to
the parent, patch digest, tree, command policy and execution environment.
A stale result must not authorise landing a different candidate. Foremerge's
fingerprint-bound validation and recent runnable-commit research are useful
prior art, not an implementation bundled here. [S09, S14]

## Evidence that would change the recommendation

Choose an existing tool instead if its pinned version satisfies the same
end-to-end contract with lower setup, correction and recovery effort.
Retain One Checkout as a small adapter only if the ordinary-index contract
is genuinely needed. If normal Git commits after a hand-off remain common,
prefer isolation or an explicit, separately designed reconciliation workflow.

External evaluation should include real host discovery, exact runtime
identity, both agents' final requirements, candidate-only checks, mid-operation
interruptions and total time through the second commit. Recent coordination
and skill-conflict studies support measuring integration and skill fidelity,
not merely an agent's “done” message. Their results do not establish this
package's effectiveness. [S15-S17, S22-S23]

## Candidate-validation adapter design

The executable dependency counterexample in `compare_workflows.py` demonstrates
why testing the mixed checkout is insufficient. An optional adapter belongs
outside the commit kernel; project commands and execution permissions are policy,
not properties Git can infer.

A future adapter must accept an explicitly trusted argv/environment policy and
a saved plan, then create a disposable candidate checkout without changing the
shared index or working files. Its receipt must bind: repository/branch, parent,
patch digest, exact paths, candidate tree, canonical command-policy digest, tool
version, environment identity, result/exit code and logs. The candidate tree must
be checked before execution and again before issuing the receipt. Generated test
artifacts must not become candidate source accidentally. The runtime's later
plan binding must still refuse drift; a passing test receipt is not permission.

Required countercases are a candidate missing a dependency present in the mixed
checkout, changed patch/parent/tree after validation, changed command policy,
missing dependencies, timeout, test failure, modified candidate source, and a
stale result replay. Use a trusted external execution boundary when candidate
code is untrusted; a disposable clone alone is not a sandbox. A general adapter
is deliberately not shipped until that policy/execution boundary is chosen and
tested. The current comparison exposes the hazard rather than granting false
assurance through a generic shell hook.
