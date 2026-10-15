#!/usr/bin/env python3
"""Test-only, instrumented caller. Every Git operation uses the real implementation.

The controller owns the pipes and enforces deadlines. Never run against a live repo.
This is not part of the installed skill and adds no production fault-injection API.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import time

WORKER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True), flush=True)


def worker(config_path: Path) -> int:
    config = json.loads(config_path.read_text())
    spec = importlib.util.spec_from_file_location("stress_target", config["tool"])
    assert spec and spec.loader
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    events: list[dict] = []
    processes: list[dict] = []
    ref_pid = None
    candidate = None
    lock_start = None
    lock_end = None
    started = time.monotonic()

    def gate(name: str) -> None:
        event = {"event": name, "pid": os.getpid(), "ref_pid": ref_pid,
                 "candidate": candidate, "seconds": time.monotonic() - started}
        events.append(event)
        if name in config.get("gates", []):
            emit(event)
            if sys.stdin.readline() != "continue\n":
                raise RuntimeError("controller disconnected at test barrier")

    popen = core.subprocess.Popen

    def observed_popen(args, *positional, **kwargs):
        nonlocal ref_pid
        process = popen(args, *positional, **kwargs)
        processes.append({"pid": process.pid, "argv": list(args)})
        if "update-ref" in args:
            ref_pid = process.pid
        return process

    core.subprocess.Popen = observed_popen
    # Preserve the snapshot barrier for both current and older --tool pins.
    # The combined reader supersedes _entries only for shared-index snapshots.
    reader_name = "_index_state" if hasattr(core, "_index_state") else "_entries"
    read_index = getattr(core, reader_name)

    def observed_entries(git, env, *args, **kwargs):
        result = read_index(git, env, *args, **kwargs)
        if Path(env.get("GIT_INDEX_FILE", "")).name == "original-index":
            gate("snapshot")
        return result

    setattr(core, reader_name, observed_entries)
    verify = core._verify_prepared_head

    def observed_verify(*args, **kwargs):
        result = verify(*args, **kwargs)
        gate("prepare")
        return result

    core._verify_prepared_head = observed_verify
    publish_ref = core._publish_ref

    def observed_ref(git, branch, new_commit, parent, *args, **kwargs):
        nonlocal candidate
        candidate = new_commit
        result = publish_ref(git, branch, new_commit, parent, *args, **kwargs)
        gate("ack")  # Acknowledged, Git child has exited; settlement is still pending.
        return result

    core._publish_ref = observed_ref
    enter = core.GitLock.__enter__
    leave = core.GitLock.__exit__
    publish_index = core.GitLock.publish_index

    def observed_enter(self, *args, **kwargs):
        nonlocal lock_start
        result = enter(self, *args, **kwargs)
        lock_start = time.monotonic()
        return result

    def observed_leave(self, *args, **kwargs):
        nonlocal lock_end
        try:
            return leave(self, *args, **kwargs)
        finally:
            if lock_end is None:
                lock_end = time.monotonic()

    def observed_index(self, *args, **kwargs):
        nonlocal lock_end
        gate("settlement")  # After the last ref check, before index publication.
        result = publish_index(self, *args, **kwargs)
        lock_end = time.monotonic()
        return result

    core.GitLock.__enter__ = observed_enter
    core.GitLock.__exit__ = observed_leave
    core.GitLock.publish_index = observed_index
    gate("start")
    try:
        receipt = core.commit_patch(
            repo=config["repo"], patch=Path(config["patch"]).read_bytes(),
            paths=config["paths"], expected_parent=config["parent"],
            message="Synthetic stress candidate", dry_run=config.get("dry_run", False),
        )
        code = 3 if receipt["status"] in ("landing_uncertain", "landed_unsettled") else 0
    except (core.Refusal, OSError, UnicodeError) as error:
        receipt = {"status": "refused", "code": getattr(error, "code", "io_error"),
                   "message": str(error)}
        code = 2
    except Exception as error:
        receipt = {"status": "worker_error", "type": type(error).__name__, "message": str(error)}
        code = 70
    scale = 1024 if sys.platform == "darwin" else 1
    # Linux ru_maxrss can include fork-before-exec high-water memory from the
    # fixture controller. VmHWM measures this executable's mapping high-water.
    executable_rss = None
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                executable_rss = int(line.split()[1])
    except OSError:
        pass
    emit({"event": "result", "receipt": receipt, "events": events, "processes": processes,
          "runtime_sha256": hashlib.sha256(Path(config["tool"]).read_bytes()).hexdigest(),
          "worker_sha256": WORKER_SHA256,
          "metrics": {"elapsed_seconds": time.monotonic() - started,
                      "index_lock_seconds": (lock_end - lock_start)
                      if lock_start is not None and lock_end is not None else None,
                      "peak_executable_rss_kib_linux": executable_rss,
                      "peak_self_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale,
                      "peak_child_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / scale,
                      "python_spawned_processes": len(processes)}})
    return code


if __name__ == "__main__":
    raise SystemExit(worker(Path(sys.argv[1])))
