#!/usr/bin/env python3
"""Report local prerequisites; optionally exercise this installed skill."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true', help='run the bundled demo in a disposable repository')
    args = parser.parse_args()
    git = shutil.which('git')
    version = None
    error = None
    if git:
        try:
            result = subprocess.run([git, '--version'], capture_output=True, text=True, timeout=10)
            match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', result.stdout)
            if result.returncode == 0 and match:
                version = tuple(int(v or 0) for v in match.groups())
        except (OSError, subprocess.SubprocessError) as exc:
            error = str(exc)
    identity = {'GIT_AUTHOR_NAME','GIT_AUTHOR_EMAIL','GIT_AUTHOR_DATE',
                'GIT_COMMITTER_NAME','GIT_COMMITTER_EMAIL','GIT_COMMITTER_DATE'}
    controls = sorted(k for k in os.environ if k.startswith('GIT_') and k not in identity)
    ok = platform.system() in ('Darwin', 'Linux') and sys.version_info >= (3, 10) and version is not None and version >= (2, 39, 0) and not controls
    report = {'ok': ok, 'status': 'doctor_passed' if ok else 'doctor_failed',
              'python': {'executable': sys.executable, 'version': platform.python_version()},
              'git': {'executable': git, 'version': version},
              'platform': platform.system(), 'git_control_variables': controls,
              'agent_discovery_verified': False}
    if error:
        report['error'] = error
    if args.self_test and ok:
        try:
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('demo.py'))],
                                    capture_output=True, text=True, timeout=90)
            report['self_test'] = json.loads(result.stdout) if result.stdout else {'error': result.stderr.strip()}
            report['ok'] = result.returncode == 0
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            report.update(ok=False, error=str(exc))
        report['status'] = 'doctor_passed' if report['ok'] else 'doctor_failed'
    print(json.dumps(report, sort_keys=True))
    return 0 if report['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
