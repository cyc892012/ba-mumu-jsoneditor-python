"""全局键盘监听 — 对应 C# GlobalKeyboardListener

使用 pynput.keyboard.Listener 替代 SetWindowsHookEx(WH_KEYBOARD_LL)。
"""
from typing import Callable, Optional
import threading
from pynput.keyboard import Listener, Key
from infra.log_service import LogService


class KeyboardListener:
    """全局键盘监听器"""

    def __init__(self):
        self._listener: Optional[Listener] = None
        self._is_listening: bool = False
        self._key_down_callbacks: list[Callable[[str], None]] = []
        self._pause_depth = 0
        self._pressed = set()
        self._state_lock = threading.RLock()

    @property
    def is_listening(self) -> bool:
        return self._is_listening

    def on_key_down(self, callback: Callable[[str], None]) -> None:
        self._key_down_callbacks.append(callback)

    def start_listening(self) -> bool:
        """启动全局键盘监听"""
        if self._is_listening:
            return True
        try:
            self._listener = Listener(on_press=self._on_press, on_release=self._on_release)
            self._is_listening = True
            self._listener.start()
            return True
        except Exception as ex:
            LogService.error_ex("KeyboardListener", ex, "启动键盘监听失败")
            self._is_listening = False
            self._listener = None
            return False

    def stop_listening(self) -> None:
        """停止全局键盘监听"""
        self._is_listening = False
        with self._state_lock:
            self._pressed.clear()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception as ex:
                LogService.error_ex("KeyboardListener", ex, "停止键盘监听失败")
            self._listener = None

    def pause(self) -> None:
        """只暂停派发，保留底层释放事件以防长按重入。"""
        with self._state_lock:
            self._pause_depth += 1

    def resume(self) -> None:
        with self._state_lock:
            self._pause_depth = max(0, self._pause_depth - 1)

    def _key_identity(self, key):
        vk = self._get_vk(key)
        return ("vk", vk) if vk else ("text", self._key_to_string(key))

    def _on_release(self, key) -> None:
        with self._state_lock:
            self._pressed.discard(self._key_identity(key))

    def dispose(self) -> None:
        self.stop_listening()
        self._key_down_callbacks.clear()

    def _on_press(self, key) -> None:
        """按键按下回调"""
        if not self._is_listening:
            return

        try:
            key_str = self._key_to_string(key)
            vk = self._get_vk(key)
            with self._state_lock:
                identity = self._key_identity(key)
                if identity in self._pressed:
                    return
                self._pressed.add(identity)
                if self._pause_depth:
                    return
            for cb in list(self._key_down_callbacks):
                try:
                    cb(key_str, vk)
                except Exception as ex:
                    # 消费者回调异常必须可见：静默吞掉时表现为「按键无反应」，
                    # 完全查不到原因（本项目真实踩过的坑，见 Agent接手提示词.md 坑 1）
                    LogService.error_ex("KeyboardListener", ex, "按键回调执行失败")
        except Exception as ex:
            LogService.error_ex("KeyboardListener", ex, "键盘事件处理异常")

    @staticmethod
    def _get_vk(key) -> int:
        """从 pynput Key 提取虚拟键码"""
        if hasattr(key, 'vk') and key.vk is not None:
            return key.vk
        # pynput 的特殊键把实际 VK 放在 value 中，覆盖 F13、Windows 等完整键集合。
        value = getattr(key, "value", None)
        if getattr(value, "vk", None) is not None:
            return value.vk
        try:
            mapping = {
                Key.space: 0x20, Key.enter: 0x0D, Key.tab: 0x09,
                Key.esc: 0x1B, Key.backspace: 0x08, Key.shift: 0x10,
                Key.shift_l: 0xA0, Key.shift_r: 0xA1,
                Key.ctrl: 0x11, Key.ctrl_l: 0xA2, Key.ctrl_r: 0xA3,
                Key.alt: 0x12, Key.alt_l: 0xA4, Key.alt_r: 0xA5,
                Key.caps_lock: 0x14, Key.num_lock: 0x90,
                Key.up: 0x26, Key.down: 0x28, Key.left: 0x25, Key.right: 0x27,
                Key.home: 0x24, Key.end: 0x23, Key.page_up: 0x21, Key.page_down: 0x22,
                Key.insert: 0x2D, Key.delete: 0x2E,
                Key.f1: 0x70, Key.f2: 0x71, Key.f3: 0x72, Key.f4: 0x73,
                Key.f5: 0x74, Key.f6: 0x75, Key.f7: 0x76, Key.f8: 0x77,
                Key.f9: 0x78, Key.f10: 0x79, Key.f11: 0x7A, Key.f12: 0x7B,
            }
            if isinstance(key, Key):
                return mapping.get(key, 0)
        except Exception:
            pass
        return 0

    @staticmethod
    def _key_to_string(key) -> str:
        """将 pynput Key 转为字符串"""
        try:
            if hasattr(key, 'char') and key.char is not None:
                return key.char.upper()
            # 特殊键
            name = str(key).replace("Key.", "")
            return name
        except Exception:
            return ""
