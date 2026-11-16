import json
import os
import shutil
import sys
import uuid
import unittest
from pathlib import Path
from unittest import mock

from core import builtin_presets


TASK_TEMP_DIR = Path(__file__).resolve().parents[2] / "打包记录" / "20261117_分发版"


class TaskTempCase(unittest.TestCase):
    def new_temp_dir(self, prefix):
        TASK_TEMP_DIR.mkdir(parents=True, exist_ok=True)
        path = TASK_TEMP_DIR / (prefix + uuid.uuid4().hex)
        path.mkdir()
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path


class BuiltinPresetTests(TaskTempCase):
    def setUp(self):
        self.root = self.new_temp_dir("builtin_presets_")
        self.data_dir = self.root / "data"
        self.bundle_dir = self.root / "bundle"
        self.bundle_dir.mkdir()
        self.bundle_files = {
            "基础键位.json": b'{"name":"basic","keymaps":[]}',
            "爬塔键位.json": b'{"name":"tower","keymaps":[]}',
        }
        for name, payload in self.bundle_files.items():
            (self.bundle_dir / name).write_bytes(payload)
        self.source_patch = mock.patch.object(
            builtin_presets, "get_builtin_presets_dir", return_value=str(self.bundle_dir))
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)

    def test_first_seed_copies_all_presets_byte_for_byte(self):
        builtin_presets.seed_builtin_presets(str(self.data_dir))

        self.assertEqual(
            {path.name for path in self.data_dir.glob("*.json")},
            set(self.bundle_files),
        )
        for name, payload in self.bundle_files.items():
            self.assertEqual((self.data_dir / name).read_bytes(), payload)
        marker = json.loads((self.data_dir / builtin_presets.MARKER_FILENAME).read_text(
            encoding="utf-8"))
        self.assertEqual(set(marker["files"]), set(self.bundle_files))

    def test_existing_user_file_is_never_overwritten(self):
        self.data_dir.mkdir()
        custom = b"user data"
        (self.data_dir / "基础键位.json").write_bytes(custom)

        builtin_presets.seed_builtin_presets(str(self.data_dir))

        self.assertEqual((self.data_dir / "基础键位.json").read_bytes(), custom)
        self.assertEqual((self.data_dir / "爬塔键位.json").read_bytes(),
                         self.bundle_files["爬塔键位.json"])

    def test_deleted_seeded_file_is_not_resurrected(self):
        builtin_presets.seed_builtin_presets(str(self.data_dir))
        (self.data_dir / "基础键位.json").unlink()

        builtin_presets.seed_builtin_presets(str(self.data_dir))

        self.assertFalse((self.data_dir / "基础键位.json").exists())

    def test_corrupt_marker_stops_safely_without_overwriting_user_data(self):
        self.data_dir.mkdir()
        marker_path = self.data_dir / builtin_presets.MARKER_FILENAME
        marker_path.write_text("{broken", encoding="utf-8")
        custom = b"keep this"
        (self.data_dir / "基础键位.json").write_bytes(custom)

        builtin_presets.seed_builtin_presets(str(self.data_dir))

        self.assertEqual(marker_path.read_text(encoding="utf-8"), "{broken")
        self.assertEqual((self.data_dir / "基础键位.json").read_bytes(), custom)
        self.assertFalse((self.data_dir / "爬塔键位.json").exists())

    def test_new_bundle_file_is_seeded_after_initial_marker(self):
        builtin_presets.seed_builtin_presets(str(self.data_dir))
        added = b'{"name":"later"}'
        (self.bundle_dir / "新增预设.json").write_bytes(added)

        builtin_presets.seed_builtin_presets(str(self.data_dir))

        self.assertEqual((self.data_dir / "新增预设.json").read_bytes(), added)


class BuiltinPresetPathTests(TaskTempCase):
    def test_frozen_resource_path_uses_meipass_presets(self):
        temporary = self.new_temp_dir("frozen_presets_")
        meipass = temporary / "_internal"
        expected = meipass / "presets"
        expected.mkdir(parents=True)
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", str(meipass), create=True), \
                mock.patch.object(sys, "executable", str(temporary / "app.exe")):
            self.assertEqual(Path(builtin_presets.get_builtin_presets_dir()), expected)

    def test_frozen_resource_path_falls_back_to_exe_internal(self):
        temporary = self.new_temp_dir("frozen_internal_")
        expected = temporary / "_internal" / "presets"
        expected.mkdir(parents=True)
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", "", create=True), \
                mock.patch.object(sys, "executable", str(temporary / "app.exe")):
            self.assertEqual(Path(builtin_presets.get_builtin_presets_dir()), expected)


if __name__ == "__main__":
    unittest.main()
