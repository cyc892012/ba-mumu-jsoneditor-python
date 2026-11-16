"""P2 正式主窗定向：独立进程离屏运行，设备/监听由外层隔离。"""
import os
from types import SimpleNamespace, ModuleType
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtTest import QSignalSpy
from PyQt5.QtWidgets import QWidget, QApplication
import test_migration_main_transactions as baseline
from controls import hina_aside
from controls.segmented import SegmentedControl
from ui.p2_window_management import plan_subwindow_position


class P2MainIntegrationTests(unittest.TestCase):
    def setUp(self):
        baseline.MainTransactionMigrationTests.setUp(self)
        self.win._kb_handler_instance = mock.Mock()
        self.win.bg.setOpenExternalLinks(False)

    def test_hidden_layout_keeps_size_sidebar_and_all_entries(self):
        self.win.show()
        QApplication.processEvents()
        self.assertTrue(self.win.testAttribute(Qt.WA_DontShowOnScreen))
        self.assertEqual((self.win.width(), self.win.height()), (660, 476))
        self.assertTrue(120 <= self.win.bg.width() <= 132)
        self.assertTrue(self.win.bg.hasImage())
        self.assertEqual(set(self.win._tool_buttons), {"adb", "mouse", "quick", "wide", "preset", "clear", "backup", "logs"})
        self.assertEqual(self.win.tab.count(), 2)
        self.assertTrue(self.win.tab.isHidden())
        self.assertLessEqual(self.win._file_card.geometry().bottom(), self.win._write_card.geometry().top())
        self.assertLessEqual(self.win._write_card.geometry().bottom(), self.win._tool_dock.geometry().top())
        self.assertFalse(self.win.bg.children())
        for key, button in self.win._tool_buttons.items():
            self.assertTrue(button.toolTip(), key)
            self.assertGreater(button.receivers(button.clicked), 0, key)
        for name in ("_history_btn", "_read_coord_btn", "_modify_save_btn", "_force_replace_cb"):
            self.assertTrue(getattr(self.win, name).toolTip(), name)

    def test_badge_and_unsaved_default_marker_follow_current_and_typed_size(self):
        default = self.win._config.fx, self.win._config.fy
        self.win._sync_resolution_controls()
        self.assertNotIn("*", self.win._save_res_btn.text())
        self.assertTrue(self.win._apply_current_resolution(2560, 1440))
        self.assertEqual(self.win._resolution_badge.text(), "2560×1440")
        self.assertIn("*", self.win._save_res_btn.text())
        self.assertIn("尚未存为默认", self.win._res_cb.toolTip())
        self.assertEqual((self.win._config.fx, self.win._config.fy), default)
        self.win._ratio_cb.setCurrentText("自定义")
        self.win._res_cb.setEditText("3000×1700")
        self.assertIn("*", self.win._save_res_btn.text())
        self.assertTrue(self.win._save_resolution())
        self.assertEqual(self.win._resolution_badge.text(), "3000×1700")
        self.assertNotIn("*", self.win._save_res_btn.text())

    def test_lock_readonly_and_segmented_type_bridge(self):
        for lock, edits in ((self.win.k_lock, (self.win.kx_input, self.win.ky_input)),
                            (self.win.js_lock, (self.win.jsx_input, self.win.jsy_input))):
            lock.setChecked(True)
            self.assertTrue(all(edit.isReadOnly() for edit in edits))
            lock.setChecked(False)
            self.assertFalse(any(edit.isReadOnly() for edit in edits))
        self.assertIsInstance(self.win._key_type_cb, SegmentedControl)
        panel = mock.Mock()
        self.win._sub_windows["adb"] = panel
        self.win.set_current_key_type("Macro")
        self.assertEqual(self.win._key_type_cb.currentText(), "宏指牌按键")
        self.assertEqual(self.win._key_type_cb.currentData(), "Macro")
        panel.set_key_type_getter.assert_called_with(self.win.get_current_key_type)
        self.assertEqual(panel.set_key_type_getter.call_args.args[0](), "Macro")

    def test_extended_native_vk_and_full_key_tooltip(self):
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_F24, Qt.NoModifier, 0, 0x87, 0, "")
        self.win._key_input.keyPressEvent(event)
        self.assertEqual((self.win._key_input.text(), self.win._key_input.vk), ("F24", 0x87))
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Print, Qt.NoModifier, 0, 0x2C, 0, "")
        self.win._key_input.keyPressEvent(event)
        self.assertEqual(self.win._key_input.toolTip(), "PrintScreen")
        self.assertEqual(self.win._key_input.vk, 0x2C)

    def test_search_empty_result_has_visible_hint_and_no_fake_commit(self):
        self.win.show()
        combo = self.win._file_cb
        combo.clear()
        combo.addItem("Alpha完整方案.json", "真实A")
        combo.addItem("Beta完整方案.json", "真实B")
        combo._position_and_show_search()
        model = combo.model()
        committed = QSignalSpy(combo.activated)
        combo._search.setText("完全不存在的匹配")
        self.assertEqual(combo._visible_rows(), [])
        self.assertEqual(combo._first_visible_index(), -1)
        self.assertTrue(combo._search._empty_hint.isVisible())
        self.assertEqual(combo._search._empty_hint.text(), "无匹配结果")
        self.assertGreater(combo._search._empty_hint.width(), 0)
        combo._search.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
        combo._search.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.assertEqual(len(committed), 0)
        self.assertIs(combo.model(), model)
        self.assertEqual(combo.count(), 2)
        self.assertEqual(combo.itemData(1), "真实B")
        combo._search.setText("bETA")
        self.assertEqual(combo._visible_rows(), [1])
        combo._move_visible(1)
        self.assertEqual(combo.currentData(), "真实B")
        self.assertTrue(combo._search._empty_hint.isHidden())

    def test_silent_long_filename_elision_keeps_full_tooltip(self):
        combo = self.win._file_cb
        text = "完整长文件名" * 18 + ".json"
        path = str(self.root / text)
        previous = combo.blockSignals(True)
        combo.clear()
        combo.addItem(text, path)
        combo.blockSignals(previous)
        combo.combo._p2_elision_filter.eventFilter(combo.combo, QEvent(QEvent.ToolTip))
        expected = text + "\n" + path
        self.assertEqual(combo.combo.toolTip(), expected)
        self.assertEqual(QWidget.toolTip(combo), expected)
        self.assertEqual(combo.itemData(0, Qt.ToolTipRole), expected)
        self.assertEqual(combo.currentText(), text)

    def test_author_open_false_reports_failure_without_opening_browser(self):
        self.win.bg.setOpenExternalLinks(True)
        with mock.patch.object(hina_aside.QDesktopServices, "openUrl", return_value=False) as opened:
            self.win.bg._activate_author()
        opened.assert_called_once()
        self.assertIn("无法打开作者链接", self.win._status_label.text())
        self.assertIn(self.win.bg.AUTHOR_URL, self.win._status_label.text())

    def test_owner_hidden_reopen_keeps_position_and_overwrite_bridge(self):
        class Panel(QWidget):
            def set_overwrite_guard(self, guard, epoch):
                self.guard, self.epoch = guard, epoch
        panel = Panel()
        panel.resize(240, 160)
        posted = mock.Mock()
        with mock.patch.object(panel, "raise_") as raised, mock.patch.object(panel, "activateWindow") as activated:
            self.win._show_sub("p2", lambda: panel, posted)
            self.assertIs(panel.parentWidget(), self.win)
            self.assertTrue(panel.isWindow())
            self.assertTrue(panel.testAttribute(Qt.WA_DontShowOnScreen))
            self.assertEqual(panel.epoch(), self.win._overwrite_coordinator.get_epoch())
            panel.move(40, 40)
            self.win._show_sub("p2", lambda: self.fail("不应重建既有窗"), posted)
            self.assertEqual((panel.x(), panel.y()), (40, 40))
            self.assertEqual(posted.call_count, 2)
            raised.assert_not_called()
            activated.assert_not_called()
            with mock.patch.object(self.win, "_confirm_key_overwrite", return_value=False) as confirm:
                self.assertFalse(panel.guard("old", [1]))
                confirm.assert_called_once_with(panel, "old", [1])

    def test_negative_screens_reachability_preserves_user_position(self):
        areas = [(-1920, 0, 1920, 1040), (0, 0, 1920, 1040)]
        self.assertEqual(plan_subwindow_position((-1800, 120, 660, 476), (0, 0, 300, 200), areas,
                                                first_open=True), (-1620, 258))
        self.assertEqual(plan_subwindow_position((0, 0, 660, 476), (-1700, 200, 300, 200), areas,
                                                first_open=False), (-1700, 200))
        self.assertEqual(plan_subwindow_position((0, 0, 660, 476), (-1700, 200, 300, 200), [areas[1]],
                                                first_open=False), (0, 200))
        self.assertEqual(plan_subwindow_position((-900, 10, 660, 476), (0, 0, 2000, 1600), [(-1024, 0, 1024, 728)],
                                                first_open=True), (-1024, 0))
