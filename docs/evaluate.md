# Evaluate before installing

This is a short evaluation path, not a promise that every machine or person
will finish in five minutes. Use Python 3.10+ and Git on macOS or Linux.
No model credentials, network service, telemetry or pip installation is needed.

## First encounter

From an inspected source checkout:

```sh
python3 scripts/demo.py --human
python3 scripts/demo.py --human --compare-git
python3 scripts/try_install.py --human
```

The first command prints the three states of `shared.py`, the actual staged
diff and individual assertions. A patch containing only A is supplied; patch
selection is not solved for you. The second executes three controls on separate,
identical fixtures. Ordinary `git commit` commits staged content; a file-scoped
commit includes that file's working contents but excludes the other staged file;
private-index Git can produce A correctly. This is not a timing benchmark.

The last command snapshots the current declared bundle (including local edits),
then installs both self-contained skill copies in project and isolated-user
scopes, hashes the complete declared payload, executes all four installed runtimes,
checks that reinstallation changes nothing, and removes the project. It creates
an isolated HOME and routes nested fixtures inside its scratch directory.
It neither calls an agent CLI nor proves skill discovery in a running agent.
All success reports appear only after cleanup completes.

Set `TMPDIR` to an existing scratch directory to route runtime temporary files.
Set `ONE_CHECKOUT_TEST_TMPDIR` for the outer demonstration and test fixtures.
Neither option redirects an intentional installation's destination.

## Fresh committed clone, with no user installation

Run this from the root of a clean, committed local copy, obtained through the
[versioned GitHub clone instructions](../README.md) or your development checkout.
It clones that exact local commit; uncommitted changes are not part of a clone.
The subshell deletes only the directory it
creates with `mktemp`; it does not change the original checkout.

```sh
(
  set -eu
  source_repo="$PWD"
  test -z "$(git status --porcelain)"
  scratch=$(mktemp -d "${TMPDIR:-/tmp}/one-checkout-eval.XXXXXXXX")
  trap 'rm -rf -- "$scratch"' EXIT
  mkdir "$scratch/home" "$scratch/tmp"
  export HOME="$scratch/home" TMPDIR="$scratch/tmp"
  export ONE_CHECKOUT_TEST_TMPDIR="$scratch/tmp"
  git clone --no-local "$source_repo" "$scratch/clone"
  cd "$scratch/clone"
  git rev-parse HEAD
  python3 --version
  git --version
  python3 -m unittest discover -s tests -v
  python3 scripts/check_layout.py --root .
  python3 scripts/demo.py --human --compare-git
  python3 scripts/try_install.py --human
)
```

For release verification, check that `git rev-parse HEAD` matches the commit
listed on the [release page](https://github.com/wcook04/one-checkout/releases).
Hosted checks are available in [GitHub Actions](https://github.com/wcook04/one-checkout/actions/workflows/ci.yml).

## Deliberate use in an agent

After the disposable checks, the installer can target a real trusted project:

```sh
python3 scripts/install.py --agent both --scope project --project /path/to/project --dry-run
python3 scripts/install.py --agent both --scope project --project /path/to/project
```

Replace `/path/to/project` with the intended directory, inspect the reported
destinations, then start a fresh agent session in that project. Ask it to use
`one-checkout` and to identify the skill and runtime path before any commit.
It must obtain an explicit owned patch, exact paths and the expected parent,
not assume that all current changes belong to it. A Claude plugin loaded through
`--plugin-dir` uses a different discovery route from the bare skill copied here.
See the official skill references in [provenance](provenance.md).

For a real integration acceptance test, use a disposable project with synthetic
A/B/C changes. Record agent CLI version, requested/observed model where exposed,
Python/Git executable paths and versions, source commit, installed skill/runtime
hashes, transcript, JSON receipt, and independent Git state checks. Test both a
valid patch and stale-parent refusal. Mark absent model observations as unknown.
A matching file hash is not proof that the model chose or followed the skill.

For reversible removal, repeat the installation command with `--uninstall`.
Inspect the reported backup before restoring; never overwrite an occupied
destination or remove the enclosing skills directory. See [installation](installation.md).

## Report a useful result

Use the evaluation issue form with a synthetic reproducer and redacted receipt.
Report failure and recovery, not just a screenshot saying PASS. Never post private
patches, source files, keys, usernames or internal repository paths. Security
reports follow SECURITY.md rather than a public issue. There is no automatic
telemetry; the user decides what to share.
