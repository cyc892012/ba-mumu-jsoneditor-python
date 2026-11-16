"""MuMu 更新后的宏模板回归；只使用提取的 A 和隔离临时文件。"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from core import json_editor as editor
from core.coord_model import rel_to_px, px_to_rel

FIXTURE = Path(__file__).resolve().parent / "data" / "mumu_macro_a.json"


def sample():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def document(*keys):
    return json.dumps({"keymaps": list(keys), "custom_root": {"keep": True}}, ensure_ascii=False)


class MuMuMacroUpdateTests(unittest.TestCase):
    def test_fixture_is_only_the_verified_a_macro(self):
        value = sample()
        self.assertEqual(value["type"], "Macro")
        self.assertEqual(value["key"], {"device": "keyboard", "scan_code": 30,
                                        "text": "A", "virtual_key": 65})
        self.assertNotIn("keymaps", value)
        self.assertEqual(value["press_actions"][3], "curve_rel:mouse;(0.593200,0.297917)")

    def test_generated_script_matches_working_a_except_dynamic_coordinates(self):
        expected = sample()["press_actions"]
        actual = json.loads(editor.create_key("Macro", "B", "0.25", "0.75", "48", "66"))
        expected[3] = "curve_rel:mouse;(0.25,0.75)"
        self.assertEqual(actual["press_actions"], expected)
        self.assertEqual(actual["release_actions"], sample()["release_actions"])

    def test_generated_structure_and_identity_are_dynamic_for_multiple_vks(self):
        for text, vk, scan, x, y, icon_x, icon_y in (
            ("B", 66, 48, "0.25", "0.75", 0.28, 0.78),
            ("Return", 13, 28, "0.98", "0.99", 0.95, 0.96),
            ("D1", 49, 2, "0", "1", 0.03, 0.97),
        ):
            with self.subTest(vk=vk):
                actual = json.loads(editor.create_key("Macro", text, x, y, str(scan), str(vk)))
                expected = sample()
                expected["key"].update(text=text, virtual_key=vk, scan_code=scan)
                expected["press_actions"][3] = f"curve_rel:mouse;({float(x)},{float(y)})"
                expected["icon"]["rel_position"] = {"rel_x": icon_x, "rel_y": icon_y}
                expected["rel_work_position"] = {"rel_x": icon_x, "rel_y": icon_y}
                self.assertEqual(actual, expected)

    def test_generation_preserves_existing_float_precision(self):
        x, y = "0.1234567890123456", "0.9876543210987654"
        generated = editor.create_key("Macro", "F2", x, y, "60", "113")
        text = document(json.loads(generated))
        self.assertEqual(editor.read_key_by_virtual_key(text, 113), (x, y))
        self.assertNotIn("\r", generated)
        self.assertFalse(generated.endswith("\n"))

    def test_new_a_coordinate_readers_use_curve_instead_of_icon_position(self):
        text = document(sample())
        expected = ("0.593200", "0.297917")
        self.assertEqual(editor.read_key_by_virtual_key(text, 65), expected)
        self.assertEqual(editor.read_key_by_text(text, "A"), expected)
        self.assertEqual(editor.try_read_key_coordinates(text, vk=65), expected)
        self.assertEqual(editor.try_read_key_coordinates(text, key_text="A"), expected)

    def test_edit_new_a_changes_only_curve_coordinates_with_full_input_precision(self):
        x, y = "0.12345678901234567890", "0.98765432109876543210"
        original = document(sample())
        expected = json.loads(original)
        expected["keymaps"][0]["press_actions"][3] = f"curve_rel:mouse;({x},{y})"
        for edit, identity in ((editor.re_key_by_virtual_key, 65), (editor.re_key_by_text, "A")):
            with self.subTest(edit=edit.__name__):
                actual = edit(original, identity, x, y)
                self.assertEqual(json.loads(actual), expected)
                self.assertEqual(editor.try_read_key_coordinates(actual, vk=65), (x, y))

    def test_custom_macro_keeps_legacy_release_other_actions_and_extension_fields(self):
        custom = sample()
        custom["press_actions"] = ["key_down:Q", "sleep:25", "curve_rel:mouse;(0.1,0.2)",
                                   "curve_release", "curve_rel:mouse;(0.8,0.9)", "key_up:Q"]
        custom["release_actions"] = ["key_up:Q", "sleep:7"]
        custom["vendor_extension"] = {"value": [1, "自定义", {"keep": True}]}
        original = document(custom, sample())
        expected = json.loads(original)
        expected["keymaps"][0]["press_actions"][2] = "curve_rel:mouse;(0.333333333333333333,0.666666666666666667)"
        for edit, identity in ((editor.re_key_by_virtual_key, 65), (editor.re_key_by_text, "A")):
            with self.subTest(edit=edit.__name__):
                actual = edit(original, identity, "0.333333333333333333", "0.666666666666666667")
                self.assertEqual(json.loads(actual), expected)

    def test_coordinate_edit_preserves_remainder_of_existing_curve_action(self):
        custom = sample()
        custom["press_actions"][3] = "curve_rel:mouse;(0.1,0.2);(0.7000000000000001,0.8)"
        original = document(custom)
        expected = json.loads(original)
        expected["keymaps"][0]["press_actions"][3] = "curve_rel:mouse;(0.3,0.4);(0.7000000000000001,0.8)"
        for edit, identity in ((editor.re_key_by_virtual_key, 65), (editor.re_key_by_text, "A")):
            with self.subTest(edit=edit.__name__):
                self.assertEqual(json.loads(edit(original, identity, "0.3", "0.4")), expected)

    def test_unreadable_macro_is_not_rebuilt_or_given_zero_coordinates(self):
        for actions in ([], ["key_down:Q", "sleep:25", "key_up:Q"]):
            custom = sample()
            custom["press_actions"] = actions
            original = document(custom)
            self.assertIsNone(editor.try_read_key_coordinates(original, vk=65))
            self.assertIsNone(editor.re_key_by_virtual_key(original, 65, "0.3", "0.4"))
            self.assertIsNone(editor.re_key_by_text(original, "A", "0.3", "0.4"))
            self.assertEqual(json.loads(original)["keymaps"][0], custom)

    def test_nonfinite_coordinates_are_rejected_for_creation_and_editing(self):
        original = document(sample())
        for value in ("nan", "inf", "-inf", "1e9999"):
            for x, y in ((value, "0.5"), ("0.5", value)):
                with self.subTest(x=x, y=y):
                    self.assertIsNone(editor.create_key("Macro", "A", x, y, "30", "65"))
                    self.assertIsNone(editor.re_key_by_virtual_key(original, 65, x, y))
                    self.assertIsNone(editor.re_key_by_text(original, "A", x, y))

    def test_click_and_bunchclick_keep_their_original_fields_and_edit_behavior(self):
        for kind in ("Click", "BunchClick"):
            with self.subTest(kind=kind):
                generated = json.loads(editor.create_key(kind, "C", "0.125", "0.875", "46", "67"))
                expected = {"editor_icon_scale": 1,
                            "icon": {"background_color": "00000066", "description": "", "radius_correction": 1,
                                     "rel_position": {"rel_x": 0.125, "rel_y": 0.875}, "visibility": True},
                            "key": {"device": "keyboard", "scan_code": 46, "text": "C", "virtual_key": 67},
                            "rel_work_position": {"rel_x": 0.125, "rel_y": 0.875}, "type": kind}
                self.assertEqual(generated, expected)
                for edit, identity in ((editor.re_key_by_virtual_key, 67), (editor.re_key_by_text, "C")):
                    actual = json.loads(edit(document(generated), identity, "0.1234567890123456", "0.75"))
                    updated = copy.deepcopy(expected)
                    for pos in (updated["icon"]["rel_position"], updated["rel_work_position"]):
                        pos.update(rel_x=0.1234567890123456, rel_y=0.75)
                    self.assertEqual(actual["keymaps"][0], updated)

    def test_merging_new_macros_preserves_unrelated_user_macro(self):
        custom = sample()
        custom["press_actions"] = ["key_down:Q", "curve_release", "key_up:Q"]
        blocks = [json.loads(editor.create_key("Macro", text, x, y, scan, vk))
                  for text, x, y, scan, vk in (("B", "0.2", "0.3", "48", "66"),
                                             ("C", "0.7", "0.8", "46", "67"))]
        actual = json.loads(editor.write_keys(json.dumps(blocks), document(custom)))
        self.assertEqual(actual["keymaps"], [custom, *blocks])
        self.assertEqual(actual["custom_root"], {"keep": True})
        self.assertEqual(editor.read_key_by_virtual_key(json.dumps(actual), 66), ("0.2", "0.3"))
        self.assertEqual(editor.read_key_by_virtual_key(json.dumps(actual), 67), ("0.7", "0.8"))

    def test_atomic_failure_keeps_original_macro_bytes_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory(prefix="macro_atomic_") as folder:
            target = Path(folder) / "scheme.json"
            before = document(sample()).encode("utf-8")
            target.write_bytes(before)
            candidate = editor.re_key_by_virtual_key(before.decode("utf-8"), 65, "0.3", "0.4")
            with mock.patch.object(editor.os, "replace", side_effect=PermissionError("模拟占用")):
                self.assertFalse(editor.write_text_atomic(str(target), candidate))
            self.assertEqual(target.read_bytes(), before)
            self.assertFalse(Path(str(target) + ".tmp").exists())
            self.assertIn("模拟占用", editor.get_last_write_error())

    def test_preset_read_by_real_vk_preserves_the_complete_new_a_block(self):
        with tempfile.TemporaryDirectory(prefix="macro_preset_") as folder:
            target = Path(folder) / "preset.json"
            target.write_text(document(sample()), encoding="utf-8-sig")
            self.assertEqual(json.loads(editor.read_key_from_file_by_virtual_key(str(target), 65)), sample())
            self.assertIsNone(editor.read_key_from_file_by_virtual_key(str(target), 66))

    def test_wide_screen_separate_reader_and_converter_accept_new_a(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from ui.wide_screen_form import WideScreenForm
        with tempfile.TemporaryDirectory(prefix="macro_wide_") as folder:
            target = Path(folder) / "source.json"
            target.write_text(document(sample()), encoding="utf-8")
            form = SimpleNamespace(_get_src_path=lambda: str(target), _get_src_resolution=lambda: (1280, 720),
                                   _invalidate_source=mock.Mock(), status_bar=mock.Mock(),
                                   _tbl=mock.Mock(), _on_check_changed=mock.Mock())
            WideScreenForm._on_read(form)
            self.assertEqual(len(form._click_keys), 1)
            info = form._click_keys[0]
            self.assertAlmostEqual(info["rel_x"], rel_to_px(0.5932, 1280), places=12)
            self.assertAlmostEqual(info["rel_y"], rel_to_px(0.297917, 720), places=12)
            converted, status = WideScreenForm._convert_info(form, info, (1280, 720))
            self.assertEqual(status, 0)
            actual = json.loads(converted)
            # 同分辨率转换也输出精确格点，其他字段仍逐项全等。
            coords = editor.try_read_key_coordinates(document(actual), vk=65)
            self.assertAlmostEqual(float(coords[0]), px_to_rel(info["rel_x"], 1280), places=15)
            self.assertAlmostEqual(float(coords[1]), px_to_rel(info["rel_y"], 720), places=15)
            expected = sample()
            expected["press_actions"][3] = actual["press_actions"][3]
            self.assertEqual(actual, expected)
            self.assertEqual(info["key_obj"], sample())


if __name__ == "__main__":
    unittest.main()
