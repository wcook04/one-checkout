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

Relevant public work:

- [Agentlocks](https://github.com/simke9445/agentlocks) coordinates advisory
  resource locks and offers a path-scoped commit command for a shared worktree.
  One Checkout does not implement its path-lock service.
- [Worktrunk](https://github.com/max-sixty/worktrunk) manages separate Git
  worktrees for parallel coding agents. That remains the appropriate approach
  when separate working directories are needed.
- [Git's update-ref documentation](https://git-scm.com/docs/git-update-ref)
  describes conditional ref updates.
- [Git's commit-tree documentation](https://git-scm.com/docs/git-commit-tree)
  describes creating a commit without the porcelain commit workflow.
- [Git's alternate index documentation](https://git-scm.com/docs/git)
  documents `GIT_INDEX_FILE`.

The useful boundary is explicit patch + exact path set + expected parent,
with unrelated staged work preserved. This is not a claim that no other
tool supports scoped commits. Worktrees remain a good choice when agents
need separate working files.

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
