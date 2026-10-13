# One Checkout

Commit an explicit patch from a shared Git checkout while preserving other
staged snapshots and working files. Give it a reviewed patch, exact paths and
an expected parent; it builds that commit through Git's private-index and
conditional-ref primitives. It does not infer which changes belong to you.

Python 3.10+, Git 2.39+, macOS or Linux. Standard library only: no package
manager, server or model credentials needed for the runtime.

Get the versioned source:

```sh
git clone --branch v0.1.0 --depth 1 https://github.com/wcook04/one-checkout.git
cd one-checkout
git rev-parse HEAD
```

## Try it before installing

From a reviewed source checkout or source archive:

```sh
python3 scripts/demo.py --human --compare-git
python3 scripts/try_install.py --human
```

The demo supplies A alone while another change B is staged and C is unstaged
in the same file. It checks actual Git state in a disposable repository:

| State | Before | After committing A |
|---|---|---|
| HEAD | Base | Base + A |
| Staged snapshot | Base + B | Base + B |
| Working file | Base + A + B + C | Base + A + B + C |

The capable native-Git private-index control also commits A alone. One Checkout
packages that operation with explicit scope, parent and branch checks, index
preservation and outcome reporting. This demonstration is not a productivity
benchmark. See [the demo](docs/demo.md) and [workflow choice](docs/choosing_a_workflow.md).

**Preserving B is not rebasing it onto A.** The retained staged snapshot may
lack A; an ordinary next commit of that index can reverse A. The receipt identifies
selected paths needing review. Read [staging handoff](docs/staging_handoff.md)
before the next commit. Working-file isolation still needs separate worktrees.

## Install for Claude Code and Codex

From the source directory above:

```sh
python3 scripts/install.py --agent both --dry-run
python3 scripts/install.py --agent both
```

The installer copies one self-contained skill into `~/.agents/skills/one-checkout`
for Codex and `~/.claude/skills/one-checkout` for Claude Code. For a project,
add `--scope project --project /absolute/path/to/project`. It refuses differing
existing copies unless `--backup-existing` is given. Removal with `--uninstall`
retains a recoverable backup. See [installation](docs/installation.md) for profiles,
updates, partial failures and source identity.

Verify the skill's source location in the agent and ask it to use `one-checkout`.
Reload skills or start a fresh session if the host has not detected it. Use one
installation route per host/scope to avoid duplicate skills. The installer does
not change agent permissions or global instructions.

