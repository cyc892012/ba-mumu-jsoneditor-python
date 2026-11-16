"""触摸事件解析器，沿用 C# 坐标换算并兼容 MuMu12 的增量坐标事件。"""
import re
from dataclasses import dataclass
from typing import Callable, List
from core.coord_model import adb_px_to_rel, raw_to_px


@dataclass
class TouchCoordinate:
    x: float = 0.0
    y: float = 0.0
    screen_width: int = 0
    screen_height: int = 0
    is_press: bool = False

    @property
    def rel_x(self) -> float:
        return adb_px_to_rel(self.x, self.screen_width) if self.screen_width > 1 else 0.0

    @property
    def rel_y(self) -> float:
        return adb_px_to_rel(self.y, self.screen_height) if self.screen_height > 1 else 0.0


class TouchEventParser:

    _hex_regex = re.compile(r'\b([0-9a-fA-F]{4,8})\b', re.ASCII)

    def __init__(self):
        # ── C# 原版字段 ──
        self._sensor_x: int = -1
        self._sensor_y: int = -1
        self._mouse_x: int = -1
        self._mouse_y: int = -1
        self._mouse_max_x: int = 65535
        self._mouse_max_y: int = 65535
        self._mouse_seen_max_x: int = 0
        self._mouse_seen_max_y: int = 0
        self._mouse_active: bool = False
        self._screen_width: int = 0
        self._screen_height: int = 0
        self._touching: bool = False
        self._scale_x: float = 1.0
        self._scale_y: float = 1.0
        self._use_scale: bool = False
        self._sensor_max_x: int = 0
        self._sensor_max_y: int = 0
        self._seen_max_x: int = 0
        self._seen_max_y: int = 0

        self._touch_callbacks: List[Callable[[TouchCoordinate], None]] = []
        self._debug_callbacks: List[Callable[[str], None]] = []

    # ── C# 原版 public API ──

    def on_touch_detected(self, callback: Callable[[TouchCoordinate], None]) -> None:
        self._touch_callbacks.append(callback)

    def on_debug_output(self, callback: Callable[[str], None]) -> None:
        self._debug_callbacks.append(callback)

    def set_screen_size(self, width: int, height: int) -> None:
        self._screen_width = width
        self._screen_height = height
        self._emit_debug(f"屏幕分辨率:{width}x{height}")

    def set_sensor_scale(self, sensor_max_x: int, sensor_max_y: int) -> None:
        self._sensor_max_x = sensor_max_x if sensor_max_x > 0 else 0
        self._sensor_max_y = sensor_max_y if sensor_max_y > 0 else 0
        self._use_scale = False
        self._recalc_scale()

    def set_mouse_scale(self, mouse_max_x: int, mouse_max_y: int) -> None:
        if mouse_max_x > 0:
            self._mouse_max_x = mouse_max_x
        if mouse_max_y > 0:
            self._mouse_max_y = mouse_max_y
        self._emit_debug(f"鼠标集成分辨率:{self._mouse_max_x}x{self._mouse_max_y}")

    def reset_seen_max_values(self) -> None:
        self._seen_max_x = 0
        self._seen_max_y = 0
        self._mouse_seen_max_x = 0
        self._mouse_seen_max_y = 0

    # ── C# 原版 RecalcScale ──

    def _recalc_scale(self) -> None:
        if self._screen_width <= 1 or self._screen_height <= 1:
            return
        self._recalc_scale_internal()

    def _recalc_scale_internal(self) -> None:
        effective_max_x = max(self._sensor_max_x, self._seen_max_x)
        effective_max_y = max(self._sensor_max_y, self._seen_max_y)

        if (effective_max_x > 0 and effective_max_y > 0
                and (effective_max_y > self._screen_width - 1
                     or effective_max_x > self._screen_height - 1)):
            self._scale_x = raw_to_px(1, effective_max_y, self._screen_width)
            self._scale_y = raw_to_px(1, effective_max_x, self._screen_height)
            self._use_scale = True
            self._emit_debug(
                f"传感器缩放:Xmax={effective_max_x} Ymax={effective_max_y} "
                f"scaleX={self._scale_x:.6f} scaleY={self._scale_y:.6f}")
        elif effective_max_x > 0 and effective_max_y > 0:
            self._scale_x = 1.0
            self._scale_y = 1.0
            self._use_scale = False
            self._emit_debug("传感器范围与屏幕一致，无需缩放")
        else:
            self._scale_x = 1.0
            self._scale_y = 1.0
            self._use_scale = False
            self._emit_debug("传感器范围未知，使用1:1映射(自校准中)")

    # ── C# 原版 ParseLine + ABS_MT_TRACKING_ID 扩展 ──

    def parse_line(self, line: str) -> None:
        try:
            if not line:
                return

            # C# 原版: ABS_X / ABS_Y
            if "ABS_X" in line or "0003 0000" in line:
                val = self._extract_last_hex(line)
                if val >= 0:
                    self._mouse_x = val
                    self._mouse_active = True
                return

            if "ABS_Y" in line or "0003 0001" in line:
                val = self._extract_last_hex(line)
                if val >= 0:
                    self._mouse_y = val
                    self._mouse_active = True
                return

            # C# 原版: ABS_MT_POSITION_X
            if "ABS_MT_POSITION_X" in line:
                val = self._extract_last_hex(line)
                if val >= 0:
                    self._sensor_x = val
            # C# 原版: ABS_MT_POSITION_Y
            elif "ABS_MT_POSITION_Y" in line:
                val = self._extract_last_hex(line)
                if val >= 0:
                    self._sensor_y = val

            # ★ MuMu12 扩展: ABS_MT_TRACKING_ID
            elif "ABS_MT_TRACKING_ID" in line:
                val = self._extract_last_hex(line)
                if val >= 0:
                    if val == 0xFFFFFFFF:
                        self._emit_debug(
                            f"[事件]UP(tracking) sensor=({self._sensor_x},{self._sensor_y}) "
                            f"mouse=({self._mouse_x},{self._mouse_y})")
                        self._release_touch()
                    else:
                        self._touching = True
                        self._emit_debug(
                            f"[事件]DOWN(tracking) sensor=({self._sensor_x},{self._sensor_y}) "
                            f"mouse=({self._mouse_x},{self._mouse_y})")

            # C# 原版: BTN_TOUCH
            elif "BTN_TOUCH" in line and "DOWN" in line:
                self._touching = True
                self._emit_debug(
                    f"[事件]DOWN sensor=({self._sensor_x},{self._sensor_y}) "
                    f"mouse=({self._mouse_x},{self._mouse_y})")
            elif "BTN_TOUCH" in line and "UP" in line:
                self._emit_debug(
                    f"[事件]UP sensor=({self._sensor_x},{self._sensor_y}) "
                    f"mouse=({self._mouse_x},{self._mouse_y})")
                self._release_touch()

            # C# 原版: SYN_REPORT
            elif "SYN_REPORT" in line and self._touching:
                self._emit_debug(
                    f"[事件]SYN sensor=({self._sensor_x},{self._sensor_y}) "
                    f"mouse=({self._mouse_x},{self._mouse_y})")
                self._fire_touch_if_valid(True)

            # C# 原版: hex-only events
            elif "0003 0035" in line:
                val = self._extract_last_hex(line)
                if val >= 0:
                    self._sensor_x = val
            elif "0003 0036" in line:
                val = self._extract_last_hex(line)
                if val >= 0:
                    self._sensor_y = val
            # ★ MuMu12 扩展: ABS_MT_TRACKING_ID hex
            elif "0003 0039" in line:
                val = self._extract_last_hex(line)
                if val >= 0:
                    if val == 0xFFFFFFFF:
                        self._release_touch()
                    else:
                        self._touching = True
            elif "0001 014a" in line and line.rstrip().endswith("00000001"):
                self._touching = True
                self._emit_debug(f"[事件]DOWN(hex) mouse=({self._mouse_x},{self._mouse_y})")
            elif "0001 014a" in line and line.rstrip().endswith("00000000"):
                self._emit_debug(f"[事件]UP(hex) mouse=({self._mouse_x},{self._mouse_y})")
                self._release_touch()
            else:
                pass

        except Exception as ex:
            print(f"[TouchEventParser.parse_line] {ex}")

    def _release_touch(self) -> None:
        # 部分设备同时发送 tracking 结束和 BTN_TOUCH UP，只提交一次抬手。
        if not self._touching:
            return
        self._touching = False
        self._fire_touch_if_valid(False)

    # ── 坐标换算与触摸提交 ──

    def _fire_touch_if_valid(self, is_press: bool) -> None:
        if self._screen_width <= 0 or self._screen_height <= 0:
            self._emit_debug("[触发]跳过:分辨率无效")
            return

        use_mouse = self._mouse_active and self._mouse_x >= 0 and self._mouse_y >= 0
        use_sensor = self._sensor_x >= 0 and self._sensor_y >= 0

        if not use_mouse and not use_sensor:
            self._emit_debug("[触发]跳过:无有效坐标源")
            return

        if self._sensor_x > self._seen_max_x:
            self._seen_max_x = self._sensor_x
            self._recalc_scale()
        if self._sensor_y > self._seen_max_y:
            self._seen_max_y = self._sensor_y
            self._recalc_scale()

        logical_x: float
        logical_y: float

        if use_mouse:
            if self._mouse_x > self._mouse_seen_max_x:
                self._mouse_seen_max_x = self._mouse_x
            if self._mouse_y > self._mouse_seen_max_y:
                self._mouse_seen_max_y = self._mouse_y

            eff_mouse_max_x = max(self._mouse_max_x, self._mouse_seen_max_x)
            eff_mouse_max_y = max(self._mouse_max_y, self._mouse_seen_max_y)
            if eff_mouse_max_x <= 0 or eff_mouse_max_y <= 0:
                return

            logical_x = raw_to_px(self._mouse_y, eff_mouse_max_y, self._screen_width)
            logical_y = (self._screen_height - 1) - raw_to_px(self._mouse_x, eff_mouse_max_x, self._screen_height)

            self._emit_debug(
                f"[高精]mouse({self._mouse_x},{self._mouse_y})"
                f"/{eff_mouse_max_x},{eff_mouse_max_y}"
                f"→({logical_x:.2f},{logical_y:.2f})")

        elif self._use_scale:
            logical_x = self._sensor_y * self._scale_x
            logical_y = (self._screen_height - 1) - self._sensor_x * self._scale_y
        else:
            logical_x = float(self._sensor_y)
            logical_y = float(self._screen_height - 1 - self._sensor_x)

        self._emit_debug(f"[映射]→logical({logical_x:.2f},{logical_y:.2f})")

        if (logical_x < 0 or logical_y < 0
                or logical_x >= self._screen_width
                or logical_y >= self._screen_height):
            self._emit_debug("[触发]跳过:映射后坐标越界")
            if not is_press:
                self._mouse_active = False
            return

        coord = TouchCoordinate(
            x=logical_x,
            y=logical_y,
            screen_width=self._screen_width,
            screen_height=self._screen_height,
            is_press=is_press,
        )

        self._emit_debug(
            f"[结果]{'按下' if is_press else '松开'} "
            f"raw=({coord.x:.2f},{coord.y:.2f}) "
            f"rel=({coord.rel_x:.6f},{coord.rel_y:.6f})")

        for callback in self._touch_callbacks:
            try:
                callback(coord)
            except Exception:
                pass

        if not is_press:
            # Linux 只上报变化的轴；跨触摸保留设备坐标，横线/竖线和原位连点才不会漏采。
            self._mouse_active = False

    # ── 辅助 ──

    def _extract_last_hex(self, line: str) -> int:
        matches = self._hex_regex.findall(line)
        if matches:
            try:
                return int(matches[-1], 16)
            except (ValueError, TypeError):
                return -1
        return -1

    def _emit_debug(self, msg: str) -> None:
        for callback in self._debug_callbacks:
            try:
                callback(msg)
            except Exception:
                pass
