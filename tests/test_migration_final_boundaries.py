"""最终事务边界：假设备、假热键、隐藏Qt，不启动外部进程。"""
import json
import sys
import threading
from types import SimpleNamespace
from unittest import TestCase, mock

from PyQt5.QtCore import Qt, QEvent, QSignalBlocker
from PyQt5.QtGui import QKeyEvent
import main
import test_migration_main_transactions as main_tests
import test_migration_overwrite as overwrite_tests
from core.json_editor import get_scan_code
from core.key_names import canonical_hotkey_key
from infra.index_backup import IndexFileBackupManager
from input import hotkey_manager
from input.keyboard_handler import KeyboardBindingHandler
from ui import main_window, wide_screen_form
from ui.message_dialog import MessageDialog


class FinalOverwriteBoundaryTests(TestCase):
    setUp = main_tests.MainTransactionMigrationTests.setUp
    reset_document = overwrite_tests.OverwriteMigrationTests.reset_document
    state = overwrite_tests.OverwriteMigrationTests.state
    host_form = overwrite_tests.OverwriteMigrationTests.host_form
    adb = overwrite_tests.OverwriteMigrationTests.adb
    quick = overwrite_tests.OverwriteMigrationTests.quick

    def test_adb_and_quick_reject_queued_pre_confirmation_events(self):
        self.reset_document(overwrite_tests.block())
        old = self.win._overwrite_coordinator.get_epoch()
        self.ask.return_value = False
        self.win._confirm_key_overwrite(self.win, self.win._json_str, overwrite_tests.block(x=.8))
        self.ask.reset_mock()
        before = self.state()
        form = self.adb()
        form._gen_multi_cb.setChecked(True)
        form._on_key_captured_continuously("A", 65, old)
        form.close()
        form = self.quick()
        with QSignalBlocker(form._multi_cb):
            form._multi_cb.setChecked(True)
        self.assertFalse(form._on_key_down_ui("A", 65, form._listen_epoch, old))
        self.assertEqual(self.state(), before)
        self.ask.assert_not_called()

    def test_single_mode_cancel_requires_physical_release_before_retry(self):
        self.reset_document(overwrite_tests.block())
        self.win.set_jsxy(".8", ".2")
        handler = KeyboardBindingHandler()
        self.win._kb_handler_instance = handler
        handler.on_key_captured_once(lambda text, vk: self.win._on_global_key_captured(text, vk, False))
        listener = handler._listener_once
        self.ask.return_value = False
        key = SimpleNamespace(vk=65, char="a")
        with mock.patch("input.keyboard_listener.Listener", overwrite_tests.FakeNativeListener):
            handler.set_listen_once_mode(True)
            listener._on_press(key)
            listener._on_press(key)
            self.ask.assert_called_once()
            listener._on_release(key)
            listener._on_press(key)
            self.assertEqual(self.ask.call_count, 2)
            self.assertTrue(handler.is_key_creation_active)
            self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 1)

    def test_wide_single_and_batch_cancel_preserve_every_state(self):
        for single in (False, True):
            with self.subTest(single=single):
                original = self.reset_document(overwrite_tests.block())
                form = self.host_form(wide_screen_form.WideScreenForm(), "ws")
                source = self.root / "source.json"
                source.write_text(json.dumps({"keymaps": [overwrite_tests.block(x=.8),
                    overwrite_tests.block(66, "B", .6)]}), encoding="utf-8")
                form._file_cb.clear()
                form._file_cb.addItem("source", str(source))
                form._res_cb.addItem("1280×720", "1280,720")
                form._res_cb.setCurrentIndex(form._res_cb.count() - 1)
                form.set_resolution_getter(lambda: (1280, 720))
                form.set_json_data(original)
                form.set_write_callback(self.win.try_set_mumu_json_and_save)
                form._on_read()
                form._tbl.setCurrentCell(0, 1)
                for row in range(form._tbl.rowCount()):
                    form._tbl.item(row, 0).setCheckState(Qt.Checked)
                callback = form._on_single_write if single else form._on_batch_write
                before, label = self.state(), form.status_bar.currentMessage()
                self.ask.return_value = False
                self.ask.reset_mock()
                callback()
                self.ask.assert_called_once()
                self.assertEqual(self.state(), before)
                self.assertEqual(form._json_str, original)
                self.assertEqual(form.status_bar.currentMessage(), label)
                self.ask.return_value = True
                callback()
                self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 2)
                self.assertEqual(len(json.loads(self.win._json_str)["keymaps"]), 1 if single else 2)

    def test_confirmation_keyboard_defaults_cancel_and_accepts_event(self):
        for key in (Qt.Key_Escape, Qt.Key_Enter, Qt.Key_Return):
            dialog = MessageDialog(show_cancel=True)
            dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
            try:
                event = QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier)
                event.ignore()
                dialog.keyPressEvent(event)
                self.assertFalse(dialog.result)
                self.assertTrue(event.isAccepted())
            finally:
                dialog.deleteLater()


