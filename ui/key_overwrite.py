"""写入前的同键覆盖确认；纯 JSON 处理仍留在 core。"""
import json
import threading
from .message_dialog import MessageDialog


def key_conflicts(original, blocks):
    """只按有效正整数 VK 识别将改变的旧键，一次返回完整冲突集。"""
    current = json.loads(original)
    proposed = json.loads(blocks) if isinstance(blocks, str) else blocks
    proposed = [proposed] if isinstance(proposed, dict) else proposed
    if not isinstance(current, dict) or not isinstance(proposed, list):
        raise ValueError("覆盖检测需要有效的方案和按键数组")
    keymaps = current.get("keymaps", [])
    if not isinstance(keymaps, list):
        keymaps = []
    existing = {}
    for block in keymaps:
        key = block.get("key") if isinstance(block, dict) else None
        vk = key.get("virtual_key") if isinstance(key, dict) else None
        if isinstance(vk, int) and not isinstance(vk, bool) and vk > 0:
            existing.setdefault(vk, block)
    conflicts = {}
    for block in proposed:
        key = block.get("key") if isinstance(block, dict) else None
        vk = key.get("virtual_key") if isinstance(key, dict) else None
        if isinstance(vk, int) and not isinstance(vk, bool) and vk > 0 and vk in existing:
            if existing[vk] != block:
                old_name = existing[vk].get("key", {}).get("text", "")
                new_name = key.get("text", "")
                conflicts[vk] = f"{old_name or new_name} → {new_name} (VK {vk})"
    return list(conflicts.values())


def request_overwrite(owner, original, blocks):
    """未接确认桥时拒绝冲突，不能绕过主窗保护静默覆盖。"""
    if not key_conflicts(original, blocks):
        return True
    guard = getattr(owner, "_overwrite_guard", None)
    return bool(guard(original, blocks)) if guard is not None else False


def overwrite_epoch(owner):
    getter = getattr(owner, "_overwrite_epoch_getter", None)
    return getter() if getter else 0


class OverwriteCoordinator:
    def __init__(self):
        self._lock = threading.RLock()
        self._active = False
        self._epoch = 0

    @property
    def active(self):
        with self._lock:
            return self._active

    def get_epoch(self):
        with self._lock:
            return self._epoch

    def confirm(self, parent, original, blocks, listeners=(), validate=None, force_name=None):
        conflicts = key_conflicts(original, blocks)
        if not conflicts and force_name is None:
            return True
        with self._lock:
            if self._active:
                return False
            self._active = True
            self._epoch += 1
        paused = []
        try:
            for listener in dict.fromkeys(listener for listener in listeners if listener is not None):
                listener.pause()
                paused.append(listener)
            names = "；".join(conflicts) if conflicts else force_name
            accepted = MessageDialog.question(parent, "确认覆盖旧点位",
                f"将替换以下已有按键的点位或内容：\n{names}\n是否覆盖？取消将保留整批原内容。",
                confirm="覆盖", cancel="取消")
            return bool(accepted and (validate is None or validate()))
        finally:
            with self._lock:
                self._epoch += 1
                self._active = False
            for listener in reversed(paused):
                listener.resume()
