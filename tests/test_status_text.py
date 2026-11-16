"""状态文案回归测试 — 钉住「按键 [X] 已存在」的统一格式。

背景：这一个语义迁移前有 5 种写法（全角/半角冒号、有无方括号各不相同）。
本模块只产出文本、不依赖 Qt，所以可以直接断言字面量。

运行：<解释器> -m unittest discover -s tests -v
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.status_text import (  # noqa: E402
    DETAIL_EXACT_MATCH, DETAIL_NO_INJECT, KEY_EXISTS_REASON,
    key_created, key_exists, listening,
)


class TestKeyExists(unittest.TestCase):

    def test_default_wording(self):
        """统一后的标准文案（用户确认采用）"""
        self.assertEqual(key_exists("D"), "按键 [D] 已存在，禁止重复生成")

    def test_reason_constant_is_the_approved_wording(self):
        self.assertEqual(KEY_EXISTS_REASON, "已存在，禁止重复生成")
        self.assertEqual(key_exists("X"), f"按键 [X] {KEY_EXISTS_REASON}")

    def test_detail_is_appended_in_parentheses(self):
        self.assertEqual(key_exists("D", DETAIL_NO_INJECT),
                         "按键 [D] 已存在（无需注入）")
        self.assertEqual(key_exists("D", DETAIL_EXACT_MATCH),
                         "按键 [D] 已存在（坐标、按键、类型完全一致）")
        self.assertEqual(key_exists("D", "类型: 点击按键"),
                         "按键 [D] 已存在（类型: 点击按键）")

    def test_always_uses_halfwidth_brackets(self):
        """方括号一律半角 —— 这是 5 种写法漂移的主要来源之一"""
        for text in (key_exists("D"), key_exists("D", DETAIL_NO_INJECT)):
            self.assertIn("[D]", text)
            self.assertNotIn("【", text)

    def test_never_uses_fullwidth_colon(self):
        """旧写法里 adb 用「已存在：」、quick_touch 用「已存在: 」，统一后不应再出现冒号"""
        for text in (key_exists("D"), key_exists("D", DETAIL_NO_INJECT),
                     key_created("D"), key_created("D", "坐标 (1,2)")):
            self.assertNotIn("：", text)

    def test_default_variant_uses_fullwidth_comma_before_reason(self):
        self.assertIn("，", key_exists("D"))
        self.assertNotIn(",", key_exists("D").rsplit("[D]", 1)[1])

    def test_name_is_not_mutated(self):
        """大小写由调用方决定（quick_touch 会先 upper）"""
        self.assertEqual(key_exists("d"), "按键 [d] 已存在，禁止重复生成")
        self.assertEqual(key_exists("D"), "按键 [D] 已存在，禁止重复生成")


class TestKeyCreated(unittest.TestCase):

    def test_default(self):
        self.assertEqual(key_created("D"), "已生成按键 [D]")

    def test_with_detail(self):
        self.assertEqual(key_created("D", "坐标 (800,450)"),
                         "已生成按键 [D]（坐标 (800,450)）")


class TestListening(unittest.TestCase):

    def test_off(self):
        self.assertEqual(listening(False), "提示：已关闭键盘监听")

    def test_on_once(self):
        self.assertEqual(listening(True), "提示：已开启键盘监听（单次）")

    def test_on_continuous(self):
        self.assertEqual(listening(True, continuous=True),
                         "提示：已开启键盘监听（连续）")

    def test_off_ignores_continuous_flag(self):
        self.assertEqual(listening(False, continuous=True), "提示：已关闭键盘监听")


class TestNoDriftInUiLayer(unittest.TestCase):
    """UI 层不应再出现旧的文案写法（防止有人"顺手"写回硬编码）"""

    LEGACY_PATTERNS = (
        "已存在：按键",          # adb 的全角冒号写法
        "已存在: ",              # quick_touch 的半角无方括号写法
        "禁止重复生成\")",        # 硬编码整句
        "无需注入\")",            # 硬编码整句
    )

    def test_no_legacy_literal_in_ui(self):
        offenders = []
        for py in sorted((PROJECT_ROOT / "ui").glob("*.py")):
            if py.name == "status_text.py":      # 该模块自身是文案来源，需保留字面量
                continue
            src = py.read_text(encoding="utf-8")
            for pat in self.LEGACY_PATTERNS:
                if pat in src:
                    offenders.append(f"{py.name}: {pat}")
        self.assertEqual(offenders, [], f"UI 层出现旧文案写法: {offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
