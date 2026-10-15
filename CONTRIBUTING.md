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

Report reproducible bugs and discuss changes in
[GitHub issues](https://github.com/wcook04/one-checkout/issues); submit changes as
[pull requests](https://github.com/wcook04/one-checkout/pulls).
Maintainer: [wcook04](https://github.com/wcook04). For suspected vulnerabilities,
follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
Keep discussion specific to code and results, and treat contributors respectfully.
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

If your host aliases temporary paths or restricts Git worktrees in its temporary
area, set `ONE_CHECKOUT_TEST_TMPDIR` to a physical, writable scratch directory
outside the project before running the checks. Recovery-record paths deliberately
reject symlink ancestors; keep that guard rather than relaxing it for a fixture.

Run the layout gate in both working and staged views before landing a change:
`python3 scripts/check_layout.py --root .` and
`python3 scripts/check_layout.py --root . --index`. A passing working tree cannot
validate different staged contents. CI is a definition until a hosted run passes.

## Two-role demo trial

This is a small task for an agent group testing how it shares source and receipts.
An **executor** runs the existing disposable demo on an exact One Checkout source
revision; a **reviewer** examines that run's JSON and source without treating a
reported pass as an independent observation. Keep each run's setup, results and
judgment together. Do not combine a runtime from one run with checks from another.

1. Executor: use a clean checkout and name who ran it. Record
   `git rev-parse HEAD`, `git status --short`, OS, `python3 --version` and
   `git --version`, then run `python3 scripts/demo.py --compare-git` and retain
   its complete JSON output and exit status. JSON is the default; add `--human`
   only for a readable companion. Record setup failures or skipped steps
   separately from assertions that actually ran.
2. Reviewer: name yourself and the exact source revision and JSON you opened.
   Check `versions` and `runtime_sha256`; compare `before`, `after`, `patch`,
   `receipt`, `checks`, `controls`, `refusal` and
   `follow_on_control` against the [demo source](skills/one-checkout/scripts/demo.py).
   Match all 11 named `checks`
   in `demo()` and all five named `follow_on_control.checks` in
   `follow_on_commit()` at that revision; report each result and any missing
   or failed case. Compare all three Git control rows with the matrix below
   and inspect `control_assessment.mismatches`; an aggregate pass or a shorter
   list cannot establish the claim. Inspect the snapshots and commit IDs,
   then state the judgment and what remains untested.

| `--compare-git` control | A-only selected | Index `shared.py` remains B | Foreign staged file excluded | Working bytes kept |
|---|---:|---:|---:|---:|
| `git commit` | false | true | false | true |
| `git commit --only shared.py` | false | false | true | true |
| `native private index` | true | true | true | true |

The false values are expected outcomes of the two ordinary Git controls.
`control_assessment` checks the exact matrix, including those false values.
With `--compare-git`, a mismatch sets `status=demo_comparison_failed` and exits
2 while retaining the primary and follow-on results for review. A passing
primary result alone does not accept the full comparison.

The fixture supplies an A-only reviewed patch and captured parent **before** B
and C arrive; neither the demo nor One Checkout infers authorship. The expected
observation is that the selected commit contains A, the retained staged snapshot
contains B but omits A, and the working file keeps A+B+C. The 11 primary checks
cover those snapshots and a stale-parent refusal. With `--compare-git`, three
separate Git controls include a native private-index case that also selects A;
the five `follow_on_control.checks` verify that a later ordinary commit in a
separate fixture actually reverses A. A report should distinguish these observed
fixture results, the executor's account, and the reviewer's judgment. Hooks are
disabled in the fixture. Reviewing fixture mechanics does not establish that A
was the right patch for a real project. This task does not test live agent
collaboration, build isolation, ownership inference or required project checks.

## Feedback contributions

[Source-only control review by xMaxos's operator Codex](https://github.com/a2aproject/A2A/discussions/2354#discussioncomment-18952740)
prompted the explicit Git-control expectation matrix and comparison mismatch
assessment. The reviewer inspected a pinned card and demo source; this credits an
acceptance suggestion, not their execution of One Checkout, code authorship or
endorsement. Earlier feedback credits remain below.

[snail_host's runtime-evidence proposal on SNAIL](https://joinsnail.com/posts/88685289-95a2-4a17-bec3-af634fa70923?reply_id=22f25d6e-beeb-4732-8756-83c54be005f0)
inspired the separation of per-run setup, assertions and review judgment in the
trial above. It is a text proposal about evidence boundaries in another project,
not a One Checkout run, code contribution or endorsement.

[Penny’s verification-cost question on The Colony](https://thecolony.ai/post/64ff24a2-a9b1-4c54-af68-daacaad0fd7b)
prompted us to measure the cost of verification. Will then asked us to investigate
safe optimisations. That led to the [paired runtime experiment](docs/performance.md)
and combining the entry/flag reads of each private index snapshot. This credits
the question that inspired measurement, not a performance-bug diagnosis, code
contribution, outside trial or endorsement. Earlier contributor credits remain below.

Thanks to [vina's later staging-risk observation](https://www.moltbook.com/post/cceb0260-7bbb-45e1-80bd-c7daf499ca84)
(comment `51aa90c6-bf3e-4de8-b3ed-991f0f563c3f`) for prompting the demo's
executable receipt-warning check and ordinary follow-on commit control. The
control now observes A being reverted in a separate disposable fixture and
retains its two commit IDs and snapshots. This credits discussion that improved
the demonstration, not a new outside run, code authorship or endorsement.
Earlier contributors and their distinct reports remain below.

Thanks to [tanod's native Git example](https://www.moltbook.com/post/ff8434cc-618d-4d88-a788-84e1da61078b)
(comment `5a9e9d27-256e-4765-946c-0be42091722d`) for prompting the README's
post-commit staging explanation. We adapted the existing disposable fixture into
a context-separated positive case and an adjacent-context case where the final
staged apply fails after A reaches the branch. This credits an alternative-workflow
example and resulting documentation, not a reported One Checkout trial or endorsement.

Thanks to [vina's staged/working-diff question](https://www.moltbook.com/post/ff8434cc-618d-4d88-a788-84e1da61078b)
(comment `3f5862cc-da1b-484a-825d-6f9b6f97f11e`) for prompting the explicit
three-delta comparison and reconciliation boundary in the README/staging guide.
This is documentation feedback credit, not a reported execution, code authorship
or endorsement.


Thanks to [lobsternigel's recovery comparison suggestion](https://www.moltbook.com/post/42420abe-80ea-4677-854f-517860bbe826)
(comment `c1978dbd-6197-40a4-9569-5bfc195cd3b9`) for prompting the executable
`scripts/recovery_demo.py`. It separates the candidate object, observed branch
publication and index settlement, and checks the no-automatic-retry contract.
This is example-design feedback credit; it does not establish a tool trial,
code authorship or endorsement.


Thanks to [midearthguild's implementation questions](https://www.moltbook.com/post/ff8434cc-618d-4d88-a788-84e1da61078b)
(comment `294af9dd-0a68-474e-a7ac-e1ff837be83e`) for prompting the README's
locking and captured-parent answers. This is documentation feedback credit;
it does not establish a trial, code authorship or endorsement.

Thanks to [Skitter's public replay report](https://swarmmemo.com/e/b5317916e9b1f89044d352972fea24c8)
for prompting the prominent staging-handoff warning in the human demo.
The report covers a pinned disposable Linux run; it does not establish live-agent
adoption, concurrency coverage or endorsement.

Skitter's [repeat disposable replay](https://swarmmemo.com/e/422da021a5439d906963b18f07840398)
at `eee46ab6c1f18ec2c2fde62d171fc20888702e32` reported all ten demo checks
passing on Linux aarch64 and requested a direct next-action pointer for B's owner.
The human demo now links the staging guide's "Deliberate next action" section,
which explains reviewing B against a chosen new parent while preserving A.
This is demo-navigation feedback credit; the replay did not test a real repository,
concurrent writers, macOS or a live agent session.

Skitter's [next pinned replay](https://swarmmemo.com/e/fab4e5d3a1e461862c73a0c15b6da103)
at `548c65d3332f74d0f2f77fea06796d6dffe7ea85` reported the new pointer and ten
passing checks, then noted that its `main` URL could drift. The demo now pins the
compatible guide revision; that guide's bytes are unchanged in this correction.
This remains disposable replay and link-stability feedback, not live-host adoption.

Thanks also to [Dione's shared-checkout feedback](https://www.moltbook.com/post/c293570d-d312-4108-ac25-18cb19af23d3)
for the concrete mixed-note example now in the README: when neither writer has
a separate reviewed patch, selecting ownership remains an earlier workflow step.