class FinalMainBoundaryTests(TestCase):
    setUp = main_tests.MainTransactionMigrationTests.setUp

    def test_external_reject_does_not_authorize_overwriting_new_disk_version(self):
        self.win._accepted_file_versions[self.win._canonical_path(str(self.path))] = self.win._file_version(str(self.path))
        external = main_tests.scheme(.234567)
        self.path.write_text(external, encoding="utf-8")
        self.ask.return_value = False
        self.win._on_external_file_changed(str(self.path))
        self.win._on_external_file_changed(str(self.path))
        self.ask.assert_called_once()
        self.assertFalse(self.win.try_set_mumu_json_and_save(main_tests.scheme(.9)))
        self.assertEqual(self.path.read_text(encoding="utf-8"), external)
        self.assertEqual(self.win._json_str, main_tests.scheme())
        self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 1)

    def test_file_and_directory_events_debounce_and_old_path_is_ignored(self):
        with mock.patch.object(self.win, "_on_external_file_changed") as changed:
            self.win._queue_external_file_changed(str(self.path))
            self.win._on_external_directory_changed(str(self.root))
            self.win._queue_external_file_changed(str(self.root / "old.json"))
            changed.assert_not_called()
            self.assertTrue(self.win._external_change_timer.isActive())
            self.win._external_change_timer.stop()
            self.win._flush_external_change()
            changed.assert_called_once_with(str(self.path))

    def test_index_worker_captures_original_path_and_cleanup_failure(self):
        executor = mock.Mock()
        self.win._index_executor = executor
        manager = mock.Mock(last_error="清理被占用")
        manager.cleanup_old_backups.return_value = False
        with mock.patch.object(main_window, "IndexFileBackupManager", return_value=manager):
            self.win._submit_index_job("backup", str(self.path), "v1")
            work = executor.submit.call_args.args[0]
            self.win._json_path = str(self.root / "B服-b.json")
            work()
            manager.backup_index.assert_called_once_with(str(self.path))
            self.win._submit_index_job("cleanup", str(self.path), None)
            payload = executor.submit.call_args.args[0]()
            self.assertFalse(payload[3])
            self.win._on_index_task_done(payload)
            self.assertIn("清理失败", self.status())
        self.win._index_executor = None

    status = main_tests.MainTransactionMigrationTests.status

    def test_stale_index_check_result_cannot_change_current_status(self):
        self.win._update_status("新文件状态")
        identity = ("check", self.win._canonical_path(str(self.root / "old.json")), "v0")
        self.win._on_index_task_done((id(self.win), identity, str(self.root / "old.json"), False, "旧错误"))
        self.assertEqual(self.status(), "新文件状态")

    def test_index_panel_initial_selection_follows_loaded_package(self):
        panel = mock.Mock()
        manager = mock.Mock()
        manager.resolve_index_file_path.return_value = str(self.root / "官服.json")
        self.win._index_backup_mgr = manager
        with mock.patch.object(self.win, "_show_sub", side_effect=lambda key, factory, post_init: post_init(panel)):
            self.win._open_index_backup()
        panel.set_index_directory.assert_called_once_with(str(self.root))
        panel.set_current_index_file.assert_called_once_with("官服.json")

    def test_index_panel_prefers_selected_package_to_loaded_old_path(self):
        panel = mock.Mock()
        prefix = main_window.PACKAGE_PREFIX_BY_NAME["B服"].rstrip('-')
        (self.root / (prefix + ".json")).write_text('{}', encoding="utf-8")
        self.win._package_cb.blockSignals(True)
        self.win._package_cb.setCurrentText("B服")
        self.win._package_cb.blockSignals(False)
        with mock.patch.object(self.win, "_show_sub", side_effect=lambda key, factory, post_init: post_init(panel)):
            self.win._open_index_backup()
        panel.set_current_index_file.assert_called_once_with(prefix + ".json")

    def test_directory_read_failure_clears_stale_targets_and_missing_is_distinct(self):
        self.win._file_cb.addItem("stale", str(self.path))
        with mock.patch.object(main_window.os, "listdir", side_effect=PermissionError("占用")):
            self.win._refresh_file_list()
        self.assertEqual(self.win._file_cb.count(), 0)
        self.assertIn("目录失败", self.status())
        self.win._config.json_folder_path = str(self.root / "missing")
        self.win._refresh_file_list()
        self.assertEqual(self.win._file_cb.count(), 0)
        self.assertIn("不存在", self.status())

    def test_missing_preset_directory_is_not_reported_as_valid_empty_data(self):
        from ui.key_preset_form import KeyPresetForm
        form = KeyPresetForm()
        self.addCleanup(form.deleteLater)
        self.addCleanup(form.close)
        form._refresh_preset_list()
        self.assertEqual(form._preset_cb.count(), 0)
        self.assertIn("目录不存在", form.status_bar.currentMessage())


