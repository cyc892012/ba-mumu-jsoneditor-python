"""
自绘标题栏 — 粉→米黄渐变 + 标题 + 置顶按钮 + 关闭按钮
用于 frameless 子窗口顶部
"""
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PyQt5.QtCore import Qt, QPoint, QPointF, pyqtSignal
from PyQt5.QtGui import QMouseEvent, QPainter, QLinearGradient, QColor, QFont, QFontDatabase

import theme
from utils.win_topmost import apply_topmost


class TitleBar(QWidget):

    close_clicked = pyqtSignal()
    topmost_toggled = pyqtSignal(bool)

    def __init__(self, title="", parent=None, show_minimize=False):
        super().__init__(parent)
        self._parent_window = parent
        self._dragging = False
        self._drag_pos = QPoint()
        self._topmost = False
        families = set(QFontDatabase().families())
        self._icon_family = next((name for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets")
                                  if name in families), None)

        self.setFixedHeight(30)
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 6, 0)
        layout.setSpacing(6)

        # 标题
        self._title_label = QLabel(title)
        self._title_label.setStyleSheet(
            f"color: {theme.TITLE_TEXT}; font-size: {theme.FONT_SIZE}pt; "
            f"font-weight: bold; background: transparent; border: none;"
        )
        layout.addWidget(self._title_label)
        layout.addStretch()

        # 置顶按钮
        self._top_btn = self._make_button("\uE718", "置顶", "置顶")
        self._top_btn.clicked.connect(self._on_topmost)
        layout.addWidget(self._top_btn)
        self._update_topmost_button()

        if show_minimize:
            self._min_btn = self._make_button("\uE921", "—", "最小化")
            if self._parent_window:
                self._min_btn.clicked.connect(self._parent_window.showMinimized)
            layout.addWidget(self._min_btn)

        # 关闭按钮
        close_btn = self._make_button("\uE8BB", "×", "关闭", close=True)
        close_btn.clicked.connect(self.close_clicked.emit)
        layout.addWidget(close_btn)

    def _make_button(self, glyph, fallback, tooltip, close=False):
        button = QPushButton(glyph if self._icon_family else fallback)
        button.setFixedSize(30, 26)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(tooltip)
        family = self._icon_family or theme.FONT_FAMILY
        button.setFont(QFont(family, 9))
        hover = f"background: {theme.RED}; color: white;" if close else "background: rgba(255,255,255,0.5);"
        button.setStyleSheet(
            f'QPushButton {{font-family: "{family}"; font-size: 9pt; color: {theme.TITLE_TEXT}; '
            "background: transparent; border: none; border-radius: 6px; padding: 0;}"
            f"QPushButton:hover {{{hover}}}"
            "QPushButton:pressed {background: rgba(255,255,255,0.75);}"
            f"QPushButton[pinned=true] {{background: rgba(255,255,255,0.7); color: {theme.VIOLET_700};}}"
        )
        return button

    def _update_topmost_button(self):
        self._top_btn.setText(("\uE840" if self._topmost else "\uE718") if self._icon_family
                              else ("取消" if self._topmost else "置顶"))
        self._top_btn.setToolTip("取消置顶" if self._topmost else "置顶")
        self._top_btn.setProperty("pinned", self._topmost)
        self._top_btn.style().unpolish(self._top_btn)
        self._top_btn.style().polish(self._top_btn)
        self._top_btn.update()

    def set_topmost_visual(self, topmost):
        """同步置顶按钮外观，不执行窗口置顶操作。"""
        self._topmost = topmost
        self._update_topmost_button()

    def paintEvent(self, event):
        """直接绘制渐变背景，避免 QSS 级联干扰"""
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0, QColor(theme.TITLE_BAR_START))
        gradient.setColorAt(0.55, QColor(theme.TITLE_BAR_MIDDLE))
        gradient.setColorAt(1, QColor(theme.TITLE_BAR_END))
        painter.fillRect(self.rect(), gradient)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 102))
        for y in range(7, self.height(), 14):
            for x in range(7, self.width(), 14):
                painter.drawEllipse(QPointF(x, y), 1.5, 1.5)

    def _on_topmost(self):
        self._topmost = not self._topmost
        if self._parent_window:
            apply_topmost(self._parent_window, self._topmost)
        self._update_topmost_button()
        self.topmost_toggled.emit(self._topmost)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_pos = event.globalPos()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging and self._parent_window:
            delta = event.globalPos() - self._drag_pos
            self._parent_window.move(
                self._parent_window.x() + delta.x(),
                self._parent_window.y() + delta.y(),
            )
            self._drag_pos = event.globalPos()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._dragging = False
