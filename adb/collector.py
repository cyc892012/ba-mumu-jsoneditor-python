"""触摸采集流水线 — 对应 C# TouchCollector

功能：
- 后台线程读取 getevent 输出
- 去重统计（按 relX:F4,relY:F4 分组）
- CoordinateCaptured / StatusChanged / ErrorOccurred 回调
- IDisposable 模式（start/stop 管理生命周期）
"""
import threading
from datetime import datetime
from typing import Callable, Dict, List, Optional, Any
from adb.client import AdbClient
from adb.parser import TouchEventParser, TouchCoordinate
from infra.log_service import LogService


class TouchCoordInfo:
    """触摸坐标统计信息 — 对应 C# TouchCollector.TouchCoordInfo"""
    __slots__ = ('coord', 'hit_count', 'first_seen', 'last_seen')

    def __init__(self, coord: TouchCoordinate):
        self.coord: TouchCoordinate = coord
        self.hit_count: int = 1
        self.first_seen: datetime = datetime.now()
        self.last_seen: datetime = datetime.now()


class TouchCollector:
    """触摸采集流水线 — 对应 C# TouchCollector"""

    def __init__(self, adb: AdbClient):
        self._adb: AdbClient = adb
        self._parser: TouchEventParser = TouchEventParser()
        self._parser.on_touch_detected(self._on_touch_detected)
        self._parser.on_debug_output(self._on_debug_output)
        self._read_thread: Optional[threading.Thread] = None
        self._running: bool = False
        self._lock_obj: threading.Lock = threading.Lock()
        self._disposed: bool = False

        self._unique_coords: Dict[str, TouchCoordInfo] = {}
        self._coords_lock: threading.Lock = threading.Lock()

        # 回调
        self._coordinate_callbacks: List[Callable[[TouchCoordinate], None]] = []
        self._status_callbacks: List[Callable[[str], None]] = []
        self._error_callbacks: List[Callable[[str], None]] = []
        self._resolution_callbacks: List[Callable[[int, int], None]] = []

    @property
    def is_running(self) -> bool:
        return self._running

    def on_coordinate_captured(self, callback: Callable[[TouchCoordinate], None]) -> None:
        self._coordinate_callbacks.append(callback)

    def on_status_changed(self, callback: Callable[[str], None]) -> None:
        self._status_callbacks.append(callback)

    def on_error_occurred(self, callback: Callable[[str], None]) -> None:
        self._error_callbacks.append(callback)

    def on_resolution_detected(self, callback: Callable[[int, int], None]) -> None:
        self._resolution_callbacks.append(callback)

    def start(self) -> bool:
        """启动采集"""
        if self._running:
            return True

        self._raise_status("正在连接 ADB...")
        if not self._adb.connect():
            LogService.error("TouchCollector", "ADB连接失败")
            self._raise_error("ADB 连接失败，请检查端口号是否正确。")
            return False

        size_str = self._adb.get_screen_size()
        if not size_str:
            LogService.error("TouchCollector", "无法获取模拟器屏幕分辨率")
            self._raise_error("无法获取模拟器屏幕分辨率。")
            self._adb.disconnect()
            return False

        parts = size_str.strip().split('x')
        if len(parts) != 2:
            LogService.error("TouchCollector", f"屏幕分辨率格式异常: {size_str}")
            self._raise_error(f"屏幕分辨率格式异常：{size_str}")
            self._adb.disconnect()
            return False

        try:
            w = int(parts[0])
            h = int(parts[1])
        except (ValueError, TypeError):
            LogService.error("TouchCollector", f"屏幕分辨率解析失败: {size_str}")
            self._raise_error(f"屏幕分辨率格式异常：{size_str}")
            self._adb.disconnect()
            return False

        # 保证 w >= h（横屏）
        if w < h:
            w, h = h, w

        self._parser.set_screen_size(w, h)
        with self._coords_lock:
            self._unique_coords.clear()

        for cb in self._resolution_callbacks:
            try:
                cb(w, h)
            except Exception:
                pass

        mouse_max = self._adb.get_mouse_max_values()
        if mouse_max[0] > 0 and mouse_max[1] > 0:
            self._parser.set_mouse_scale(mouse_max[0], mouse_max[1])
            self._raise_status(f"鼠标集成分辨率:{mouse_max[0]}x{mouse_max[1]}")

        sensor_max = self._adb.get_sensor_max_values()
        if sensor_max[0] > 0 and sensor_max[1] > 0:
            self._parser.set_sensor_scale(sensor_max[0], sensor_max[1])

        process = self._adb.start_getevent()
        if process is None:
            self._raise_error("无法启动 getevent 进程。")
            self._adb.disconnect()
            return False

        self._running = True
        self._read_thread = threading.Thread(
            target=self._read_loop,
            args=(process,),
            daemon=True,
            name="TouchCollector"
        )
        self._read_thread.start()

        self._raise_status(f"采集已启动({w}x{h})")
        return True

    def stop(self) -> None:
        """停止采集"""
        self._running = False
        self._adb.stop_getevent()
        self._adb.disconnect()

        with self._lock_obj:
            t = self._read_thread
            self._read_thread = None

        if t is not None and t.is_alive():
            t.join(2.0)
            if t.is_alive():
                LogService.warn("TouchCollector", "采集线程在3秒后仍未退出")
                self._raise_status("采集线程未能正常退出")

        self._raise_status("采集已停止")

    def get_unique_coords(self) -> List[TouchCoordInfo]:
        """获取去重坐标列表（按点击数降序）"""
        with self._coords_lock:
            result = list(self._unique_coords.values())
        result.sort(key=lambda x: x.hit_count, reverse=True)
        return result

    def clear_coords(self) -> None:
        """清空采集数据"""
        self._parser.reset_seen_max_values()
        with self._coords_lock:
            self._unique_coords.clear()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self.stop()
        self._parser.on_touch_detected = None
        self._parser.on_debug_output = None

    # ── 内部实现 ──

    def _read_loop(self, process: Any) -> None:
        try:
            reader = process.stdout
            line_count = 0
            touch_line_start = -1
            while self._running:
                try:
                    line = reader.readline()
                except Exception:
                    break
                if not line:
                    break
                line = line.rstrip('\n').rstrip('\r')
                line_count += 1
                if line_count == 1:
                    self._raise_status("采集已启动 - 开始接收事件数据...")
                if touch_line_start < 0 and ("ABS_MT" in line or "BTN_TOUCH" in line):
                    touch_line_start = line_count
                self._parser.parse_line(line)

            touch_str = str(touch_line_start) if touch_line_start >= 0 else "无"
            self._raise_status(
                f"采集线程结束, 共接收 {line_count} 行, 首个触摸行号: {touch_str}"
            )
        except IOError as ex:
            if self._running:
                LogService.error_ex("TouchCollector", ex, "ADB管道IO异常")
                self._raise_error(f"ADB管道读取异常：{ex}")
        except Exception as ex:
            LogService.error_ex("TouchCollector", ex, "ReadLoop采集异常")
            self._raise_error(f"采集异常：{ex}")

    def _on_touch_detected(self, coord: TouchCoordinate) -> None:
        if not coord.is_press:
            key = f"{coord.rel_x:.4f},{coord.rel_y:.4f}"
            with self._coords_lock:
                if key in self._unique_coords:
                    info = self._unique_coords[key]
                    info.hit_count += 1
                    info.last_seen = datetime.now()
                else:
                    self._unique_coords[key] = TouchCoordInfo(coord)
                count = len(self._unique_coords)

            self._raise_status(
                f"[松开] x={coord.x} y={coord.y} "
                f"rx={coord.rel_x:.4f} ry={coord.rel_y:.4f} 总数={count}"
            )

            for cb in self._coordinate_callbacks:
                try:
                    cb(coord)
                except Exception:
                    pass
        else:
            self._raise_status(
                f"[按下] x={coord.x} y={coord.y} "
                f"rx={coord.rel_x:.4f} ry={coord.rel_y:.4f}"
            )

    def _raise_status(self, msg: str) -> None:
        for cb in self._status_callbacks:
            try:
                cb(msg)
            except Exception:
                pass

    def _on_debug_output(self, msg: str) -> None:
        self._raise_status(msg)

    def _raise_error(self, msg: str) -> None:
        for cb in self._error_callbacks:
            try:
                cb(msg)
            except Exception:
                pass
