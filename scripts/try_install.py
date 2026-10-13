#!/usr/bin/env python3
"""Verify both agents at project and user scope inside a disposable sandbox."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def verify_install(root: Path = ROOT) -> dict:
    root = root.resolve()
    source = root / "skills/one-checkout"
    spec = importlib.util.spec_from_file_location("install_demo_source", root / "scripts/install.py")
    assert spec and spec.loader
    installer = importlib.util.module_from_spec(spec)
    previous_bytecode_setting = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(installer)
    finally:
        sys.dont_write_bytecode = previous_bytecode_setting
    # Use the owning installer admission API on the ORIGINAL source inventory:
    # an undeclared resource must fail, rather than disappear during copying.
    payload = installer.snapshot(source)
    members = list(payload)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, (data, _) in payload.items()}
    with tempfile.TemporaryDirectory(prefix="one-checkout-install-demo-",
                                     dir=os.environ.get("ONE_CHECKOUT_TEST_TMPDIR")) as raw:
        scratch = Path(raw).resolve()
        project, home, temporary = (scratch / name for name in ("project", "home", "tmp"))
        for path in (project, home, temporary):
            path.mkdir()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("GIT_") and k not in ("PYTHONPATH", "PYTHONHOME", "CLAUDE_CONFIG_DIR")}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   TMPDIR=str(temporary), ONE_CHECKOUT_TEST_TMPDIR=str(temporary))
        def run(script: Path, *args: str) -> dict:
            result = subprocess.run([sys.executable, "-B", "-I", str(script), *args],
                                    cwd=project, env=env, capture_output=True, timeout=60)
            if result.returncode:
                raise RuntimeError(f"{script.name} exited {result.returncode}: "
                                   + result.stderr.decode("utf-8", "replace").strip())
            return json.loads(result.stdout)
        # Snapshot only the declared bundle and installer. No Git history, user
        # configuration or unrelated source files enter this disposable copy.
        copied_source = scratch / "source"
        copied_bundle = copied_source / "skills/one-checkout"
        for member, (data, mode) in payload.items():
            target = copied_bundle / member
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(mode)
        copied_installer = copied_source / "scripts/install.py"
        copied_installer.parent.mkdir(parents=True)
        shutil.copy2(root / "scripts/install.py", copied_installer)
        if installer.snapshot(source) != payload:
            raise RuntimeError("source changed during the disposable snapshot")
        for scope in ("project", "user"):
            arguments = ("--agent", "both", "--scope", scope, "--project", str(project))
            installed = run(copied_installer, *arguments)
            if not installed.get("ok") or [p["action"] for p in installed["skills"]] != ["install", "install"]:
                raise RuntimeError(f"initial {scope} installation did not create both skills")
            reinstalled = run(copied_installer, *arguments)
            if [p["action"] for p in reinstalled["skills"]] != ["unchanged", "unchanged"]:
                raise RuntimeError(f"identical {scope} reinstall was not a no-op")
        # Remove our own temporary source only, before exercising installed
        # entrypoints. Never rename or remove the caller's source checkout.
        shutil.rmtree(copied_source)
        if copied_source.exists():
            raise RuntimeError("disposable source remains before installed checks")
        agents = []
        for scope, base, name, folder in (
                ("project", project, "codex", ".agents"),
                ("project", project, "claude", ".claude"),
                ("user", home, "codex", ".agents"),
                ("user", home, "claude", ".claude")):
            destination = base / folder / "skills/one-checkout"
            actual_members = {str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()}
            if actual_members != set(members) or any(p.is_symlink() for p in destination.rglob("*")):
                raise RuntimeError("installed skill has unexpected members or symlinks")
            actual = {member: hashlib.sha256((destination / member).read_bytes()).hexdigest()
                      for member in members}
            if actual != hashes:
                raise RuntimeError("installed skill differs from source")
            demonstration = run(destination / "scripts/demo.py")
            if demonstration.get("status") != "demo_passed" or not all(demonstration["checks"].values()):
                raise RuntimeError("installed runtime failed the demonstration")
            doctor = run(destination / "scripts/doctor.py")
            if doctor.get("ok") is not True or doctor.get("status") != "doctor_passed":
                raise RuntimeError("installed doctor failed")
            agents.append({"agent": name, "scope": scope,
                           "destination": str(destination.relative_to(scratch)),
                           "hashes_match": True, "demo_checks": demonstration["checks"],
                           "doctor_status": doctor["status"]})
        if {path.name for path in home.iterdir()} != {".agents", ".claude"}:
            raise RuntimeError("unexpected writes to the isolated user home")
        result = {"schema": "one_checkout_install_demo_v1", "status": "install_demo_passed",
                  "agents": agents, "source_sha256": hashes, "reinstall_unchanged": True,
                  "installed_copies_tested": len(agents), "user_scope_used_isolated_home": True,
                  "source_snapshot_removed_before_checks": True,
                  "source_kind": "validated current-source snapshot; clean-commit provenance not checked",
                  "global_install_performed": False, "live_agent_discovery_tested": False}
    if scratch.exists():
        raise RuntimeError("temporary project was not removed")
    result["scratch_removed"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--human", action="store_true", help="show a readable verification summary")
    args = parser.parse_args()
    try:
        result = verify_install()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "install_demo_failed", "error": str(exc)}), file=sys.stderr)
        return 2
    if args.human:
        print("DISPOSABLE INSTALLATION CHECK")
        print("Testing a validated current-source snapshot; clean-commit provenance was not checked.")
        for agent in result["agents"]:
            print(f"PASS {agent['scope']}/{agent['agent']}: exact skill copy and all {len(agent['demo_checks'])} demo checks")
        print("PASS identical reinstall leaves all four copies unchanged")
        print("PASS installed demo and doctor run after the disposable source copy is removed")
        print("PASS temporary project, isolated home and fixture repositories removed")
        print("Runtime SHA-256: " + result["source_sha256"]["scripts/one_checkout.py"])
        print("No global installation or live Claude Code/Codex session was performed; user scope used an isolated HOME.")
    else:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
