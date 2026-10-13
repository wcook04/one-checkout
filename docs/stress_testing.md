# Stress testing and performance evidence

These developer tools create synthetic repositories. They do not accept a live
repository as a test target, require a service, or enter the installed skill.
The runtime remains one standard-library script. Use the ordinary test suite
first; fault injection and timing runs are separate, opt-in activities.

Fixture construction and raw-Git oracle commands have a separate 60-second
per-command deadline. `--timeout` bounds the instrumented worker; the runtime's
`--git-timeout` remains its own default 30-second per-command/acknowledgement
limit. A `fixture_timeout` occurs outside the measured runtime and is reported
separately from a worker timeout or invariant failure.

Cases expecting successful settlement can instead observe an honest exit-3
`landing_uncertain` or `landed_unsettled` receipt, for example after Git's
acknowledgement deadline expires. The harness checks the candidate tree and
parent, original branch identity, permitted HEAD/index states, working bytes
and remaining locks independently. Verified conservative outcomes are
`operational_uncertainty`: the run needs attention, exits nonzero, and does not
count them as passing runtime cases. Unexpected state remains an
`invariant_failure`. Unconfirmed publication requires the original index bytes;
an acknowledged commit may retain them or have settled the expected index
before a later cleanup error.

```sh
python3 -m unittest discover -s tests -v
mkdir -p /path/on/test-disk/oc-scratch
python3 scripts/stress.py --mode all --seeds 32 \
  --scratch /path/on/test-disk/oc-scratch --output /tmp/oc-results.json
```

`--scratch` must already exist. The harness requires 512 MiB free at start;
that check is not a reservation or a guarantee that the disk cannot fill.
It neither changes a storage policy nor deletes other caches. `--tool` chooses
an alternative runtime script for baseline comparisons, never an existing
repository to mutate. The controller invokes that script on fixtures it creates.
The scripts require POSIX process groups; Windows is reported unsupported.

## Independent oracle

For a parent tree **B**, selected candidate tree **C**, pre-existing index **I**,
and working files **W**, successful settlement requires:

* The new commit has exactly C and the recorded parent.
* For each changed path p where I[p] equals B[p] and has no preserved semantic
  flag, the new index has C[p].
  Every other index entry retains its original mode and blob identifier.
* W remains byte-for-byte and mode-for-mode unchanged, including untracked files
  and symlink targets.

The model computes blob identifiers from raw bytes independently of the runtime.
Observations parse NUL-delimited `ls-tree` and `ls-files` output. Small fixtures
include path bytes in hexadecimal, modes, blob identifiers and expected/actual
maps in the JSON. Index bytes are also compared where no settlement is allowed.
This is stronger than checking porcelain status, but not a proof of filesystem
isolation or preservation of every possible index extension. The v3 case also
checks one unrelated extended index flag explicitly.

The default portable corpus covers additions, deletions, foreign staging,
binary data, executable modes, symlinks, leading hyphens, pathspec-looking names,
tabs and newlines. `--name-profile posix-stress` additionally probes case and
Unicode normalisation distinctions and undecodable POSIX filename bytes.
Filesystems unable to represent a fixture report unsupported.
The CLI byte-path case uses `--path=NAME`, including for leading hyphens.

## Real processes and controlled schedules

`stress_worker.py` observes the production implementation without substituting
successful Git responses. Pipe barriers expose snapshot, prepared ref transaction,
acknowledgement and index settlement. Experiments start competing Python/CLI
processes and actual `git add`, `update-ref` and `symbolic-ref` writers. Same-parent
races run two, four and eight callers. The seed fixes input data, not the OS scheduler.
These are reproducible controlled schedules, not exhaustive interleaving exploration
or a fully deterministic simulator.

```sh
python3 scripts/stress.py --mode concurrency --scratch /path/to/scratch --output /tmp/races.json
python3 scripts/stress.py --mode crash --scratch /path/to/scratch --output /tmp/deaths.json
```

Crash mode sends SIGKILL only to a fixture caller or its recorded Git child.
It inspects refs, index bytes, lock identities and surviving process state. A
caller killed after acknowledgement may leave a landed commit and unsettled
index; a killed native ref child may leave Git's own locks. The test controller
terminates only its created process groups. It does not remove individual locks,
automatically retry the transaction, or roll back a branch. Death/failure fixture
directories are retained for inspection. No killed fixture is reused for a commit.
A zombie is reported separately from a running process using Linux `/proc`
or POSIX `ps` where available.
SIGKILL is not power failure, storage fault injection or evidence of durability.

