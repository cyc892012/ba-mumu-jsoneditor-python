"""文件监控器 — 对应 C# FileMonitor

基于 watchdog 监听单个文件的 LastWrite/Size 变更，带 2000ms 防抖和错误自动重启。
"""
import os
import threading
import time
from typing import Callable, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from infra.log_service import LogService


class _ChangeHandler(FileSystemEventHandler):
    """watchdog 事件处理器"""

    def __init__(self, watched_file: str, callback: Callable[[], None]):
        self._watched_file = os.path.normpath(watched_file).lower()
        self._callback = callback

    def on_modified(self, event: FileSystemEvent) -> None:
        self._forward(event)

    def on_created(self, event: FileSystemEvent) -> None:
        self._forward(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._forward(event, getattr(event, "dest_path", ""))

    def _forward(self, event: FileSystemEvent, destination: str = "") -> None:
        if event.is_directory:
            return
        src = getattr(event, "src_path", "")
        paths = [os.path.normpath(path).lower() for path in (src, destination) if path]
        if self._watched_file not in paths:
            return
        self._callback()


class FileMonitor:
    """文件监控器 — 对应 C# FileMonitor"""

    DEBOUNCE_MS: int = 2000

    def __init__(self):
        self._observer: Optional[Observer] = None
        self._watched_file: str = ""
        self._watched_dir: str = ""
        self._debounce_lock: threading.Lock = threading.Lock()
        self._last_event_time: float = 0.0
        self._file_changed_callbacks: list[Callable[[], None]] = []
        self._disposed: bool = False
        self._lifecycle_lock = threading.RLock()
        self._generation = 0

    def on_file_changed(self, callback: Callable[[], None]) -> None:
        self._file_changed_callbacks.append(callback)

    def watch(self, file_path: str) -> None:
        """开始监控文件"""
        self.stop()
        with self._lifecycle_lock:
            if self._disposed or not os.path.isfile(file_path):
                return
            self._watched_file = file_path
            self._watched_dir = os.path.dirname(file_path) or "."
            self._last_event_time = 0.0
            self._start_observer(file_path, self._generation)

    def _start_observer(self, file_path: str, generation: int) -> None:
        """调用方持有生命周期锁；回调及恢复都绑定到当前监控代次。"""
        observer = Observer()
        try:
            handler = _ChangeHandler(file_path, lambda: self._on_file_changed(generation))
            observer.schedule(handler, os.path.dirname(file_path) or ".", recursive=False)
            observer.start()
            self._observer = observer

            def _monitor() -> None:
                try:
                    observer.join()
                except Exception:
                    pass
                with self._lifecycle_lock:
                    if (self._disposed or generation != self._generation
                            or self._observer is not observer):
                        return
                    self._observer = None
                    self._restart_observer(file_path)

            threading.Thread(target=_monitor, daemon=True, name="FileMonitorWatcherMon").start()
            LogService.debug("FileMonitor", f"开始监控: {file_path}")
        except Exception as ex:
            observer.stop()
            if observer.is_alive():
                observer.join(1.0)
            LogService.error_ex("FileMonitor", ex, "启动文件监控失败")

    def stop(self) -> None:
        """停止监控"""
        with self._lifecycle_lock:
            self._generation += 1
            observer, self._observer = self._observer, None
            self._watched_file = self._watched_dir = ""
        if observer is not None:
            try:
                observer.stop()
                if observer is not threading.current_thread():
                    observer.join(1.0)
            except Exception:
                pass

    def _restart_observer(self, file_path: str) -> None:
        """重建 Observer 并重新开始监控同一文件。"""
        with self._lifecycle_lock:
            if (self._disposed or file_path != self._watched_file
                    or self._observer is not None or not os.path.isfile(file_path)):
                return
            self._last_event_time = 0.0
            self._start_observer(file_path, self._generation)

    def dispose(self) -> None:
        with self._lifecycle_lock:
            if self._disposed:
                return
            self._disposed = True
        self.stop()

    def _on_file_changed(self, generation=None) -> None:
        """文件变更事件处理（带防抖）"""
        with self._lifecycle_lock:
            if self._disposed or (generation is not None and generation != self._generation):
                return
        with self._debounce_lock:
            now = time.monotonic()
            if (now - self._last_event_time) * 1000 < self.DEBOUNCE_MS:
                return
            self._last_event_time = now

        for cb in self._file_changed_callbacks:
            try:
                cb()
            except Exception as ex:
                LogService.error_ex("FileMonitor", ex, "回调异常")
