"""鼠标位置控制 — 对应 C# MouseSimulator

使用 pynput.mouse.Controller 替代 SetCursorPos/GetCursorPos。
"""
from typing import Tuple
from pynput.mouse import Controller as MouseController


_mouse_controller = MouseController()


def get_cursor_pos() -> Tuple[int, int]:
    """获取当前鼠标位置"""
    try:
        pos = _mouse_controller.position
        return int(pos[0]), int(pos[1])
    except Exception:
        return 0, 0


def move_mouse_to(x: int, y: int) -> None:
    """移动鼠标到指定位置"""
    try:
        _mouse_controller.position = (x, y)
    except Exception as ex:
        raise RuntimeError(f"无法将鼠标移动到指定位置: {ex}")
