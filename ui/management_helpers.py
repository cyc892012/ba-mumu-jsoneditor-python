"""管理窗体私有呈现辅助；不读写业务数据，不修改全局主题。"""
from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QLabel, QSizePolicy, QAbstractItemView, QHeaderView
from controls.searchable_combo_box import SearchableComboBox
import theme


class IndexFileComboBox(SearchableComboBox):
    """显示服务器名，业务接口仍返回真实文件名，原始值稳定绑定到 itemData。"""
    _servers = {
        "com.roamingstar.bluearchive.json": "官服",
        "com.roamingstar.bluearchive.bilibili.json": "B服",
        "com.yostarjp.bluearchive.json": "日服",
        "com.nexon.bluearchive.json": "国际服",
    }

    def addItem(self, text, userData=None):
        server = self._servers.get(text.casefold())
        display = f"{server}（{text}）" if server else text
        super().addItem(display, text if userData is None else userData)
        self.combo.setItemData(self.count() - 1, text, Qt.UserRole + 1)
        self.combo.setItemData(self.count() - 1, display, Qt.ToolTipRole)

    def currentText(self):
        return self.currentData(Qt.UserRole + 1) or ""

    def findText(self, text, flags=Qt.MatchExactly | Qt.MatchCaseSensitive):
        index = self.combo.findData(text, Qt.UserRole + 1, flags)
        return index if index >= 0 else super().findText(text, flags)

    def setCurrentText(self, text):
        index = self.findText(text)
        if index >= 0:
            self.setCurrentIndex(index)


class EmptyTableState(QLabel):
    """覆盖在空视口内，始终保留零条真实表格行。"""
    def __init__(self, table, message):
        super().__init__(table.viewport())
        self.table = table
        self.message = message
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setTextFormat(Qt.PlainText)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet(f"color: {theme.INK_2}; background: transparent; padding: 8px;")
        table.viewport().installEventFilter(self)
        for signal in (table.model().rowsInserted, table.model().rowsRemoved,
                       table.model().modelReset):
            signal.connect(self.schedule_sync)
        self.sync()

    def schedule_sync(self, *args):
        QTimer.singleShot(0, self.sync)

    def sync(self, message=None):
        if message is not None:
            self.message = message
        self.setText(self.message)
        self.setGeometry(self.table.viewport().rect())
        visible_rows = any(not self.table.isRowHidden(r) for r in range(self.table.rowCount()))
        self.setVisible(not visible_rows)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.sync()
        return super().eventFilter(watched, event)


class StatusLabel(QLabel):
    """保留原始 text()，长路径可悬停完整查看，避免挤压状态栏。"""
    def __init__(self, text=""):
        super().__init__(text)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(text)
        self.setTextFormat(Qt.PlainText)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        # 保留text()供业务/测试使用，只在绘制时中间省略长路径，不静默裁断。
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideMiddle, self.contentsRect().width())
        if text == self.text():
            return super().paintEvent(event)
        painter = QPainter(self)
        self.style().drawItemText(painter, self.contentsRect(), self.alignment(), self.palette(),
                                  self.isEnabled(), text, self.foregroundRole())


def setup_table(table, stretch_column, fixed_columns):
    table.setStyleSheet(theme.TABLE_STYLE)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(26)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setMinimumHeight(80)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.ElideMiddle)
    header = table.horizontalHeader()
    header.setSectionResizeMode(stretch_column, QHeaderView.Stretch)
    for col, width in fixed_columns.items():
        header.setSectionResizeMode(col, QHeaderView.Interactive)
        table.setColumnWidth(col, width)


def add_cell_tooltips(table):
    for row in range(table.rowCount()):
        for col in range(table.columnCount()):
            item = table.item(row, col)
            if item is not None:
                item.setToolTip(item.text())
