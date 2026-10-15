# One Checkout

Commit one agent's fix without including another agent's unfinished edits.

When Claude Code, Codex, or several sessions share one project folder, their edits
can get mixed together. One Checkout commits a **reviewed patch** while preserving
other staged changes and every working file. You decide which edits belong in it.

[Plectis website](https://wcook04.github.io/plectis/) ·
[Try the demo](#try-it-before-installing) · [Install](#install-for-claude-code-and-codex) ·
[Agent skill](skills/one-checkout/SKILL.md) · [Architecture](docs/architecture.md) ·
[Video plans](#videos)

Improved with public feedback from **Skitter** (staging warning and next-owner guide),
**Dione** (missing-patch prerequisite), **midearthguild** (locking and parent questions),
**lobsternigel** (the recovery comparison), **vina** (the staged/working-diff boundary
and executable next-commit reversal)
and **tanod** (the native post-commit staging example).
[Contributions and source reports](CONTRIBUTING.md).

## Try it before installing

Requires Python 3.10+, Git 2.39+, and macOS or Linux. No packages, server or model
credentials are needed.

```sh
git clone --depth 1 https://github.com/wcook04/one-checkout.git
cd one-checkout
python3 scripts/demo.py --human --compare-git
python3 scripts/try_install.py --human
```

Both commands use disposable fixtures. They neither install into your real home
nor launch an AI model. The second rehearses installation for both hosts at user
and project scope, including operation after its temporary source is removed.

The demo puts three changes in the **same file**: A is the reviewed patch,
B is another staged change, and C is an unstaged change.

| State | Before | After committing A |
|---|---|---|
| Latest commit (HEAD) | Base | Base + A |
| Staged snapshot | Base + B | Base + B |
| Working file | Base + A + B + C | Base + A + B + C |

**Review the preserved staging before the next commit.** Its snapshot can still
lack A, so a later ordinary `git commit` can reverse A. Preservation does not
rebase B onto A. The result identifies paths needing
[staging review](docs/staging_handoff.md). The demo checks that its receipt emits
the divergence warning. With `--compare-git`, a separate disposable fixture
actually makes that ordinary next commit and shows A being reverted; JSON saves
the two snapshots and commit IDs in `follow_on_control`.

The demo also shows that native Git can build the same A-only commit. One Checkout
packages those primitives with scope, parent, branch, preservation and recovery
checks. This is a behaviour demonstration, not a productivity benchmark.

## The problem

Imagine three tasks have changed the same file, `auth.py`:

| Work in the file | Ready to commit? |
|---|---|
| Claude fixed the expired-token check | Yes: you reviewed this fix |
| Codex started a permissions refactor | No: it is half-finished, with some edits already staged |
| You added temporary debugging output | No: keep it local |

You want a commit containing **only the token fix**. The refactor and debugging
output need to stay exactly where they are so everyone can keep working.

Git has one shared **staging area**—the changes prepared for the next commit—in
that project folder. It does not maintain a separate staging area for each agent
or know which task an edit belongs to. `git add .` can gather everyone's changes.
Selecting just `auth.py` still includes multiple tasks, and an ordinary
`git commit` includes whatever is already staged. Stashing or resetting work to
get it out of the way changes the files or staging that other sessions are using.

This is the problem One Checkout addresses: **making one specific commit from a
folder that contains several people's or agents' unfinished changes.**

## What One Checkout does

You or your agent prepare a patch containing exactly the change you intend to
commit. One Checkout applies it to the captured starting commit in a separate
Git index, checks the allowed paths, and commits that candidate. If the branch
has moved, it refuses so you can review the new situation.

The working files keep all their edits. Other people's staged snapshots stay
staged. **Review the staging handoff:** a preserved snapshot can lack the fix you
just committed, so a later ordinary commit could undo it. Staged and working
diffs can therefore differ after a successful commit. The
[handoff guide](docs/staging_handoff.md) explains the comparisons and the next
writer's review, including a native-Git example.

The package contains a small Python command and an installable skill for Claude
Code and Codex. It uses Git's existing mechanisms, with explicit scope, parent,
preservation and recovery checks.

It cannot infer authorship from a mixed file, stop writers overwriting each
other, or make unfinished code correct. If nobody retained a separate patch,
prepare and review one without removing other edits from the shared files; see
[patch selection](skills/one-checkout/references/patch-selection.md).
For independent editing environments, use Git worktrees. For a single owner's
ordinary commit, normal Git is usually simpler. See
[choosing a workflow](docs/choosing_a_workflow.md).

## Install for Claude Code and Codex

**Requires:** Python 3.10+, Git 2.39+, macOS or Linux. The runtime uses only the
Python standard library and Git. It needs no server or model credentials.

The demo above follows current `main`. Install the tagged `v0.1.0` release
from a separate clone. If you just ran the demo, use `cd ..` first to return to
its parent directory; the two source folders can then sit beside each other:

```sh
git clone --branch v0.1.0 --depth 1 https://github.com/wcook04/one-checkout.git one-checkout-v0.1.0
cd one-checkout-v0.1.0
python3 scripts/install.py --agent both --dry-run
python3 scripts/install.py --agent both
```

The [installation guide](docs/installation.md) also covers upgrades and removal.

The installer copies a complete skill into:

- **Codex:** `~/.agents/skills/one-checkout`
- **Claude Code:** `~/.claude/skills/one-checkout`

Choose one host with `--agent codex` or `--agent claude`. Add
`--scope project --project /absolute/path/to/project` for a project installation.
A differing existing copy is refused; review the update and use
`--backup-existing` to retain it before replacement. See
[installation, upgrades and removal](docs/installation.md).

Start a fresh agent session or reload skills. The copied skill works without
this source checkout. Installation does not change agent permissions: the host
must permit Git metadata and external receipt-file writes. The
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

After reviewing the successful plan and testing its exact candidate:

> Commit that same reviewed patch using the saved plan, with message
> “Describe the change”. Preserve unrelated staging and all working files.
> Report the result; stop on refusal or uncertainty.

The expected parent is a commit in **your target project**, not the version of
One Checkout. For mixed hunks or new/deleted files, follow
[patch selection](skills/one-checkout/references/patch-selection.md). Never remove
other people's edits from the shared checkout just to manufacture a patch.

## Review and commit a patch

The CLI works directly from source as well as from an installed skill. It is a
Python script; installation does not add an executable to `PATH`.

<details>
<summary>Direct CLI example: plan, review, then commit</summary>

Start with an **already reviewed patch**, its original target-repository parent
and exact file scope. Replace the example values. Keep artifacts outside the
checkout in a fresh directory:

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
python3 "$TOOL" plan --repo "$REPO" --patch "$PATCH" \
  --path src/example.py --expected-parent "$PARENT" \
  > "$ARTIFACTS/plan.json" 2> "$ARTIFACTS/plan.stderr"
plan_exit=$?
printf 'Plan exit: %s\n' "$plan_exit"
cat "$ARTIFACTS/plan.json" "$ARTIFACTS/plan.stderr"
```

**Stop unless the plan exits 0.** Review its branch, parent, paths and staging
warnings, and run required checks on the exact candidate. A plan is not
permission to commit. Once authorised, in the same shell:

```sh
python3 "$TOOL" commit --repo "$REPO" --patch "$PATCH" \
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
candidate tree. It reserves nothing: commit checks the live state again.

</details>

## Outcomes and recovery

To compare caller knowledge with actual Git effects, run the source-checkout
example `python3 scripts/recovery_demo.py --human`. It injects faults around real
Git in four disposable repositories: an undelivered request, lost acknowledgement
after delivery, failed index settlement after confirmed publication, and success.
Both uncertain cases have a candidate commit object; only one has that commit on
the branch. The comparison checks one commit request per case, disabled automatic
retry, and repeated recovery that leaves repository and record bytes unchanged.
These are sequential fault fixtures, not a scheduler or proof that a missing
branch/reflog observation permits retry. See [transaction safety](docs/transaction_safety.md).


Keep the JSON result, stderr and exit status. The result describes what happened;
it is not a guarantee that every later operation succeeded.

| Exit | What to do |
|---:|---|
| **0** | Read the result: the plan, confirmed commit, or read-only recovery inspection completed. |
| **2** | Stop and review the refusal or plan-output error. Do not substitute a new parent to force the patch through. |
| **3** | The commit may have landed, or a later step/output delivery failed. Inspect; **never blindly retry**. |

`landing_uncertain` names a candidate without confirming publication.
`landed_unsettled` confirms publication but reports later problems. Output failure
can leave a fallback result on stderr; missing JSON is not proof of failure.
The optional `--record-file` retains phase evidence, and `recover --record-file`
inspects it without changing Git state. See the
[recovery guide](skills/one-checkout/references/recovery.md).

## Locking and parent checks

**Does it lock the shared index?** A real commit acquires `index.lock`
exclusively and tracks its inode. It refuses an existing lock and never removes
a replacement writer's lock. The selected patch is applied through a separate
private index. Git's native prepared `update-ref` transaction protects HEAD and
the resolved branch during publication. A plan does not reserve locks or state.

**Where does the expected parent come from?** The caller supplies a full commit
ID with `--expected-parent`; it is not taken from a patch header. The commit
checks live HEAD against that captured parent, rechecks branch identity and the
index while preparing, and uses Git's ref transaction to publish only against
the expected parent. If the checkout has moved, review the patch again rather
than substitute a new parent merely to make it pass.

These checks assume cooperating Git writers. Working files are not locked, and
ref publication plus index settlement are separate steps. Inspect an uncertain
or unsettled result before retrying. See [transaction safety](docs/transaction_safety.md).

## Boundaries

- **Shared files are not isolated.** This command never writes or locks working
  files, infers ownership, resolves semantic conflicts, or pushes a branch.
- **Project checks remain your responsibility.** Hooks are disabled per invocation;
  run required policy checks and test the exact candidate separately. Configured
  commit signing is honoured.
- **Staging is preserved selectively.** Selected entries that are unchanged from
  the parent and have no semantic flags advance to the candidate. Foreign or flagged
  staged entries retain their snapshot; unrelated entries are preserved.
- **Use trusted repositories and cooperating writers.** Named branches and full
  indexes are required. Split, sparse, unmerged and partial/promisor repositories
  are refused. Branch publication and shared-index replacement are separate steps;
  crashes, hostile writers and storage failures are not made atomic.

Read [transaction safety](docs/transaction_safety.md) and
[security reporting](SECURITY.md) for the full boundary.

## Videos

| Film | Status | What it introduces |
|---|---|---|
| [Introduction](https://wcook04.github.io/plectis/#film-introduction) | Coming soon | The wider Plectis project and its tools for inspecting agent work. |
| [One Checkout walkthrough](https://wcook04.github.io/plectis/#film-one-checkout) | Coming soon | Choosing a patch, preserving unfinished edits and reviewing the staging handoff. |

These are reserved sections on the project website. The films are being prepared;
no video is published at these links yet.

## Verify and contribute

The [release](https://github.com/wcook04/one-checkout/releases/tag/v0.1.0) links its
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

The architecture has **one runtime and one installable bundle**. The installer
copies it to each host; development tools and repository docs stay in the source
checkout. The [architecture](docs/architecture.md) and
[repository guide](docs/repository_guide.md) explain the ownership boundaries.

| Source | Responsibility |
|---|---|
| [Runtime](skills/one-checkout/scripts/one_checkout.py) | Git validation, candidate preparation, publication, staging and result delivery |
| [Installed skill](skills/one-checkout/SKILL.md) and [bundle](skills/one-checkout/bundle.json) | Agent workflow and complete copied payload |
| [Installer](scripts/install.py) and [demo](scripts/demo.py) | Distribution and runnable first-use example |
| [tests/](tests/) and [scripts/](scripts/) | Regression checks, comparisons, stress and evaluation tools |
| [Layout policy](docs/repository_layout.json) | Enforced directory roles and required interfaces |

Questions and bug reports belong in [GitHub issues](https://github.com/wcook04/one-checkout/issues);
PRs are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and report suspected
vulnerabilities through [SECURITY.md](SECURITY.md). Maintainer:
[wcook04](https://github.com/wcook04). Code and documentation were prepared with
AI assistance under human direction; [provenance](docs/provenance.md) records prior art.

## Where One Checkout fits

Plectis is Will Cook’s programme of research and software built with AI agents. The aim is to make the resulting work understandable enough for other people to inspect, question and build on. One Checkout handles a practical part of agent collaboration: landing a reviewed patch without including unfinished work.

- [Safety Core](https://github.com/wcook04/safety-core) provides editable agent rules, hooks and a map of where checks apply.
- [Plectis toolkit](https://github.com/wcook04/plectis) contains earlier Python components and worked examples; [explore its software map](https://wcook04.github.io/plectis/docs/system-map.html).
- [Plectis Zenith](https://github.com/wcook04/plectis-zenith) studies executable investigations, measured code repair and reusable testers; [read the research](https://wcook04.github.io/plectis-zenith/).

See the [programme directory](https://github.com/wcook04/plectis-erdos#the-wider-plectis-programme) for the full set of projects, or start at the [Plectis website](https://wcook04.github.io/plectis/).

## License

[MIT](LICENSE), copyright One Checkout contributors.
