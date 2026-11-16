"""文件监控的停止、切换及原子替换回归；不启动真实监听。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from infra.file_monitor import FileMonitor, _ChangeHandler


class FakeObserver:
    instances = []

    def __init__(self):
        self.alive = False
        self.instances.append(self)

    def schedule(self, handler, directory, recursive=False):
        self.handler = handler

    def start(self):
        self.alive = True

    def stop(self):
        self.alive = False

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return self.alive


class FakeThread:
    instances = []

    def __init__(self, target, **kwargs):
        self.target = target
        self.instances.append(self)

    def start(self):
        pass


class FileMonitorLifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.a = self.root / "a.json"
        self.b = self.root / "b.json"
        self.a.write_text("{}", encoding="utf-8")
        self.b.write_text("{}", encoding="utf-8")
        FakeObserver.instances = []
        FakeThread.instances = []
        for patcher in (mock.patch("infra.file_monitor.Observer", FakeObserver),
                        mock.patch("infra.file_monitor.threading.Thread", FakeThread)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.monitor = FileMonitor()
        self.addCleanup(self.monitor.dispose)
        self.seen = []
        self.monitor.on_file_changed(lambda: self.seen.append(True))

    def test_stop_does_not_restart_observer(self):
        self.monitor.watch(str(self.a))
        thread = FakeThread.instances[-1]
        self.monitor.stop()
        thread.target()
        self.assertEqual(len(FakeObserver.instances), 1)
        self.assertIsNone(self.monitor._observer)

    def test_delayed_old_exit_cannot_replace_new_watch(self):
        self.monitor.watch(str(self.a))
        old_thread = FakeThread.instances[-1]
        old_handler = self.monitor._observer.handler
        self.monitor.watch(str(self.b))
        current = self.monitor._observer
        old_thread.target()
        old_handler.on_modified(SimpleNamespace(is_directory=False, src_path=str(self.a)))
        self.assertIs(self.monitor._observer, current)
        self.assertEqual(len(FakeObserver.instances), 2)
        self.assertEqual(self.seen, [])

    def test_unexpected_observer_exit_restarts_current_watch(self):
        self.monitor.watch(str(self.a))
        old = self.monitor._observer
        old.alive = False
        thread = FakeThread.instances[-1]
        thread.target()
        self.assertIsNot(self.monitor._observer, old)
        self.assertTrue(self.monitor._observer.alive)
        thread.target()
        self.assertEqual(len(FakeObserver.instances), 2)

    def test_dispose_rejects_watch_and_delayed_callbacks(self):
        self.monitor.watch(str(self.a))
        old_handler = self.monitor._observer.handler
        old_thread = FakeThread.instances[-1]
        self.monitor.dispose()
        self.monitor.watch(str(self.b))
        old_thread.target()
        old_handler.on_created(SimpleNamespace(is_directory=False, src_path=str(self.a)))
        self.assertIsNone(self.monitor._observer)
        self.assertEqual(len(FakeObserver.instances), 1)
        self.assertEqual(self.seen, [])

    def test_atomic_move_to_watched_file_notifies(self):
        seen = []
        handler = _ChangeHandler(str(self.a), lambda: seen.append(True))
        handler.on_moved(SimpleNamespace(is_directory=False, src_path=str(self.root / "tmp"), dest_path=str(self.a)))
        self.assertEqual(seen, [True])

    def test_created_file_notifies_and_other_paths_do_not(self):
        seen = []
        handler = _ChangeHandler(str(self.a), lambda: seen.append(True))
        handler.on_created(SimpleNamespace(is_directory=False, src_path=str(self.a)))
        handler.on_modified(SimpleNamespace(is_directory=True, src_path=str(self.a)))
        handler.on_modified(SimpleNamespace(is_directory=False, src_path=str(self.b)))
        handler.on_moved(SimpleNamespace(is_directory=False, src_path=str(self.b), dest_path=str(self.root / "tmp")))
        self.assertEqual(seen, [True])

    def test_burst_events_debounce_with_monotonic_clock(self):
        self.monitor.watch(str(self.a))
        handler = self.monitor._observer.handler
        event = SimpleNamespace(is_directory=False, src_path=str(self.a))
        with mock.patch("infra.file_monitor.time.monotonic", side_effect=[100, 100.1, 102.1]):
            handler.on_created(event)
            handler.on_modified(event)
            handler.on_modified(event)
        self.assertEqual(self.seen, [True, True])


if __name__ == "__main__":
    unittest.main()
