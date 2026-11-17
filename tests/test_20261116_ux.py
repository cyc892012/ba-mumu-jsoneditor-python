"""20261116 体验优化：离线进程边界、真实鼠标事件及表格模型回归。"""
import ctypes
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import Qt, QEvent, QPointF
from PyQt5.QtGui import QMouseEvent, QFocusEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from adb.parser import TouchCoordinate
from core.json_editor import TYPE_CLICK, TYPE_MACRO
from ui.adb_touch_form import AdbTouchForm
from ui.range_clear_form import RangeClearForm
from ui.log_viewer_form import LogViewerForm
from ui.main_window import MainWindow
from ui.touch_presentation import ROW_ROLE
from utils.open_folder import open_folder

APP = QApplication.instance() or QApplication([])


def hidden(form):
    form.setAttribute(Qt.WA_DontShowOnScreen, True)
    form.show()
    APP.processEvents()
    return form


class FolderTests(unittest.TestCase):
    def test_select_quotes_windows_special_paths_without_shell(self):
        shell32 = ctypes.WinDLL("shell32")
        shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        with tempfile.TemporaryDirectory(prefix="open_folder_") as temp_dir:
            root = Path(temp_dir)
            paths = (
                root / "键位" / "方案.json",
                root / "有 空格" / "方案 (1).json",
                root / "中文 (备份) & 副本" / "a b.json",
            )
            for path in paths:
                path = str(path)
                with self.subTest(path=path), patch("utils.open_folder.subprocess.Popen") as popen, patch("os.system") as system:
                    open_folder(path, select=True)
                    popen.assert_called_once()
                    command = popen.call_args.args[0]
                    self.assertEqual(command, f'explorer /select,"{os.path.normpath(path)}"')
                    self.assertFalse(popen.call_args.kwargs["shell"])
                    self.assertEqual(popen.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
                    argc = ctypes.c_int()
                    argv = shell32.CommandLineToArgvW(command, ctypes.byref(argc))
                    try:
                        self.assertEqual([argv[i] for i in range(argc.value)], ["explorer", "/select," + os.path.normpath(path)])
                    finally:
                        kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))
                    system.assert_not_called()

    def test_directory_uses_startfile_only(self):
        with tempfile.TemporaryDirectory(prefix="open_folder_") as temp_dir:
            path = os.path.join(temp_dir, "中文 目录 (1)", "子目录", "..")
            with patch("os.startfile") as start, patch("subprocess.Popen") as popen, patch("os.system") as system:
                open_folder(path)
                start.assert_called_once_with(os.path.normpath(path))
                popen.assert_not_called()
                system.assert_not_called()

    def test_main_loaded_file_uses_shared_helper(self):
        folder = os.path.join(tempfile.gettempdir(), "键位")
        fake = SimpleNamespace(_json_path=os.path.join(folder, "a b.json"), _config=SimpleNamespace(json_folder_path=folder))
        with patch("os.path.isfile", return_value=True), patch("utils.open_folder.open_folder") as launch:
            MainWindow._open_json_dir(fake)
            launch.assert_called_once_with(fake._json_path, select=True)

    def test_main_unloaded_folder_uses_shared_helper(self):
        folder = os.path.join(tempfile.gettempdir(), "键位")
        fake = SimpleNamespace(_json_path="", _config=SimpleNamespace(json_folder_path=folder))
        with patch("os.path.isdir", return_value=True), patch("utils.open_folder.open_folder") as launch:
            MainWindow._open_json_dir(fake)
            launch.assert_called_once_with(fake._config.json_folder_path)

    def test_main_launch_failure_is_caught(self):
        folder = os.path.join(tempfile.gettempdir(), "键位")
        fake = SimpleNamespace(_json_path="", _config=SimpleNamespace(json_folder_path=folder))
        with patch("os.path.isdir", return_value=True), patch("utils.open_folder.open_folder", side_effect=OSError("拒绝访问")), patch("ui.main_window.MessageDialog.warning") as warning:
            MainWindow._open_json_dir(fake)
            self.assertIn("无法打开文件夹", warning.call_args.args[2])

    def test_main_missing_folder_retains_warning(self):
        fake = SimpleNamespace(_json_path="", _config=SimpleNamespace(json_folder_path=""))
        with patch("ui.main_window.MessageDialog.warning") as warning:
            MainWindow._open_json_dir(fake)
            self.assertEqual(warning.call_args.args[2], "文件夹不存在！")

    def test_log_shared_helper_and_launch_failure(self):
        fake = SimpleNamespace(_log_dir=os.path.join(tempfile.gettempdir(), "日志 (1)"))
        with patch("os.path.isdir", return_value=True), patch("ui.log_viewer_form.open_folder") as launch:
            LogViewerForm._on_open_log_dir(fake)
            launch.assert_called_once_with(fake._log_dir)
        with patch("os.path.isdir", return_value=True), patch("ui.log_viewer_form.open_folder", side_effect=OSError("拒绝访问")), patch("ui.log_viewer_form.QMessageBox.warning") as warning:
            LogViewerForm._on_open_log_dir(fake)
            self.assertIn("无法打开目录", warning.call_args.args[2])


