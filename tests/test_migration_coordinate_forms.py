"""坐标工具功能迁移回归：隐藏Qt、假热键/鼠标和独立配置。"""
import contextlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication, QMessageBox, QAbstractItemView
from core.config_manager import ConfigManager
from core.json_editor import create_key, calculate_coordinates_k_to_ck
from core.coord_model import rel_to_px, px_to_rel
from ui.range_clear_form import RangeClearForm
from ui.wide_screen_form import WideScreenForm

APP = QApplication.instance() or QApplication([])
# 构造原生Controller前替换模块；从不读取或移动真实鼠标。
fake_mouse = types.ModuleType("input.mouse_controller")
fake_mouse.get_cursor_pos = lambda: (10, 20)
fake_mouse.move_mouse_to = lambda x, y: None
with mock.patch.dict(sys.modules, {"input.mouse_controller": fake_mouse}):
    mouse_module = importlib.import_module("ui.mouse_track_form")


def key(vk=65, text="A", x=.5, y=.5, kind="Click"):
    return json.loads(create_key(kind, text, str(x), str(y), "30", str(vk)))


def document(*items):
    return json.dumps({"keymaps": list(items)}, ensure_ascii=False)


class FakeHotkeys:
    def __init__(self):
        self.callbacks = {}
        self.calls = []
        self.fail = set()
    def register(self, combo, callback):
        self.calls.append(combo)
        if combo in self.fail:
            return False
        self.callbacks[combo] = callback
        return True
    def unregister_all(self):
        self.callbacks.clear()


class CoordinateFormsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="coordinate_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"MOMO_DATA_DIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        ConfigManager.reset_shared()
        self.addCleanup(ConfigManager.reset_shared)
        self.forms = []
        self.question = mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes)
        self.question.start()
        self.addCleanup(self.question.stop)
        for name in ("warning", "information"):
            patch = mock.patch.object(QMessageBox, name)
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        for form in self.forms:
            form.close()
            form.deleteLater()
        APP.processEvents()

    def range_form(self, text=None):
        form = RangeClearForm()
        self.forms.append(form)
        form.set_resolution(101, 101)
        form.set_json_data(text or document(key()))
        form.set_sync_callback(mock.Mock(return_value=True))
        return form

    def wide_form(self, text=None):
        source = self.root / "source.json"
        source.write_text(text or document(key(x=.1234567891234567, y=.67890123456789)), encoding="utf-8-sig")
        form = WideScreenForm()
        self.forms.append(form)
        form._file_cb.clear()
        form._file_cb.addItem("source", str(source))
        form._res_cb.addItem("1280×720", "1280,720")
        form._res_cb.setCurrentIndex(form._res_cb.count()-1)
        form.set_resolution_getter(lambda: (1280, 720))
        form.set_json_data(document())
        form.set_write_callback(mock.Mock(return_value=True))
        form._on_read()
        return form, source

    def mouse_form(self):
        with mock.patch.object(mouse_module, "HotKeyManager", FakeHotkeys):
            form = mouse_module.MouseTrackForm()
        self.forms.append(form)
        return form

    def test_range_rejects_each_invalid_field_and_nonfinite(self):
        form = self.range_form()
        original = form._json_str
        for field in (form._x1, form._y1, form._x2, form._y2):
            old = field.text()
            for invalid in ("", "-", ".", "NaN", "Infinity", "oops"):
                field.setText(invalid)
                form._on_clear_clicked()
                self.assertEqual(form._json_str, original)
                form._sync_cb.assert_not_called()
            field.setText(old)

    def test_range_false_exception_and_no_callback_keep_local_json(self):
        for callback in (mock.Mock(return_value=False), mock.Mock(side_effect=OSError("占用")), None):
            form = self.range_form()
            original = form._json_str
            form.set_sync_callback(callback)
            form._on_clear_clicked()
            self.assertEqual(original, form._json_str)
            self.assertNotIn("已清空", form.status_bar.currentMessage())

    def test_range_duplicate_vk_and_zero_delete_only_matching_indexes(self):
        items = [key(65,"outside",.1,.1), key(65,"inside",.5,.5), key(0,"zero-in",.6,.6), key(0,"zero-out",.9,.9)]
        form = self.range_form(document(*items))
        for edit, value in zip((form._x1,form._y1,form._x2,form._y2),(20,20,80,80)):
            edit.setText(str(value))
        self.assertEqual([i for i,n in form.get_pending_keys()], [1,2])
        form._on_clear_clicked()
        names = [i["key"]["text"] for i in json.loads(form._json_str)["keymaps"]]
        self.assertEqual(names,["outside","zero-out"])
        self.assertIn("2",form.status_bar.currentMessage())
        form._sync_cb.assert_called_once()

    def test_range_open_internal_edges_and_crossed_screen_edges(self):
        form = self.range_form(document(*(key(i,str(i),x,y) for i,(x,y) in enumerate([(0,0),(1,1),(0,1),(1,0),(.5,.5)]))))
        self.assertEqual(len(form.get_pending_keys()),1)
        for edit,value in zip((form._x1,form._y1,form._x2,form._y2),(-1,-1,101,101)):
            edit.setText(str(value))
        self.assertEqual(len(form.get_pending_keys()),5)
        form._x1.setText("101"); form._x2.setText("-1")
        self.assertEqual(len(form.get_pending_keys()),5)

    def test_range_live_resolution_and_invalid_size(self):
        form = self.range_form()
        size = [101,101]
        form.set_resolution_getter(lambda: size)
        form._x2.setText("60"); form._y2.setText("60")
        self.assertEqual(len(form.get_pending_keys()),1)
        size[:] = [201,201]
        self.assertEqual(len(form.get_pending_keys()),0)
        for value in (1,0,-1,float("nan"),1.5):
            size[:] = [value,101]
            form._on_clear_clicked()
            form._sync_cb.assert_not_called()

    def test_range_cancel_and_stale_confirmation(self):
        form = self.range_form()
        original = form._json_str
        with mock.patch.object(QMessageBox,"question",return_value=QMessageBox.No): form._on_clear_clicked()
        self.assertEqual(form._json_str,original)
        def change(*args):
            form._x2.setText("40")
            return QMessageBox.Yes
        with mock.patch.object(QMessageBox,"question",side_effect=change): form._on_clear_clicked()
        form._sync_cb.assert_not_called()

    def test_range_malformed_json_differs_from_empty_region(self):
        form = self.range_form("broken")
        self.assertIn("无法预览",form._status_lbl.text())
        form._on_clear_clicked()
        form._sync_cb.assert_not_called()
        form.set_json_data(document())
        self.assertIn("0",form._status_lbl.text())

    def test_wide_fractional_preview_equals_real_conversion(self):
        form, source = self.wide_form()
        form._tbl.setCurrentCell(0,1)
        source_obj = json.loads(source.read_text(encoding="utf-8-sig"))["keymaps"][0]
        info = form._click_keys[0]
        self.assertAlmostEqual(info["rel_x"],rel_to_px(source_obj["rel_work_position"]["rel_x"],1280),places=12)
        form._on_single_write()
        result = json.loads(form._json_str)["keymaps"][0]
        self.assertAlmostEqual(rel_to_px(result["rel_work_position"]["rel_x"],1280),float(form._wide_x.text()),places=10)
        self.assertAlmostEqual(result["rel_work_position"]["rel_x"],px_to_rel(info["rel_x"],1280),places=14)
        form._write_cb.assert_called_once()

    def test_wide_keyboard_row_and_display_tampering_do_not_change_identity(self):
        form,_ = self.wide_form(document(key(65,"same",.2,.2),key(66,"same",.8,.8)))
        self.assertEqual(form._tbl.editTriggers(),QAbstractItemView.NoEditTriggers)
        form._tbl.setCurrentCell(0,1)
        form._tbl.setCurrentCell(1,1)
        form._tbl.item(1,1).setText("fake")
        form._tbl.item(1,2).setText("0,0")
        form._on_single_write()
        result = json.loads(form._json_str)["keymaps"][0]
        self.assertEqual(result["key"]["virtual_key"],66)
        self.assertEqual(result["key"]["text"],"same")
        self.assertAlmostEqual(result["rel_work_position"]["rel_x"],.8)

    def test_wide_file_changed_or_resolution_changed_rejects_stale_rows(self):
        form,source = self.wide_form()
        form._tbl.setCurrentCell(0,1)
        source.write_text(document(key(66)),encoding="utf-8")
        form._on_single_write()
        form._write_cb.assert_not_called()
        self.assertIn("变化",form.status_bar.currentMessage())
        form._res_cb.setCurrentIndex(0)
        self.assertEqual(form._tbl.rowCount(),0)

    def test_wide_false_exception_and_no_callback_rollback(self):
        for callback in (mock.Mock(return_value=False),mock.Mock(side_effect=OSError("占用")),None):
            form,_=self.wide_form()
            form._tbl.setCurrentCell(0,1)
            form.set_write_callback(callback)
            original=form._json_str
            form._on_single_write()
            self.assertEqual(form._json_str,original)
            self.assertNotIn("成功",form.status_bar.currentMessage())

    def test_wide_macro_all_curves_transformed_without_rounding_clicks(self):
        macro=key(67,"macro",.25,.25,"Macro")
        macro["press_actions"]=["curve_rel:mouse;(0.25,0.25)","wait:5","curve_rel:mouse;(0.75,0.75)"]
        form,_=self.wide_form(document(macro))
        self.assertEqual(form._tbl.rowCount(),1)
        form._tbl.setCurrentCell(0,1)
        form._on_single_write()
        result=json.loads(form._json_str)["keymaps"][0]
        self.assertEqual(result["press_actions"][0],"curve_rel:mouse;(0.25,0.25)")
        self.assertEqual(result["press_actions"][2],"curve_rel:mouse;(0.75,0.75)")
        self.assertEqual(result["press_actions"][1],"wait:5")

    def test_wide_batch_once_and_checkbox_header_sync(self):
        form,_=self.wide_form(document(key(65),key(66)))
        form._toggle_all(0)
        self.assertEqual(form._hdr_chk.text(),"☑")
        form._tbl.item(1,0).setCheckState(Qt.Unchecked)
        self.assertEqual(form._hdr_chk.text(),"◩")
        form._on_batch_write()
        form._write_cb.assert_called_once()
        self.assertEqual(len(json.loads(form._json_str)["keymaps"]),1)

    def test_wide_source_dirs_restore_same_name_and_deleted_selection(self):
        form,_=self.wide_form()
        schemes=self.root/"schemes"; schemes.mkdir()
        data=self.root/"data"; data.mkdir()
        for folder in (schemes,data): (folder/"方案.json").write_text(document(key()),encoding="utf-8")
        form._config.json_folder_path=str(schemes)
        form._pkg_cb.setCurrentIndex(form._pkg_cb.findText("其他"))
        form._refresh_file_list()
        target=str(data/"方案.json")
        form._file_cb.setCurrentIndex(form._file_cb.combo.findData(target))
        self.assertEqual(form._get_src_path(),target)
        form._refresh_file_list()
        self.assertEqual(form._get_src_path(),target)
        (data/"方案.json").unlink()
        form._refresh_file_list()
        self.assertEqual(form._file_cb.currentIndex(),-1)

    def test_wide_bom_invalid_json_and_explicit_default_sizes(self):
        form,source=self.wide_form()
        for index,expected in ((0,(1920,1080)),(1,(1080,1920)),(2,(3200,1440))):
            form._ratio_cb.setCurrentIndex(index)
            form._on_ratio_changed(index)
            self.assertEqual(form._get_src_resolution(),expected)
        source.write_text("[]",encoding="utf-8")
        form._on_read()
        self.assertEqual(form._tbl.rowCount(),0)
        self.assertIn("失败",form.status_bar.currentMessage())

    def test_wide_clipping_confirm_cancel_and_invalid_target(self):
        form,_=self.wide_form(document(key(x=0,y=0)))
        form.set_resolution_getter(lambda:(720,1280))
        form._tbl.setCurrentCell(0,1)
        with mock.patch.object(QMessageBox,"question",return_value=QMessageBox.No): form._on_single_write()
        form._write_cb.assert_not_called()
        form.set_resolution_getter(lambda:(1,720))
        form._on_single_write()
        form._write_cb.assert_not_called()

    def test_mouse_edit_locked_until_explicit_doubleclick(self):
        form=self.mouse_form()
        event=QKeyEvent(QEvent.KeyPress,Qt.Key_A,Qt.NoModifier,"a")
        APP.sendEvent(form._save_key_edit,event)
        self.assertEqual(form._save_key_edit.text(),"D")
        APP.sendEvent(form._save_key_edit,QEvent(QEvent.MouseButtonDblClick))
        APP.sendEvent(form._save_key_edit,event)
        self.assertEqual(form._save_key_edit.text(),"A")
        self.assertIn("未保存",form._status_label.text())

    def test_mouse_conflict_enable_and_edit_keep_old_bindings(self):
        form=self.mouse_form()
        form._recall_key_edit.setText("d")
        form._enable_cb.setChecked(True)
        self.assertFalse(form._enable_cb.isChecked())
        self.assertEqual(form._hotkey_mgr.calls,[])
        self.assertEqual(form._recall_key_edit.text(),"F")
        form._enable_cb.setChecked(True)
        original=dict(form._hotkey_mgr.callbacks)
        form._save_key_edit.setText("F")
        form._on_key_captured()
        self.assertEqual(form._hotkey_mgr.callbacks,original)
        self.assertEqual(form._save_key_edit.text(),"D")

    def test_mouse_registration_failure_rolls_back_actual_bindings(self):
        form=self.mouse_form()
        form._enable_cb.setChecked(True)
        form._hotkey_mgr.fail.add("ctrl+a")
        form._save_key_edit.setText("A")
        form._on_key_captured()
        self.assertEqual(set(form._hotkey_mgr.callbacks),{"ctrl+d","ctrl+f"})
        self.assertEqual(form._save_key_edit.text(),"D")

    def test_mouse_integer_unsaved_guard_numeric_match_and_safe_close(self):
        form=self.mouse_form()
        form._enable_cb.setChecked(True)
        with mock.patch.object(mouse_module,"move_mouse_to") as move:
            form._do_recall_ui()
            move.assert_not_called()
            form._cur_x.setText("10.5"); form._do_save_ui()
            self.assertFalse(form._has_saved_coord)
            form._cur_x.setText("0010"); form._cur_y.setText("-20")
            form._do_save_ui()
            form._saved_x.setText("0010")
            with mock.patch.object(mouse_module,"get_cursor_pos",return_value=(10,-20)): form._poll_mouse()
            self.assertTrue(form._cur_x.styleSheet())
            form._saved_y.setText("oops"); form._do_recall_ui(); move.assert_not_called()
            form._saved_y.setText("-20"); form._do_recall_ui(); move.assert_called_once_with(10,-20)
            form.close()
            self.assertFalse(form._enable_cb.isChecked())
            self.assertFalse(form._timer.isActive())
            self.assertEqual(form._hotkey_mgr.callbacks,{})
            form._enable_cb.setChecked(True)
            self.assertTrue(form._timer.isActive())

    def test_mouse_config_failures_not_success_and_extended_names_valid(self):
        form=self.mouse_form()
        with mock.patch.object(form._config,"reload",return_value=False): form._on_recall_clicked()
        self.assertIn("失败",form._status_label.text())
        form._save_key_edit.setText("F24")
        with mock.patch.object(form._config,"save",return_value=False): form._on_save_clicked()
        self.assertIn("未保存",form._status_label.text())
        self.assertEqual(form._key_name("F13"),"f13")
        self.assertEqual(form._key_name("PageDown"),"page down")


if __name__=="__main__":
    unittest.main()
