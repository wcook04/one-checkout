# Installation, updates and removal

One Checkout copies a self-contained skill. It does not configure an agent,
change permissions, register a marketplace or publish anything. Choose one
route per host and scope; avoid duplicate bare-skill and plugin copies.

## First run

Clone a versioned release from [wcook04/one-checkout](https://github.com/wcook04/one-checkout):

```sh
git clone --branch v0.3.0 --depth 1 https://github.com/wcook04/one-checkout.git
cd one-checkout
git rev-parse HEAD
python3 scripts/demo.py --human --compare-git
python3 scripts/try_install.py --human
python3 scripts/install.py --agent both --dry-run
python3 scripts/install.py --agent both
```

The demo and install rehearsal use disposable synthetic repositories. The last two
inspect and perform an intentional user installation. For an existing project,
add `--scope project --project /absolute/path/to/project`; select only one host
with `--agent codex` or `--agent claude`.

| Host | User copy | Project copy |
|---|---|---|
| Codex | `~/.agents/skills/one-checkout` | `<project>/.agents/skills/one-checkout` |
| Claude Code | `~/.claude/skills/one-checkout` | `<project>/.claude/skills/one-checkout` |

An absolute exported `CLAUDE_CONFIG_DIR` selects a different Claude user root;
it does not affect project scope. Read the actual destination in the dry-run
receipt. Symlinked source/destination ancestry is refused; the installer does
not silently dereference dotfile links. No sudo, pip, Node, service or network
connection is required once Python, Git and the reviewed source are available.

Run the installed checker, called the doctor, from the destination printed by
the installer. Choose the commands for the scope you installed.

For a **user installation**:

```sh
python3 "$HOME/.agents/skills/one-checkout/scripts/doctor.py" --self-test
python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/one-checkout/scripts/doctor.py" --self-test
```

For a **project installation**, replace the project path below, then run the
command for each host you installed:

```sh
cd /absolute/path/to/project
python3 .agents/skills/one-checkout/scripts/doctor.py --self-test
python3 .claude/skills/one-checkout/scripts/doctor.py --self-test
```

The doctor reports prerequisites and runs the bundled disposable demo. It does
not launch a model. Verify the visible skill and its source location in
the host; reload skills or start a new session if necessary. A passed doctor is
not evidence that an agent discovered or followed the instructions.

## Host permissions and environment

The agent needs ordinary permission to write the target repository's Git metadata
and its external patch/receipt directory. A successful installation grants no
such permission. Use the host's normal scoped approval controls; do not disable
the sandbox to make a commit succeed.

The runtime refuses inherited `GIT_*` controls other than author/committer identity.
Read the named variables in an `unsafe_environment` refusal without exposing their
values. Claude Code can inject `GIT_EDITOR` for each Bash command, even if it was
absent when Claude started. For this editor-only preference, an explicit-message
invocation can use `env -u GIT_EDITOR python3 "$TOOL" ...` after reviewing the refusal
and confirming the same authorised patch, plan, parent and branch. Remove it for
that command only; preserve the host's settings. Other Git controls require their
own review. Environment repair does not justify retrying an uncertain landing.

## Reviewed source and explicit bundle

`skills/one-checkout/bundle.json` is the explicit payload inventory. Missing or
unlisted resources are refused; adding a resource requires a deliberate manifest
change. The manifest and all payloads travel together. Stable symlinks and
special files are refused; generated cache files are not installable resources.

A dirty skill source in a Git checkout is refused by default. Maintainers testing
local changes may opt in with `--allow-dirty-source`; the receipt identifies that
snapshot. Ordinary users should install a reviewed commit. A source archive has
no authenticated Git identity: its content hashes describe bytes, not a signed
release. Unrelated repository changes do not redefine the skill payload.

A tag selects a version; the Git installation receipt records its resolved commit
ID and payload hashes. Admission compares captured raw blobs and Git executable
modes with that commit independently of status, including hidden index flags.
The installer publishes those same captured bytes. These checks establish content
identity, not publisher authenticity. A Git-less source archive remains supported.

## Upgrade and reversible uninstall

An identical copy is left unchanged. A different existing copy is refused until
you inspect it and choose to retain it during replacement:

```sh
VERSION=v0.3.0  # Replace with the released tag you have chosen to review.
git clone --branch "$VERSION" --depth 1 https://github.com/wcook04/one-checkout.git "one-checkout-$VERSION"
cd "one-checkout-$VERSION"
git rev-parse HEAD
python3 scripts/demo.py --human --compare-git
python3 scripts/install.py --agent both --backup-existing --dry-run
python3 scripts/install.py --agent both --backup-existing
```

Use a new source directory for each reviewed upgrade. If it already exists, Git
refuses the clone; inspect it rather than deleting or resetting it automatically.
Check the proposed destinations and backups before the final command. To remove:

```sh
python3 scripts/install.py --agent both --uninstall --dry-run
python3 scripts/install.py --agent both --uninstall
```

Repeat the original scope/profile options. Uninstall moves the installed
one-checkout directory into a backup outside skill discovery. It does not delete
the files or the enclosing skills directory. Stop active use before upgrading
or removing; a running agent may retain loaded instructions.

The installer prepares and verifies complete payloads before publication.
Directory rename is atomic per destination; updating two hosts is **not** one
atomic transaction. If publication fails partway through, read every per-host
outcome and retained backup/recovery path. A nonzero exit does not undo an
already installed copy. Cleanup failure must not erase successful outcomes.

A caught failure after backing up a prior copy may restore it only into an
absent destination. Never overwrite a newly occupied destination. Preserve any
reported recovery directory until its contents have been reviewed. Do not
remove locks or backups merely because an earlier process stopped responding.
The filesystem must be trusted; this is not protection against a hostile writer
or a power-loss durability protocol. Backups consume space until deliberately
reviewed and removed.

## Plugin metadata and host scope

The root `plugin.json` describes portable Agent Plugins packaging. The
`.claude-plugin/plugin.json` describes Claude Code packaging, with the same
skill source. Claude's `--plugin-dir /absolute/package` is a session loading
route, not persistent installation. No marketplace is supplied. Local copied
skills are the tested installation route; plugin discovery must be tested
separately before claiming it works on a particular host.

Host references checked 13 October 2026:
[OpenAI skills](https://learn.chatgpt.com/docs/build-skills),
[portable plugins](https://developers.openai.com/plugins/build/plugins),
[Claude skills](https://code.claude.com/docs/en/skills) and
[Claude plugin reference](https://code.claude.com/docs/en/plugins-reference).
Host policy, profiles, cloud/remote execution and duplicate skills can change
availability. See [release checks](release_checks.md).
