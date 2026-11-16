"""下拉框/勾选框工具回归测试 — 钉住「信号必须屏蔽」这个易漏点。

背景：重建下拉框时要 ``blockSignals`` 包住整个过程，否则 addItem/clear 会触发
``currentIndexChanged``，把「程序在填数据」误当成「用户切换了选项」，
连锁触发刷新甚至写盘。这个动作原先在项目里被手写了 12 处，漏一处就出怪 bug。

运行：<解释器> -m unittest discover -s tests -v
"""
import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from PyQt5.QtWidgets import QApplication, QCheckBox, QComboBox
    _QT_ERROR = None
except ImportError as exc:
    _QT_ERROR = exc

if _QT_ERROR is None:
    _app = QApplication.instance() or QApplication([])

from controls.combo_box_utils import (  # noqa: E402
    KEY_TYPE_DISPLAY_NAMES, KEY_TYPE_ITEMS, key_type_display_name,
    key_type_value, rebuild_combo, set_checked_silently,
)
from core.json_editor import TYPE_CLICK, TYPE_MACRO  # noqa: E402


class Recorder:
    """记录下拉框发出的信号次数"""

    def __init__(self, combo):
        self.count = 0
        combo.currentIndexChanged.connect(self._bump)

    def _bump(self, *args):
        self.count += 1


@unittest.skipIf(_QT_ERROR is not None, f"PyQt5 不可用: {_QT_ERROR}")
class TestRebuildCombo(unittest.TestCase):

    def test_builds_text_only_items(self):
        combo = QComboBox()
        rebuild_combo(combo, ["a", "b", "c"])
        self.assertEqual(combo.count(), 3)
        self.assertEqual(combo.itemText(1), "b")
        self.assertIsNone(combo.itemData(1))

    def test_builds_text_and_data_items(self):
        combo = QComboBox()
        rebuild_combo(combo, [("甲", "A"), ("乙", "B")])
        self.assertEqual(combo.count(), 2)
        self.assertEqual(combo.itemData(1), "B")

    def test_signals_are_blocked_during_rebuild(self):
        """★核心契约：重建过程不得发出 currentIndexChanged"""
        combo = QComboBox()
        rebuild_combo(combo, ["x", "y"])
        rec = Recorder(combo)
        rebuild_combo(combo, ["1", "2", "3"])
        self.assertEqual(rec.count, 0, "重建期间不应触发 currentIndexChanged")

    def test_restore_data_selects_matching_item(self):
        combo = QComboBox()
        rebuild_combo(combo, [("甲", "A"), ("乙", "B"), ("丙", "C")],
                      restore_data="B")
        self.assertEqual(combo.currentIndex(), 1)
        self.assertEqual(combo.currentData(), "B")

    def test_restore_data_not_found_keeps_first(self):
        combo = QComboBox()
        rebuild_combo(combo, [("甲", "A"), ("乙", "B")], restore_data="ZZZ")
        self.assertEqual(combo.currentIndex(), 0, "找不到时保持第一项，不额外处理")

    def test_restore_data_none_does_not_restore(self):
        """wide_screen 的历史行为：不恢复选中项"""
        combo = QComboBox()
        rebuild_combo(combo, [("甲", "A"), ("乙", "B")])
        combo.setCurrentIndex(1)
        rebuild_combo(combo, [("甲", "A"), ("乙", "B")])
        self.assertEqual(combo.currentIndex(), 0)

    def test_restore_text_selects_matching_item(self):
        combo = QComboBox()
        rebuild_combo(combo, ["1280×720", "1600×900"], restore_text="1600×900")
        self.assertEqual(combo.currentText(), "1600×900")

    def test_restore_text_not_found_on_editable_combo_sets_text(self):
        combo = QComboBox()
        combo.setEditable(True)
        rebuild_combo(combo, ["1280×720"], restore_text="1920×1080")
        self.assertEqual(combo.currentText(), "1920×1080")

    def test_restore_text_not_found_on_readonly_combo_is_noop(self):
        combo = QComboBox()
        combo.setEditable(False)
        rebuild_combo(combo, ["1280×720"], restore_text="1920×1080")
        self.assertEqual(combo.currentText(), "1280×720")

    def test_empty_items(self):
        combo = QComboBox()
        rebuild_combo(combo, [])
        self.assertEqual(combo.count(), 0)

    def test_rebuild_is_idempotent(self):
        combo = QComboBox()
        items = [("甲", "A"), ("乙", "B")]
        rebuild_combo(combo, items)
        first = [combo.itemText(i) for i in range(combo.count())]
        rebuild_combo(combo, items)
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], first)


@unittest.skipIf(_QT_ERROR is not None, f"PyQt5 不可用: {_QT_ERROR}")
class TestSetCheckedSilently(unittest.TestCase):

    def test_does_not_emit_toggled(self):
        cb = QCheckBox()
        cb.setChecked(True)
        hits = []
        cb.toggled.connect(hits.append)
        set_checked_silently(cb, False)
        self.assertFalse(cb.isChecked())
        self.assertEqual(hits, [], "不应触发 toggled")

    def test_manual_set_still_emits(self):
        """对照：普通 setChecked 是会发信号的（所以互斥勾选必须屏蔽）"""
        cb = QCheckBox()
        hits = []
        cb.toggled.connect(hits.append)
        cb.setChecked(True)
        self.assertEqual(hits, [True])


class TestKeyTypeMapping(unittest.TestCase):

    def test_items_match_csharp_predefined_key_types(self):
        """与 C# ComboBoxInitializer.PredefinedKeyTypes 一致"""
        self.assertEqual(KEY_TYPE_ITEMS,
                         (("点击按键", TYPE_CLICK), ("宏指牌按键", TYPE_MACRO)))

    def test_display_names_order(self):
        self.assertEqual(list(KEY_TYPE_DISPLAY_NAMES), ["点击按键", "宏指牌按键"])

    def test_display_name_from_value(self):
        self.assertEqual(key_type_display_name(TYPE_MACRO), "宏指牌按键")
        self.assertEqual(key_type_display_name(TYPE_CLICK), "点击按键")

    def test_unknown_value_falls_back_to_click(self):
        """对应 C# 的 `keyType == Macro ? ... : "点击按键"` else 分支"""
        self.assertEqual(key_type_display_name("BunchClick"), "点击按键")
        self.assertEqual(key_type_display_name(""), "点击按键")

    def test_value_from_display_name(self):
        self.assertEqual(key_type_value("宏指牌按键"), TYPE_MACRO)
        self.assertEqual(key_type_value("点击按键"), TYPE_CLICK)
        self.assertEqual(key_type_value("不存在"), TYPE_CLICK)

    def test_round_trip(self):
        for name, value in KEY_TYPE_ITEMS:
            self.assertEqual(key_type_display_name(key_type_value(name)), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
