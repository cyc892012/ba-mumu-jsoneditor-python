"""迁移安全回归：失败不破坏文件、索引兼容、非法数值拒绝。"""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import json_editor as editor
from infra.index_backup import IndexFileBackupManager
from infra.mumu_sync import MuMuKeymapSyncService
from input.keyboard_listener import KeyboardListener
from pynput.keyboard import Key
from utils.safe_parse import try_parse_float


class MigrationSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="migration_safe_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manager = IndexFileBackupManager({"官服": "官服"}, str(self.root))
        self.manager.index_directory = str(self.root)
        self.key = editor.create_key("Click", "A", "0.5", "0.5", "30", "65")
        self.text = json.dumps({"keymaps": [json.loads(self.key)]})

    def test_failed_atomic_replace_preserves_old_bytes(self):
        target = self.root / "scheme.json"
        target.write_bytes(self.text.encode())
        before = target.read_bytes()
        with mock.patch.object(editor.os, "replace", side_effect=PermissionError("模拟占用")):
            self.assertFalse(editor.write_text_atomic(str(target), "replacement"))
        self.assertEqual(before, target.read_bytes())

    def test_nonfinite_coordinates_cannot_be_modified(self):
        macro = json.dumps({"keymaps": [json.loads(editor.create_key("Macro", "A", "0.5", "0.5", "30", "65"))]})
        for value in ("NaN", "Infinity", "-inf", "1e9999"):
            self.assertFalse(try_parse_float(value)[0])
            self.assertIsNone(editor.re_key_by_text(self.text, "A", value, "0.5"))
            self.assertIsNone(editor.re_key_by_virtual_key(macro, 65, "0.5", value))

    def test_nonfinite_json_is_never_serialized(self):
        with self.assertRaises(ValueError):
            editor._serialize_json({"x": float("nan")})

    def test_nonarray_keymaps_recovers_on_write(self):
        for value in (None, {}, "bad"):
            updated = editor.write_keys(self.key, json.dumps({"keymaps": value}))
            self.assertEqual(len(json.loads(updated)["keymaps"]), 1)

    def test_malformed_items_do_not_hide_valid_key(self):
        value = json.dumps({"keymaps": [None, 12, {"key": "bad"}, json.loads(self.key)]})
        self.assertEqual(editor.find_key_by_virtual_key(value, 65), 3)
        self.assertEqual(editor.find_key_by_text(value, "A"), 3)

    def test_special_key_uses_native_vk(self):
        self.assertEqual(KeyboardListener._get_vk(Key.cmd), Key.cmd.value.vk)

    def test_bom_index_can_be_backed_up_and_synced(self):
        target = self.root / "官服.json"
        scheme = str(self.root / "官服-test.json")
        target.write_text(json.dumps({"Config": [], "Current": {"test": {"path": scheme}}}), encoding="utf-8-sig")
        self.assertTrue(self.manager.backup_index_direct(target.name))
        self.assertEqual(MuMuKeymapSyncService().read_current_path(str(target)), scheme)

    def test_nonobject_index_backup_is_rejected(self):
        target = self.root / "bad.json"
        for text in ("[]", "null", '"bad"'):
            target.write_text(text, encoding="utf-8")
            self.assertFalse(self.manager.backup_index_direct(target.name))

    def test_orphan_case_only_difference_is_not_duplicate(self):
        (self.root / "官服.json").write_text(json.dumps({"Config": [{"a": {}}]}), encoding="utf-8")
        (self.root / "官服-A.json").write_text(self.text, encoding="utf-8")
        self.assertEqual(self.manager.scan_orphan_schemes("官服.json"), [])

    def test_index_write_failure_reports_zero_added(self):
        target = self.root / "官服.json"
        target.write_text('{"Config": []}', encoding="utf-8")
        before = target.read_bytes()
        with mock.patch.object(IndexFileBackupManager, "atomic_write_all_text", side_effect=OSError("模拟占用")), \
                mock.patch.object(IndexFileBackupManager, "retry_io", side_effect=lambda fn: fn()):
            count = self.manager.add_schemes_to_index(target.name, [{"SchemeName": "a", "FilePath": "a.json"}])
        self.assertEqual(count, 0)
        self.assertEqual(target.read_bytes(), before)
