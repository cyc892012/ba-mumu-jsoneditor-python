"""正式管理窗体的紧凑呈现回归；全部数据隔离，无设备/监听。"""
from pathlib import Path
import unittest
from unittest import mock

from PyQt5.QtCore import Qt, QPoint, QRect
from PyQt5.QtGui import QColor, QPainter, QPixmap
from PyQt5.QtWidgets import QPushButton
import theme
import test_migration_management_forms as migration
from core.backup_manager import BackupManager
from ui.index_backup_form import IndexBackupForm
from ui.log_viewer_form import LogViewerForm
from ui.undo_history_form import UndoHistoryForm

APP = migration.APP
INDEX = migration.INDEX


class P3ManagementLayoutTest(unittest.TestCase):
    # 复用隔离夹具，不继承原测试类，避免重复执行其测试。
    setUp = migration.ManagementFormsTest.setUp
    close_windows = migration.ManagementFormsTest.close_windows
    window = migration.ManagementFormsTest.window
    index = migration.ManagementFormsTest.index
    backup = migration.ManagementFormsTest.backup
    history = migration.ManagementFormsTest.history
    log = migration.ManagementFormsTest.log

    def show_hidden(self, window):
        window.show()
        APP.processEvents()
        self.assertEqual(APP.platformName(), "offscreen")
        self.assertTrue(window.testAttribute(Qt.WA_DontShowOnScreen))

    def test_server_display_preserves_filename_selection_and_itemdata(self):
        window = self.index()
        combo = window._index_cb
        self.assertEqual(combo.currentText(), INDEX)
        self.assertEqual(combo.currentData(), INDEX)
        self.assertIn("官服", combo.combo.currentText())
        self.assertIn(INDEX, combo.combo.toolTip())
        identity = combo.currentData()
        window._on_refresh()
        self.assertEqual(combo.currentData(), identity)
        combo.setCurrentText(migration.OTHER)
        self.assertEqual(combo.currentText(), migration.OTHER)
        self.assertIn("日服", combo.combo.currentText())
        self.assertEqual(combo.itemData(combo.findText(INDEX)), INDEX)

    def test_backup_tooltips_bind_to_valid_rows_not_corrupt_positions(self):
        window = self.index()
        stamps = ["2026-11-10_12-00-00-123", "2026-11-10_12-00-50-987"]
        window._populate_backup_table([None, {"timestamp": ""}] + [
            {"timestamp": stamp, "indexFileName": INDEX, "schemeCount": 2} for stamp in stamps])
        self.assertEqual(window._tbl1.rowCount(), 2)
        for row, stamp in enumerate(stamps):
            item = window._tbl1.item(row, 0)
            self.assertEqual(item.toolTip(), stamp)
            self.assertEqual(item.data(Qt.UserRole), (window._target(), stamp))

    def test_missing_count_filter_and_failure_keep_true_zero_rows(self):
        for name in ("a", "b"):
            (self.keymaps / (Path(INDEX).stem + f"-{name}.json")).write_text("{}", encoding="utf-8")
        window = self.index()
        window._tbl2.item(0, 0).setCheckState(Qt.Checked)
        self.assertEqual(window._missing_count.text(), "已选 1 / 可见 2 / 共 2")
        window._search_ie.setText("a")
        self.assertIn("可见 1 / 共 2", window._missing_count.text())
        window._search_ie.setText("no-match")
        self.assertEqual(window._tbl2.rowCount(), 0)
        self.assertIn("没有匹配", window._missing_empty.text())
        window._search_ie.clear()
        self.assertEqual(window._missing_count.text(), "已选 0 / 可见 2 / 共 2")
        with mock.patch.object(window._backup_mgr, "scan_orphan_schemes", side_effect=OSError("离线扫描拒绝")):
            window._on_scan_missing()
        APP.processEvents()
        self.assertEqual(window._tbl2.rowCount(), 0)
        self.assertIn("扫描失败", window._missing_empty.text())
        self.assertIn("离线扫描拒绝", window._status_label.text())
        self.assertFalse(window._btn_fill.isEnabled())

    def test_empty_backup_help_and_all_actions_remain_reachable(self):
        window = self.index()
        self.show_hidden(window)
        self.assertEqual(window._tbl1.rowCount(), 0)
        self.assertFalse(window._backup_empty.isHidden())
        buttons = {button.text(): button for button in window.findChildren(QPushButton)}
        for text in ("刷新", "手动备份", "还原选中", "清理旧备份", "扫描缺失", "补全索引"):
            self.assertIn(text, buttons)
        self.assertEqual(buttons["还原选中"]._role, "danger")
        self.assertEqual(buttons["清理旧备份"]._role, "danger")
        from PyQt5.QtWidgets import QLabel
        help_labels = [label for label in window.findChildren(QLabel) if "自动备份由主窗口" in label.toolTip()]
        self.assertEqual(len(help_labels), 1)
        self.assertIn("内容相同会去重", help_labels[0].toolTip())

    def test_log_tooltips_and_filter_use_same_real_rows(self):
        window, folder = self.log()
        self.assertEqual(window._tbl.horizontalHeaderItem(1).text(), "创建时间")
        for row, (path, name, timestamp, size) in enumerate(window._all_files):
            self.assertEqual(window._tbl.item(row, 0).data(Qt.UserRole), (path, name))
            self.assertIn(path, window._tbl.item(row, 0).toolTip())
            self.assertEqual(window._tbl.item(row, 1).toolTip(), timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"))
        window._tbl.selectRow(0)
        window._txt_search.setText("no-match")
        self.assertEqual(window._tbl.rowCount(), 2)
        self.assertIn("没有匹配", window._empty_state.text())
        self.assertFalse(window._btn_delete.isEnabled())
        window._txt_search.clear()
        self.assertTrue(window._empty_state.isHidden())

    def test_log_missing_empty_and_permission_failure_are_distinct(self):
        window = self.window(LogViewerForm)
        self.assertEqual(window._tbl.rowCount(), 0)
        self.assertIn("尚不存在", window._empty_state.text())
        folder = self.root / "Logs"
        folder.mkdir()
        window.refresh()
        self.assertIn("暂无日志文件", window._empty_state.text())
        with mock.patch("ui.log_viewer_form.os.listdir", side_effect=PermissionError("denied")):
            window.refresh()
        window._txt_search.setText("query")
        APP.processEvents()
        self.assertEqual(window._tbl.rowCount(), 0)
        self.assertIn("无法读取日志目录", window._empty_state.text())
        self.assertEqual(window._lbl_status.text(), "无法读取日志目录")
        self.assertEqual(window._lbl_status.toolTip(), window._lbl_status.text())

    def test_history_full_tooltip_identity_scroll_and_current_emphasis(self):
        window, manager = self.history(50)
        self.show_hidden(window)
        current = manager.get_current_index()
        rect = window._tbl.visualItemRect(window._tbl.item(current, 0))
        self.assertTrue(window._tbl.viewport().rect().intersects(rect))
        self.assertGreater(window._tbl.verticalScrollBar().maximum(), 0)
        for row, info in enumerate(manager.get_history()):
            self.assertEqual(window._tbl.item(row, 0).data(Qt.UserRole), (manager.get_revision(), row))
            self.assertEqual(window._tbl.item(row, 1).toolTip(), info.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"))
            if info.description:
                self.assertIn(info.description, window._tbl.item(row, 2).toolTip())
        marker = window._tbl.item(current, 0)
        self.assertEqual(marker.background().color(), QColor(theme.PINK_50))
        self.assertEqual(marker.foreground().color(), QColor(theme.VIOLET_700))
        self.assertTrue(marker.font().bold())
        self.assertFalse(window._btn_jump.isEnabled())
        window._tbl.selectRow(0)
        self.assertTrue(window._btn_jump.isEnabled())

    def test_history_empty_and_unbound_do_not_create_fake_current_row(self):
        window = self.window(UndoHistoryForm)
        self.show_hidden(window)
        self.assertEqual(window._tbl.rowCount(), 0)
        self.assertEqual(window._lbl_status.text(), "当前位置：--")
        window.set_backup_manager(BackupManager())
        self.assertEqual(window._tbl.rowCount(), 0)
        self.assertEqual(window._lbl_status.text(), "暂无历史记录")
        for button in (window._btn_undo, window._btn_redo, window._btn_jump):
            self.assertFalse(button.isEnabled())
        self.assertFalse(window._empty_state.isHidden())

    def test_compact_hidden_render_and_long_status_do_not_expand_windows(self):
        # 仅输出一张本次集成接触表；三档最终验收由主会话统一执行。
        self.backup("2026-11-10_12-34-56-789", [{"方案预览": {}}])
        for i in range(18):
            (self.keymaps / (Path(INDEX).stem + f"-超长缺失方案名称_{i:02d}.json")).write_text("{}", encoding="utf-8")
        index = self.index()
        log, folder = self.log()
        for i in range(18):
            (folder / ("log_" + "long_name_" * 8 + str(i) + ".txt")).write_text("离线", encoding="utf-8")
        history, manager = self.history(50)
        snapshots = []
        for window, size in ((index, (540, 380)), (log, (520, 330)), (history, (520, 310))):
            self.show_hidden(window)
            self.assertEqual((window.width(), window.height()), size)
            status = index._status_label if window is index else window._lbl_status
            status.setText("离线长路径状态：" + "目录名称/" * 40)
            APP.processEvents()
            self.assertEqual(status.text(), status.toolTip())
            self.assertEqual((window.width(), window.height()), size)
            snapshots.append(window.grab())
        index._tabs.setCurrentIndex(1)
        APP.processEvents()
        self.assertGreater(index._tbl2.verticalScrollBar().maximum(), 0)
        for button in (index._btn_fill, index._btn_scan):
            point = button.mapTo(index, QPoint(0, 0))
            self.assertTrue(index.rect().contains(QRect(point, button.size())))
            self.assertEqual(button.height(), 26)
        snapshots.insert(1, index.grab())
        import os
        evidence = os.environ.get("P3_MANAGEMENT_EVIDENCE")
        if evidence:
            contact = QPixmap(1088, 780)
            contact.fill(QColor("#ececec"))
            painter = QPainter(contact)
            for image, (x, y) in zip(snapshots, ((0, 0), (548, 0), (0, 400), (548, 400))):
                painter.drawPixmap(x, y, image)
            painter.end()
            self.assertTrue(contact.save(str(Path(evidence) / "integration_contact_100.png")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
