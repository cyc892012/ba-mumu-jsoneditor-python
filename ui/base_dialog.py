"""
Frameless 子窗口基类 — 自绘标题栏 + 拖拽 + 置顶 + 底部状态栏
"""
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QStatusBar
from PyQt5.QtCore import Qt
from controls import TitleBar
import theme


class BaseDialog(QWidget):
    """所有子窗口的基类，使用 frameless + 自定义标题栏"""

    def __init__(self, parent=None, title=""):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        # ※ 不在此 setStyleSheet，避免 GLOBAL_QSS 级联污染 TitleBar

        # 根布局
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

        # 自绘标题栏（不受 GLOBAL_QSS 影响）
        self._title_bar = TitleBar(title, parent=self)
        self._title_bar.close_clicked.connect(self.close)
        self._root_layout.addWidget(self._title_bar)

        # 内容包装器 — GLOBAL_QSS 只应用到这里，不影响 TitleBar
        self._content_wrapper = QWidget()
        self._content_wrapper.setObjectName("DialogContent")
        self._content_wrapper.setStyleSheet(
            theme.GLOBAL_QSS + f"QWidget#DialogContent {{background: {theme.BG_MAIN};}}"
        )
        self._content_layout = QVBoxLayout(self._content_wrapper)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        self._root_layout.addWidget(self._content_wrapper, 1)

        # 底部状态栏（粉色，与主窗体同款）
        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(theme.STATUS_QSS)
        self._root_layout.addWidget(self._status_bar)

    @property
    def content_layout(self):
        """子类往这里添加控件"""
        return self._content_layout

    @property
    def title_bar(self):
        return self._title_bar

    @property
    def status_bar(self):
        """子类可在此添加状态提示"""
        return self._status_bar
