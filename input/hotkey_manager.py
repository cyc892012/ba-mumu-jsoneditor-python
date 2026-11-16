"""全局热键管理 — 对应 C# HotKey

使用 keyboard.add_hotkey() 替代 RegisterHotKey。
``keyboard`` 是重量级依赖（自带全局钩子 + ctypes 扫描），只在
真正注册热键时才导入，避免启动加载。
"""
from typing import Callable, Optional
from infra.log_service import LogService
from core.key_names import canonical_hotkey_key

_keyboard = None


def _kb():
    """惰性加载 keyboard 模块（首次注册热键时才付这 ~82ms）"""
    global _keyboard
    if _keyboard is None:
        import keyboard
        _keyboard = keyboard
    return _keyboard


class HotKeyManager:
    """全局热键管理器 — 对应 C# HotKey (RegisterHotKey/UnregisterHotKey)"""

    def __init__(self):
        self._hotkeys: dict[str, Optional[Callable]] = {}
        self._registered_combos: set[str] = set()
        self._backend_handles = {}

    def register(self, combo: str, callback: Callable[[], None]) -> bool:
        """注册热键组合 — combo 格式如 'ctrl+d'"""
        try:
            if combo in self._registered_combos:
                self.unregister(combo)

            parts = [canonical_hotkey_key(part) if part.casefold() not in ("ctrl", "alt", "shift")
                     else part.casefold() for part in combo.split("+")]
            backend_combo = "+".join(parts)
            if any(part.startswith("numpad") for part in parts):
                # 数字小键盘保持独立物理扫描码，不退化为顶排数字键。
                from core.json_editor import get_scan_code
                backend_combo = (tuple((get_scan_code(0x60 + int(part[-1])),)
                                       if part.startswith("numpad") else _kb().key_to_scan_codes(part)
                                       for part in parts),)
            self._backend_handles[combo] = _kb().add_hotkey(backend_combo, callback)
            self._registered_combos.add(combo)
            self._hotkeys[combo] = callback
            return True
        except Exception as ex:
            LogService.error_ex("HotKeyManager", ex, f"注册热键失败: {combo}")
            return False

    def unregister(self, combo: str) -> None:
        """注销热键"""
        if combo in self._registered_combos:
            try:
                handle = self._backend_handles.get(combo)
                _kb().remove_hotkey(handle if handle is not None else combo)
            except Exception:
                pass
            self._registered_combos.discard(combo)
            self._hotkeys.pop(combo, None)
            self._backend_handles.pop(combo, None)

    def unregister_all(self) -> None:
        """注销所有热键"""
        for combo in list(self._registered_combos):
            self.unregister(combo)
        self._hotkeys.clear()

    def dispose(self) -> None:
        self.unregister_all()
