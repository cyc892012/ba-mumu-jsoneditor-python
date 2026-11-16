"""
圆角按钮 — 直接注入样式表，不依赖全局 QSS 选择器
"""
from PyQt5.QtWidgets import QPushButton
from PyQt5.QtCore import Qt
import theme


class RoundedButton(QPushButton):
    """圆角按钮，role 决定配色"""

    def __init__(self, text="", role="primary", parent=None):
        super().__init__(text, parent)
        self._role = role
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self._apply_style()

    def _apply_style(self):
        styles = {
            "primary": theme.BTN_PRIMARY_STYLE,
            "secondary": theme.BTN_SECONDARY_STYLE,
            "ghost": theme.BTN_GHOST_STYLE,
            "danger": theme.BTN_DANGER_STYLE,
        }
        role = self._role if self._role in styles else "secondary"
        base = styles[role]
        hover, pressed, disabled = theme.BUTTON_STATES[role]

        self.setStyleSheet(
            f"QPushButton {{{base}}}"
            f"QPushButton:hover:enabled {{{hover}}}"
            f"QPushButton:pressed:enabled {{{pressed}}}"
            f"QPushButton:disabled {{{disabled}}}"
        )
