"""包名表回归测试 — 钉住「长前缀优先」契约与文件筛选规则。

为什么必须有：这张表迁移前在项目里有 **4 处副本 4 种形式**，合并成一处后，
最危险的失效方式是**有人"顺手整理"把列表按字母或业务顺序重排** —— 那会让
B 服文件被误判成官服（因为 "官服" 前缀 ``com.RoamingStar.BlueArchive-`` 是
"B服" 前缀的真前缀）。C# 原实现用 ``OrderByDescending(v => v.Length)``
显式保证这一点，这里的测试就是它的等价物。

运行：<解释器> -m unittest discover -s tests -v
"""
import os
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.package_names import (  # noqa: E402
    KNOWN_PREFIXES, OTHER_PREFIX, PACKAGE_ITEMS, PACKAGE_PREFIX_BY_NAME,
    REAL_PACKAGE_NAMES, REAL_PACKAGE_PREFIXES, UNIVERSE_PREFIX,
    filter_keymap_files, package_name_from_path, package_prefix,
    strip_package_prefix,
)

B服 = "com.RoamingStar.BlueArchive.bilibili-"
官服 = "com.RoamingStar.BlueArchive-"


class TestPackageTable(unittest.TestCase):

    def test_table_matches_csharp_package_name_types(self):
        """与 C# Form1.PackageNameTypes 逐项一致"""
        self.assertEqual(PACKAGE_PREFIX_BY_NAME, {
            "官服": "com.RoamingStar.BlueArchive-",
            "B服": "com.RoamingStar.BlueArchive.bilibili-",
            "日服": "com.YostarJP.BlueArchive-",
            "国际服": "com.nexon.bluearchive-",
            "其他": "other",
            "宇宙服": "萌新666sssaaa",
        })

    def test_display_name_order_preserved(self):
        """显示名顺序被 UI 依赖（下拉框顺序），不要重排"""
        self.assertEqual(list(PACKAGE_PREFIX_BY_NAME), list(PACKAGE_ITEMS_NAMES()))

    def test_prefixes_are_sorted_by_descending_length(self):
        """★长前缀优先：C# 的 OrderByDescending(v => v.Length) 等价物"""
        lengths = [len(p) for p in REAL_PACKAGE_PREFIXES]
        self.assertEqual(
            lengths, sorted(lengths, reverse=True),
            "REAL_PACKAGE_PREFIXES 必须按长度降序 —— 颠倒会让 B 服文件被判成官服",
        )

    def test_bilibili_prefix_comes_before_official(self):
        """单独钉一次：真前缀关系不可逆，最容易被"整理"搞坏"""
        self.assertLess(
            REAL_PACKAGE_PREFIXES.index(B服), REAL_PACKAGE_PREFIXES.index(官服),
            "B 服前缀必须排在官服前缀之前",
        )
        self.assertTrue(B服.startswith(官服[:27]), "前置条件：B服前缀确实是官服前缀的扩展")

    def test_real_package_names_excludes_pseudo(self):
        self.assertEqual(REAL_PACKAGE_NAMES, ("官服", "B服", "日服", "国际服"))
        self.assertNotIn(OTHER_PREFIX, REAL_PACKAGE_PREFIXES)
        self.assertNotIn(UNIVERSE_PREFIX, REAL_PACKAGE_PREFIXES)

    def test_known_prefixes_same_set_as_real(self):
        self.assertEqual(set(KNOWN_PREFIXES), set(REAL_PACKAGE_PREFIXES))


def PACKAGE_ITEMS_NAMES():
    return [n for n, _ in PACKAGE_ITEMS]


class TestPackagePrefixLookup(unittest.TestCase):

    def test_known_names(self):
        self.assertEqual(package_prefix("官服"), 官服)
        self.assertEqual(package_prefix("B服"), B服)
        self.assertEqual(package_prefix("宇宙服"), UNIVERSE_PREFIX)

    def test_unknown_name_returns_default(self):
        self.assertEqual(package_prefix("不存在"), None)
        self.assertEqual(package_prefix("不存在", OTHER_PREFIX), "other")
        self.assertEqual(package_prefix("", OTHER_PREFIX), "other")


class TestStripPackagePrefix(unittest.TestCase):

    def test_strips_known_prefix_keeps_extension(self):
        self.assertEqual(
            strip_package_prefix("com.YostarJP.BlueArchive-13判测试.json"),
            "13判测试.json",
        )

    def test_long_prefix_wins_for_bilibili_file(self):
        """B 服文件必须剥掉完整的 bilibili- 前缀，而不是留 '.bilibili-x'"""
        self.assertEqual(strip_package_prefix(B服 + "S62TM.json"), "S62TM.json")

    def test_unknown_prefix_returned_as_is(self):
        self.assertEqual(strip_package_prefix("自定义方案(1).json"), "自定义方案(1).json")

    def test_empty_and_no_extension(self):
        self.assertEqual(strip_package_prefix(""), "")
        self.assertEqual(strip_package_prefix("no_ext"), "no_ext")


