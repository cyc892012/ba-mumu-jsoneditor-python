"""
卡片面板 — 圆角白色容器
"""
from PyQt5.QtWidgets import QFrame, QVBoxLayout, QLabel
import theme


class CardPanel(QFrame):
    """圆角卡片，带标题"""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setStyleSheet(theme.CARD_STYLE)
        self.setFrameShape(QFrame.StyledPanel)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(4)

        if title:
            lbl = QLabel(title)
            lbl.setStyleSheet(
                f"font-weight: bold; color: {theme.VIOLET_700}; "
                f"font-size: {theme.FONT_SIZE}pt; background: transparent; border: none;"
            )
            self._layout.addWidget(lbl)

    @property
    def content_layout(self):
        return self._layout
