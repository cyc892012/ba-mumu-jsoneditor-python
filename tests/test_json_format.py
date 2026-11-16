"""JSON 格式回归测试 — 钉住「与 C# / MuMu 对齐」这条硬约束。

跑的动机：键位 JSON 的格式约定（ensure_ascii=False / indent=4 / LF / 无 BOM）
是**全项目最容易静默破坏**的约束 —— 一旦某个面板自己写文件时漏掉参数，
MuMu 就读不了键位文件，而且不会报错。

运行方式（用项目自带的解释器）::

    cd <主项目>
    <解释器> -m unittest discover -s tests -v

或用 pytest（若已安装）::

    <解释器> -m pytest tests -v
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

from core.json_editor import (  # noqa: E402
    _serialize_json,
    create_key,
    write_keys,
    write_text_atomic,
    get_key_count_from_obj,
)
from core.config_manager import ConfigManager  # noqa: E402
from infra.index_backup import _serialize_json as index_serialize  # noqa: E402

# 真实键位文件目录（MuMu 模拟器）。不存在时测试自动退化为内置夹具。
# 注意：pathlib.Path("") == Path(".") 且为真值，所以必须显式判断而不是用 or 兜底。
_env_keymap_dir = os.environ.get("MOMO_KEYMAP_DIR", "").strip()
MUMU_KEYMAP_DIR = (
    pathlib.Path(_env_keymap_dir)
    if _env_keymap_dir
    else pathlib.Path(os.path.expandvars(r"%APPDATA%\Netease\MuMuPlayer\data\keymapConfig"))
)

BUILTIN_FIXTURE = {
    "ConfigVersion": 1,
    "keymaps": [
        {
            "editor_icon_scale": 1,
            "icon": {"rel_position": {"rel_x": 0.5, "rel_y": 0.25}, "visibility": True},
            "key": {"device": "keyboard", "scan_code": 32, "text": "D", "virtual_key": 68},
            "rel_work_position": {"rel_x": 0.5, "rel_y": 0.25},
            "type": "Click",
        },
        {
            "icon": {"rel_position": {"rel_x": 0.125, "rel_y": 0.75}, "visibility": True},
            "key": {"device": "keyboard", "scan_code": 33, "text": "中文键名", "virtual_key": 70},
            "press_actions": ["start_loop:until_release", "curve_rel:mouse;(0.125,0.75)"],
            "release_actions": [],
            "type": "Macro",
        },
    ],
}


def load_fixtures():
    """返回 [(名称, 原始文本)]；优先用真实 MuMu 键位文件，缺失时用内置夹具。"""
    out = []
    if MUMU_KEYMAP_DIR.is_dir():
        for p in sorted(MUMU_KEYMAP_DIR.glob("*.json"))[:12]:
            try:
                text = p.read_text(encoding="utf-8-sig")
                json.loads(text)
            except Exception:
                continue
            out.append((p.name, text))
    if not out:
        out.append(("<内置夹具>", json.dumps(BUILTIN_FIXTURE, ensure_ascii=False, indent=4)))
    return out


class TempDirMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="momo_test_")
        self.tmp = pathlib.Path(self._tmp.name)
        self._old_env = os.environ.get("MOMO_DATA_DIR")
        os.environ["MOMO_DATA_DIR"] = str(self.tmp)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("MOMO_DATA_DIR", None)
        else:
            os.environ["MOMO_DATA_DIR"] = self._old_env
        self._tmp.cleanup()


class TestSerializerFormat(unittest.TestCase):
    """_serialize_json 的格式约定"""

    def test_indent_is_four_spaces(self):
        text = _serialize_json({"a": {"b": 1}})
        self.assertIn('\n    "a": {', text)
        self.assertIn('\n        "b": 1', text)

    def test_ensure_ascii_is_false(self):
        self.assertEqual(_serialize_json({"a": "中文"}), '{\n    "a": "中文"\n}')

    def test_no_trailing_newline(self):
        text = _serialize_json({"a": 1})
        self.assertFalse(text.endswith("\n"))
        self.assertFalse(text.endswith("\r"))

    def test_no_escaped_slash_or_unicode(self):
        self.assertNotIn("\\u", _serialize_json({"a": "日奈"}))
        self.assertIn("日奈", _serialize_json({"a": "日奈"}))


class TestAtomicWrite(TempDirMixin, unittest.TestCase):
    """write_text_atomic：全项目唯一的落盘实现"""

    def test_writes_lf_without_bom(self):
        target = self.tmp / "a" / "b" / "keymap.json"
        self.assertTrue(write_text_atomic(str(target), _serialize_json({"a": "中文"})))
        raw = target.read_bytes()
        self.assertNotIn(b"\r\n", raw, "键位文件必须是 LF，出现 CRLF 会让 MuMu 解析异常")
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "不能有 UTF-8 BOM")
        self.assertEqual(raw.decode("utf-8"), _serialize_json({"a": "中文"}))

    def test_creates_parent_dirs(self):
        target = self.tmp / "x" / "y" / "z.json"
        self.assertTrue(write_text_atomic(str(target), "{}"))
        self.assertTrue(target.is_file())

    def test_no_tmp_file_left_behind(self):
        target = self.tmp / "k.json"
        write_text_atomic(str(target), "{}")
        self.assertFalse((self.tmp / "k.json.tmp").exists())

    def test_overwrite_is_atomic_and_returns_true(self):
        target = self.tmp / "k.json"
        self.assertTrue(write_text_atomic(str(target), "1"))
        self.assertTrue(write_text_atomic(str(target), "2"))
        self.assertEqual(target.read_text(encoding="utf-8"), "2")

    def test_returns_false_instead_of_raising(self):
        blocker = self.tmp / "blocker"
        blocker.write_text("i am a file", encoding="utf-8")
        self.assertIs(write_text_atomic(str(blocker / "sub" / "x.json"), "{}"), False)


class TestFixtureRoundTrip(unittest.TestCase):
    """真实键位文件的语义等价性与格式稳定性"""

    def test_fixtures_exist(self):
        self.assertTrue(load_fixtures(), "至少要有一个夹具（真实文件或内置）")

    def test_semantics_preserved(self):
        """重新序列化后语义必须完全不变（浮点尾零等纯风格差异允许存在）。"""
        for name, text in load_fixtures():
            with self.subTest(fixture=name):
                original = json.loads(text)
                reserialized = _serialize_json(original)
                self.assertEqual(json.loads(reserialized), original)

    def test_serialize_is_fixpoint(self):
        """再序列化一次不应继续变化 —— 保证反复读写不会漂移。"""
        for name, text in load_fixtures():
            with self.subTest(fixture=name):
                once = _serialize_json(json.loads(text))
                twice = _serialize_json(json.loads(once))
                self.assertEqual(once, twice)

    def test_output_is_lf_only(self):
        for name, text in load_fixtures():
            with self.subTest(fixture=name):
                self.assertNotIn("\r", _serialize_json(json.loads(text)))


class TestWriteKeys(TempDirMixin, unittest.TestCase):
    """写键位文件的端到端格式"""

    def test_write_keys_then_atomic_write(self):
        base = _serialize_json({"ConfigVersion": 1, "keymaps": []})
        new_key = create_key("Click", "D", "0.5", "0.5", "32", "68")
        merged = write_keys(new_key, base)
        self.assertIsNotNone(merged)

        target = self.tmp / "com.RoamingStar.BlueArchive-测试.json"
        self.assertTrue(write_text_atomic(str(target), merged))

        raw = target.read_bytes()
        self.assertNotIn(b"\r\n", raw)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(get_key_count_from_obj(json.loads(raw.decode("utf-8"))), 1)

    def test_chinese_key_text_not_escaped_on_disk(self):
        base = _serialize_json({"keymaps": []})
        merged = write_keys(create_key("Click", "日奈", "0.1", "0.2", "32", "68"), base)
        target = self.tmp / "cn.json"
        write_text_atomic(str(target), merged)
        self.assertIn("日奈", target.read_text(encoding="utf-8"))


class TestConfigFormat(TempDirMixin, unittest.TestCase):
    """config.json 的格式与字段集"""

    EXPECTED_KEYS = {
        "ConfigVersion", "FX", "FY", "FindKey", "ResetKey", "JsonFolderPath",
        "Resolution4String", "AdbPort", "AdbPath", "AdbPortsHistory",
    }

    def test_config_is_lf_without_bom_and_has_all_fields(self):
        cfg = ConfigManager()
        cfg.find_key = "K"
        cfg.reset_key = "L"
        cfg.save()

        path = self.tmp / "config.json"
        self.assertTrue(path.is_file(), f"未在 MOMO_DATA_DIR 下生成 config.json: {path}")
        raw = path.read_bytes()
        self.assertNotIn(b"\r\n", raw)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(set(json.loads(raw.decode("utf-8"))), self.EXPECTED_KEYS)

    def test_config_round_trip(self):
        cfg = ConfigManager()
        cfg.find_key = "K"
        cfg.reset_key = "L"
        cfg.save()
        reloaded = ConfigManager()
        self.assertEqual(reloaded.find_key, "K")
        self.assertEqual(reloaded.reset_key, "L")


class TestIndexBackupFormat(unittest.TestCase):
    """索引备份的序列化格式（与键位文件同规格）"""

    def test_indent_four_and_ensure_ascii_false(self):
        text = index_serialize({"a": "中文", "b": {"c": 1}})
        self.assertIn('\n    "a": "中文"', text)
        self.assertIn('\n        "c": 1', text)
        self.assertNotIn("\r", text)

    def test_index_backup_raises_on_write_failure(self):
        """索引备份的原子写必须保留抛出语义，否则 retry_io 的重试会静默失效。"""
        from infra.index_backup import IndexFileBackupManager

        with tempfile.TemporaryDirectory(prefix="momo_test_idx_") as d:
            blocker = pathlib.Path(d) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            with self.assertRaises(OSError):
                IndexFileBackupManager.atomic_write_all_text(
                    str(blocker / "sub" / "index.json.tmp"),
                    str(blocker / "sub" / "index.json"),
                    '{"a":1}',
                )


class TestDegradeLogging(unittest.TestCase):
    """json_editor 的降级异常必须写进日志，不能静默返回兜底值。

    这是本项目最常见的一类隐性故障：「功能按了没反应」，查不到任何线索。
    """

    def setUp(self):
        from infra.log_service import LogService
        self._log_dir = tempfile.TemporaryDirectory(prefix="momo_logtest_")
        self._old_env = os.environ.get("MOMO_DATA_DIR")
        os.environ["MOMO_DATA_DIR"] = self._log_dir.name
        LogService.shutdown()          # 清掉可能存在的上一次状态
        LogService.initialize(self._log_dir.name)

    def tearDown(self):
        from infra.log_service import LogService
        LogService.shutdown()
        if self._old_env is None:
            os.environ.pop("MOMO_DATA_DIR", None)
        else:
            os.environ["MOMO_DATA_DIR"] = self._old_env
        self._log_dir.cleanup()

    def test_bad_json_is_logged_and_returns_sentinel(self):
        from core.json_editor import find_key_by_virtual_key, find_key_texts
        from infra.log_service import LogService

        self.assertEqual(find_key_by_virtual_key("not json", 68), -1,
                         "返回值必须与改造前一致（兜底值不变）")
        find_key_texts("{broken")
        LogService.shutdown()          # 刷盘

        logs = list(pathlib.Path(self._log_dir.name).rglob("*.txt"))
        self.assertTrue(logs, "应该产生日志文件")
        text = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in logs)
        self.assertIn("[JsonEditor]", text, "降级异常应带 [JsonEditor] 来源标记")
        self.assertIn("JSONDecodeError", text, "应记录异常类型")

    def test_valid_json_does_not_log(self):
        from core.json_editor import get_key_count
        from infra.log_service import LogService

        self.assertEqual(get_key_count('{"keymaps":[]}'), 0)
        LogService.shutdown()

        logs = list(pathlib.Path(self._log_dir.name).rglob("*.txt"))
        text = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in logs)
        self.assertNotIn("[JsonEditor]", text, "正常调用不应产生错误日志")


class TestSerializationSingleSource(unittest.TestCase):
    """格式约定的实现必须收口，不能散落在 UI 层"""

    def test_ui_layer_does_not_write_files_directly(self):
        offenders = []
        for py in (PROJECT_ROOT / "ui").rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            src = py.read_text(encoding="utf-8-sig")
            if 'newline="\\n"' in src or "newline='\\n'" in src:
                offenders.append(py.name)
        self.assertEqual(
            offenders, [],
            f"UI 层不应自己落盘（应调用 core.json_editor.write_text_atomic）: {offenders}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
