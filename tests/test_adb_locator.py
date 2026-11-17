"""离线验证定位优先级、Win32/注册表边界和界面回退，禁止真实设备调用。"""
import ctypes
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from adb import adb_locator as locator


class LocatorTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.base = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="adb_locator_")))
        self.app = self.base / "app"
        self.app.mkdir()
        self.bundle = self.base / "resources/tools/adb/adb.exe"
        self.process = self.stack.enter_context(patch.object(locator, "_iter_mumu_processes", return_value=[]))
        self.registry = self.stack.enter_context(patch.object(locator, "_registry_roots", return_value=[]))
        self.directories = self.stack.enter_context(patch.object(locator, "_directory_roots", return_value=[]))
        self.which = self.stack.enter_context(patch.object(locator.shutil, "which", return_value=None))
        self.stack.enter_context(patch.object(locator, "bundled_adb_path", return_value=self.bundle))

    def file(self, relative):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"offline-adb-fixture")
        return path

    def locate(self, current=""):
        return locator.locate_adb(str(current), self.app)

    def test_all_priority_levels_in_order(self):
        manual = self.file("manual/adb.exe")
        running = self.file("running/MuMu Player 12/nx_main/adb.exe")
        registered = self.file("registered/MuMu Player 12/shell/adb.exe")
        scanned = self.file("scanned/MuMuPlayer-12.0/nx_main/adb.exe")
        program = self.file("app/adb.exe")
        system = self.file("system/adb.exe")
        self.file(self.bundle)
        self.process.return_value = [("MuMuNxMain.exe", str(running.parent / "MuMuNxMain.exe"))]
        self.registry.return_value = [registered.parent.parent]
        self.directories.return_value = [scanned.parent.parent]
        self.which.return_value = str(system)
        self.assertEqual(self.locate(manual).source, "manual")
        self.assertEqual(self.locate(manual).install_dir, str(running.parent.parent))
        for path, source in ((running, "mumu_process"), (registered, "mumu_registry"),
                             (scanned, "mumu_directory"), (program, "program"),
                             (system, "path"), (self.bundle, "bundled")):
            with self.subTest(source=source):
                result = self.locate("missing.exe")
                self.assertEqual((result.path, result.source), (str(path), source))
                path.unlink()
        self.assertIsNone(self.locate())

    def test_program_ancestor_precedes_path(self):
        parent = self.file("adb.exe")
        self.which.return_value = str(self.file("system/adb.exe"))
        self.assertEqual(self.locate().path, str(parent))

    def test_nested_version_and_legacy_candidates(self):
        for relative in ("nx_device/12.7.3/shell/adb.exe", "emulator/nemu/vmonitor/bin/adb_server.exe",
                         "emulator/nemu/vmonitor/bin/adb.exe", "shell/adb.exe", "nx_main/adb.exe"):
            with self.subTest(relative=relative):
                path = self.file("MuMu Player 12/" + relative)
                self.directories.return_value = [path.parents[len(Path(relative).parts) - 1]]
                self.assertEqual(self.locate().path, str(path))
                self.assertEqual(locator.find_mumu_install_dir(path), str(self.base / "MuMu Player 12"))
                path.unlink()

    def test_candidate_order_within_root(self):
        paths = [self.file("MuMu/" + value) for value in ("nx_main/adb.exe", "shell/adb.exe",
                 "nx_device/12/shell/adb.exe", "emulator/nemu/vmonitor/bin/adb_server.exe",
                 "emulator/nemu/vmonitor/bin/adb.exe")]
        for path in paths:
            self.assertEqual(locator._find_adb_in_root(self.base / "MuMu"), str(path))
            path.unlink()

    def test_process_names_case_insensitive_and_crashpad_filter(self):
        path = self.file("MuMu Player 12/nx_main/adb.exe")
        for name in locator.MUMU_PROCESSES:
            with self.subTest(name=name):
                self.process.return_value = [(name.upper(), str(path.parent / name))]
                self.assertEqual(self.locate().source, "mumu_process")
        other = self.file("Other/nx_main/adb.exe")
        self.process.return_value = [("crashpad_handler.exe", str(other.parent / "crashpad_handler.exe"))]
        self.assertIsNone(self.locate())

    def test_process_failure_safely_uses_bundle(self):
        self.file(self.bundle)
        self.process.side_effect = OSError("受控进程权限错误")
        result = self.locate()
        self.assertEqual(result.source, "bundled")
        self.assertIsNone(result.install_dir)

    def test_windows_api_failure_is_safe(self):
        with patch.object(locator.ctypes, "WinDLL", side_effect=OSError("受控 API 失败")), \
                patch.object(locator.os, "name", "nt"):
            # 调用原始函数，避免 setUp 的外边界 mock 掩盖 API 失败。
            self.assertEqual(ORIGINAL_PROCESSES(), [])

    def test_ctypes_process_enumeration_closes_handles(self):
        kernel = MagicMock()
        kernel.CreateToolhelp32Snapshot.return_value = 100
        kernel.OpenProcess.return_value = 200
        names = iter(["MUMUNXMAIN.EXE", "crashpad_handler.exe", "unrelated.exe"])

        def next_process(snapshot, pointer):
            try:
                pointer._obj.szExeFile = next(names)
                pointer._obj.th32ProcessID = 7
                return True
            except StopIteration:
                return False

        kernel.Process32FirstW.side_effect = next_process
        kernel.Process32NextW.side_effect = next_process
        def image(handle, flags, buffer, size):
            buffer.value = "C:/Other/nx_main/player.exe"
            return True
        kernel.QueryFullProcessImageNameW.side_effect = image
        with patch.object(locator.ctypes, "WinDLL", return_value=kernel), patch.object(locator.os, "name", "nt"):
            self.assertEqual(ORIGINAL_PROCESSES(), [("mumunxmain.exe", "C:/Other/nx_main/player.exe")])
        self.assertEqual(kernel.CloseHandle.call_count, 3)
        kernel.CloseHandle.assert_any_call(100)

    def test_invalid_snapshot_does_not_close_invalid_handle(self):
        kernel = MagicMock()
        kernel.CreateToolhelp32Snapshot.return_value = ctypes.c_void_p(-1).value
        with patch.object(locator.ctypes, "WinDLL", return_value=kernel), patch.object(locator.os, "name", "nt"):
            self.assertEqual(ORIGINAL_PROCESSES(), [])
        kernel.CloseHandle.assert_not_called()

    def test_directory_scan_spaces_netease_and_versions(self):
        cases = ("MuMu Player 12/nx_main/adb.exe", "mumu/MuMuPlayer-12.0/nx_main/adb.exe",
                 "Program Files/Netease/MuMu Player 12/nx_device/12/shell/adb.exe",
                 "Program Files (x86)/MuMu Player 12/shell/adb.exe")
        for relative in cases:
            with self.subTest(relative=relative):
                path = self.file("disk/" + relative)
                with patch.object(locator, "_drive_roots", return_value=[self.base / "disk"]), \
                        patch.dict(locator.os.environ, {"ProgramFiles": "", "ProgramFiles(x86)": ""}):
                    roots = list(ORIGINAL_DIRECTORIES())
                self.assertTrue(any(locator._find_adb_in_root(root) == str(path) for root in roots))
                path.unlink()

    def test_directory_depth_and_time_budget(self):
        path = self.file("disk/Netease/Netease/Netease/MuMu/nx_main/adb.exe")
        with patch.object(locator, "_drive_roots", return_value=[self.base / "disk"]), \
                patch.dict(locator.os.environ, {"ProgramFiles": "", "ProgramFiles(x86)": ""}):
            self.assertFalse(any(locator._find_adb_in_root(root) == str(path) for root in ORIGINAL_DIRECTORIES()))
            with patch.object(locator.time, "monotonic", side_effect=[0, 1]):
                self.assertEqual(list(ORIGINAL_DIRECTORIES()), [])

    def test_registry_derivation_all_fields_hives_and_views(self):
        path = self.file("Registry/MuMu Player 12/nx_main/adb.exe")
        root = path.parent.parent
        for hive in ("HKLM", "HKCU"):
            for prefix in ("SOFTWARE", "SOFTWARE\\WOW6432Node"):
                for field, value in (("InstallLocation", str(root)),
                                     ("DisplayIcon", '"' + str(path.parent / "MuMuPlayer.exe") + '",0'),
                                     ("UninstallString", str(root / "uninstall.exe") + " /S")):
                    with self.subTest(hive=hive, prefix=prefix, field=field):
                        registry = MagicMock(HKEY_LOCAL_MACHINE="HKLM", HKEY_CURRENT_USER="HKCU")
                        base = SimpleNamespace(kind="base")
                        item = SimpleNamespace(kind="item")
                        def open_key(parent, key):
                            if parent == hive and key == prefix + r"\Microsoft\Windows\CurrentVersion\Uninstall":
                                target = base
                            elif parent is base and key == "MuMuEntry":
                                target = item
                            else:
                                raise OSError("无此项")
                            context = MagicMock()
                            context.__enter__.return_value = target
                            return context
                        registry.OpenKey.side_effect = open_key
                        def enum_key(parent, index):
                            if index == 0:
                                return "MuMuEntry"
                            raise OSError("结束")
                        registry.EnumKey.side_effect = enum_key
                        def query(parent, key):
                            values = {"DisplayName": "NetEase mUmU Player 12", field: value}
                            if key not in values:
                                raise OSError("缺失值")
                            return values[key], 1
                        registry.QueryValueEx.side_effect = query
                        with patch.object(locator, "winreg", registry):
                            self.assertEqual(list(ORIGINAL_REGISTRY()), [str(root)])

    def test_registry_missing_or_inaccessible_is_safe(self):
        with patch.object(locator, "winreg", None):
            self.assertEqual(list(ORIGINAL_REGISTRY()), [])
        registry = MagicMock()
        registry.OpenKey.side_effect = PermissionError("无读取权限")
        with patch.object(locator, "winreg", registry):
            self.assertEqual(list(ORIGINAL_REGISTRY()), [])

    def test_resource_paths_source_and_pyinstaller(self):
        with patch.object(locator.sys, "frozen", False, create=True):
            self.assertEqual(ORIGINAL_BUNDLE(), Path(locator.__file__).resolve().parents[1] / "tools/adb/adb.exe")
        with patch.object(locator.sys, "frozen", True, create=True), \
                patch.object(locator.sys, "_MEIPASS", str(self.base / "_internal"), create=True):
            self.assertEqual(ORIGINAL_BUNDLE(), self.base / "_internal/tools/adb/adb.exe")

    def test_no_subprocess_or_network_for_locator(self):
        self.file(self.bundle)
        with patch("subprocess.Popen", side_effect=AssertionError("禁止启动进程")), \
                patch("socket.socket", side_effect=AssertionError("禁止网络")):
            self.assertEqual(self.locate().source, "bundled")

    def test_ui_missing_adb_preserves_original_message(self):
        from ui.adb_touch_form import AdbTouchForm
        form = SimpleNamespace()
        worker = MagicMock(current_path="", history_ports=())
        with patch("ui.adb_touch_form.locate_adb", return_value=None):
            AdbTouchForm._do_auto_detect(form, worker)
        worker.detect_error.emit.assert_called_once_with(
            "未找到 adb.exe，请手动指定路径或先启动 MuMu 模拟器。", "warning")
        worker.detect_finished.emit.assert_called_once_with([])

    def test_ui_bundle_without_mumu_uses_default_ports(self):
        from ui.adb_touch_form import AdbTouchForm
        result = locator.AdbLocation(str(self.bundle), "bundled")
        form = SimpleNamespace(_find_mumu_install_dir=lambda path: None,
            _listening_tcp_ports=lambda: {"16384"},
            _query_mumu12_instances=MagicMock(side_effect=AssertionError("不应运行 MuMuManager")),
            _output_text=AdbTouchForm._output_text)
        worker = MagicMock(current_path="", history_ports=("16384",))
        with patch("ui.adb_touch_form.locate_adb", return_value=result), \
                patch("ui.adb_touch_form.subprocess.run", side_effect=AssertionError("禁止设备命令")):
            AdbTouchForm._do_auto_detect(form, worker)
        worker.detect_error.emit.assert_not_called()
        self.assertEqual(worker.detect_finished.emit.call_args.args[0][0].port, "16384")

    def test_ui_preserves_discovered_install_root_and_source(self):
        from ui.adb_touch_form import AdbTouchForm, MuMuPortItem
        root = str(self.base / "MuMu Player 12")
        result = locator.AdbLocation(str(self.bundle), "manual", root)
        form = SimpleNamespace(_resolve_adb_path=lambda current: result.path, _adb_location=result,
            _find_mumu_install_dir=lambda path: None, _find_mumu_manager_path=lambda path: "manager.exe",
            _query_mumu12_instances=MagicMock(return_value=[MuMuPortItem("16416", "实例")]),
            _output_text=AdbTouchForm._output_text)
        form._listening_tcp_ports = lambda: {"16416"}
        worker = MagicMock(current_path="", history_ports=())
        with patch("ui.adb_touch_form.locate_adb", return_value=result), \
                patch("ui.adb_touch_form.subprocess.run", side_effect=AssertionError("禁止设备命令")):
            AdbTouchForm._do_auto_detect(form, worker)
        form._query_mumu12_instances.assert_called_once_with(root)
        self.assertEqual(AdbTouchForm._adb_source_message(form), result.message + "；")


