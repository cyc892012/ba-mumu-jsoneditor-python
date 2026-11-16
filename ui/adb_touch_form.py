"""
ADB 触控采集 — UI 沿用新版设计，后端逻辑完整采用旧版实现
"""
import json
import math
import os
import queue
import re
import subprocess
import threading
from PyQt5.QtWidgets import (
    QHBoxLayout, QLabel, QComboBox,
    QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QLineEdit,
    QFileDialog, QApplication, QAbstractItemView, QPushButton, QMenu,
)
from .base_dialog import BaseDialog
from .message_dialog import MessageDialog
from .key_overwrite import request_overwrite, overwrite_epoch
from .status_text import DETAIL_EXACT_MATCH, key_created, key_exists, listening
from controls import RoundedButton, KeyCaptureEdit, KEY_TYPE_ITEMS, set_checked_silently, HintLabel
from controls.segmented import GenerationModeControl
from controls.status_pill import StatusPill
from .touch_presentation import COLUMNS, TouchTable, DetailLabel, ElidedStatusLabel, type_segments, comfortable_controls
from PyQt5.QtCore import Qt, QTimer, QObject, pyqtSignal, QEvent
from PyQt5.QtGui import QFont
import theme
from adb.client import AdbClient
from adb.parser import TouchEventParser, TouchCoordinate
from core.json_editor import (
    create_key, write_keys, find_all_repeat_key_values, get_scan_code,
    TYPE_CLICK, try_read_key_coordinates,
)
from core.coord_model import adb_px_to_rel
from infra.log_service import LogService
from utils.qt_thread_bridge import ThreadBridge
from typing import Optional, Callable, Dict, List


# ── MuMu 端口数据类 ──

class MuMuPortItem:
    __slots__ = ('port', 'instance_name')
    def __init__(self, port="", instance_name=""):
        self.port = port
        self.instance_name = instance_name
    @property
    def display_text(self):
        return f"{self.instance_name} ({self.port})" if self.instance_name else self.port
    def __str__(self):
        return self.display_text

# ── 触摸坐标行数据 ──

class TouchCoordRowData:
    __slots__ = ('seq', 'captured_key', 'vk_code', 'screen_x', 'screen_y', 'rel_x', 'rel_y',
                 'hit_count', 'key_type', 'checked')
    def __init__(self):
        self.seq = 0
        self.captured_key = None
        self.vk_code = None  # 虚拟键码（用于查重）
        self.screen_x = 0.0
        self.screen_y = 0.0
        self.rel_x = 0.0
        self.rel_y = 0.0
        self.hit_count = 1
        self.key_type = TYPE_CLICK
        self.checked = False


# 按键捕获框已统一到 controls/key_capture_edit.py（本项目曾有 4 份重复实现）。
# 本面板的差异：Esc 清空（回调带 clear=True）、同键互斥由 _on_key_capture_for_row 处理。

# ── 预定义按键类型 ──
# 已收口到 controls/combo_box_utils.py::KEY_TYPE_ITEMS
# （C# 里它就在 ComboBoxInitializer.PredefinedKeyTypes，同处）

_ADB_DEVICE_REGEX = re.compile(r"127\.0\.0\.1:(\d+)\s+device")
_PORT_NUMBER_REGEX = re.compile(r"\b(\d{4,5})\b")
_JSON_OBJECT_REGEX = re.compile(r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}")


class _DetectWorker(QObject):
    """自动检测后台线程"""
    status_update = pyqtSignal(str)
    detect_finished = pyqtSignal(list)
    adb_path_found = pyqtSignal(str)
    detect_error = pyqtSignal(str, str)


# ── ADB 触控采集窗体 ──

