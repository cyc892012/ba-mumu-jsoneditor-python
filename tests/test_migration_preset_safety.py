"""预设组功能回归；只实例化离屏子窗，文件写入使用测试临时目录。"""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtTest import QSignalSpy
from PyQt5.QtWidgets import QApplication, QPushButton
from ui import key_preset_form as preset

APP = QApplication.instance() or QApplication([])


def block(text="A", vk=65, x=0.3, y=0.4, kind="Click"):
    value = {"type": kind, "key": {"text": text, "virtual_key": vk},
             "icon": {"rel_position": {"rel_x": x, "rel_y": y}},
             "rel_work_position": {"rel_x": x, "rel_y": y}}
    if kind == "Macro":
        value["press_actions"] = [f"curve_rel:mouse;({x},{y})", "key_down:A", "delay:25"]
    return value


def scheme(*keys):
    return json.dumps({"keymaps": list(keys), "metadata": {"保留": True}}, ensure_ascii=False)


class MigrationPresetSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="migration_preset_")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        patch = mock.patch.dict(os.environ, {"MOMO_DATA_DIR": str(self.root)})
        patch.start()
        self.addCleanup(patch.stop)
        self.form = preset.KeyPresetForm()
        self.form.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.addCleanup(self.form.deleteLater)
        self.addCleanup(self.form.close)
        self.sync = mock.Mock(return_value=True)
        self.form.set_sync_callback(self.sync)
        self.form.set_json_data(scheme())
        self.question_patch = mock.patch.object(preset.MessageDialog, "question", return_value=True)
        self.question = self.question_patch.start()
        self.addCleanup(self.question_patch.stop)
        self.file_patch = mock.patch.object(preset.QFileDialog, "getOpenFileName", return_value=("", ""))
        self.files = self.file_patch.start()
        self.addCleanup(self.file_patch.stop)

    def save_preset(self, text, name="基础.json", bom=False):
        folder = self.root / "data"
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_text(text, encoding="utf-8-sig" if bom else "utf-8")
        self.form._refresh_preset_list()
        self.form._preset_cb.setCurrentText(name)
        return path

    def status(self):
        return self.form.status_bar.currentMessage()

    def test_layout_and_native_dialogs_are_not_replaced(self):
        self.assertEqual((self.form.width(), self.form.height()), (390, 300))
        labels = [button.text() for button in self.form.findChildren(QPushButton)]
        self.assertEqual(labels.count("删除"), 1)
        self.assertIn("MessageDialog", preset.__dict__)

    def test_batch_uses_vk_and_preserves_macro_and_unknown_fields(self):
        macro = block("Return", 13, kind="Macro")
        macro["extra"] = {"custom": [1, 2, 3]}
        self.save_preset(scheme(block("A", 66), macro, block("", 112)))
        self.form.set_json_data(scheme(block("A", 65)))
        self.form._inject_all_keys()
        keys = json.loads(self.form._json_str)["keymaps"]
        self.assertEqual([key["key"]["virtual_key"] for key in keys], [65, 66, 13, 112])
        self.assertEqual(keys[2], macro)
        self.sync.assert_called_once_with(self.form._json_str)

    def test_batch_same_vk_alias_is_held_for_supervisor_confirmation(self):
        self.save_preset(scheme(block("Return", 13)))
        original = scheme(block("Enter", 13))
        self.form.set_json_data(original)
        self.form._inject_all_keys()
        self.assertEqual(self.form._json_str, original)
        self.sync.assert_not_called()
        self.assertIn("无变化", self.status())

    def test_batch_false_has_one_attempt_and_no_cache_commit(self):
        self.save_preset(scheme(block("A", 65), block("B", 66)))
        before = self.form._json_str
        self.sync.return_value = False
        with mock.patch.object(preset, "write_keys", wraps=preset.write_keys) as build:
            self.form._inject_all_keys()
        self.assertEqual(build.call_count, 1)
        self.sync.assert_called_once()
        self.assertEqual(self.form._json_str, before)
        self.assertIn("写回失败", self.status())

    def test_batch_exception_does_not_report_success(self):
        self.save_preset(scheme(block()))
        before = self.form._json_str
        self.sync.side_effect = OSError("模拟写盘占用")
        self.form._inject_all_keys()
        self.assertEqual(self.form._json_str, before)
        self.assertIn("模拟写盘占用", self.status())
        self.assertNotIn("注入 1", self.status())

    def test_batch_bom_and_malformed_json_are_distinguished(self):
        path = self.save_preset(scheme(block()), bom=True)
        self.form._inject_all_keys()
        self.assertEqual(len(json.loads(self.form._json_str)["keymaps"]), 1)
        self.sync.reset_mock()
        path.write_text("[]", encoding="utf-8")
        self.form._inject_all_keys()
        self.sync.assert_not_called()
        self.assertIn("keymaps", self.status())

    def test_single_uses_native_vk_for_cs_aliases(self):
        for alias, captured, vk in (("D1", "1", 49), ("Return", "Enter", 13), ("Oemcomma", ",", 188)):
            with self.subTest(alias=alias):
                original = block(alias, vk, kind="Macro")
                self.save_preset(scheme(original), bom=True)
                self.form.set_json_data(scheme())
                self.form._on_key_captured(captured, vk)
                self.sync.reset_mock()
                self.form._inject_single_key()
                self.assertEqual(json.loads(self.form._json_str)["keymaps"], [original])
                self.sync.assert_called_once()

    def test_single_vk_zero_uses_text_not_first_zero_key(self):
        self.save_preset(scheme(block("wrong", 0), block("A", 65)))
        self.form._on_key_captured("A", 0)
        self.form._inject_single_key()
        self.assertEqual(json.loads(self.form._json_str)["keymaps"][0]["key"], {"text": "A", "virtual_key": 65})

    def test_single_same_text_different_vk_can_be_injected(self):
        self.save_preset(scheme(block("A", 66)))
        self.form.set_json_data(scheme(block("A", 65)))
        self.form._on_key_captured("A", 66)
        self.form._inject_single_key()
        self.assertEqual(len(json.loads(self.form._json_str)["keymaps"]), 2)

    def test_single_missing_does_not_commit(self):
        self.save_preset(scheme(block()))
        self.form._on_key_captured("B", 66)
        self.form._inject_single_key()
        self.sync.assert_not_called()
        self.assertIn("未找到", self.status())

    def test_all_mutations_false_and_exception_preserve_cache(self):
        for method in ("_inject_single_key", "_dedup_against_preset", "_clear_range_rd"):
            for error in (False, OSError("模拟拒绝保存")):
                with self.subTest(method=method, error=str(error)):
                    self.save_preset(scheme(block("A", 65), block("B", 66)))
                    before = scheme(block("B", 66, 0.8, 0.9))
                    self.form.set_json_data(before)
                    self.form._on_key_captured("A", 65)
                    self.sync.reset_mock()
                    self.sync.return_value = error if error is False else True
                    self.sync.side_effect = error if isinstance(error, Exception) else None
                    getattr(self.form, method)()
                    self.assertEqual(self.form._json_str, before)
                    self.sync.assert_called_once()
                    self.assertIn("写回失败", self.status())

    def test_no_callback_does_not_claim_saved(self):
        self.save_preset(scheme(block()))
        before = self.form._json_str
        self.form.set_sync_callback(None)
        self.form._inject_all_keys()
        self.assertEqual(self.form._json_str, before)
        self.assertIn("未设置保存回调", self.status())

    def test_legacy_none_callback_is_compatible(self):
        self.save_preset(scheme(block()))
        self.sync.return_value = None
        self.form._inject_all_keys()
        self.assertEqual(len(json.loads(self.form._json_str)["keymaps"]), 1)

    def test_dedup_counts_real_array_entries_and_requires_confirmation(self):
        self.save_preset(scheme(block("Return", 13)))
        self.form.set_json_data(scheme(block("Enter", 13), block("Alias", 13), block("A", 65)))
        self.form._dedup_against_preset()
        self.assertEqual([key["key"]["virtual_key"] for key in json.loads(self.form._json_str)["keymaps"]], [65])
        self.assertIn("2 个", self.question.call_args.args[2])
        self.assertTrue(self.question.call_args.kwargs["danger"])
        self.assertIn("删除 2", self.status())

    def test_dedup_cancel_and_no_change_do_not_write(self):
        self.save_preset(scheme(block()))
        original = scheme(block())
        self.form.set_json_data(original)
        self.question.return_value = False
        self.form._dedup_against_preset()
        self.assertEqual(self.form._json_str, original)
        self.sync.assert_not_called()
        self.question.reset_mock()
        self.form.set_json_data(scheme(block("B", 66)))
        self.form._dedup_against_preset()
        self.question.assert_not_called()
        self.sync.assert_not_called()

    def test_dedup_does_not_treat_vk_zero_as_shared_identity(self):
        self.save_preset(scheme(block("A", 0)))
        self.form.set_json_data(scheme(block("B", 0)))
        self.form._dedup_against_preset()
        self.sync.assert_not_called()
        self.assertIn("无重复", self.status())

    def test_clear_range_deletes_indexes_including_zero_only_inside(self):
        outside = [block("A", 65, 0.2, 0.2), block("outside0", 0, 0.2, 0.2)]
        inside = [block("A", 65, 0.8, 0.9), block("inside0", 0, 0.9, 0.9)]
        self.form.set_json_data(scheme(outside[0], inside[0], outside[1], inside[1]))
        self.form._clear_range_rd()
        self.assertEqual(json.loads(self.form._json_str)["keymaps"], outside)
        self.assertIn("2 个", self.question.call_args.args[2])
        self.assertIn("16:9", self.question.call_args.args[2])
        self.assertTrue(self.question.call_args.kwargs["danger"])
        self.sync.assert_called_once()

    def test_clear_range_cancel_does_not_need_preset_library(self):
        original = scheme(block("A", 65, 0.8, 0.9))
        self.form.set_json_data(original)
        self.question.return_value = False
        self.form._clear_range_rd()
        self.assertEqual(self.form._json_str, original)
        self.sync.assert_not_called()
        self.question.assert_called_once()
        self.assertFalse((self.root / "data").exists())

    def test_clear_empty_and_invalid_are_different(self):
        for text in ("bad json", "[]", '{"keymaps":null}', scheme(block(x=float("nan")))):
            with self.subTest(text=text):
                self.form.set_json_data(text)
                self.form._clear_range_rd()
                self.assertIn("读取按键失败", self.status())
        self.form.set_json_data(scheme())
        self.form._clear_range_rd()
        self.assertIn("该区域无按键", self.status())
        self.question.assert_not_called()
        self.sync.assert_not_called()

    def test_clear_keeps_original_open_interval_edges(self):
        keys = [block("left", 65, 0.661, 0.9), block("top", 66, 0.8, 0.798),
                block("right", 67, 1.0, 0.9), block("bottom", 68, 0.8, 1.0)]
        before = scheme(*keys)
        self.form.set_json_data(before)
        self.form._clear_range_rd()
        self.assertEqual(self.form._json_str, before)
        self.sync.assert_not_called()

    def test_danger_default_cancel_at_theme_call_boundary(self):
        self.assertEqual(APP.platformName(), "offscreen")
        before = scheme(block(x=0.8, y=0.9))
        self.form.set_json_data(before)
        observed = []

        def cancel_default(parent, title, message, *, confirm, cancel, danger, default_cancel=True):
            observed.append(default_cancel)
            self.assertTrue(danger)
            self.assertEqual(cancel, "取消")
            return False

        self.question.side_effect = cancel_default
        self.form._clear_range_rd()
        self.assertEqual(observed, [True])
        self.assertEqual(self.form._json_str, before)
        self.sync.assert_not_called()

    def test_import_cancel_keeps_existing_bytes_and_selection(self):
        path = self.save_preset(scheme(block("old", 65)), name="方案.json", bom=True)
        before = path.read_bytes()
        self.form.set_json_path(str(self.root / "方案.json"))
        self.form.set_json_data(scheme(block("new", 66)))
        self.question.return_value = False
        self.form._import_to_preset()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.form._preset_cb.currentText(), "方案.json")
        self.assertTrue(self.question.call_args.kwargs["danger"])

    def test_import_approved_overwrite_and_atomic_failure(self):
        path = self.save_preset(scheme(block("old", 65)), name="方案.json")
        self.form.set_json_path(str(self.root / "方案.json"))
        updated = scheme(block("new", 66))
        self.form.set_json_data(updated)
        self.form._import_to_preset()
        self.assertEqual(path.read_text(encoding="utf-8"), updated)
        self.form.set_json_data(scheme())
        with mock.patch.object(preset, "write_text_atomic", return_value=False):
            self.form._import_to_preset()
        self.assertEqual(path.read_text(encoding="utf-8"), updated)
        self.assertIn("导入失败", self.status())

    def test_import_rejects_bad_json_and_directory_error(self):
        self.form.set_json_path(str(self.root / "方案.json"))
        for text in ("[]", '{"keymaps":null}', "broken"):
            self.form.set_json_data(text)
            self.form._import_to_preset()
            self.assertIn("导入失败", self.status())
            self.assertFalse((self.root / "data").exists())
        self.form.set_json_data(scheme())
        with mock.patch.object(preset.os, "makedirs", side_effect=PermissionError("目录只读")):
            self.form._import_to_preset()
        self.assertIn("目录只读", self.status())

    def test_open_import_cancel_preserves_existing_bytes(self):
        target = self.save_preset(scheme(block("old", 65)), name="方案.json")
        source = self.root / "方案.json"
        source.write_text(scheme(block("new", 66)), encoding="utf-8-sig")
        before = target.read_bytes()
        self.files.return_value = (str(source), "")
        self.question.return_value = False
        self.form._on_open_preset()
        self.assertEqual(target.read_bytes(), before)
        self.assertTrue(self.question.call_args.kwargs["danger"])

    def test_open_import_preserves_bom_and_line_endings(self):
        source = self.root / "方案.json"
        raw = ("\ufeff" + json.dumps({"keymaps": [block()]}, indent=2).replace("\n", "\r\n")).encode("utf-8")
        source.write_bytes(raw)
        self.files.return_value = (str(source), "")
        self.form._on_open_preset()
        self.assertEqual((self.root / "data" / source.name).read_bytes(), raw)
        self.assertEqual(self.form._preset_cb.currentText(), source.name)

    def test_open_invalid_file_reports_error_without_import(self):
        source = self.root / "bad.json"
        source.write_bytes(b"\xff\xfeinvalid")
        self.files.return_value = (str(source), "")
        self.form._on_open_preset()
        self.assertFalse((self.root / "data" / source.name).exists())
        self.assertIn("失败", self.status())

    def test_open_same_file_does_not_overwrite(self):
        source = self.save_preset(scheme(block()), bom=True)
        before = source.read_bytes()
        self.files.return_value = (str(source), "")
        self.form._on_open_preset()
        self.assertEqual(source.read_bytes(), before)
        self.question.assert_not_called()

    def test_open_folder_uses_local_url_and_reports_failure(self):
        with mock.patch.object(preset.QDesktopServices, "openUrl", return_value=False) as launch:
            self.form._on_open_presets_directory()
        self.assertEqual(os.path.normpath(launch.call_args.args[0].toLocalFile()), os.path.normpath(self.form._presets_dir))
        self.assertIn("打开预设目录失败", self.status())
        button = next(button for button in self.form.findChildren(QPushButton) if button.text() == "打开")
        self.assertEqual([action.text() for action in button.menu().actions()], ["选择并导入预设文件", "打开预设文件夹"])

    def test_read_alias_bom_and_precision(self):
        original = block("Return", 13, kind="Macro")
        original["press_actions"][0] = "curve_rel:mouse;(0.1234567890123456,0.9876543210987654)"
        self.save_preset(scheme(original), bom=True)
        callback = mock.Mock()
        self.form.set_apply_coord_callback(callback)
        self.form._on_key_captured("Enter", 13)
        self.form._on_read_from_preset()
        callback.assert_called_once_with("0.1234567890123456", "0.9876543210987654")
        self.assertIn("VK: 13", self.status())

    def test_read_missing_coordinates_does_not_apply_zero(self):
        callback = mock.Mock()
        self.form.set_apply_coord_callback(callback)
        self.form._on_key_captured("A", 65)
        for original in ({"type": "Click", "key": {"text": "A", "virtual_key": 65}},
                         {"type": "Macro", "key": {"text": "A", "virtual_key": 65}, "press_actions": []}):
            self.save_preset(scheme(original))
            self.form._on_read_from_preset()
            self.assertIn("可读坐标", self.status())
        callback.assert_not_called()

    def test_read_legitimate_zero_and_text_fallback(self):
        self.save_preset(scheme(block("A", 65, 0, 0), block("wrong", 0, 0.9, 0.9)))
        self.form._on_key_captured("A", 0)
        callback = mock.Mock()
        self.form.set_apply_coord_callback(callback)
        self.form._on_read_from_preset()
        callback.assert_called_once_with("0", "0")

    def test_read_callback_error_and_missing_callback_report_failure(self):
        self.save_preset(scheme(block()))
        self.form._on_key_captured("A", 65)
        self.form._on_read_from_preset()
        self.assertIn("未设置坐标回填回调", self.status())
        self.form.set_apply_coord_callback(mock.Mock(side_effect=ValueError("回填拒绝")))
        self.form._on_read_from_preset()
        self.assertIn("回填拒绝", self.status())

    def test_escape_is_a_bindable_key(self):
        self.form._preset_read_ie.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier, 0, 27, 0, "", False, 1))
        self.assertEqual((self.form._captured_key, self.form._captured_vk), ("Esc", 27))
        self.assertEqual(self.form._preset_read_ie.text(), "Esc")

    def test_refresh_preserves_selection_and_blocks_intermediate_signals(self):
        self.save_preset(scheme(), "A.json")
        self.save_preset(scheme(), "B.json")
        spy = QSignalSpy(self.form._preset_cb.currentTextChanged)
        self.form._refresh_preset_list()
        self.assertEqual(self.form._preset_cb.currentText(), "B.json")
        self.assertEqual(len(spy), 0)
        self.form._preset_cb.blockSignals(True)
        self.form._refresh_preset_list()
        self.assertTrue(self.form._preset_cb.combo.signalsBlocked())

    def test_popup_signal_rescans_external_files_preserving_selection(self):
        self.save_preset(scheme(), "B.json")
        (self.root / "data" / "A.json").write_text(scheme(), encoding="utf-8")
        self.form._preset_cb.popupAboutToShow.emit()
        self.assertEqual(self.form._preset_cb.count(), 2)
        self.assertEqual(self.form._preset_cb.currentText(), "B.json")

    def test_refresh_directory_error_keeps_current_items(self):
        self.save_preset(scheme(), "B.json")
        with mock.patch.object(preset.os, "listdir", side_effect=PermissionError("扫描拒绝")):
            self.form._refresh_preset_list()
        self.assertEqual(self.form._preset_cb.currentText(), "B.json")
        self.assertIn("扫描拒绝", self.status())

    def test_form_renders_hidden_at_original_size_and_font(self):
        self.assertEqual(APP.platformName(), "offscreen")
        self.form.ensurePolished()
        self.form.layout().activate()
        pixmap = self.form.grab()
        self.assertFalse(pixmap.isNull())
        self.assertEqual((pixmap.width(), pixmap.height()), (390, 300))
        self.assertEqual(APP.font().pointSize(), 9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
