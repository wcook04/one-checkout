# Provenance and comparisons

One Checkout was developed under human direction with AI coding assistance.
It is a small standalone implementation of a workflow used in a larger
private development environment: build a commit from an explicit patch in a
private Git index, then update the branch only if its parent is still current.
No private data, instruction tree or runtime service is required.

The implementation uses Git's existing `read-tree`, `apply`, `write-tree`
and `commit-tree` operations. It retains the shared `index.lock` and uses
`update-ref --stdin` with `start`, `prepare` and `commit`: after preparation,
it checks the named branch and expected parent before committing the native
ref transaction. Shared-index settlement follows separately. These mechanisms
belong to Git; One Checkout packages them into a bounded agent command.

## Related work and scope

Research and return reviews were checked on 13 October 2026. The dated
[source ledger](prior_art.json) preserves source pins and evidence limits.
Links are attribution, not endorsements or measurements of other tools.

- [Git index timestamps](https://git-scm.com/docs/racy-git) explain why preserving
  blob identities alone does not preserve change detection. This tool retains
  the relevant index timestamps and verifies semantic staging flags.
- [GitButler parallel agents](https://docs.gitbutler.com/ai-agents/parallel-agents)
  explicitly supports agents sharing one workspace. Its selected-hunk and branch
  workflows are material prior art. Exact equivalence to this tool's ordinary
  shared-index conservation and recovery contract has not been established.
  The inspected repository identifies FSL-1.1-MIT; its code is not vendored here.
- [git-hunk](https://github.com/wkentaro/git-hunk) is adjacent programmatic hunk
  selection and agent-workflow work. Selection is a potential integration seam,
  not a capability this runtime infers. Its published performance is not ours.
- [Agentlocks](https://github.com/simke9445/agentlocks) offers advisory resource
  locks and path-scoped commits. Its path helper must not be described as
  sweeping all unrelated staged paths; path selection and same-file patch
  selection are different contracts.
- [Worktrunk](https://github.com/max-sixty/worktrunk) manages separate Git worktrees.
  Use isolation when tasks need independent filesystem state. No environment
  cost or speed advantage over it is claimed here.
- [Foremerge](https://github.com/naw103/foremerge) addresses intent, conflict and
  validation coordination beyond this tool's local commit boundary. Its public
  [release discussion](https://news.ycombinator.com/item?id=49789356) supplies
  both creator rationale and contrary coordination-cost reports, not adoption
  or productivity measurements for One Checkout.
- [Jujutsu operation logs](https://docs.jj-vcs.dev/latest/operation-log/) are
  recovery-design prior art. A local record does not make arbitrary Git actions
  atomic or exactly once.

Git's [update-ref](https://git-scm.com/docs/git-update-ref),
[commit-tree](https://git-scm.com/docs/git-commit-tree) and
[alternate index](https://git-scm.com/docs/git) documentation describe the
underlying primitives. Our executable comparison includes a capable native-Git
control as well as pathspec and worktree controls. These demonstrate different
contracts, not a broad winner. [Choose a workflow](choosing_a_workflow.md) and
[understand the staged handoff](staging_handoff.md).

## Packaging

Packaging guidance checked on 13 October 2026:

- [OpenAI portable plugin packaging](https://developers.openai.com/plugins/build/plugins):
  root `plugin.json` with the Agent Plugins schema; root `skills/` discovery.
- [OpenAI skill locations](https://learn.chatgpt.com/docs/build-skills):
  repository and user `.agents/skills` directories.
- [Claude Code plugin manifests](https://code.claude.com/docs/en/plugins-reference):
  `.claude-plugin/plugin.json`, with skill files outside the manifest directory.
- [Claude Code skill locations](https://code.claude.com/docs/en/skills):
  repository and user `.claude/skills` directories.

The portable manifest targets
`https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`.
No marketplace or public hosting URL is declared.

Release preparation also consulted Trail of Bits'
[open-sourcing workflow](https://github.com/trailofbits/skills/tree/8e54050ae63c2f62224c35b1616efe71865b4777/plugins/open-sourcing/skills/open-sourcing)
at commit `8e54050ae63c2f62224c35b1616efe71865b4777` (CC-BY-SA-4.0).
That developer guidance is not bundled or required to run One Checkout.

The layout checker and its tests are copied from the repository-organization
skill used during development. Their original source is independently authored
Python standard-library tooling from the same development environment; the
test import, temporary-directory routing and diagnostic guide are adapted to
this repository. The local layout policy is specific
to this package. All bundled code is distributed under this repository's MIT
licence; no third-party runtime code is vendored.

## Review assimilation

Eight independent reviews against the first standalone source informed index
flags/timestamps, truthful delivery, bounded waits and recovery, plan binding,
manifest installation, role checks, demonstrations and independent evaluation.
Overlapping patches were reconciled by responsibility rather than concatenated.
Synthetic reproducers and executable acceptance tests belong in this repository;
private review archives and host transcripts do not. Proposed field trials,
competitor adapters and general candidate-validation policy are not represented
as completed experiments. No third-party runtime implementation was imported.
