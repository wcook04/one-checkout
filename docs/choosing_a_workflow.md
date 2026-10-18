# Choose the workflow, not the slogan

Research checked on 13 October 2026. This is a bounded comparison, not a
claim that One Checkout is novel or better than every alternative. Source
pins, licences observed, and verification limits are in [prior_art.json](prior_art.json).

## When One Checkout earns its place

Use it when a cooperating harness already has an authorised, parent-relative
patch, must land it on a reviewed named branch or detached HEAD, and must retain another
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
| Git private-index plumbing | Can create an A-only commit and leave shared staging alone. [S02-S04; executable comparison] | A short recipe is not the complete lock, scope, HEAD-identity and recovery contract. |
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
`branch_policy_controls` separately compares an ordinary pre-commit branch
guard, One Checkout without admission, and a project-owned check of a saved
plan. Its 11 cases cover the primary checkout on `main` and a feature branch,
a linked feature worktree, and a same-OID branch switch after plan approval.
Both passing commits and refusals check refs, staged entries and working and
untracked bytes. A refused native commit can refresh index stat data; the
project admission and saved-plan refusal also require unchanged index bytes.

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

## Project-owned branch policy

One Checkout suppresses ordinary Git hooks so a transaction cannot silently run
arbitrary project commands or alter the candidate. This also means a pre-commit
branch guard does not protect its plumbing commit. The comparison includes that
bypass as a negative control; successful patch preservation alone does not make
the operation comply with the project's policy.

[agent-media PR153](https://github.com/southpenlabs/agent-media/pull/153), at
[`34f82b4a`](https://github.com/southpenlabs/agent-media/blob/34f82b4ab2210fae8ba1d1ef7b9499c58b6156d8/.githooks/pre-commit),
provides a concrete policy: allow `main` in the primary checkout and allow feature
branches in linked worktrees. That is the requirement source, not a claim that
its author contributed this integration, approved One Checkout or adopted it.
The comparison's hook is independently authored for this named-branch subset;
it does not copy the upstream hook or reproduce its detached-rebase exception.
One Checkout supports ordinary detached HEAD, but that does not make this
named-branch policy recipe admit detached rebase commits. Such an exception
belongs to the project's policy and sequencer workflow; it is not inferred here.

A project can apply that narrow policy to the saved plan, then use the existing
`--plan-file` binding. Replace these absolute paths and the selected file path;
the reviewed patch and plan belong outside the working checkout:

```sh
ONE_CHECKOUT=/absolute/path/to/one-checkout/skills/one-checkout/scripts/one_checkout.py
REPO=/absolute/path/to/project
PATCH=/absolute/path/to/reviewed.patch
PLAN=/absolute/path/to/reviewed-plan.json
PARENT=$(git -C "$REPO" rev-parse HEAD)
python3 "$ONE_CHECKOUT" plan --repo "$REPO" --patch "$PATCH" \
  --path src/file.py --expected-parent "$PARENT" > "$PLAN"
```

Review the patch, branch, parent, paths, candidate and project checks before
authorising the next command. Keep the reviewed plan and patch unchanged. This
example evaluates only branch metadata and runs no hook or candidate code:

```sh
python3 - "$ONE_CHECKOUT" "$REPO" "$PATCH" "$PLAN" <<'PY'
import json, pathlib, subprocess, sys
tool, repo, patch, plan_path = sys.argv[1:]
root = pathlib.Path(repo).resolve(strict=True)
plan = json.loads(pathlib.Path(plan_path).read_text(encoding="utf-8"))
def git_path(*args):
    return pathlib.Path(subprocess.check_output(
        ["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
         "-c", "core.hooksPath=", "-C", str(root), "rev-parse", *args],
        text=True).strip()).resolve(strict=True)
if pathlib.Path(plan["repository"]).resolve() != root:
    raise SystemExit("Plan repository differs; review a new plan.")
branch = plan.get("branch")
if (not isinstance(branch, str) or not branch.startswith("refs/heads/")
        or plan.get("head_mode", "attached") != "attached"):
    raise SystemExit("This project policy admits named branches only.")
primary = git_path("--absolute-git-dir") == git_path(
    "--path-format=absolute", "--git-common-dir")
if primary and plan["branch"] != "refs/heads/main":
    raise SystemExit("Primary checkout policy requires main; use a worktree.")
argv = [sys.executable, tool, "commit", "--repo", str(root), "--patch", patch,
        "--plan-file", plan_path, "--expected-parent", plan["parent"],
        "--message", "Apply reviewed change"]
for path in plan["changed_paths"]:
    argv.extend(["--path", path])
raise SystemExit(subprocess.run(argv, check=False).returncode)
PY
```

One Checkout validates the saved plan against the actual repository, branch,
parent, patch digest, paths and candidate tree. A switch to another branch with
the same parent OID still refuses. The example is a caller-owned admission
convention, not access control against someone bypassing it or changing its
reviewed artifacts. It neither grants authorisation nor infers branch intent,
prevents branch switching, isolates edits/builds or implements every project
hook. Keep code validation separate: this branch check says nothing about
whether the candidate passes tests or meets the project's other requirements.

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
