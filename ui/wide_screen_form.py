"""
超宽屏坐标转换
"""
import os
import json
import copy
import math
import re
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QAbstractItemView, QMessageBox, QGridLayout, QSizePolicy,
    QStyle, QStyleOptionComboBox, QStylePainter,
)
from PyQt5.QtCore import Qt, QTimer, QObject, QEvent, QSize
from PyQt5.QtGui import QFont, QPainter
from .base_dialog import BaseDialog
from .key_overwrite import request_overwrite
from controls import RoundedButton, SearchableComboBox, rebuild_combo, HintLabel, CardPanel
from core.package_names import PACKAGE_ITEMS, filter_keymap_files
from core.json_editor import (
    calculate_coordinates_k_to_ck, read_key_from_file, get_click_keys,
)
from core.resolution_manager import (
    ResolutionManager,
    TYPE_TABLET, TYPE_PHONE, TYPE_ULTRA_WIDE, TYPE_CUSTOM,
)
from core.config_manager import ConfigManager
from core.coord_model import rel_to_px, px_to_rel, landing_hint
from utils.safe_parse import try_parse_int, try_parse_float
import theme

# 包名表已收口到 core/package_names.py（过去本项目有 4 处副本）。


class WideScreenForm(BaseDialog):
    def set_overwrite_guard(self, callback, epoch_getter=None):
        self._overwrite_guard = callback
        self._overwrite_epoch_getter = epoch_getter

    def __init__(self, parent=None):
        super().__init__(parent, "超宽屏坐标转换")
        self.setFixedSize(710, 390)
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self._json_str = ""        # 主窗口当前 JSON（目标，追加写入）
        self._json_path = ""       # 主窗口 JSON 路径
        self._write_cb = None      # callback(json_str) -> bool
        self._res_getter = None    # callback() -> (fx, fy)
        self._src_json_str = ""    # 源键位文件内容
        self._click_keys = []      # 源文件 Click 键列表（像素坐标）
        self._selected_row = -1
        self._source_path = ""
        self._source_resolution = (0, 0)
        self._config = ConfigManager.shared()
        self._build()
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tbl.currentCellChanged.connect(lambda row, col, old_row, old_col: self._on_table_click(row, col))
        self._tbl.itemChanged.connect(self._on_check_changed)
        self._file_cb.currentIndexChanged.connect(self._invalidate_source)
        self._res_cb.currentIndexChanged.connect(self._invalidate_source)
        if hasattr(self._file_cb, "popupAboutToShow"):
            self._file_cb.popupAboutToShow.connect(self._refresh_file_list)
        self._init_package_cb()
        self._on_ratio_changed(0)
        self._refresh_file_list()

    def _build(self):
        self.status_bar.setSizeGripEnabled(False)
        ct = self.content_layout
        ct.setContentsMargins(8, 6, 8, 6)
        ct.setSpacing(5)
        columns = QHBoxLayout()
        columns.setSpacing(6)
        source = CardPanel("来源")
        layout = source.content_layout
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        grid.addWidget(QLabel("包名"), 0, 0)
        self._pkg_cb = QComboBox()
        self._pkg_cb.currentTextChanged.connect(self._pkg_cb.setToolTip)
        self._pkg_cb.currentIndexChanged.connect(self._on_package_changed)
        grid.addWidget(self._pkg_cb, 0, 1, 1, 3)
        grid.addWidget(QLabel("文件"), 1, 0)
        self._file_cb = SearchableComboBox()
        self._file_cb.combo.setMinimumWidth(0)
        self._file_cb.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._file_cb.combo.setMinimumContentsLength(6)
        self._file_elide_paint = _ElidedComboPaint(self._file_cb.combo)
        self._file_cb.currentTextChanged.connect(self._file_cb.setToolTip)
        grid.addWidget(self._file_cb, 1, 1, 1, 3)
        grid.addWidget(QLabel("分辨率"), 2, 0)
        self._ratio_cb = QComboBox()
        for name, code in (("平板", TYPE_TABLET), ("手机", TYPE_PHONE),
                           ("超宽屏", TYPE_ULTRA_WIDE), ("自定义", TYPE_CUSTOM)):
            self._ratio_cb.addItem(name, code)
        self._ratio_cb.currentIndexChanged.connect(self._on_ratio_changed)
        grid.addWidget(self._ratio_cb, 2, 1)
        self._res_cb = QComboBox()
        self._res_cb.setMinimumWidth(100)
        self._res_cb.currentTextChanged.connect(self._res_cb.setToolTip)
        grid.addWidget(self._res_cb, 2, 2)
        self._read_btn = RoundedButton("读取", "secondary")
        self._read_btn.clicked.connect(self._on_read)
        grid.addWidget(self._read_btn, 2, 3)
        layout.addLayout(grid)
        self._tbl = QTableWidget(0, 3)
        self._tbl.setHorizontalHeaderLabels(["☐", "按键", "来源像素坐标"])
        self._hdr_chk = self._tbl.horizontalHeaderItem(0)
        font = QFont()
        font.setPointSize(14)
        self._hdr_chk.setFont(font)
        header = self._tbl.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self._tbl.setColumnWidth(0, 32)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        self._tbl.setColumnWidth(1, 78)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self._tbl.verticalHeader().hide()
        self._tbl.verticalHeader().setDefaultSectionSize(26)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setStyleSheet(theme.TABLE_STYLE)
        header.sectionClicked.connect(self._toggle_all)
        self._tbl.cellClicked.connect(self._on_table_click)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.itemChanged.connect(self._table_item_changed)
        layout.addWidget(self._tbl, 1)
        self._empty_hint = _ElidedLabel("选择来源文件和分辨率后点击“读取”")
        layout.addWidget(self._empty_hint)
        columns.addWidget(source, 5)
        arrow = QLabel("→")
        arrow.setStyleSheet(f"font-size: 20pt; color: {theme.VIOLET};")
        columns.addWidget(arrow)
        target = CardPanel("目标")
        layout = target.content_layout
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        self._target_res_label = _ElidedLabel("等待主窗口提供分辨率")
        self._target_res_label.setStyleSheet(f"color: {theme.VIOLET_700}; font-weight: bold;")
        layout.addWidget(self._target_res_label)
        self._target_file_label = _ElidedLabel("目标：主窗口当前 JSON")
        layout.addWidget(self._target_file_label)
        self._selected_key_label = _ElidedLabel("尚未选择按键")
        layout.addWidget(self._selected_key_label)
        self._sel_x, self._sel_y = _readout(), _readout()
        self._wide_x, self._wide_y = _readout(), _readout()
        self._relative_label = _ElidedLabel("目标相对坐标：—")
        pair = QGridLayout()
        pair.setHorizontalSpacing(4)
        pair.setVerticalSpacing(4)
        pair.addWidget(QLabel("X"), 0, 1)
        pair.addWidget(QLabel("Y"), 0, 2)
        for row, (title, x, y) in enumerate((("来源像素", self._sel_x, self._sel_y), ("目标像素", self._wide_x, self._wide_y)), 1):
            pair.addWidget(QLabel(title), row, 0)
            pair.addWidget(x, row, 1)
            pair.addWidget(y, row, 2)
        layout.addLayout(pair)
        layout.addWidget(self._relative_label)
        self._landing_hint = _ElidedLabel("实际落点：—")
        self._landing_hint.setStyleSheet(f"color: {theme.INK_2}; font-size: 11px;")
        layout.addWidget(self._landing_hint)
        layout.addStretch()
        self._single_btn = RoundedButton("单点写入", "secondary")
        self._single_btn.clicked.connect(self._on_single_write)
        self._batch_btn = RoundedButton("批量写入", "primary")
        self._batch_btn.clicked.connect(self._on_batch_write)
        buttons = QHBoxLayout()
        buttons.addWidget(self._single_btn)
        buttons.addWidget(self._batch_btn)
        layout.addLayout(buttons)
        columns.addWidget(target, 4)
        ct.addLayout(columns, 1)
        ct.addWidget(HintLabel("预览为像素坐标；请核对来源尺寸与目标分辨率后写入。", "warn"))
        self._res_cb.currentIndexChanged.connect(self._refresh_preview_ui)
        # 仅轮询主窗分辨率回调，不查询设备或鼠标；隐藏时停止。
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(250)
        self._preview_timer.timeout.connect(self._refresh_preview_ui)

    # ========================================================================
    # 公开接口
    # ========================================================================

    def set_json_data(self, json_str):
        self._json_str = json_str or ""

    def set_json_path(self, json_path):
        self._json_path = json_path or ""
        self._refresh_file_list()
        self._target_file_label.setText("目标：" + (self._json_path or "主窗口当前 JSON"))

    def set_write_callback(self, callback):
        """设置写回回调（callback(json_str) -> bool），由主窗口写文件 + 备份"""
        self._write_cb = callback

    def set_resolution_getter(self, callback):
        """设置目标分辨率获取回调（callback() -> (fx, fy)）"""
        self._res_getter = callback
        self._refresh_preview_ui()

    # ========================================================================
    # 初始化 / 填充
    # ========================================================================

    @property
    def _data_dir(self):
        # 与 config 同源解析（源码版 / exe 版 / MOMO_DATA_DIR 一致）
        from core.config_manager import resolve_app_dir
        return os.path.join(resolve_app_dir(), "data")

    def _init_package_cb(self):
        rebuild_combo(self._pkg_cb, PACKAGE_ITEMS)

    def _refresh_file_list(self):
        previous = self._file_cb.currentData()
        directories = [self._config.json_folder_path, self._data_dir]
        if self._json_path:
            directories.insert(0, os.path.dirname(self._json_path))
        pkg = self._pkg_cb.currentData() or ""
        items, seen, errors, available = [], set(), [], False
        for folder in directories:
            folder = os.path.abspath(folder) if folder else ""
            if not folder or os.path.normcase(folder) in seen:
                continue
            seen.add(os.path.normcase(folder))
            try:
                files = sorted(os.listdir(folder))
                available = True
            except OSError as ex:
                errors.append(str(ex))
                continue
            for display, filename in filter_keymap_files(files, pkg, only_json=True):
                path = os.path.join(folder, filename)
                if os.path.isfile(path):
                    label = f"{display} [data]" if os.path.normcase(folder) == os.path.normcase(os.path.abspath(self._data_dir)) else display
                    items.append((label, path))
        rebuild_combo(self._file_cb, items)
        self._file_cb.blockSignals(True)
        try:
            if previous:
                self._file_cb.setCurrentIndex(self._file_cb.combo.findData(previous))
        finally:
            self._file_cb.blockSignals(False)
        for index in range(self._file_cb.count()):
            self._file_cb.combo.setItemData(index, self._file_cb.itemData(index), Qt.ToolTipRole)
        if self._source_path and self._get_src_path() != self._source_path:
            self._invalidate_source()
        if not available:
            self.status_bar.showMessage("来源目录不存在或无法读取：" + "; ".join(errors), 5000)
        elif not items:
            self.status_bar.showMessage("当前包名下没有来源文件", 3000)

    def _on_package_changed(self, index):
        self._refresh_file_list()

    def _on_ratio_changed(self, index):
        type_code = self._ratio_cb.currentData()
        if not type_code:
            return
        custom_str = self._config.resolution4_string
        res_dict = ResolutionManager.get_resolution_dict_by_type(type_code, custom_str)
        # 重建后**在屏蔽区间之外**设置默认选中项（刻意触发一次联动，保持原行为）
        rebuild_combo(self._res_cb, [(name.replace("x", "×"), value)
                                     for name, value in res_dict.items()])
        defaults = {TYPE_TABLET: "1920,1080", TYPE_PHONE: "1080,1920", TYPE_ULTRA_WIDE: "3200,1440"}
        preferred = self._res_cb.findData(defaults.get(type_code))
        if self._res_cb.count() > 0:
            self._res_cb.setCurrentIndex(max(0, preferred))
        self._invalidate_source()

    # ========================================================================
    # 工具
    # ========================================================================

    def _get_src_path(self):
        filename = self._file_cb.currentData()
        if not filename:
            return ""
        return os.path.abspath(filename) if os.path.isabs(filename) else os.path.join(self._data_dir, filename)

    def _get_src_resolution(self):
        value = self._res_cb.currentData()
        if not value:
            return (0, 0)
        parts = str(value).split(",")
        if len(parts) < 2:
            return (0, 0)
        ok_w, w = try_parse_int(parts[0])
        ok_h, h = try_parse_int(parts[1])
        return (w, h) if (ok_w and ok_h) else (0, 0)

    def _get_target_resolution(self):
        if self._res_getter is not None:
            try:
                fx, fy = self._res_getter()
                values = (float(fx), float(fy))
                if all(math.isfinite(v) and v > 1 and v.is_integer() for v in values):
                    return tuple(int(v) for v in values)
            except Exception:
                pass
        return (0, 0)

    # ========================================================================
    # 读取源文件
    # ========================================================================

    def _invalidate_source(self, *args):
        self._source_path = ""
        self._src_json_str = ""
        self._click_keys = []
        self._selected_row = -1
        self._tbl.setRowCount(0)
        self._reset_preview()
        self._on_check_changed()

    def _reset_preview(self):
        for edit in (self._sel_x, self._sel_y, self._wide_x, self._wide_y):
            edit.setText("0")
        self._refresh_preview_ui(recalculate=False)

    def _on_read(self):
        src_path = self._get_src_path()
        src_w, src_h = self._get_src_resolution()
        self._invalidate_source()
        if not src_path or not os.path.isfile(src_path):
            self.status_bar.showMessage("请先选择有效的键位文件", 3000)
            return
        if src_w <= 1 or src_h <= 1:
            self.status_bar.showMessage("来源分辨率须大于1", 3000)
            return
        try:
            with open(src_path, "r", encoding="utf-8-sig") as stream:
                text = stream.read()
            obj = json.loads(text)
            if not isinstance(obj, dict) or not isinstance(obj.get("keymaps", []), list):
                raise ValueError("keymaps必须为数组")
            rows = []
            for index, item in enumerate(obj.get("keymaps", [])):
                if not isinstance(item, dict):
                    continue
                kind = item.get("type")
                if kind not in ("Click", "Macro"):
                    continue
                if kind == "Macro":
                    actions = item.get("press_actions", [])
                    action = next((a for a in actions if isinstance(a, str) and a.startswith("curve_rel:mouse;(")), None) if isinstance(actions, list) else None
                    match = re.fullmatch(r"curve_rel:mouse;\(([^,]+),([^,]+)\)", action or "")
                    if not match:
                        continue
                    rx, ry = map(float, match.groups())
                else:
                    pos = item.get("rel_work_position")
                    if not isinstance(pos, dict):
                        continue
                    rx, ry = float(pos["rel_x"]), float(pos["rel_y"])
                if not all(math.isfinite(v) for v in (rx, ry)):
                    raise ValueError("来源键位坐标不是有限数")
                key = item.get("key")
                name = str(key.get("text", "")) if isinstance(key, dict) else ""
                rows.append({"key_text": name, "rel_x": rel_to_px(rx, src_w),
                             "rel_y": rel_to_px(ry, src_h), "index": index, "key_obj": copy.deepcopy(item)})
        except Exception as ex:
            self.status_bar.showMessage(f"读取失败: {ex}", 5000)
            return
        self._src_json_str, self._source_path = text, src_path
        self._source_resolution = (src_w, src_h)
        self._click_keys = rows
        self._tbl.blockSignals(True)
        try:
            self._tbl.setRowCount(len(rows))
            for row, info in enumerate(rows):
                chk = QTableWidgetItem()
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                chk.setCheckState(Qt.Unchecked)
                chk.setData(Qt.UserRole, info["index"])
                self._tbl.setItem(row, 0, chk)
                for col, value in ((1, info["key_text"]), (2, f"{info['rel_x']:.16g},{info['rel_y']:.16g}")):
                    item = QTableWidgetItem(value)
                    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                    item.setToolTip(value)
                    self._tbl.setItem(row, col, item)
        finally:
            self._tbl.blockSignals(False)
        self._selected_row = -1
        self._on_check_changed()
        self.status_bar.showMessage(f"已加载 {len(rows)} 个按键", 3000)

    def _on_check_changed(self, *args):
        checked = sum(self._tbl.item(r, 0) is not None and self._tbl.item(r, 0).checkState() == Qt.Checked for r in range(self._tbl.rowCount()))
        total = self._tbl.rowCount()
        self._hdr_chk.setText("☑" if total and checked == total else ("◩" if checked else "☐"))
        self._refresh_preview_ui()

    def _toggle_all(self, section):
        if section != 0:
            return
        all_checked = self._tbl.rowCount() > 0 and all(self._tbl.item(r, 0).checkState() == Qt.Checked for r in range(self._tbl.rowCount()))
        self._tbl.blockSignals(True)
        try:
            for row in range(self._tbl.rowCount()):
                self._tbl.item(row, 0).setCheckState(Qt.Unchecked if all_checked else Qt.Checked)
        finally:
            self._tbl.blockSignals(False)
        self._tbl.clearSelection()
        self._tbl.setCurrentCell(-1, -1)
        self._selected_row = -1
        self._reset_preview()
        self._on_check_changed()

    def _row_info(self, row):
        if not 0 <= row < len(self._click_keys):
            raise ValueError("请先在表格中选择一个按键")
        info = self._click_keys[row]
        item = self._tbl.item(row, 0)
        if item is None or item.data(Qt.UserRole) != info["index"]:
            raise ValueError("来源行身份已变化，请重新读取")
        return info

    def _validate_source(self):
        if not self._source_path or self._get_src_path() != self._source_path or self._get_src_resolution() != self._source_resolution:
            raise ValueError("来源选择或分辨率已变化，请重新读取")
        with open(self._source_path, "r", encoding="utf-8-sig") as stream:
            if stream.read() != self._src_json_str:
                raise ValueError("来源文件已变化，请重新读取")

    def _on_table_click(self, row, col):
        self._selected_row = row
        try:
            info = self._row_info(row)
        except ValueError:
            self._reset_preview()
            return
        self._sel_x.setText(f"{info['rel_x']:.16g}")
        self._sel_y.setText(f"{info['rel_y']:.16g}")
        self._calc_wide_preview()

    def _calc_wide_preview(self):
        try:
            info = self._row_info(self._selected_row)
            self._validate_source()
            fx, fy = self._get_target_resolution()
            if fx <= 1 or fy <= 1:
                raise ValueError("目标分辨率须大于1")
            x, y, status = calculate_coordinates_k_to_ck(*self._source_resolution, fx, fy, info["rel_x"], info["rel_y"])
            self._wide_x.setText(f"{x:.16g}")
            self._wide_y.setText(f"{y:.16g}")
            if status < 0:
                self.status_bar.showMessage("部分坐标超出目标边界，预览已安全裁剪", 5000)
        except Exception as ex:
            self._wide_x.setText("0")
            self._wide_y.setText("0")
            self.status_bar.showMessage(f"无法预览: {ex}", 5000)
            self._refresh_preview_ui(recalculate=False)
        else:
            self._refresh_preview_ui(recalculate=False, preview=(x, y, fx, fy))

    def _convert_info(self, info, target):
        fx, fy = target
        if fx <= 1 or fy <= 1:
            raise ValueError("目标分辨率须大于1")
        px, py, status = calculate_coordinates_k_to_ck(*self._source_resolution, fx, fy, info["rel_x"], info["rel_y"])
        rx, ry = px_to_rel(px, fx), px_to_rel(py, fy)
        obj = copy.deepcopy(info["key_obj"])
        if obj.get("type") == "Macro":
            actions = obj.get("press_actions", [])
            replaced = False
            for index, action in enumerate(actions):
                if not isinstance(action, str) or not action.startswith("curve_rel:mouse;"):
                    continue
                match = re.fullmatch(r"curve_rel:mouse;\(([^,]+),([^,]+)\)", action)
                if not match:
                    raise ValueError("复杂曲线宏暂不支持宽屏移植，已保留原文件")
                ax, ay = map(float, match.groups())
                if not all(math.isfinite(value) for value in (ax, ay)):
                    raise ValueError("宏动作坐标不是有限数")
                sw, sh = self._source_resolution
                cx, cy, action_status = calculate_coordinates_k_to_ck(sw, sh, fx, fy, rel_to_px(ax, sw), rel_to_px(ay, sh))
                actions[index] = f"curve_rel:mouse;({px_to_rel(cx, fx):.16g},{px_to_rel(cy, fy):.16g})"
                status = min(status, action_status)
                replaced = True
            if not replaced:
                raise ValueError("宏无可转换的curve_rel:mouse坐标")
        elif obj.get("type") in ("Click", "BunchClick"):
            for pos in (obj.get("icon", {}).get("rel_position"), obj.get("rel_work_position")):
                if isinstance(pos, dict):
                    pos.update(rel_x=rx, rel_y=ry)
        else:
            raise ValueError("此按键类型不支持坐标移植")
        return json.dumps(obj, ensure_ascii=False, indent=4, allow_nan=False), status

    def _convert_key(self, key_text, mx, my):
        """兼容旧调用；业务数据只取已读取快照，不信任展示文本或六位小数。"""
        try:
            self._validate_source()
            matches = [info for info in self._click_keys if info["key_text"] == key_text]
            if len(matches) != 1:
                return None
            return self._convert_info(matches[0], self._get_target_resolution())[0]
        except Exception:
            return None

    def _write_rows(self, rows):
        from core.json_editor import write_keys
        original = self._json_str
        if not original:
            self.status_bar.showMessage("请先在主窗口加载 JSON 文件", 3000)
            return
        try:
            if not rows:
                raise ValueError("没有勾选或选中的按键")
            self._validate_source()
            target = self._get_target_resolution()
            modified, clipped, names, blocks = original, 0, [], []
            for row in rows:
                info = self._row_info(row)
                block, status = self._convert_info(info, target)
                blocks.append(json.loads(block))
                result = write_keys(block, modified)
                if result is None:
                    raise ValueError(f"转换按键[{info['key_text']}]失败")
                modified = result
                clipped += status < 0
                names.append(info["key_text"])
            if clipped:
                reply = QMessageBox.question(self, "超出边界", f"{clipped}个按键已裁剪至目标边缘，坐标可能不准确。是否继续？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if reply != QMessageBox.Yes:
                    return
            if not request_overwrite(self, original, blocks):
                return
            self._validate_source()
            if self._get_target_resolution() != target or self._json_str != original:
                raise ValueError("确认期间目标数据或尺寸已变化，请重新核对")
            if self._write_cb is None:
                raise ValueError("未连接主窗口保存接口")
            if self._write_cb(modified) is False:
                raise ValueError("写回文件失败")
        except Exception as ex:
            self._json_str = original
            self.status_bar.showMessage(f"移植失败: {ex}", 5000)
            return
        self._json_str = modified
        self.status_bar.showMessage(f"键位移植成功: {len(names)} 个按键 [{ '、'.join(names) }]", 5000)

    def _on_single_write(self):
        row = self._tbl.currentRow()
        self._write_rows([row] if row >= 0 else [])

    def _on_batch_write(self):
        rows = [row for row in range(self._tbl.rowCount()) if self._tbl.item(row, 0) is not None and self._tbl.item(row, 0).checkState() == Qt.Checked]
        self._write_rows(rows)


    def _table_item_changed(self, item):
        if item.toolTip() != item.text():
            self._tbl.blockSignals(True)
            item.setToolTip(item.text())
            self._tbl.blockSignals(False)

    def _refresh_preview_ui(self, *args, recalculate=True, preview=None):
        fx, fy = self._get_target_resolution()
        self._target_res_label.setText(f"主窗口分辨率  {fx} × {fy}" if fx > 1 and fy > 1 else "等待主窗口提供分辨率")
        self._file_cb.setToolTip(self._get_src_path() or self._file_cb.currentText())
        self._file_cb.combo.setToolTip(self._file_cb.toolTip())
        rows = self._tbl.rowCount()
        checked = sum(self._tbl.item(row, 0) is not None and self._tbl.item(row, 0).checkState() == Qt.Checked for row in range(rows))
        self._empty_hint.setText(f"已加载 {rows} 个按键 · 已勾选 {checked} 个" if rows else "暂无按键 · 选择来源文件和分辨率后读取")
        valid = 0 <= self._selected_row < len(self._click_keys)
        info = self._click_keys[self._selected_row] if valid else None
        self._selected_key_label.setText("当前按键：" + info["key_text"] if info else "尚未选择按键")
        if valid and recalculate:
            self._calc_wide_preview()
            return
        if preview is not None:
            x, y, width, height = preview
            self._relative_label.setText(f"目标相对坐标：{px_to_rel(x, width):.16g}, {px_to_rel(y, height):.16g}")
            ideal_x, ideal_y, _ = calculate_coordinates_k_to_ck(
                *self._source_resolution, width, height, info["rel_x"], info["rel_y"], snap=False)
            self._landing_hint.setText(landing_hint(ideal_x, ideal_y, width, height))
        else:
            self._relative_label.setText("目标相对坐标：—")
            self._landing_hint.setText("实际落点：—")
            if not valid:
                self._wide_x.setText("—")
                self._wide_y.setText("—")

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_preview_ui()
        self._preview_timer.start()

    def hideEvent(self, event):
        self._preview_timer.stop()
        super().hideEvent(event)


def _in_ro(default, width=36):
    inp = QLineEdit(default); inp.setFixedWidth(width); inp.setReadOnly(True)
    return inp


def _readout():
    edit = QLineEdit("0")
    edit.setReadOnly(True)
    edit.setMinimumWidth(80)
    edit.textChanged.connect(edit.setToolTip)
    return edit


class _ElidedComboPaint(QObject):
    """仅替换闭合下拉框的文字绘制；保留完整模型、搜索与所有选择信号。"""

    def __init__(self, combo):
        super().__init__(combo)
        combo.installEventFilter(self)

    def eventFilter(self, combo, event):
        if event.type() != QEvent.Paint or combo.isEditable():
            return False
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        field = combo.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, combo)
        option.currentText = combo.fontMetrics().elidedText(option.currentText, Qt.ElideMiddle, max(0, field.width() - 8))
        painter = QStylePainter(combo)
        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        painter.drawControl(QStyle.CE_ComboBoxLabel, option)
        return True


class _ElidedLabel(QLabel):
    """保留完整 text/tooltip，以省略号绘制单行长文本，不挤占邻近控件。"""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(text)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)
        self.setAccessibleDescription(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideMiddle, self.contentsRect().width())
        painter.drawText(self.contentsRect(), int(self.alignment() | Qt.AlignVCenter), text)

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