class FinalRuntimeBoundaryTests(TestCase):
    def test_cleanup_error_propagates_without_deleting_external_paths(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix="cleanup_boundary_") as directory:
            manager = IndexFileBackupManager({}, directory)
            invalid = Path(manager.backup_directory) / "官服" / "invalid"
            invalid.mkdir(parents=True)
            with mock.patch("infra.index_backup.shutil.rmtree", side_effect=PermissionError("占用")):
                self.assertFalse(manager.cleanup_old_backups())
            self.assertIn("占用", manager.last_error)
            self.assertTrue(invalid.exists())

    def test_legacy_hotkey_alias_and_numpad_use_backend_handles(self):
        self.assertEqual(canonical_hotkey_key("D1"), "1")
        self.assertEqual(canonical_hotkey_key("Return"), "enter")
        self.assertEqual(canonical_hotkey_key("Oemcomma"), "comma")
        backend = mock.Mock()
        backend.key_to_scan_codes.return_value = (29,)
        backend.add_hotkey.return_value = "handle"
        with mock.patch.object(hotkey_manager, "_kb", return_value=backend):
            manager = hotkey_manager.HotKeyManager()
            self.assertTrue(manager.register("ctrl+NumPad1", lambda: None))
            self.assertIn((get_scan_code(0x61),), backend.add_hotkey.call_args.args[0][0])
            manager.unregister_all()
            backend.remove_hotkey.assert_called_once_with("handle")

    def test_application_and_thread_hooks_report_and_restore_originals(self):
        previous_sys, previous_thread, reporter = mock.Mock(), mock.Mock(), mock.Mock()
        with mock.patch.object(sys, "excepthook", previous_sys), mock.patch.object(threading, "excepthook", previous_thread), \
                mock.patch("infra.log_service.LogService.error_ex") as logged:
            restore = main.install_exception_handlers(main_tests.APP, reporter)
            try:
                error = ValueError("离线异常")
                sys.excepthook(ValueError, error, None)
                args = SimpleNamespace(exc_type=ValueError, exc_value=error, exc_traceback=None)
                threading.excepthook(args)
                self.assertEqual(reporter.call_count, 2)
                self.assertEqual(logged.call_count, 2)
                previous_sys.assert_called_once()
                previous_thread.assert_called_once_with(args)
            finally:
                restore()
            self.assertIs(sys.excepthook, previous_sys)
            self.assertIs(threading.excepthook, previous_thread)

    def test_startup_construction_error_always_restores_hooks_and_shuts_log(self):
        app, restore = mock.Mock(), mock.Mock()
        with mock.patch.object(main, "QApplication", return_value=app), \
                mock.patch.object(main, "install_exception_handlers", return_value=restore), \
                mock.patch.object(main, "_icon_candidates", return_value=[]), \
                mock.patch.object(main_window, "MainWindow", side_effect=ValueError("构造失败")), \
                mock.patch("infra.log_service.LogService.initialize"), \
                mock.patch("infra.log_service.LogService.shutdown") as shutdown, \
                mock.patch("infra.log_service.LogService.info"), mock.patch.object(sys, "excepthook") as hook:
            self.assertEqual(main.main(), 1)
            hook.assert_called_once()
            restore.assert_called_once()
            shutdown.assert_called_once()
            app.exec_.assert_not_called()
