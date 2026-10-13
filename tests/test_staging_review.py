"""Receipt metadata describes snapshots, not ownership or an automatic rebase."""
from __future__ import annotations

import os
import unittest
from unittest import mock

import test_one_checkout as original


class StagingReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.f = original.OneCheckoutTests('runTest')
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()

    def prepare_mixed(self) -> bytes:
        f = self.f
        patch = f.patch_for('shared.py', f.base.replace('line 01', 'A owned').encode())
        (f.repo / 'shared.py').write_text(f.base.replace('line 12', 'B stage'))
        f.git('add', '--', 'shared.py')
        (f.repo / 'shared.py').write_text(f.base.replace('line 01', 'A owned').replace('line 12', 'B stage'))
        return patch

    def test_review_identifies_all_three_entries_without_source_contents(self) -> None:
        f = self.f
        patch = self.prepare_mixed()
        base = f.git('rev-parse', f'{f.parent}:shared.py').stdout.decode().strip()
        staged = f.git('rev-parse', ':shared.py').stdout.decode().strip()
        receipt = f.land(patch)
        candidate = f.git('rev-parse', 'HEAD:shared.py').stdout.decode().strip()
        self.assertEqual(receipt['staging_review'], [{
            'path': 'shared.py', 'parent_entry': {'mode': '100644', 'oid': base},
            'preserved_index_entry': {'mode': '100644', 'oid': staged},
            'candidate_entry': {'mode': '100644', 'oid': candidate}, 'preserved_flags': 0}])
        self.assertEqual(receipt['warnings'][0]['code'], 'preserved_staging_differs')
        self.assertNotIn('B stage', str(receipt))

    def test_plan_reports_review_without_writing_metadata(self) -> None:
        patch = self.prepare_mixed()
        f = self.f
        before = f.metadata()
        mtime = (f.repo / '.git/index').stat().st_mtime_ns
        result = f.land(patch, dry_run=True)
        self.assertEqual(result['status'], 'planned')
        self.assertTrue(result['staging_review'])
        self.assertEqual(before, f.metadata())
        self.assertEqual(mtime, (f.repo / '.git/index').stat().st_mtime_ns)

    def test_clean_entry_needs_no_staging_review(self) -> None:
        f = self.f
        result = f.land(f.patch_for('shared.py', b'new candidate\n'))
        self.assertEqual(result['warnings'], [])
        self.assertEqual(result['staging_review'], [])

    def test_already_staged_candidate_is_not_a_divergent_snapshot(self) -> None:
        f = self.f
        patch = f.patch_for('shared.py', b'new candidate\n')
        (f.repo / 'shared.py').write_bytes(b'new candidate\n')
        f.git('add', '--', 'shared.py')
        result = f.land(patch)
        self.assertEqual(result['preserved_staged_paths'], ['shared.py'])
        self.assertEqual(result['staging_review'], [])
        self.assertEqual(result['warnings'], [])

    def test_staged_deletion_has_null_preserved_entry(self) -> None:
        f = self.f
        patch = f.patch_for('shared.py', b'new candidate\n')
        f.git('rm', '--cached', '--', 'shared.py')
        review = f.land(patch)['staging_review'][0]
        self.assertIsNone(review['preserved_index_entry'])
        self.assertIsNotNone(review['parent_entry'])
        self.assertIsNotNone(review['candidate_entry'])

    def test_candidate_deletion_has_null_candidate_entry(self) -> None:
        f = self.f
        (f.repo / 'shared.py').unlink()
        patch = f.git('diff', '--binary', f.parent, '--', 'shared.py').stdout
        (f.repo / 'shared.py').write_bytes(b'foreign staged\n')
        f.git('add', '--', 'shared.py')
        review = f.land(patch)['staging_review'][0]
        self.assertIsNone(review['candidate_entry'])
        self.assertIsNotNone(review['preserved_index_entry'])

    @unittest.skipIf(os.name == 'nt', 'POSIX mode fixture')
    def test_mode_difference_is_included(self) -> None:
        f = self.f
        patch = f.patch_for('shared.py', b'new candidate\n')
        f.git('update-index', '--chmod=+x', '--', 'shared.py')
        review = f.land(patch)['staging_review'][0]
        self.assertEqual(review['preserved_index_entry']['mode'], '100755')
        self.assertEqual(review['candidate_entry']['mode'], '100644')

    def test_uncertain_outcome_keeps_review_and_never_claims_settlement(self) -> None:
        f = self.f
        patch = self.prepare_mixed()
        before = (f.repo / '.git/index').read_bytes()
        with mock.patch.object(original.core, '_publish_ref', side_effect=
                               original.core.RefOutcome('injected lost acknowledgement', confirmed=False)):
            result = f.land(patch)
        self.assertEqual(result['status'], 'landing_uncertain')
        self.assertFalse(result['ref_update_confirmed'])
        self.assertTrue(result['staging_review'])
        self.assertEqual(before, (f.repo / '.git/index').read_bytes())


if __name__ == '__main__':
    unittest.main()
