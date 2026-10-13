# A terminal demonstration that explains the contract

Run `python3 scripts/demo.py --human --compare-git`. This is an executable
synthetic example, not a video or a live-agent benchmark. The default without
`--human` remains machine-readable JSON. A failed assertion exits nonzero.

## Proposed 60–90 second recording

This is a scene plan; no recording or viewer-timing result is claimed.

| Approximate scene | What to show and explain |
|---|---|
| 0–15 s | State the problem: your patch is ready, but another agent has staged changes in the same file. Point at HEAD, index and working-file rows. |
| 15–35 s | Run the human demo. Show that only A reaches HEAD, B's complete staged snapshot survives, and A+B+C stays in the file. A was captured before mixing changes; there is no authorship inference. |
| 35–50 s | Read the real staged diff. Its reversal of A is expected because the preserved index is a snapshot, not a rebased patch. Do not blindly commit or reset it. |
| 50–65 s | Show the stale-parent refusal and fair Git controls. Native private-index Git succeeds too; the package adds a bounded interface and guarded operation. |
| 65–90 s | Run `python3 scripts/try_install.py --human`. Show both hosts in project/user scopes executing and cleanup; state that live agent discovery remains a separate check. |

Capture actual output from the released commit on a clean terminal. Keep stderr
visible. Record Git/Python versions and commit. Do not hide failures, label manual
editing as autonomous, accelerate footage without saying so, or imply that the
script wall time measures a developer's time to understand it.

## Claim-to-evidence map

| Claim | Runnable evidence | Important limit |
|---|---|---|
| A only is committed | `test_demo.DemoTests.test_exact_three_views_and_observed_diff`; demo HEAD and changed-path assertions | A is explicitly supplied, not recovered from intent |
| B's staged snapshot survives | Demo index blob, foreign-file assertions and actual cached diff | The snapshot may reverse A relative to the new HEAD |
| Unstaged work remains visible to Git | `test_index_timestamps.py`; demo `diff-files` check | This does not lock arbitrary editors or prove every filesystem safe |
| A stale parent refuses | Demo's sequential second call and existing core race tests | The short demo is sequential; it is not a concurrent stress test |
| Both hosts and both scopes contain a working runtime | `scripts/try_install.py`; `tests/test_try_install.py` | No live model, plugin-marketplace or cloud acceptance test |
| First-run leaves no installed footprint | Cleanup assertions and temporary routing tests | A deliberate real installation is a different command |
| Git already supplies patch selection primitives | `--compare-git` private-index positive control | That short control is not a concurrent/recovery-safe replacement |

The three-state visual is an original text presentation of this package's fixture.
No third-party screenshot, animation, logo, visual template or code is copied.
Comparable command-first presentations and evaluation ideas are credited in
[provenance](provenance.md); their adoption outcomes are not our results.