class RegionTests(unittest.TestCase):
    def setUp(self):
        self.form = hidden(RangeClearForm())
        self.form._preview_timer.stop()
        self.form.set_resolution(1001, 1001)
        self.form.set_json_data(json.dumps({"keymaps": [
            {"key": {"text": str(i)}, "icon": {"rel_position": {"rel_x": x, "rel_y": x}}}
            for i, x in enumerate((0, .25, .5, .9, 1))]}))
        self.widget = self.form._thumbnail
        self.set_bounds((400, 400, 600, 600))

    def tearDown(self):
        self.form.close()
        self.form.deleteLater()
        APP.processEvents()

    def edits(self):
        return self.form._x1, self.form._y1, self.form._x2, self.form._y2

    def values(self):
        return tuple(int(edit.text()) for edit in self.edits())

    def set_bounds(self, values):
        for edit, value in zip(self.edits(), values):
            edit.setText(str(value))

    def point(self, x, y):
        canvas = self.widget.canvas_rect()
        return QPointF(canvas.left() + x * canvas.width(), canvas.top() + y * canvas.height())

    def mouse(self, kind, point, button=Qt.NoButton, buttons=Qt.NoButton):
        QApplication.sendEvent(self.widget, QMouseEvent(kind, point, button, buttons, Qt.NoModifier))

    def drag(self, start, end):
        self.mouse(QEvent.MouseButtonPress, self.point(*start), Qt.LeftButton, Qt.LeftButton)
        self.mouse(QEvent.MouseMove, self.point(*end), buttons=Qt.LeftButton)
        result = self.values()
        self.mouse(QEvent.MouseButtonRelease, self.point(*end), Qt.LeftButton)
        self.assertEqual(self.values(), result)

    def test_new_box_all_directions_syncs_hits_and_points(self):
        for start, end in (((.1, .1), (.8, .8)), ((.8, .8), (.1, .1)), ((.1, .8), (.8, .1)), ((.8, .1), (.1, .8))):
            with self.subTest(start=start):
                self.set_bounds((400, 400, 600, 600))
                self.drag(start, end)
                self.assertEqual(self.values(), (100, 100, 800, 800))
                self.assertEqual(self.form._pending_delete_count, 2)
                self.assertEqual(self.form._range_hit_count, 2)
                self.assertEqual([p[2] for p in self.widget.points], [False, True, True, False, False])
                self.assertIn("命中 2 个", self.form._count_label.text())
                self.assertIn("2 个", self.form._status_lbl.text())

    def test_move_clamps_at_each_boundary_without_squashing(self):
        for end, expected in (((2, 2), (600, 600, 1000, 1000)), ((-1, -1), (0, 0, 400, 400))):
            with self.subTest(end=end):
                self.set_bounds((200, 200, 600, 600))
                self.drag((.4, .4), end)
                self.assertEqual(self.values(), expected)
                self.assertEqual(self.values()[2] - self.values()[0], 400)
                self.assertEqual(self.values()[3] - self.values()[1], 400)

    def test_resize_four_edges_preserves_other_edges(self):
        for start, end, expected in (((.2, .5), (.1, .5), (100, 200, 800, 800)),
                                     ((.8, .5), (.9, .5), (200, 200, 900, 800)),
                                     ((.5, .2), (.5, .1), (200, 100, 800, 800)),
                                     ((.5, .8), (.5, .9), (200, 200, 800, 900))):
            with self.subTest(start=start):
                self.set_bounds((200, 200, 800, 800))
                self.drag(start, end)
                self.assertEqual(self.values(), expected)

    def test_resize_four_corners(self):
        for start, end, expected in (((.2, .2), (.1, .1), (100, 100, 800, 800)),
                                     ((.8, .2), (.9, .1), (200, 100, 900, 800)),
                                     ((.2, .8), (.1, .9), (100, 200, 800, 900)),
                                     ((.8, .8), (.9, .9), (200, 200, 900, 900))):
            with self.subTest(start=start):
                self.set_bounds((200, 200, 800, 800))
                self.drag(start, end)
                self.assertEqual(self.values(), expected)

    def test_edge_and_corner_flip_past_opposite_side(self):
        self.set_bounds((200, 200, 800, 800))
        self.drag((.2, .5), (.95, .5))
        self.assertEqual(self.values(), (800, 200, 950, 800))
        self.set_bounds((200, 200, 800, 800))
        self.drag((.2, .2), (.95, .95))
        self.assertEqual(self.values(), (800, 800, 950, 950))

    def test_under_four_control_pixels_is_click(self):
        for rel in ((.1, .1), (.5, .5), (.4, .4)):
            with self.subTest(rel=rel):
                start = self.point(*rel)
                end = start + QPointF(2, 1)
                self.mouse(QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
                self.mouse(QEvent.MouseMove, end, buttons=Qt.LeftButton)
                self.mouse(QEvent.MouseButtonRelease, end, Qt.LeftButton)
                self.assertEqual(self.values(), (400, 400, 600, 600))

    def test_double_click_resets_to_current_resolution(self):
        self.form.set_resolution_getter(lambda: (1280, 720))
        self.mouse(QEvent.MouseButtonDblClick, self.point(.4, .4), Qt.LeftButton, Qt.LeftButton)
        self.assertEqual(self.values(), (0, 0, 1279, 719))
        self.assertEqual(self.form._range_hit_count, 3)

    def test_manual_reverse_and_overscreen_semantics_unchanged(self):
        self.set_bounds((1100, 1100, -100, -100))
        self.assertEqual(self.widget.bounds, (0, 0, 1, 1))
        self.assertEqual(self.form._range_hit_count, 5)
        self.set_bounds((800, 800, 100, 100))
        self.assertEqual(self.widget.bounds, (.1, .1, .8, .8))
        self.assertEqual(self.form._range_hit_count, 2)

    def test_letterbox_uses_actual_canvas_and_ignores_padding_press(self):
        self.widget.setFixedSize(220, 180)
        APP.processEvents()
        canvas = self.widget.canvas_rect()
        self.assertGreater(canvas.top(), 4)
        self.mouse(QEvent.MouseButtonPress, QPointF(50, 1), Qt.LeftButton, Qt.LeftButton)
        self.mouse(QEvent.MouseMove, self.point(.9, .9), buttons=Qt.LeftButton)
        self.mouse(QEvent.MouseButtonRelease, self.point(.9, .9), Qt.LeftButton)
        self.assertEqual(self.values(), (400, 400, 600, 600))
        self.drag((.1, .1), (.8, .8))
        self.assertEqual(self.values(), (100, 100, 800, 800))

    def test_cursor_shapes_follow_hit_test(self):
        self.set_bounds((200, 200, 800, 800))
        for pos, cursor in (((.05, .05), Qt.CrossCursor), ((.5, .5), Qt.SizeAllCursor),
                            ((.2, .5), Qt.SizeHorCursor), ((.5, .2), Qt.SizeVerCursor),
                            ((.2, .2), Qt.SizeFDiagCursor), ((.8, .2), Qt.SizeBDiagCursor)):
            with self.subTest(pos=pos):
                self.mouse(QEvent.MouseMove, self.point(*pos))
                self.assertEqual(self.widget.cursor().shape(), cursor)

    def test_drag_writes_are_signal_blocked_and_refresh_once_per_move(self):
        with patch.object(self.form, "_update_count", wraps=self.form._update_count) as refresh:
            self.mouse(QEvent.MouseButtonPress, self.point(.1, .1), Qt.LeftButton, Qt.LeftButton)
            self.mouse(QEvent.MouseMove, self.point(.8, .8), buttons=Qt.LeftButton)
            self.assertEqual(refresh.call_count, 1)
            self.assertTrue(all(not edit.signalsBlocked() for edit in self.edits()))
            self.mouse(QEvent.MouseButtonRelease, self.point(.8, .8), Qt.LeftButton)
            self.assertEqual(refresh.call_count, 1)

    def test_pixel_rounding_clamping_and_invalid_resolution(self):
        self.form._on_preview_bounds_changed((-.1, .0005, 1.1, .5015))
        self.assertEqual(self.values(), (0, 1, 1000, 502))
        before = self.values()
        self.form._res_getter = lambda: (1, 1)
        self.form._on_preview_bounds_changed((0, 0, 1, 1))
        self.assertEqual(self.values(), before)
        self.assertIn("无法调整范围", self.form.status_bar.currentMessage())


class AdbRowTests(unittest.TestCase):
    def setUp(self):
        auto_detection = patch.object(AdbTouchForm, "_auto_detect_on_show")
        auto_detection.start()
        self.addCleanup(auto_detection.stop)
        self.form = hidden(AdbTouchForm())
        self.form._ui_timer.stop()
        self.form._screen_width, self.form._screen_height = 1001, 1001
        self.form._is_collecting = True
        self.add(100, 100)
        self.add(500, 500)
        self.add(900, 900)

    def tearDown(self):
        self.form.close()
        self.form.deleteLater()
        APP.processEvents()

    def add(self, x, y):
        self.form._on_touch_detected(TouchCoordinate(x=x, y=y))
        self.form._drain_touch_queue()

    def assert_rows(self, expected):
        form = self.form
        self.assertEqual(form._tbl.rowCount(), len(expected))
        self.assertEqual(list(form._row_data), list(range(len(expected))))
        self.assertEqual([d.screen_x for d in form._row_data.values()], expected)
        for row, data in form._row_data.items():
            self.assertEqual(form._tbl.item(row, form.COL_SEQ).text(), str(row + 1))
            self.assertEqual(data.seq, row + 1)
            self.assertIs(form._tbl.item(row, form.COL_SCREEN).data(ROW_ROLE), data)
            self.assertIs(form._tbl.cellWidget(row, form.COL_KEY).property("touchRowData"), data)
        self.assertIn(f"捕获 {len(expected)} 个点", form._collection_status_label.text())

    def test_delete_middle_first_last_and_only_row(self):
        self.form._delete_row(1)
        self.assert_rows([100, 900])
        self.form._delete_row(0)
        self.assert_rows([900])
        self.form._delete_row(0)
        self.assert_rows([])
        self.assertTrue(self.form._tbl.empty_hint.isVisible())
        self.form._delete_row(0)
        self.add(300, 300)
        self.assert_rows([300])
        self.add(700, 700)
        self.form._delete_row(1)
        self.assert_rows([300])

    def test_moved_widgets_keep_correct_key_type_and_delete_identity(self):
        last = self.form._row_data[2]
        last.checked, last.captured_key, last.vk_code, last.key_type = True, "C", 67, TYPE_MACRO
        self.form._delete_row(0)
        self.assertIs(self.form._row_data[1], last)
        edit = self.form._tbl.cellWidget(1, self.form.COL_KEY)
        edit._on_captured("D", 68)
        self.assertEqual((last.captured_key, last.vk_code), ("D", 68))
        self.assertIsNone(self.form._row_data[0].captured_key)
        combo = self.form._tbl.cellWidget(1, self.form.COL_KEY_TYPE)
        combo.setCurrentIndex(combo.findData(TYPE_MACRO))
        self.assertEqual(last.key_type, TYPE_MACRO)
        button = self.form._tbl.cellWidget(1, self.form.COL_DELETE)
        self.assertEqual(button.toolTip(), "删除此行")
        button.click()
        self.assert_rows([500])

    def test_deleted_coordinate_returns_as_fresh_row_while_collecting(self):
        removed = self.form._row_data[1]
        removed.checked, removed.captured_key, removed.hit_count = True, "B", 5
        self.form._delete_row(1)
        self.add(500, 500)
        self.assert_rows([100, 900, 500])
        fresh = self.form._row_data[2]
        self.assertIsNot(fresh, removed)
        self.assertEqual(fresh.hit_count, 1)
        self.assertFalse(fresh.checked)
        self.assertIsNone(fresh.captured_key)
        self.add(500, 500)
        self.assertIs(self.form._row_data[0], fresh)
        self.assertEqual(fresh.hit_count, 2)
        self.assert_rows([500, 100, 900])

    def test_checked_generation_uses_surviving_rows_only(self):
        output = []
        self.form._json_getter = lambda: '{"keymaps": []}'
        self.form._write_callback = lambda text: output.append(text) or True
        self.form.set_overwrite_guard(lambda *args: True)
        for row, data in self.form._row_data.items():
            data.checked, data.captured_key, data.vk_code = True, chr(65 + row), 65 + row
        self.form._delete_row(1)
        self.assertIn("勾选 2 个", self.form._collection_status_label.text())
        self.assertIn("2 个", self.form._btn_gen_checked.toolTip())
        self.assertTrue(self.form._on_generate_selected())
        maps = json.loads(output[0])["keymaps"]
        self.assertEqual([item["key"]["virtual_key"] for item in maps], [65, 67])
        self.assertEqual([item["icon"]["rel_position"]["rel_x"] for item in maps], [.1, .9])

    def test_delete_key_removes_selected_row_without_confirmation(self):
        self.form._tbl.selectRow(1)
        self.form._tbl.setFocus()
        with patch("ui.adb_touch_form.MessageDialog.question") as confirm:
            QTest.keyClick(self.form._tbl, Qt.Key_Delete)
            confirm.assert_not_called()
        self.assert_rows([100, 900])

    def test_context_menu_deletes_clicked_row(self):
        point = self.form._tbl.visualItemRect(self.form._tbl.item(1, self.form.COL_SEQ)).center()
        def select_action(menu, pos):
            self.assertEqual([a.text() for a in menu.actions()], ["删除此行"])
            menu.actions()[0].trigger()
        with patch("ui.adb_touch_form.QMenu.exec_", new=select_action):
            self.form._tbl.customContextMenuRequested.emit(point)
        self.assert_rows([100, 900])

    def test_stopped_count_and_last_capture_are_updated(self):
        self.form._is_collecting = False
        self.form._delete_row(2)
        self.assertIsNone(self.form._last_captured_screen)
        self.assertIn("已停止，捕获 2 个点", self.form._collection_status_label.text())
        self.assert_rows([100, 500])

    def test_deleting_focused_keycap_restores_paused_handler(self):
        handler = SimpleNamespace(pause=lambda: None, resume=lambda: None, is_key_creation_active=False)
        edit = self.form._tbl.cellWidget(1, self.form.COL_KEY)
        with patch.object(handler, "resume") as resume:
            self.form._keyboard_handler = handler
            self.form.eventFilter(edit, QFocusEvent(QEvent.FocusIn))
            self.form._delete_row(1)
            resume.assert_called_once()
            self.assertIsNone(self.form._editing_key)
            self.assertIsNone(self.form._paused_handler)
        self.form._keyboard_handler = None

    def test_eight_columns_fit_with_vertical_scrollbar(self):
        for i in range(12):
            self.add(1000 - i * 10, 1000 - i * 10)
        APP.processEvents()
        self.assertEqual(self.form._tbl.columnCount(), 8)
        table = self.form._tbl
        header = table.horizontalHeader()
        self.assertEqual(table.horizontalHeaderItem(self.form.COL_DELETE).text(), "删除")
        self.assertGreaterEqual(table.columnWidth(self.form.COL_DELETE) - 8,
                                header.fontMetrics().horizontalAdvance("删除"))
        self.assertGreaterEqual(table.columnWidth(self.form.COL_DELETE),
                                header.sectionSizeHint(self.form.COL_DELETE))
        button = table.cellWidget(0, self.form.COL_DELETE)
        cell = table.visualRect(table.model().index(0, self.form.COL_DELETE))
        self.assertLessEqual(abs(button.geometry().center().x() - cell.center().x()), 1)
        self.assertLessEqual(abs(button.geometry().center().y() - cell.center().y()), 1)
        self.assertEqual(self.form._tbl.horizontalScrollBar().maximum(), 0)


if __name__ == "__main__":
    unittest.main()
