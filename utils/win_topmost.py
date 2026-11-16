"""Win32 窗口置顶 — 全项目唯一的实现。

为什么不用 Qt：
    ``setWindowFlag(Qt.WindowStaysOnTopHint, ...)`` 会**重建 HWND**，
    窗口先消失再出现，肉眼可见闪烁。

正确做法（本项目「坑 2」）：
    用 Win32 直接改扩展样式 —— **先** ``SetWindowLongW(GWL_EXSTYLE)`` 改
    ``WS_EX_TOPMOST``，**再** ``SetWindowPos``。不重建 HWND、无闪烁。

注意：对 **frameless 窗口，裸 SetWindowPos 不生效**（Qt 不维持该标志），
所以必须先改扩展样式。

调用方：``ui/main_window.py``（主窗口置顶按钮）、``controls/title_bar.py``（子窗口标题栏）。
"""
import ctypes
import sys

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010


def apply_topmost(win, on: bool) -> bool:
    """置顶 / 取消置顶窗口，不重建 HWND、无闪烁。

    Args:
        win: 任意有 ``winId()`` 的 QWidget。
        on: True 置顶，False 取消。

    Returns:
        True 表示已设置；非 Windows 平台或取不到句柄时返回 False（静默降级）。
    """
    if sys.platform != "win32":
        return False
    try:
        from ctypes import wintypes

        hwnd = win.winId()
        if hasattr(hwnd, "int"):
            hwnd = hwnd.int()
        hwnd = int(hwnd)

        user32 = ctypes.windll.user32
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SetWindowPos.restype = ctypes.c_bool

        cur = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)
        new = cur | WS_EX_TOPMOST if on else cur & ~WS_EX_TOPMOST
        user32.SetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE, new)
        user32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(HWND_TOPMOST if on else HWND_NOTOPMOST),
            0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
        )
        return True
    except Exception:
        return False
