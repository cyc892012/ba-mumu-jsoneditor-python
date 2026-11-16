"""按键捕获输入框 — 全项目唯一实现。

合并前项目里有 **4 份几乎相同的实现**：
    - ``ui/main_window.py``        只认单字符 + 暴露 vk + 点击清空
    - ``ui/adb_touch_form.py``     F1~F12 / 特殊键 + Esc 清空并回调
    - ``ui/key_preset_form.py``    同 adb，但回调少一个 row 参数、固定宽 28
    - ``ui/mouse_track_form.py``   同 adb，但 Esc 记为 "Esc"、固定宽 55

其中 3 份的 ``_F_KEYS``（F1~F12）与 ``_SPECIAL`` 映射表逐字相同 ——
一旦某天要支持新特殊键（比如小键盘），就得改 4 处、极容易漏。现在只有这里一处。

对应 C#：主窗口的「按键」输入框、ADB 表格的 KeyCell、预设的 Button2textBox
都映射到本控件。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLineEdit

# Qt 支持 F1～F24；身份仍取 nativeVirtualKey，不能按显示文本猜键码。
F_KEY_NAMES = {getattr(Qt, f"Key_F{i}"): f"F{i}" for i in range(1, 25)}

# 特殊键显示名（含 Esc —— 调用方若需「Esc 清空」请用 escape_clears=True）
SPECIAL_KEY_NAMES = {
    Qt.Key_Escape: "Esc", Qt.Key_Space: "Space", Qt.Key_Tab: "Tab",
    Qt.Key_Return: "Enter", Qt.Key_Enter: "Enter", Qt.Key_Backspace: "Backspace",
    Qt.Key_Up: "Up", Qt.Key_Down: "Down", Qt.Key_Left: "Left", Qt.Key_Right: "Right",
    Qt.Key_Home: "Home", Qt.Key_End: "End", Qt.Key_PageUp: "PageUp",
    Qt.Key_PageDown: "PageDown", Qt.Key_Insert: "Insert", Qt.Key_Delete: "Delete",
    Qt.Key_Shift: "Shift", Qt.Key_Control: "Ctrl", Qt.Key_Alt: "Alt",
    Qt.Key_Meta: "Win", Qt.Key_CapsLock: "CapsLock", Qt.Key_NumLock: "NumLock",
    Qt.Key_ScrollLock: "ScrollLock", Qt.Key_Pause: "Pause", Qt.Key_Print: "PrintScreen",
}


def resolve_key_name(key: int, text: str, allow_special: bool = True, extended_keys: bool = False):
    """把一次按键解析成显示名；无法识别时返回 None。

    Args:
        key: ``QKeyEvent.key()``
        text: ``QKeyEvent.text()``
        allow_special: False 时只接受可打印单字符（主窗口的按键框用这种模式）。

    Returns:
        显示名（大写单字符或特殊键名），识别不出返回 None。
    """
    if allow_special:
        if key in F_KEY_NAMES and (extended_keys or key <= Qt.Key_F12):
            return F_KEY_NAMES[key]
        extra = {Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_CapsLock,
                 Qt.Key_NumLock, Qt.Key_ScrollLock, Qt.Key_Pause, Qt.Key_Print}
        if key in SPECIAL_KEY_NAMES and (extended_keys or key not in extra):
            return SPECIAL_KEY_NAMES[key]

    t = (text or "").strip()
    if t and len(t) == 1 and t.isprintable() and not t.isspace():
        return t.upper()
    return None


class KeyCaptureEdit(QLineEdit):
    """捕获单次按键的只读输入框。

    默认禁用所有文本编辑行为（不调用 ``super().keyPressEvent``），
    避免用户输入落进文本框。

    Args:
        parent: Qt 父对象。
        text: 初始文本。
        width: 固定宽度；None 表示不设置（由调用方自行 setFixedWidth）。
        on_captured: 捕获回调 ``(name, vk)``。Esc 在 ``escape_clears=True`` 时
            以 ``("", 0)`` 回调。
        allow_special: 是否识别 F1~F12 与特殊键名。
        escape_clears: Esc 是否「清空并回调空值」。
            False 时 Esc 走特殊键表，显示为 "Esc"。
        clear_on_click: 鼠标点击是否清空当前值。
        placeholder: 占位文本。

    Attributes:
        vk: 最近一次捕获到的虚拟键码（未捕获过为 0）。
    """

    def __init__(self, parent=None, text: str = "", *, on_captured=None,
                 width=None, allow_special: bool = True,
                 extended_keys: bool = False,
                 escape_clears: bool = False, clear_on_click: bool = False,
                 placeholder: str = "按键"):
        super().__init__(text, parent)
        self._on_captured = on_captured
        self._allow_special = allow_special
        # 旧构造保持捕获范围；迁移后的生产入口明确启用完整键集合。
        self._extended_keys = extended_keys
        self._escape_clears = escape_clears
        self._clear_on_click = clear_on_click
        self._vk = 0

        if width is not None:
            self.setFixedWidth(width)
        self.setAlignment(Qt.AlignCenter)
        self.setReadOnly(True)
        self.setPlaceholderText(placeholder)

    @property
    def vk(self) -> int:
        """最近一次捕获的虚拟键码。"""
        return self._vk

    def keyPressEvent(self, event) -> None:
        key = event.key()

        if self._escape_clears and key == Qt.Key_Escape:
            self.setText("")
            self._vk = 0
            if self._on_captured:
                self._on_captured("", 0)
            return

        name = resolve_key_name(key, event.text(), self._allow_special, self._extended_keys)
        if name:
            self.setText(name)
            self._vk = event.nativeVirtualKey()
            if self._on_captured:
                self._on_captured(name, self._vk)
        # 不调用 super() — 禁用默认文本编辑行为

    def mousePressEvent(self, event) -> None:
        if self._clear_on_click:
            self.clear()
            self._vk = 0
        super().mousePressEvent(event)