## Signing and liveness

```sh
python3 scripts/stress.py --mode liveness --scratch /short/scratch --output /tmp/signer.json
python3 scripts/stress.py --mode bench --tracked 1000 --selected 1 \
  --signing-delay .5 --scratch /short/scratch --output /tmp/signed-bench.json
```

These optional cases use a real ephemeral SSH signer, or GnuPG plus `gpgconf`
when SSH signing is unavailable. Keys live only in synthetic directories, never
in a user's keyring. GnuPG agent shutdown names the exact synthetic home. Long
socket paths or absent tools are unsupported/skipped, not a signing success.
Do not publish retained private keys with test evidence.

Liveness mode deliberately stalls the external signer, observes that the caller
and index lock are still waiting and that ordinary staging is refused, then
releases it. The resulting signature is verified. A `controlled_stall` with
`expected_fault: true` is the observed availability hazard, not an invariant
failure. The run intentionally exits non-zero so dashboards cannot count it as
a healthy case. The controller has a deadline; the integrated runtime also bounds each Git
command and acknowledgement. This controlled-stall case does not measure expiry
of the production timeout; dedicated runtime tests exercise that deadline.
Baseline runtimes may still hang. Interpret the
observed phase and outcome instead of assuming that a timeout proves non-landing.
The harness never retries or repairs the fixture automatically.

## Benchmarks and fair controls

```sh
python3 scripts/stress.py --mode bench --tracked 10000 --selected 10000 \
  --repetitions 5 --scratch /path/to/scratch --output /tmp/large-patch.json
python3 scripts/stress.py --mode compare --scratch /path/to/scratch --output /tmp/controls.json
```

Bounds limit seeds, files, repetitions, payload and per-worker waits. Setup and
invocation wall time are separate from the instrumented commit interval. Metrics
include index-lock duration, worker and maximum individual child RSS, Python-launched
subprocess count, actual object-store growth and exact-oracle checks. The RSS
measure is not aggregate process-tree memory; subprocess counts omit Git's own
descendants. Record the filesystem and environment. No timing threshold belongs
in the unit suite.

Patch-generation blobs live in a separate object store, so fixture construction
does not prepopulate the target with candidate objects. This does not flush OS
caches: describe results as fresh fixtures, not cold-disk benchmarks. Compare
baseline and candidate using the same harness, repeat and alternate arm order,
and publish all measurements, not just the fastest run.

The same-file control intentionally shows that `git commit --only FILE` commits
the file rather than the supplied patch. This is expected Git behaviour, not a
Git defect. A fresh detached worktree can produce the exact candidate while
preserving original working files and staging, but does not advance the original
branch. Its setup is timed. Do not claim equivalent end-to-end semantics or a
speed advantage from those different workflows. Real worktrees share object
storage; they are not necessarily full clones.

## Result interpretation and maintenance

JSON separates `pass`, `invariant_failure`, `operational_hang`, `controlled_stall`,
`unsupported`, `skip` and `harness_error`. An empty or all-skipped corpus returns
`no_cases_exercised` and exits non-zero. Each invocation freezes the runtime for
all workers, recording its hash. It records commands, runtime and harness hashes,
Git/Python/platform versions, inputs, receipts and observations. Keep failure
artifacts. Fix harness errors before interpreting them as product defects.
An aggregate `pass_with_skips` means the exercised cases passed while listed
capabilities were unavailable; it does not turn skipped rows into passes.
A seed is a reproducible fixture, not necessarily a minimised counterexample;
reduce any genuine failure and add it to the small unit corpus before a fix.

The normal suite includes a small process/model corpus, no hard-crash or signing
requirement. The dedicated opt-in runner includes broader schedules. Existing
CI discovers the new tests; a workflow declaration is not a hosted CI result.
Linux evidence does not validate macOS, network filesystems, linked-worktree Git
administrative paths, reftable refs, every Git version, power failure or hostile
writers. No LLM productivity benchmark is implied.

The method borrows independent-invariant and replay ideas from
[FoundationDB](https://apple.github.io/foundationdb/testing.html), and the explicit
separation of safety and liveness from
[TigerBeetle](https://tigerbeetle.com/blog/2023-07-06-simulation-testing-for-liveness/).
It uses real Git transactions described in
[update-ref](https://git-scm.com/docs/git-update-ref). Those systems' guarantees
and testing scale do not transfer merely by citing them. Broader prior art is
listed in [provenance.md](provenance.md).
