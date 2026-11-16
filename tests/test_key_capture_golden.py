"""按键捕获框行为黄金测试 — 钉住 KeyCaptureEdit 的对外行为契约。

背景：``controls/key_capture_edit.py`` 由**4 份重复实现**合并而来
（main_window / adb_touch_form / key_preset_form / mouse_track_form），
它们的差异很微妙：

    - main_window     只认可打印单字符、暴露 vk、点击清空
    - adb_touch_form  F1~F12 + 特殊键，Esc 清空并回调 ``("", 0)``
    - key_preset_form 同上，但固定宽 28
    - mouse_track_form 同上，但 Esc 记为 "Esc"、固定宽 55、无占位文本

``tests/data/key_capture_golden.json`` 是**合并前**用同一批按键抓取的真实行为快照
（4 个变体 × 30 例 = 120 项）。本测试保证合并后行为逐项不变 ——
这类 UI 控件行为没法靠「看代码」验证，只能靠快照。

运行：<解释器> -m unittest discover -s tests -v
"""
import json
import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

GOLDEN_PATH = pathlib.Path(__file__).parent / "data" / "key_capture_golden.json"

try:
    from PyQt5.QtCore import QEvent, QPointF, Qt
    from PyQt5.QtGui import QKeyEvent, QMouseEvent
    from PyQt5.QtWidgets import QApplication
    _QT_ERROR = None
except ImportError as exc:      # 无 PyQt5 环境下优雅跳过
    _QT_ERROR = exc

_app = None
if _QT_ERROR is None:
    _app = QApplication.instance() or QApplication([])

from controls import KeyCaptureEdit  # noqa: E402

# 与被抓取快照时完全相同的按键电池：(text, key, nativeVirtualKey)
KEYS = [
    ("d", Qt.Key_D, 68), ("a", Qt.Key_A, 65), ("Z", Qt.Key_Z, 90),
    ("1", Qt.Key_1, 49), ("9", Qt.Key_9, 57),
    ("", Qt.Key_F1, 112), ("", Qt.Key_F6, 117), ("", Qt.Key_F12, 123),
    ("", Qt.Key_F13, 124),
    ("", Qt.Key_Escape, 27), (" ", Qt.Key_Space, 32), ("\t", Qt.Key_Tab, 9),
    ("\r", Qt.Key_Return, 13), ("", Qt.Key_Enter, 13),
    ("", Qt.Key_Backspace, 8),
    ("", Qt.Key_Up, 38), ("", Qt.Key_Down, 40), ("", Qt.Key_Left, 37),
    ("", Qt.Key_Right, 39), ("", Qt.Key_Home, 36), ("", Qt.Key_End, 35),
    ("", Qt.Key_PageUp, 33), ("", Qt.Key_PageDown, 34),
    ("", Qt.Key_Insert, 45), ("", Qt.Key_Delete, 46),
    ("", Qt.Key_Shift, 16), ("", Qt.Key_Control, 17), ("", Qt.Key_Alt, 18),
    ("", Qt.Key_unknown, 0),
]


def feed(widget, text, key, vk):
    widget.keyPressEvent(
        QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, 0, vk, 0, text, False, 1))


def click(widget):
    widget.mousePressEvent(
        QMouseEvent(QEvent.MouseButtonPress, QPointF(1, 1), Qt.LeftButton,
                    Qt.LeftButton, Qt.NoModifier))


# ═══ 四个调用点的构造方式（必须与 ui/ 里的实际调用一致）═══

def make_main_window():
    calls = []
    return KeyCaptureEdit(allow_special=False, clear_on_click=True), calls


def make_adb():
    """等价于 ui/adb_touch_form.py 里的适配 lambda"""
    calls = []

    def cb(name, vk):
        calls.append([7, name, vk, name == ""])

    return KeyCaptureEdit(escape_clears=True, on_captured=cb), calls


def make_key_preset():
    calls = []

    def cb(name, vk):
        calls.append([name, vk])

    return KeyCaptureEdit(on_captured=cb, width=28, escape_clears=True), calls


def make_mouse_track():
    return KeyCaptureEdit(text="D", width=55, placeholder=""), []


VARIANTS = {
    "main_window": make_main_window,
    "adb_touch_form": make_adb,
    "key_preset_form": make_key_preset,
    "mouse_track_form": make_mouse_track,
}


@unittest.skipIf(_QT_ERROR is not None, f"PyQt5 不可用: {_QT_ERROR}")
class TestKeyCaptureGolden(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    def _replay(self, make):
        widget, calls = make()
        rows = []
        for text, key, vk in KEYS:
            before = len(calls)
            feed(widget, text, key, vk)
            rows.append({
                "key": text or f"<{key}>",
                "text": widget.text(),
                "vk": widget.vk if make is make_main_window else None,
                "cb": list(calls[before:]),
            })
        click(widget)
        rows.append({"key": "<mouseClick>", "text": widget.text(),
                     "vk": None, "cb": list(calls[len(KEYS):])})
        return widget, rows

    def test_all_variants_match_golden_behaviour(self):
        for name, make in VARIANTS.items():
            with self.subTest(variant=name):
                golden = self.golden[name]
                widget, rows = self._replay(make)

                self.assertEqual(widget.isReadOnly(), golden["isReadOnly"])
                self.assertEqual(widget.placeholderText(), golden["placeholder"],
                                 "占位文本不一致（mouse_track 历史上为空串）")
                self.assertTrue(bool(widget.alignment() & Qt.AlignHCenter),
                                "必须居中")
                self.assertEqual(len(rows), len(golden["rows"]))

                for i, (g, n) in enumerate(zip(golden["rows"], rows)):
                    ctx = f"{name} 第{i}例({g['key']})"
                    self.assertEqual(g["text"], n["text"], f"{ctx} 显示文本不一致")
                    self.assertEqual(g["vk"], n["vk"], f"{ctx} vk 不一致")
                    self.assertEqual(g["cb"], n["cb"], f"{ctx} 回调序列不一致")

    def test_escape_behaviour_differs_by_variant(self):
        """Esc 的三种语义必须分别保住 —— 这是最容易改错的点。"""
        mw, _ = make_main_window()
        feed(mw, "", Qt.Key_Escape, 27)
        self.assertEqual(mw.text(), "", "main_window：Esc 不产生任何键名")

        adb, calls = make_adb()
        feed(adb, "", Qt.Key_Escape, 27)
        self.assertEqual(adb.text(), "")
        self.assertEqual(calls, [[7, "", 0, True]], "adb：Esc 要回调 clear=True")

        preset, calls = make_key_preset()
        feed(preset, "", Qt.Key_Escape, 27)
        self.assertEqual(calls, [["", 0]], "key_preset：Esc 回调空值")

        mt, _ = make_mouse_track()
        feed(mt, "", Qt.Key_Escape, 27)
        self.assertEqual(mt.text(), "Esc", "mouse_track：Esc 记为 'Esc'")

    def test_unknown_key_does_nothing(self):
        widget, calls = make_adb()
        feed(widget, "", Qt.Key_unknown, 0)
        self.assertEqual(widget.text(), "")
        self.assertEqual(calls, [])

    def test_vk_only_set_for_recognised_keys(self):
        widget, _ = make_main_window()
        feed(widget, "q", Qt.Key_Q, 81)
        self.assertEqual(widget.vk, 81)
        feed(widget, "", Qt.Key_Shift, 16)
        self.assertEqual(widget.vk, 81, "未识别按键不应改 vk")


if __name__ == "__main__":
    unittest.main(verbosity=2)
