"""共用提示条，按信息或注意事项显示，并自动换行。"""
from PyQt5.QtWidgets import QLabel, QSizePolicy
import theme


class HintLabel(QLabel):
    def __init__(self, text, kind="info", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        policy = self.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Minimum)
        self.setSizePolicy(policy)
        self.setStyleSheet(theme.HINT_WARN_STYLE if kind == "warn" else theme.HINT_INFO_STYLE)
