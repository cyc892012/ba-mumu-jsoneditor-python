"""实例选择与手动端口联动；使用离线后端验证真实窗口行为。"""
import unittest
from unittest.mock import patch

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
import test_migration_touch_forms as touch


class AdbInstanceSelectionTests(unittest.TestCase):
    setUp = touch.TouchMigrationTests.setUp
    tearDown = touch.TouchMigrationTests.tearDown

    def populate(self):
        self.adb._on_detect_finished([
            touch.adb_ui.MuMuPortItem("16512", "BA对策2号机"),
            touch.adb_ui.MuMuPortItem("16544", "BA对策机体待练"),
        ])

    def test_clicking_instance_text_opens_selection_and_updates_port(self):
        self.populate()
        combo = self.adb._port_combo
        self.assertFalse(combo.isEditable())
        QTest.mouseClick(combo, Qt.LeftButton, pos=combo.rect().center())
        touch.pump()
        self.assertTrue(combo.view().isVisible())
        index = combo.model().index(1, 0)
        QTest.mouseMove(combo.view().viewport(), combo.view().visualRect(index).center())
        QTest.qWait(30)
        QTest.mouseClick(combo.view().viewport(), Qt.LeftButton,
                         pos=combo.view().visualRect(index).center(), delay=30)
        touch.pump()
        self.assertEqual(combo.currentText(), "BA对策机体待练 (16544)")
        self.assertEqual(self.adb._get_selected_port(), "16544")

    def test_typing_known_port_selects_corresponding_instance(self):
        self.populate()
        self.adb._port_edit.selectAll()
        QTest.keyClicks(self.adb._port_edit, "16544")
        self.assertEqual(self.adb._port_combo.currentText(), "BA对策机体待练 (16544)")
        self.adb._port_edit.setText("16512")
        self.assertEqual(self.adb._port_combo.currentText(), "BA对策2号机 (16512)")

    def test_unmatched_or_invalid_port_clears_instance_without_changing_input(self):
        self.populate()
        for port in ("1111", "", "abc", "0", "65536", "１６５１２"):
            with self.subTest(port=port):
                self.adb._port_edit.setText(port)
                self.assertEqual(self.adb._port_combo.currentIndex(), -1)
                self.assertEqual(self.adb._port_combo.currentText(), "")
                self.assertEqual(self.adb._port_edit.text(), port)
        self.assertEqual(self.adb._port_combo.count(), 2)

    def test_unknown_port_connection_failure_never_falls_back_to_selected_instance(self):
        self.populate()
        self.adb._port_edit.setText("1111")
        touch.FakeAdb.fail = "connect"
        self.assertFalse(self.adb._connect_adb())
        self.assertEqual(touch.FakeAdb.instances[-1].serial, "127.0.0.1:1111")
        self.assertFalse(self.adb._is_connected)
        self.assertFalse(self.adb._is_collecting)
        self.assertEqual(self.adb._connection_pill.text(), "未连接")
        self.assertEqual(self.adb._port_combo.currentText(), "")
        self.assertIn("连接失败", self.adb._status_label.text())

    def test_invalid_port_is_rejected_before_creating_connection(self):
        self.populate()
        for port in ("", "abc", "0", "65536", "１６５１２"):
            with self.subTest(port=port):
                self.adb._port_edit.setText(port)
                with patch.object(touch.adb_ui, "AdbClient") as client:
                    self.assertFalse(self.adb._connect_adb())
                    client.assert_not_called()
                self.assertEqual(self.adb._port_combo.currentText(), "")

    def test_adb_refused_port_cannot_be_reported_connected(self):
        from adb.client import AdbClient
        client = AdbClient("unused.exe", "127.0.0.1:1111")
        with patch.object(client, "execute", return_value="cannot connect to 127.0.0.1:1111: Connection refused"):
            self.assertFalse(client.connect())
        self.assertFalse(client.is_connected)

    def test_at_least_five_touch_rows_are_fully_visible(self):
        self.adb._screen_width, self.adb._screen_height = 1280, 720
        for index in range(6):
            self.adb._touch_queue.put((index + 1, 100.0 + index * 80, 200.0 + index * 40))
        self.adb._drain_touch_queue()
        touch.pump()
        table = self.adb._tbl
        self.assertEqual(table.rowCount(), 6)
        table.scrollToTop()
        viewport = table.viewport().rect()
        for row in range(5):
            with self.subTest(row=row):
                rect = table.visualRect(table.model().index(row, 0))
                self.assertTrue(viewport.contains(rect), f"第 {row + 1} 行未完整显示：{rect} / {viewport}")


if __name__ == "__main__":
    unittest.main()
