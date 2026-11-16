"""MuMu 键位同步服务 — 对应 C# MuMuKeymapSyncService

双通道检测：watchdog (on *.json Created/Modified/Deleted) + threading.Event 轮询 (1s 间隔)
- 600ms 防抖 per-file
- 路径去重 (_last_known_path)
- 线程安全回调
"""
import json
import os
import threading
import time
from typing import Callable, Dict, List, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from infra.log_service import LogService


class _KeymapEventHandler(FileSystemEventHandler):
    """watchdog 事件处理器 — 转发给 MuMuKeymapSyncService"""

    def __init__(self, service: "MuMuKeymapSyncService"):
        self._service = service
        self._generation = service._generation

    def _forward(self, path):
        if self._generation == self._service._generation and self._service._running:
            self._service._on_file_event(path)

    def on_modified(self, event):
        if not event.is_directory and getattr(event, "src_path", "").lower().endswith(".json"):
            self._forward(event.src_path)

    def on_created(self, event):
        if not event.is_directory and getattr(event, "src_path", "").lower().endswith(".json"):
            self._forward(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory and getattr(event, "src_path", "").lower().endswith(".json"):
            self._forward(event.src_path)

    def on_moved(self, event):
        if not event.is_directory and getattr(event, "dest_path", "").lower().endswith(".json"):
            self._forward(event.dest_path)


class MuMuKeymapSyncService:
    """MuMu 键位同步服务 — 对应 C# MuMuKeymapSyncService

    监控 keymap 目录的 .json 索引文件变更，检测 "Current" 字段中的方案路径变化。
    """

    DEBOUNCE_MS: int = 600
    POLL_INTERVAL_MS: int = 1000
    WATCHER_RESTART_DELAY_MS: int = 2000

    def __init__(self):
        """初始化 watcher + 轮询基础设施。"""
        self._watcher: Optional[Observer] = None
        self._poll_stop_event = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None

        self._debounce_cache: Dict[str, float] = {}
        self._last_known_path: Dict[str, str] = {}
        self._sync_lock = threading.Lock()

        self._is_enabled: bool = False
        self._disposed: bool = False
        self._watcher_error_triggered: bool = False
        self._keymap_dir: str = ""
        self._generation = 0
        self._running = False
        self._restart_timer = None
        self._lifecycle_lock = threading.RLock()

        self._keymap_changed_callbacks: List[Callable[[str], None]] = []
        self._callbacks_lock = threading.Lock()

    # ── 公共 API ──

    def on_keymap_changed(self, callback: Callable[[str], None]) -> None:
        """注册键位变更回调。

        Args:
            callback: 回调函数，参数为变更的方案文件路径
        """
        with self._callbacks_lock:
            self._keymap_changed_callbacks.append(callback)

    def _monitoring_alive(self) -> bool:
        """watchdog 与轮询线程是否都还活着（用于避免无意义地反复重启）"""
        return (self._watcher is not None and self._watcher.is_alive()
                and self._poll_thread is not None and self._poll_thread.is_alive())

    def start(self, keymap_dir: str) -> None:
        """启动监控（幂等）。

        启动期 ``_try_auto_sync()`` 会被多处调用，且 ``enable()`` 内部还会再调一次
        ``start()``。旧实现每次都 stop→start 两遍：反复 join 线程、重复打印目录快照、
        旧 Observer 的存活监控线程还会误报「Watcher 错误, 2s后重启」并再拉起一个
        Observer。同一目录且监控仍存活时直接返回即可。
        """
        with self._lifecycle_lock:
            if self._disposed:
                return
            same_dir = self._path_key(self._keymap_dir) == self._path_key(keymap_dir)
            if same_dir and self._running and self._monitoring_alive():
                return
            self.stop()
            self._keymap_dir = keymap_dir
            if not keymap_dir or not os.path.isdir(keymap_dir):
                return
            self._running = True
            self._diag_log(f"启动监控: {keymap_dir}")
            self._print_directory_state("启动时")
            self._start_watcher(keymap_dir)
            self._start_poll_timer()

    def stop(self) -> None:
        """停止所有监控。"""
        self._diag_log("停止所有监控")
        with self._lifecycle_lock:
            self._generation += 1
            self._running = False
            if self._restart_timer is not None:
                self._restart_timer.cancel()
                self._restart_timer = None
            self._stop_poll_timer()
            self._stop_watcher()

    @staticmethod
    def _path_key(path: str) -> str:
        return os.path.normpath(path.replace("/", "\\")).casefold() if path else ""

    def enable(self) -> None:
        """启用同步。清空防抖缓存，首次启用时预加载当前目录状态。"""
        was_enabled = self._is_enabled
        already_running = bool(self._keymap_dir) and self._monitoring_alive()
        self._is_enabled = True
        self._diag_log("同步已启用")
        with self._sync_lock:
            self._debounce_cache.clear()

        if was_enabled and already_running:
            # 已经启用且监控正常：不必再全量扫描一遍目录（旧实现每次都扫 138 个 json）
            return
        if self._keymap_dir:
            self._preload_last_known_paths()
            self.start(self._keymap_dir)

    def disable(self) -> None:
        """禁用同步。"""
        self._is_enabled = False
        self._diag_log("同步已禁用")

    def immediate_poll(self) -> None:
        """触发一次即时轮询。"""
        if not self._is_enabled or not self._keymap_dir or not os.path.isdir(self._keymap_dir):
            return
        self._poll_once()

    # ── 静态方法 ──

    @staticmethod
    def read_current_path(index_file_path: str) -> str:
        """读取索引 JSON 中 "Current" 字段的方案路径。

        重试最多 5 次（间隔 200ms），处理文件被锁定的情况。

        Args:
            index_file_path: 索引文件完整路径

        Returns:
            当前方案路径，读取失败或不存在时返回空字符串
        """
        for i in range(5):
            try:
                with open(index_file_path, "r", encoding="utf-8-sig") as f:
                    content = f.read()

                # 快速检查：不含 "Current" 则跳过 JSON 解析
                if '"Current"' not in content:
                    return ""

                data = json.loads(content)
                current = data.get("Current")
                if not isinstance(current, dict) or not current:
                    return ""

                # 取第一个属性的 path 值
                first_key = next(iter(current))
                first_entry = current[first_key]
                if not isinstance(first_entry, dict):
                    return ""

                path = first_entry.get("path")
                if not isinstance(path, str) or not path:
                    return ""

                # 过滤 sanitary- 前缀（MuMu 临时条目）
                if path.startswith("sanitary-"):
                    return ""

                # 统一路径分隔符
                return path.replace("/", "\\")

            except (OSError, PermissionError):
                if i < 4:
                    time.sleep(0.2)
            except (json.JSONDecodeError, ValueError, StopIteration):
                if i < 4:
                    time.sleep(0.2)
                else:
                    return ""
            except Exception:
                return ""

        return ""

    # ── 内部方法 ──

    def _on_file_event(self, file_path: str) -> None:
        """watchdog 文件事件处理（带防抖）。"""
        LogService.debug("MuMuKeymapSync", f"文件事件: {file_path}")
        self._diag_log(f"文件事件: {file_path}")

        if not self._is_enabled:
            return
        if not file_path:
            return

        with self._sync_lock:
            now = time.time()
            key = self._path_key(file_path)
            last = self._debounce_cache.get(key)
            if last is not None and (now - last) * 1000 < self.DEBOUNCE_MS:
                return
            self._debounce_cache[key] = now

        self._process_file(file_path)

    def _process_file(self, file_path: str) -> None:
        """处理单个文件变更。"""
        path = self.read_current_path(file_path)
        if not path:
            return
        self._try_notify_path(file_path, path)

    def _try_notify_path(self, index_file: str, path: str) -> None:
        """比较路径并通知回调（去重）。"""
        with self._sync_lock:
            key = self._path_key(index_file)
            last_path = self._last_known_path.get(key)
            if last_path is not None and self._path_key(last_path) == self._path_key(path):
                return
            self._last_known_path[key] = path

        LogService.debug("MuMuKeymapSync", f"检测到变更: {index_file} -> {path}")
        self._diag_log(f"检测到变更: {index_file} -> {path}")

        # 通知所有回调
        with self._callbacks_lock:
            callbacks = list(self._keymap_changed_callbacks)

        for cb in callbacks:
            try:
                cb(path)
            except Exception as ex:
                LogService.error_ex("MuMuKeymapSync", ex, "回调异常")

    def _poll_once(self, generation=None) -> None:
        """执行一次轮询扫描。"""
        if not self._keymap_dir or not os.path.isdir(self._keymap_dir):
            return

        try:
            files = [
                f for f in os.listdir(self._keymap_dir)
                if f.lower().endswith(".json")
                and os.path.isfile(os.path.join(self._keymap_dir, f))
            ]
        except OSError as ex:
            LogService.error_ex("MuMuKeymapSync", ex, "轮询列目录异常")
            return

        watcher_alive = self._watcher is not None

        for file_name in files:
            if not self._is_enabled or (generation is not None and generation != self._generation):
                return

            file_path = os.path.join(self._keymap_dir, file_name)
            path = self.read_current_path(file_path)
            if not path:
                continue
            if generation is not None and generation != self._generation:
                return

            with self._sync_lock:
                key = self._path_key(file_path)
                last_path = self._last_known_path.get(key)
                if last_path is not None and self._path_key(last_path) == self._path_key(path):
                    continue

                now = time.time()
                last_debounce = self._debounce_cache.get(key)
                if last_debounce is not None and (now - last_debounce) * 1000 < self.DEBOUNCE_MS:
                    continue

            LogService.debug("MuMuKeymapSync", f"轮询检测到变更: {file_path} -> {path}")
            self._diag_log(f"轮询检测到变更: {file_path} -> {path}")
            self._try_notify_path(file_path, path)

        if not watcher_alive and not self._watcher_error_triggered:
            self._watcher_error_triggered = True
            LogService.warn("MuMuKeymapSync", "警告: Watcher 已失效, 只有轮询在工作!")
            self._on_watcher_error()

    def _poll_loop(self, stop_event=None, generation=None) -> None:
        """轮询线程主循环。"""
        stop_event = self._poll_stop_event if stop_event is None else stop_event
        generation = self._generation if generation is None else generation
        while not stop_event.wait(self.POLL_INTERVAL_MS / 1000.0):
            if generation != self._generation:
                return
            if not self._is_enabled:
                continue
            try:
                self._poll_once(generation)
            except Exception as ex:
                LogService.error_ex("MuMuKeymapSync", ex, "轮询异常")

    def _preload_last_known_paths(self) -> None:
        """预加载当前目录中所有 .json 文件的 "Current" 路径。"""
        if not self._keymap_dir or not os.path.isdir(self._keymap_dir):
            return

        try:
            files = [
                f for f in os.listdir(self._keymap_dir)
                if f.lower().endswith(".json")
                and os.path.isfile(os.path.join(self._keymap_dir, f))
            ]
        except OSError:
            return

        with self._sync_lock:
            for file_name in files:
                file_path = os.path.join(self._keymap_dir, file_name)
                path = self.read_current_path(file_path)
                if path:
                    self._last_known_path[self._path_key(file_path)] = path

    def _start_watcher(self, keymap_dir: str) -> None:
        """启动 watchdog 文件系统监控。"""
        self._watcher_error_triggered = False
        try:
            handler = _KeymapEventHandler(self)
            self._watcher = Observer()
            self._watcher.schedule(handler, keymap_dir, recursive=False)
            self._watcher.start()
            LogService.debug("MuMuKeymapSync", f"Watcher已启动: {keymap_dir}")
            self._diag_log(f"Watcher已启动: {keymap_dir}")

            # 启动 watcher 存活监控线程
            watcher_ref = self._watcher
            generation = self._generation

            def _monitor_watcher() -> None:
                try:
                    watcher_ref.join()
                except Exception:
                    pass
                with self._lifecycle_lock:
                    if (generation == self._generation and self._running
                            and self._watcher is watcher_ref and not self._disposed
                            and not self._watcher_error_triggered):
                        self._watcher_error_triggered = True
                        self._on_watcher_error()

            monitor_thread = threading.Thread(
                target=_monitor_watcher,
                daemon=True,
                name="MuMuSyncWatcherMon",
            )
            monitor_thread.start()
        except Exception as ex:
            LogService.error_ex("MuMuKeymapSync", ex, "启动Watcher失败")
            self._diag_log(f"启动Watcher失败: {ex}")

    def _stop_watcher(self) -> None:
        """停止 watchdog 文件系统监控。"""
        if self._watcher is not None:
            watcher = self._watcher
            self._watcher = None
            try:
                watcher.stop()
                if threading.current_thread() is not watcher:
                    watcher.join(1.0)
            except Exception:
                pass
            self._diag_log("Watcher已停止")
            LogService.debug("MuMuKeymapSync", "Watcher已停止")

    def _on_watcher_error(self) -> None:
        """Watcher 错误处理：2s 后尝试重启。"""
        if self._disposed or not self._running:
            return
        LogService.warn("MuMuKeymapSync", "Watcher错误, 2s后重启")
        self._diag_log("Watcher错误, 2s后重启")
        self._stop_watcher()
        generation = self._generation

        def _restart() -> None:
            with self._lifecycle_lock:
                if (generation == self._generation and self._running and self._is_enabled
                        and self._keymap_dir and not self._disposed):
                    self._diag_log("尝试重启Watcher")
                    self._start_watcher(self._keymap_dir)

        restart_timer = threading.Timer(
            self.WATCHER_RESTART_DELAY_MS / 1000.0, _restart
        )
        restart_timer.daemon = True
        if self._restart_timer is not None:
            self._restart_timer.cancel()
        self._restart_timer = restart_timer
        restart_timer.start()

    def _start_poll_timer(self) -> None:
        """启动轮询线程。"""
        self._stop_poll_timer()
        self._poll_stop_event = threading.Event()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            args=(self._poll_stop_event, self._generation),
            daemon=True,
            name="MuMuSyncPoll",
        )
        self._poll_thread.start()

    def _stop_poll_timer(self) -> None:
        """停止轮询线程。"""
        self._poll_stop_event.set()
        if (self._poll_thread is not None and self._poll_thread.is_alive()
                and self._poll_thread is not threading.current_thread()):
            self._poll_thread.join(2.0)
        self._poll_thread = None

    def _print_directory_state(self, tag: str) -> None:
        """输出目录文件快照（调试用）。

        默认关闭：每次启动会对目录下 138 个文件逐个 os.stat 并写 27KB 日志，
        对一个「趁手小工具」是纯浪费。需要排查同步问题时设 MOMO_DIAG=1 再启动。
        """
        if os.environ.get("MOMO_DIAG") != "1":
            return
        try:
            LogService.debug("MuMuKeymapSync", f"====== {tag} 目录文件快照 ======")
            if not self._keymap_dir or not os.path.isdir(self._keymap_dir):
                LogService.debug("MuMuKeymapSync", "  (目录不存在)")
                return

            all_items = os.listdir(self._keymap_dir)
            top_files = [
                f for f in all_items
                if os.path.isfile(os.path.join(self._keymap_dir, f))
            ]
            LogService.debug("MuMuKeymapSync", f"  顶层文件({len(top_files)}个):")
            for fname in top_files:
                fp = os.path.join(self._keymap_dir, fname)
                try:
                    st = os.stat(fp)
                    LogService.debug(
                        "MuMuKeymapSync",
                        f"    {fname}  size={st.st_size}  "
                        f"mtime={time.strftime('%H:%M:%S', time.localtime(st.st_mtime))}",
                    )
                except OSError:
                    pass

            # 递归统计
            all_files_count = 0
            for root, dirs, files in os.walk(self._keymap_dir):
                all_files_count += len(files)
            if all_files_count > len(top_files):
                LogService.debug(
                    "MuMuKeymapSync",
                    f"  所有子文件({all_files_count}个)",
                )
                for root, dirs, files in os.walk(self._keymap_dir):
                    for fname in files:
                        fp = os.path.join(root, fname)
                        rel = os.path.relpath(fp, self._keymap_dir)
                        try:
                            st = os.stat(fp)
                            LogService.debug(
                                "MuMuKeymapSync",
                                f"    {rel}  size={st.st_size}  "
                                f"mtime={time.strftime('%H:%M:%S', time.localtime(st.st_mtime))}",
                            )
                        except OSError:
                            pass

            LogService.debug("MuMuKeymapSync", "  ====== 快照结束 ======")
        except Exception as ex:
            LogService.error_ex("MuMuKeymapSync", ex, "快照异常")

    def _diag_log(self, message: str) -> None:
        """写入诊断日志到 %TEMP%\\MuMu_Sync_Diag.log — 对应 C# DiagLog()"""
        try:
            temp_dir = os.environ.get("TEMP", os.environ.get("TMP", os.path.abspath(".")))
            diag_path = os.path.join(temp_dir, "MuMu_Sync_Diag.log")
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            entry = f"[{time_str}] {message}\n"
            with open(diag_path, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception:
            pass  # 诊断日志写入失败不应影响主流程

    def dispose(self) -> None:
        """释放所有资源。"""
        if self._disposed:
            return
        self._disposed = True
        self.stop()
        with self._callbacks_lock:
            self._keymap_changed_callbacks.clear()
