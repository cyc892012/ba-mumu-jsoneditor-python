"""
索引备份管理
"""
import os

from PyQt5.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QWidget, QLabel, QTabWidget, QSizePolicy,
    QLineEdit, QCheckBox, QTableWidget, QTableWidgetItem,
    QMessageBox,
)
from PyQt5.QtCore import Qt
from .base_dialog import BaseDialog
from controls import RoundedButton, HintLabel
from .management_helpers import (IndexFileComboBox, EmptyTableState, StatusLabel,
                                 setup_table, add_cell_tooltips)
import theme
from core.config_manager import ConfigManager
from infra.index_backup import IndexFileBackupManager


class IndexBackupForm(BaseDialog):
    def __init__(self, parent=None):
        super().__init__(parent, "索引备份管理")
        self.setFixedSize(540, 380)

        # ── 初始化后端 ──
        self._config = ConfigManager.shared()
        from core.config_manager import resolve_app_dir
        base_dir = resolve_app_dir()
        self._backup_mgr = IndexFileBackupManager(
            {
                "官服": "com.RoamingStar.BlueArchive-",
                "B服": "com.RoamingStar.BlueArchive.bilibili-",
                "日服": "com.YostarJP.BlueArchive-",
                "国际服": "com.nexon.bluearchive-",
            },
            base_dir,
        )
        json_folder = self._config.json_folder_path
        if json_folder:
            self._backup_mgr.index_directory = json_folder

        self._updating_check_state = False
        self._status_cb = None
        self._missing_items = []
        self._build()
        self._index_cb.currentIndexChanged.connect(self._on_index_changed)
        if hasattr(self._index_cb, "popupAboutToShow"):
            self._index_cb.popupAboutToShow.connect(self._refresh_all)
        self._refresh_all()

    def _build(self):
        ct = self.content_layout
        ct.setContentsMargins(8, 6, 8, 6)
        ct.setSpacing(5)
        source_row = QHBoxLayout()
        source_row.setSpacing(5)
        source_row.addWidget(QLabel("索引文件"))
        self._index_cb = IndexFileComboBox()
        self._index_cb.setMinimumWidth(0)
        self._index_cb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        files = self._backup_mgr.get_available_index_files()
        if files:
            self._index_cb.addItems(files)
        self._index_cb.setToolTip(self._index_cb.currentText())
        self._index_cb.currentTextChanged.connect(self._index_cb.setToolTip)
        source_row.addWidget(self._index_cb, 1)
        btn_refresh = RoundedButton("刷新", "secondary")
        btn_refresh.clicked.connect(self._on_refresh)
        source_row.addWidget(btn_refresh)
        ct.addLayout(source_row)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: 1px solid {theme.LINE}; background: {theme.CARD}; }}"
            f"QTabBar::tab {{ padding: 5px 14px; color: {theme.INK_2}; background: {theme.BG}; }}"
            f"QTabBar::tab:selected {{ color: {theme.VIOLET_700}; background: {theme.PINK_50};"
            f" border-bottom: 2px solid {theme.VIOLET}; }}")
        backup_page, missing_page = QWidget(), QWidget()
        backup_layout, missing_layout = QVBoxLayout(backup_page), QVBoxLayout(missing_page)
        for layout in (backup_layout, missing_layout):
            layout.setContentsMargins(6, 6, 6, 6)
            layout.setSpacing(5)
        backup_row = QHBoxLayout()
        btn_backup = RoundedButton("手动备份", "secondary")
        self._btn_backup = btn_backup
        btn_backup.clicked.connect(self._on_manual_backup)
        backup_row.addWidget(btn_backup)
        backup_row.addStretch()
        btn_restore = RoundedButton("还原选中", "danger")
        self._btn_restore = btn_restore
        btn_restore.setToolTip("危险操作：用选中备份还原当前索引文件")
        btn_restore.clicked.connect(self._on_restore_selected)
        backup_row.addWidget(btn_restore)
        btn_clean = RoundedButton("清理旧备份", "danger")
        btn_clean.setToolTip("危险操作：删除旧备份，仅保留最新 10 个")
        btn_clean.clicked.connect(self._on_clean_old_backups)
        backup_row.addWidget(btn_clean)
        backup_layout.addLayout(backup_row)
        self._tbl1 = QTableWidget(0, 3)
        self._tbl1.setHorizontalHeaderLabels(["备份时间", "索引文件", "方案数"])
        setup_table(self._tbl1, 1, {0: 146, 2: 64})
        self._tbl1.cellDoubleClicked.connect(self._on_backup_double_clicked)
        self._tbl1.itemSelectionChanged.connect(self._update_action_states)
        self._backup_empty = EmptyTableState(self._tbl1, "暂无备份记录\n可手动备份；自动备份说明见下方提示。")
        backup_layout.addWidget(self._tbl1, 1)
        backup_note = QLabel("双击备份查看方案；自动备份帮助请悬停此处。还原/清理会修改文件。")
        backup_note.setToolTip("自动备份由主窗口按方案变更排队执行，内容相同会去重；也可使用手动备份。")
        backup_note.setWordWrap(True)
        backup_note.setStyleSheet(f"color: {theme.INK_2};")
        backup_layout.addWidget(backup_note)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("搜索缺失"))
        self._search_ie = QLineEdit()
        self._search_ie.setPlaceholderText("关键词，空格或逗号分隔")
        self._search_ie.setClearButtonEnabled(True)
        self._search_ie.textChanged.connect(self._apply_search_filter)
        search_row.addWidget(self._search_ie, 1)
        btn_scan = RoundedButton("扫描缺失", "secondary")
        self._btn_scan = btn_scan
        btn_scan.clicked.connect(self._on_scan_missing)
        search_row.addWidget(btn_scan)
        missing_layout.addLayout(search_row)
        self._tbl2 = QTableWidget(0, 3)
        self._tbl2.setHorizontalHeaderLabels(["", "文件名", "状态"])
        setup_table(self._tbl2, 1, {0: 34, 2: 74})
        self._tbl2.itemChanged.connect(self._on_item_check_changed)
        self._missing_empty = EmptyTableState(self._tbl2, "暂无缺失文件\n点击“扫描缺失”检查当前索引。")
        missing_layout.addWidget(self._tbl2, 1)
        fill_row = QHBoxLayout()
        self._select_all_cb = QCheckBox("全选")
        self._select_all_cb.stateChanged.connect(self._on_select_all_changed)
        fill_row.addWidget(self._select_all_cb)
        self._missing_count = QLabel("已选 0 / 可见 0 / 共 0")
        fill_row.addWidget(self._missing_count)
        fill_row.addStretch()
        btn_fill = RoundedButton("补全索引", "primary")
        self._btn_fill = btn_fill
        btn_fill.clicked.connect(self._on_fill_missing)
        fill_row.addWidget(btn_fill)
        missing_layout.addLayout(fill_row)
        self._tabs.addTab(backup_page, "备份记录")
        self._tabs.addTab(missing_page, "补全缺失")
        ct.addWidget(self._tabs, 1)
        tip = HintLabel("修改索引文件（补全/还原）前请先关闭 MuMu 模拟器，完成后再启动模拟器即可生效。", "warn")
        ct.addWidget(tip)
        self._status_label = StatusLabel("就绪")
        self.status_bar.addWidget(self._status_label, 1)

    # ── 状态栏 ──────────────────────────────────
    def _set_status(self, message):
        self._status_label.setText(message)
        if self._status_cb is not None:
            try:
                self._status_cb(message)
            except Exception:
                pass

    def set_status_callback(self, callback):
        """设置主窗口状态栏回调"""
        self._status_cb = callback

    def set_current_index_file(self, index_file):
        """按当前方案文件自动选中对应索引文件"""
        if not index_file:
            return
        index_file = os.path.basename(index_file)
        self._refresh_index_files()
        idx = self._index_cb.findText(index_file)
        if idx >= 0:
            self._index_cb.setCurrentIndex(idx)
            self._on_index_changed()

    def set_index_directory(self, path):
        """主窗配置变化时重新注入目标目录，旧行立即失效。"""
        path = os.path.abspath(path) if path else ""
        if self._directory_key(path) == self._directory_key(self._backup_mgr.index_directory):
            return
        self._backup_mgr.index_directory = path
        self._missing_items = []
        self._tbl1.setRowCount(0)
        self._tbl2.setRowCount(0)
        self._refresh_all()

    @staticmethod
    def _directory_key(path):
        return os.path.normcase(os.path.abspath(path)) if path else ""

    def _target(self):
        return (self._directory_key(self._backup_mgr.index_directory), self._index_cb.currentText())

    def _refresh_index_files(self):
        previous = self._index_cb.currentText()
        files = self._backup_mgr.get_available_index_files()
        blocked = self._index_cb.combo.blockSignals(True)
        try:
            self._index_cb.clear()
            self._index_cb.addItems(files)
            if previous in files:
                self._index_cb.setCurrentIndex(files.index(previous))
        finally:
            self._index_cb.combo.blockSignals(blocked)
        self._index_cb.setToolTip(self._index_cb.combo.currentText())

    def _on_index_changed(self, *args):
        self._missing_items = []
        self._search_ie.blockSignals(True)
        self._search_ie.clear()
        self._search_ie.blockSignals(False)
        self._refresh_all()

    def _update_action_states(self):
        if not hasattr(self, "_btn_fill"):
            return
        available = bool(self._index_cb.currentText())
        self._btn_backup.setEnabled(available)
        self._btn_scan.setEnabled(available)
        self._btn_restore.setEnabled(available and self._tbl1.currentRow() >= 0)
        self._btn_fill.setEnabled(available and any(
            self._tbl2.item(r, 0).checkState() == Qt.Checked for r in range(self._tbl2.rowCount())
        ))
        checked = sum(self._tbl2.item(r, 0).checkState() == Qt.Checked
                      for r in range(self._tbl2.rowCount()))
        self._missing_count.setText(f"已选 {checked} / 可见 {self._tbl2.rowCount()} / 共 {len(self._missing_items)}")
        self._missing_count.setToolTip(f"扫描目标：{self._index_cb.currentText()}\n目录：{self._backup_mgr.index_directory}")

    # ── 刷新 ────────────────────────────────────
    def _refresh_all(self, report_status=True):
        try:
            self._refresh_index_files()
            self._refresh_backup_table()
            self._refresh_missing_table()
            self._update_action_states()
            if report_status:
                self._set_status("刷新完成")
        except Exception as exc:
            self._set_status(f"刷新失败：{exc}")
            return False
        return True

    def _refresh_backup_table(self):
        index_file = self._index_cb.currentText()
        selected = self._backup_identity(self._tbl1.currentRow())
        self._tbl1.setRowCount(0)
        if not index_file:
            return
        backups = self._backup_mgr.get_backups(index_file)
        self._populate_backup_table(backups)
        if selected:
            for row in range(self._tbl1.rowCount()):
                if self._backup_identity(row) == selected:
                    self._tbl1.selectRow(row)
                    break

    def _populate_backup_table(self, backups):
        self._tbl1.setRowCount(0)
        for b in backups:
            if not isinstance(b, dict) or not isinstance(b.get("timestamp"), str) or not b["timestamp"]:
                continue
            i = self._tbl1.rowCount()
            self._tbl1.insertRow(i)
            # 时间戳格式化
            ts = b.get("timestamp", "")
            display_ts = ts
            try:
                # 2026-07-24_12-00-00-000 → 2026-07-24 12:00
                if len(ts) >= 16:
                    date_part = ts[:10].replace("_", "-")
                    time_part = ts[11:16].replace("-", ":")
                    display_ts = f"{date_part} {time_part}"
            except Exception:
                pass
            self._tbl1.setItem(i, 0, QTableWidgetItem(display_ts))
            self._tbl1.item(i, 0).setData(Qt.UserRole, (self._target(), ts))
            self._tbl1.setItem(i, 1, QTableWidgetItem(b.get("indexFileName", "")))
            scheme_count = b.get("schemeCount", 0)
            self._tbl1.setItem(i, 2, QTableWidgetItem(f"{scheme_count} 方案"))
        add_cell_tooltips(self._tbl1)
        for row in range(self._tbl1.rowCount()):
            item = self._tbl1.item(row, 0)
            item.setToolTip(item.data(Qt.UserRole)[1])
        self._backup_empty.sync()

    def _refresh_missing_table(self):
        index_file = self._index_cb.currentText()
        if not index_file:
            self._tbl2.setRowCount(0)
            self._missing_items = []
            return
        try:
            orphans = self._backup_mgr.scan_orphan_schemes(index_file)
        except Exception:
            self._missing_items = []
            self._tbl2.setRowCount(0)
            self._update_action_states()
            self._missing_empty.sync("扫描失败\n详情见底部状态；请重新扫描。")
            raise
        error = getattr(self._backup_mgr, "last_error", "")
        if error:
            self._missing_items = []
            self._tbl2.setRowCount(0)
            self._update_action_states()
            self._missing_empty.sync("扫描失败\n详情见底部状态；请重新扫描。")
            raise RuntimeError(error)
        self._missing_items = [dict(item) for item in orphans if isinstance(item, dict)
                               and item.get("SchemeName") and item.get("FilePath")]
        self._populate_missing_table(self._missing_items)

    def _populate_missing_table(self, missing):
        selected = {self._tbl2.item(r, 1).data(Qt.UserRole)[1]["FilePath"]
                    for r in range(self._tbl2.rowCount())
                    if self._tbl2.item(r, 0).checkState() == Qt.Checked
                    and self._tbl2.item(r, 1).data(Qt.UserRole)
                    and self._tbl2.item(r, 1).data(Qt.UserRole)[0] == self._target()}
        self._updating_check_state = True
        self._tbl2.setRowCount(0)
        keywords = [k.strip().lower() for k in self._search_ie.text().replace(",", " ").split() if k.strip()]
        for item_data in missing:
            scheme_name = item_data.get("SchemeName", "")
            # 多关键词搜索（空格/逗号拆分，任一命中，对应 C# FilterOrphanList）
            if keywords and not any(k in scheme_name.lower() for k in keywords):
                continue
            row = self._tbl2.rowCount()
            self._tbl2.insertRow(row)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if item_data["FilePath"] in selected else Qt.Unchecked)
            self._tbl2.setItem(row, 0, chk)
            self._tbl2.setItem(row, 1, QTableWidgetItem(scheme_name))
            self._tbl2.item(row, 1).setData(Qt.UserRole, (self._target(), dict(item_data)))
            status_item = QTableWidgetItem("缺失")
            status_item.setForeground(Qt.red)
            self._tbl2.setItem(row, 2, status_item)
        self._updating_check_state = False
        self._sync_check_state()
        add_cell_tooltips(self._tbl2)
        message = ("没有匹配的缺失文件\n请调整关键词或清空搜索。" if self._search_ie.text().strip()
                   else "暂无缺失文件\n当前扫描未发现需要补全的文件。")
        self._missing_empty.sync(message)

    def _apply_search_filter(self):
        index_file = self._index_cb.currentText()
        if not index_file:
            self._tbl2.setRowCount(0)
            return
        self._populate_missing_table(self._missing_items)

    # ── 按钮事件 ────────────────────────────────
    def _on_refresh(self):
        self._refresh_all()

    def _on_backup_double_clicked(self, row, col):
        """双击备份行显示方案详情（对应 C# GetSchemesInBackup 弹窗）"""
        identity = self._validated_backup(row)
        if identity is None:
            return
        target, ts = identity
        try:
            schemes = self._backup_mgr.get_schemes_in_backup(target[1], ts)
        except Exception as exc:
            self._set_status(f"读取备份失败：{exc}")
            return
        if not schemes:
            QMessageBox.information(self, "方案详情", "该备份中无方案数据。")
            return
        QMessageBox.information(self, "方案详情", "备份中的方案：\n" + "\n".join(schemes))

    def _on_manual_backup(self):
        index_file = self._index_cb.currentText()
        if not index_file:
            self._set_status("请先选择索引文件")
            return
        try:
            ok = self._backup_mgr.backup_index_direct(index_file)
            message = f"已备份：{index_file}" if ok else self._backend_result("备份失败或内容无变化")
            if ok:
                self._refresh_all(False)
            self._set_status(message)
        except Exception as exc:
            self._set_status(f"备份失败：{exc}")

    def _backend_result(self, fallback):
        error = getattr(self._backup_mgr, "last_error", "")
        return f"操作失败：{error}" if error else fallback

    def _backup_identity(self, row):
        item = self._tbl1.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item else None

    def _validated_backup(self, row, identity=None):
        identity = identity or self._backup_identity(row)
        if not identity or identity[0] != self._target():
            self._set_status("备份目标已变化，请刷新后重新选择")
            return None
        try:
            available = self._backup_mgr.get_backups(identity[0][1])
            if any(isinstance(b, dict) and b.get("timestamp") == identity[1] for b in available):
                return identity
        except Exception as exc:
            self._set_status(f"读取备份失败：{exc}")
            return None
        self._refresh_all(False)
        self._set_status("所选备份已失效，请重新选择")
        return None

    def _on_restore_selected(self):
        row = self._tbl1.currentRow()
        if row < 0:
            self._set_status("请先选择要还原的备份")
            return
        identity = self._validated_backup(row)
        if identity is None:
            return
        target, ts = identity
        if QMessageBox.question(self, "确认还原", f"还原索引 {target[1]} 到 {ts}？\n请先关闭 MuMu，完成后重新启动。",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if self._validated_backup(row, identity) is None:
            return
        try:
            ok = self._backup_mgr.restore_backup(target[1], ts)
            message = (f"已还原：{target[1]} ← {ts}；请重启 MuMu" if ok else self._backend_result("还原失败"))
            if ok:
                self._refresh_all(False)
            self._set_status(message)
        except Exception as exc:
            self._set_status(f"还原失败：{exc}")

    def _on_clean_old_backups(self):
        if QMessageBox.question(self, "确认清理", "清理所有索引的旧备份，每个保留最新10个，确定继续？",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            if hasattr(self._backup_mgr, "last_error"):
                self._backup_mgr.last_error = ""
            result = self._backup_mgr.cleanup_old_backups(10)
            error = getattr(self._backup_mgr, "last_error", "")
            if result is False or error:
                self._set_status(self._backend_result("清理失败"))
                return
            self._refresh_backup_table()
            self._update_action_states()
            self._set_status("已清理旧备份（保留最新 10 个）")
        except Exception as exc:
            self._set_status(f"清理失败：{exc}")

    def _on_scan_missing(self):
        try:
            self._refresh_missing_table()
            self._set_status(f"扫描完成：发现 {self._tbl2.rowCount()} 个缺失文件")
        except Exception as exc:
            self._set_status(f"扫描失败：{exc}")
        self._update_action_states()

    def _on_fill_missing(self):
        index_file = self._index_cb.currentText()
        if not index_file:
            self._set_status("请先选择索引文件")
            return
        schemes = []
        for row in range(self._tbl2.rowCount()):
            chk_item = self._tbl2.item(row, 0)
            if chk_item and chk_item.checkState() == Qt.Checked:
                name_item = self._tbl2.item(row, 1)
                if name_item:
                    identity = name_item.data(Qt.UserRole)
                    if not identity or identity[0] != self._target() or not os.path.isfile(identity[1]["FilePath"]):
                        self._set_status("扫描结果已失效，请重新扫描")
                        return
                    schemes.append(dict(identity[1]))
        if not schemes:
            self._set_status("未选中任何缺失文件")
            return
        target = self._target()
        if QMessageBox.question(self, "确认补全", f"向 {index_file} 补全 {len(schemes)} 个方案：\n" +
                                "\n".join(s["SchemeName"] for s in schemes) + "\n完成后请重启 MuMu。",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if target != self._target() or any(not os.path.isfile(s["FilePath"]) for s in schemes):
            self._set_status("扫描目标已变化，请重新扫描")
            return
        try:
            current = self._backup_mgr.scan_orphan_schemes(index_file)
            if getattr(self._backup_mgr, "last_error", ""):
                self._set_status(self._backend_result("扫描失败"))
                return
            valid = {(s.get("SchemeName"), self._directory_key(s.get("FilePath")))
                     for s in current if isinstance(s, dict)}
            if any((s["SchemeName"], self._directory_key(s["FilePath"])) not in valid for s in schemes):
                self._set_status("扫描结果已变化，请重新扫描")
                return
            added = self._backup_mgr.add_schemes_to_index(index_file, schemes)
            if added is False or added is None or added <= 0:
                self._set_status(self._backend_result("补全失败或没有新增索引项"))
                return
            self._refresh_all(False)
            self._set_status(f"已补全 {added} 个索引项；请重启 MuMu")
        except Exception as exc:
            self._set_status(f"补全失败：{exc}")

    def _on_select_all_changed(self, state):
        self._updating_check_state = True
        check_state = Qt.Checked if state == Qt.Checked else Qt.Unchecked
        for row in range(self._tbl2.rowCount()):
            chk_item = self._tbl2.item(row, 0)
            if chk_item:
                chk_item.setCheckState(check_state)
        self._updating_check_state = False
        self._update_action_states()

    def _on_item_check_changed(self, item):
        """单项勾选变化 → 回写全选框状态（对应 C# UpdateSelectAllState）"""
        if self._updating_check_state or item.column() != 0:
            return
        if self._tbl2.rowCount() == 0:
            return
        self._sync_check_state()

    def _sync_check_state(self):
        all_checked = self._tbl2.rowCount() > 0 and all(
            self._tbl2.item(r, 0).checkState() == Qt.Checked
            for r in range(self._tbl2.rowCount())
        )
        self._updating_check_state = True
        self._select_all_cb.setCheckState(Qt.Checked if all_checked else Qt.Unchecked)
        self._updating_check_state = False
        self._update_action_states()
