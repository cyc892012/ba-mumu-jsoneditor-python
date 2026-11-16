"""ADB 重复生成比较实际点击坐标，而非可独立移动的键帽位置。"""
import json
import unittest

from core.json_editor import create_key, TYPE_CLICK, TYPE_MACRO, TYPE_BUNCH_CLICK
from ui.adb_touch_form import AdbTouchForm


class AdbDuplicateCoordinateTests(unittest.TestCase):
    def is_duplicate(self, block, key_type, x=.2, y=.4):
        return AdbTouchForm._is_exact_duplicate(None, json.dumps({"keymaps": [block]}), 65, key_type, x, y)

    def block(self, key_type):
        return json.loads(create_key(key_type, "A", ".2", ".4", "30", "65"))

    def test_macro_duplicate_uses_action_coordinate_despite_icon_offset(self):
        block = self.block(TYPE_MACRO)
        self.assertTrue(self.is_duplicate(block, TYPE_MACRO))
        self.assertFalse(self.is_duplicate(block, TYPE_MACRO, .23, .43))

    def test_click_with_matching_icon_but_different_action_is_not_duplicate(self):
        for key_type in (TYPE_CLICK, TYPE_BUNCH_CLICK):
            block = self.block(key_type)
            block["rel_work_position"]["rel_x"] = .8
            self.assertFalse(self.is_duplicate(block, key_type))

    def test_working_click_is_duplicate_even_with_moved_icon(self):
        block = self.block(TYPE_CLICK)
        block["icon"]["rel_position"]["rel_x"] = .8
        self.assertTrue(self.is_duplicate(block, TYPE_CLICK))

    def test_type_or_vk_mismatch_and_invalid_coordinate_are_not_duplicates(self):
        block = self.block(TYPE_CLICK)
        self.assertFalse(self.is_duplicate(block, TYPE_MACRO))
        block["key"]["virtual_key"] = 66
        self.assertFalse(self.is_duplicate(block, TYPE_CLICK))
        block["key"]["virtual_key"] = 65
        block["rel_work_position"]["rel_x"] = float("nan")
        self.assertFalse(self.is_duplicate(block, TYPE_CLICK))
