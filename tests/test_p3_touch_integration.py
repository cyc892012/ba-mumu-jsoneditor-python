"""紧凑触控集成定向回归：假边界、真实渲染及真实生成，不打开可见窗口。"""
import json
import unittest
from unittest.mock import patch

from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtGui import QKeyEvent, QFocusEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLineEdit
import test_migration_touch_forms as touch
from ui.touch_presentation import COLUMNS, RAW_ROLE, ROW_ROLE


class CompactTouchIntegrationTests(unittest.TestCase):
    setUp = touch.TouchMigrationTests.setUp
    tearDown = touch.TouchMigrationTests.tearDown
    connected = touch.TouchMigrationTests.connected
    point = touch.TouchMigrationTests.point

    def test_compact_geometry_controls_and_empty_guide(self):
        self.assertEqual((self.adb.width(), self.adb.height()), (600, 520))
        self.assertEqual((self.quick.width(), self.quick.height()), (400, 260))
        self.assertEqual(self.adb.font().pointSize(), 9)
        self.assertEqual(self.quick.font().pointSize(), 9)
        self.assertTrue(self.adb.testAttribute(Qt.WA_DontShowOnScreen))
        self.assertTrue(self.quick.testAttribute(Qt.WA_DontShowOnScreen))
        self.assertEqual(self.adb._tbl.columnCount(), 8)
        self.assertTrue(self.adb._tbl.empty_hint.isVisible())
        self.assertEqual(self.adb._tbl.horizontalScrollBar().maximum(), 0)
        for control in (self.adb._connect_btn, self.adb._port_edit, self.quick._inner_x,
                        self.quick._auto_btn, *self.adb._generation_mode._buttons,
                        *self.quick._generation_mode._buttons):
            self.assertGreaterEqual(control.height(), 26, f"{type(control).__name__} {getattr(control, 'text', lambda: '')()} min={control.minimumHeight()} max={control.maximumHeight()}")
        self.assertFalse(self.quick._inner_x.isReadOnly())
        self.assertFalse(self.quick._inner_y.isReadOnly())
        self.assertTrue(self.quick._cur_x.isReadOnly())

    def test_semantic_pairs_raw_identity_checked_sort_and_generation(self):
        self.connected()
        self.point(480.123456789123, 230.987654321987)
        original = self.adb._row_data[0]
        original.checked, original.captured_key, original.vk_code = True, "F24", 0x87
        original.key_type = "Macro"
        self.point(900.876543210987, 550.123456789123)
        self.point(480.123457789123, 230.987655321987)
        self.assertIs(self.adb._row_data[0], original)
        self.assertEqual(original.hit_count, 2)
        self.assertTrue(original.checked)
        self.assertEqual(original.vk_code, 0x87)
        self.assertEqual(original.key_type, "Macro")
        self.adb._tbl.cellWidget(0, COLUMNS["type"]).setCurrentIndex(0)
        screen = self.adb._tbl.item(0, COLUMNS["screen"])
        relative = self.adb._tbl.item(0, COLUMNS["relative"])
        self.assertEqual(tuple(screen.data(RAW_ROLE)), (original.screen_x, original.screen_y))
        self.assertEqual(tuple(relative.data(RAW_ROLE)), (original.rel_x, original.rel_y))
        self.assertIs(relative.data(ROW_ROLE), original)
        self.assertIn(",", screen.text())
        self.assertIn(",", relative.text())
        screen.setText("伪造显示")
        relative.setText("0, 0")
        self.assertTrue(self.adb._on_generate_selected())
        result = json.loads(self.doc.text)["keymaps"][0]
        self.assertEqual(result["key"]["virtual_key"], 0x87)
        self.assertEqual(result["type"], "Click")
        self.assertEqual(result["icon"]["rel_position"], {
            "rel_x": float(f"{original.rel_x:.16f}"), "rel_y": float(f"{original.rel_y:.16f}")})
        applied = []
        self.adb.set_apply_coord_callback(lambda *args: applied.append(args))
        self.adb._tbl.selectRow(0)
        self.adb._on_apply_to_main()
        self.assertEqual(applied, [(f"{original.rel_x:.16f}", f"{original.rel_y:.16f}")])

    def test_keycap_events_pause_edit_and_escape_preserve_row_identity(self):
        self.connected()
        self.point()
        edit = self.adb._tbl.cellWidget(0, COLUMNS["keycap"])
        self.adb._generation_mode.setMode("continuous")
        self.adb.eventFilter(edit, QFocusEvent(QEvent.FocusIn))
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_F24, Qt.NoModifier, 0, 0x87, 0, "")
        edit.keyPressEvent(event)
        row = self.adb._row_data[0]
        self.assertEqual((row.captured_key, row.vk_code, edit.vk), ("F24", 0x87, 0x87))
        self.assertTrue(edit.property("p2Keycap"))
        self.assertEqual(self.handler.depth, 1)
        self.assertFalse(self.adb._generate_from_last_captured("A", 65))
        self.assertEqual(self.doc.writes, [])
        edit.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        self.assertIsNone(row.vk_code)
        self.assertEqual(edit.vk, 0)
        self.adb.eventFilter(edit, QFocusEvent(QEvent.FocusOut))
        self.assertEqual(self.handler.depth, 0)

    def test_mode_segments_drive_one_handler_and_one_quick_listener(self):
        self.adb.set_keyboard_handler(self.handler)
        self.assertEqual(len(self.handler.once_callbacks), 1)
        self.assertEqual(len(self.handler.multi_callbacks), 1)
        for mode in ("once", "continuous", "off"):
            self.adb._generation_mode.setMode(mode)
            self.assertEqual(self.handler.once, mode == "once")
            self.assertEqual(self.handler.multi, mode == "continuous")
            self.assertEqual(self.adb._listen_pill.isChecked(), mode != "off")
        listener = self.quick._keyboard_listener
        self.assertEqual(len(listener.callbacks), 1)
        self.quick._generation_mode.setMode("once")
        self.assertTrue(self.quick._on_key_down_ui("G", 71))
        self.assertEqual(self.quick._generation_mode.mode(), "off")
        self.assertFalse(listener.is_listening)
        self.quick._generation_mode.setMode("continuous")
        self.assertIs(self.quick._keyboard_listener, listener)
        self.assertTrue(self.quick._multi_cb.isChecked())
        self.assertFalse(self.quick._once_cb.isChecked())
        self.assertTrue(self.quick._listen_pill.isChecked())

    def test_single_connection_button_stops_and_reconnects_real_fake_session(self):
        QTest.mouseClick(self.adb._connect_btn, Qt.LeftButton)
        self.assertTrue(self.adb._is_collecting)
        self.assertEqual(self.adb._connect_btn.text(), "停止并断开")
        self.assertEqual(self.adb._connection_pill.text(), "采集中")
        old_session = self.adb._session_id
        QTest.mouseClick(self.adb._connect_btn, Qt.LeftButton)
        self.assertFalse(self.adb._is_connected)
        self.assertEqual(self.adb._connection_pill.text(), "未连接")
        QTest.mouseClick(self.adb._connect_btn, Qt.LeftButton)
        self.assertTrue(self.adb._is_collecting)
        self.adb._on_collection_ended(old_session, 1, "旧会话")
        self.assertTrue(self.adb._is_collecting)

    def test_manual_quick_edit_is_live_and_preserves_full_precision(self):
        self.quick._generation_mode.setMode("once")
        self.quick.eventFilter(self.quick._inner_x, QFocusEvent(QEvent.FocusIn))
        self.quick._inner_x.setText("12.12345678912345")
        self.quick._inner_y.setText("34.98765432198765")
        self.quick._inner_x.textEdited.emit(self.quick._inner_x.text())
        self.assertFalse(self.quick._on_key_down_ui("A", 65))
        self.quick.eventFilter(self.quick._inner_x, QFocusEvent(QEvent.FocusOut))
        self.quick._on_timer_tick()
        self.assertEqual(self.quick._inner_x.text(), "12.12345678912345")
        applied = []
        self.quick.set_apply_coord_callback(lambda *args: applied.append(args))
        self.assertTrue(self.quick._on_key_down_ui("A", 65))
        self.assertEqual(applied, [(f"{12.12345678912345:.16f}", f"{34.98765432198765:.16f}")])
        self.assertEqual(self.quick._generation_mode.mode(), "off")

    def test_long_quick_details_scroll_and_full_status_remain_accessible(self):
        message = "完整状态与操作说明。" * 35 + "可访问的末尾"
        self.quick._status_perm.setText(message)
        touch.pump()
        scroll = self.quick._hint_scroll.verticalScrollBar()
        self.assertGreater(scroll.maximum(), 0)
        scroll.setValue(scroll.maximum())
        touch.pump()
        self.assertEqual(scroll.value(), scroll.maximum())
        self.assertEqual(self.quick._result_view.text(), message)
        self.assertEqual(self.quick._result_view.toolTip(), message)
        self.assertTrue(self.quick._hint_scroll.widget().rect().contains(self.quick._status_perm.geometry()))


if __name__ == "__main__":
    unittest.main()
