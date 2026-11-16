"""统一主题消息/输入弹窗，保留监督确认键盘安全边界。"""
from math import ceil
from PyQt5.QtCore import QEvent, QEventLoop, QPointF, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QFont, QPainter, QPen, QPolygonF, QTextOption
from PyQt5.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)
from .base_dialog import BaseDialog
from controls import RoundedButton
import theme


class _TypeIcon(QWidget):
    """用 Qt 绘制类型图标，避免符号缺字或新增图片依赖。"""

    def __init__(self, kind, parent):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(30, 32)
        self.setAccessibleName({"information": "信息", "warning": "警告",
                                "error": "错误", "question": "确认"}[kind])

    def paintEvent(self, event):
        colors = {"information": (theme.VIOLET, theme.VIOLET_50),
                  "question": (theme.VIOLET, theme.VIOLET_50),
                  "warning": (theme.AMBER, theme.AMBER_50),
                  "error": (theme.RED, theme.RED_50)}
        foreground, background = colors[self.kind]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(background))
        if self.kind == "warning":
            painter.drawPolygon(QPolygonF([QPointF(15, 2), QPointF(29, 28), QPointF(1, 28)]))
        else:
            painter.drawEllipse(1, 1, 28, 28)
        painter.setPen(QPen(QColor(foreground), 2.2, Qt.SolidLine, Qt.RoundCap))
        if self.kind == "error":
            painter.drawLine(11, 11, 19, 19)
            painter.drawLine(19, 11, 11, 19)
        elif self.kind == "warning":
            painter.drawLine(15, 11, 15, 18)
            painter.drawPoint(15, 23)
        else:
            painter.setFont(QFont(theme.FONT_FAMILY, 14, QFont.Bold))
            painter.drawText(self.rect().adjusted(0, -2, 0, -2), Qt.AlignCenter,
                             "?" if self.kind == "question" else "i")



