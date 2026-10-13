# Local release checks

Preparation does not publish this repository. Run these from its root before
a release, with Python 3.10+ and Git on macOS or Linux:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/demo.py
python3 scripts/check_layout.py --root .
claude plugin validate --strict .
```

The last command is optional when Claude Code is not installed. It validates
the manifest, not a live agent session. Codex's portable package and bare-skill
discovery are separate routes. Exercising an installed script verifies that
the implementation was bundled; it does not verify live agent discovery or a
Codex plugin session. Hosted CI must be observed separately after a submission.

For a full-history secret scan, run `gitleaks git .` with Gitleaks installed.
Before the first commit, `gitleaks dir .` checks the prepared tree. Keep the
history scan as a separate release check once commits exist. A presence
checklist or a filename scan is not a secret audit.

## Fresh clone and both installed copies

These instructions require the source to have a commit: Git clones committed
files, not an uncommitted working tree. Select an empty scratch parent on a
disk with room for the small fixture. It must have no personal `.agents` or
`.claude` configuration.

```sh
git clone --no-local /absolute/path/to/one-checkout /scratch/one-checkout-clean
cd /scratch/one-checkout-clean
python3 scripts/demo.py
python3 -m unittest discover -s tests -v
python3 scripts/check_layout.py --root .
python3 scripts/install.py --agent both --scope project --project /scratch
python3 scripts/demo.py --tool /scratch/.agents/skills/one-checkout/scripts/one_checkout.py
python3 scripts/demo.py --tool /scratch/.claude/skills/one-checkout/scripts/one_checkout.py
```

Each demo verifies an A-only commit, the exact preserved B index blob and
untouched A+B+C worktree bytes. The installer will refuse an existing differing
copy, so use a fresh scratch parent. Tests and demos remove their own temporary
fixtures; the clone and installed skills remain available for inspection.

Set `TMPDIR` to an existing directory to route the runtime's temporary scratch
to a different disk. `ONE_CHECKOUT_TEST_TMPDIR` overrides the demo and test
fixture location. Neither changes the installed skill destinations.

After publishing is separately authorized, configure a real private security
reporting destination, require passing checks on the release branch and run
the hosted CI workflow. This local package supplies no remote or credentials.
