# Working on One Checkout

Read README.md and the runtime before changing behavior. The runtime lives in
`skills/one-checkout/scripts/one_checkout.py`; a copied skill must include it.
Python 3.10+, Git and the standard library are the whole dependency set.

Keep commits tied to the caller's explicit patch, exact paths and expected
parent. Preserve unrelated worktree bytes and staged entries. Test refusals
as well as success, including a concurrently advanced branch. Never relax
these invariants to make a happy-path test pass.

Run `python3 -m unittest discover -s tests -v` and
`python3 scripts/check_layout.py --root .`. See CONTRIBUTING.md for scope.
Tests create temporary repositories; point TMPDIR at a suitable disk if needed.
Do not run Git mutation tests in a user's working repository.

Update the README map and the layout policy when adding a directory role.
Keep host-specific paths, private records and local runtime state out of this
repository. Public publication and agent installation require their own user
request; creating a plugin manifest does not perform either action.

The installed payload is declared in `skills/one-checkout/bundle.json`. New
skill resources must be added there; the installer refuses missing/unlisted files.
Read [architecture](docs/architecture.md), [repository ownership](docs/repository_guide.md)
and [staging handoff](docs/staging_handoff.md) when changing those boundaries.
Keep developer fault injection and model evaluation in `scripts/` and `tests/`;
installed resources must work after the authoring checkout is removed.
Do not equate passing scripted fixtures with live agent discovery or efficacy.
