# One Checkout

Commit one reviewed change without disturbing other work in a shared Git checkout.

One Checkout is a Python command and an installable skill for **Claude Code and
Codex**. Give it a patch, the exact files it may change, and the commit that patch
was prepared against. It commits that patch while preserving other staged changes
and leaving working files untouched. You supply the ownership evidence; the tool
checks the Git operation.

**Requires:** Python 3.10+, Git 2.39+, macOS or Linux. The runtime uses only the
Python standard library and Git. It needs no server or model credentials.

[Install](#install-for-claude-code-and-codex) · [Ask your agent](#use-it-with-your-agent) ·
[Try the demo](#try-it-before-installing) · [Direct CLI](#review-and-commit-a-patch) ·
[Outcomes](#outcomes-and-recovery) · [Limits](#boundaries) ·
[Architecture and contributing](#verify-and-contribute) · [License](#license)

## Install for Claude Code and Codex

Use this when collaborators intentionally share a checkout and already have a
reviewed patch to commit. Use separate worktrees when they need independent
working files. For a single owner with no mixed changes, ordinary Git is usually
simpler. See [choosing a workflow](docs/choosing_a_workflow.md).

```sh
git clone --branch v0.1.0 --depth 1 https://github.com/wcook04/one-checkout.git
cd one-checkout
python3 scripts/install.py --agent both --dry-run
python3 scripts/install.py --agent both
```

Want to inspect the behaviour first? After cloning, run the
[disposable demo](#try-it-before-installing) before the install commands.

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

## Try it before installing

From the cloned source directory:

```sh
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
[staging review](docs/staging_handoff.md).

The demo also shows that native Git can build the same A-only commit. One Checkout
packages those primitives with scope, parent, branch, preservation and recovery
checks. This is a behaviour demonstration, not a productivity benchmark.

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

## License

[MIT](LICENSE), copyright One Checkout contributors.
