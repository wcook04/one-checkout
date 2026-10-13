# Contributing

This is a small Python standard-library tool. Prefer a concrete failing
scenario and a small change over another coordination framework.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check_layout.py --root .
```

Tests create disposable Git repositories. They do not require Claude Code,
Codex, network access or a package installation. Use TMPDIR on a disk with
enough free space. Supported platforms and deliberately refused Git index
formats are listed in the README.

For runtime changes, cover the resulting commit tree, unrelated index entries
and worktree bytes, and the refusal path. For installer changes, cover an
existing different destination, a backup, a symlink and installation for both
agents. Preserve the runtime inside the installed skill.

Keep docs examples aligned with `--help`. A layout change also updates
`docs/repository_layout.json` and the README map. Run Claude's optional
`claude plugin validate --strict .` if that CLI is available; do not make it a
dependency of the offline Python tests.

No public issue tracker or contribution destination is configured yet.
Fresh-clone and installed-copy checks are in [docs/release_checks.md](docs/release_checks.md).
