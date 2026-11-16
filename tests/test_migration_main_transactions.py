"""主窗离屏事务回归，不启动设备、监听或事件循环。"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from core.backup_manager import BackupManager
from core.config_manager import ConfigManager
from ui import main_window

APP = QApplication.instance() or QApplication([])


def scheme(x=0.5, vk=65, text="A"):
    return json.dumps({"keymaps": [{"type": "Click", "key": {"virtual_key": vk, "text": text},
                      "rel_work_position": {"rel_x": x, "rel_y": 0}}]})


class MainTransactionMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="migration_main_")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = mock.patch.dict(os.environ, {"MOMO_DATA_DIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        config = ConfigManager()
        config.json_folder_path = str(self.root)
        with mock.patch.object(ConfigManager, "shared", return_value=config), \
                mock.patch.object(main_window.QTimer, "singleShot"):
            self.win = main_window.MainWindow()
        self.win.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.addCleanup(self.win.deleteLater)
        self.addCleanup(self.win.close)
        for name in ("_try_auto_sync", "_start_index_backup_timer", "_try_index_backup"):
            patcher = mock.patch.object(self.win, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.path = self.root / "官服-a.json"
        self.path.write_text(scheme(), encoding="utf-8")
        self.win._json_path = str(self.path)
        self.win._json_str = scheme()
        self.win._backup_mgr.record_initial(scheme())
        self.question = mock.patch.object(main_window.MessageDialog, "question", return_value=True)
        self.ask = self.question.start()
        self.addCleanup(self.question.stop)

    def status(self):
        return self.win._status_label.text()

    def test_failed_write_keeps_memory_history_and_index(self):
        before = self.win._json_str
        with mock.patch.object(self.win, "_atomic_write_json", return_value=False), \
                mock.patch.object(self.win, "_schedule_index_damage_check") as schedule:
            self.assertFalse(self.win.try_set_mumu_json_and_save(scheme(.8)))
            self.assertEqual(self.win._json_str, before)
            self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 1)
            self.assertFalse(self.win._needs_index_backup)
            schedule.assert_not_called()
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_success_commits_once_and_enables_undo(self):
        self.assertTrue(self.win.try_set_mumu_json_and_save(scheme(.8)))
        self.assertEqual(self.path.read_text(encoding="utf-8"), scheme(.8))
        self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 2)
        self.assertTrue(self.win._undo_btn.isEnabled())
        self.assertTrue(self.win.try_set_mumu_json_and_save(scheme(.8)))
        self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 2)

    def test_undo_failure_keeps_history_position(self):
        self.win._backup_mgr.record_change(scheme(.8))
        self.win._json_str = scheme(.8)
        with mock.patch.object(self.win, "_atomic_write_json", return_value=False):
            self.assertFalse(self.win._on_undo())
        self.assertEqual(self.win._backup_mgr.get_current_index(), 1)
        self.assertEqual(self.win._json_str, scheme(.8))

    def test_broken_load_does_not_mix_old_content_and_new_path(self):
        bad = self.root / "B服-b.json"
        bad.write_text("[bad", encoding="utf-8")
        self.assertFalse(self.win._load_scheme_path(str(bad), manual=True))
        self.assertEqual(self.win._json_path, str(self.path))
        self.assertEqual(self.win._json_str, scheme())

    def test_cancel_switch_preserves_true_history_and_path(self):
        other = self.root / "B服-b.json"
        other.write_text(scheme(.2), encoding="utf-8")
        self.win._backup_mgr.record_change(scheme(.8))
        self.win._undo_btn.setEnabled(False)
        self.ask.return_value = False
        self.assertFalse(self.win._load_scheme_path(str(other), manual=True))
        self.assertEqual(self.win._json_path, str(self.path))
        self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 2)

    def test_same_unchanged_load_preserves_history(self):
        self.win._backup_mgr.record_change(scheme())
        self.assertTrue(self.win._load_scheme_path(str(self.path)))
        self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 2)
        self.ask.assert_not_called()

    def test_external_reject_is_deduplicated_and_old_path_ignored(self):
        self.ask.return_value = False
        self.win._on_external_file_changed(str(self.path))
        self.win._on_external_file_changed(str(self.path))
        self.win._on_external_file_changed(str(self.root / "old.json"))
        self.ask.assert_called_once()
        self.assertEqual(self.win._json_str, scheme())

    def test_default_and_current_resolution_are_separate(self):
        self.win._config.fx = "1920"
        self.win._config.fy = "1080"
        self.win._current_fx, self.win._current_fy = "1920", "1080"
        self.win._sync_resolution_controls()
        self.assertEqual(self.win._ratio_cb.currentText(), self.win._classify_resolution("1920x1080"))
        self.win._apply_current_resolution(2560, 1440)
        self.assertEqual((self.win._config.fx, self.win._config.fy), ("1920", "1080"))
        self.assertTrue(self.win._read_resolution())
        self.assertEqual((self.win._get_fx(), self.win._get_fy()), (1920, 1080))

    def test_failed_default_load_keeps_current_and_reports_failure(self):
        self.win._apply_current_resolution(1920, 1080)
        with mock.patch.object(self.win._config, "reload", return_value=False):
            self.assertFalse(self.win._read_resolution())
        self.assertEqual((self.win._get_fx(), self.win._get_fy()), (1920, 1080))
        self.assertIn("失败", self.status())

    def test_read_zero_and_fraction_preserves_relative_value(self):
        self.win._key_input.setText("A")
        self.win._key_input._vk = 65
        self.assertTrue(self.win._read_key_coord())
        self.assertEqual(self.win.jsx_input.text(), "0.5")
        self.assertEqual(self.win.kx_input.text(), "639.5")
        self.assertEqual(self.win.jsy_input.text(), "0")

    def test_invalid_input_does_not_fallback_to_old_developer_coordinate(self):
        self.win._key_input.setText("A")
        self.win._key_input._vk = 65
        self.win.jsx_input.setText("invalid")
        with mock.patch.object(self.win, "try_set_mumu_json_and_save") as commit:
            self.assertFalse(self.win._modify_save_key())
            commit.assert_not_called()
        self.ask.assert_not_called()

    def test_two_locks_do_not_report_conversion_complete(self):
        self.win.k_lock.setChecked(True)
        self.win.js_lock.setChecked(True)
        self.assertTrue(self.win.kx_input.isReadOnly())
        self.assertTrue(self.win.jsx_input.isReadOnly())
        self.assertFalse(self.win._on_convert())
        self.assertNotIn("完成", self.status())


if __name__ == "__main__":
    unittest.main()
