"""MuMu 键位坐标与 ADB 采集坐标的公共模型。

2026-11-16 实测：1280×720 横屏中，MuMu 先发送整数原始值
n = half_up(rel * size)，Android 再映射为 n * (size - 1) / size。
例如键 0 的 rel=(0.6629098360655737, 0.51183970856102)，
原始值为 (849, 369)，实际落点为 (848.33671875, 368.4875)。
可达格点 k*(size-1)/size（k=0..size），写入时只对最终目标吸附一次。
ADB 则沿用 raw*(size-1)/raw_max、px/(size-1)，不做键位取整。
设备换轴及反向仍由 adb.parser 处理。
"""
import math


def _size(size: int) -> int:
    if not math.isfinite(size) or size < 2 or int(size) != size:
        raise ValueError("屏幕尺寸必须是大于 1 的整数")
    return int(size)


def _finite(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("坐标必须是有限数")
    return value


def _half_up(value: float) -> int:
    # 仅消除半格处约 1e-9 的浮点尾差；保留 848.4999999 与 848.5 的区别。
    return math.floor(value + 0.5 + 1e-9)


def rel_to_px(rel: float, size: int) -> float:
    """键位相对坐标 → MuMu 取整后的实际像素落点，越界夹到屏幕内。"""
    size = _size(size)
    n = _half_up(min(1.0, max(0.0, _finite(rel))) * size)
    return n * (size - 1) / size


def px_to_rel(px: float, size: int) -> float:
    """用户目标像素 → 最近可达格点的相对坐标 n/size（半格向上）。"""
    size = _size(size)
    px = min(size - 1, max(0.0, _finite(px)))
    n = min(size, max(0, _half_up(px * size / (size - 1))))
    return n / size


def snap_px(px: float, size: int) -> float:
    """返回目标像素在 MuMu 中的实际落点。"""
    return rel_to_px(px_to_rel(px, size), size)


def raw_to_px(raw: float, raw_max: float, size: int) -> float:
    """ADB 原始值 → 连续像素；保持 raw*(size-1)/raw_max 的既有数学。"""
    size = _size(size)
    raw_max = _finite(raw_max)
    if raw_max <= 0:
        raise ValueError("原始坐标最大值必须大于 0")
    return _finite(raw) * (size - 1) / raw_max


def adb_px_to_rel(px: float, size: int) -> float:
    """ADB 采集像素的连续归一化；与写入手动目标像素的 px_to_rel 区分。"""
    return _finite(px) / (_size(size) - 1)


def landing_hint(x: float, y: float, width: int, height: int) -> str:
    """供输入界面复用的实际落点和有符号偏差提示。"""
    actual_x, actual_y = snap_px(x, width), snap_px(y, height)
    dx, dy = actual_x - x, actual_y - y
    if abs(dx) < 0.005 and abs(dy) < 0.005:
        return "实际落点与输入一致"

    def signed(value):
        value = 0.0 if abs(value) < 0.005 else value
        return f"{value:+.2f}".replace("-", "−")

    return (f"实际落点 {actual_x:.2f}, {actual_y:.2f}"
            f"（偏差 {signed(dx)}, {signed(dy)}）")