class TestPackageNameFromPath(unittest.TestCase):

    def test_all_servers(self):
        cases = {
            "官服": 官服 + "BA.json",
            "B服": B服 + "B服测试.json",
            "日服": "com.YostarJP.BlueArchive-日服.json",
            "国际服": "com.nexon.bluearchive-国际.json",
        }
        for server, filename in cases.items():
            with self.subTest(server=server):
                self.assertEqual(package_name_from_path("/tmp/" + filename), server)

    def test_long_prefix_priority_end_to_end(self):
        """端到端：B 服文件绝不能被判成官服（C# OrderByDescending 的等价保证）"""
        self.assertEqual(package_name_from_path(B服 + "x.json"), "B服")
        self.assertEqual(package_name_from_path(官服 + "x.json"), "官服")

    def test_unknown_returns_other(self):
        self.assertEqual(package_name_from_path("自定义方案(1).json"), "其他")
        self.assertEqual(package_name_from_path("com.mumu.launcher-默认.json"), "其他")
        self.assertEqual(package_name_from_path(""), "其他")


class TestFilterKeymapFiles(unittest.TestCase):

    FILES = [
        官服 + "BA.json",
        B服 + "B服测试.json",
        "com.YostarJP.BlueArchive-日服.json",
        "com.nexon.bluearchive-国际.json",
        "自定义方案(1).json",
        "com.mumu.launcher-默认.json",
        "com.RoamingStar.BlueArchive-BA.txt",
        "恢复方案2.json~RF2b06927.TMP",
        "no_ext",
    ]

    def test_known_prefix_keeps_display_name_stripped(self):
        got = filter_keymap_files(self.FILES, 官服, only_json=False)
        # 只有含官服前缀的命中；.TMP / no_ext / 其他包的文件都不含该前缀，自然排除。
        # 注意显示名由 splitext 去扩展名得到，所以 .txt 与 .json 的显示名都是 "BA"
        # —— 这正是 only_json=False 的副作用（下拉框里会出现看起来重复的条目）。
        self.assertEqual(got, [
            ("BA", 官服 + "BA.json"),
            ("BA", 官服 + "BA.txt"),
        ])
        self.assertNotIn("B服测试", [d for d, _ in got], "B 服文件不应混进官服列表")

    def test_bilibili_prefix(self):
        got = filter_keymap_files(self.FILES, B服, only_json=False)
        self.assertEqual([d for d, _ in got], ["B服测试"])

    def test_other_excludes_all_known_prefixes(self):
        got = filter_keymap_files(self.FILES, OTHER_PREFIX, only_json=False)
        displays = [d for d, _ in got]
        self.assertIn("自定义方案(1)", displays)
        self.assertIn("no_ext", displays)
        self.assertNotIn("BA", displays)

    def test_universe_returns_empty(self):
        self.assertEqual(filter_keymap_files(self.FILES, UNIVERSE_PREFIX), [])

    def test_only_json_true_filters_other_extensions(self):
        """wide_screen 的语义：只列 .json"""
        got = filter_keymap_files(self.FILES, 官服, only_json=True)
        self.assertEqual([d for d, _ in got], ["BA"])

    def test_only_json_false_keeps_other_extensions(self):
        """main_window 的语义：不过滤扩展名（保留历史行为，见函数文档）。

        用 ``...BlueArchive-BA.txt`` 验证 —— 它含官服前缀、扩展名不是 .json，
        正好把两种语义的差别暴露出来。
        """
        loose = [f for _, f in filter_keymap_files(self.FILES, 官服, only_json=False)]
        strict = [f for _, f in filter_keymap_files(self.FILES, 官服, only_json=True)]
        self.assertIn(官服 + "BA.txt", loose)
        self.assertNotIn(官服 + "BA.txt", strict)
        self.assertLess(len(strict), len(loose))

    def test_returns_bare_filenames_not_paths(self):
        """调用方自行拼目录 —— 两个原始调用点的做法不同，必须保持裸文件名"""
        for _, f in filter_keymap_files(self.FILES, 官服, only_json=False):
            self.assertNotIn(os.sep, f)
            self.assertNotIn("/", f)

    def test_empty_prefix_keeps_everything(self):
        """wide_screen 的 `currentData() or ""` 退化路径：不过滤"""
        got = filter_keymap_files(self.FILES, "", only_json=True)
        self.assertEqual(len(got), 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
