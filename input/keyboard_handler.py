"""键盘绑定编排器 — 对应 C# KeyboardBindingHandler

管理绑定键（BindKey1/2）、查找键（FindKey）、重置键（ResetKey），
暂停/恢复全局监听。
"""
from typing import Callable, Optional
import threading
from input.keyboard_listener import KeyboardListener
from infra.log_service import LogService


class KeyboardBindingHandler:
    """键盘绑定编排器 — 对应 C# KeyboardBindingHandler"""

    def __init__(self):
        self._listener_once = KeyboardListener()
        self._listener_continuously = KeyboardListener()

        # 绑定键
        self._bind_key1: str = ""
        self._bind_key2: str = ""
        self._bind_key3: str = ""

        # 查找键和重置键
        self.find_key: str = "D"
        self.reset_key: str = "F"

        # 状态
        self._is_once_mode: bool = False
        self._is_continuous_mode: bool = False
        self._pause_depth = 0
        self._mode_revision = 0
        self._state_lock = threading.RLock()

        # 回调
        self._key_captured_once_callbacks: list[Callable[[str, int], None]] = []
        self._key_captured_continuously_callbacks: list[Callable[[str, int], None]] = []
        self._bind_key_changed_callbacks: list[Callable[[int, str], None]] = []
        self._config_changed_callback: Optional[Callable[[], None]] = None
        # 桥接标记：listener → _fire_xxx 的转发只注册一次（防多个消费者重复注册导致回调多次触发）
        self._once_bridge_attached = False
        self._continuous_bridge_attached = False

    @property
    def bind_key1(self) -> str:
        return self._bind_key1

    @property
    def bind_key2(self) -> str:
        return self._bind_key2

    @property
    def bind_key3(self) -> str:
        return self._bind_key3

    @property
    def is_key_creation_active(self) -> bool:
        return self._is_once_mode or self._is_continuous_mode

    def on_key_captured_once(self, callback: Callable[[str, int], None]) -> None:
        self._key_captured_once_callbacks.append(callback)
        if not self._once_bridge_attached:
            self._once_bridge_attached = True
            self._listener_once.on_key_down(self._fire_once)

    def on_key_captured_continuously(self, callback: Callable[[str, int], None]) -> None:
        self._key_captured_continuously_callbacks.append(callback)
        if not self._continuous_bridge_attached:
            self._continuous_bridge_attached = True
            self._listener_continuously.on_key_down(self._fire_continuously)

    def on_bind_key_changed(self, callback: Callable[[int, str], None]) -> None:
        self._bind_key_changed_callbacks.append(callback)

    def set_listen_once_mode(self, enabled: bool) -> None:
        """设置一次性监听模式"""
        with self._state_lock:
            self._mode_revision += 1
            if enabled:
                self._listener_continuously.stop_listening()
                self._is_continuous_mode = False
                self._listener_once.start_listening()
                self._is_once_mode = self._listener_once.is_listening
            else:
                self._listener_once.stop_listening()
                self._is_once_mode = False

    def set_listen_continuously_mode(self, enabled: bool) -> None:
        """设置连续监听模式"""
        with self._state_lock:
            self._mode_revision += 1
            if enabled:
                self._listener_once.stop_listening()
                self._is_once_mode = False
                self._listener_continuously.start_listening()
                self._is_continuous_mode = self._listener_continuously.is_listening
            else:
                self._listener_continuously.stop_listening()
                self._is_continuous_mode = False

    def stop_all_listening(self) -> None:
        """停止所有监听"""
        with self._state_lock:
            self._mode_revision += 1
            self._listener_once.stop_listening()
            self._listener_continuously.stop_listening()
            self._is_once_mode = False
            self._is_continuous_mode = False

    def get_mode_revision(self) -> int:
        with self._state_lock:
            return self._mode_revision

    def pause(self) -> None:
        """嵌套暂停派发；底层继续跟踪按键释放。"""
        with self._state_lock:
            self._pause_depth += 1
            self._listener_once.pause()
            self._listener_continuously.pause()

    def resume(self) -> None:
        """配对恢复派发，不复活暂停期间被显式关闭的监听。"""
        with self._state_lock:
            if self._pause_depth == 0:
                return
            self._pause_depth -= 1
            self._listener_once.resume()
            self._listener_continuously.resume()

    def process_bind_key1_down(self, key_str: str) -> None:
        self._bind_key1 = key_str
        self._notify_bind_key_changed(1, key_str)

    def process_bind_key2_down(self, key_str: str) -> None:
        self._bind_key2 = key_str
        self._notify_bind_key_changed(2, key_str)

    def process_bind_key3_down(self, key_str: str) -> None:
        self._bind_key3 = key_str
        self._notify_bind_key_changed(3, key_str)

    def process_find_key_down(self, key_str: str) -> tuple[str, bool]:
        """处理查找键变更 — 返回 (key, conflict)"""
        conflict = key_str == self.reset_key
        if not conflict:
            self.find_key = key_str
            self._notify_config_changed()
        return self.find_key, conflict

    def process_reset_key_down(self, key_str: str) -> tuple[str, bool]:
        """处理重置键变更 — 返回 (key, conflict)"""
        conflict = key_str == self.find_key
        if not conflict:
            self.reset_key = key_str
            self._notify_config_changed()
        return self.reset_key, conflict

    def set_on_config_changed(self, callback: Callable[[], None]) -> None:
        """设置配置变更回调（find_key/reset_key 改变时触发）"""
        self._config_changed_callback = callback

    def _notify_config_changed(self) -> None:
        if self._config_changed_callback:
            try:
                self._config_changed_callback()
            except Exception as ex:
                # 回调异常必须可见：过去这里静默 pass，导致「面板热键/键盘捕获失效」
                # 这类问题完全查不到原因（见 Agent接手提示词.md 坑 1）
                LogService.error_ex("KeyboardBindingHandler", ex, "配置变更回调执行失败")

    def dispose(self) -> None:
        self.stop_all_listening()
        self._key_captured_once_callbacks.clear()
        self._key_captured_continuously_callbacks.clear()
        self._bind_key_changed_callbacks.clear()

    @staticmethod
    def _safe_call(cb, args: tuple, context: str) -> None:
        """执行消费者回调；异常必须落日志而不是静默丢弃。

        这是全局键盘钩子的回调线程 —— 一旦静默吞掉，表现就是「按了没反应」，
        完全没有排查线索（本项目真实踩过的坑）。
        """
        try:
            cb(*args)
        except Exception as ex:
            LogService.error_ex("KeyboardBindingHandler", ex, context)

    def _fire_once(self, key_str: str, vk: int) -> None:
        with self._state_lock:
            if self._pause_depth or not self._is_once_mode:
                return
            # 先消费再向 UI 排队，避免同一轮后台事件产生多次单次操作。
            self._is_once_mode = False
            # 保留释放跟踪；取消后重新 arm 不会把长按当新按。
        for cb in list(self._key_captured_once_callbacks):
            self._safe_call(cb, (key_str, vk), "一次性按键捕获回调失败")

    def _fire_continuously(self, key_str: str, vk: int) -> None:
        with self._state_lock:
            if self._pause_depth or not self._is_continuous_mode:
                return
        for cb in list(self._key_captured_continuously_callbacks):
            self._safe_call(cb, (key_str, vk), "连续按键捕获回调失败")

    def _notify_bind_key_changed(self, index: int, key_str: str) -> None:
        for cb in self._bind_key_changed_callbacks:
            self._safe_call(cb, (index, key_str), "绑定键变更回调失败")

    @staticmethod
    def key_to_display_text(key_str: str) -> str:
        """按键转显示文本"""
        return key_str

    @staticmethod
    def key_event_to_display_text(key_str: str) -> str:
        return key_str
