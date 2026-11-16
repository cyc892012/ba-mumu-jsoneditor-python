"""B-3 正式桥接回归：取消零提交、一次批量确认和连续释放去重。"""
import json
from types import SimpleNamespace
import unittest
from unittest import mock
from PyQt5.QtCore import Qt, QSignalBlocker
from PyQt5.QtWidgets import QApplication
import test_migration_main_transactions as main_tests
import test_migration_touch_forms as touch_tests
from core.json_editor import create_key
from input.keyboard_handler import KeyboardBindingHandler
from input.keyboard_listener import KeyboardListener
from ui import key_overwrite, adb_touch_form, quick_touch_form, key_preset_form


def block(vk=65, text="A", x=.2):
    return json.loads(create_key("Click", text, str(x), ".2", "0", str(vk)))


class FakeNativeListener:
    def __init__(self, **kwargs):
        pass
    def start(self):
        pass
    def stop(self):
        pass


class OverwriteMigrationTests(unittest.TestCase):
    setUp = main_tests.MainTransactionMigrationTests.setUp

    def reset_document(self, *keys):
        value = json.dumps({"keymaps": list(keys)}, ensure_ascii=False)
        self.win._json_str = value
        self.path.write_text(value, encoding="utf-8")
        self.win._accepted_file_versions[self.win._canonical_path(str(self.path))] = self.win._file_version(str(self.path))
        self.win._backup_mgr.record_initial(value)
        self.win._needs_index_backup = False
        self.win._pending_index_backups.clear()
        self.win._update_status("原成功状态")
        return value

    def state(self):
        return (self.win._json_str, self.path.read_bytes(), self.win._backup_mgr.get_revision(),
                self.win._backup_mgr.get_current_index(), self.win._backup_mgr.get_snapshot_count(),
                self.win._needs_index_backup, self.win._status_label.text())

    def host_form(self, form, key):
        form.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.win._sub_windows[key] = form
        form.set_overwrite_guard(lambda original, blocks: self.win._confirm_key_overwrite(form, original, blocks),
                                 self.win._overwrite_coordinator.get_epoch)
        self.addCleanup(form.deleteLater)
        self.addCleanup(form.close)
        return form

    def quick(self):
        with mock.patch.object(quick_touch_form, "KeyboardListener", touch_tests.FakeListener), \
                mock.patch.object(quick_touch_form.ConfigManager, "shared", return_value=self.win._config):
            form = self.host_form(quick_touch_form.QuickTouchForm(), "qt")
        form.set_json_data(self.win._json_str)
        form.set_json_path(str(self.path))
        form.set_sync_callback(self.win.try_set_mumu_json_and_save)
        form.set_resolution_getter(lambda: (1280, 720))
        form._generation_coordinates = lambda: (900., 300., 1280, 720)
        form.isVisible = lambda: True
        return form

    def adb(self):
        form = self.host_form(adb_touch_form.AdbTouchForm(), "adb")
        form._ui_timer.stop()
        handler = touch_tests.FakeHandler()
        form.set_keyboard_handler(handler)
        self.win._kb_handler_instance = handler
        form.set_main_window_bridge(lambda: self.win._json_str, self.win._on_adb_write_json)
        form._last_captured_screen = (900., 300.)
        form._screen_width, form._screen_height = 1280, 720
        form.isVisible = lambda: True
        return form

    def preset(self, *keys):
        form = self.host_form(key_preset_form.KeyPresetForm(), "kp")
        form.set_json_data(self.win._json_str)
        form.set_sync_callback(self.win.try_set_mumu_json_and_save)
        directory = self.root / "data"
        directory.mkdir(exist_ok=True)
        path = directory / "覆盖.json"
        path.write_text(json.dumps({"keymaps": list(keys)}), encoding="utf-8")
        form._get_preset_path = lambda: str(path)
        return form

    def test_main_manual_cancel_changes_no_state(self):
        self.reset_document(block())
        self.win._key_input.setText("A")
        self.win._key_input._vk = 65
        self.win.set_jsxy(".8", ".2")
        before = self.state()
        self.ask.return_value = False
        self.assertFalse(self.win._modify_save_key())
        self.assertEqual(self.state(), before)
        self.ask.assert_called_once()

    def test_main_manual_force_uses_one_confirmation(self):
        self.reset_document(block())
        self.win._key_input.setText("A")
        self.win._key_input._vk = 65
        self.win._force_replace_cb.setChecked(True)
        self.win.set_jsxy(".8", ".2")
        self.assertTrue(self.win._modify_save_key())
        self.ask.assert_called_once()
        self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 2)

    def test_main_global_once_and_continuous_cancel_and_accept(self):
        for continuous in (False, True):
            for accepted in (False, True):
                with self.subTest(continuous=continuous, accepted=accepted):
                    self.reset_document(block())
                    self.win.set_jsxy(".8", ".2")
                    before = self.state()
                    self.ask.reset_mock()
                    self.ask.return_value = accepted
                    self.assertEqual(self.win._on_global_key_captured("A", 65, continuous), accepted)
                    self.ask.assert_called_once()
                    if accepted:
                        self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 2)
                    else:
                        self.assertEqual(self.state(), before)

    def test_no_conflict_global_writes_without_confirmation(self):
        self.reset_document(block())
        self.win.set_jsxy(".8", ".2")
        self.assertTrue(self.win._on_global_key_captured("B", 66, True))
        self.ask.assert_not_called()

    def test_adb_batch_cancel_is_all_or_nothing_and_one_confirmation(self):
        self.reset_document(block(65), block(66, "B"))
        form = self.adb()
        for i, (vk, name) in enumerate(((65, "A"), (66, "B"), (67, "C"))):
            row = adb_touch_form.TouchCoordRowData()
            row.checked, row.captured_key, row.vk_code = True, name, vk
            row.rel_x, row.rel_y = .8, .4
            form._row_data[i] = row
        before = self.state()
        status = form._status_label.text()
        self.ask.return_value = False
        self.assertFalse(form._on_generate_selected())
        self.assertEqual(self.state(), before)
        self.assertEqual(form._status_label.text(), status)
        self.ask.assert_called_once()
        message = self.ask.call_args.args[2]
        self.assertIn("VK 65", message)
        self.assertIn("VK 66", message)
        self.ask.return_value = True
        self.ask.reset_mock()
        self.assertTrue(form._on_generate_selected())
        self.ask.assert_called_once()
        self.assertEqual(len(json.loads(self.win._json_str)["keymaps"]), 3)
        self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 2)

    def test_adb_once_and_continuous_cancel_do_not_consume_or_write(self):
        for once in (False, True):
            with self.subTest(once=once):
                self.reset_document(block())
                form = self.adb()
                own = form._gen_once_cb if once else form._gen_multi_cb
                own.setChecked(True)
                before, label = self.state(), form._status_label.text()
                self.ask.return_value = False
                self.ask.reset_mock()
                callback = form._on_key_captured_once if once else form._on_key_captured_continuously
                callback("A", 65)
                self.assertEqual(self.state(), before)
                self.assertEqual(form._status_label.text(), label)
                self.assertTrue(own.isChecked())
                self.ask.assert_called_once()
                self.ask.return_value = True
                callback("A", 65)
                self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 2)
                form.close()

    def test_quick_once_and_continuous_cancel_keep_cache_and_mode(self):
        for once in (False, True):
            with self.subTest(once=once):
                original = self.reset_document(block())
                form = self.quick()
                own = form._once_cb if once else form._multi_cb
                with QSignalBlocker(own):
                    own.setChecked(True)
                before, label = self.state(), form._status_perm.text()
                self.ask.return_value = False
                self.ask.reset_mock()
                self.assertFalse(form._on_key_down_ui("A", 65))
                self.ask.assert_called_once()
                self.assertEqual(form._json_str, original)
                self.assertEqual(form._status_perm.text(), label)
                self.assertEqual(self.state(), before)
                self.assertTrue(own.isChecked())
                self.ask.return_value = True
                self.assertTrue(form._on_key_down_ui("A", 65))
                self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 2)
                form.close()

    def test_preset_batch_and_single_cancel_do_not_skip_to_new_subset(self):
        for single in (False, True):
            with self.subTest(single=single):
                original = self.reset_document(block(65), block(66, "B"))
                form = self.preset(block(65, x=.8), block(66, "B", x=.8), block(67, "C"))
                form._captured_key, form._captured_vk = "A", 65
                callback = form._inject_single_key if single else form._inject_all_keys
                before, status = self.state(), form.status_bar.currentMessage()
                self.ask.return_value = False
                self.ask.reset_mock()
                callback()
                self.ask.assert_called_once()
                self.assertEqual(form._json_str, original)
                self.assertEqual(form.status_bar.currentMessage(), status)
                self.assertEqual(self.state(), before)
                self.ask.return_value = True
                callback()
                self.assertEqual(self.win._backup_mgr.get_snapshot_count(), 2)
                expected = 2 if single else 3
                self.assertEqual(len(json.loads(self.win._json_str)["keymaps"]), expected)

    def test_coordinator_reentry_stale_queue_and_later_press(self):
        self.reset_document(block())
        self.win.set_jsxy(".8", ".2")
        old = self.win._overwrite_coordinator.get_epoch()
        results = []
        def cancel(*args, **kwargs):
            results.append(self.win._on_global_key_captured("A", 65, True, old))
            return False
        self.ask.side_effect = cancel
        self.assertFalse(self.win._on_global_key_captured("A", 65, True, old))
        self.assertEqual(results, [False])
        self.assertFalse(self.win._on_global_key_captured("A", 65, True, old))
        self.ask.assert_called_once()
        self.ask.side_effect = None
        self.ask.return_value = False
        self.win._on_global_key_captured("A", 65, True, self.win._overwrite_coordinator.get_epoch())
        self.assertEqual(self.ask.call_count, 2)

    def test_physical_hold_release_and_dialog_keys_do_not_leak(self):
        self.reset_document(block())
        self.win.set_jsxy(".8", ".2")
        handler = KeyboardBindingHandler()
        self.win._kb_handler_instance = handler
        listener = handler._listener_continuously
        handler.on_key_captured_continuously(lambda name, vk: self.win._on_global_key_captured(name, vk, True,
            self.win._overwrite_coordinator.get_epoch()))
        a = SimpleNamespace(vk=65, char="a")
        enter = SimpleNamespace(vk=13, char=None)
        esc = SimpleNamespace(vk=27, char=None)
        def cancel(*args, **kwargs):
            listener._on_press(a)
            listener._on_press(enter)
            listener._on_press(esc)
            return False
        self.ask.side_effect = cancel
        with mock.patch("input.keyboard_listener.Listener", FakeNativeListener):
            handler.set_listen_continuously_mode(True)
            listener._on_press(a)
            listener._on_press(a)
            listener._on_press(enter)
            listener._on_press(esc)
            self.ask.assert_called_once()
            listener._on_release(a)
            listener._on_press(a)
            self.assertEqual(self.ask.call_count, 2)
            self.assertEqual(len(json.loads(self.win._json_str)["keymaps"]), 1)
            self.assertTrue(handler.is_key_creation_active)

    def test_write_failure_after_approval_keeps_all_data(self):
        original = self.reset_document(block())
        form = self.quick()
        with QSignalBlocker(form._multi_cb):
            form._multi_cb.setChecked(True)
        before = self.state()[:6]
        with mock.patch.object(self.win, "_atomic_write_json", return_value=False):
            self.assertFalse(form._on_key_down_ui("A", 65))
        self.assertEqual(self.state()[:6], before)
        self.assertEqual(form._json_str, original)
        self.assertNotIn("已创建", form._status_perm.text())


if __name__ == "__main__":
    unittest.main()
