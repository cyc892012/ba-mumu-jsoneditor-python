"""确认加载期间外部修改文件时，内存、版本基线和磁盘保持一致。"""
from unittest import TestCase

import test_migration_main_transactions as main_tests


class SchemeLoadRaceTests(TestCase):
    setUp = main_tests.MainTransactionMigrationTests.setUp

    def test_load_reads_new_content_after_confirmation(self):
        other = self.root / "B服-new.json"
        old = main_tests.scheme(.2)
        latest = main_tests.scheme(.9)
        other.write_text(old, encoding="utf-8")
        self.win._backup_mgr.record_change(main_tests.scheme(.7))

        def approve(*args, **kwargs):
            other.write_text(latest, encoding="utf-8")
            return True

        self.ask.side_effect = approve
        self.assertTrue(self.win._load_scheme_path(str(other), manual=True))
        self.assertEqual(self.win._json_str, latest)
        self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 1)
        self.assertEqual(self.win._json_path, str(other))

    def test_file_corrupted_during_confirmation_preserves_loaded_scheme(self):
        other = self.root / "B服-new.json"
        other.write_text(main_tests.scheme(.2), encoding="utf-8")
        before = self.win._json_str
        self.win._backup_mgr.record_change(main_tests.scheme(.7))

        def approve(*args, **kwargs):
            other.write_text("[broken", encoding="utf-8")
            return True

        self.ask.side_effect = approve
        self.assertFalse(self.win._load_scheme_path(str(other), manual=True))
        self.assertEqual(self.win._json_str, before)
        self.assertEqual(self.win._json_path, str(self.path))
        self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 2)

    def test_file_deleted_during_confirmation_does_not_load_cached_text(self):
        other = self.root / "B服-new.json"
        other.write_text(main_tests.scheme(.2), encoding="utf-8")
        self.win._backup_mgr.record_change(main_tests.scheme(.7))
        self.ask.side_effect = lambda *a, **k: (other.unlink(), True)[1]
        self.assertFalse(self.win._load_scheme_path(str(other), manual=True))
        self.assertEqual(self.win._json_path, str(self.path))
