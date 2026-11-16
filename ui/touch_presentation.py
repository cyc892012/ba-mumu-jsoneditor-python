"""触控窗口私有展示组件；原始坐标、行身份和生成状态仍由面板持有。"""
from enum import IntEnum
from types import MappingProxyType

from PyQt5.QtCore import Qt, QSize, pyqtSignal
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QLabel, QPushButton, QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView
from controls import KeyCaptureEdit, KEY_TYPE_ITEMS
from controls.segmented import SegmentedControl
from controls.keycap_style import apply_keycap_style
import theme


class TouchColumn(IntEnum):
    CHECK = 0
    SEQUENCE = 1
    SCREEN = 2
    RELATIVE = 3
    HITS = 4
    KEYCAP = 5
    TYPE = 6


COLUMNS = MappingProxyType({
    "check": TouchColumn.CHECK, "sequence": TouchColumn.SEQUENCE,
    "screen": TouchColumn.SCREEN, "relative": TouchColumn.RELATIVE,
    "hits": TouchColumn.HITS, "keycap": TouchColumn.KEYCAP, "type": TouchColumn.TYPE,
})
ROW_ROLE = Qt.UserRole + 30
RAW_ROLE = Qt.UserRole + 31


class DetailLabel(QLabel):
    messageChanged = pyqtSignal(str)

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setToolTip(text)
        self.setStyleSheet(f"color: {theme.INK_2}; background: transparent;")

    def setText(self, text):
        changed = self.text() != text
        super().setText(text)
        self.setToolTip(text)
        if changed:
            self.messageChanged.emit(text)


class ElidedStatusLabel(DetailLabel):
    """只省略底栏绘制，完整原文仍在详情区及tooltip。"""
    def sizeHint(self):
        return QSize(80, 20)

    def minimumSizeHint(self):
        return QSize(0, 20)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().windowText().color())
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideMiddle, self.width())
        painter.drawText(self.rect(), Qt.AlignVCenter | Qt.AlignLeft, text)


def type_segments():
    control = SegmentedControl()
    for name, value in KEY_TYPE_ITEMS:
        control.addItem(name, value)
    control.setItemCaption(0, "点击")
    control.setItemCaption(1, "宏指令")
    return control


def comfortable_controls(*widgets):
    """保持9pt文字和至少26px高度，不通过缩小字号换空间。"""
    for widget in widgets:
        font = widget.font()
        font.setPointSize(9)
        widget.setFont(font)
        widget.setMinimumHeight(26)
        if isinstance(widget, QPushButton):
            # Qt样式polish会重设min-height；在当前按钮上覆盖，不改共享组件。
            widget.setStyleSheet(widget.styleSheet() + "\nQPushButton {font-size:9pt; min-height:24px; padding-top:0px; padding-bottom:0px;}")


class TouchTable(QTableWidget):
    def __init__(self, owner):
        super().__init__(0, len(COLUMNS), owner)
        self.owner = owner
        self.setHorizontalHeaderLabels(["选择", "序号", "屏幕坐标", "相对坐标", "次数", "键帽", "类型"])
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(38)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setStyleSheet(theme.TABLE_STYLE)
        self.setSortingEnabled(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
        widths = {"check": 40, "sequence": 36, "screen": 110, "relative": 126,
                  "hits": 40, "keycap": 112, "type": 106}
        header = self.horizontalHeader()
        header.setMinimumSectionSize(28)
        for name, column in COLUMNS.items():
            header.setSectionResizeMode(column, QHeaderView.Stretch if name in ("screen", "relative") else QHeaderView.Fixed)
            self.setColumnWidth(column, widths[name])
        self.empty_hint = QLabel("还没有采集点\n连接后点击模拟器，再在键帽中绑定按键。", self.viewport())
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.empty_hint.setStyleSheet(f"color: {theme.INK_2}; background: transparent;")
        self._sync_empty()

    def sizeHint(self):
        return QSize(580, self.horizontalHeader().height() + 5 * self.verticalHeader().defaultSectionSize() + 2 * self.frameWidth())

    def minimumSizeHint(self):
        return QSize(560, self.sizeHint().height())

    def _sync_empty(self):
        self.empty_hint.setGeometry(self.viewport().rect().adjusted(8, 2, -8, -2))
        self.empty_hint.setVisible(self.rowCount() == 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "empty_hint"):
            self._sync_empty()

    def setRowCount(self, count):
        super().setRowCount(count)
        if hasattr(self, "empty_hint"):
            self._sync_empty()

    def insertRow(self, row):
        super().insertRow(row)
        self._sync_empty()

    def render_coordinates(self, row, data):
        # 每个语义列只写一次；显示文本不参与坐标计算或生成。
        for column, values, precision in (
            (TouchColumn.SCREEN, (data.screen_x, data.screen_y), 2),
            (TouchColumn.RELATIVE, (data.rel_x, data.rel_y), 4),
        ):
            item = QTableWidgetItem(f"{values[0]:.{precision}f}, {values[1]:.{precision}f}")
            item.setData(RAW_ROLE, values)
            item.setData(ROW_ROLE, data)
            item.setToolTip(f"X={values[0]!r}\nY={values[1]!r}\n原采集序号={data.seq}")
            item.setTextAlignment(Qt.AlignCenter)
            self.setItem(row, column, item)

    def setCellWidget(self, row, column, widget):
        if isinstance(widget, KeyCaptureEdit):
            apply_keycap_style(widget)
        else:
            comfortable_controls(widget)
        super().setCellWidget(row, int(column), widget)
        self.sync_keycap_metadata()

    def sync_keycap_metadata(self):
        for row, data in self.owner._row_data.items():
            edit = self.cellWidget(row, TouchColumn.KEYCAP)
            if isinstance(edit, KeyCaptureEdit):
                edit._vk = data.vk_code or 0
                edit.setProperty("touchRowData", data)
                edit.setToolTip(f"{data.captured_key or '未绑定'} · vk={data.vk_code}\n原采集序号 {data.seq} · Esc 清空")
