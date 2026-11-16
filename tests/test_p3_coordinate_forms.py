"""P3 坐标布局定向：沿用离线假边界，核对展示与最新业务一致。"""
import json
import unittest
from unittest import mock
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtWidgets import QComboBox, QLineEdit, QMessageBox
import test_migration_coordinate_forms as fixtures
from core.json_editor import calculate_coordinates_k_to_ck
from core.coord_model import px_to_rel
from ui.key_overwrite import MessageDialog, OverwriteCoordinator


class P3CoordinateTests(unittest.TestCase):
    setUp = fixtures.CoordinateFormsTest.setUp
    tearDown = fixtures.CoordinateFormsTest.tearDown
    wide_form = fixtures.CoordinateFormsTest.wide_form
    range_form = fixtures.CoordinateFormsTest.range_form
    mouse_form = fixtures.CoordinateFormsTest.mouse_form

    @staticmethod
    def show_hidden(form):
        form.setAttribute(Qt.WA_DontShowOnScreen, True)
        form.setAttribute(Qt.WA_ShowWithoutActivating, True)
        form.show()
        fixtures.APP.processEvents()

    def test_compact_geometry_9pt_long_tooltips_and_scrolling(self):
        long_name = "长名称需要完整提示" * 12
        wide, source = self.wide_form(fixtures.document(*[
            fixtures.key(65 + i, long_name + str(i), .3, .4) for i in range(40)
        ]))
        region = self.range_form()
        mouse = self.mouse_form()
        wide.set_json_path(str(self.root / (long_name + ".json")))
        # 路径 setter 会按最新包名规则重扫列表；重新装入本测试的离线来源。
        wide._file_cb.clear()
        wide._file_cb.addItem("source", str(source))
        wide._on_read()
        wide._tbl.setCurrentCell(0, 1)
        mouse._save_key_edit.setText("PAGEDOWN")
        mouse._recall_key_edit.setText("BACKSPACE")
        mouse._status_label.setText(long_name)
        for form, size in ((wide, (710, 390)), (region, (480, 310)), (mouse, (260, 230))):
            with self.subTest(form=type(form).__name__):
                self.show_hidden(form)
                self.assertEqual((form.width(), form.height()), size)
                self.assertEqual(form.font().pointSizeF(), 9)
                for widget in form.findChildren(QLineEdit) + form.findChildren(QComboBox):
                    if not widget.isVisible():
                        continue
                    self.assertGreaterEqual(widget.height(), 26)
                    self.assertEqual(widget.font().pointSizeF(), 9)
                    point = widget.mapTo(form, QPoint(0, 0))
                    self.assertGreaterEqual(point.x(), 0)
                    self.assertGreaterEqual(point.y(), 0)
                    self.assertLessEqual(point.x() + widget.width(), form.width())
                    self.assertLessEqual(point.y() + widget.height(), form.height())
        region._status_lbl.setText(long_name)
        self.assertGreater(wide._tbl.verticalScrollBar().maximum(), 0)
        self.assertEqual(wide._tbl.item(0, 1).toolTip(), long_name + "0")
        self.assertIn(long_name, wide._target_file_label.toolTip())
        self.assertEqual(mouse._status_label.toolTip(), long_name)
        self.assertEqual(region._status_lbl.toolTip(), long_name)
        self.assertEqual(mouse._recall_key_edit.toolTip(), "BACKSPACE")

    def test_preview_uses_original_row_precision_and_stale_source_is_not_displayed(self):
        wide, source = self.wide_form(fixtures.document(
            fixtures.key(65, "真实按键", .1234567891234567, .67890123456789)))
        wide._tbl.setCurrentCell(0, 1)
        info = wide._click_keys[0]
        for target in ((1280, 720), (2560, 1080), (3840, 1080)):
            with self.subTest(target=target):
                wide.set_json_data(fixtures.document())
                wide.set_resolution_getter(lambda target=target: target)
                wide._tbl.item(0, 1).setText("显示文字被改写")
                wide._tbl.item(0, 2).setText("0,0")
                wide._refresh_preview_ui()
                x, y, _ = calculate_coordinates_k_to_ck(
                    *wide._source_resolution, *target, info["rel_x"], info["rel_y"])
                self.assertEqual(wide._wide_x.text(), f"{x:.16g}")
                self.assertEqual(wide._relative_label.text(),
                                 f"目标相对坐标：{px_to_rel(x, target[0]):.16g}, {px_to_rel(y, target[1]):.16g}")
                self.assertEqual(wide._selected_key_label.text(), "当前按键：真实按键")
                with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                    wide._on_single_write()
                result = json.loads(wide._json_str)["keymaps"][0]
                self.assertEqual(result["key"]["text"], "真实按键")
                self.assertEqual(result["rel_work_position"]["rel_x"], px_to_rel(x, target[0]))
                self.assertEqual(result["rel_work_position"]["rel_y"], px_to_rel(y, target[1]))
        wide._write_cb.reset_mock()
        source.write_text(fixtures.document(fixtures.key(66)), encoding="utf-8")
        wide._refresh_preview_ui()
        self.assertEqual(wide._relative_label.text(), "目标相对坐标：—")
        wide._on_single_write()
        wide._write_cb.assert_not_called()

    def test_batch_B3_coordinator_cancel_zero_submit_then_single_confirm_commit(self):
        wide, _ = self.wide_form(fixtures.document(
            fixtures.key(65, "A", .7, .4), fixtures.key(66, "B", .8, .5)))
        original = fixtures.document(fixtures.key(65, "A", .1, .1),
                                     fixtures.key(66, "B", .1, .1))
        wide.set_json_data(original)
        coordinator = OverwriteCoordinator()
        listener = mock.Mock()
        wide.set_overwrite_guard(
            lambda before, blocks: coordinator.confirm(wide, before, blocks, listeners=(listener,)),
            coordinator.get_epoch)
        wide._toggle_all(0)
        before_status = wide.status_bar.currentMessage()
        with mock.patch.object(MessageDialog, "question", return_value=False) as ask:
            wide._on_batch_write()
            ask.assert_called_once()
        self.assertEqual(wide._json_str, original)
        self.assertEqual(wide.status_bar.currentMessage(), before_status)
        wide._write_cb.assert_not_called()
        self.assertFalse(coordinator.active)
        with mock.patch.object(MessageDialog, "question", return_value=True) as ask:
            wide._on_batch_write()
            ask.assert_called_once()
        wide._write_cb.assert_called_once()
        self.assertNotEqual(wide._json_str, original)
        self.assertEqual(len(json.loads(wide._json_str)["keymaps"]), 2)
        self.assertEqual(listener.pause.call_count, 2)
        self.assertEqual(listener.resume.call_count, 2)

    def test_region_thumbnail_count_equals_actual_index_deletion(self):
        region = self.range_form(fixtures.document(
            fixtures.key(65, "区外同VK", .1, .1), fixtures.key(65, "区内", .5, .5),
            fixtures.key(0, "区内VK0", .6, .6), fixtures.key(0, "区外VK0", .9, .9)))
        for edit, value in zip((region._x1, region._y1, region._x2, region._y2), (20, 20, 80, 80)):
            edit.setText(str(value))
        self.assertEqual(region._pending_delete_count, 2)
        self.assertEqual(sum(deleting for _, _, deleting in region._thumbnail.points), 2)
        self.assertEqual([index for index, _ in region.get_pending_keys()], [1, 2])
        self.assertIn("待删除 2", region._count_label.text())
        region._on_clear_clicked()
        names = [item["key"]["text"] for item in json.loads(region._json_str)["keymaps"]]
        self.assertEqual(names, ["区外同VK", "区外VK0"])
        self.assertEqual(region._pending_delete_count, 0)
        region._sync_cb.assert_called_once()
        canvas = region._thumbnail.canvas_rect()
        self.assertAlmostEqual(canvas.width() / canvas.height(), 16 / 9)

    def test_live_getter_refresh_and_display_timers_stop_when_hidden(self):
        wide, _ = self.wide_form()
        region = self.range_form()
        size = [101, 101]
        region.set_resolution_getter(lambda: size)
        region._x2.setText("60")
        region._y2.setText("60")
        self.assertEqual(region._pending_delete_count, 1)
        for form in (wide, region):
            self.show_hidden(form)
            self.assertTrue(form._preview_timer.isActive())
        size[:] = [201, 201]
        region._preview_timer.timeout.emit()
        self.assertEqual(region._pending_delete_count, 0)
        self.assertIn("201 × 201", region._resolution_label.text())
        for form in (wide, region):
            form.hide()
            self.assertFalse(form._preview_timer.isActive())
            self.show_hidden(form)
            self.assertTrue(form._preview_timer.isActive())
            form.close()
            self.assertFalse(form._preview_timer.isActive())

    def test_mouse_match_border_keeps_integer_semantics_and_extended_key_support(self):
        mouse = self.mouse_form()
        self.assertFalse(mouse._cur_x.property("p3Matched"))
        mouse._enable_cb.setChecked(True)
        mouse._cur_x.setText("0010")
        mouse._cur_y.setText("-20")
        mouse._do_save_ui()
        self.assertTrue(mouse._cur_x.property("p3Matched"))
        self.assertTrue(mouse._cur_y.property("p3Matched"))
        mouse._saved_x.setText("0010")
        self.assertTrue(mouse._cur_x.property("p3Matched"))
        mouse._saved_y.setText("bad")
        self.assertFalse(mouse._cur_y.property("p3Matched"))
        mouse._saved_y.setText("-20")
        with mock.patch.object(fixtures.mouse_module, "get_cursor_pos", return_value=(10, 999)):
            mouse._poll_mouse()
        self.assertTrue(mouse._cur_x.property("p3Matched"))
        self.assertFalse(mouse._cur_y.property("p3Matched"))
        self.assertIn("#4FAE84", mouse._cur_x.styleSheet())
        self.assertTrue(mouse._save_key_edit._extended_keys)


if __name__ == "__main__":
    unittest.main()
