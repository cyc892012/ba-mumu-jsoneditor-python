"""候选主窗的纯呈现辅助；正式集成时可移入 controls，不含业务逻辑。"""
from PyQt5.QtCore import Qt, QSize, QObject, QEvent
from PyQt5.QtGui import QPainter, QColor
from PyQt5.QtWidgets import QLabel, QWidget, QVBoxLayout, QSizePolicy, QStylePainter, QStyleOptionComboBox, QStyle
from controls.rounded_button import RoundedButton
import theme


class ElidedStatusLabel(QLabel):
    """QLabel.text 始终返回完整文本，仅绘制时省略；兼容启动测速钩子。"""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height())

    def sizeHint(self):
        return QSize(220, self.fontMetrics().height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QColor(theme.INK_2))
        painter.setFont(self.font())
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideMiddle, max(0, self.width()))
        painter.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter, text)


def compact_button(text, role="ghost", width=None, height=26):
    button = RoundedButton(text, role)
    button.setStyleSheet(button.styleSheet() + f'\nQPushButton {{padding:0 5px;font-size:12px;min-height:{height-2}px;max-height:{height-2}px;}}')
    button.setFixedHeight(height)
    if width is not None:
        button.setFixedWidth(width)
    return button


def set_icon(button, family, glyph, fallback, tooltip):
    button.setText(glyph if family else fallback)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    if getattr(button, "_p2_icon_family", object()) != family:
        button.setStyleSheet(button.styleSheet() + f'\nQPushButton {{font-family:"{family or theme.FONT_FAMILY}";font-size:12px;padding:0;}}')
        button._p2_icon_family = family


class ComboElisionFilter(QObject):
    """保留现有搜索框、模型和信号，只在非编辑显示面绘制省略文本。"""
    def __init__(self, combo, refresh_tooltip=None):
        super().__init__(combo)
        self._refresh_tooltip = refresh_tooltip

    def eventFilter(self, combo, event):
        if event.type() in (QEvent.Paint, QEvent.ToolTip) and self._refresh_tooltip is not None:
            # 列表重建会屏蔽信号，取消加载也可能无状态消息；按当前项校正提示。
            self._refresh_tooltip()
        if event.type() == QEvent.Paint and not combo.isEditable():
            option = QStyleOptionComboBox()
            combo.initStyleOption(option)
            area = combo.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, combo)
            option.currentText = combo.fontMetrics().elidedText(option.currentText, Qt.ElideMiddle, max(0, area.width()))
            painter = QStylePainter(combo)
            painter.drawComplexControl(QStyle.CC_ComboBox, option)
            painter.drawControl(QStyle.CE_ComboBoxLabel, option)
            painter.end()
            return True
        return super().eventFilter(combo, event)


def install_combo_elision(combo, refresh_tooltip=None):
    # 新参数可选，保留现有调用方式。
    guard = ComboElisionFilter(combo, refresh_tooltip=refresh_tooltip)
    combo.installEventFilter(guard)
    combo._p2_elision_filter = guard
    return guard


class ToolTile(QWidget):
    """真按钮与推荐角标；角标单独预留上沿，不盖住按钮文字。"""
    def __init__(self, text, callback, recommended=False, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(36)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(0)
        self.button = compact_button(text, "secondary" if recommended else "ghost", height=30)
        self.button.setStyleSheet(self.button.styleSheet() + '\nQPushButton {font-size:11px; padding:0 3px;}')
        self.button.setMinimumWidth(0)
        self.button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.button.setToolTip(text)
        self.button.setAccessibleName(text)
        self.button.clicked.connect(callback)
        layout.addWidget(self.button)
        self.badge = None
        if recommended:
            self.badge = QLabel("推荐", self)
            self.badge.setAlignment(Qt.AlignCenter)
            self.badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.badge.setStyleSheet(f'QLabel {{background:{theme.VIOLET};color:white;border:none;border-radius:6px;font-size:9px;}}')
            self.badge.setFixedSize(26, 13)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.badge is not None:
            self.badge.move(self.width() - self.badge.width() - 2, 0)