class AdbTouchForm(BaseDialog):
    COL_CHECK = COLUMNS["check"]
    COL_SEQ = COLUMNS["sequence"]
    COL_SCREEN = COLUMNS["screen"]
    COL_RELATIVE = COLUMNS["relative"]
    COL_HIT = COLUMNS["hits"]
    COL_KEY = COLUMNS["keycap"]
    COL_KEY_TYPE = COLUMNS["type"]
    COL_DELETE = len(COLUMNS)

    def set_overwrite_guard(self, callback, epoch_getter=None):
        self._overwrite_guard = callback
        self._overwrite_epoch_getter = epoch_getter

    def __init__(self, parent=None,
                 keyboard_handler=None, json_getter=None,
                 apply_callback=None, write_callback=None,
                 config_getter=None):
        super().__init__(parent, "ADB 触控采集")
        self.setFont(QFont(theme.FONT_FAMILY, 9))
        self.setFixedSize(600, 520)

        self._keyboard_handler = keyboard_handler
        self._json_getter = json_getter
        self._apply_callback = apply_callback
        self._write_callback = write_callback
        self._config_getter = config_getter

        self._adapter: Optional[AdbClient] = None
        self._is_connected = False
        self._is_collecting = False
        self._is_detecting = False
        self._stop_collection_event = threading.Event()
        self._touch_queue = queue.Queue()
        self._touch_count = 0
        self._screen_width: Optional[int] = None
        self._screen_height: Optional[int] = None
        self._sensor_max_x = 0
        self._sensor_max_y = 0
        self._mouse_max_x = 65535
        self._mouse_max_y = 65535
        self._cached_instances: List[MuMuPortItem] = []
        self._row_data: Dict[int, TouchCoordRowData] = {}
        self._line_count = 0
        self._shown_resolution_msg = False

        self._on_resolution_detected_cb: Optional[Callable[[int, int], None]] = None
        self._apply_coord_cb: Optional[Callable[[str, str], None]] = None
        self._key_type_getter: Optional[Callable[[], str]] = None
        self._key_type_setter: Optional[Callable[[str], None]] = None

        self._session_id = 0
        self._closed = False
        self._connecting = False
        self._editing_key = None
        self._paused_handler = None
        self._generating = False
        self._detect_error_message = ''
        self._build()

        # 跨线程信号桥：键盘捕获回调来自 pynput 监听线程，必须经它转发到主线程（见坑 1）
        self._bridge = ThreadBridge(self)

        # 定时器从采集线程获取坐标并更新表格
        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._drain_touch_queue)
        self._ui_timer.start(50)

        # 生成模式键盘监听集成（注册统一到 set_keyboard_handler，防重复）
        self._is_generate_once = False
        self._is_generate_continuous = False
        self._kb_callbacks_registered = False
        self._last_captured_screen = None  # 最新捕获的屏幕坐标 (x, y)，对应 C# _lastCapturedCoord

    # ── 紧凑布局，生成与采集仍由正式业务函数负责 ──

    def _build(self):
        ct = self.content_layout
        ct.setContentsMargins(8, 6, 8, 6)
        ct.setSpacing(3)

        tip = HintLabel("连接前请开启 MuMu 的 ADB 调试。采集后绑定键帽并勾选，即可生成。", "info")
        ct.addWidget(tip)

        r1 = QHBoxLayout(); r1.setSpacing(4)
        r1.addWidget(QLabel("ADB 路径"))
        self._adb_path_lbl = QLineEdit()
        self._adb_path_lbl.setPlaceholderText("自动检测或手动选择 adb.exe 路径...")
        r1.addWidget(self._adb_path_lbl, 1)
        self._btn_browse_adb = RoundedButton("浏览", "secondary")
        self._btn_browse_adb.clicked.connect(self._on_browse_adb)
        r1.addWidget(self._btn_browse_adb)
        ct.addLayout(r1)

        r2 = QHBoxLayout(); r2.setSpacing(4)
        r2.addWidget(QLabel("实例"))
        self._port_combo = QComboBox()
        self._port_combo.setEditable(False)
        arrow_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icons", "chevron-down.svg").replace("\\", "/")
        self._port_combo.setStyleSheet(
            f'QComboBox {{padding-right:20px;}} QComboBox::down-arrow {{image:url("{arrow_path}");width:12px;height:12px;}}'
        )
        self._port_combo.currentIndexChanged.connect(self._on_instance_changed)
        r2.addWidget(self._port_combo, 1)
        r2.addWidget(QLabel("端口"))
        self._port_edit = QLineEdit()
        self._port_edit.setFixedWidth(64)
        self._port_edit.setPlaceholderText("端口")
        self._port_edit.textChanged.connect(self._on_port_changed)
        r2.addWidget(self._port_edit)
        btn_auto = self._auto_detect_btn = RoundedButton("自动获取", "secondary")
        btn_auto.clicked.connect(self._on_auto_detect)
        r2.addWidget(btn_auto)
        self._btn_clear_settings = RoundedButton("清空设置", "secondary")
        self._btn_clear_settings.clicked.connect(self._on_clear_settings)
        r2.addWidget(self._btn_clear_settings)
        ct.addLayout(r2)

        r3 = QHBoxLayout(); r3.setSpacing(4)
        self._connect_btn = RoundedButton("连接并采集", "primary")
        self._connect_btn.clicked.connect(self._on_toggle_connection)
        r3.addWidget(self._connect_btn)
        self._connection_pill = StatusPill(checked_text="采集中", unchecked_text="未连接", interactive=False)
        r3.addWidget(self._connection_pill)
        self._listen_pill = StatusPill(checked_text="连续监听", unchecked_text="未监听", interactive=False, kind="info")
        r3.addWidget(self._listen_pill)
        r3.addStretch()
        self._resolution_label = QLabel("分辨率：-")
        r3.addWidget(self._resolution_label)
        ct.addLayout(r3)

        # 表格
        self._tbl = TouchTable(self)
        self._tbl.setColumnCount(self.COL_DELETE + 1)
        self._tbl.setHorizontalHeaderItem(self.COL_DELETE, QTableWidgetItem("删除"))
        header = self._tbl.horizontalHeader()
        for column, width in ((self.COL_KEY, 112), (self.COL_KEY_TYPE, 96)):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self._tbl.setColumnWidth(column, width)
        header.setSectionResizeMode(self.COL_DELETE, QHeaderView.Fixed)
        self._tbl.installEventFilter(self)
        self._tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tbl.customContextMenuRequested.connect(self._on_table_context_menu)
        self._tbl.cellDoubleClicked.connect(self._on_cell_double_click)
        self._tbl.cellClicked.connect(self._on_cell_clicked)
        ct.addWidget(self._tbl, 1)

        r4 = QHBoxLayout(); r4.setSpacing(4)
        btn_apply = RoundedButton("应用到主窗口", "secondary")
        btn_apply.clicked.connect(self._on_apply_to_main)
        r4.addWidget(btn_apply)
        btn_clear = RoundedButton("清空列表", "secondary")
        btn_clear.clicked.connect(self._on_clear_table)
        r4.addWidget(btn_clear)
        self._btn_select_all = RoundedButton("全选 / 取消", "ghost")
        self._btn_select_all.clicked.connect(self._toggle_all_checked)
        r4.addWidget(self._btn_select_all)
        r4.addStretch()
        self._btn_gen_checked = RoundedButton("生成勾选", "primary")
        self._btn_gen_checked.clicked.connect(self._on_generate_selected)
        r4.addWidget(self._btn_gen_checked)
        ct.addLayout(r4)

        modes = QHBoxLayout()
        modes.addWidget(QLabel("生成"))
        self._generation_mode = GenerationModeControl()
        self._gen_once_cb = self._generation_mode.onceCheckBox()
        self._gen_multi_cb = self._generation_mode.continuousCheckBox()
        # 只接原复选框槽，不通过modeChanged另起监听。
        self._gen_once_cb.toggled.connect(self._on_generate_once_toggled)
        self._gen_multi_cb.toggled.connect(self._on_generate_multi_toggled)
        modes.addWidget(self._generation_mode)
        modes.addStretch()
        modes.addWidget(QLabel("类型"))
        self._key_type_combo = type_segments()
        modes.addWidget(self._key_type_combo)
        ct.addLayout(modes)
        self._status_label = DetailLabel("未连接")
        self._status_label.setMinimumHeight(18)
        ct.addWidget(self._status_label)
        self._collection_status_label = ElidedStatusLabel("捕获 0 个点")
        self.status_bar.addWidget(self._collection_status_label, 1)
        self._status_label.messageChanged.connect(self._refresh_presentation)
        comfortable_controls(self._adb_path_lbl, self._port_combo, self._port_edit, self._btn_browse_adb,
                             self._auto_detect_btn, self._btn_clear_settings, self._connect_btn,
                             btn_apply, btn_clear, self._btn_select_all, self._btn_gen_checked,
                             self._connection_pill, self._listen_pill,
                             *self._generation_mode._buttons, *self._key_type_combo._buttons)

    def _toggle_all_checked(self):
        target = not all(data.checked for data in self._row_data.values())
        for row, data in self._row_data.items():
            data.checked = target
            self._update_checkbox_display(row)
        self._update_collection_summary()

    def _refresh_presentation(self, *_):
        state = "检测中" if self._is_detecting else "连接中" if self._connecting else "采集中" if self._is_collecting else "已连接" if self._is_connected else "未连接"
        active = self._is_collecting or self._is_connected or self._connecting or self._is_detecting
        self._connection_pill.setStateTexts(state, state)
        self._connection_pill.setChecked(active)
        handler = self._keyboard_handler
        active = bool(handler and handler.is_key_creation_active)
        text = "连续监听" if active and self._gen_multi_cb.isChecked() else "单次监听" if active else "未监听"
        self._listen_pill.setStateTexts(text, text)
        self._listen_pill.setChecked(active)

    # ── 表格列交互 ──

    def _on_cell_clicked(self, row, col):
        if col == self.COL_CHECK and row in self._row_data:
            self._row_data[row].checked = not self._row_data[row].checked
            self._update_checkbox_display(row)
            self._update_collection_summary()

    def _row_for_data(self, data):
        """控件绑定数据身份，删除或聚合重排后再查现行行号。"""
        return next((row for row, current in self._row_data.items() if current is data), -1)

    def _on_data_key_captured(self, data, name, vk):
        self._on_key_capture_for_row(self._row_for_data(data), name, vk, clear=(name == ""))

    def _delete_row(self, row):
        if row not in self._row_data:
            return
        removed = self._row_data[row]
        edit = self._tbl.cellWidget(row, self.COL_KEY)
        if self._editing_key is edit:
            self._editing_key = None
            handler, self._paused_handler = self._paused_handler, None
            if handler:
                handler.resume()
        # 不改采集线程的单调事件计数，也不清空队列；只删真实的当前行数据。
        remaining = [data for index, data in sorted(self._row_data.items()) if index != row]
        blocked = self._tbl.blockSignals(True)
        try:
            self._tbl.removeRow(row)
            self._row_data = dict(enumerate(remaining))
            for index, data in self._row_data.items():
                data.seq = index + 1
                self._tbl.item(index, self.COL_SEQ).setText(str(index + 1))
                self._tbl.render_coordinates(index, data)
            self._tbl.sync_keycap_metadata()
            self._tbl._sync_empty()
        finally:
            self._tbl.blockSignals(blocked)
        last = self._last_captured_screen
        if last and self._screen_width and self._screen_width > 1 and self._screen_height and self._screen_height > 1:
            last_key = (f"{adb_px_to_rel(last[0], self._screen_width):.4f}",
                        f"{adb_px_to_rel(last[1], self._screen_height):.4f}")
            if last_key == (f"{removed.rel_x:.4f}", f"{removed.rel_y:.4f}"):
                self._last_captured_screen = None
        self._update_collection_summary()

    def _on_table_context_menu(self, pos):
        row = self._tbl.rowAt(pos.y())
        if row not in self._row_data:
            return
        data = self._row_data[row]
        self._tbl.selectRow(row)
        menu = QMenu(self._tbl)
        action = menu.addAction("删除此行")
        action.triggered.connect(lambda: self._delete_row(self._row_for_data(data)))
        try:
            menu.exec_(self._tbl.viewport().mapToGlobal(pos))
        finally:
            menu.deleteLater()

    def _update_collection_summary(self):
        count = len(self._row_data)
        checked = sum(data.checked for data in self._row_data.values())
        state = "采集中" if self._is_collecting else "已停止"
        self._collection_status_label.setText(f"{state}，捕获 {count} 个点 · 勾选 {checked} 个")
        self._btn_gen_checked.setToolTip(f"生成勾选的 {checked} 个坐标行")

    def _on_cell_double_click(self, row, col):
        """双击行 → 回填 JSX/JSY 到主窗口"""
        if row in self._row_data:
            data = self._row_data[row]
            if self._apply_coord_cb:
                self._apply_coord_cb(f"{data.rel_x:.16f}", f"{data.rel_y:.16f}")

    def _on_key_capture_for_row(self, row, key_text, vk, clear=False):
        """表格按键列捕获回调 — 对应 C# KeyCell_KeyDown（Esc 清除 + 同键互斥）"""
        if row not in self._row_data:
            return
        data = self._row_data[row]
        if clear:
            data.captured_key = None
            data.vk_code = None
            self._tbl.sync_keycap_metadata()
            return
        # 同键互斥：清空其他行绑定的相同虚拟键码
        if vk:
            for r, other in self._row_data.items():
                if r != row and other.vk_code == vk:
                    other.captured_key = None
                    other.vk_code = None
                    w = self._tbl.cellWidget(r, self.COL_KEY)
                    if isinstance(w, KeyCaptureEdit):
                        w.setText("")
                        w._vk = 0
        data.captured_key = key_text
        data.vk_code = vk
        self._tbl.sync_keycap_metadata()

    def _update_checkbox_display(self, row):
        if row in self._row_data:
            checked = self._row_data[row].checked
            item = self._tbl.item(row, self.COL_CHECK)
            if item is None:
                item = QTableWidgetItem()
                self._tbl.setItem(row, self.COL_CHECK, item)
            item.setText("☑" if checked else "☐")
            item.setTextAlignment(Qt.AlignCenter)

    def _is_exact_duplicate(self, json_str: str, virtual_key: int, key_type: str, rel_x: float, rel_y: float) -> bool:
        """检查 JSON 中是否已存在完全相同的按键（按键名 + 类型 + 坐标 三重匹配）"""
        try:
            import json as json_lib
            data = json_lib.loads(json_str)
            keymaps = data.get("keymaps")
            if not keymaps or not isinstance(keymaps, list):
                return False
            for kmap in keymaps:
                vk = kmap.get("key", {}).get("virtual_key")
                if vk != virtual_key:
                    continue
                existing_type = kmap.get("type")
                if existing_type != key_type:
                    continue
                # 宏的键帽默认偏移 0.03；点击键的键帽也可单独移动，不能据此判断重复。
                coords = try_read_key_coordinates(json_lib.dumps({"keymaps": [kmap]}), vk=virtual_key)
                if coords is None:
                    continue
                ex, ey = map(float, coords)
                if abs(ex - rel_x) < 0.00000000000001 and abs(ey - rel_y) < 0.00000000000001:
                    return True
            return False
        except Exception:
            return False

    # ── 应用到主窗口 ──

    def _on_apply_to_main(self):
        """回填选中行的相对坐标到主窗口 JSX/JSY（对应 C# ApplyToMainForm → SetJSXY）"""
        rows = self._tbl.selectionModel().selectedRows() if self._tbl.selectionModel() else []
        if not rows:
            self._status_label.setText("请先选择一个坐标项。")
            return
        row = rows[0].row()
        if row in self._row_data:
            data = self._row_data[row]
            if self._apply_coord_cb:
                self._apply_coord_cb(f"{data.rel_x:.16f}", f"{data.rel_y:.16f}")

    # ── 生成勾选 ──

    def _on_generate_selected(self):
        if self._generating:
            return False
        checked = [data for data in self._row_data.values() if data.checked]
        if not checked:
            self._status_label.setText("请先勾选坐标行并设置按键。")
            return False
        if any(not data.captured_key for data in checked):
            self._status_label.setText("请先为勾选的行设置按键。")
            return False
        self._sync_key_type_from_main()
        text = self._json_getter() if self._json_getter else None
        if not text:
            self._status_label.setText("主窗口未加载 Json 文件")
            return False
        self._generating = True
        try:
            keys = []
            for data in checked:
                if not all(math.isfinite(value) and 0 <= value <= 1 for value in (data.rel_x, data.rel_y)):
                    self._status_label.setText("坐标无效，未生成按键。")
                    return False
                value = create_key(data.key_type, data.captured_key, f"{data.rel_x:.16f}", f"{data.rel_y:.16f}",
                                   str(get_scan_code(data.vk_code)) if data.vk_code else "0", str(data.vk_code or 0))
                if value is None:
                    self._status_label.setText("按键创建失败，未提交。")
                    return False
                keys.append(value)
            blocks = "[" + ",".join(keys) + "]"
            if not request_overwrite(self, text, blocks):
                return False
            if self._json_getter() != text:
                return False
            updated = write_keys(blocks, text)
            if updated is None:
                self._status_label.setText("JSON 生成失败，未提交。")
                return False
            return self._commit_generated(updated, len(keys))
        except Exception as ex:
            self._status_label.setText(f"生成失败：{ex}")
            return False
        finally:
            self._generating = False

    # ── 生成模式键盘监听 ──

    def _on_generate_once_toggled(self, checked):
        self._set_generate_mode(True, checked)

    def _on_generate_multi_toggled(self, checked):
        self._set_generate_mode(False, checked)

    def _on_key_captured_once(self, key_text, vk=0, epoch=None):
        if epoch is not None and epoch != overwrite_epoch(self):
            return
        mode_revision = self._keyboard_handler.get_mode_revision() if self._keyboard_handler and hasattr(self._keyboard_handler, 'get_mode_revision') else None
        if not self.isVisible() or not self._gen_once_cb.isChecked():
            return
        if self._editing_key is None and self._generate_from_last_captured(key_text, vk):
            self._gen_once_cb.setChecked(False)
            self._status_label.setText(key_created(key_text.upper()) + "；" + listening(False))
        elif (self._gen_once_cb.isChecked() and self._keyboard_handler and
              (mode_revision is None or mode_revision == self._keyboard_handler.get_mode_revision())):
            self._keyboard_handler.set_listen_once_mode(True)

    def _on_key_captured_continuously(self, key_text, vk=0, epoch=None):
        if epoch is not None and epoch != overwrite_epoch(self):
            return
        if self.isVisible() and self._gen_multi_cb.isChecked() and self._editing_key is None:
            self._generate_from_last_captured(key_text, vk)

    def _generate_from_last_captured(self, key_text, vk=0):
        if self._generating or self._editing_key is not None or isinstance(QApplication.focusWidget(), KeyCaptureEdit):
            return False
        if self._last_captured_screen is None:
            self._status_label.setText("请先捕获坐标！")
            return False
        if min(self._screen_width or 0, self._screen_height or 0) <= 1:
            self._status_label.setText("屏幕尺寸必须大于 1，未生成按键。")
            return False
        self._sync_key_type_from_main()
        text = self._json_getter() if self._json_getter else None
        if not text:
            self._status_label.setText("主窗口未加载 Json 文件")
            return False
        x, y = self._last_captured_screen
        rel_x, rel_y = adb_px_to_rel(x, self._screen_width), adb_px_to_rel(y, self._screen_height)
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in (rel_x, rel_y)):
            self._status_label.setText("捕获坐标超出有效屏幕，未生成按键。")
            return False
        name = (key_text or "").upper()
        if not name:
            self._status_label.setText("未捕获到有效按键。")
            return False
        key_type = self._key_type_combo.currentData() or TYPE_CLICK
        if self._is_exact_duplicate(text, vk, key_type, rel_x, rel_y):
            self._status_label.setText(key_exists(name, DETAIL_EXACT_MATCH))
            return False
        self._generating = True
        try:
            value = create_key(key_type, name, f"{rel_x:.16f}", f"{rel_y:.16f}", str(get_scan_code(vk)) if vk else "0", str(vk))
            if value is None or not request_overwrite(self, text, value):
                return False
            if self._json_getter() != text:
                return False
            updated = write_keys(value, text)
            if updated is None:
                self._status_label.setText("按键或 JSON 创建失败，未提交。")
                return False
            if not self._commit_generated(updated, 1):
                return False
            self._status_label.setText(key_created(name))
            return True
        except Exception as ex:
            self._status_label.setText(f"生成失败：{ex}")
            return False
        finally:
            self._generating = False

    def _create_key_type_combo(self, row):
        """创建行内的按键类型下拉框，选项与底部下拉框一致"""
        cb = QComboBox()
        for name, val in KEY_TYPE_ITEMS:
            cb.addItem(name, val)
        # 按行数据自身类型初始化（表格重建后保持该行原选类型）
        data = self._row_data.get(row)
        idx = cb.findData(data.key_type) if data is not None else -1
        cb.setCurrentIndex(idx if idx >= 0 else 0)
        cb.setStyleSheet("QComboBox {padding: 0px 4px; padding-right: 18px;} QComboBox::drop-down {width: 16px;}")
        cb.currentIndexChanged.connect(lambda idx, d=data: self._on_row_key_type_changed(self._row_for_data(d), idx))
        return cb

    def _on_row_key_type_changed(self, row, index):
        """行内下拉框切换 → 同步 row_data"""
        if row in self._row_data:
            cb = self._tbl.cellWidget(row, self.COL_KEY_TYPE)
            if isinstance(cb, QComboBox):
                self._row_data[row].key_type = cb.currentData() or TYPE_CLICK

    # ── 端口选择 ──

    def _on_port_changed(self, text):
        """手动端口只匹配现有实例，未匹配时清空选择并保留输入。"""
        port = text.strip()
        index = next((i for i in range(self._port_combo.count())
                      if isinstance(self._port_combo.itemData(i), MuMuPortItem)
                      and self._port_combo.itemData(i).port == port), -1)
        blocked = self._port_combo.blockSignals(True)
        try:
            self._port_combo.setCurrentIndex(index)
        finally:
            self._port_combo.blockSignals(blocked)

    def _on_instance_changed(self, index):
        """实例下拉框切换时自动填入对应端口号"""
        if index < 0:
            return
        data = self._port_combo.currentData()
        if data is not None and isinstance(data, MuMuPortItem):
            self._port_edit.setText(data.port)

    def _get_selected_port(self) -> str:
        # 手动端口始终优先，非法输入也不能静默回落到历史端口。
        return self._port_edit.text().strip()

    def _on_browse_adb(self):
        current = self._adb_path_lbl.text().strip()
        initial = os.path.dirname(current) if current else os.path.dirname(os.path.abspath(__file__))
        path, _ = QFileDialog.getOpenFileName(self, "选择 ADB 程序", initial,
                                            "ADB 程序 (adb*.exe);;所有文件 (*.*)")
        if path:
            self._adb_path_lbl.setText(path)

    # ── 清空设置 ──

    def _on_clear_settings(self):
        self._disconnect_adb()
        self._adb_path_lbl.clear()
        self._port_combo.clear()
        self._port_combo.addItem("16384", MuMuPortItem("16384"))
        self._port_edit.setText("16384")
        if self._save_config() is not False:
            self._status_label.setText("已清空连接设置：端口 16384，ADB 路径为空。")

    # ── 自动检测（后台线程）──

    def _on_auto_detect(self):
        if self._is_detecting:
            return
        self._is_detecting = True
        self._detect_error_message = ""
        self._detect_path = self._adb_path_lbl.text().strip()
        self._set_connection_controls()
        self._status_label.setText("正在检测 MuMu 模拟器端口...")
        QApplication.processEvents()

        self._worker = _DetectWorker()
        self._detect_thread = threading.Thread(
            target=self._do_auto_detect, args=(self._worker,), daemon=True, name="AdbDetect"
        )
        self._worker.status_update.connect(lambda value, worker=self._worker: self._on_detect_status(value) if self._worker is worker and not self._closed else None)
        self._worker.adb_path_found.connect(lambda value, worker=self._worker: self._on_adb_path_found(value) if self._worker is worker and not self._closed else None)
        self._worker.detect_finished.connect(lambda value, worker=self._worker: self._on_detect_finished(value) if self._worker is worker and not self._closed else None)
        self._worker.detect_error.connect(lambda message, kind, worker=self._worker: self._on_detect_error(message, kind) if self._worker is worker and not self._closed else None)
        self._detect_thread.start()

    def _do_auto_detect(self, worker: _DetectWorker):
        try:
            adb_path = self._resolve_adb_path(getattr(self, "_detect_path", ""))
            if not adb_path:
                worker.detect_error.emit("未找到 adb.exe，请手动指定路径或先启动 MuMu 模拟器。", "warning")
                worker.detect_finished.emit([])
                return
            worker.adb_path_found.emit(adb_path)

            install_base = self._find_mumu_install_dir(adb_path)
            has_mumu12 = False
            has_mumu6 = False
            if install_base:
                has_mumu12 = self._find_mumu_manager_path(install_base) is not None
                has_mumu6 = os.path.isdir(os.path.join(install_base, "emulator", "nemu"))

            port_map: Dict[str, MuMuPortItem] = {}
            pre_existing = set()
            worker.status_update.emit("正在检测 MuMu 模拟器端口...")

            if has_mumu12:
                instances = self._query_mumu12_instances(install_base)
                if instances and len(instances) > 0:
                    for inst in instances:
                        if inst.port and inst.port not in port_map:
                            port_map[inst.port] = inst
                    self._cached_instances = list(instances)
                else:
                    for i in range(10):
                        port = self._query_mumu12_port(install_base, i)
                        if port and port not in port_map:
                            port_map[port] = MuMuPortItem(port=port)

            try:
                result = subprocess.run(
                    [adb_path, "devices"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                for m in _ADB_DEVICE_REGEX.finditer(self._output_text(result.stdout)):
                    p = m.group(1)
                    pre_existing.add(p)
                    if p not in port_map:
                        port_map[p] = MuMuPortItem(port=p)
            except Exception:
                LogService.warn("AdbTouchDialog", "ADB devices 扫描失败")

            if has_mumu6:
                for port in ("7555", "21503", "21513", "21523", "21533"):
                    if port in port_map:
                        continue
                    worker.status_update.emit(f"尝试端口 {port}...")
                    success, unauth = self._try_adb_connect(adb_path, port)
                    if success:
                        port_map[port] = MuMuPortItem(port=port)
                    elif unauth:
                        worker.detect_error.emit("请在模拟器中点击「允许 USB 调试」。", "unauth")

            if not has_mumu12 and not has_mumu6:
                for i in range(20):
                    port = str(16384 + i * 32)
                    if port in port_map:
                        continue
                    worker.status_update.emit(f"尝试端口 {port}...")
                    success, unauth = self._try_adb_connect(adb_path, port)
                    if success:
                        port_map[port] = MuMuPortItem(port=port)
                        break
                    elif unauth:
                        worker.detect_error.emit("请在模拟器中点击「允许 USB 调试」。", "unauth")

            for port in port_map:
                if port not in pre_existing:
                    try:
                        subprocess.run(
                            [adb_path, "disconnect", f"127.0.0.1:{port}"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3,
                            creationflags=subprocess.CREATE_NO_WINDOW
                        )
                    except Exception:
                        pass

            if len(port_map) == 0:
                worker.status_update.emit("无法连接到模拟器，请检查 USB 调试授权。")

            worker.detect_finished.emit(list(port_map.values()))
        except Exception as ex:
            LogService.error_ex("AdbTouchDialog", ex, "自动检测异常")
            worker.detect_error.emit(f"检测异常: {ex}", "warning")
            worker.detect_finished.emit([])

    def _on_detect_status(self, text):
        if not self._detect_error_message:
            self._status_label.setText(text)

    def _on_adb_path_found(self, adb_path):
        if not self._adb_path_lbl.text().strip() or not os.path.isfile(self._adb_path_lbl.text().strip()):
            self._adb_path_lbl.setText(adb_path)

    def _on_detect_error(self, message, error_type):
        self._detect_error_message = message
        self._status_label.setText(message)

    def _on_detect_finished(self, items):
        self._is_detecting = False
        self._set_connection_controls()
        if items:
            self._cached_instances = list(items)
            manual = self._port_edit.text().strip()
            blocked = self._port_combo.blockSignals(True)
            try:
                self._port_combo.clear()
                for item in items:
                    self._port_combo.addItem(item.display_text, item)
                chosen = next((i for i, item in enumerate(items) if item.port == manual), 0)
                self._port_combo.setCurrentIndex(chosen)
            finally:
                self._port_combo.blockSignals(blocked)
            self._port_edit.setText(items[chosen].port)
            self._status_label.setText(f"已检测到 {len(items)} 个端口：" + ", ".join(item.port for item in items) +
                ("；" + self._detect_error_message if self._detect_error_message else ""))
        else:
            self._status_label.setText(self._detect_error_message or "未检测到 MuMu 模拟器，请检查调试设置。")

    # ── ADB 路径解析 ──

    def _resolve_adb_path(self, current=None) -> Optional[str]:
        current = self._adb_path_lbl.text().strip() if current is None else current
        if current and os.path.isfile(current):
            return current

        proc_adb = self._find_adb_via_process("MuMuNxService")
        if proc_adb:
            return proc_adb
        proc_adb = self._find_adb_via_process("crashpad_handler")
        if proc_adb:
            return proc_adb

        # 与 config 同源解析：源码版(=项目根/或 MOMO_DATA_DIR) 与 exe 版(=exe 所在目录) 口径一致
        from core.config_manager import resolve_app_dir
        app_dir = resolve_app_dir()

        local_adb = os.path.join(app_dir, "adb.exe")
        if os.path.isfile(local_adb):
            return local_adb

        import string
        for drive_letter in string.ascii_uppercase:
            drive_root = f"{drive_letter}:\\"
            if not os.path.exists(drive_root):
                continue
            mumu_root = os.path.join(drive_root, "mumu")
            if not os.path.isdir(mumu_root):
                continue
            try:
                for entry in os.listdir(mumu_root):
                    if not entry.lower().startswith("mumuplayer"):
                        continue
                    candidate_dir = os.path.join(mumu_root, entry)
                    p = os.path.join(candidate_dir, "nx_main", "adb.exe")
                    if os.path.isfile(p):
                        return p
                    device_shell = os.path.join(candidate_dir, "nx_device")
                    if os.path.isdir(device_shell):
                        try:
                            for ver_dir in os.listdir(device_shell):
                                p = os.path.join(device_shell, ver_dir, "shell", "adb.exe")
                                if os.path.isfile(p):
                                    return p
                        except Exception:
                            pass
            except Exception:
                pass

        program_dirs = []
        pf = os.environ.get("ProgramFiles", "")
        if pf:
            program_dirs.append(os.path.join(pf, "Netease"))
        pfx86 = os.environ.get("ProgramFiles(x86)", "")
        if pfx86:
            program_dirs.append(os.path.join(pfx86, "Netease"))
        for base_dir in program_dirs:
            if not os.path.isdir(base_dir):
                continue
            try:
                for entry in os.listdir(base_dir):
                    if not entry.lower().startswith("mumuplayer"):
                        continue
                    mu_dir = os.path.join(base_dir, entry)
                    for sub in (
                        os.path.join("shell", "adb.exe"),
                        os.path.join("emulator", "nemu", "vmonitor", "bin", "adb_server.exe"),
                        os.path.join("emulator", "nemu", "vmonitor", "bin", "adb.exe"),
                    ):
                        p = os.path.join(mu_dir, sub)
                        if os.path.isfile(p):
                            return p
            except Exception:
                pass
        return self._find_adb_near_exe(app_dir)

    @staticmethod
    def _find_adb_via_process(process_name):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", process_name):
            return None
        exe_name = process_name if process_name.lower().endswith(".exe") else process_name + ".exe"
        paths = []
        try:
            result = subprocess.run(["wmic", "process", "where", f"name='{exe_name}'", "get", "ExecutablePath", "/format:csv"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            paths = [line.rsplit(",", 1)[-1].strip().strip('"') for line in AdbTouchForm._output_text(result.stdout).splitlines()
                     if line.strip() and "ExecutablePath" not in line]
        except (FileNotFoundError, OSError):
            try:
                result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                    f"Get-CimInstance Win32_Process -Filter \"Name='{exe_name}'\" | Select-Object -ExpandProperty ExecutablePath"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                paths = AdbTouchForm._output_text(result.stdout).splitlines()
            except Exception:
                return None
        except Exception:
            return None
        for path in paths:
            path = path.strip().strip('"')
            if not os.path.isfile(path):
                continue
            directory = os.path.dirname(path)
            for candidate in (os.path.join(directory, "adb.exe"), os.path.normpath(os.path.join(directory, "..", "adb.exe"))):
                if os.path.isfile(candidate):
                    return candidate
        return None

    @staticmethod
    def _find_adb_near_exe(exe_dir: str) -> Optional[str]:
        d = exe_dir
        while d:
            candidate = os.path.join(d, "adb.exe")
            if os.path.isfile(candidate):
                return candidate
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        return None

    @staticmethod
    def _find_mumu_install_dir(start_path: str) -> Optional[str]:
        d = os.path.dirname(start_path)
        while d:
            if os.path.isfile(os.path.join(d, "shell", "adb.exe")):
                return d
            if os.path.isfile(os.path.join(d, "nx_main", "adb.exe")):
                return d
            if os.path.isdir(os.path.join(d, "emulator", "nemu")):
                return d
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        return None

    @staticmethod
    def _find_mumu_manager_path(install_base: str) -> Optional[str]:
        nx_path = os.path.join(install_base, "nx_main", "MuMuManager.exe")
        if os.path.isfile(nx_path):
            return nx_path
        shell_path = os.path.join(install_base, "shell", "MuMuManager.exe")
        if os.path.isfile(shell_path):
            return shell_path
        return None

    def _query_mumu12_instances(self, install_base):
        manager = self._find_mumu_manager_path(install_base)
        if not manager:
            return None
        output = self._execute_mumu_manager(manager, "info -v all")
        if not output:
            return None
        output = self._output_text(output).lstrip("\ufeff")
        try:
            data = json.loads(output)
            items = [data] if isinstance(data, dict) and "adb_port" in data else list(data.values()) if isinstance(data, dict) else data if isinstance(data, list) else []
        except json.JSONDecodeError:
            items = []
            for match in _JSON_OBJECT_REGEX.finditer(output):
                try:
                    items.append(json.loads(match.group()))
                except json.JSONDecodeError:
                    continue
        result = []
        for obj in items:
            if not isinstance(obj, dict):
                continue
            port = str(obj.get("adb_port", "")).strip()
            if port.isascii() and port.isdigit() and 1 <= int(port) <= 65535 and not any(item.port == port for item in result):
                result.append(MuMuPortItem(port, str(obj.get("name", ""))))
        return result or None

    @classmethod
    def _query_mumu12_port(cls, install_base: str, index: int) -> Optional[str]:
        mumu_mgr = cls._find_mumu_manager_path(install_base)
        if not mumu_mgr:
            return None
        output = cls._execute_mumu_manager(mumu_mgr, f"adb -v {index}")
        if output:
            m = _PORT_NUMBER_REGEX.search(output)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _execute_mumu_manager(exe_path: str, arguments: str) -> Optional[str]:
        try:
            # 清除 Qt 环境变量，避免与 MuMuManager 自身的 Qt 库冲突
            clean_env = os.environ.copy()
            for key in list(clean_env.keys()):
                if key.startswith("QT_") or key.startswith("QML"):
                    del clean_env[key]
            result = subprocess.run(
                [exe_path] + arguments.split(),
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                env=clean_env,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return AdbTouchForm._output_text(result.stdout or result.stderr)
        except Exception:
            return None

    @staticmethod
    def _try_adb_connect(adb_path: str, port: str):
        try:
            result = subprocess.run(
                [adb_path, "connect", f"127.0.0.1:{port}"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1.5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            output = AdbTouchForm._output_text(result.stdout).lower() + AdbTouchForm._output_text(result.stderr).lower()
            if "connected to" in output or "already connected" in output:
                return True, False
            if "unauthorized" in output:
                return False, True
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass
        return False, False

    # ── 连接管理 ──

    def _on_toggle_connection(self):
        if self._is_connected:
            self._disconnect_adb()
        else:
            self._connect_adb()

    def _connect_adb(self):
        if self._connecting or self._is_detecting:
            return False
        port = self._get_selected_port()
        if not (port.isascii() and port.isdigit() and 1 <= int(port) <= 65535):
            self._status_label.setText("端口必须是 1～65535 的整数，请检查手动输入。")
            return False
        adb_path = self._adb_path_lbl.text().strip()
        if not adb_path or not os.path.isfile(adb_path):
            adb_path = self._resolve_adb_path()
            if not adb_path:
                self._status_label.setText("未找到 adb.exe：请启动 MuMu，或手动选择有效的 ADB 程序。")
                return False
            self._adb_path_lbl.setText(adb_path)
        self._connecting = True
        self._set_connection_controls()
        try:
            self._adapter = AdbClient(adb_path, f"127.0.0.1:{port}")
            self._status_label.setText("正在连接 ADB...")
            if not self._adapter.connect():
                raise RuntimeError("连接失败，请检查端口号、ADB 调试及模拟器的 USB 调试授权。")
            size = self._adapter.get_screen_size() or ""
            parts = size.strip().replace("x", " ").split()
            if len(parts) != 2:
                raise ValueError(f"无法获取有效屏幕分辨率：{size!r}")
            w, h = map(int, parts)
            if min(w, h) <= 1:
                raise ValueError("屏幕宽高必须大于 1")
            if w < h:
                w, h = h, w
            self._screen_width, self._screen_height = w, h
            self._resolution_label.setText(f"分辨率：{w}×{h}")
            mx, my = self._adapter.get_mouse_max_values()
            self._mouse_max_x, self._mouse_max_y = (mx, my) if mx > 0 and my > 0 else (65535, 65535)
            sx, sy = self._adapter.get_sensor_max_values()
            self._sensor_max_x, self._sensor_max_y = (sx, sy) if sx > 0 and sy > 0 else (0, 0)
            self._is_connected = True
            if not self._start_getevent_collection():
                return False
            self._remember_connected_port(port, adb_path)
            self._save_config()
            return True
        except Exception as ex:
            self._finish_collection(f"ADB 连接失败：{ex}")
            LogService.error_ex("AdbTouchDialog", ex, "ADB 连接异常")
            return False
        finally:
            self._connecting = False
            self._set_connection_controls()

    def _disconnect_adb(self):
        self._finish_collection("未连接")

    def _start_getevent_collection(self):
        if not self._adapter or not self._is_connected or min(self._screen_width or 0, self._screen_height or 0) <= 1:
            self._finish_collection("采集启动失败：连接或屏幕尺寸无效。")
            return False
        try:
            self._session_id += 1
            session = self._session_id
            self._stop_collection_event = threading.Event()
            self._on_clear_table()
            self._raw_sample = ""
            self._line_count = 0
            parser = self._parser = TouchEventParser()
            parser.set_screen_size(self._screen_width, self._screen_height)
            parser.set_mouse_scale(self._mouse_max_x, self._mouse_max_y)
            parser.set_sensor_scale(self._sensor_max_x, self._sensor_max_y)
            parser.on_touch_detected(lambda coord: self._on_touch_detected(coord, session))
            process = self._adapter.start_getevent()
            if process is None:
                raise RuntimeError("无法启动 getevent，请检查 ADB 授权及设备状态。")
            self._is_collecting = True
            self._status_label.setText(f"采集中 ({self._screen_width}x{self._screen_height})")
            self._collection_thread = threading.Thread(target=self._collection_loop,
                args=(process, session, self._stop_collection_event, parser), daemon=True, name="AdbCollection")
            self._collection_thread.start()
            if self._on_resolution_detected_cb:
                self._on_resolution_detected_cb(self._screen_width, self._screen_height)
            if not self._shown_resolution_msg:
                self._shown_resolution_msg = True
                MessageDialog.information(self, "连接成功", f"采集已连接，分辨率 {self._screen_width}×{self._screen_height}。\n已通知主窗口同步。", confirm="OK")
            return True
        except Exception as ex:
            self._finish_collection(f"采集启动失败：{ex}")
            return False

    def _on_touch_detected(self, coord, session=None):
        if (session is not None and session != self._session_id) or not self._is_collecting or coord.is_press:
            return
        if not all(math.isfinite(value) for value in (coord.x, coord.y)):
            return
        self._touch_count += 1
        self._touch_queue.put_nowait((self._touch_count, coord.x, coord.y))
        self._last_captured_screen = (coord.x, coord.y)

    def _collection_loop(self, process, session=None, stop_event=None, parser=None):
        session = self._session_id if session is None else session
        stop_event = self._stop_collection_event if stop_event is None else stop_event
        parser = self._parser if parser is None else parser
        count, error = 0, ""
        try:
            for line in process.stdout:
                if stop_event.is_set() or session != self._session_id:
                    break
                line = self._output_text(line).strip()
                count += 1
                self._line_count = count
                if line:
                    if count <= 5:
                        self._raw_sample = (self._raw_sample + "\n" + line[:120]).strip()
                    parser.parse_line(line)
        except Exception as ex:
            error = str(ex)
            LogService.error_ex("AdbTouchDialog", ex, "采集读取异常")
        finally:
            self._bridge.post(self._on_collection_ended, session, count, error)

    def _drain_touch_queue(self):
        """从采集队列取出坐标，以 (relX:F4, relY:F4) 为键聚合点击数"""
        if self._editing_key is not None:
            return
        self._update_collection_summary()

        batch = []
        while not self._touch_queue.empty():
            try:
                batch.append(self._touch_queue.get_nowait())
            except queue.Empty:
                break
        if not batch:
            return

        # 聚合坐标：以 (relX:F4, relY:F4) 为键
        coord_map = {}  # key: (relX:F4, relY:F4) → TouchCoordRowData
        # 先保留旧行数据中的勾选/按键/类型信息
        for row in range(self._tbl.rowCount()):
            if row in self._row_data:
                old = self._row_data[row]
                key = (f"{old.rel_x:.4f}", f"{old.rel_y:.4f}")
                coord_map[key] = old

        for seq, x, y in batch:
            data = TouchCoordRowData()
            data.seq = seq
            data.screen_x = x
            data.screen_y = y
            if self._screen_width and self._screen_height:
                data.rel_x = adb_px_to_rel(x, self._screen_width)
                data.rel_y = adb_px_to_rel(y, self._screen_height)

            coord_key = (f"{data.rel_x:.4f}", f"{data.rel_y:.4f}")
            if coord_key in coord_map:
                existing = coord_map[coord_key]
                existing.screen_x = x  # 更新为最新屏幕坐标
                existing.screen_y = y
                existing.rel_x = data.rel_x
                existing.rel_y = data.rel_y
                existing.hit_count += 1
            else:
                data.hit_count = 1
                coord_map[coord_key] = data

        # 重建表格（按点击数降序，对应 C# GetUniqueCoords 的 HitCount 排序）
        self._tbl.blockSignals(True)
        self._tbl.setRowCount(0)
        self._row_data.clear()

        sorted_items = sorted(coord_map.items(), key=lambda kv: kv[1].hit_count, reverse=True)
        for i, (coord_key, data) in enumerate(sorted_items):
            row = self._tbl.rowCount()
            self._tbl.insertRow(row)

            if data.rel_x == 0.0 and data.rel_y == 0.0 and data.hit_count <= 1:
                if self._screen_width and self._screen_height:
                    data.rel_x = adb_px_to_rel(data.screen_x, self._screen_width)
                    data.rel_y = adb_px_to_rel(data.screen_y, self._screen_height)

            self._row_data[row] = data
            data.seq = i + 1
            self._update_checkbox_display(row)
            self._tbl.setItem(row, self.COL_SEQ, QTableWidgetItem(str(i + 1)))
            self._tbl.render_coordinates(row, data)
            self._tbl.setItem(row, self.COL_HIT, QTableWidgetItem(str(data.hit_count)))
            # Esc → 回调 ("", 0)：转成旧契约 (row, "", 0, clear=True)
            key_edit = KeyCaptureEdit(extended_keys=True,
                escape_clears=True,
                on_captured=lambda name, vk, d=data: self._on_data_key_captured(d, name, vk),
            )
            key_edit.setText(data.captured_key or "")
            key_edit._vk = data.vk_code or 0
            key_edit.installEventFilter(self)
            self._tbl.setCellWidget(row, self.COL_KEY, key_edit)
            key_type_cb = self._create_key_type_combo(row)
            self._tbl.setCellWidget(row, self.COL_KEY_TYPE, key_type_cb)
            delete_button = QPushButton("×")
            delete_button.setFlat(True)
            delete_button.setFocusPolicy(Qt.NoFocus)
            delete_button.setToolTip("删除此行")
            self._tbl.setCellWidget(row, self.COL_DELETE, delete_button)
            delete_button.setStyleSheet(f"QPushButton {{border: none; background: transparent; color: {theme.INK_2}; padding: 0px; min-width: 0px; font-size: 16px;}} QPushButton:hover {{color: {theme.RED};}}")
            delete_button.clicked.connect(lambda checked=False, d=data: self._delete_row(self._row_for_data(d)))

        self._tbl.scrollToBottom()
        self._tbl.blockSignals(False)
        self._update_collection_summary()

    def _stop_collection(self):
        self._finish_collection("采集已停止，可直接重新连接。")

    def _on_clear_table(self):
        while not self._touch_queue.empty():
            try:
                self._touch_queue.get_nowait()
            except queue.Empty:
                break
        self._tbl.setRowCount(0)
        self._row_data.clear()
        self._touch_count = 0
        self._last_captured_screen = None
        parser = getattr(self, "_parser", None)
        if parser:
            parser.reset_seen_max_values()
        self._update_collection_summary()
        if self._gen_once_cb.isChecked() or self._gen_multi_cb.isChecked():
            self._status_label.setText("列表已清空；请先捕获坐标。")

    def _save_config(self):
        if not self._config_getter:
            return
        try:
            config = self._config_getter()
            if config is None:
                return
            port = self._get_selected_port() or "16384"
            history = [port]
            for i in range(self._port_combo.count()):
                item = self._port_combo.itemData(i)
                value = item.port if isinstance(item, MuMuPortItem) else self._port_combo.itemText(i).strip()
                if value.isascii() and value.isdigit() and 1 <= int(value) <= 65535 and value not in history:
                    history.append(value)
            config.adb_port = port
            config.adb_path = self._adb_path_lbl.text().strip()
            config.adb_ports_history = ",".join(history[:99])
            if config.save() is False:
                self._status_label.setText("连接设置保存失败，请检查配置目录权限。")
                return False
            return True
        except Exception as ex:
            self._status_label.setText(f"连接设置保存失败：{ex}")
            return False

    # ── 主窗口桥接 ──

    def set_on_resolution_detected(self, callback: Callable[[int, int], None]) -> None:
        self._on_resolution_detected_cb = callback

    def set_apply_coord_callback(self, callback: Callable[[float, float], None]) -> None:
        self._apply_coord_cb = callback

    def set_main_window_bridge(self, get_json: Callable, write_json: Callable) -> None:
        self._json_getter = get_json
        self._write_callback = write_json

    def set_config_getter(self, getter: Callable) -> None:
        self._config_getter = getter
        self._load_config()

    def set_key_type_getter(self, getter: Callable) -> None:
        """设置按键类型读取回调（从主窗口同步类型，对应 C# SyncKeyTypeFromMainForm）"""
        self._key_type_getter = getter
        self._sync_key_type_from_main()

    def set_key_type_setter(self, setter: Callable) -> None:
        """设置按键类型写回回调（主窗口 SetCurrentKeyType）"""
        self._key_type_setter = setter
        # 守卫防止重复连接（主窗口 post_init 每次显示都会调用）
        if not getattr(self, "_key_type_signal_registered", False):
            self._key_type_signal_registered = True
            self._key_type_combo.currentIndexChanged.connect(self._on_key_type_changed)

    def _sync_key_type_from_main(self):
        if self._key_type_getter is None:
            return
        try:
            key_type = self._key_type_getter()
        except Exception:
            return
        idx = self._key_type_combo.findData(key_type)
        if idx >= 0:
            self._key_type_combo.setCurrentIndex(idx)

    def _on_key_type_changed(self, index):
        if self._key_type_setter is None:
            return
        key_type = self._key_type_combo.currentData()
        if key_type:
            try:
                self._key_type_setter(key_type)
            except Exception:
                pass

    def _load_config(self):
        if not self._config_getter:
            return
        config = self._config_getter()
        if config is None:
            return
        self._adb_path_lbl.setText(config.adb_path or "")
        port = str(config.adb_port or "16384").strip()
        ports = []
        for value in [port] + str(config.adb_ports_history or "").split(","):
            value = value.strip()
            if value.isascii() and value.isdigit() and 1 <= int(value) <= 65535 and value not in ports:
                ports.append(value)
        if not ports:
            ports = ["16384"]
        blocked = self._port_combo.blockSignals(True)
        try:
            self._port_combo.clear()
            for value in ports:
                cached = next((item for item in self._cached_instances if item.port == value), MuMuPortItem(value))
                self._port_combo.addItem(cached.display_text, cached)
            self._port_combo.setCurrentIndex(ports.index(port) if port in ports else 0)
        finally:
            self._port_combo.blockSignals(blocked)
        self._port_edit.setText(port if port in ports else ports[0])

    def set_keyboard_handler(self, handler):
        if handler is self._keyboard_handler and self._kb_callbacks_registered:
            return
        if self._keyboard_handler and self._keyboard_handler is not handler:
            self._keyboard_handler.stop_all_listening()
        self._keyboard_handler = handler
        self._kb_callbacks_registered = False
        if handler is None:
            return
        self._kb_callbacks_registered = True
        def safe_once(text, vk):
            if self._keyboard_handler is handler and not self._closed:
                self._bridge.post(self._on_key_captured_once, text, vk, overwrite_epoch(self))
        def safe_multi(text, vk):
            if self._keyboard_handler is handler and not self._closed:
                self._bridge.post(self._on_key_captured_continuously, text, vk, overwrite_epoch(self))
        handler.on_key_captured_once(safe_once)
        handler.on_key_captured_continuously(safe_multi)

    # ── 生命周期 ──

    def closeEvent(self, event):
        self._closed = True
        self._save_config()
        self._ui_timer.stop()
        self._is_detecting = False
        if hasattr(self, "_worker"):
            self._worker = None
        set_checked_silently(self._gen_once_cb, False)
        set_checked_silently(self._gen_multi_cb, False)
        self._is_generate_once = self._is_generate_continuous = False
        if self._keyboard_handler:
            self._keyboard_handler.stop_all_listening()
        if self._paused_handler:
            self._paused_handler.resume()
            self._paused_handler = None
        self._editing_key = None
        self._disconnect_adb()
        super().closeEvent(event)

    def _set_connection_controls(self):
        editable = not self._is_connected and not self._connecting and not self._is_detecting
        for widget in (self._port_edit, self._port_combo, self._adb_path_lbl,
                       self._btn_browse_adb, self._btn_clear_settings, self._auto_detect_btn):
            widget.setEnabled(editable)
        self._connect_btn.setEnabled(not self._connecting and not self._is_detecting)
        self._connect_btn.setText("停止并断开" if self._is_connected else "连接并采集")
        self._refresh_presentation()

    def _remember_connected_port(self, port, adb_path):
        cached = list(self._cached_instances)
        if not any(item.port == port and item.instance_name for item in cached):
            base = self._find_mumu_install_dir(adb_path)
            if base:
                cached = self._query_mumu12_instances(base) or cached
        selected = next((item for item in cached if item.port == port), MuMuPortItem(port))
        items = [selected]
        for i in range(self._port_combo.count()):
            item = self._port_combo.itemData(i)
            if isinstance(item, MuMuPortItem) and not any(old.port == item.port for old in items):
                items.append(item)
        blocked = self._port_combo.blockSignals(True)
        try:
            self._port_combo.clear()
            for item in items:
                self._port_combo.addItem(item.display_text, item)
            self._port_combo.setCurrentIndex(0)
        finally:
            self._port_combo.blockSignals(blocked)
        self._port_edit.setText(port)

    def _finish_collection(self, message):
        # 每次结束递增会话号，旧线程排队中的 EOF/坐标不会污染下一次连接。
        self._session_id += 1
        self._stop_collection_event.set()
        adapter, self._adapter = self._adapter, None
        self._is_collecting = False
        self._is_connected = False
        if adapter:
            try:
                adapter.stop_getevent()
                adapter.disconnect()
            except Exception as ex:
                LogService.error_ex("AdbTouchDialog", ex, "停止 ADB 采集")
        self._last_captured_screen = None
        self._screen_width = self._screen_height = None
        self._mouse_max_x = self._mouse_max_y = 65535
        self._sensor_max_x = self._sensor_max_y = 0
        self._resolution_label.setText("分辨率：-")
        self._update_collection_summary()
        self._status_label.setText(message)
        self._set_connection_controls()

    def _on_collection_ended(self, session, count, error):
        if session != self._session_id or self._closed:
            return
        self._drain_touch_queue()
        message = f"采集读取异常：{error}" if error else f"采集线程已结束，共读取 {count} 行；可重新连接。"
        self._finish_collection(message)

    def _commit_generated(self, updated, count):
        if not self._write_callback:
            self._status_label.setText("未设置写回回调，尚未写入按键。")
            return False
        try:
            if self._write_callback(updated) is False:
                self._status_label.setText("写回失败，按键未提交。")
                return False
        except Exception as ex:
            self._status_label.setText(f"写回失败：{ex}")
            return False
        self._last_generated_count = count
        self._status_label.setText(f"已成功写入 {count} 个按键")
        return True

    def _set_generate_mode(self, once, enabled):
        own, other = (self._gen_once_cb, self._gen_multi_cb) if once else (self._gen_multi_cb, self._gen_once_cb)
        handler = self._keyboard_handler
        if enabled:
            set_checked_silently(other, False)
            self._sync_key_type_from_main()
        try:
            if handler:
                if once:
                    handler.set_listen_once_mode(enabled)
                else:
                    handler.set_listen_continuously_mode(enabled)
                if enabled and not handler.is_key_creation_active:
                    raise RuntimeError("键盘监听未启动，请检查权限")
            elif enabled:
                raise RuntimeError("未设置键盘监听器")
            self._is_generate_once = self._gen_once_cb.isChecked()
            self._is_generate_continuous = self._gen_multi_cb.isChecked()
            active = self._is_generate_once or self._is_generate_continuous
            if handler and not active:
                handler.stop_all_listening()
            self._status_label.setText(listening(active, continuous=self._is_generate_continuous) +
                                       ("；请先捕获坐标" if active and self._last_captured_screen is None else ""))
        except Exception as ex:
            set_checked_silently(own, False)
            set_checked_silently(other, False)
            self._is_generate_once = self._is_generate_continuous = False
            if handler:
                handler.stop_all_listening()
            self._status_label.setText(f"生成功能未开启：{ex}")
        finally:
            # 模式切换可能产生相同提示文本，也必须刷新真实监听状态。
            self._refresh_presentation()

    def eventFilter(self, watched, event):
        if watched is self._tbl and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Delete:
            selected = self._tbl.selectionModel().selectedRows()
            for row in sorted((index.row() for index in selected), reverse=True):
                self._delete_row(row)
            event.accept()
            return True
        if isinstance(watched, KeyCaptureEdit):
            if event.type() == QEvent.FocusIn:
                self._editing_key = watched
                if self._keyboard_handler and self._paused_handler is None:
                    self._paused_handler = self._keyboard_handler
                    self._paused_handler.pause()
            elif event.type() == QEvent.FocusOut and self._editing_key is watched:
                self._editing_key = None
                handler, self._paused_handler = self._paused_handler, None
                if handler:
                    handler.resume()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        self._closed = False
        self._ui_timer.start(50)
        self._set_connection_controls()
        super().showEvent(event)
        # 表格加入内容布局后才继承最终字体；包含两侧各 4px 及原生表头边距。
        header = self._tbl.horizontalHeader()
        delete_width = max(header.fontMetrics().horizontalAdvance("删除") + 8,
                           header.sectionSizeHint(self.COL_DELETE))
        self._tbl.setColumnWidth(self.COL_DELETE, delete_width)

    @staticmethod
    def _output_text(value):
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
