"""同步和日志代际回归，全部使用离线替身。"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from adb.client import AdbClient
from core.config_manager import ConfigManager
from infra import log_service
from infra.mumu_sync import MuMuKeymapSyncService, _KeymapEventHandler


class FakeTimer:
    instances = []

    def __init__(self, interval, function, args=()):
        self.function, self.args = function, args
        self.cancelled = False
        self.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.function(*self.args)


class LifecycleMigrationTests(unittest.TestCase):
    def test_log_shutdown_and_reinitialize_reject_old_callback(self):
        log_service.shutdown()
        FakeTimer.instances = []
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(log_service.threading, "Timer", FakeTimer):
            try:
                log_service.initialize(directory)
                old = FakeTimer.instances[-1]
                log_service.shutdown()
                self.assertTrue(old.cancelled)
                log_service.initialize(directory)
                count = len(FakeTimer.instances)
                old.fire()
                self.assertEqual(len(FakeTimer.instances), count)
                FakeTimer.instances[-1].fire()
                self.assertEqual(len(FakeTimer.instances), count + 1)
                log_service.shutdown()
                FakeTimer.instances[-1].fire()
                self.assertEqual(len(FakeTimer.instances), count + 1)
            finally:
                log_service.shutdown()

    def test_log_uses_supplied_exception_traceback(self):
        try:
            raise ValueError("原始异常")
        except ValueError as ex:
            original = ex
        with mock.patch.object(log_service, "_write") as write:
            log_service.error_ex("test", original)
        message = write.call_args.args[2]
        self.assertIn("test_log_uses_supplied_exception_traceback", message)
        self.assertIn("ValueError: 原始异常", message)
        self.assertNotIn("NoneType: None", message)

    def test_sync_path_case_and_separator_dedup(self):
        service = MuMuKeymapSyncService()
        paths = []
        service.on_keymap_changed(paths.append)
        with mock.patch.object(service, "_diag_log"):
            with tempfile.TemporaryDirectory() as directory:
                old_path = os.path.join(directory, "官服.json")
                new_path = os.path.join(directory, "官服-a.json")
                service._try_notify_path(old_path, new_path)
                service._try_notify_path(
                    old_path.replace("\\", "/").swapcase(),
                    new_path.replace("\\", "/").swapcase(),
                )
        self.assertEqual(paths, [new_path])

    def test_stopped_sync_rejects_restart_and_old_event(self):
        service = MuMuKeymapSyncService()
        service._running = service._is_enabled = True
        service._keymap_dir = "test"
        handler = _KeymapEventHandler(service)
        with mock.patch.object(service, "_diag_log"), \
                mock.patch("infra.mumu_sync.threading.Timer", FakeTimer), \
                mock.patch.object(service, "_start_watcher") as start, \
                mock.patch.object(service, "_on_file_event") as event:
            service._on_watcher_error()
            timer = service._restart_timer
            service.stop()
            self.assertTrue(timer.cancelled)
            service._running = True
            timer.fire()
            handler.on_modified(SimpleNamespace(is_directory=False, src_path="a.json"))
            start.assert_not_called()
            event.assert_not_called()

    def test_poll_restart_uses_distinct_stop_events(self):
        service = MuMuKeymapSyncService()
        with mock.patch("infra.mumu_sync.threading.Thread"):
            service._start_poll_timer()
            old = service._poll_stop_event
            service._start_poll_timer()
            self.assertIsNot(old, service._poll_stop_event)
            self.assertTrue(old.is_set())
            self.assertFalse(service._poll_stop_event.is_set())
            service._stop_poll_timer()

    def test_null_strings_and_keyboard_defaults(self):
        config = ConfigManager(design_time=True)
        config._values.update({key: None for key in ("FX", "FY", "JsonFolderPath", "Resolution4String", "AdbPort", "AdbPath", "AdbPortsHistory", "FindKey", "ResetKey")})
        for name in ("fx", "fy", "json_folder_path", "resolution4_string", "adb_port", "adb_path", "adb_ports_history"):
            self.assertEqual(getattr(config, name), "")
        handler = SimpleNamespace()
        config.load_into_keyboard_handler(handler)
        self.assertEqual((handler.find_key, handler.reset_key), ("D", "F"))

    def test_adb_decode_replace_parameters_without_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "adb.exe"
            path.touch()
            client = AdbClient(str(path), "offline")
            with mock.patch("adb.client.subprocess.run", return_value=SimpleNamespace(stdout="ok", stderr="")) as run:
                self.assertEqual(client.execute("version", 1000), "ok")
                self.assertEqual(run.call_args.kwargs["errors"], "replace")
            with mock.patch("adb.client.subprocess.Popen") as popen:
                client.start_getevent()
                self.assertEqual(popen.call_args.kwargs["errors"], "replace")


if __name__ == "__main__":
    unittest.main()
