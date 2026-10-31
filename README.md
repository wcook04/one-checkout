# One Checkout

One Checkout saves a selected code change in Git while leaving other unfinished
edits in the same project folder. A saved Git version is called a **commit**.

Suppose Claude Code fixes a login bug while Codex is halfway through a
permissions change in the same file. You want to commit the login fix without
including or removing the unfinished work. You supply a **patch**—a file listing
the exact changes you have selected and reviewed—and One Checkout makes that
commit. It does not decide which edits belong to each task.

Git's **staging area** contains the version prepared for the next commit.
One Checkout preserves unrelated staged edits and all working files. Review
that preserved staging afterwards: if it still contains the old code, the next
ordinary commit could undo the fix. The demo shows this happening.

[Try the demo](#try-it-before-installing) · [Install](#install-for-claude-code-and-codex) ·
[Watch the walkthrough](https://www.youtube.com/watch?v=MHBxGPLDqt4) ·
[Use it with an assistant](#use-it-with-your-agent) · [Source map](#verify-and-contribute) ·
[Will Cook's projects](https://wcook04.github.io/)

## Try it before installing

Requires Python 3.10+, Git 2.39+, and macOS or Linux. No packages, server or model
credentials are needed.

```sh
git clone --depth 1 https://github.com/wcook04/one-checkout.git
cd one-checkout
python3 scripts/demo.py --human --compare-git
python3 scripts/try_install.py --human
```

Both commands create temporary example projects. They do not install into your
actual assistant settings or call an AI model. The second tests installation
for Claude Code and Codex, both for one project and for all your projects. It
also checks that the installed copy works after the temporary source is removed.

The demo puts three changes in the **same file**: A is the reviewed patch,
B is another staged change, and C is an unstaged change.

| State | Before | After committing A |
|---|---|---|
| Latest saved version (HEAD) | Base | Base + A |
| Version in the staging area | Base + B | Base + B |
| Working file | Base + A + B + C | Base + A + B + C |

**Review the preserved staging before the next commit.** Its snapshot can still
lack A, so a later ordinary `git commit` can reverse A. Keeping B unchanged does not combine it with A. The result identifies paths needing
[staging review](docs/staging_handoff.md). The demo checks that its saved result warns about this difference. With `--compare-git`, a separate temporary project
actually makes that ordinary next commit and shows A being reverted. Omit `--human` to print the detailed result as
JSON, text with named fields. Its `follow_on_control` field contains the two
versions and their commit identifiers.

<details>
<summary>Preview the demo's checked output</summary>

Excerpt from `python3 scripts/demo.py --human --compare-git`:

```text
PASS commit_contains_only_A
PASS staged_B_blob_unchanged
PASS working_A_B_C_unchanged
```

The comparison then makes an ordinary next commit in a separate temporary
project. A returns to zero while B is committed:

```text
Before: A = 1; B = 0; C = 0
After:  A = 0; B = 2; C = 0
```

The full output also checks that the command refuses a patch prepared against
an outdated starting version, and compares three ways to commit with Git. Without `--human`, the command returns JSON for inspection.

</details>

The same selected commit can be made with Git commands. One Checkout adds
checks that the patch changes only the specified files, still applies to the
reviewed starting version and leaves other edits in place. The demo tests those
behaviours; it does not measure whether the tool saves time. See the separate
[performance experiment](docs/performance.md) for timings and their limits.

## Can I use it today?

The command-line tool and installable instructions for Claude Code and Codex
work on macOS and Linux. Tests use temporary repositories to exercise patch
selection, preserved edits, refusals, installation and recovery.
[Outside test reports](CONTRIBUTING.md) are recorded separately from use on a
real project's changes. Discovery and use of the instructions by live AI
assistants across concurrent sessions remain experimental; these tests do not
establish independent project adoption.

<a id="the-problem"></a>

## What One Checkout does

You supply a reviewed patch, the exact file paths it may change, and the full
identifier of its starting commit. That starting commit is called the
**parent**. The tool prepares a proposed commit without changing the working
files. You inspect its plan and run your project's checks before authorizing it.
If the starting version or commit destination changes, the tool refuses the
commit so you can review the new situation.

It cannot select your edits, prevent two writers overwriting each other, or
make unfinished code correct. It also does not reserve the project while you
review a plan. If you need separate editing folders, use
[Git worktrees](docs/choosing_a_workflow.md). For an ordinary commit containing
one person's work, normal Git is usually simpler.

**Git hooks are disabled for this operation.** If your project uses a hook to
run tests or enforce a branch rule before committing, run the required check
separately on the proposed commit. The
[workflow guide](docs/choosing_a_workflow.md) gives an example. See
[patch selection](skills/one-checkout/references/patch-selection.md) for preparing
a patch and [staging handoff](docs/staging_handoff.md) for reviewing what remains.

## Install for Claude Code and Codex

**Requires:** Python 3.10+, Git 2.39+, macOS or Linux. The runtime uses only the
Python standard library and Git. It needs no server or model credentials.

The demo above uses the current development version on `main`. To
install that source version, record `git rev-parse HEAD` in the demo clone and
run `python3 scripts/install.py --agent both --dry-run`, then the same command
without `--dry-run` after reviewing the destination.

The tagged `v0.3.0` release includes the tool, demos and assistant instructions. Later commits on `main` may differ; its
[release record](https://github.com/wcook04/one-checkout/releases/tag/v0.3.0)
names the exact tested commit. To install that release, use a separate clone.
If you just ran the demo,
use `cd ..` first to return to its parent directory; the two source folders can
then sit beside each other:

```sh
git clone --branch v0.3.0 --depth 1 https://github.com/wcook04/one-checkout.git one-checkout-v0.3.0
cd one-checkout-v0.3.0
python3 scripts/install.py --agent both --dry-run
python3 scripts/install.py --agent both
```

The [installation guide](docs/installation.md) also covers upgrades and removal.

The installer copies the Python tool and a **skill**, a set of instructions
for an AI assistant, into:

- **Codex:** `~/.agents/skills/one-checkout`
- **Claude Code:** `~/.claude/skills/one-checkout`

Install for just one assistant with `--agent codex` or `--agent claude`. Add
`--scope project --project /absolute/path/to/project` for a project installation.
A differing existing copy is refused; review the update and use
`--backup-existing` to retain it before replacement. See
[installation, upgrades and removal](docs/installation.md).

Start a fresh agent session or reload skills. The copied skill works without
this source checkout. Installation does not change agent permissions: the host
must permit Git metadata and writing result files outside the project. The
[installation guide](docs/installation.md) also covers host-injected Git variables.

## Use it with your agent

First select and review the patch. A filename or “commit my work” does not tell
an agent which mixed edits belong to you. Replace the paths and SHA below with
those from your reviewed change:

> Use the installed one-checkout skill to plan a commit of
> `/outside/project/owned.patch` in `/path/to/project`. I reviewed this patch;
> its only authorised changes are to `src/example.py`, against target-repository
> parent `<full commit SHA>`. Save the plan outside the checkout and show me
> the branch, selected paths and staging warnings. Do not commit yet.

After reviewing the successful plan and testing the exact proposed version:

> Commit that same reviewed patch using the saved plan, with message
> “Describe the change”. Preserve unrelated staging and all working files.
> Report the result; stop on refusal or uncertainty.

The expected parent is a commit in **your target project**, not the version of
One Checkout. For mixed hunks or new/deleted files, follow
[patch selection](skills/one-checkout/references/patch-selection.md). Never remove
other people's edits from the shared checkout just to manufacture a patch.

## Review and commit a patch

You can also run the command directly, without an AI assistant. It is a Python
script; installation does not add an executable to `PATH`.

<details>
<summary>Terminal commands: plan, review, then commit</summary>

Start with an **already reviewed patch**, its starting commit in the target
project and the exact files it changes. Replace the example values. Save the
plan and results in a fresh directory outside the project:

The example uses the Codex user copy. Set `TOOL` to the source clone's absolute
`skills/one-checkout/scripts/one_checkout.py` path for use without installation,
or to `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/one-checkout/scripts/one_checkout.py"`
for the Claude Code user copy.

```sh
TOOL="$HOME/.agents/skills/one-checkout/scripts/one_checkout.py"
REPO=/absolute/path/to/project
PATCH=/absolute/path/to/owned.patch
PARENT=full_commit_SHA_the_patch_was_prepared_against
ARTIFACTS=$(cd "$(mktemp -d)" && pwd -P)
env -u GIT_EDITOR python3 "$TOOL" plan --repo "$REPO" --patch "$PATCH" \
  --path src/example.py --expected-parent "$PARENT" \
  > "$ARTIFACTS/plan.json" 2> "$ARTIFACTS/plan.stderr"
plan_exit=$?
printf 'Plan exit: %s\n' "$plan_exit"
cat "$ARTIFACTS/plan.json" "$ARTIFACTS/plan.stderr"
```

**Stop unless the plan exits 0.** Review its branch, parent, paths and staging
warnings, and run required checks on the exact proposed version. A plan is not
permission to commit. Once authorised, in the same shell:

```sh
env -u GIT_EDITOR python3 "$TOOL" commit --repo "$REPO" --patch "$PATCH" \
  --path src/example.py --expected-parent "$PARENT" \
  --plan-file "$ARTIFACTS/plan.json" --record-file "$ARTIFACTS/operation.jsonl" \
  --message "Describe the authorised change" \
  > "$ARTIFACTS/commit.json" 2> "$ARTIFACTS/commit.stderr"
commit_exit=$?
printf 'Commit exit: %s\n' "$commit_exit"
cat "$ARTIFACTS/commit.json" "$ARTIFACTS/commit.stderr"
```

Repeat `--path` for every exact file; globs and directories are
refused. A saved plan binds the repository, branch, parent, patch, paths and
proposed file contents. It reserves nothing: commit checks the live state again.

</details>

## Outcomes and recovery

If the operation is interrupted, the commit may have succeeded even when no
success response arrived. The
[recovery guide](skills/one-checkout/references/recovery.md) explains how to
inspect the saved result without changing Git state. To see simulated failures
and recovery in temporary repositories, run
`python3 scripts/recovery_demo.py --human`.

Keep the JSON result, stderr and exit status. The result describes what happened;
it is not a guarantee that every later operation succeeded.

| Exit | What to do |
|---:|---|
| **0** | Read the result: the plan, confirmed commit, or read-only recovery inspection completed. |
| **2** | Stop and review the refusal or plan-output error. Do not substitute a new parent to force the patch through. |
| **3** | The commit may have landed, or a later step/output delivery failed. Inspect; **never blindly retry**. |

`landing_uncertain` identifies a proposed commit without confirming that it became the current commit.
`landed_unsettled` confirms publication but reports later problems. Output failure
can leave a fallback result on stderr; missing JSON is not proof of failure.
The optional `--record-file` retains phase evidence, and `recover --record-file`
inspects it without changing Git state. See the
[recovery guide](skills/one-checkout/references/recovery.md).

## Locking and parent checks

During a commit, the tool locks the shared staging area and uses Git's own
transaction mechanism to check and update the commit destination. It refuses
an existing lock and does not remove another writer's replacement lock.
The working files are not locked.

The parent identifier must come from the project you are changing. The tool
also checks the destination: a named branch, or a **detached HEAD**, where Git
points directly to a commit without a branch name. Matching starting commit
identifiers do not make different destinations interchangeable. A detached
commit does not advance a named branch; you remain responsible for keeping it
reachable.

Updating the branch and updating shared staging are separate steps. A failure
between them needs inspection, not an automatic retry. The
[transaction guide](docs/transaction_safety.md) explains the sequence and limits.

## Boundaries

- **Shared files are not isolated.** This command never writes or locks working
  files, infers ownership, resolves semantic conflicts, or pushes a branch.
- **Project checks remain your responsibility.** Hooks are disabled per invocation;
  run required policy checks and test the exact proposed version separately. Configured
  commit signing is honoured.
- **Staging is preserved selectively.** Selected entries that are unchanged from
  the parent and have no special Git index flags advance to the new version. Foreign or flagged
  staged entries retain their snapshot; unrelated entries are preserved.
- **Use trusted repositories and cooperating writers.** An existing commit and a full
  index are required; named branches and detached HEAD are supported. Split, sparse,
  unmerged and partial/promisor repositories are refused. Ref publication and
  shared-index replacement are separate steps; crashes, hostile writers and
  storage failures are not made atomic.

Read [transaction safety](docs/transaction_safety.md) and
[security reporting](SECURITY.md) for the full boundary.

## Videos

The [One Checkout walkthrough](https://www.youtube.com/watch?v=MHBxGPLDqt4)
follows a login fix from a mixed working file into its own commit, then explains
why another agent must review the old staged copy before committing again.

| In the video | Try or inspect it here |
|---|---|
| Save the selected fix and leave the other edits in place | [Disposable demo and Git comparison](#try-it-before-installing) |
| Avoid undoing that fix in the next commit | [Staging handoff](docs/staging_handoff.md) |
| Prepare and test the proposed commit | [Patch review workflow](#review-and-commit-a-patch) and [agent skill](skills/one-checkout/SKILL.md) |

The [Plectis introduction](https://www.youtube.com/watch?v=MBmVMMogXTA)
places this tool alongside the other maths and software projects.

## Verify and contribute

The [release](https://github.com/wcook04/one-checkout/releases/tag/v0.3.0) links its
exact tested commit and results. [CI](.github/workflows/ci.yml) exercises Linux and
macOS, installation, native Git backends and these local checks:

```sh
python3 -B -I -m unittest discover -s tests -v
python3 scripts/check_layout.py --root .
python3 scripts/check_layout.py --root . --index
```

Tests use synthetic repositories. Set `TMPDIR` and `ONE_CHECKOUT_TEST_TMPDIR` to
existing scratch directories if needed. See [release checks](docs/release_checks.md),
[agent evaluation](docs/agent_evaluation.md) and [stress testing](docs/stress_testing.md).

There is **one copy of the Python tool and assistant instructions in the source**.
The installer copies them to each assistant; development tools and repository docs stay in the source
checkout. The [architecture](docs/architecture.md) and
[repository guide](docs/repository_guide.md) explain the ownership boundaries.

| Source | Responsibility |
|---|---|
| [Runtime](skills/one-checkout/scripts/one_checkout.py) | Checks the Git state, prepares the selected commit, saves it and reports the outcome |
| [Installed skill](skills/one-checkout/SKILL.md) and [bundle](skills/one-checkout/bundle.json) | Assistant instructions and the list of files copied by the installer |
| [Installer](scripts/install.py) and [demo](scripts/demo.py) | Distribution and runnable first-use example |
| [tests/](tests/) and [scripts/](scripts/) | Regression checks, comparisons, stress and evaluation tools |
| [Evaluation guide](docs/evaluate.md) | Disposable first run, demonstration evidence and fresh-clone checks |
| [Benchmark](scripts/benchmark.py) and [performance notes](docs/performance.md) | Paired runtime timings, setup/verification costs and limits |
| [Layout policy](docs/repository_layout.json) | Enforced directory roles and required interfaces |

Questions and bug reports belong in [GitHub issues](https://github.com/wcook04/one-checkout/issues);
PRs are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and report suspected
vulnerabilities through [SECURITY.md](SECURITY.md). Maintainer:
[wcook04](https://github.com/wcook04). Code and documentation were prepared with
AI assistance under human direction; [provenance](docs/provenance.md) records prior art.

## Where One Checkout fits

One Checkout grew out of work with several coding-agent sessions in the
same folder. It handles the commit step: selecting the patch, coordinating
edits and checking the proposed version remain the caller’s work. It is a standalone
tool within Will Cook’s Plectis research and software projects.

- [Safety Core](https://github.com/wcook04/safety-core) provides instructions and automatic checks for AI coding assistants, with a map of which files they apply to.
- [Plectis toolkit](https://github.com/wcook04/practical-formal-verification) contains earlier Python components and worked examples; [explore its software map](https://wcook04.github.io/practical-formal-verification/docs/system-map.html).
- [Plectis Zenith](https://github.com/wcook04/plectis-zenith) tests whether records of earlier mistakes help an AI model find bugs in new programs. Its README separates the experiment shown in the video from earlier repair and tester studies.

Explore the [research corpus](https://github.com/wcook04/plectis-erdos), or browse
[Will Cook’s projects](https://wcook04.github.io/) for the wider collection.

## License

[MIT](LICENSE), copyright One Checkout contributors.
