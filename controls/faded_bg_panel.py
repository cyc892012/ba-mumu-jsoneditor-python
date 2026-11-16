"""
淡化背景面板 — 日奈立绘
"""
from PyQt5.QtWidgets import QWidget
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtCore import Qt


class FadedBackgroundPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None
        self._opacity = 0.65
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap
        self.update()

    def set_image_path(self, path):
        pix = QPixmap(path)
        if not pix.isNull():
            self._pixmap = pix
            self.update()

    def paintEvent(self, event):
        if self._pixmap is None or self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setOpacity(self._opacity)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        pw, ph = self._pixmap.width(), self._pixmap.height()
        # 上下留白：标题栏 30px + 状态栏 24px
        top_margin = 30
        bottom_margin = 24
        available_h = self.height() - top_margin - bottom_margin
        target_h = available_h
        target_w = int(pw * target_h / ph) if ph > 0 else 180
        if target_w > self.width() * 0.38:
            target_w = int(self.width() * 0.38)
            target_h = int(ph * target_w / pw) if pw > 0 else target_h

        x = self.width() - target_w
        y = top_margin
        painter.drawPixmap(int(x), int(y), target_w, target_h, self._pixmap)
        painter.end()