Source and versioned releases are at [wcook04/one-checkout](https://github.com/wcook04/one-checkout).
The root [portable manifest](plugin.json) and
[Claude manifest](.claude-plugin/plugin.json) describe plugin packaging.
Copied skills are the tested installation route; no marketplace is supplied.

## Review and commit a patch

Establish the authorised change before producing a patch. If one file contains
mixed work, use recorded edit provenance or reconstruct the authorised change in
a disposable clone. Do not temporarily remove other people's edits from the
shared checkout. [Patch selection](skills/one-checkout/references/patch-selection.md)
covers mixed hunks, additions and deletions.

This example assumes **all changes in the selected file are authorised**. Save
artifacts outside the checkout, with a fresh directory for each attempt:

```sh
TOOL="$HOME/.agents/skills/one-checkout/scripts/one_checkout.py"
REPO=/absolute/path/to/project
ARTIFACTS=$(mktemp -d)
PARENT=$(git -C "$REPO" rev-parse HEAD)
git --no-replace-objects --literal-pathspecs -C "$REPO" diff \
  --no-ext-diff --no-textconv --binary "$PARENT" -- src/example.py > "$ARTIFACTS/owned.patch"
# Review owned.patch and the authorised branch before proceeding.
python3 "$TOOL" plan --repo "$REPO" --patch "$ARTIFACTS/owned.patch" \
  --path src/example.py --expected-parent "$PARENT" > "$ARTIFACTS/plan.json"
# Inspect the successful plan. Run required checks on the exact candidate.
python3 "$TOOL" commit --repo "$REPO" --patch "$ARTIFACTS/owned.patch" \
  --path src/example.py --expected-parent "$PARENT" \
  --plan-file "$ARTIFACTS/plan.json" --record-file "$ARTIFACTS/operation.jsonl" \
  --message "Describe the authorised change"
```

`TOOL` above uses the Codex user copy. For the Claude Code user copy, set
`TOOL="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/one-checkout/scripts/one_checkout.py"`.
For a project installation, use the destination in the install receipt plus
`/scripts/one_checkout.py`. The copied runtime works without the source checkout.

Repeat `--path` for each exact file. Globs/directories and a declared set that
differs from the actual patch are refused. Ordinary `git diff` omits untracked
files. Use `--message-file` for longer messages. `--plan-file` binds the reviewed
repository, named branch, parent, patch hash, paths and candidate tree across
calls; it is optional for direct API compatibility. It is not an approval,
reservation, signature or idempotency key. Commit rechecks the live state.

## Outcomes and recovery

Keep stdout, stderr and exit status separately. Normal receipts are JSON:

| Exit | Meaning |
|---:|---|
| 0 | Successful plan, confirmed commit with normal index settlement, or completed read-only recovery inspection. |
| 2 | Refusal before publication is requested, or plan-output failure. |
| 3 | Commit may have landed, landed with later problems, or its receipt could not reach stdout. Inspect; never blindly retry. |

`landing_uncertain` exposes a candidate SHA without claiming that the branch
advanced. `landed_unsettled` confirms landing but reports settlement/cleanup
problems. A broken stdout stream cannot turn a known landing into a refusal;
stderr may contain the fallback receipt. Missing output is insufficient evidence.
Optional `--record-file` retains phase evidence; `recover --record-file` inspects
it without modifying the repository. Read the installed [recovery guide](skills/one-checkout/references/recovery.md).

## Boundaries

Use trusted repositories and cooperating writers on named branches with full
indexes. Split, sparse, unmerged and partial/promisor repositories are refused.
Semantic staging flags and index timestamps matter as well as blob contents.
The tool does not lock working files, infer ownership, resolve semantic conflicts,
validate the candidate program or push a branch.

Hooks are disabled per invocation, including reference-transaction hooks.
Required policy checks run separately. Configured signing is honoured;
`--git-timeout` bounds individual Git commands and protocol acknowledgements.
The branch update and subsequent index replacement are separate operations.
Process death, hostile writers and storage failures remain real boundaries.
See [architecture](docs/architecture.md), [transaction safety](docs/transaction_safety.md)
and [SECURITY.md](SECURITY.md).

## Verify and contribute

```sh
python3 -B -I -m unittest discover -s tests -v
python3 scripts/check_layout.py --root .
python3 scripts/demo.py --human --compare-git
python3 scripts/try_install.py --human
```

Set `TMPDIR` and `ONE_CHECKOUT_TEST_TMPDIR` to existing scratch directories when
needed. Tests create synthetic repositories; never use a working project as a
fault-injection fixture. [Release checks](docs/release_checks.md) distinguish
local tests, clean-clone installation, hosted CI and actual agent use.

| Owner | Purpose |
|---|---|
| [skills/one-checkout/](skills/one-checkout/) | Installed runtime, demo, doctor and agent guidance; explicit [bundle](skills/one-checkout/bundle.json) |
| [scripts/](scripts/) | Install, demo entrypoint, comparison, stress, agent-evaluation and layout tools |
| [tests/](tests/) | Behavioural regressions and independent Git-state checks |
| [repository guide](docs/repository_guide.md) | Source/copy ownership and maintenance routes |
| [layout policy](docs/repository_layout.json) | Enforced roles and required interfaces |
| [evaluation](docs/evaluate.md) | First-run and clean-clone journey |
| [agent evaluation](docs/agent_evaluation.md) | Prepared tasks and independent grading; scripted success is not model efficacy |
| [stress testing](docs/stress_testing.md) | Process races, fault injection and bounded performance experiments |
| [Git backend checks](scripts/check_backends.py) | Disposable files/reftable, packed-ref, linked-worktree and hash-format cases |
| [provenance](docs/provenance.md) | Git mechanisms and dated prior-art evidence |
| [CI](.github/workflows/ci.yml) | Supported-platform checks, separate from observed hosted results |

See [CONTRIBUTING.md](CONTRIBUTING.md). Code and documentation were prepared
with AI assistance under human direction. Licence: [MIT](LICENSE).
