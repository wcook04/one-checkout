#!/usr/bin/env python3
"""Compare real Git effects with caller knowledge in disposable repositories."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/one-checkout/scripts"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("missing source module: " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def demo() -> dict:
    core = load("recovery_demo_core", SCRIPTS / "one_checkout.py")
    fixtures = load("recovery_demo_fixtures", SCRIPTS / "demo.py")
    cases = []
    modes = ("request_not_delivered", "acknowledgement_lost", "index_settlement_failed", "settled")
    with tempfile.TemporaryDirectory(prefix="one-checkout-recovery-demo-",
                                     dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as raw:
        root = Path(raw).resolve()
        for mode in modes:
            fixture = fixtures.Fixture(root / mode)
            # Only this disposable fixture's index is reset to its baseline.
            # Its selected entry must differ after successful settlement.
            fixture.git("read-tree", fixture.parent)
            before = fixture.snapshot()
            record = fixture.folder / "operation.jsonl"
            requests = []
            popen = subprocess.Popen

            class Input:
                def __init__(self, stream):
                    self.stream = stream

                def write(self, data):
                    if data == b"commit\n":
                        requests.append(mode)
                        if mode == "acknowledgement_lost":
                            self.stream.write(data)
                            self.stream.flush()
                            raise BrokenPipeError("fixture loses acknowledgement after delivery")
                        if mode == "request_not_delivered":
                            raise BrokenPipeError("fixture loses request before delivery")
                    return self.stream.write(data)

                def flush(self):
                    return self.stream.flush()

                def close(self):
                    return self.stream.close()

            def launch(args, **kwargs):
                process = popen(args, **kwargs)
                if "update-ref" in args:
                    process.stdin = Input(process.stdin)
                return process

            with ExitStack() as stack:
                stack.enter_context(mock.patch.dict(os.environ, fixture.env, clear=True))
                stack.enter_context(mock.patch.object(core.subprocess, "Popen", side_effect=launch))
                if mode == "index_settlement_failed":
                    stack.enter_context(mock.patch.object(core.GitLock, "publish_index",
                                                          side_effect=OSError("fixture index publication fails")))
                receipt = core.caller_receipt(core.commit_patch(
                    repo=fixture.repo, patch=fixture.patch.read_bytes(), paths=["shared.py"],
                    expected_parent=fixture.parent, message="Synthetic recovery comparison",
                    record_file=record))
            after = fixture.snapshot()
            metadata = fixture.metadata()
            record_bytes = record.read_bytes()
            observations = []
            for _ in range(2):
                observed = fixture.execute([sys.executable, "-B", "-I", str(SCRIPTS / "one_checkout.py"),
                                            "recover", "--record-file", str(record)])
                observations.append(json.loads(observed.stdout))
            observation = observations[-1]
            landed = mode != "request_not_delivered"
            settled = mode == "settled"
            expected_status = ("landing_uncertain" if mode in modes[:2] else
                               "landed_unsettled" if mode == "index_settlement_failed" else "committed")
            checks = {
                "caller_status_matches_boundary": receipt["status"] == expected_status,
                "candidate_object_exists_in_both_uncertain_cases": observation["candidate_object_available"],
                "observed_branch_matches_fixture_effect": observation["candidate_is_current_branch"] == landed,
                "confirmation_matches_received_acknowledgement": receipt["ref_update_confirmed"] == (mode in modes[2:]),
                "index_matches_expected_snapshot": (observation["index_matches_prepared"] == settled
                                                     and observation["index_matches_original"] != settled),
                "working_files_unchanged": all(after[k] == before[k]
                                               for k in ("worktree", "foreign_worktree", "untracked")),
                "one_commit_request_only": len(requests) == 1,
                "automatic_retry_disallowed": receipt["automatic_retry_allowed"] is False
                                              and all(o["automatic_retry_allowed"] is False for o in observations),
                "repeated_recovery_does_not_mutate_repository_or_record": fixture.metadata() == metadata
                                              and fixture.snapshot() == after and record.read_bytes() == record_bytes,
            }
            if not all(checks.values()):
                raise RuntimeError(mode + " failed: " + json.dumps(checks))
            cases.append({"case": mode, "checks": checks, "receipt": receipt,
                          "recovery": observation, "commit_requests": len(requests)})
        versions = {"python": sys.version.split()[0], "git": fixture.git("--version").decode().strip()}
    return {"schema": "one_checkout_recovery_demo_v1", "status": "demo_passed", "cases": cases,
            "versions": versions, "runtime_sha256": hashlib.sha256((SCRIPTS / "one_checkout.py").read_bytes()).hexdigest(),
            "fixture_removed_on_exit": True,
            "limits": "Sequential injected faults around real local Git; not a scheduler, live-agent trial, "
                      "power-loss test or atomic recovery snapshot. A missing branch/reflog observation "
                      "does not prove nonlanding. Recovery never authorizes retry or repairs the index."}


def render_human(result: dict) -> str:
    lines = ["ONE CHECKOUT RECOVERY: caller knowledge and observed effects", "",
             "Case                         Caller status       Candidate on branch  Index", ""]
    for case in result["cases"]:
        observed = case["recovery"]
        index = "prepared" if observed["index_matches_prepared"] else "original"
        lines.append(f"{case['case']:<28} {case['receipt']['status']:<19} "
                     f"{str(observed['candidate_is_current_branch']):<20} {index}")
    lines.extend(["", "PASS: four cases, one commit request each, automatic_retry_allowed=false.",
                  "PASS: repeated read-only recovery preserved repository and record bytes.",
                  result["limits"], "Disposable fixtures removed."])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--human", action="store_true")
    args = parser.parse_args()
    try:
        result = demo()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "demo_failed", "error": str(error)}), file=sys.stderr)
        return 2
    print(render_human(result) if args.human else json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
