"""
日志查看器
"""
import os
from utils.open_folder import open_folder
from datetime import datetime
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem,
    QLineEdit, QTextEdit, QDialog, QMessageBox, QApplication,
)
from PyQt5.QtCore import Qt
from .base_dialog import BaseDialog
from .management_helpers import EmptyTableState, StatusLabel, setup_table, add_cell_tooltips
from controls import RoundedButton
import theme


class LogViewerForm(BaseDialog):
    def __init__(self, parent=None):
        super().__init__(parent, "日志查看器")
        self.setFixedSize(520, 330)
        self._all_files = []          # [(path, name, mtime, size), ...]
        # 与日志服务同源：源码版 / exe 版 / MOMO_DATA_DIR 一致
        from core.config_manager import resolve_app_dir
        self._log_dir = os.path.join(resolve_app_dir(), "Logs")
        self._build()

    def _build(self):
        ct = self.content_layout
        ct.setContentsMargins(8, 6, 8, 6)
        ct.setSpacing(5)
        search_row = QHBoxLayout()
        search_row.setSpacing(5)
        self._txt_search = QLineEdit()
        self._txt_search.setPlaceholderText("搜索日志文件名…")
        self._txt_search.setClearButtonEnabled(True)
        search_row.addWidget(self._txt_search, 1)
        self._btn_refresh = RoundedButton("刷新", "secondary")
        search_row.addWidget(self._btn_refresh)
        ct.addLayout(search_row)
        self._tbl = QTableWidget(0, 3)
        self._tbl.setHorizontalHeaderLabels(["文件名", "创建时间", "大小"])
        setup_table(self._tbl, 0, {1: 146, 2: 70})
        self._empty_state = EmptyTableState(self._tbl, "暂无日志文件\n日志产生后，点击“刷新”查看。")
        ct.addWidget(self._tbl, 1)
        actions = QHBoxLayout()
        actions.setSpacing(5)
        self._btn_view = RoundedButton("查看", "secondary")
        self._btn_delete = RoundedButton("删除", "danger")
        self._btn_open_dir = RoundedButton("打开目录", "secondary")
        self._btn_copy_path = RoundedButton("复制路径", "secondary")
        for button in (self._btn_view, self._btn_delete, self._btn_open_dir, self._btn_copy_path):
            actions.addWidget(button)
        actions.addStretch()
        ct.addLayout(actions)
        self._btn_view.clicked.connect(self._on_view_log)
        self._btn_delete.clicked.connect(self._on_delete_log)
        self._btn_open_dir.clicked.connect(self._on_open_log_dir)
        self._btn_copy_path.clicked.connect(self._on_copy_log_path)
        self._btn_refresh.clicked.connect(self.refresh)
        self._txt_search.textChanged.connect(self._on_search)
        self._tbl.itemSelectionChanged.connect(self._update_action_states)
        self._lbl_status = StatusLabel("")
        self.status_bar.addWidget(self._lbl_status, 1)
        self.refresh()

    # ── 公开接口 ──

    def refresh(self) -> None:
        """扫描日志目录，填充表格"""
        selected = self._selected_log()
        self._all_files.clear()
        self._tbl.setRowCount(0)

        if not os.path.isdir(self._log_dir):
            self._lbl_status.setText(f"日志目录不存在: {self._log_dir}")
            self._empty_state.sync("日志目录尚不存在\n首次产生日志后，点击“刷新”查看。")
            self._update_action_states()
            return

        try:
            entries = os.listdir(self._log_dir)
        except OSError:
            self._lbl_status.setText("无法读取日志目录")
            self._empty_state.sync("无法读取日志目录\n请检查目录权限后重试；详情见底部状态。")
            self._update_action_states()
            return

        log_files = []
        for name in entries:
            if not (name.lower().startswith("log_") and name.lower().endswith(".txt")):
                continue
            path = os.path.join(self._log_dir, name)
            if os.path.isfile(path):
                try:
                    stat = os.stat(path)
                    # Windows 下 st_ctime 为创建时间，与 C# File.CreationTime 对齐
                    ctime = datetime.fromtimestamp(stat.st_ctime)
                    size = stat.st_size
                    log_files.append((path, name, ctime, size))
                except OSError:
                    continue

        log_files.sort(key=lambda x: x[2], reverse=True)
        self._all_files = log_files

        for i, (path, name, mtime, size) in enumerate(log_files):
            self._tbl.insertRow(i)
            self._tbl.setItem(i, 0, QTableWidgetItem(name))
            self._tbl.item(i, 0).setData(Qt.UserRole, (path, name))
            self._tbl.setItem(i, 1, QTableWidgetItem(
                mtime.strftime("%Y-%m-%d %H:%M")
            ))
            self._tbl.setItem(i, 2, QTableWidgetItem(self._format_size(size)))
            if selected and selected[0] == path:
                self._tbl.selectRow(i)

        add_cell_tooltips(self._tbl)
        for row, (path, name, timestamp, size) in enumerate(log_files):
            self._tbl.item(row, 0).setToolTip(f"{name}\n{path}")
            self._tbl.item(row, 1).setToolTip(timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"))
        self._lbl_status.setText(f"共 {len(log_files)} 个日志文件")
        self._on_search(self._txt_search.text())

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def _selected_log(self):
        row = self._tbl.currentRow()
        if row < 0 or self._tbl.isRowHidden(row):
            return None
        item = self._tbl.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def _update_action_states(self):
        if not hasattr(self, "_btn_view"):
            return
        selected = bool(self._selected_log())
        for button in (self._btn_view, self._btn_delete, self._btn_copy_path):
            button.setEnabled(selected)

    # ── 槽函数 ──

    def _on_view_log(self) -> None:
        selected = self._selected_log()
        if not selected:
            return
        path, name = selected

        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                content = f.read()
        except OSError as e:
            QMessageBox.warning(self, "错误", f"无法读取日志文件:\n{e}")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"日志 - {name}")
        dlg.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        dlg.resize(640, 420)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(8, 8, 8, 8)

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText(content)
        txt.setStyleSheet(f"QTextEdit {{ background: {theme.BG_MAIN}; color: {theme.INK}; font-family: Consolas, monospace; font-size: 10pt; border: 1px solid {theme.BORDER_CARD}; }}")
        layout.addWidget(txt)

        btn = RoundedButton("关闭", "secondary")
        btn.clicked.connect(dlg.close)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        dlg.exec_()

    def _on_search(self, text: str) -> None:
        keyword = text.strip().lower()
        for row in range(self._tbl.rowCount()):
            item = self._tbl.item(row, 0)
            if item is None:
                self._tbl.setRowHidden(row, False)
                continue
            self._tbl.setRowHidden(row, keyword not in item.text().lower())
        if self._tbl.currentRow() >= 0 and self._tbl.isRowHidden(self._tbl.currentRow()):
            self._tbl.clearSelection()
            self._tbl.setCurrentItem(None)
        self._update_action_states()
        if self._all_files:
            message = "没有匹配的日志文件\n请调整关键词或清空搜索。"
        elif self._lbl_status.text().startswith("日志目录不存在"):
            message = "日志目录尚不存在\n首次产生日志后，点击“刷新”查看。"
        elif self._lbl_status.text() == "无法读取日志目录":
            message = "无法读取日志目录\n请检查目录权限后重试；详情见底部状态。"
        else:
            message = "暂无日志文件\n日志产生后，点击“刷新”查看。"
        self._empty_state.sync(message)

    # ── 删除日志 ──

    def _on_delete_log(self) -> None:
        selected = self._selected_log()
        if not selected:
            return
        path, name = selected
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除日志文件 [{name}] 吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            os.remove(path)
        except OSError as e:
            QMessageBox.warning(self, "错误", f"删除失败:\n{e}")
            return
        self.refresh()
        self._lbl_status.setText(f"已删除: {name}")

    # ── 打开目录 ──

    def _on_open_log_dir(self) -> None:
        if not os.path.isdir(self._log_dir):
            try:
                os.makedirs(self._log_dir, exist_ok=True)
            except OSError:
                QMessageBox.information(self, "提示", f"日志目录不存在:\n{self._log_dir}")
                return
        try:
            open_folder(self._log_dir)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法打开目录:\n{e}")

    # ── 复制路径 ──

    def _on_copy_log_path(self) -> None:
        selected = self._selected_log()
        if not selected:
            return
        path, name = selected
        clipboard = QApplication.clipboard()
        clipboard.setText(path)
        self._lbl_status.setText(f"已复制路径: {name}")

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.1f} GB"
