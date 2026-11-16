"""配置管理器回归测试 — 钉住「共享实例」与「批量化落盘」两条约定。

背景：每个面板各 new 一份 ConfigManager 会各持一份内存快照，而落盘写的是
**整份**配置 —— 于是「打开鼠标回溯面板保存热键」会把主窗口期间更新的
JsonFolderPath 用旧快照覆盖回去。见 TestSharedInstance 里的对照用例。

运行：<解释器> -m unittest discover -s tests -v
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config_manager import ConfigManager, DEFAULT_CONFIG  # noqa: E402


class IsolatedConfigTest(unittest.TestCase):
    """每个用例一个临时 MOMO_DATA_DIR，互不干扰"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="momo_cfg_")
        self.tmp = pathlib.Path(self._tmp.name)
        self._old_env = os.environ.get("MOMO_DATA_DIR")
        os.environ["MOMO_DATA_DIR"] = str(self.tmp)
        ConfigManager.reset_shared()

    def tearDown(self):
        ConfigManager.reset_shared()
        if self._old_env is None:
            os.environ.pop("MOMO_DATA_DIR", None)
        else:
            os.environ["MOMO_DATA_DIR"] = self._old_env
        self._tmp.cleanup()

    def read_config(self):
        return json.loads((self.tmp / "config.json").read_text(encoding="utf-8"))


class TestSharedInstance(IsolatedConfigTest):

    def test_shared_returns_same_object(self):
        self.assertIs(ConfigManager.shared(), ConfigManager.shared())

    def test_plain_constructor_still_creates_independent_instance(self):
        """保留 ConfigManager() 的独立实例语义（build_tool 等场景仍会用到）。"""
        self.assertIsNot(ConfigManager.shared(), ConfigManager())

    def test_shared_instance_sees_other_panels_writes(self):
        """面板改了热键，主窗口那一份应立刻看到（同一对象）。"""
        main = ConfigManager.shared()
        panel = ConfigManager.shared()
        panel.find_key = "K"
        self.assertEqual(main.find_key, "K")

    def test_independent_instance_loses_concurrent_change(self):
        """⚠ 反例：独立实例会把别人的更新覆盖掉 —— 这正是必须用 shared() 的原因。

        本用例刻意断言「旧写法确实会丢数据」，防止有人把 shared() 改回 ConfigManager()。
        """
        stale = ConfigManager()                 # 模拟某个面板在早期创建的独立快照
        main = ConfigManager.shared()
        folder_path = str(self.tmp / "新目录")
        main.json_folder_path = folder_path
        main.save()
        self.assertEqual(self.read_config()["JsonFolderPath"], folder_path)

        stale.find_key = "Q"                    # 面板只改热键
        stale.save()                            # 但它整份写回旧快照
        self.assertEqual(
            self.read_config()["JsonFolderPath"], "",
            "独立实例用旧快照覆盖了 JsonFolderPath —— 这就是配置脑裂（数据丢失）",
        )

    def test_shared_instance_does_not_lose_concurrent_change(self):
        """正例：走 shared() 时，面板保存热键不会碰掉 JsonFolderPath。"""
        main = ConfigManager.shared()
        folder_path = str(self.tmp / "新目录")
        main.json_folder_path = folder_path
        main.save()

        panel = ConfigManager.shared()
        panel.find_key = "Q"
        panel.save()

        cfg = self.read_config()
        self.assertEqual(cfg["JsonFolderPath"], folder_path)
        self.assertEqual(cfg["FindKey"], "Q")


class TestBatchWrites(IsolatedConfigTest):

    def _count_writes(self, cfg):
        """包裹 _write_now，统计真实落盘次数"""
        calls = []
        original = cfg._write_now

        def counting():
            calls.append(1)
            return original()

        cfg._write_now = counting
        return calls

    def test_setter_persists_immediately_without_batch(self):
        """默认语义必须保留：赋值即落盘（有调用点只赋值、不显式 save）。"""
        cfg = ConfigManager.shared()
        writes = self._count_writes(cfg)
        cfg.fx = "1920"
        self.assertEqual(len(writes), 1, "无 batch 时赋值应立即落盘")
        self.assertEqual(self.read_config()["FX"], "1920")

    def test_batch_merges_three_writes_into_one(self):
        cfg = ConfigManager.shared()
        writes = self._count_writes(cfg)
        with cfg.batch():
            cfg.fx = "1920"
            cfg.fy = "1080"
            cfg.save()
        self.assertEqual(len(writes), 1, f"batch 内应只落盘 1 次，实际 {len(writes)} 次")
        cfg_json = self.read_config()
        self.assertEqual(cfg_json["FX"], "1920")
        self.assertEqual(cfg_json["FY"], "1080")

    def test_without_batch_would_write_three_times(self):
        """对照：不加 batch 时确实是 3 次写盘。"""
        cfg = ConfigManager.shared()
        writes = self._count_writes(cfg)
        cfg.fx = "1920"
        cfg.fy = "1080"
        cfg.save()
        self.assertEqual(len(writes), 3)

    def test_batch_flushes_even_on_exception(self):
        cfg = ConfigManager.shared()
        with self.assertRaises(RuntimeError):
            with cfg.batch():
                cfg.fx = "1024"
                raise RuntimeError("模拟业务异常")
        self.assertEqual(self.read_config()["FX"], "1024",
                         "batch 异常退出也必须落盘，不能丢配置")

    def test_nested_batch_writes_once(self):
        cfg = ConfigManager.shared()
        writes = self._count_writes(cfg)
        with cfg.batch():
            cfg.fx = "800"
            with cfg.batch():
                cfg.fy = "600"
            self.assertEqual(len(writes), 0, "内层退出时不应落盘")
        self.assertEqual(len(writes), 1, "最外层退出时才落盘")


class TestConfigDefaults(IsolatedConfigTest):

    def test_default_fields_complete(self):
        expected = {"ConfigVersion", "FX", "FY", "FindKey", "ResetKey",
                    "JsonFolderPath", "Resolution4String", "AdbPort",
                    "AdbPath", "AdbPortsHistory"}
        self.assertEqual(set(DEFAULT_CONFIG), expected)

    def test_first_run_creates_config_with_defaults(self):
        cfg = ConfigManager.shared()
        cfg.save()
        self.assertEqual(self.read_config()["FX"], "1280")
        self.assertEqual(self.read_config()["AdbPort"], "16384")

    def test_key_setter_uppercases_and_rejects_empty(self):
        cfg = ConfigManager.shared()
        cfg.find_key = "k"
        self.assertEqual(cfg.find_key, "K")
        cfg.find_key = ""
        self.assertEqual(cfg.find_key, "K", "空值不应写入")

    def test_corrupted_config_is_backed_up(self):
        path = self.tmp / "config.json"
        path.write_text("{ broken", encoding="utf-8")
        cfg = ConfigManager()
        self.assertEqual(cfg.fx, "1280", "损坏配置应回落默认值")
        self.assertTrue(list(self.tmp.glob("config.json.corrupted_backup")),
                        "损坏配置应留 .corrupted_backup 备份")


if __name__ == "__main__":
    unittest.main(verbosity=2)
