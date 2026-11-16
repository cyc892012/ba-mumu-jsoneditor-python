"""
操作历史
"""
from typing import Callable, Optional
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QTableWidget, QTableWidgetItem,
    QMessageBox, QShortcut,
)
from PyQt5.QtGui import QColor, QKeySequence
from PyQt5.QtCore import Qt, QEvent
from .base_dialog import BaseDialog
from .management_helpers import EmptyTableState, StatusLabel, setup_table
from controls import RoundedButton
import theme
from core.backup_manager import BackupManager
from utils.qt_thread_bridge import ThreadBridge


class UndoHistoryForm(BaseDialog):
    def __init__(self, parent=None):
        super().__init__(parent, "操作历史")
        self.setFixedSize(520, 310)
        self._backup_manager: BackupManager = None
        self._sync_callback: Optional[Callable[[str], None]] = None
        self._history_action_callback = None
        self._keyboard_handler = None
        self._listener_paused = False
        self._history_active = False
        self._history_subscription = None
        self._subscription_generation = 0
        self._history_revision = None
        self._action_pending = False
        self._bridge = ThreadBridge(self)
        self._build()
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self._on_undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self._on_redo)

    def _build(self):
        ct = self.content_layout
        ct.setContentsMargins(8, 6, 8, 6)
        ct.setSpacing(5)
        self._tbl = QTableWidget(0, 4)
        self._tbl.setHorizontalHeaderLabels(["#", "操作时间", "操作", "按键数"])
        setup_table(self._tbl, 2, {0: 46, 1: 158, 3: 60})
        # 选中其他历史时，当前位置仍保留浅粉底和紫色文字。
        self._tbl.setStyleSheet(self._tbl.styleSheet() +
            f"QTableWidget::item:selected {{ background: {theme.PINK_50}; color: {theme.VIOLET_700}; }}")
        self._tbl.cellDoubleClicked.connect(self._on_row_double_clicked)
        self._tbl.itemSelectionChanged.connect(self._update_jump_state)
        self._empty_state = EmptyTableState(self._tbl, "暂无操作历史\n打开方案并进行编辑后，操作记录将显示在这里。")
        ct.addWidget(self._tbl, 1)
        actions = QHBoxLayout()
        actions.setSpacing(5)
        self._btn_undo = RoundedButton("撤销", "secondary")
        self._btn_redo = RoundedButton("重做", "secondary")
        self._btn_jump = RoundedButton("跳转到选中", "primary")
        actions.addWidget(self._btn_undo)
        actions.addWidget(self._btn_redo)
        actions.addStretch()
        actions.addWidget(self._btn_jump)
        ct.addLayout(actions)
        self._btn_undo.clicked.connect(self._on_undo)
        self._btn_redo.clicked.connect(self._on_redo)
        self._btn_jump.clicked.connect(self._on_jump_to_selected)
        self._lbl_status = StatusLabel("当前位置：--")
        self.status_bar.addWidget(self._lbl_status, 1)
        self.refresh()

    # ── 公开接口 ──

    def set_backup_manager(self, backup_mgr: BackupManager) -> None:
        """接受外部 BackupManager 并刷新"""
        if backup_mgr is not self._backup_manager:
            self._unsubscribe_history()
            self._backup_manager = backup_mgr
        self._subscribe_history()
        self.refresh()

    def set_sync_callback(self, callback: Callable[[str], None]) -> None:
        """设置同步回调：当撤销/重做/跳转时，将新的 JSON 字符串传回主窗口"""
        self._sync_callback = callback

    def set_history_action_callback(self, callback):
        """主窗事务接口：(action, index, expected_revision)，写盘并提交历史后返回结果。"""
        self._history_action_callback = callback

    def set_keyboard_handler(self, handler):
        self._resume_listener()
        self._keyboard_handler = handler
        if self._history_active:
            self._pause_listener()

    def _pause_listener(self):
        if self._keyboard_handler is not None and not self._listener_paused:
            self._keyboard_handler.pause()
            self._listener_paused = True

    def _resume_listener(self):
        if self._keyboard_handler is not None and self._listener_paused:
            self._keyboard_handler.resume()
            self._listener_paused = False

    def _subscribe_history(self):
        if self._backup_manager is None or self._history_subscription is not None:
            return
        manager = self._backup_manager
        self._subscription_generation += 1
        generation = self._subscription_generation
        callback = lambda: self._bridge.post(self._on_history_changed, manager, generation)
        manager.subscribe_history_changed(callback)
        self._history_subscription = (manager, callback)

    def _unsubscribe_history(self):
        self._subscription_generation += 1
        if self._history_subscription is not None:
            manager, callback = self._history_subscription
            manager.unsubscribe_history_changed(callback)
            self._history_subscription = None

    def _on_history_changed(self, manager, generation):
        if manager is self._backup_manager and generation == self._subscription_generation:
            self.refresh()

    def showEvent(self, event):
        self._subscribe_history()
        self.refresh()
        self._history_active = True
        self._pause_listener()
        super().showEvent(event)

    def event(self, event):
        # 接口注入前构造 QWidget 也可能收到事件。
        if hasattr(self, "_history_active"):
            if event.type() == QEvent.WindowActivate:
                self._history_active = True
                self._pause_listener()
            elif event.type() == QEvent.WindowDeactivate:
                self._history_active = False
                self._resume_listener()
        return super().event(event)

    def hideEvent(self, event):
        self._history_active = False
        self._resume_listener()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._unsubscribe_history()
        self._history_active = False
        self._resume_listener()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        """Esc 关闭窗口（对应 C# ProcessCmdKey）"""
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def refresh(self) -> None:
        """从 BackupManager 拉取元数据，刷新表格"""
        tbl = self._tbl
        tbl.setRowCount(0)
        if self._backup_manager is None:
            self._history_revision = None
            self._lbl_status.setText("当前位置：--")
            self._btn_undo.setEnabled(False)
            self._btn_redo.setEnabled(False)
            self._btn_jump.setEnabled(False)
            self._empty_state.sync()
            return

        revision = self._backup_manager.get_revision()
        metadatas = self._backup_manager.get_history()
        current_idx = self._backup_manager.get_current_index()
        if revision != self._backup_manager.get_revision():
            self._history_revision = None
            self._bridge.post(self.refresh)
            self._empty_state.sync("历史正在更新\n请稍候。")
            return
        self._history_revision = revision

        for i, info in enumerate(metadatas):
            tbl.insertRow(i)
            is_current = (i == current_idx)
            tbl.setItem(i, 0, QTableWidgetItem(f"{'● ' if is_current else ''}{i + 1}"))
            tbl.item(i, 0).setData(Qt.UserRole, (revision, i))
            tbl.setItem(i, 1, QTableWidgetItem(
                info.timestamp.strftime("%Y-%m-%d %H:%M:%S") if info.timestamp else ""
            ))
            tbl.setItem(i, 2, QTableWidgetItem(info.operation_type or info.description))
            operation = tbl.item(i, 2).text()
            tbl.item(i, 2).setToolTip(
                f"{operation}\n{info.description}" if info.description and info.description != operation else operation)
            tbl.item(i, 0).setToolTip(tbl.item(i, 0).text())
            tbl.item(i, 1).setToolTip(info.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f") if info.timestamp else "")
            tbl.setItem(i, 3, QTableWidgetItem(str(info.key_count)))
            tbl.item(i, 3).setToolTip(str(info.key_count))

            if is_current:
                for col in range(4):
                    item = tbl.item(i, col)
                    if item:
                        item.setBackground(QColor(theme.PINK_50))
                        item.setForeground(QColor(theme.VIOLET_700))
                marker = tbl.item(i, 0)
                font = marker.font()
                font.setBold(True)
                marker.setFont(font)
                marker.setToolTip(f"当前位置：#{i + 1}")

        self._lbl_status.setText(f"当前位置：#{current_idx + 1}/{len(metadatas)}" if metadatas else "暂无历史记录")
        self._btn_undo.setEnabled(bool(self._backup_manager.can_undo))
        self._btn_redo.setEnabled(bool(self._backup_manager.can_redo))
        if 0 <= current_idx < tbl.rowCount():
            tbl.selectRow(current_idx)
            tbl.scrollToItem(tbl.item(current_idx, 0))
        self._update_jump_state()
        self._empty_state.sync("暂无操作历史\n打开方案并进行编辑后，操作记录将显示在这里。")

    def _update_jump_state(self):
        if not hasattr(self, "_btn_jump"):
            return
        row = self._tbl.currentRow()
        item = self._tbl.item(row, 0) if row >= 0 else None
        identity = item.data(Qt.UserRole) if item else None
        manager = self._backup_manager
        self._btn_jump.setEnabled(bool(manager and identity and
            identity[0] == manager.get_revision() and identity[1] != manager.get_current_index()))

    # ── 槽函数 ──

    def _on_row_double_clicked(self, row: int, col: int) -> None:
        self._jump_with_confirm(row)

    def _on_undo(self) -> None:
        if self._backup_manager is not None and self._backup_manager.can_undo:
            self._request_action("undo", self._backup_manager.get_current_index() - 1, self._history_revision)

    def _on_redo(self) -> None:
        if self._backup_manager is not None and self._backup_manager.can_redo:
            self._request_action("redo", self._backup_manager.get_current_index() + 1, self._history_revision)

    def _reject_stale(self):
        self.refresh()
        self._lbl_status.setText("历史已变化，请重新选择")

    def _request_action(self, action, index, revision):
        manager = self._backup_manager
        if manager is None or self._action_pending:
            return
        if revision is None or revision != manager.get_revision():
            self._reject_stale()
            return
        self._action_pending = True
        try:
            if self._history_action_callback is not None:
                # 事务所有权属于主窗，不在此提前移动指针或裁除 redo。
                result = self._history_action_callback(action, index, revision)
            elif self._sync_callback is not None:
                snapshot = manager.peek_snapshot(index)
                if snapshot is None:
                    self._reject_stale()
                    return
                result = self._sync_callback(snapshot)
                if result is not False and manager is self._backup_manager and revision == manager.get_revision():
                    # 旧回调 None 表示已完成；兼容路径也先写盘，再提交历史。
                    if action == "undo":
                        manager.undo()
                    elif action == "redo":
                        manager.redo()
                    else:
                        manager.jump_to(index)
            else:
                self._lbl_status.setText("尚未连接历史写回接口，未修改历史")
                return
            if result is False:
                self._lbl_status.setText("历史恢复失败，未提交操作")
                return
            self.refresh()
        except Exception as exc:
            self._lbl_status.setText(f"历史恢复失败：{exc}")
        finally:
            self._action_pending = False

    def _jump_with_confirm(self, row: int) -> None:
        """跳转前确认（会丢弃之后的记录，对应 C# btnJump_Click 确认）"""
        if self._backup_manager is None or row < 0:
            return
        manager = self._backup_manager
        item = self._tbl.item(row, 0)
        identity = item.data(Qt.UserRole) if item else None
        if not identity or identity[0] != manager.get_revision():
            self._reject_stale()
            return
        revision, index = identity
        current = manager.get_current_index()
        if index == current:
            return
        discarded = manager.get_snapshot_count() - index - 1
        reply = QMessageBox.question(
            self, "确认跳转",
            f"将丢弃此版本之后的 {discarded} 条操作记录，确定继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if manager is not self._backup_manager or revision != manager.get_revision():
            self._reject_stale()
            return
        self._request_action("jump", index, revision)

    def _on_jump_to_selected(self) -> None:
        if self._backup_manager is None:
            return
        row = self._tbl.currentRow()
        if row < 0:
            return
        self._jump_with_confirm(row)
