"""P3 正式弹窗/预设集成定向验收；仅离屏、本地临时数据及桩回调。"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import sip
from PyQt5.QtCore import QEvent, QTimer, Qt
from PyQt5.QtGui import QKeyEvent, QTextCursor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QInputDialog, QLineEdit, QMessageBox, QWidget
from ui import key_preset_form as preset
from ui.message_dialog import MessageDialog, _ItemInputDialog, _TextInputDialog

APP = QApplication.instance() or QApplication([])


def run_modal(factory, action):
    errors = []
    timer = QTimer()
    watchdog = QTimer()
    watchdog.setSingleShot(True)

    def drive():
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, MessageDialog) or not dialog.isVisible():
            return
        timer.stop()
        try:
            APP.setActiveWindow(dialog)
            action(dialog)
        except BaseException as exc:
            errors.append(exc)
            if not sip.isdeleted(dialog):
                dialog.reject()

    def timeout():
        errors.append(AssertionError("离屏弹窗未在2秒内结束"))
        dialog = QApplication.activeModalWidget()
        if dialog is not None:
            dialog.close()

    timer.timeout.connect(drive)
    watchdog.timeout.connect(timeout)
    timer.start(5)
    watchdog.start(2000)
    try:
        result = factory()
    finally:
        timer.stop()
        watchdog.stop()
    if errors:
        raise errors[0]
    return result


class P3DialogIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(APP.platformName(), "offscreen")

    def show(self, dialog):
        self.addCleanup(dialog.close)
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        APP.processEvents()
        APP.setActiveWindow(dialog)
        return dialog

    def test_supervisor_direct_keyboard_contract_with_modifiers(self):
        for modifier in (Qt.NoModifier, Qt.ControlModifier, Qt.ShiftModifier):
            for key in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
                dialog = MessageDialog(show_cancel=True)
                event = QKeyEvent(QEvent.KeyPress, key, modifier)
                event.ignore()
                dialog.keyPressEvent(event)
                self.assertTrue(event.isAccepted())
                self.assertFalse(dialog.result)
                dialog.deleteLater()

    def test_child_enter_escape_and_release_do_not_propagate(self):
        class Parent(QWidget):
            def __init__(self):
                super().__init__()
                self.leaks = []

            def keyPressEvent(self, event):
                self.leaks.append(event.key())

            def keyReleaseEvent(self, event):
                self.leaks.append(event.key())

        parent = Parent()
        self.addCleanup(parent.deleteLater)
        for key in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            dialog = self.show(MessageDialog(parent=parent, danger=True))
            for event_type in (QEvent.ShortcutOverride, QEvent.KeyPress, QEvent.KeyRelease):
                event = QKeyEvent(event_type, key, Qt.ControlModifier)
                event.ignore()
                QApplication.sendEvent(dialog._msg, event)
                self.assertTrue(event.isAccepted())
            self.assertFalse(dialog.result)
        self.assertEqual(parent.leaks, [])

    def test_question_default_enter_cancel_and_explicit_confirm(self):
        self.assertFalse(run_modal(lambda: MessageDialog.question(None, "确认", "覆盖？"),
                                   lambda dialog: QTest.keyClick(dialog._cancel_btn, Qt.Key_Return)))

        def confirm(dialog):
            QTest.keyClick(dialog._cancel_btn, Qt.Key_Tab)
            self.assertIs(APP.focusWidget(), dialog._confirm_btn)
            QTest.keyClick(dialog._confirm_btn, Qt.Key_Return)

        self.assertTrue(run_modal(lambda: MessageDialog.question(None, "确认", "覆盖？"), confirm))

    def test_danger_cannot_disable_cancel_default(self):
        def cancel(dialog):
            self.assertEqual(dialog._confirm_btn._role, "danger")
            self.assertTrue(dialog._cancel_btn.isDefault())
            self.assertEqual(dialog._confirm_btn.height(), 26)
            QTest.keyClick(dialog, Qt.Key_Enter)

        self.assertFalse(run_modal(lambda: MessageDialog.question(None, "危险", "删除？", danger=True,
                                                                  default_cancel=False), cancel))

    def test_notice_factories_preserve_none_and_type(self):
        for kind in ("information", "warning", "error"):
            def acknowledge(dialog):
                self.assertEqual(dialog._icon.kind, kind)
                self.assertEqual((dialog.width(), dialog.height()), (350, 160))
                dialog.accept()
            self.assertIsNone(run_modal(lambda: getattr(MessageDialog, kind)(None, "提示", "短消息"), acknowledge))

    def test_text_confirm_empty_and_cancel_are_distinct(self):
        def confirm_empty(dialog):
            self.assertEqual(dialog.width(), 380)
            self.assertEqual(dialog._edit.height(), 26)
            dialog._edit.clear()
            QTest.keyClick(dialog._edit, Qt.Key_Return)
        self.assertEqual(run_modal(lambda: MessageDialog.get_text(None, "名称", "名称：", text="old"), confirm_empty), ("", True))
        self.assertEqual(run_modal(lambda: MessageDialog.get_text(None, "名称", "名称：", text="old"),
                                   lambda dialog: QTest.keyClick(dialog._edit, Qt.Key_Escape)), ("", False))

    def test_password_is_masked_and_cancel_clears_value(self):
        def cancel(dialog):
            self.assertNotEqual(dialog._edit.displayText(), "secret")
            dialog.reject()
            self.assertEqual(dialog._edit.text(), "")
        self.assertEqual(run_modal(lambda: MessageDialog.get_text(None, "密码", "密码：", QLineEdit.Password, "secret"), cancel), ("", False))

    def test_long_item_selection_scrolls_and_returns_complete_name(self):
        names = [f"预设_{index:03d}_" + "很长名称" * 20 + ".json" for index in range(120)]
        def choose(dialog):
            self.assertEqual(dialog.width(), 400)
            self.assertLessEqual(dialog.height(), 340)
            rect = dialog._list.visualItemRect(dialog._list.currentItem())
            self.assertGreaterEqual(rect.top(), 0)
            self.assertLessEqual(rect.bottom(), dialog._list.viewport().rect().bottom())
            self.assertGreater(dialog._list.verticalScrollBar().maximum(), 0)
            self.assertEqual(dialog._list.currentItem().toolTip(), names[-1])
            dialog.accept()
        self.assertEqual(run_modal(lambda: MessageDialog.get_item(None, "选择", "预设：", names, 119, False), choose), (names[-1], True))

    def test_item_cancel_and_readonly_empty_do_not_return_value(self):
        self.assertEqual(run_modal(lambda: MessageDialog.get_item(None, "选择", "预设：", ["old"], 0, False),
                                   lambda dialog: dialog.reject()), ("", False))
        def cancel_empty(dialog):
            self.assertFalse(dialog._confirm_btn.isEnabled())
            dialog.reject()
        self.assertEqual(run_modal(lambda: MessageDialog.get_item(None, "选择", "预设：", [], 0, False), cancel_empty), ("", False))

    def test_long_message_is_bounded_and_tail_reachable(self):
        dialog = self.show(MessageDialog(message="\n".join("长正文 " * 30 for _ in range(160)), show_cancel=False))
        self.assertEqual(dialog.width(), 420)
        self.assertGreaterEqual(dialog.height(), 160)
        self.assertLessEqual(dialog.height(), 340)
        bar = dialog._scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.maximum())
        cursor = QTextCursor(dialog._msg.document())
        cursor.movePosition(QTextCursor.End)
        self.assertTrue(dialog._msg.viewport().rect().intersects(dialog._msg.cursorRect(cursor)))
        self.assertTrue(dialog.rect().contains(dialog._confirm_btn.geometry().translated(dialog._buttons.pos() + dialog._buttons.parentWidget().pos())))

    def test_parent_destroyed_returns_cancel(self):
        parent = QWidget()
        self.assertFalse(run_modal(lambda: MessageDialog.question(parent, "确认", "取消"),
                                   lambda dialog: parent.deleteLater()))


class P3PresetIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="p3_preset_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        patch = mock.patch.dict(os.environ, {"MOMO_DATA_DIR": str(self.root)})
        patch.start()
        self.addCleanup(patch.stop)
        self.form = preset.KeyPresetForm()
        self.addCleanup(self.form.deleteLater)
        self.addCleanup(self.form.close)
        self.sync = mock.Mock(return_value=True)
        self.form.set_sync_callback(self.sync)

    def save(self, name):
        directory = self.root / "data"
        directory.mkdir(exist_ok=True)
        path = directory / name
        path.write_text('{"keymaps":[]}', encoding="utf-8-sig")
        self.form._refresh_preset_list()
        self.form._preset_cb.setCurrentText(name)
        return path

    def test_compact_groups_inputs_and_directory_menu_preserved(self):
        self.form.show()
        APP.processEvents()
        self.assertEqual((self.form.width(), self.form.height()), (390, 300))
        self.assertEqual(len(self.form._preset_actions), 8)
        self.assertEqual(sum(button.text() == "删除" for button in self.form._preset_actions.values()), 1)
        self.assertTrue(all(button.height() == 26 for button in self.form._preset_actions.values()))
        self.assertEqual(self.form._preset_read_ie.height(), 26)
        self.assertFalse(self.form._preset_read_ie._escape_clears)
        self.assertTrue(self.form._preset_read_ie._extended_keys)
        menu = self.form._preset_actions["_on_open_preset"].menu()
        self.assertEqual([action.text() for action in menu.actions()], ["选择并导入预设文件", "打开预设文件夹"])

    def test_long_selection_hint_preserves_model_and_tooltip(self):
        name = "方案_" + "名称" * 40 + ".json"
        self.save(name)
        self.assertEqual(self.form._preset_cb.currentText(), name)
        self.assertIn(name, self.form._preset_hint.toolTip())
        self.assertIn("…", self.form._preset_hint.text())
        self.assertEqual(self.form._preset_cb.combo.itemData(0, Qt.ToolTipRole), name)

    def test_delete_selection_cancel_keeps_bytes(self):
        path = self.save("保留.json")
        before = path.read_bytes()
        with mock.patch.object(MessageDialog, "get_item", return_value=("", False)), \
                mock.patch.object(MessageDialog, "question") as confirm:
            self.form._delete_preset()
        self.assertEqual(path.read_bytes(), before)
        confirm.assert_not_called()

    def test_delete_danger_cancel_default_selection_and_original_text(self):
        self.save("A.json")
        path = self.save("B.json")
        before = path.read_bytes()
        with mock.patch.object(MessageDialog, "get_item", return_value=("B.json", True)) as select, \
                mock.patch.object(MessageDialog, "question", return_value=False) as confirm, \
                mock.patch.object(QMessageBox, "question", side_effect=AssertionError("不应调用原生确认")), \
                mock.patch.object(QInputDialog, "getItem", side_effect=AssertionError("不应调用原生列表")):
            self.form._delete_preset()
        self.assertEqual(select.call_args.args[4], 1)
        self.assertEqual(confirm.call_args.args[2], "确定要删除预设文件 [B.json] 吗？此操作不可撤销。")
        self.assertTrue(confirm.call_args.kwargs["danger"])
        self.assertEqual(path.read_bytes(), before)

    def test_migrated_overwrite_guard_still_replaces_or_cancels_whole_batch(self):
        old = {"key": {"text": "Enter", "virtual_key": 13}, "type": "Click"}
        new = {"key": {"text": "Return", "virtual_key": 13}, "type": "Macro", "press_actions": ["custom-action"]}
        path = self.save("覆盖.json")
        path.write_text(json.dumps({"keymaps": [new]}), encoding="utf-8-sig")
        original = json.dumps({"keymaps": [old]})
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                self.form.set_json_data(original)
                self.sync.reset_mock()
                guard = mock.Mock(return_value=accepted)
                self.form.set_overwrite_guard(guard)
                self.form._inject_all_keys()
                guard.assert_called_once()
                if accepted:
                    self.assertEqual(json.loads(self.form._json_str)["keymaps"], [new])
                    self.sync.assert_called_once()
                else:
                    self.assertEqual(self.form._json_str, original)
                    self.sync.assert_not_called()
