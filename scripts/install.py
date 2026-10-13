#!/usr/bin/env python3
"""Install the self-contained skill for Codex, Claude Code, or both."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import uuid


SOURCE = Path(__file__).resolve().parents[1] / "skills" / "one-checkout"
MEMBERS = ("SKILL.md", "LICENSE", "scripts/one_checkout.py")


def same_bundle(source: Path, destination: Path) -> bool:
    if destination.is_symlink() or not destination.is_dir():
        return False
    actual = {str(p.relative_to(destination)) for p in destination.rglob("*")
              if p.is_file() or p.is_symlink()}
    if actual != set(MEMBERS):
        return False
    return all(not (destination / name).is_symlink()
               and (destination / name).read_bytes() == (source / name).read_bytes()
               for name in MEMBERS)


def install_skill(source: Path, destinations: list[Path], *,
                  backup_existing: bool = False, dry_run: bool = False) -> list[dict]:
    for name in MEMBERS:
        member = source / name
        if member.is_symlink() or not member.is_file():
            raise ValueError(f"missing or symlinked skill member: {name}")
    plans = []
    for destination in destinations:
        if destination.is_symlink():
            raise ValueError(f"refusing a symlink destination: {destination}")
        exists = destination.exists()
        if exists and not destination.is_dir():
            raise ValueError(f"destination is not a directory: {destination}")
        identical = exists and same_bundle(source, destination)
        if exists and not identical and not backup_existing:
            raise ValueError(f"destination differs: {destination}; inspect it first, "
                             "then use --backup-existing to retain it and install")
        plans.append({"destination": str(destination),
                      "action": "unchanged" if identical else "backup-and-install" if exists else "install"})
    # Preflight every destination before creating either agent's skill.
    if dry_run:
        return plans
    for plan in plans:
        if plan["action"] == "unchanged":
            continue
        destination = Path(plan["destination"])
        backup = None
        if plan["action"] == "backup-and-install":
            # Recheck immediately before moving the existing directory.
            if destination.is_symlink() or not destination.is_dir():
                raise ValueError(f"destination changed during installation: {destination}")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            # Backups stay outside skills/ so agents cannot discover two copies.
            backup_root = destination.parent.parent / "one-checkout-backups"
            backup_root.mkdir(parents=True, exist_ok=True)
            backup = backup_root / f"{stamp}-{uuid.uuid4().hex}"
            destination.rename(backup)
            plan["backup"] = str(backup)
        destination.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            # Exclusive mkdir refuses a destination created after preflight.
            destination.mkdir()
            created = True
            for name in MEMBERS:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / name, target)
        except BaseException:
            if created:
                shutil.rmtree(destination)
            if backup is not None and not destination.exists() and not destination.is_symlink():
                backup.rename(destination)
            raise
    return plans


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("both", "codex", "claude"), default="both")
    parser.add_argument("--scope", choices=("user", "project"), default="user")
    parser.add_argument("--project", type=Path, default=Path.cwd(),
                        help="project directory for --scope project (default: current directory)")
    parser.add_argument("--backup-existing", action="store_true",
                        help="retain a differing installed skill in a sibling backup directory")
    parser.add_argument("--dry-run", action="store_true", help="inspect destinations without writing")
    args = parser.parse_args()
    base = Path.home() if args.scope == "user" else args.project.resolve()
    if not base.is_dir():
        parser.error("installation base must be an existing directory")
    agents = ("codex", "claude") if args.agent == "both" else (args.agent,)
    roots = {"codex": ".agents", "claude": ".claude"}
    destinations = [base / roots[agent] / "skills" / "one-checkout" for agent in agents]
    try:
        plans = install_skill(SOURCE, destinations, backup_existing=args.backup_existing,
                              dry_run=args.dry_run)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "dry_run": args.dry_run, "skills": plans}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
