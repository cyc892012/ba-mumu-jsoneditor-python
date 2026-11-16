"""宽屏移植保留宏中各独立动作的位置，复杂曲线不得被静默截断。"""
import json
from unittest import TestCase

from core.json_editor import create_key, TYPE_MACRO
import test_migration_coordinate_forms as coordinate_tests


class WideMacroIntegrityTests(TestCase):
    setUp = coordinate_tests.CoordinateFormsTest.setUp
    tearDown = coordinate_tests.CoordinateFormsTest.tearDown
    wide_form = coordinate_tests.CoordinateFormsTest.wide_form

    def test_each_macro_action_keeps_its_own_coordinate(self):
        block = json.loads(create_key(TYPE_MACRO, "A", ".25", ".25", "30", "65"))
        block["press_actions"] = ["curve_rel:mouse;(0.25,0.25)", "sleep:5", "curve_rel:mouse;(0.75,0.75)"]
        form, _ = self.wide_form(json.dumps({"keymaps": [block]}))
        form._tbl.setCurrentCell(0, 1)
        form._on_single_write()
        actions = json.loads(form._json_str)["keymaps"][0]["press_actions"]
        self.assertEqual(actions, block["press_actions"])

    def test_complex_later_curve_is_rejected_without_writing(self):
        block = json.loads(create_key(TYPE_MACRO, "A", ".25", ".25", "30", "65"))
        block["press_actions"].append("curve_rel:mouse;(0.75,0.75);(0.8,0.9);tail")
        form, _ = self.wide_form(json.dumps({"keymaps": [block]}))
        before = form._json_str
        form._tbl.setCurrentCell(0, 1)
        form._on_single_write()
        form._write_cb.assert_not_called()
        self.assertEqual(form._json_str, before)
        self.assertIn("复杂曲线", form.status_bar.currentMessage())
