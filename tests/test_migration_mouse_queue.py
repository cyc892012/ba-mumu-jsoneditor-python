"""C-77：用真实 Qt 排队桥和假系统边界验证鼠标热键代次。"""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtWidgets import QApplication
from core.config_manager import ConfigManager


class FakeHotkeys:
    """保留注销/失败的旧回调，用于模拟后端迟到事件。"""

    def __init__(self):
        self.callbacks = {}
        self.attempts = []
        self.fail = set()
        self.emit_on_register = set()

    def register(self, combo, callback):
        self.attempts.append((combo, callback))
        if combo in self.emit_on_register:
            callback()
        if combo in self.fail:
            return False
        self.callbacks[combo] = callback
        return True

    def unregister_all(self):
        self.callbacks.clear()


# 在窗体模块首次导入前截断鼠标边界，不构造原生 Controller。
fake_mouse = types.ModuleType("input.mouse_controller")
fake_mouse.get_cursor_pos = lambda: (101, -202)
fake_mouse.move_mouse_to = lambda x, y: None
with mock.patch.dict(sys.modules, {"input.mouse_controller": fake_mouse}):
    mouse_module = importlib.import_module("ui.mouse_track_form")

APP = QApplication.instance() or QApplication([])


class MouseQueueTest(unittest.TestCase):
    def setUp(self):
        self.assertEqual(QApplication.platformName(), "offscreen")
        self.tmp = tempfile.TemporaryDirectory(prefix="mouse_queue_")
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {
            "MOMO_DATA_DIR": self.tmp.name,
            "LOCALAPPDATA": self.tmp.name,
        })
        env.start()
        self.addCleanup(env.stop)
        ConfigManager.reset_shared()
        self.addCleanup(ConfigManager.reset_shared)
        for name, value in (("HotKeyManager", FakeHotkeys),
                            ("get_cursor_pos", mock.Mock(return_value=(101, -202))),
                            ("move_mouse_to", mock.Mock())):
            patch = mock.patch.object(mouse_module, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.move = mouse_module.move_mouse_to
        self.forms = []

    def tearDown(self):
        for form in self.forms:
            form.close()
            form.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def form(self):
        form = mouse_module.MouseTrackForm()
        form.setAttribute(Qt.WA_DontShowOnScreen, True)
        form.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.forms.append(form)
        form._cur_x.setText("101")
        form._cur_y.setText("-202")
        form._saved_x.setText("7")
        form._saved_y.setText("9")
        form._has_saved_coord = True
        form.set_status_callback(mock.Mock())
        self.move.reset_mock()
        form._enable_cb.setChecked(True)
        self.assertTrue(form._enable_cb.isChecked())
        self.assertTrue(form._timer.isActive())
        return form

    def enqueue(self, *callbacks):
        errors = []

        def worker():
            try:
                for callback in callbacks:
                    callback()
            except BaseException as ex:
                errors.append(ex)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(3)
        self.assertFalse(thread.is_alive(), "假热键线程未结束")
        self.assertEqual(errors, [])

    @staticmethod
    def flush(form):
        # 只送达真实 QueuedConnection，不用睡眠或触发轮询定时器。
        QCoreApplication.sendPostedEvents(form._bridge, QEvent.MetaCall)

    def assert_ignored(self, form):
        before = (form._saved_x.text(), form._saved_y.text(),
                  form._has_saved_coord, form._status_label.text())
        self.flush(form)
        self.assertEqual(before, (form._saved_x.text(), form._saved_y.text(),
                                 form._has_saved_coord, form._status_label.text()))
        self.move.assert_not_called()
        form._status_callback.assert_not_called()

    def assert_current_pair_works(self, form, save="ctrl+d", recall="ctrl+f"):
        callbacks = form._hotkey_mgr.callbacks
        self.enqueue(callbacks[save], callbacks[recall])
        self.move.assert_not_called()
        self.flush(form)
        self.assertEqual((form._saved_x.text(), form._saved_y.text()), ("101", "-202"))
        self.move.assert_called_once_with(101, -202)
        self.assertEqual(form._status_callback.call_count, 2)

    def test_current_callbacks_run_only_after_real_qt_queue_delivery(self):
        self.assert_current_pair_works(self.form())

    def test_queued_save_and_recall_are_rejected_after_disable(self):
        form = self.form()
        self.enqueue(*form._hotkey_mgr.callbacks.values())
        form._enable_cb.setChecked(False)
        self.assertFalse(form._timer.isActive())
        self.assertEqual(form._hotkey_mgr.callbacks, {})
        self.assert_ignored(form)

    def test_queued_callbacks_do_not_survive_disable_then_reenable(self):
        form = self.form()
        self.enqueue(*form._hotkey_mgr.callbacks.values())
        form._enable_cb.setChecked(False)
        form._enable_cb.setChecked(True)
        self.assert_ignored(form)
        self.assert_current_pair_works(form)

    def test_queued_callbacks_are_rejected_after_close(self):
        form = self.form()
        form.show()
        self.enqueue(*form._hotkey_mgr.callbacks.values())
        form.close()
        self.assertFalse(form._enable_cb.isChecked())
        self.assertFalse(form._timer.isActive())
        self.assert_ignored(form)

    def test_queued_callbacks_do_not_survive_close_show_then_reenable(self):
        form = self.form()
        form.show()
        self.enqueue(*form._hotkey_mgr.callbacks.values())
        form.close()
        form.show()
        form._enable_cb.setChecked(True)
        self.assert_ignored(form)
        self.assert_current_pair_works(form)

    def test_late_old_backend_callbacks_cannot_adopt_reopened_generation(self):
        for close in (False, True):
            with self.subTest(close=close):
                form = self.form()
                old = tuple(form._hotkey_mgr.callbacks.values())
                if close:
                    form.close()
                    form.show()
                else:
                    form._enable_cb.setChecked(False)
                form._enable_cb.setChecked(True)
                self.enqueue(*old)
                self.assert_ignored(form)
                self.assert_current_pair_works(form)

    def test_successful_rebind_rejects_queued_and_late_old_callbacks(self):
        form = self.form()
        old = tuple(form._hotkey_mgr.callbacks.values())
        self.enqueue(*old)
        form._save_key_edit.setText("A")
        form._on_key_captured()
        self.assertEqual(form._bindings, ("A", "F"))
        self.assertIn("未保存", form._status_label.text())
        self.enqueue(*old)
        self.assert_ignored(form)
        self.assert_current_pair_works(form, save="ctrl+a")

    def test_partial_registration_failure_and_restoration_use_fresh_callbacks(self):
        form = self.form()
        manager = form._hotkey_mgr
        old = tuple(manager.callbacks.values())
        self.enqueue(*old)
        manager.fail.add("ctrl+b")
        manager.emit_on_register.add("ctrl+a")
        start = len(manager.attempts)
        form._save_key_edit.setText("A")
        form._recall_key_edit.setText("B")
        form._on_key_captured()
        self.assertEqual(form._bindings, ("D", "F"))
        self.assertTrue(form._enable_cb.isChecked())
        self.assertTrue(form._timer.isActive())
        self.assertIn("未更改", form._status_label.text())
        abandoned = [callback for _, callback in manager.attempts[start:start + 2]]
        self.enqueue(*old, *abandoned)
        self.assert_ignored(form)
        self.assert_current_pair_works(form)

    def test_failed_restoration_stops_and_old_callbacks_stay_invalid_after_retry(self):
        form = self.form()
        manager = form._hotkey_mgr
        old = tuple(manager.callbacks.values())
        self.enqueue(*old)
        manager.fail.update(("ctrl+b", "ctrl+d"))
        manager.emit_on_register.add("ctrl+a")
        start = len(manager.attempts)
        form._save_key_edit.setText("A")
        form._recall_key_edit.setText("B")
        form._on_key_captured()
        abandoned = [callback for _, callback in manager.attempts[start:]]
        self.assertFalse(form._enable_cb.isChecked())
        self.assertFalse(form._timer.isActive())
        self.assertEqual(manager.callbacks, {})
        self.assert_ignored(form)
        manager.fail.clear()
        manager.emit_on_register.clear()
        form._enable_cb.setChecked(True)
        self.enqueue(*old, *abandoned)
        self.assert_ignored(form)
        self.assert_current_pair_works(form)

    def test_conflict_rejection_preserves_current_registered_callbacks(self):
        form = self.form()
        old = dict(form._hotkey_mgr.callbacks)
        form._recall_key_edit.setText("d")
        form._on_key_captured()
        self.assertEqual(form._hotkey_mgr.callbacks, old)
        self.assertEqual(form._recall_key_edit.text(), "F")
        self.assert_current_pair_works(form)

    def test_checked_but_unregistered_lifecycle_cannot_accept_queue(self):
        form = self.form()
        self.enqueue(*form._hotkey_mgr.callbacks.values())
        form._unregister_hotkeys()
        self.assertTrue(form._enable_cb.isChecked())
        self.assert_ignored(form)
        self.assertTrue(form._register_hotkeys())
        self.assert_current_pair_works(form)


if __name__ == "__main__":
    unittest.main()
