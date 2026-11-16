"""保留 QCheckBox 公共接口的状态胶囊，无定时器和后台行为。"""
from PyQt5.QtCore import Qt, QSize, QRectF
from PyQt5.QtGui import QColor, QPainter, QPen, QFontMetrics
from PyQt5.QtWidgets import QCheckBox, QSizePolicy

import theme


class StatusPill(QCheckBox):
    def __init__(self, parent=None, *, checked_text="自动同步中", unchecked_text="自动同步已关",
                 checked=False, interactive=True, kind="success"):
        super().__init__(parent)
        self._checked_text = checked_text
        self._unchecked_text = unchecked_text
        self._interactive = interactive
        self._kind = kind
        font = self.font()
        font.setFamily(theme.FONT_FAMILY)
        font.setPixelSize(11)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor if interactive else Qt.ArrowCursor)
        self.setFocusPolicy(Qt.StrongFocus if interactive else Qt.NoFocus)
        self.toggled.connect(self._refresh_text)
        self.setChecked(checked)
        self._refresh_text()

    def _refresh_text(self, *_):
        self.setText(self._checked_text if self.isChecked() else self._unchecked_text)
        self.setAccessibleName(self.text())
        self.setToolTip(("点击切换；" if self._interactive else "") + self.text())
        self.updateGeometry()
        self.update()

    def setStateTexts(self, checked_text, unchecked_text):
        self._checked_text, self._unchecked_text = checked_text, unchecked_text
        self._refresh_text()

    def setChecked(self, checked):
        super().setChecked(checked)
        # QSignalBlocker 会屏蔽 toggled；业务静默赋值时也要同步可见文字。
        self._refresh_text()

    def setCheckState(self, state):
        super().setCheckState(state)
        self._refresh_text()

    def toggle(self):
        super().toggle()
        self._refresh_text()

    def setKind(self, kind):
        if kind not in ("success", "warning", "danger", "info"):
            raise ValueError(f"未知状态类型：{kind!r}")
        self._kind = kind
        self.update()

    def sizeHint(self):
        metrics = QFontMetrics(self.font())
        # 两种状态取较宽值，切换时不会挤动卡片标题。
        width = max(metrics.horizontalAdvance(text) for text in (self._checked_text, self._unchecked_text))
        return QSize(width + 29, max(22, metrics.height() + 6))

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, pos):
        return self._interactive and self.rect().contains(pos)

    def nextCheckState(self):
        if self._interactive:
            super().nextCheckState()
            self._refresh_text()

    def paintEvent(self, event):
        active = self.isChecked() and self.isEnabled()
        colors = {
            "success": (theme.MINT_50, "#2F7D5B"),
            "warning": (theme.AMBER_50, "#8A5A10"),
            "danger": (theme.RED_50, "#A72C44"),
            "info": (theme.VIOLET_50, theme.VIOLET_700),
        }
        background, foreground = colors.get(self._kind, colors["success"]) if active else ("#F1EDF0", theme.INK_2)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.VIOLET if self.hasFocus() else background), 1))
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 11, 11)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(foreground))
        painter.drawEllipse(QRectF(8, self.height() / 2 - 3, 6, 6))
        painter.setFont(self.font())
        painter.setPen(QColor(foreground))
        painter.drawText(self.rect().adjusted(20, 0, -8, 0), Qt.AlignLeft | Qt.AlignVCenter, self.text())


AutoSyncPill = StatusPill
