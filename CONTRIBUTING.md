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

The [repository guide](docs/repository_guide.md) names canonical owners and the
explicit bundle. The [architecture](docs/architecture.md) describes protocol
phases and receipt delivery separately. Preserve both index semantics and Git's
ability to detect working changes; blob equality alone is insufficient.

Use [stress testing](docs/stress_testing.md) for actual process races and
[agent evaluation](docs/agent_evaluation.md) for synthetic tasks/independent grading.
Run `python3 scripts/try_install.py --human` to test a content-hashed disposable
snapshot of the current source. Real installation from dirty skill source
requires the installer's explicit `--allow-dirty-source` option. The bundled doctor/demo must
work with the source removed. Keep external research and raw host transcripts
out of the public runtime; use synthetic reproducers and bounded provenance.

Run the layout gate in both working and staged views before landing a change:
`python3 scripts/check_layout.py --root .` and
`python3 scripts/check_layout.py --root . --index`. A passing working tree cannot
validate different staged contents. CI is a definition until a hosted run passes.
