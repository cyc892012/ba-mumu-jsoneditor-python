"""管理组功能回归：全部文件在临时目录，监听和外部窗口均为替身。"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtWidgets import QApplication, QAbstractItemView, QMessageBox, QDialog, QTextEdit
from core.config_manager import ConfigManager
from core.backup_manager import BackupManager
from ui.index_backup_form import IndexBackupForm
from ui.log_viewer_form import LogViewerForm
from ui.undo_history_form import UndoHistoryForm

APP = QApplication.instance() or QApplication([])
INDEX = "com.RoamingStar.BlueArchive.json"
OTHER = "com.YostarJP.BlueArchive.json"


class ManagementFormsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="management_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {"MOMO_DATA_DIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        ConfigManager.reset_shared()
        self.addCleanup(ConfigManager.reset_shared)
        self.keymaps = self.root / "keymaps"
        self.keymaps.mkdir()
        for name in (INDEX, OTHER):
            (self.keymaps / name).write_text('{"Config":[]}', encoding="utf-8-sig")
        ConfigManager.shared().json_folder_path = str(self.keymaps)
        self.windows = []
        self.addCleanup(self.close_windows)

    def close_windows(self):
        for window in self.windows:
            window.close()
            window.deleteLater()
        APP.processEvents()

    def window(self, cls):
        window = cls()
        window.setAttribute(Qt.WA_DontShowOnScreen)
        self.windows.append(window)
        return window

    def index(self):
        window = self.window(IndexBackupForm)
        window.set_current_index_file(INDEX)
        return window

    def backup(self, stamp, config=None):
        folder = self.root / "data" / "index_backups" / Path(INDEX).stem / stamp
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.json").write_text(json.dumps({"Config": config or []}), encoding="utf-8")
        return folder

    def history(self, count=4):
        manager = BackupManager()
        manager.record_initial("snapshot0")
        for i in range(1, count):
            manager.record_change(f"snapshot{i}", "修改", f"说明{i}", i)
        window = self.window(UndoHistoryForm)
        window.set_backup_manager(manager)
        return window, manager

    def test_index_corrupt_entries_and_inserted_backup_keep_identity(self):
        self.backup("2026-11-09_12-00-00-000", [{"正确": {}}])
        broken = self.backup("2026-11-10_12-00-00-000")
        (broken / "index.json").write_text("broken", encoding="utf-8")
        window = self.index()
        window._tbl1.selectRow(0)
        self.backup("2026-11-11_12-00-00-000", [{"错误": {}}])
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             mock.patch.object(window._backup_mgr, "restore_backup", return_value=True) as restore:
            window._on_restore_selected()
        restore.assert_called_once_with(INDEX, "2026-11-09_12-00-00-000")

    def test_index_restore_cancel_preserves_bytes(self):
        self.backup("2026-11-09_12-00-00-000", [{"old": {}}])
        window = self.index()
        window._tbl1.selectRow(0)
        before = (self.keymaps / INDEX).read_bytes()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No), \
             mock.patch.object(window._backup_mgr, "restore_backup") as restore:
            window._on_restore_selected()
        restore.assert_not_called()
        self.assertEqual(before, (self.keymaps / INDEX).read_bytes())

    def test_index_restore_failure_not_erased(self):
        self.backup("2026-11-09_12-00-00-000")
        window = self.index()
        window._tbl1.selectRow(0)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             mock.patch.object(window._backup_mgr, "restore_backup", return_value=False):
            window._on_restore_selected()
        self.assertIn("失败", window._status_label.text())

    def test_index_switch_and_directory_invalidate_rows(self):
        self.backup("2026-11-09_12-00-00-000")
        window = self.index()
        window._index_cb.setCurrentText(OTHER)
        self.assertEqual(window._tbl1.rowCount(), 0)
        destination = self.root / "other"
        destination.mkdir()
        (destination / INDEX).write_text('{"Config":[]}', encoding="utf-8")
        window.set_index_directory(str(destination))
        self.assertEqual(Path(window._backup_mgr.index_directory), destination)
        self.assertEqual(window._tbl2.rowCount(), 0)

    def test_index_backup_selection_survives_refresh(self):
        self.backup("2026-11-09_12-00-00-000")
        window = self.index()
        window._tbl1.selectRow(0)
        identity = window._backup_identity(0)
        self.backup("2026-11-11_12-00-00-000")
        window._on_refresh()
        self.assertEqual(window._backup_identity(window._tbl1.currentRow()), identity)

    def test_index_confirmation_target_change_rejects_restore(self):
        self.backup("2026-11-09_12-00-00-000")
        window = self.index()
        window._tbl1.selectRow(0)
        def confirm(*args):
            window._index_cb.setCurrentText(OTHER)
            return QMessageBox.Yes
        with mock.patch.object(QMessageBox, "question", side_effect=confirm), \
             mock.patch.object(window._backup_mgr, "restore_backup") as restore:
            window._on_restore_selected()
        restore.assert_not_called()

    def test_index_fill_uses_scanned_real_path_not_display(self):
        source = self.keymaps / (Path(INDEX).stem + "-a.JSON")
        source.write_text("{}", encoding="utf-8")
        window = self.index()
        self.assertEqual(window._tbl2.rowCount(), 1)
        window._tbl2.item(0, 1).setText("伪显示")
        window._tbl2.item(0, 0).setCheckState(Qt.Checked)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            window._on_fill_missing()
        result = json.loads((self.keymaps / INDEX).read_text(encoding="utf-8-sig"))
        self.assertEqual(result["Config"][0]["a"]["path"], str(source).replace("\\", "/"))

    def test_index_fill_failure_preserves_selection_and_file(self):
        (self.keymaps / (Path(INDEX).stem + "-a.json")).write_text("{}", encoding="utf-8")
        window = self.index()
        window._tbl2.item(0, 0).setCheckState(Qt.Checked)
        before = (self.keymaps / INDEX).read_bytes()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             mock.patch.object(window._backup_mgr, "add_schemes_to_index", return_value=0):
            window._on_fill_missing()
        self.assertIn("失败", window._status_label.text())
        self.assertEqual(window._tbl2.item(0, 0).checkState(), Qt.Checked)
        self.assertEqual(before, (self.keymaps / INDEX).read_bytes())

    def test_index_fill_cancel_and_clean_cancel_do_nothing(self):
        (self.keymaps / (Path(INDEX).stem + "-a.json")).write_text("{}", encoding="utf-8")
        window = self.index()
        window._tbl2.item(0, 0).setCheckState(Qt.Checked)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No), \
             mock.patch.object(window._backup_mgr, "add_schemes_to_index") as fill, \
             mock.patch.object(window._backup_mgr, "cleanup_old_backups") as clean:
            window._on_fill_missing()
            window._on_clean_old_backups()
        fill.assert_not_called()
        clean.assert_not_called()

    def test_index_refresh_discovers_external_file(self):
        window = self.index()
        extra = self.keymaps / "com.nexon.bluearchive.json"
        extra.write_text('{"Config":[]}', encoding="utf-8")
        window._on_refresh()
        self.assertGreaterEqual(window._index_cb.findText(extra.name), 0)
        self.assertEqual(window._index_cb.currentText(), INDEX)

    def test_index_backend_exception_is_failure(self):
        window = self.index()
        with mock.patch.object(window._backup_mgr, "backup_index_direct", side_effect=OSError("blocked")):
            window._on_manual_backup()
        self.assertIn("失败", window._status_label.text())
        self.assertNotIn("刷新完成", window._status_label.text())

    def test_index_scan_failure_invalidates_old_fill(self):
        (self.keymaps / (Path(INDEX).stem + "-a.json")).write_text("{}", encoding="utf-8")
        window = self.index()
        window._tbl2.item(0, 0).setCheckState(Qt.Checked)
        with mock.patch.object(window._backup_mgr, "scan_orphan_schemes", side_effect=OSError("扫描失败")):
            window._on_scan_missing()
        self.assertEqual(window._tbl2.rowCount(), 0)
        self.assertFalse(window._btn_fill.isEnabled())

    def test_index_registered_during_confirm_cannot_duplicate_fill(self):
        (self.keymaps / (Path(INDEX).stem + "-a.json")).write_text("{}", encoding="utf-8")
        window = self.index()
        window._tbl2.item(0, 0).setCheckState(Qt.Checked)
        def confirm(*args):
            (self.keymaps / INDEX).write_text('{"Config":[{"a":{}}]}', encoding="utf-8")
            return QMessageBox.Yes
        with mock.patch.object(QMessageBox, "question", side_effect=confirm), \
             mock.patch.object(window._backup_mgr, "add_schemes_to_index") as fill:
            window._on_fill_missing()
        fill.assert_not_called()
        self.assertIn("变化", window._status_label.text())

    def test_index_cleanup_none_compatibility_false_failure(self):
        window = self.index()
        for value, message in ((None, "已清理"), (False, "清理失败")):
            window._backup_mgr.last_error = "上次操作旧错误"
            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
                 mock.patch.object(window._backup_mgr, "cleanup_old_backups", return_value=value):
                window._on_clean_old_backups()
            self.assertIn(message, window._status_label.text())

    def test_tables_are_readonly(self):
        for window, table in ((self.index(), "_tbl1"), (self.window(LogViewerForm), "_tbl"), (self.history()[0], "_tbl")):
            self.assertEqual(getattr(window, table).editTriggers(), QAbstractItemView.NoEditTriggers)

    def log(self):
        folder = self.root / "Logs"
        folder.mkdir(exist_ok=True)
        for name in ("log_a.txt", "log_b.txt"):
            (folder / name).write_text("日志正文", encoding="utf-8-sig")
        return self.window(LogViewerForm), folder

    def test_log_row_identity_does_not_use_all_files_position(self):
        window, folder = self.log()
        window._tbl.selectRow(0)
        selected = window._selected_log()
        window._all_files.reverse()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            window._on_delete_log()
        self.assertFalse(Path(selected[0]).exists())
        self.assertEqual(len(list(folder.glob("*.txt"))), 1)
        self.assertIn("已删除", window._lbl_status.text())

    def test_log_bom_readonly_viewer(self):
        window, _ = self.log()
        window._tbl.selectRow(0)
        seen = []
        def inspect(dialog):
            text = dialog.findChild(QTextEdit)
            seen.append((text.toPlainText(), text.isReadOnly()))
            return 0
        with mock.patch.object(QDialog, "exec_", inspect):
            window._on_view_log()
        self.assertEqual(seen, [("日志正文", True)])

    def test_log_cancel_and_failure_keep_file(self):
        window, _ = self.log()
        window._tbl.selectRow(0)
        selected = Path(window._selected_log()[0])
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            window._on_delete_log()
        self.assertTrue(selected.exists())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             mock.patch("ui.log_viewer_form.os.remove", side_effect=PermissionError("denied")), \
             mock.patch.object(QMessageBox, "warning") as warning:
            window._on_delete_log()
        self.assertTrue(selected.exists())
        warning.assert_called_once()

    def test_log_filter_refresh_preserves_query_and_selection(self):
        window, _ = self.log()
        window._txt_search.setText("log_a")
        for row in range(window._tbl.rowCount()):
            if not window._tbl.isRowHidden(row):
                window._tbl.selectRow(row)
        selected = window._selected_log()
        window.refresh()
        self.assertEqual(window._selected_log(), selected)
        self.assertEqual(sum(not window._tbl.isRowHidden(r) for r in range(window._tbl.rowCount())), 1)
        window._txt_search.setText("no-match")
        self.assertFalse(window._btn_delete.isEnabled())

    def test_log_reopen_rescans(self):
        window, folder = self.log()
        window.show()
        window.close()
        (folder / "log_c.txt").write_text("new", encoding="utf-8")
        window.show()
        self.assertEqual(window._tbl.rowCount(), 3)

    def test_history_clipped_revision_rejects_stale_row(self):
        window, manager = self.history(50)
        window._tbl.selectRow(0)
        manager.record_change("new")
        with mock.patch.object(QMessageBox, "question") as question:
            window._jump_with_confirm(0)
        question.assert_not_called()
        self.assertEqual(manager.get_snapshot_count(), 50)
        self.assertEqual(manager.peek_snapshot(0), "snapshot1")
        self.assertIn("历史已变化", window._lbl_status.text())

    def test_history_queued_notification_refreshes_and_closes_cleanly(self):
        window, manager = self.history()
        manager.record_change("new")
        self.assertEqual(window._tbl.rowCount(), 4)
        APP.processEvents()
        self.assertEqual(window._tbl.rowCount(), 5)
        window.close()
        self.assertEqual(len(manager._history_changed_callbacks), 0)
        manager.record_change("after-close")
        APP.processEvents()
        self.assertEqual(window._tbl.rowCount(), 5)
        window.show()
        self.assertEqual(len(manager._history_changed_callbacks), 1)
        self.assertEqual(window._tbl.rowCount(), 6)

    def test_history_switch_manager_ignores_queued_old_event(self):
        window, old = self.history()
        old.record_change("queued")
        new = BackupManager()
        new.record_initial("new")
        window.set_backup_manager(new)
        APP.processEvents()
        self.assertEqual(window._tbl.rowCount(), 1)
        self.assertEqual(len(old._history_changed_callbacks), 0)

    def test_history_confirm_revision_changes_rejects(self):
        window, manager = self.history()
        def confirm(*args):
            manager.record_change("during-confirm")
            return QMessageBox.Yes
        callback = mock.Mock(return_value=True)
        window.set_history_action_callback(callback)
        with mock.patch.object(QMessageBox, "question", side_effect=confirm):
            window._jump_with_confirm(0)
        callback.assert_not_called()
        self.assertEqual(manager.get_snapshot_count(), 5)

    def test_history_discard_count_and_cancel(self):
        window, manager = self.history(10)
        manager.undo()
        manager.undo()
        window.refresh()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No) as question:
            window._jump_with_confirm(3)
        self.assertIn("6 条", question.call_args.args[2])
        self.assertEqual(manager.get_snapshot_count(), 10)

    def test_history_false_and_exception_do_not_advance(self):
        window, manager = self.history()
        before = (manager.get_revision(), manager.get_current_index(), manager.get_snapshot_count())
        for callback in (lambda text: False, mock.Mock(side_effect=OSError("blocked"))):
            window.set_sync_callback(callback)
            window._on_undo()
            self.assertEqual(before, (manager.get_revision(), manager.get_current_index(), manager.get_snapshot_count()))
            self.assertIn("失败", window._lbl_status.text())

    def test_history_none_legacy_writes_before_committing(self):
        window, manager = self.history()
        seen = []
        def sync(text):
            seen.append((text, manager.get_current_index()))
        window.set_sync_callback(sync)
        window._on_undo()
        self.assertEqual(seen, [("snapshot2", 3)])
        self.assertEqual(manager.get_current_index(), 2)

    def test_history_new_callback_owns_commit_and_false(self):
        window, manager = self.history()
        called = []
        def action(kind, index, revision):
            called.append((kind, index, revision, manager.get_current_index()))
            return False
        window.set_history_action_callback(action)
        window._on_undo()
        self.assertEqual(called[0][:2], ("undo", 2))
        self.assertEqual(manager.get_current_index(), 3)

    def test_history_no_callback_cannot_claim_disk_write(self):
        window, manager = self.history()
        window._on_undo()
        self.assertEqual(manager.get_current_index(), 3)
        self.assertIn("未修改历史", window._lbl_status.text())

    def test_history_main_transaction_commits_once_after_write(self):
        window, manager = self.history()
        seen = []
        def transaction(action, index, revision):
            self.assertEqual(revision, manager.get_revision())
            seen.append((action, manager.get_current_index(), manager.peek_snapshot(index)))
            # 模拟主窗已写盘成功，此后才允许推进历史。
            manager.jump_to(index)
            return True
        window.set_history_action_callback(transaction)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            window._jump_with_confirm(1)
        self.assertEqual(seen, [("jump", 3, "snapshot1")])
        self.assertEqual(manager.get_snapshot_count(), 2)
        self.assertEqual(manager.get_current_index(), 1)

    def test_history_pause_resume_nested_without_real_listener(self):
        from input.keyboard_handler import KeyboardBindingHandler
        class Listener:
            def __init__(self): self.is_listening = False; self.depth = 0
            def start_listening(self): self.is_listening = True
            def stop_listening(self): self.is_listening = False
            def pause(self): self.depth += 1
            def resume(self): self.depth -= 1
        for mode in ("once", "continuous", "off"):
            with mock.patch("input.keyboard_handler.KeyboardListener", Listener):
                handler = KeyboardBindingHandler()
            if mode == "once": handler.set_listen_once_mode(True)
            if mode == "continuous": handler.set_listen_continuously_mode(True)
            window, _ = self.history()
            window.set_keyboard_handler(handler)
            window.show()
            APP.sendEvent(window, QEvent(QEvent.WindowActivate))
            self.assertEqual(handler._pause_depth, 1)
            APP.sendEvent(window, QEvent(QEvent.WindowDeactivate))
            self.assertEqual(handler._pause_depth, 0)
            APP.sendEvent(window, QEvent(QEvent.WindowActivate))
            handler.stop_all_listening()
            window.close()
            self.assertEqual(handler._pause_depth, 0)
            self.assertFalse(handler.is_key_creation_active)


if __name__ == "__main__":
    unittest.main(verbosity=2)
