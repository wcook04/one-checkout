# Validation and release checks

A check definition is not a passing run. Record exact commit, Python/Git/host
versions, command, exit status and evidence for the tree you intend to release.
Local unit tests, hosted CI and actual agent use are separate observations.

## Maintainer checks

```sh
python3 -B -I -m unittest discover -s tests -v
python3 scripts/check_layout.py --root .
python3 scripts/check_layout.py --root . --index
python3 scripts/demo.py --human --compare-git
python3 scripts/try_install.py --human
python3 scripts/compare_workflows.py --repeats 1
python3 scripts/check_backends.py --scratch /path/to/existing/scratch --output /tmp/backends.json
```

`try_install.py` tests a hashed disposable snapshot, including local edits.
Real installation of dirty skill source needs `--allow-dirty-source`; release
installation must run without that flag from a clean commit.
Run the dedicated process/crash/signing modes in [stress testing](stress_testing.md)
when changing resource ownership, ref publication, timeouts or recovery. Capture
unsupported capabilities honestly rather than counting skipped cases as passes.

## Fresh receiver

From a clean committed checkout, clone it into a new scratch directory. Use an
empty HOME, isolated Python (`-I`), no inherited `GIT_*` control variables and
only documented dependencies. Set TMPDIR and ONE_CHECKOUT_TEST_TMPDIR inside
scratch. Follow [evaluate](evaluate.md), including full tests, layout, demo and
both installed copies. Verify the clone remains clean.

Exercise both user/project scopes for both hosts, a Git-less source archive,
idempotent reinstall, differing-copy refusal, backup update and reversible removal.
Delete the disposable source after installation and run each installed doctor/demo.
The bundle manifest and regular-file inventory must agree. Compare installed
hashes, runtime identity, plugin metadata and both licence copies. Test symlink,
missing/extra resource and partial-publication failures as well as success.

## Actual hosts

In a synthetic project, test Claude Code and Codex separately. Capture the host
version, requested/observed model where available, discovered skill path, installed
runtime/skill hashes, command arguments, receipt and independent Git-state checks.
Use ordinary approval controls. Record blocked, unavailable or denied commands;
never turn a scripted installed-runtime test into a host success claim.

Test explicit invocation first; automatic discovery is a different trial. Include
correct A-only selection, stale-parent/changed-plan refusal, ambiguous scope and
recovery. Final snapshots cannot reveal forbidden intermediate mutations: inspect
the complete trace. See [agent evaluation](agent_evaluation.md). A few smoke tests
do not establish an efficacy rate or robustness amid every conflicting skill.

## Before a public release

- Scan the full final Git history and working tree with a secret scanner. Confirm
  fixtures are synthetic and review exports for local paths or personal records.
- Validate both plugin manifests; use `claude plugin validate --strict .` where
  available. A manifest is not a marketplace acceptance test.
- Observe CI on the exact release commit across the supported Linux/macOS and
  Python version matrix. Exercise another current Git version/backend where
  available; document unsupported cases.
- Establish a real repository URL, vulnerability-reporting destination and
  maintainer contact before public launch. Do not invent them in examples.
- Choose/tag an immutable reviewed version, verify the release archive and
  installation journey, and configure repository protections through the actual
  host when publishing is authorised.
- Claims must match executable evidence. Publish failures and limitations with
  any benchmark. Adoption/recognition trials are proposals until run with real
  consenting users; no automatic outreach or posting follows from preparation.

The MIT licence covers the distributed project. Referenced external guidance
and implementations retain their own licences; no comparator licence is inferred
from a GitHub link or a source-available label.
