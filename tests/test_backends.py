"""Bounded genuine matrix fixtures; full60-case campaign remains explicit."""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('backends', ROOT / 'scripts/check_backends.py')
assert SPEC and SPEC.loader
backends = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backends)


@unittest.skipUnless(os.name == 'posix' and shutil.which('git'), 'requires POSIX and Git')
class BackendTests(unittest.TestCase):
    def test_matrix_declares_sixty_distinct_real_cases(self):
        rows = list(backends.configurations())
        self.assertEqual(len(rows), 60)
        self.assertEqual(len(set(rows)), 60)
        self.assertNotIn(('reftable', 'sha1', 'packed', 'same_file'), rows)

    def test_native_files_backend_and_same_oid_branch_races(self):
        with tempfile.TemporaryDirectory(prefix='oc-backends-unit-', dir=os.environ.get('ONE_CHECKOUT_TEST_TMPDIR')) as d:
            root = Path(d)
            tool, _ = backends.stress.freeze_runtime(backends.stress.TOOL, root)
            for number, (state, case) in enumerate((('ordinary', 'same_file'),
                                                 ('packed', 'switch_before_prepare'),
                                                 ('linked', 'switch_while_prepared'),
                                                 ('ordinary', 'detached_same_file'),
                                                 ('packed', 'detached_attach_before_prepare'),
                                                 ('linked', 'detached_attach_while_prepared'))):
                with self.subTest(state=state, case=case):
                    created = []
                    ordinary_tempdir = tempfile.TemporaryDirectory
                    def observed_tempdir(*args, **kwargs):
                        context = ordinary_tempdir(*args, **kwargs)
                        created.append(Path(context.name))
                        return context
                    case_root = root / str(number)
                    with mock.patch.object(tempfile, 'TemporaryDirectory', observed_tempdir):
                        result = backends.backend_case(case_root, tool, 'files', 'sha1', state, case)
                    self.assertEqual(result['classification'], 'pass', result)
                    self.assertTrue(created)
                    self.assertTrue(all(path.is_relative_to(case_root) for path in created), created)
                    self.assertTrue(result['checks']['index_bytes_unchanged'])
                    if state == 'linked':
                        self.assertTrue(result['gitdir_distinct_from_common'])
                        if case.startswith('detached_'):
                            self.assertTrue(result['checks']['primary_head_identity_unchanged'])


if __name__ == '__main__':
    unittest.main()