class PanelAutoDetectionTests(unittest.TestCase):
    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        from ui import adb_touch_form
        self.ui = adb_touch_form
        self.app = QApplication.instance() or QApplication([])
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.thread = MagicMock()
        self.thread.is_alive.return_value = False
        self.threads = self.stack.enter_context(patch.object(adb_touch_form.threading, "Thread", return_value=self.thread))
        self.connect = self.stack.enter_context(patch.object(adb_touch_form.AdbTouchForm, "_connect_adb"))
        self.form = adb_touch_form.AdbTouchForm()
        self.addCleanup(self.form.deleteLater)
        self.addCleanup(self.form.close)

    def open(self):
        self.form.show()
        self.app.processEvents()

    def test_show_starts_one_background_detection_without_connecting(self):
        self.open()
        self.threads.assert_called_once()
        self.thread.start.assert_called_once()
        self.assertIs(self.threads.call_args.kwargs["target"].__self__, self.form)
        self.form._on_auto_detect()
        self.form._auto_detect_on_show()
        self.thread.start.assert_called_once()
        self.connect.assert_not_called()
        self.assertFalse(self.form._is_connected)

    def test_manual_path_previous_instance_and_port_are_preserved(self):
        self.form._adb_path_lbl.setText(__file__)
        self.form._port_edit.setText("16416")
        self.open()
        worker = self.form._worker
        self.assertEqual(worker.current_path, __file__)
        worker.location_found.emit(locator.AdbLocation(__file__, "manual"))
        worker.adb_path_found.emit("another.exe")
        worker.detect_finished.emit([self.ui.MuMuPortItem("16384", "首个"), self.ui.MuMuPortItem("16416", "上次")])
        self.assertEqual(self.form._adb_path_lbl.text(), __file__)
        self.assertEqual(self.form._port_edit.text(), "16416")
        self.assertIn("上次", self.form._port_combo.currentText())
        self.assertIn("2 个模拟器实例", self.form._status_label.text())
        self.assertIn("手动指定", self.form._status_label.text())
        self.connect.assert_not_called()

    def test_empty_result_keeps_saved_ports(self):
        self.form._port_combo.addItem("16416", self.ui.MuMuPortItem("16416", "历史"))
        self.form._port_edit.setText("16416")
        self.open()
        before = self.form._port_combo.count()
        self.form._worker.detect_finished.emit([])
        self.assertEqual(self.form._port_combo.count(), before)
        self.assertEqual(self.form._port_edit.text(), "16416")
        self.assertIn("未检测到运行中的 MuMu", self.form._status_label.text())

    def test_closed_and_stale_callbacks_are_discarded(self):
        self.open()
        worker = self.form._worker
        self.form.close()
        before = self.form._status_label.text()
        worker.location_found.emit(locator.AdbLocation("stale.exe", "bundled"))
        worker.adb_path_found.emit("stale.exe")
        worker.status_update.emit("过期状态")
        worker.detect_error.emit("过期错误", "warning")
        worker.detect_finished.emit([self.ui.MuMuPortItem("12345")])
        self.assertEqual(self.form._status_label.text(), before)
        self.assertEqual(self.form._adb_path_lbl.text(), "")
        self.open()
        worker.adb_path_found.emit("stale_after_reopen.exe")
        worker.detect_finished.emit([self.ui.MuMuPortItem("12345")])
        self.assertEqual(self.form._adb_path_lbl.text(), "")
        self.assertNotEqual(self.form._port_edit.text(), "12345")

    def test_reopen_cooldown_manual_retry_and_expiration(self):
        self.open()
        self.form._worker.detect_finished.emit([])
        self.form.close()
        self.open()
        self.thread.start.assert_called_once()
        self.form._on_auto_detect()
        self.assertEqual(self.thread.start.call_count, 2)
        self.form._worker.detect_finished.emit([])
        self.form.close()
        self.form._detect_last_finished -= 31
        self.open()
        self.assertEqual(self.thread.start.call_count, 3)

    def test_close_reopen_does_not_overlap_live_worker(self):
        self.open()
        self.thread.is_alive.return_value = True
        self.form.close()
        self.open()
        self.form._on_auto_detect()
        self.thread.start.assert_called_once()
        self.thread.is_alive.return_value = False
        self.form._auto_detect_on_show()
        self.assertEqual(self.thread.start.call_count, 2)

    def test_worker_only_reads_ports_and_does_not_start_capture(self):
        self.open()
        worker = self.form._worker
        location = locator.AdbLocation(__file__, "bundled")
        with patch.object(self.ui, "locate_adb", return_value=location), \
                patch.object(self.form, "_find_mumu_install_dir", return_value=None), \
                patch.object(self.form, "_listening_tcp_ports", return_value={"16384"}), \
                patch.object(self.ui.subprocess, "run", side_effect=AssertionError("禁止设备操作")), \
                patch.object(self.ui, "AdbClient", side_effect=AssertionError("禁止开始采集")):
            self.form._do_auto_detect(worker)
        self.assertEqual(self.form._port_edit.text(), "16384")
        self.assertIn("内置", self.form._status_label.text())
        self.connect.assert_not_called()

    def test_only_running_listening_instances_are_listed(self):
        self.open()
        location = locator.AdbLocation(__file__, "mumu_process", "mock-install")
        with patch.object(self.ui, "locate_adb", return_value=location), \
                patch.object(self.form, "_find_mumu_manager_path", return_value="mock-manager"), \
                patch.object(self.form, "_query_mumu12_instances", return_value=[
                    self.ui.MuMuPortItem("16384", "运行"), self.ui.MuMuPortItem("16416", "已关")]), \
                patch.object(self.form, "_listening_tcp_ports", return_value={"16384"}):
            self.form._do_auto_detect(self.form._worker)
        self.assertEqual(self.form._port_combo.count(), 1)
        self.assertIn("运行", self.form._port_combo.currentText())

    def test_tcp_table_filters_remote_and_non_listening_rows(self):
        api = MagicMock()
        rows = ((2, 0x0100007f, 16384), (2, 0, 16416), (5, 0x0100007f, 16448), (2, 0x0101a8c0, 16480))
        payload = len(rows).to_bytes(4, "little") + b"".join(
            state.to_bytes(4, "little") + address.to_bytes(4, "little") + port.to_bytes(2, "big") + b"\0" * 10
            for state, address, port in rows)
        def query(buffer, size, order):
            size._obj.value = len(payload)
            if buffer is None:
                return 122
            ctypes.memmove(buffer, payload, len(payload))
            return 0
        api.side_effect = query
        with patch.object(ctypes, "WinDLL", return_value=SimpleNamespace(GetTcpTable=api)):
            self.assertEqual(self.form._listening_tcp_ports(), {"16384", "16416"})


ORIGINAL_PROCESSES = locator._iter_mumu_processes
ORIGINAL_REGISTRY = locator._registry_roots
ORIGINAL_DIRECTORIES = locator._directory_roots
ORIGINAL_BUNDLE = locator.bundled_adb_path

if __name__ == "__main__":
    unittest.main()