class MessageDialog(BaseDialog):
    """统一主题弹窗；默认取消，正文有界滚动，输入取消返回空值。"""

    finished = pyqtSignal(bool)

    def __init__(self, parent=None, title="提示", message="", confirm_text="确认",
                 cancel_text="取消", show_cancel=True, *, kind=None, danger=False,
                 default_cancel=None):
        super().__init__(parent, title)
        kind = kind or ("question" if show_cancel else "information")
        if kind not in {"information", "warning", "error", "question"}:
            raise ValueError("kind 必须为 information、warning、error 或 question")
        self.setWindowTitle(title)
        self._result = False
        self._finished = False
        self._closing_accept = False
        self._danger = bool(danger)
        self._input_container = None
        self._input_focus = None
        # 危险动作始终提供取消，不允许用 default_cancel=False 绕过安全默认值。
        show_cancel = bool(show_cancel or danger)
        self._show_cancel = show_cancel
        default_cancel = bool(danger or (show_cancel if default_cancel is None else default_cancel))
        self.status_bar.hide()

        ct = self.content_layout
        ct.setContentsMargins(12, 10, 12, 10)
        ct.setSpacing(8)
        self._message_row = QWidget()
        body = QHBoxLayout(self._message_row)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        self._icon = _TypeIcon("warning" if danger else kind, self)
        body.addWidget(self._icon, 0, Qt.AlignTop)
        self._msg = QTextEdit()
        self._msg.setPlainText(message)
        self._msg.setAcceptRichText(False)
        self._msg.setReadOnly(True)
        self._msg.setFrameShape(QFrame.NoFrame)
        self._msg.document().setDocumentMargin(0)
        self._msg.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self._msg.setAccessibleName("消息内容")
        self._msg.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self._msg.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._msg.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; background: transparent; border: none;")
        self._scroll = self._msg
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.addWidget(self._scroll, 1)
        ct.addWidget(self._message_row)

        self._buttons = QWidget()
        row = QHBoxLayout(self._buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addStretch()
        self._cancel_btn = None
        if show_cancel:
            self._cancel_btn = RoundedButton(cancel_text, "secondary")
            self._cancel_btn.clicked.connect(self.reject)
            row.addWidget(self._cancel_btn)
        self._confirm_btn = RoundedButton(confirm_text, "danger" if danger else "primary")
        self._confirm_btn.clicked.connect(self.accept)
        row.addWidget(self._confirm_btn)
        self._default_btn = self._cancel_btn if default_cancel and show_cancel else self._confirm_btn
        for button in (self._cancel_btn, self._confirm_btn):
            if button is not None:
                button.setMinimumWidth(76)
                button.setFixedHeight(theme.H_BTN)
                button.setAutoDefault(False)
                button.setDefault(button is self._default_btn)
                button.setStyleSheet(button.styleSheet() +
                                    f"QPushButton:focus {{border: 2px solid {theme.VIOLET}; min-height: 22px;}}")
        ct.addWidget(self._buttons)

    def accept(self):
        if self._confirm_btn.isEnabled():
            self._result = True
            self._closing_accept = True
            self.close()

    def reject(self):
        self._result = False
        self._closing_accept = False
        self.close()

    def keyPressEvent(self, event):
        """危险确认的键盘默认项为取消，Enter/Escape 不外传为生成键。"""
        if event.key() == Qt.Key_Escape or (self._show_cancel and event.key() in (Qt.Key_Return, Qt.Key_Enter)):
            self.reject()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept()
            event.accept()
            return
        super().keyPressEvent(event)

    @property
    def result(self):
        """与旧版本一致，表示最近一次关闭是否确认。"""
        return self._result

    # ── 静态工厂方法 ──

    @staticmethod
    def question(parent, title, message, confirm="确认", cancel="取消", *, danger=False,
                 default_cancel=True):
        """返回 True/False；关闭和 Esc 均为 False，危险确认始终默认取消。"""
        return MessageDialog(parent, title, message, confirm, cancel, True,
                             kind="question", danger=danger, default_cancel=default_cancel)._exec()

    @staticmethod
    def information(parent, title, message, confirm="确定"):
        MessageDialog(parent, title, message, confirm, show_cancel=False, kind="information")._exec()

    @staticmethod
    def warning(parent, title, message, confirm="确定"):
        MessageDialog(parent, title, message, confirm, show_cancel=False, kind="warning")._exec()


    def closeEvent(self, event):
        # 标题栏关闭、Alt+F4、Esc、取消和父窗口销毁都不能产生确认结果。
        super().closeEvent(event)
        if event.isAccepted() and not self._finished:
            self._result = self._closing_accept
            self._closing_accept = False
            self._finished = True
            self.finished.emit(self._result)

    def _available_geometry(self):
        parent = self.parentWidget()
        screen = QApplication.screenAt(parent.frameGeometry().center() if parent else QCursor.pos())
        screen = screen or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(0, 0, 800, 600)

    def _input_height(self, available_height):
        return 0

    def _fit_to_screen(self):
        """正文滚动，输入与底部按钮保留；以 Qt 逻辑像素适配当前屏幕。"""
        self.ensurePolished()
        area = self._available_geometry()
        available = area.adjusted(16, 16, -16, -16)
        wide = len(self._msg.toPlainText()) > 140
        preferred_width = getattr(self, "_preferred_width", 420 if wide else 350)
        width = min(preferred_width, available.width())
        # 默认限在屏幕高度约七成内；超出部分使用正文/列表自己的滚动区域。
        max_height = min(available.height(), max(160, min(340, int(area.height() * 0.72))))
        self.setMinimumSize(0, 0)
        self.setMaximumSize(width, max_height)
        self.setFixedWidth(width)
        margins = self.content_layout.contentsMargins()
        text_width = max(1, width - margins.left() - margins.right() -
                         self._icon.width() - self._message_row.layout().spacing() - 8)
        # 文档的自动折行模式也处理没有空格的长路径/键值；预留滚动条宽度。
        document = self._msg.document().clone()
        document.setTextWidth(text_width)
        text_height = ceil(document.size().height()) + 2
        button_height = self._buttons.sizeHint().height()
        spacing = self.content_layout.spacing()
        overhead = self.title_bar.height() + margins.top() + margins.bottom() + button_height + spacing
        if self._input_container is not None:
            input_height = min(self._input_height(max_height), max(0, max_height - overhead - spacing - 32))
            self._input_container.setFixedHeight(input_height)
            overhead += input_height + spacing
        minimum = min(160, max_height)
        self.setMinimumHeight(minimum)
        height = min(max_height, max(minimum, overhead + max(32, text_height)))
        self._message_row.setFixedHeight(max(32, height - overhead))
        self.resize(width, height)
        self.layout().activate()
        center = self.parentWidget().frameGeometry().center() if self.parentWidget() else area.center()
        x = max(available.left(), min(center.x() - width // 2, available.right() - width + 1))
        y = max(available.top(), min(center.y() - height // 2, available.bottom() - height + 1))
        self.move(x, y)

    def showEvent(self, event):
        self._result = False
        self._finished = False
        self._closing_accept = False
        self._fit_to_screen()
        # BaseDialog 是 QWidget，需在本窗口内补齐 QDialog 的回车/Esc 语义。
        self.installEventFilter(self)
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)
        super().showEvent(event)
        (self._input_focus or self._default_btn).setFocus(Qt.OtherFocusReason)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress, QEvent.KeyRelease):
            key = event.key()
            if key in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
                # 修饰键也不能让确认/取消按键外传；松开只消费，不重复执行动作。
                event.accept()
                if event.type() == QEvent.KeyPress:
                    if key == Qt.Key_Escape:
                        self.reject()
                    else:
                        focus = QApplication.focusWidget()
                        if focus is not None and focus in (self._confirm_btn, self._cancel_btn):
                            focus.click()
                        elif focus is self._input_focus:
                            self._confirm_btn.click()
                        else:
                            self._default_btn.click()
                return True
        return super().eventFilter(watched, event)

    def _exec(self):
        """局部事件循环持有 Python 引用，销毁父窗口时也能退出。"""
        loop = QEventLoop()
        self.finished.connect(loop.quit)
        self.destroyed.connect(loop.quit)
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.show()
        loop.exec_()
        # C++ 对象可能已销毁，此处只读取 Python 字段。
        return self._result

    @staticmethod
    def error(parent, title, message, confirm="确定"):
        """错误提示，返回 None，与 information/warning 的提示语义一致。"""
        MessageDialog(parent, title, message, confirm, show_cancel=False, kind="error")._exec()

    @staticmethod
    def get_text(parent, title, label, echo=QLineEdit.Normal, text="", *, confirm="确定", cancel="取消"):
        """单行文本输入；支持 QLineEdit.Password，取消不返回已输入内容。"""
        dialog = _TextInputDialog(parent, title, label, echo, text, confirm, cancel)
        accepted = dialog._exec()
        return (dialog._value, True) if accepted else ("", False)

    @staticmethod
    def get_item(parent, title, label, items, current=0, editable=True, *, confirm="确定", cancel="取消"):
        """有界滚动列表；editable=False 时只能返回原列表项，空列表不能确认。"""
        dialog = _ItemInputDialog(parent, title, label, items, current, editable, confirm, cancel)
        accepted = dialog._exec()
        return (dialog._value, True) if accepted else ("", False)

    def keyReleaseEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            event.accept()
            return
        super().keyReleaseEvent(event)


class _TextInputDialog(MessageDialog):
    def __init__(self, parent, title, label, echo, text, confirm, cancel):
        super().__init__(parent, title, label, confirm, cancel, kind="information", default_cancel=False)
        self._value = ""
        self._preferred_width = 380
        self._edit = QLineEdit(text)
        self._edit.setFixedHeight(theme.H_INPUT)
        self._edit.setEchoMode(echo)
        self._edit.setAccessibleName(label)
        self._edit.selectAll()
        self._input_container = self._edit
        self._input_focus = self._edit
        self.content_layout.insertWidget(1, self._edit)

    def _input_height(self, available_height):
        return max(theme.H_INPUT, self._edit.sizeHint().height())

    def accept(self):
        self._value = self._edit.text()
        super().accept()

    def closeEvent(self, event):
        if not self._closing_accept:
            self._value = ""
        # 关闭时清理编辑器中的密码，不将内容用于标题、提示或日志。
        self._edit.clear()
        super().closeEvent(event)



class _ItemInputDialog(MessageDialog):
    def __init__(self, parent, title, label, items, current, editable, confirm, cancel):
        super().__init__(parent, title, label, confirm, cancel, kind="information", default_cancel=False)
        self._value = ""
        self._preferred_width = 400
        self._list = QListWidget()
        self._list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self._list.setAccessibleName(label)
        self._list.setStyleSheet(
            f"QListWidget {{background: {theme.CARD}; border: 1px solid {theme.LINE_STRONG};"
            f"border-radius: 6px; outline: none;}}"
            "QListWidget::item {padding: 4px 8px;}"
            f"QListWidget::item:selected {{background: {theme.PINK_50}; color: {theme.INK};}}"
            f"QListWidget::item:hover {{background: {theme.VIOLET_50};}}")
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        for text in items:
            item = QListWidgetItem(str(text))
            item.setToolTip(str(text))
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(current if 0 <= current < self._list.count() else 0)
        self._edit = QLineEdit(self._list.currentItem().text() if self._list.currentItem() else "") if editable else None
        self._input_container = QWidget()
        layout = QVBoxLayout(self._input_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        if self._edit is not None:
            self._edit.setFixedHeight(theme.H_INPUT)
            layout.addWidget(self._edit)
            self._list.currentTextChanged.connect(self._edit.setText)
        layout.addWidget(self._list, 1)
        self._input_focus = self._edit if editable else self._list
        self._confirm_btn.setEnabled(bool(editable or self._list.currentItem() is not None))
        self._list.itemDoubleClicked.connect(lambda item: self.accept())
        self.content_layout.insertWidget(1, self._input_container)

    def showEvent(self, event):
        super().showEvent(event)
        # 列表完成首次布局后，确保靠后的初始选中项也出现在视口中。
        if self._list.currentItem() is not None:
            self._list.scrollToItem(self._list.currentItem())

    def _input_height(self, available_height):
        row_height = self._list.sizeHintForRow(0) if self._list.count() else 30
        rows = max(2, min(5, self._list.count()))
        edit_height = self._edit.sizeHint().height() + 8 if self._edit is not None else 0
        return min(rows * row_height + 4 + edit_height, max(60, available_height // 2))

    def accept(self):
        if not self._confirm_btn.isEnabled():
            return
        self._value = self._edit.text() if self._edit is not None else self._list.currentItem().text()
        super().accept()

    def closeEvent(self, event):
        if not self._closing_accept:
            self._value = ""
        super().closeEvent(event)

