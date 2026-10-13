import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installed_doctor', Path(__file__).resolve().parents[1] / 'skills/one-checkout/scripts/doctor.py')
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class Doctor(unittest.TestCase):
    def probe(self, version, controls=None):
        output = io.StringIO()
        env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        env.update(controls or {})
        with patch.dict(os.environ, env, clear=True), patch.object(sys,'argv',['doctor.py']), \
                patch.object(doctor.platform,'system',return_value='Linux'), \
                patch.object(doctor.shutil,'which',return_value='/synthetic/git'), \
                patch.object(doctor.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'git version '+version,'')), \
                contextlib.redirect_stdout(output):
            code = doctor.main()
        return code, json.loads(output.getvalue())

    def test_supported_and_older_git_versions_have_different_verdicts(self):
        self.assertEqual(self.probe('2.39.0')[0], 0)
        self.assertEqual(self.probe('2.38.9')[0], 2)

    def test_inherited_git_controls_refuse_but_identity_is_allowed(self):
        code, receipt = self.probe('2.39.0', {'GIT_DIR':'/synthetic/other'})
        self.assertEqual(code, 2)
        self.assertEqual(receipt['git_control_variables'], ['GIT_DIR'])
        self.assertFalse(receipt['agent_discovery_verified'])
        self.assertEqual(self.probe('2.39.0', {'GIT_AUTHOR_NAME':'Synthetic'})[0], 0)


if __name__ == '__main__':
    unittest.main()
