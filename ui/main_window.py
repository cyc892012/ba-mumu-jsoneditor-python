"""
主窗口 — 摸点小助手 3.0（Frameless + 自定义标题栏 + 程序图标）
"""
import os
import sys
import json
import math
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QCheckBox, QTabWidget,
    QStatusBar, QPushButton, QLineEdit, QSizePolicy,
    QFileDialog, QShortcut,
)
from PyQt5.QtCore import Qt, QPoint, QTimer, QFileSystemWatcher, QObject, pyqtSignal
from PyQt5.QtGui import (
    QMouseEvent, QDragEnterEvent, QDropEvent, QKeySequence,
    QDoubleValidator,
)
from typing import Optional
import theme
from controls.segmented import SegmentedControl
from controls.hina_aside import HinaAside
from controls.status_pill import AutoSyncPill
from controls.keycap_style import apply_keycap_style
from controls.combo_box_utils import KEY_TYPE_ITEMS
from controls.p2_window_helpers import ElidedStatusLabel, compact_button, set_icon, ToolTile, install_combo_elision
from .p2_window_management import position_subwindow
from .message_dialog import MessageDialog
from .key_overwrite import OverwriteCoordinator, key_conflicts
from .status_text import key_exists
from controls import (RoundedButton, CardPanel, FadedBackgroundPanel,
                      SearchableComboBox, KeyCaptureEdit, TitleBar,
                      rebuild_combo, KEY_TYPE_DISPLAY_NAMES, key_type_display_name)
from core.config_manager import ConfigManager
from core.coord_model import rel_to_px, px_to_rel, adb_px_to_rel, landing_hint
from core.backup_manager import BackupManager
from core.app_version import APP_VERSION
from core.json_editor import (
    find_key_texts, find_key_values, find_type_by_text,
    find_key_by_virtual_key,
    read_key_by_text, re_key_by_text, re_key_by_virtual_key,
    find_type_by_virtual_key, try_read_key_coordinates, delete_keys_by_indexes,
    create_key, write_keys, get_key_count, get_key_count_from_obj,
    delete_keys_by_virtual_keys,
    get_scan_code,
    read_json_file, write_text_atomic, get_last_write_error,
    TYPE_CLICK, TYPE_MACRO, TYPE_BUNCH_CLICK,
)
from core.resolution_manager import (
    ResolutionManager, TYPE_TABLET, TYPE_PHONE, TYPE_ULTRA_WIDE,
)
from core.package_names import (
    OTHER_PREFIX, PACKAGE_PREFIX_BY_NAME, REAL_PACKAGE_NAMES,
    filter_keymap_files, package_name_from_path, package_prefix,
)
from core.operation_constants import (
    CREATE_KEY, MODIFY_KEY,
    PRESET_INJECT, RANGE_CLEAR,
    ADB_GENERATE, WIDE_SCREEN,
)
from utils.safe_parse import try_parse_int, try_parse_float
from utils.win_topmost import apply_topmost
from infra.index_backup import IndexFileBackupManager
from infra.log_service import LogService

# 注：MuMuKeymapSyncService(watchdog) 与 KeyboardBindingHandler(pynput/keyboard)
# 已改为使用处局部导入，避免拖慢启动 import 链（对应延迟导入优化）


# 按键捕获框已统一到 controls/key_capture_edit.py（本项目曾有 4 份重复实现）。
# 主窗口用「简单模式」：只认可打印单字符，点击清空，暴露 vk 属性。


# 跨线程信号桥：后台线程(watchdog/轮询/pynput)不能直接操作 Qt 控件，
# 这里只在后台线程 emit，信号 auto-queued 到主线程在槽里真正操作 UI（线程安全）
class _CrossThreadSignals(QObject):
    index_task_done = pyqtSignal(object)
    sync_path_changed = pyqtSignal(str)          # 键位同步：新方案路径
    key_captured = pyqtSignal(str, int, bool, int)    # 全局按键盘捕获：(文本, 虚拟键码, 是否连续模式)


_SHARED_SIGNALS = _CrossThreadSignals()


class MainWindow(QMainWindow):
    # 包名表已收口到 core/package_names.py（对应 C# PackageNameTypes），
    # 分辨率预设统一以 core.resolution_manager.ResolutionManager 为唯一来源，
    # 此处仅做一次「展示结构」派生：键=全角显示文字（如 "1280×720"），值=元组 (W, H)。
    # 不再手写第二份副本，杜绝两处漂移。
    _RESOLUTION_PRESETS = {
        label: {
            f"{w}×{h}": (int(w), int(h))
            for _key, val in ResolutionManager.get_resolution_dict_by_type(code).items()
            for w, h in [val.split(",")]
        }
        for label, code in (("平板", TYPE_TABLET), ("手机", TYPE_PHONE), ("超宽屏", TYPE_ULTRA_WIDE))
    }

    def __init__(self):
        super().__init__()
        # 原生窗口标题（任务栏/ALT+TAB 显示，frameless 下不影响自定义标题栏）
        self.setWindowTitle(f"摸点小助手 {APP_VERSION}")
        self._sub_windows = {}
        self._overwrite_coordinator = OverwriteCoordinator()
        self._dragging = False
        self._drag_pos = QPoint()
        self._topmost = False   # 置顶状态（用 Win32 SetWindowPos 切换，不自带 styles 标志）
        # 全进程共享一份配置实例：避免各面板各持旧快照、保存时互相覆盖
        self._config = ConfigManager.shared()
        self._backup_mgr = BackupManager()
        self._current_fx = self._config.fx
        self._current_fy = self._config.fy
        self._accepted_file_versions = {}
        self._observed_file_versions = {}
        self._file_prompt_active = False
        self._pending_external_path = None
        self._external_change_timer = QTimer(self)
        self._external_change_timer.setSingleShot(True)
        self._external_change_timer.setInterval(2000)
        self._external_change_timer.timeout.connect(self._flush_external_change)
        self._is_processing = False
        self._status_label = None
        self._json_str = None
        self._json_path = None
        self._res_locked = False
        self._sync_service = None
        self._is_auto_sync = False
        # 自己写盘的基线时间戳（纳秒）—— 文件监视回调据此忽略自己的保存
        self._last_self_write_mtime_ns = None
        self._needs_index_backup = False
        self._pending_index_backups = {}
        self._index_executor = None
        self._index_inflight = set()
        self._closing = False
        self._index_backup_mgr = None
        self._index_backup_timer = None
        self._pending_check_scheme_path = None
        self._index_damage_notified = set()
        self._index_check_timer = QTimer(self)
        self._index_check_timer.setSingleShot(True)
        self._index_check_timer.setInterval(3000)
        self._index_check_timer.timeout.connect(self._index_check_timer_tick)
        # 跨线程桥接：把后台线程事件接回主线程的处理器（连接的信号槽会在主线程执行）
        _SHARED_SIGNALS.sync_path_changed.connect(self._on_sync_keymap_changed)
        _SHARED_SIGNALS.index_task_done.connect(self._on_index_task_done)
        _SHARED_SIGNALS.key_captured.connect(self._on_global_key_captured)
        self._custom_resolutions = {}  # dict of "宽度x高度" → "宽,高"
        self._file_watcher = QFileSystemWatcher()
        self._file_watcher.fileChanged.connect(self._queue_external_file_changed)
        self._file_watcher.directoryChanged.connect(self._on_external_directory_changed)
        # 共享全局键盘监听（对应 C# KeyboardBindingHandler）：主窗体 + AdbTouchForm 共用
        # 懒加载：首次访问 _kb_handler 属性才创建（延迟 pynput/keyboard 导入）
        self._kb_handler_instance = None
        self.setAcceptDrops(True)
        self._init_ui()
        # Ctrl+Z / Ctrl+Y 撤销重做快捷键（对应 C# 隐藏菜单）
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self._on_undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self._on_redo)
        # 目录扫描/文件加载/同步服务等延后到首帧显示后执行（对应启动优化 deferred init），
        # 窗口先出现，状态栏提示初始化中
        self._update_status("正在初始化…")
        QTimer.singleShot(50, self._deferred_init)

    def _deferred_init(self):
        """首帧显示后的后台初始化（原 __init__ 尾部逻辑）"""
        # 自动检测 MuMu 键位配置目录。
        # ★ 若用户此刻已经加载了文件（如启动后立即拖放 JSON），必须跳过——
        #   否则下面的 _load_json_file 会 record_initial 清空撤销栈，
        #   并把用户已加载的内容静默重置（实测：本函数晚于用户操作触发时会清栈）。
        if not self._json_str:
            self._auto_detect_keymap_dir()
        else:
            self._refresh_file_list()
            self._try_auto_sync()
            self._try_auto_sync_from_emulator_keymap()
        self._load_custom_resolutions()
        self._sync_resolution_controls()  # 展示已存尺寸，不覆盖默认值
        self._update_status("配置已加载 | FX={} FY={}".format(self._get_fx(), self._get_fy()))
        # 启动后台索引备份定时器（与 C# 一致，60s 间隔）
        self._start_index_backup_timer()

    @property
    def _kb_handler(self):
        """共享键盘监听（懒加载）：首次访问才导入 pynput/keyboard 并创建 + 注册回调"""
        if self._kb_handler_instance is None:
            from input.keyboard_handler import KeyboardBindingHandler
            inst = KeyboardBindingHandler()
            # 用 pyqtSignal 桥把后台线程(pynput)事件转发到主线程：
            # emit 会 auto-queued 到主线程，槽必然在主线程操作 Qt 控件（线程安全）
            inst.on_key_captured_once(lambda k, v: _SHARED_SIGNALS.key_captured.emit(k, v, False, self._overwrite_coordinator.get_epoch()))
            inst.on_key_captured_continuously(lambda k, v: _SHARED_SIGNALS.key_captured.emit(k, v, True, self._overwrite_coordinator.get_epoch()))
            self._kb_handler_instance = inst
        return self._kb_handler_instance

    def _init_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(660, 476)
        self.setStyleSheet(theme.GLOBAL_QSS)
        central = QWidget()
        central.setObjectName("MainCentral")
        central.setStyleSheet(f"QWidget#MainCentral {{background:{theme.BG_MAIN};}}")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._build_title_bar(root)
        self._body_row = QHBoxLayout()
        self._body_row.setContentsMargins(0, 0, 0, 0)
        self._body_row.setSpacing(0)
        self._content_widget = QWidget()
        content = QVBoxLayout(self._content_widget)
        content.setContentsMargins(8, 8, 8, 8)
        content.setSpacing(6)
        self._build_file_card(content)
        self._build_key_card(content)
        content.addStretch(1)
        self._build_tabs(content)
        self._body_row.addWidget(self._content_widget, 1)
        root.addLayout(self._body_row, 1)
        self._build_statusbar()
        self._add_bg(central)

    # ── 拖放加载 JSON 文件 ─────────────────────
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """仅接受 .json 文件的拖放"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".json"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path.lower().endswith(".json"):
                    if self._load_scheme_path(path, manual=True):
                        event.acceptProposedAction()
                    return
        event.ignore()

    def _build_title_bar(self, root):
        bar = TitleBar("摸点小助手 3.0", parent=self, show_minimize=True)
        self._title_bar = bar
        self._top_btn = bar._top_btn
        # 主窗口保留原有置顶处理函数，标题栏仅同步外观。
        self._top_btn.clicked.disconnect(bar._on_topmost)
        self._top_btn.clicked.connect(self._on_topmost_click)
        bar.close_clicked.connect(self.close)

        # 让标题栏拖拽移动窗口
        bar.mousePressEvent = self._bar_press
        bar.mouseMoveEvent = self._bar_move
        bar.mouseReleaseEvent = self._bar_release

        root.addWidget(bar)

    def _on_topmost_click(self):
        self._topmost = not self._topmost
        apply_topmost(self, self._topmost)
        self._title_bar.set_topmost_visual(self._topmost)

    def _bar_press(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_pos = event.globalPos()

    def _bar_move(self, event: QMouseEvent):
        if self._dragging:
            delta = event.globalPos() - self._drag_pos
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self._drag_pos = event.globalPos()

    def _bar_release(self, event: QMouseEvent):
        self._dragging = False

    # ── 文件与分辨率 ──────────────────────────
    def _build_file_card(self, root):
        card = CardPanel()
        self._file_card = card
        ly = card.content_layout
        ly.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("键位方案")
        title.setStyleSheet(f"QLabel {{color:{theme.VIOLET_700};font-size:12px;font-weight:bold;}}")
        head.addWidget(title)
        self._auto_sync_cb = AutoSyncPill(checked=True)
        self._auto_sync_cb.toggled.connect(self._on_auto_sync_toggled)
        head.addWidget(self._auto_sync_cb)
        head.addStretch()
        self._undo_btn = compact_button("", width=26)
        self._redo_btn = compact_button("", width=26)
        set_icon(self._undo_btn, self._title_bar._icon_family, "\uE7A7", "撤", "撤销 Ctrl+Z")
        set_icon(self._redo_btn, self._title_bar._icon_family, "\uE7A6", "重", "重做 Ctrl+Y")
        self._undo_btn.clicked.connect(self._on_undo)
        self._redo_btn.clicked.connect(self._on_redo)
        self._history_btn = compact_button("历史", width=42)
        self._history_btn.setToolTip("查看当前方案的撤销/重做历史；跳转历史会回写当前方案")
        self._history_btn.clicked.connect(self._open_undo_history)
        for button in (self._undo_btn, self._redo_btn, self._history_btn):
            head.addWidget(button)
        ly.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(QLabel("区服"))
        self._package_cb = QComboBox()
        self._package_cb.addItems(["官服", "B服", "日服", "国际服", "其他", "宇宙服"])
        self._package_cb.setFixedWidth(64)
        self._package_cb.setFixedHeight(26)
        row.addWidget(self._package_cb)
        self._file_cb = SearchableComboBox()
        self._file_cb.setMinimumWidth(0)
        self._file_cb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._file_cb._combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._file_cb._combo.setMinimumContentsLength(1)
        install_combo_elision(self._file_cb.combo, refresh_tooltip=self._p2_refresh_file_tooltip)
        self._file_cb.setFixedHeight(26)
        row.addWidget(self._file_cb, 1)
        self._browse_btn = compact_button("浏览", "secondary", width=44)
        self._browse_btn.clicked.connect(self._browse_json_file)
        self._dir_btn = compact_button("", "secondary", width=26)
        set_icon(self._dir_btn, self._title_bar._icon_family, "\uE838", "目录"[0], "打开方案所在目录")
        self._dir_btn.clicked.connect(self._open_json_dir)
        self._get_path_btn = compact_button("", "secondary", width=26)
        set_icon(self._get_path_btn, self._title_bar._icon_family, "\uE72C", "寻", "获取路径：重新定位 MuMu 键位目录")
        self._get_path_btn.clicked.connect(self._on_get_path_clicked)
        for button in (self._browse_btn, self._dir_btn, self._get_path_btn):
            row.addWidget(button)
        ly.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(4)
        label = QLabel("分辨率")
        label.setStyleSheet("QLabel {font-size:11px;}")
        row.addWidget(label)
        self._ratio_cb = QComboBox()
        self._ratio_cb.addItems(["平板", "手机", "超宽屏", "自定义"])
        self._ratio_cb.setFixedSize(68, 26)
        row.addWidget(self._ratio_cb)
        self._res_cb = QComboBox()
        self._res_cb.setFixedSize(100, 26)
        self._res_cb.setEditable(False)
        self._res_cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        row.addWidget(self._res_cb)
        self._lock_res_btn = compact_button("", width=26)
        set_icon(self._lock_res_btn, self._title_bar._icon_family, "\uE785", "锁", "锁定分辨率")
        self._lock_res_btn.clicked.connect(self._toggle_res_lock)
        row.addWidget(self._lock_res_btn)
        row.addStretch(1)
        self._save_res_btn = compact_button("存为默认", width=62)
        self._read_res_btn = compact_button("载入默认", width=62)
        self._save_res_btn.clicked.connect(self._save_resolution)
        self._read_res_btn.clicked.connect(self._read_resolution)
        self._delete_res_btn = compact_button("", "danger", width=26)
        set_icon(self._delete_res_btn, self._title_bar._icon_family, "\uE74D", "删", "删除当前自定义分辨率")
        self._delete_res_btn.clicked.connect(self._delete_resolution)
        for button in (self._save_res_btn, self._read_res_btn, self._delete_res_btn):
            row.addWidget(button)
        self._delete_res_btn.hide()
        ly.addLayout(row)
        root.addWidget(card)
        # 展开只扫描候选；用户确认选择才进入最新加载事务。
        self._file_cb.popupAboutToShow.connect(self._refresh_file_list)
        self._file_cb.popupAboutToShow.connect(self._p2_refresh_file_tooltip)
        self._file_cb.activated.connect(self._on_file_cb_user_selected)
        self._file_cb.activated.connect(self._p2_refresh_file_tooltip)
        self._package_cb.currentIndexChanged.connect(self._on_package_changed)
        self._ratio_cb.currentIndexChanged.connect(self._on_resolution_type_changed)
        self._res_cb.currentIndexChanged.connect(self._on_resolution_changed)
        self._res_cb.editTextChanged.connect(self._on_custom_res_input_changed)
        self._res_cb.editTextChanged.connect(self._p2_refresh_status_badges)
        self._file_cb.currentTextChanged.connect(self._p2_refresh_file_tooltip)

    # ── 按键编辑 ──────────────────────────────
    def _build_key_card(self, root):
        card = CardPanel()
        self._write_card = card
        ly = card.content_layout
        ly.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel("写入按键")
        title.setStyleSheet(f"QLabel {{color:{theme.VIOLET_700};font-weight:bold;font-size:12px;}}")
        head.addWidget(title)
        head.addStretch()
        note = ElidedStatusLabel("实际落点与输入一致")
        self._landing_hint = note
        note.setStyleSheet(f"QLabel {{color:{theme.INK_2};font-size:11px;}}")
        head.addWidget(note, 1)
        ly.addLayout(head)
        body = QHBoxLayout()
        body.setSpacing(8)
        self._coordinate_panel = QWidget()
        coordinates = QVBoxLayout(self._coordinate_panel)
        coordinates.setContentsMargins(0, 0, 0, 0)
        coordinates.setSpacing(6)
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.k_lock = QCheckBox("锁")
        self.js_lock = QCheckBox("锁")
        self.k_lock.setToolTip("锁定后禁止手动编辑开发者坐标；手动换算以开发者坐标为来源")
        self.js_lock.setToolTip("锁定后禁止手动编辑 JSON 坐标；手动换算以 JSON 坐标为来源")
        for column, caption, lock in ((1, "开发者 px", self.k_lock), (3, "JSON 相对", self.js_lock)):
            heading = QHBoxLayout()
            heading.setSpacing(2)
            label = QLabel(caption)
            label.setStyleSheet(f"QLabel {{font-size:11px;color:{theme.INK_2};}}")
            heading.addWidget(label)
            heading.addStretch()
            heading.addWidget(lock)
            grid.addLayout(heading, 0, column)
        self.kx_input = _in("0", 0)
        self.ky_input = _in("0", 0)
        self.jsx_input = _in("0", 0)
        self.jsy_input = _in("0", 0)
        for row_index, name, developer, relative in ((1, "X", self.kx_input, self.jsx_input), (2, "Y", self.ky_input, self.jsy_input)):
            grid.addWidget(QLabel(name), row_index, 0)
            grid.addWidget(developer, row_index, 1)
            grid.addWidget(QLabel("⇄"), row_index, 2)
            grid.addWidget(relative, row_index, 3)
            for edit in (developer, relative):
                edit.setFixedHeight(26)
                edit.setStyleSheet(edit.styleSheet() + '\nQLineEdit {font-family:"Consolas";font-size:11px;padding:1px 3px;}')
                edit.textChanged.connect(lambda text, e=edit: e.setToolTip(text))
        coordinates.addLayout(grid)
        footer = QHBoxLayout()
        footer.setSpacing(4)
        self._fx_fy_label = QLabel(f"FX {self._get_fx()} · FY {self._get_fy()}")
        self._fx_fy_label.setStyleSheet(f"QLabel {{font-size:10px;color:{theme.INK_2};}}")
        footer.addWidget(self._fx_fy_label)
        footer.addStretch()
        self._convert_btn = compact_button("按锁定方向换算", width=100)
        self._convert_btn.setStyleSheet(self._convert_btn.styleSheet() + '\nQPushButton {font-size:11px;}')
        self._convert_btn.clicked.connect(self._on_convert)
        footer.addWidget(self._convert_btn)
        coordinates.addLayout(footer)
        coordinates.addStretch()
        body.addWidget(self._coordinate_panel, 1)
        side = QWidget()
        side.setFixedWidth(192)
        side.setObjectName("P2KeySide")
        side.setStyleSheet(f"QWidget#P2KeySide {{border-left:1px dashed {theme.LINE_STRONG};}}")
        key_layout = QVBoxLayout(side)
        key_layout.setContentsMargins(8, 0, 0, 0)
        key_layout.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(QLabel("按键"))
        self._key_input = KeyCaptureEdit(allow_special=True, extended_keys=True, clear_on_click=True)
        self._key_input.setFixedWidth(40)
        apply_keycap_style(self._key_input)
        self._key_input.textChanged.connect(self._key_input.setToolTip)
        row.addWidget(self._key_input)
        self._check_btn = compact_button("检查", width=36)
        self._check_btn.setToolTip("在当前方案中检查捕获的按键及其类型")
        self._check_btn.clicked.connect(self._check_key)
        row.addWidget(self._check_btn)
        self._read_coord_btn = compact_button("读取坐标", width=60)
        self._read_coord_btn.setToolTip("按虚拟键码从当前方案读取坐标并回填；不会修改文件")
        self._read_coord_btn.clicked.connect(self._read_key_coord)
        row.addWidget(self._read_coord_btn)
        key_layout.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(QLabel("类型"))
        self._key_type_cb = SegmentedControl()
        for text, value in KEY_TYPE_ITEMS:
            self._key_type_cb.addItem(text, value)
        self._key_type_cb.setItemCaption(0, "点击")
        self._key_type_cb.setItemCaption(1, "宏指牌")
        self._key_type_cb.currentIndexChanged.connect(self._on_main_key_type_changed)
        row.addWidget(self._key_type_cb)
        row.addStretch()
        key_layout.addLayout(row)
        self._force_replace_cb = QCheckBox("强制替换已有键")
        self._force_replace_cb.setToolTip("允许替换已有同 VK 按键；覆盖旧点位前仍需确认")
        key_layout.addWidget(self._force_replace_cb)
        self._modify_save_btn = compact_button("修改并保存", "primary", height=32)
        self._modify_save_btn.setToolTip("将此按键和坐标写回当前方案；覆盖已有点位需确认")
        self._modify_save_btn.clicked.connect(self._modify_save_key)
        key_layout.addWidget(self._modify_save_btn)
        body.addWidget(side)
        ly.addLayout(body)
        root.addWidget(card)
        self.kx_input.textChanged.connect(self._on_k_input_changed)
        self.ky_input.textChanged.connect(self._on_k_input_changed)
        self.jsx_input.textChanged.connect(self._on_js_input_changed)
        self.jsy_input.textChanged.connect(self._on_js_input_changed)
        # 坐标控件从页签移入卡片时，同时迁入正式源码的只读连接。
        self.k_lock.toggled.connect(lambda locked: (self.kx_input.setReadOnly(locked), self.ky_input.setReadOnly(locked)))
        self.js_lock.toggled.connect(lambda locked: (self.jsx_input.setReadOnly(locked), self.jsy_input.setReadOnly(locked)))

    # ── TabControl ────────────────────────────
    def _build_tabs(self, root):
        # 保留 tab 对象类型、页签名和索引接口；实际内容改为两张卡与常驻工具坞。
        self.tab = QTabWidget(self)
        self.tab.addTab(QWidget(), "坐标计算")
        self.tab.addTab(QWidget(), "更多功能")
        self.tab.hide()
        dock = QWidget()
        self._tool_dock = dock
        grid = QGridLayout(dock)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(2)
        self._tool_buttons = {}
        groups = (
            ("取点", (("adb", "ADB 触控采集", self._open_adb_touch), ("mouse", "鼠标回溯", self._open_mouse_track),
                        ("quick", "全屏快速摸点", self._open_quick_touch), ("wide", "超宽屏转换", self._open_wide_screen))),
            ("工具", (("preset", "键位预设", self._open_key_preset), ("clear", "区域清空", self._open_range_clear),
                        ("backup", "索引备份", self._open_index_backup), ("logs", "日志查看", self._open_log_viewer))),
        )
        tool_hints = {
            "adb": "连接模拟器后采集触控坐标，以当前方案和分辨率生成按键",
            "mouse": "查看鼠标坐标并使用子窗内设置的热键回溯；启用前检查快捷键",
            "quick": "打开全屏快速摸点；使用当前方案和实时分辨率，生成模式与热键在子窗设置",
            "wide": "以当前方案和分辨率为来源，转换单键或整份方案到目标宽屏尺寸",
            "preset": "读取预设中的真实按键并应用到当前方案，覆盖已有点位前需确认",
            "clear": "按矩形坐标范围预览并清空当前方案按键；操作范围以子窗预览为准",
            "backup": "查看所选区服的索引备份并恢复；所选区服优先于此前加载的方案",
            "logs": "查看运行日志与失败原因，可按子窗提供的条件筛选",
        }
        for row_index, (name, tools) in enumerate(groups):
            label = QLabel(name)
            label.setStyleSheet(f"QLabel {{font-size:11px;color:{theme.INK_2};}}")
            grid.addWidget(label, row_index, 0)
            for column, (key, text, callback) in enumerate(tools, 1):
                tile = ToolTile(text, callback, recommended=key == "adb")
                tile.button.setToolTip(tool_hints[key])
                self._tool_buttons[key] = tile.button
                grid.addWidget(tile, row_index, column)
                grid.setColumnStretch(column, 1)
        root.addWidget(dock)

    # ── 坐标计算逻辑 ──────────────────────────
    def _get_fx(self):
        ok, val = try_parse_int(getattr(self, "_current_fx", self._config.fx))
        return val if ok and val > 1 else 1280

    def _get_fy(self):
        ok, val = try_parse_int(getattr(self, "_current_fy", self._config.fy))
        return val if ok and val > 1 else 720

    def _calc_dev_to_json(self):
        """两轴均有效才一起回填，保留浮点精度。"""
        ok_x, x = try_parse_float(self.kx_input.text())
        ok_y, y = try_parse_float(self.ky_input.text())
        fx, fy = self._get_fx(), self._get_fy()
        if not (ok_x and ok_y and fx > 1 and fy > 1):
            self._landing_hint.setText("请输入完整的有限数坐标")
            return False
        self.jsx_input.setText(f"{px_to_rel(x, fx):.16f}")
        self.jsy_input.setText(f"{px_to_rel(y, fy):.16f}")
        self._landing_hint.setText(landing_hint(x, y, fx, fy))
        return True

    def _calc_json_to_dev(self):
        ok_x, x = try_parse_float(self.jsx_input.text())
        ok_y, y = try_parse_float(self.jsy_input.text())
        fx, fy = self._get_fx(), self._get_fy()
        if not (ok_x and ok_y and fx > 1 and fy > 1):
            self._landing_hint.setText("请输入完整的有限数坐标")
            return False
        x, y = rel_to_px(x, fx), rel_to_px(y, fy)
        if not (math.isfinite(x) and math.isfinite(y)):
            return False
        self.kx_input.setText(f"{x:.16g}")
        self.ky_input.setText(f"{y:.16g}")
        self._landing_hint.setText(landing_hint(x, y, fx, fy))
        return True

    def _on_k_input_changed(self):
        """KX/KY 输入变化：始终重算 JSON 坐标（对应 C# KtextBox_TextChanged，不检查锁）"""
        if self._is_processing:
            return
        sender = self.sender()
        self._is_processing = True
        try:
            self._calc_dev_to_json()
            if sender is self.kx_input:
                self._update_status("KX → JSX | FX={} FY={}".format(self._get_fx(), self._get_fy()))
            elif sender is self.ky_input:
                self._update_status("KY → JSY | FX={} FY={}".format(self._get_fx(), self._get_fy()))
        finally:
            self._is_processing = False

    def _on_js_input_changed(self):
        """JSX/JSY 输入变化：始终重算开发者坐标（对应 C# JStextBox_TextChanged，不检查锁）"""
        if self._is_processing:
            return
        sender = self.sender()
        self._is_processing = True
        try:
            self._calc_json_to_dev()
            if sender is self.jsx_input:
                self._update_status("JSX → KX | FX={} FY={}".format(self._get_fx(), self._get_fy()))
            elif sender is self.jsy_input:
                self._update_status("JSY → KY | FX={} FY={}".format(self._get_fx(), self._get_fy()))
        finally:
            self._is_processing = False

    def _on_convert(self):
        """保留批准的按锁定方向换算；拒绝时不显示完成。"""
        previous = self._is_processing
        self._is_processing = True
        try:
            if self.k_lock.isChecked() and self.js_lock.isChecked():
                self._update_status("请至少解锁一类坐标再修改分辨率")
                return False
            ok = self._calc_json_to_dev() if self.js_lock.isChecked() else self._calc_dev_to_json()
            if not ok:
                self._update_status("转换失败：请输入完整的有限数坐标")
                return False
            self._update_status(f"手动转换完成 | FX={self._get_fx()} FY={self._get_fy()}")
            return True
        finally:
            self._is_processing = previous

    def _on_undo(self):
        return self._restore_history_step(-1)

    def _on_redo(self):
        return self._restore_history_step(1)

    def _restore_history_step(self, direction):
        manager = self._backup_mgr
        return self._commit_history_action("undo" if direction < 0 else "redo",
            manager.get_current_index() + direction, manager.get_revision())

    def _invalidate_edit_context(self):
        self._key_input.clear()
        self._key_input._vk = 0
        previous = self._is_processing
        self._is_processing = True
        try:
            for field in (self.kx_input, self.ky_input, self.jsx_input, self.jsy_input):
                field.clear()
        finally:
            self._is_processing = previous

    def _update_undo_redo_states(self):
        """更新撤销/重做按钮状态（与 C# SetUndobtnAndRedobtnState 一致）"""
        can_undo = self._backup_mgr.can_undo
        can_redo = self._backup_mgr.can_redo
        self._undo_btn.setEnabled(can_undo)
        self._redo_btn.setEnabled(can_redo)

    def _atomic_write_json(self, file_path, json_str):
        """原子写入 JSON 文件（LF / 无 BOM / .tmp → replace）。

        格式与原子性的唯一实现在 core/json_editor.write_text_atomic；
        这里只做转发，避免主窗口自己再维护一份容易漏改的副本。

        写盘成功后记录该文件的 mtime（纳秒）作为「自己写入」的基线 ——
        文件监视回调据此区分「自己保存」与「外部程序修改」。
        对应 C# ``WriteToJsonAndBackup`` 里的
        ``lastWriteTime = File.GetLastWriteTimeUtc(filePath)``。
        """
        if not os.path.isfile(file_path):
            self._update_status('方案文件已不存在，请重新加载后再编辑')
            return False
        key = self._canonical_path(file_path)
        accepted = self._accepted_file_versions.get(key)
        if accepted is not None and self._file_version(file_path) != accepted:
            self._update_status("文件已被外部修改，请先重新加载或处理文件变更提示")
            return False
        ok = write_text_atomic(file_path, json_str)
        if not ok:
            self._update_status(f"写回文件失败: {get_last_write_error() or '请检查文件占用、权限和日志'}")
        if ok:
            try:
                self._last_self_write_mtime_ns = os.stat(file_path).st_mtime_ns
                self._accepted_file_versions[self._canonical_path(file_path)] = self._file_version(file_path)
                self._start_file_monitor(file_path)
            except OSError:
                pass
        return ok

    # ── 状态栏 ────────────────────────────────
    def _update_status(self, message):
        if self._status_label is not None:
            self._status_label.setText(message)
        self._p2_refresh_status_badges()

    def _p2_refresh_file_tooltip(self, *_):
        text = self._file_cb.currentText()
        path = self._file_cb.currentData()
        tooltip = f"{text}\n{path}" if path else text
        self._file_cb.setToolTip(tooltip)
        self._file_cb._combo.setToolTip(tooltip)
        QWidget.setToolTip(self._file_cb, tooltip)
        index = self._file_cb.currentIndex()
        if index >= 0 and self._file_cb.itemData(index, Qt.ToolTipRole) != tooltip:
            # 绘制前也会校正静默重建的结果；避免重复写模型引发额外重绘。
            self._file_cb.model().setData(self._file_cb.model().index(index, 0), tooltip, Qt.ToolTipRole)

    def _p2_refresh_status_badges(self, *_):
        if not hasattr(self, "_resolution_badge"):
            return
        self._p2_refresh_file_tooltip()
        fx, fy = self._get_fx(), self._get_fy()
        self._resolution_badge.setText(f"{fx}×{fy}")
        self._fx_fy_label.setText(f"FX {fx} · FY {fy}")
        selected = self._parse_resolution_text(self._res_cb.currentText())
        default = self._parse_resolution_text(f"{self._config.fx}x{self._config.fy}")
        pending = (fx, fy) != default or (selected != (0, 0) and selected != default)
        self._save_res_btn.setText("存默认 *" if pending else "存为默认")
        explanation = ("* 表示当前使用或所选尺寸尚未存为默认；点击存为默认后持久保存。"
                       if pending else "当前使用与所选尺寸已是默认分辨率。")
        self._save_res_btn.setToolTip(explanation)
        self._res_cb.setToolTip(f"{self._res_cb.currentText()}\n{explanation}\n当前 {fx}×{fy}；默认 {default[0]}×{default[1]}")
        if getattr(self, "_p2_count_source", object()) != self._json_str:
            self._p2_count_source = self._json_str
            self._key_count_badge.setText(f"{get_key_count(self._json_str) if self._json_str else 0} 个键")
        set_icon(self._lock_res_btn, self._title_bar._icon_family,
                 "\uE72E" if self._res_locked else "\uE785", "锁" if self._res_locked else "开",
                 "解锁分辨率" if self._res_locked else "锁定分辨率")

    # ── 窗口关闭 ──────────────────────────────
    def closeEvent(self, event):
        self._closing = True
        self._external_change_timer.stop()
        self._index_check_timer.stop()
        if self._index_backup_timer is not None:
            self._index_backup_timer.stop()
            self._index_backup_timer = None
        # 最后一次备份索引
        self._try_index_backup()
        if self._index_executor is not None:
            self._index_executor.shutdown(wait=True)
            self._index_executor = None
        if self._sync_service is not None:
            self._sync_service.dispose()
            self._sync_service = None
        # 关闭所有子窗口
        for key, w in list(self._sub_windows.items()):
            if w is not None:
                try:
                    w.close()
                except Exception:
                    pass
        self._sub_windows.clear()
        if self._kb_handler_instance is not None:
            try:
                self._kb_handler_instance.dispose()
            except Exception:
                pass
        self._config.save()
        self._update_status("配置已保存")
        super().closeEvent(event)

    def _build_statusbar(self):
        sb = QStatusBar()
        sb.setSizeGripEnabled(False)
        sb.setStyleSheet(theme.STATUS_QSS)
        dot = QLabel("●")
        dot.setStyleSheet(f"QLabel {{color:{theme.MINT};font-size:11px;}}")
        sb.addWidget(dot)
        self._status_label = ElidedStatusLabel("就绪")
        sb.addWidget(self._status_label, 1)
        self._resolution_badge = QLabel(f"{self._get_fx()}×{self._get_fy()}")
        self._key_count_badge = QLabel("0 个键")
        for badge in (self._resolution_badge, self._key_count_badge):
            badge.setStyleSheet(f'QLabel {{background:{theme.CARD};border:1px solid {theme.LINE};border-radius:7px;padding:0 6px;font-size:11px;}}')
            sb.addPermanentWidget(badge)
        self.setStatusBar(sb)

    # ── 背景立绘（中央容器底层，不覆盖标题栏和状态栏）────
    def _add_bg(self, parent):
        self.bg = HinaAside(parent)
        self.bg.linkOpenFailed.connect(lambda url: self._update_status(f"无法打开作者链接，可复制地址：{url}"))
        self._hina_aside = self.bg
        self._body_row.addWidget(self.bg)

    @staticmethod
    def _bg_image_candidates():
        """立绘搜索顺序：源码 ui/assets → 打包资源目录。
        保证 .py 直跑 与 PyInstaller exe 两种运行方式表现一致。"""
        names = ["hina_bg.png", "hina-pajama.png", "睡衣日奈立绘.png"]
        roots = []

        # 1) 源码运行：DS版根目录下的 ui/assets
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        roots.append(os.path.join(base, "ui", "assets"))

        # 2) 打包运行：PyInstaller 解包目录 / exe 同级目录
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            roots.append(os.path.join(meipass, "ui", "assets"))
        if getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(sys.executable)
            roots.append(os.path.join(exe_dir, "ui", "assets"))
            roots.append(os.path.join(exe_dir, "_internal", "ui", "assets"))

        return [os.path.join(r, n) for r in roots for n in names]

    def _show_sub(self, key, factory, post_init=None, *, activate=True):
        created = key not in self._sub_windows or self._sub_windows[key] is None
        if created:
            sub = factory()
            sub.setParent(self, sub.windowFlags())
            self._sub_windows[key] = sub
        sub = self._sub_windows[key]
        if hasattr(sub, 'set_overwrite_guard'):
            sub.set_overwrite_guard(lambda original, blocks: self._confirm_key_overwrite(sub, original, blocks),
                                    self._overwrite_coordinator.get_epoch)
        if post_init:
            post_init(sub)
        hidden = (os.environ.get("QT_QPA_PLATFORM") == "offscreen"
                  or self.testAttribute(Qt.WA_DontShowOnScreen))
        if hidden:
            sub.setAttribute(Qt.WA_DontShowOnScreen, True)
        elif activate and sub.isMinimized():
            # 保留最大化等其他状态；隐藏验证不触发桌面激活。
            sub.setWindowState(sub.windowState() & ~Qt.WindowMinimized)
        position_subwindow(self, sub, first_open=created)
        sub.show()
        if not hidden and activate:
            sub.raise_()
            sub.activateWindow()

    def _open_adb_touch(self):
        from .adb_touch_form import AdbTouchForm
        self._show_sub(
            "adb", AdbTouchForm,
            post_init=lambda f: self._wire_adb_touch_form(f),
        )

    def _wire_adb_touch_form(self, form) -> None:
        """桥接 AdbTouchForm 到主窗口：分辨率回调 + JSON 读写 + 坐标回填 + 配置持久化 + 键盘监听。"""
        form.set_on_resolution_detected(self._on_adb_resolution_detected)
        form.set_main_window_bridge(
            get_json=lambda: self._json_str,
            write_json=self._on_adb_write_json,
        )
        form.set_apply_coord_callback(self.set_jsxy)
        form.set_config_getter(lambda: self._config)
        form.set_key_type_getter(self.get_current_key_type)
        form.set_key_type_setter(self.set_current_key_type)
        # 对接共享全局键盘监听（对应 C#：AdbTouchForm 接收主窗体 _keyboardHandler）
        form.set_keyboard_handler(self._kb_handler)

    def _on_global_key_captured(self, key_text: str, vk: int, continuous: bool, epoch=None) -> bool:
        coordinator = self._overwrite_coordinator
        if coordinator.active or (epoch is not None and epoch != coordinator.get_epoch()):
            return False
        if not self._json_str or not self._json_path:
            self._update_status("请先加载 JSON 文件后再生成按键")
            return False
        for key in ("adb", "qt"):
            form = self._sub_windows.get(key)
            if form is not None and form.isVisible():
                return False
        ok_x, x = try_parse_float(self.jsx_input.text())
        ok_y, y = try_parse_float(self.jsy_input.text())
        if not (ok_x and ok_y) or not key_text:
            self._update_status("请输入完整的有限数 JSX/JSY 坐标")
            return False
        original = self._json_str
        handler = self._kb_handler_instance
        mode_revision = handler.get_mode_revision() if handler else None
        succeeded = False
        try:
            value = create_key(self._key_type_to_json_type(), key_text.upper(), str(x), str(y),
                               str(get_scan_code(vk) if vk else 0), str(vk))
            if value is None or not self._confirm_key_overwrite(self, original, value):
                return False
            updated = write_keys(value, original)
            if updated is None or not self.try_set_mumu_json_and_save(updated, CREATE_KEY, "快捷键创建按键"):
                return False
            self._on_file_loaded()
            self._update_status(f"已创建按键 [{key_text.upper()}]")
            succeeded = True
            return True
        finally:
            if (not succeeded and not continuous and handler is not None
                    and mode_revision == handler.get_mode_revision()):
                handler.set_listen_once_mode(True)

    def _confirm_key_overwrite(self, parent, original, blocks, force_name=None):
        if original != self._json_str or not self._json_path:
            return False
        path, version = self._json_path, self._file_version(self._json_path)
        listeners = [self._kb_handler_instance]
        quick = self._sub_windows.get("qt")
        if quick is not None:
            listeners.append(quick._keyboard_listener)
        return self._overwrite_coordinator.confirm(parent, original, blocks, listeners,
            validate=lambda: self._json_str == original and self._json_path == path and self._file_version(path) == version,
            force_name=force_name)

    def _on_adb_apply_coord(self, screen_x: float, screen_y: float) -> bool:
        if not (math.isfinite(screen_x) and math.isfinite(screen_y)):
            return False
        return self.set_jsxy(f"{adb_px_to_rel(screen_x, self._get_fx()):.16f}",
                             f"{adb_px_to_rel(screen_y, self._get_fy()):.16f}")

    def _on_adb_resolution_detected(self, w: int, h: int) -> None:
        if self._apply_current_resolution(w, h):
            self._sync_resolution_controls()
            self._update_status(f"ADB 分辨率同步: FX={w} FY={h}")

    def get_mumu_json(self) -> Optional[str]:
        """获取当前 JSON 字符串（线程安全）"""
        return self._json_str

    def set_mumu_json(self, json_str: str) -> None:
        """设置 JSON 字符串"""
        self._json_str = json_str

    def try_set_mumu_json_and_save(self, json_str: str, op_type: str = "修改", desc: str = "") -> bool:
        """候选内容先落盘，成功后才提交内存、历史和索引任务。"""
        if not json_str or not self._json_path:
            self._update_status("未选择有效的方案文件")
            return False
        try:
            if not isinstance(json.loads(json_str), dict):
                raise ValueError("方案 JSON 必须为对象")
            if json_str == self._json_str:
                return True
            if not self._atomic_write_json(self._json_path, json_str):
                self._update_status(f"写回 JSON 失败，原方案未改变: {get_last_write_error() or '请检查文件占用、权限和日志'}")
                return False
        except Exception as ex:
            LogService.error_ex("MainWindow", ex, "写回 JSON 失败")
            self._update_status(f"写回 JSON 失败: {ex}")
            return False
        self._json_str = json_str
        self._backup_mgr.record_change(json_str, op_type, desc, get_key_count(json_str))
        self._needs_index_backup = True
        self._schedule_index_damage_check()
        self._update_undo_redo_states()
        return True

    def set_mumu_json_and_save(self, json_str: str, op_type: str = "修改", desc: str = "") -> bool:
        """兼容入口，返回实际提交结果。"""
        return self.try_set_mumu_json_and_save(json_str, op_type, desc)

    def save_json_and_backup(self) -> None:
        """触发写回+备份（原子写）"""
        if self._json_str and self._json_path:
            if self._atomic_write_json(self._json_path, self._json_str):
                self._needs_index_backup = True
                self._schedule_index_damage_check()
            else:
                LogService.error_ex("MainWindow", Exception("原子写入失败"), "保存 JSON 失败")

    def get_json_file_path(self) -> Optional[str]:
        """获取当前 JSON 文件路径"""
        return self._json_path

    def set_jsxy(self, rel_x: str, rel_y: str) -> bool:
        """成对回填，避免文本信号将原坐标取整后再次反算。"""
        ok_x, x = try_parse_float(rel_x)
        ok_y, y = try_parse_float(rel_y)
        if not (ok_x and ok_y):
            return False
        previous = self._is_processing
        self._is_processing = True
        try:
            self.jsx_input.setText(str(rel_x))
            self.jsy_input.setText(str(rel_y))
            return self._calc_json_to_dev()
        finally:
            self._is_processing = previous

    def _on_adb_write_json(self, json_str: str) -> bool:
        """ADB 触控采集写入按键 JSON。"""
        old = json.loads(self._json_str or '{}').get('keymaps', [])
        new = json.loads(json_str).get('keymaps', [])
        count = sum(block not in old for block in new)
        if not self.try_set_mumu_json_and_save(json_str, ADB_GENERATE, f"ADB生成 {count} 个按键"):
            return False
        key_count = get_key_count(self._json_str)
        self._update_status(f"ADB 按键已生成 | 按键数: {key_count}")
        self._on_file_loaded()
        return True
    def _open_quick_touch(self):
        from .quick_touch_form import QuickTouchForm
        def sync(value):
            if not self.try_set_mumu_json_and_save(value, CREATE_KEY, "全屏快速摸点"):
                return False
            self._on_file_loaded()
            self._update_status("全屏摸点已更新")
            return True
        def post(form):
            form.set_json_data(self._json_str or "")
            form.set_json_path(self._json_path or "")
            form.set_sync_callback(sync)
            form.set_resolution_getter(lambda: (self._get_fx(), self._get_fy()))
            # 首次跟随主类型，已打开窗口的显式本地选择继续保留。
            if getattr(form, "_key_type_getter", None) is None:
                form.set_key_type_getter(self.get_current_key_type)
            form.set_apply_coord_callback(self._on_quick_apply_coord)
        self._show_sub("qt", QuickTouchForm, post_init=post)

    def _on_quick_apply_coord(self, x, y):
        ok_x, value_x = try_parse_float(x)
        ok_y, value_y = try_parse_float(y)
        if not (ok_x and ok_y):
            return False
        return self.set_jsxy(f"{px_to_rel(value_x, self._get_fx()):.16f}",
                             f"{px_to_rel(value_y, self._get_fy()):.16f}")

    def _open_range_clear(self):
        from .range_clear_form import RangeClearForm
        def sync(value):
            if not self.try_set_mumu_json_and_save(value, RANGE_CLEAR, "区域清空"):
                return False
            self._on_file_loaded()
            self._update_status("区域清空完成")
            return True
        def post(form):
            form.set_json_data(self._json_str or "")
            form.set_json_path(self._json_path or "")
            form.set_sync_callback(sync)
            form.set_resolution(self._get_fx(), self._get_fy())
            form.set_resolution_getter(lambda: (self._get_fx(), self._get_fy()))
        self._show_sub("rc", RangeClearForm, post_init=post)

    def _open_key_preset(self):
        from .key_preset_form import KeyPresetForm
        def _sync(json_str):
            if not self.try_set_mumu_json_and_save(json_str, PRESET_INJECT, "键位预设操作"):
                return False
            self._update_status("键位预设已更新")
            self._on_file_loaded()
            return True
        self._show_sub("kp", KeyPresetForm,
            post_init=lambda s: (
                s.set_json_data(self._json_str or ""),
                s.set_json_path(self._json_path or ""),
                s.set_sync_callback(_sync),
                s.set_apply_coord_callback(self.set_jsxy),
                None
            )[-1])
    def _open_mouse_track(self):
        from .mouse_track_form import MouseTrackForm
        self._show_sub("mt", MouseTrackForm,
                       post_init=lambda s: s.set_status_callback(self._update_status))
    def _open_wide_screen(self):
        from .wide_screen_form import WideScreenForm
        def write(value):
            if not self.try_set_mumu_json_and_save(value, WIDE_SCREEN, "宽屏坐标转换"):
                return False
            self._on_file_loaded()
            self._update_status("宽屏坐标转换已更新")
            return True
        def post(form):
            form.set_json_data(self._json_str or "")
            form.set_json_path(self._json_path or "")
            form.set_write_callback(write)
            form.set_resolution_getter(lambda: (self._get_fx(), self._get_fy()))
        self._show_sub("ws", WideScreenForm, post_init=post)

    def _open_index_backup(self):
        from .index_backup_form import IndexBackupForm
        self._start_index_backup_timer()
        def post(form):
            form.set_status_callback(self._update_status)
            form.set_index_directory(self._config.json_folder_path)
            preferred = os.path.join(self._config.json_folder_path, self._get_package_prefix().rstrip('-') + ".json")
            if os.path.isfile(preferred):
                form.set_current_index_file(os.path.basename(preferred))
            elif self._json_path and self._index_backup_mgr is not None:
                path = self._index_backup_mgr.resolve_index_file_path(self._json_path)
                if path:
                    form.set_current_index_file(os.path.basename(path))
        self._show_sub("ib", IndexBackupForm, post_init=post)

    def _open_log_viewer(self):
        from .log_viewer_form import LogViewerForm
        self._show_sub("lv", LogViewerForm)
    def _open_undo_history(self):
        from .undo_history_form import UndoHistoryForm
        def post(form):
            form.set_backup_manager(self._backup_mgr)
            form.set_history_action_callback(self._commit_history_action)
            form.set_keyboard_handler(self._kb_handler)
        self._show_sub("uh", UndoHistoryForm, post_init=post)

    def _commit_history_action(self, action, index, revision):
        manager = self._backup_mgr
        if revision != manager.get_revision():
            self._update_status("历史已变化，请重新选择")
            return False
        expected = manager.get_current_index() + (-1 if action == "undo" else 1)
        if action in ("undo", "redo") and index != expected:
            return False
        value = manager.peek_snapshot(index)
        if value is None or not self._json_path:
            return False
        if not self._atomic_write_json(self._json_path, value):
            self._update_status("写回文件失败，历史位置未改变")
            return False
        self._json_str = value
        if action == "undo":
            manager.undo()
        elif action == "redo":
            manager.redo()
        else:
            manager.jump_to(index)
        self._schedule_index_damage_check()
        self._update_undo_redo_states()
        self._on_file_loaded()
        self._invalidate_edit_context()
        self._update_status("已撤销" if action == "undo" else "已重做" if action == "redo" else "快照已恢复")
        return True

    @staticmethod
    def _find_mumu_keymap_dir():
        """查找 MuMu 标准键位目录，返回第一个存在的路径；都没有返回 None。

        C# 的 ``TryGetJsonFileFolder`` 只试了 ``%USERPROFILE%\\AppData\\Roaming\\Netease\\
        MuMuPlayer\\data\\keymapConfig`` 一条（代码里第二段 altPath 与第一段**完全相同**，
        是死代码）。这里额外补一条 ``%LOCALAPPDATA%`` 路径 —— MuMu 部分安装方式会把
        数据放这儿，多试一条更稳。
        """
        candidates = [
            os.path.join(os.environ.get("USERPROFILE", ""),
                         "AppData", "Roaming", "Netease", "MuMuPlayer", "data", "keymapConfig"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""),
                         "MuMuPlayer", "data", "keymapConfig"),
        ]
        for p in candidates:
            if os.path.isdir(p):
                return p
        return None

    def _apply_keymap_dir(self, path: str) -> None:
        """把检测到的目录写入配置并立即生效（刷新列表 + 加载 + 启动同步）。"""
        self._config.json_folder_path = path
        self._refresh_file_list()
        self._load_json_file()
        # 启动自动同步（与 C# 一致：目录存在时即启动）
        self._try_auto_sync()
        self._try_auto_sync_from_emulator_keymap()

    def _auto_detect_keymap_dir(self):
        """启动时：如果 json_folder_path 为空，自动检测 MuMu 模拟器 keymapConfig 目录"""
        if self._config.json_folder_path and os.path.isdir(self._config.json_folder_path):
            self._refresh_file_list()
            self._load_json_file()
            # 启动自动同步（与 C# 一致：目录存在时即启动）
            self._try_auto_sync()
            self._try_auto_sync_from_emulator_keymap()
            return
        found = self._find_mumu_keymap_dir()
        if found:
            self._update_status(f"自动检测到键位目录: {found}")
            self._apply_keymap_dir(found)   # 注意：状态先设，随后的加载消息会覆盖它（保持原行为）
            return
        self._update_status("未检测到 MuMu 键位目录，请手动设置")

    def _on_get_path_clicked(self):
        """「获取路径」按钮 — 对应 C# ``TryGetJsonFileFolderbutton_Click``。

        与启动时的自动检测差别：**无条件重新检测**（不管当前路径是否已设置），
        用于目录被改乱后一键恢复。

        与 C# 的两点差异（都是补强，非行为退化）：
        1. C# 找到后会刷新目录，但**找不到时完全静默**；这里会明确提示。
        2. C# 只写配置不刷新文件列表；这里顺带刷新并加载，让用户立刻看到效果。
        """
        found = self._find_mumu_keymap_dir()
        if not found:
            MessageDialog.warning(
                self, "提示",
                "未检测到 MuMu 键位目录，请关闭模拟器后确认已安装 MuMu 模拟器，"
                "或点「浏览」手动选择。"
            )
            return
        self._apply_keymap_dir(found)
        self._update_status("已自动获取MuMu键位配置目录")   # 与 C# 文案一致

    # ── 包名→前缀映射（实现在 core/package_names.py）──────────
    def _get_package_prefix(self):
        return package_prefix(self._package_cb.currentText(), OTHER_PREFIX)

    def _match_package_from_path(self, file_path):
        """根据文件路径中的包名前缀反推包名（与 C# MatchPackageFromPath 一致）"""
        return package_name_from_path(file_path)

    # ── JSON 文件加载 ──────────────────────────
    def _load_json_file(self):
        path = self._file_cb.currentData()
        if not path:
            self._update_status("当前包名下没有键位文件：请切换包名或检查键位目录")
            return False
        return self._load_scheme_path(path)

    @staticmethod
    def _canonical_path(path):
        return os.path.normcase(os.path.abspath(path)) if path else ""

    @staticmethod
    def _file_version(path):
        try:
            stat = os.stat(path)
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def _restore_committed_selection(self):
        package = self._match_package_from_path(self._json_path or "")
        previous = self._package_cb.blockSignals(True)
        try:
            if package:
                self._package_cb.setCurrentText(package)
        finally:
            self._package_cb.blockSignals(previous)
        self._refresh_file_list()

    def _load_scheme_path(self, path, confirm=True, manual=False):
        """路径、内容、历史和控件作为一次加载提交。"""
        path = os.path.abspath(path)

        def read_current():
            before = self._file_version(path)
            content, parsed = read_json_file(path)
            version = self._file_version(path)
            if before is None or version != before:
                raise ValueError("读取期间文件发生变化，请重试")
            if not isinstance(parsed, dict):
                raise ValueError("键位文件根节点必须为对象")
            return content, parsed, version

        try:
            content, parsed, loaded_version = read_current()
        except Exception as ex:
            self._restore_committed_selection()
            self._update_status(f"加载失败: {ex}")
            return False
        same = self._canonical_path(path) == self._canonical_path(self._json_path)
        if same and content == self._json_str:
            self._accepted_file_versions[self._canonical_path(path)] = loaded_version
            self._restore_committed_selection()
            if manual and not self._is_auto_sync:
                self._auto_sync_cb.setChecked(False)
            return True
        if confirm and not self._check_unsaved_changes():
            self._restore_committed_selection()
            return False
        if confirm:
            # 确认窗口可能停留很久，提交前重新读取，不能把旧内容配上新的磁盘版本。
            try:
                content, parsed, loaded_version = read_current()
            except Exception as ex:
                self._restore_committed_selection()
                self._update_status(f"加载失败: {ex}")
                return False
        self._try_index_backup()
        self._json_path, self._json_str = path, content
        self._external_change_timer.stop()
        self._pending_external_path = None
        self._accepted_file_versions[self._canonical_path(path)] = loaded_version
        self._last_self_write_mtime_ns = None
        self._pending_check_scheme_path = None
        self._index_check_timer.stop()
        self._config.json_folder_path = os.path.dirname(path)
        self._backup_mgr.record_initial(content, get_key_count_from_obj(parsed))
        self._restore_committed_selection()
        self._update_undo_redo_states()
        self._on_file_loaded()
        self._start_index_backup_timer()
        self._schedule_index_damage_check()
        self._start_file_monitor(path)
        if manual and not self._is_auto_sync:
            self._auto_sync_cb.setChecked(False)
        self._try_auto_sync()
        self._update_status(f"已加载: {os.path.basename(path)} | 按键数: {get_key_count_from_obj(parsed)}")
        return True

    def _on_file_loaded(self):
        """JSON 文件加载后刷新所有依赖它的子窗体"""
        if self._json_str is None:
            return
        # 刷新已打开的子窗体
        for key in ["rc", "kp", "ws", "qt"]:
            if key in self._sub_windows and self._sub_windows[key] is not None:
                sub = self._sub_windows[key]
                if hasattr(sub, "set_json_data"):
                    sub.set_json_data(self._json_str)
                if hasattr(sub, "set_json_path"):
                    sub.set_json_path(self._json_path or "")

    def _on_file_cb_user_selected(self, index):
        path = self._file_cb.itemData(index)
        if path:
            self._load_scheme_path(path, manual=True)

    def _on_package_changed(self):
        if self._package_cb.currentText() == "宇宙服":
            MessageDialog.information(self, "提示", "绿玩哪有宇宙服，你清醒一点。")
            self._restore_committed_selection()
            return
        self._refresh_file_list()
        path = self._file_cb.currentData()
        if path:
            self._load_scheme_path(path, manual=True)
        elif self._json_path:
            self._restore_committed_selection()

    def _refresh_file_list(self):
        """展开扫描只刷新候选；选择以已提交路径为准，不触发加载。"""
        folder = self._config.json_folder_path
        items = []
        if folder and os.path.isdir(folder):
            try:
                names = sorted(f for f in os.listdir(folder) if f.lower().endswith(".json"))
                items = [(display, os.path.join(folder, name))
                         for display, name in filter_keymap_files(names, self._get_package_prefix(), only_json=True)]
            except OSError as ex:
                rebuild_combo(self._file_cb, [])
                self._update_status(f"读取文件目录失败: {ex}")
                return
        else:
            self._update_status("键位目录不存在，请重新选择目录")
        rebuild_combo(self._file_cb, items, restore_data=self._json_path)

    def _browse_json_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 JSON 键位文件", self._config.json_folder_path,
                                            "JSON 文件 (*.json);;所有文件 (*)")
        if path:
            self._load_scheme_path(path, manual=True)

    def _open_json_dir(self):
        """用资源管理器打开 JSON 文件所在目录，若已加载文件则选中"""
        from utils.open_folder import open_folder
        try:
            if self._json_path and os.path.isfile(self._json_path):
                open_folder(self._json_path, select=True)
            elif self._config.json_folder_path and os.path.isdir(self._config.json_folder_path):
                open_folder(self._config.json_folder_path)
            else:
                MessageDialog.warning(self, "提示", "文件夹不存在！")
        except Exception as ex:
            MessageDialog.warning(self, "提示", f"无法打开文件夹：\n{ex}")

    # ── 分辨率按钮 ─────────────────────────────
    def _toggle_res_lock(self):
        """切换分辨率锁定状态"""
        self._res_locked = not self._res_locked
        self._res_cb.setEnabled(not self._res_locked)
        self._lock_res_btn.setText("解锁" if self._res_locked else "锁定")
        self._update_status("分辨率已锁定" if self._res_locked else "分辨率已解锁")

    def _on_resolution_type_changed(self):
        self._rebuild_resolution_choices(apply=True)

    def _rebuild_resolution_choices(self, apply=False):
        res_type = self._ratio_cb.currentText()
        previous = self._res_cb.blockSignals(True)
        try:
            self._res_cb.clear()
            custom = res_type == "自定义"
            self._res_cb.setEditable(custom)
            self._delete_res_btn.setVisible(custom)
            if custom:
                labels = ["1280×720"] + [k.replace("x", "×") for k in self._custom_resolutions]
            else:
                labels = list(self._RESOLUTION_PRESETS.get(res_type, {}))
            self._res_cb.addItems(list(dict.fromkeys(labels)))
            current = f"{self._get_fx()}×{self._get_fy()}"
            index = self._res_cb.findText(current)
            if index >= 0:
                self._res_cb.setCurrentIndex(index)
            elif custom:
                self._res_cb.setCurrentText(current)
            elif self._res_cb.count():
                self._res_cb.setCurrentIndex(0)
        finally:
            self._res_cb.blockSignals(previous)
        if apply:
            self._apply_resolution_from_combobox()

    def _sync_resolution_controls(self):
        previous = self._ratio_cb.blockSignals(True)
        try:
            self._ratio_cb.setCurrentText(self._classify_resolution(f"{self._get_fx()}x{self._get_fy()}"))
        finally:
            self._ratio_cb.blockSignals(previous)
        self._rebuild_resolution_choices(apply=False)

    def _apply_current_resolution(self, width, height):
        if not (isinstance(width, int) and isinstance(height, int) and width > 1 and height > 1):
            self._update_status("分辨率宽和高必须为大于1的整数")
            return False
        self._current_fx, self._current_fy = str(width), str(height)
        self._on_convert()
        return True

    def _apply_resolution_from_combobox(self):
        width, height = self._parse_resolution_text(self._res_cb.currentText())
        return self._apply_current_resolution(width, height)

    @staticmethod
    def _parse_resolution_text(text):
        """解析 '1280×720' 或 '1280x720' 为 (w, h)，失败返回 (0, 0)"""
        for sep in ("×", "x", "X"):
            if sep in text:
                parts = text.split(sep)
                if len(parts) == 2:
                    try:
                        w = int(parts[0].strip())
                        h = int(parts[1].strip())
                        if w > 1 and h > 1:
                            return (w, h)
                    except ValueError:
                        pass
        return (0, 0)

    def _on_resolution_changed(self):
        if self._res_locked:
            return
        self._apply_resolution_from_combobox()

    def _on_custom_res_input_changed(self, text):
        """自定义模式下用户输入分辨率时实时校验格式"""
        if self._ratio_cb.currentText() != "自定义":
            return
        # 不做实时拦截，仅在保存时校验
        pass

    def _save_resolution(self):
        width, height = self._parse_resolution_text(self._res_cb.currentText())
        if not width or not height:
            MessageDialog.warning(self, "无效分辨率", "请输入宽和高均大于1的整数，如 1280×720")
            return False
        key = f"{width}x{height}"
        if self._ratio_cb.currentText() == "自定义" and key != "1280x720":
            self._custom_resolutions[key] = f"{width},{height}"
        with self._config.batch():
            self._config.fx, self._config.fy = str(width), str(height)
            self._save_custom_resolutions()
        if not self._config.save():
            self._update_status("默认分辨率保存失败，请检查日志")
            return False
        self._apply_current_resolution(width, height)
        self._sync_resolution_controls()
        self._update_status(f"分辨率已保存为默认: {width}×{height}")
        return True

    def _read_resolution(self):
        if not self._config.reload():
            self._update_status("默认分辨率读取失败，当前尺寸未改变")
            return False
        width, height = self._parse_resolution_text(f"{self._config.fx}x{self._config.fy}")
        if not width or not height:
            self._update_status("默认分辨率无效，当前尺寸未改变")
            return False
        self._load_custom_resolutions()
        self._apply_current_resolution(width, height)
        self._sync_resolution_controls()
        self._update_status(f"分辨率已读取: FX={width} FY={height}")
        return True

    def _classify_resolution(self, key):
        """判断分辨率属于哪种类型"""
        for rtype, presets in MainWindow._RESOLUTION_PRESETS.items():
            for display, (w, h) in presets.items():
                if f"{w}x{h}" == key:
                    return rtype
        return "自定义"

    def _delete_resolution(self):
        """删除自定义分辨率（仅自定义模式，不可删默认 1280×720）"""
        if self._ratio_cb.currentText() != "自定义":
            return
        text = self._res_cb.currentText()
        if text == "1280×720":
            MessageDialog.information(self, "提示", "不能删除默认预设分辨率")
            return
        key = text.replace("×", "x")
        if key in self._custom_resolutions:
            del self._custom_resolutions[key]
            self._save_custom_resolutions()
            self._on_resolution_type_changed()
            self._update_status(f"已删除自定义分辨率: {text}")

    def _load_custom_resolutions(self):
        """从 ConfigManager.Resolution4String 加载自定义分辨率（V 分隔，与 C# 一致）"""
        raw = self._config.resolution4_string
        self._custom_resolutions = {}
        if raw:
            try:
                for entry in raw.split("V"):
                    entry = entry.strip()
                    if not entry:
                        continue
                    items = entry.split(",")
                    if len(items) >= 3:
                        k = items[0].strip().lstrip("*")
                        self._custom_resolutions[k] = f"{items[1].strip()},{items[2].strip()}"
            except Exception:
                self._custom_resolutions = {}

    def _save_custom_resolutions(self):
        """保存自定义分辨率到 ConfigManager（V 分隔，与 C# 一致）"""
        parts = []
        for k, v in self._custom_resolutions.items():
            vals = v.split(",")
            if len(vals) < 2:
                continue
            parts.append(f"*{k},{vals[0]},{vals[1]}")
        self._config.resolution4_string = "V".join(parts) + ("V" if parts else "")
        self._config.save()

    # ── 按键编辑业务逻辑 ────────────────────────
    @staticmethod
    def _type_display_name(key_type: str) -> str:
        """将 JSON 类型常量转为 C# 风格的详细说明"""
        if key_type == TYPE_CLICK:
            return "单击按键"
        elif key_type == TYPE_MACRO:
            return "宏指牌按键"
        elif key_type == TYPE_BUNCH_CLICK:
            return "连击按键"
        return key_type or "未知类型"

    def _check_key(self):
        if not self._json_str:
            self._update_status("请先加载 JSON 文件")
            return
        text, vk = self._key_input.text().strip(), self._key_input.vk
        if not text:
            self._update_status("请在按键框中按下一个键")
            return
        kind = (find_type_by_virtual_key(self._json_str, vk) if vk > 0
                else find_type_by_text(self._json_str, text))
        if kind:
            self._update_status(key_exists(text, f"类型: {self._type_display_name(kind)}"))
            MessageDialog.information(self, "检查按键", f"按键 [{text}] 在文件中，类型为 {self._type_display_name(kind)}。")
        else:
            self._update_status(f"按键 [{text}] 不存在")
            MessageDialog.information(self, "检查按键", f"当前方案未找到按键 [{text}]")

    def _read_key_coord(self):
        if not self._json_str:
            self._update_status("请先加载 JSON 文件")
            return False
        text, vk = self._key_input.text().strip(), self._key_input.vk
        coords = try_read_key_coordinates(self._json_str, vk=vk if vk > 0 else None, key_text=text)
        if coords is None:
            self._update_status("查找坐标失败：按键不存在或没有可读取的坐标")
            return False
        self.set_jsxy(*coords)
        self._update_status(f"已读取按键 [{text}] 坐标: JSX={coords[0]} JSY={coords[1]}")
        return True

    def _key_type_to_json_type(self):
        """将 UI 类型文本映射到 JSON 类型常量（仅 Click / Macro 两种）"""
        t = self._key_type_cb.currentText()
        if "宏" in t:
            return TYPE_MACRO
        return TYPE_CLICK

    def get_current_key_type(self):
        """获取当前按键类型（Click/Macro），供 ADB 面板同步"""
        return self._key_type_to_json_type()

    def set_current_key_type(self, key_type: str) -> None:
        text = key_type_display_name(key_type)
        index = self._key_type_cb.findText(text)
        if index >= 0:
            self._key_type_cb.setCurrentIndex(index)

    def _on_main_key_type_changed(self):
        form = self._sub_windows.get("adb")
        if form is not None:
            form.set_key_type_getter(self.get_current_key_type)

    def _modify_save_key(self):
        if not self._json_str or not self._json_path:
            self._update_status("请先加载 JSON 文件")
            return False
        text, vk = self._key_input.text().strip(), self._key_input.vk
        if not text:
            self._update_status("请在按键框中按下一个键")
            return False
        ok_x, x = try_parse_float(self.jsx_input.text())
        ok_y, y = try_parse_float(self.jsy_input.text())
        if not (ok_x and ok_y):
            self._update_status("请输入完整的有限数 JSX/JSY 坐标")
            return False
        kind = (find_type_by_virtual_key(self._json_str, vk) if vk > 0
                else find_type_by_text(self._json_str, text))
        force = self._force_replace_cb.isChecked()
        if kind and not force:
            if kind not in (TYPE_CLICK, TYPE_MACRO, TYPE_BUNCH_CLICK):
                self._update_status("该类型不支持修改坐标，请手动编辑 JSON")
                return False
            if try_read_key_coordinates(self._json_str, vk=vk if vk > 0 else None, key_text=text) is None:
                self._update_status("无法修改：宏中没有有效的 curve_rel:mouse 坐标，或坐标字段缺失")
                return False
        if kind and not force:
            updated = (re_key_by_virtual_key(self._json_str, vk, str(x), str(y)) if vk > 0
                       else re_key_by_text(self._json_str, text, str(x), str(y)))
            op_type, description = MODIFY_KEY, f"修改按键 {text}"
        else:
            candidate = self._json_str
            if force and kind:
                blocks = json.loads(candidate).get("keymaps", [])
                indexes = [i for i, item in enumerate(blocks) if isinstance(item, dict)
                           and isinstance(item.get("key"), dict)
                           and (item["key"].get("virtual_key") == vk if vk > 0 else item["key"].get("text") == text)]
                candidate = delete_keys_by_indexes(candidate, indexes)
            block = create_key(self._key_type_to_json_type(), text, str(x), str(y), str(get_scan_code(vk) if vk else 0), str(vk))
            updated = write_keys(block, candidate) if block else None
            op_type, description = CREATE_KEY, f"{'强制替换' if force else '创建'}按键 {text}"
        if updated is None:
            self._update_status("修改失败：未生成有效的候选内容")
            return False
        if not self._confirm_key_overwrite(self, self._json_str, json.loads(updated).get("keymaps", []),
                                          force_name=text if kind and vk <= 0 else None):
            return False
        if not self.try_set_mumu_json_and_save(updated, op_type, description):
            return False
        self.set_jsxy(str(x), str(y))
        self._on_file_loaded()
        self._update_status(f"{description} 完成 | JSX={x:.16g} JSY={y:.16g}")
        return True

    def _on_auto_sync_toggled(self, checked: bool):
        """自动同步勾选框切换（与 C# autoSyncCheckBox.CheckedChanged 一致）"""
        if checked:
            folder = self._config.json_folder_path
            if not folder or not os.path.isdir(folder):
                # 没有有效目录时同步服务根本起不来，必须明说，否则用户
                # 「勾选了却看不出有没有生效」
                self._update_status("自动同步无法开启：键位目录无效，请先点「获取路径」或「浏览」")
                return
            self._try_auto_sync()
            before = self._json_path
            self._try_auto_sync_from_emulator_keymap()
            if self._json_path == before:
                # 方案未变也要给回执，否则看起来像没生效
                self._update_status(f"自动同步已开启：监控 {folder}（当前方案已是模拟器方案）")
            else:
                # 自动同步会跟随模拟器当前方案，手动选的文件会被切回去 —— 必须说明
                self._update_status(
                    f"自动同步已开启：已跟随模拟器切换为 {os.path.basename(str(self._json_path))}")
        elif self._sync_service is not None:
            self._sync_service.disable()
            self._update_status("自动同步已关闭")

    def _try_auto_sync(self):
        """尝试启动 MuMu 键位自动同步服务（监控根目录，扁平结构）"""
        if not self._auto_sync_cb.isChecked():
            return
        folder = self._config.json_folder_path
        if not folder or not os.path.isdir(folder):
            return

        if self._sync_service is None:
            from infra.mumu_sync import MuMuKeymapSyncService  # 延迟导入（watchdog 较重）
            self._sync_service = MuMuKeymapSyncService()
            # 用 pyqtSignal 桥把后台线程(watchdog/轮询)事件转发到主线程：
            # emit auto-queued 到主线程，槽必然在主线程操作 Qt 控件（线程安全）
            self._sync_service.on_keymap_changed(_SHARED_SIGNALS.sync_path_changed.emit)

        self._sync_service.start(folder)
        self._sync_service.enable()

    def _try_auto_sync_from_emulator_keymap(self):
        """主动扫描 index.json，找到模拟器当前使用的方案并同步（与 C# TryAutoSyncFromEmulatorKeymap 一致）"""
        if not self._auto_sync_cb.isChecked():
            return
        folder = self._config.json_folder_path
        if not folder or not os.path.isdir(folder):
            return

        best_path = None
        best_time = 0

        from infra.mumu_sync import MuMuKeymapSyncService  # 延迟导入（watchdog 较重）
        for pkg_name in REAL_PACKAGE_NAMES:
            prefix = package_prefix(pkg_name, "")
            pkg_stripped = prefix.rstrip("-")
            index_path = os.path.join(folder, pkg_stripped + ".json")
            if not os.path.isfile(index_path):
                continue
            current_path = MuMuKeymapSyncService.read_current_path(index_path)
            if not current_path or not os.path.isfile(current_path):
                continue
            try:
                mtime = os.path.getmtime(index_path)
            except OSError:
                continue
            if mtime > best_time:
                best_time = mtime
                best_path = current_path

        if best_path and (not self._json_path or
                          os.path.normpath(best_path).lower() != os.path.normpath(self._json_path).lower()):
            self._update_status("自动同步: 检测到模拟器方案变更")
            self._on_sync_keymap_changed(best_path)

    def _on_sync_keymap_changed(self, changed_path):
        if not self._auto_sync_cb.isChecked():
            return
        if self._canonical_path(changed_path) == self._canonical_path(self._json_path):
            self._on_external_file_changed(changed_path)
            return
        self._is_auto_sync = True
        try:
            self._load_scheme_path(changed_path)
        finally:
            self._is_auto_sync = False

    def _start_index_backup_timer(self) -> None:
        folder = self._config.json_folder_path
        if not folder or not os.path.isdir(folder) or self._closing:
            return
        if self._index_backup_mgr is None:
            from core.config_manager import resolve_app_dir
            self._index_backup_mgr = IndexFileBackupManager(PACKAGE_PREFIX_BY_NAME, resolve_app_dir())
        self._index_backup_mgr.index_directory = folder
        if self._index_backup_timer is None:
            self._index_backup_timer = QTimer(self)
            self._index_backup_timer.setInterval(60000)
            self._index_backup_timer.timeout.connect(self._try_index_backup)
            self._index_backup_timer.start()
            self._submit_index_job("cleanup", os.path.join(folder, "_cleanup.json"), None)
        form = self._sub_windows.get("ib")
        if form is not None and hasattr(form, "set_index_directory"):
            form.set_index_directory(folder)

    def _submit_index_job(self, kind, path, version):
        """工作线程只捕获不可变路径；结果经信号返回 UI。"""
        identity = (kind, self._canonical_path(path), version)
        if identity in self._index_inflight:
            return
        if self._index_executor is None:
            from concurrent.futures import ThreadPoolExecutor
            self._index_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="MuMuIndex")
        from core.config_manager import resolve_app_dir
        base = resolve_app_dir()
        owner = id(self)
        self._index_inflight.add(identity)

        def work():
            manager = IndexFileBackupManager(PACKAGE_PREFIX_BY_NAME, base)
            manager.index_directory = os.path.dirname(path)
            try:
                if kind == "backup":
                    manager.backup_index(path)
                    result = not bool(manager.last_error)
                elif kind == "check":
                    result = manager.check_and_notify_damage(path)
                else:
                    result = manager.cleanup_old_backups(10)
                return owner, identity, path, result, manager.last_error
            except Exception as ex:
                LogService.error_ex("MainWindow", ex, "后台索引任务失败")
                return owner, identity, path, False, str(ex)

        future = self._index_executor.submit(work)
        def done(completed):
            if not self._closing:
                _SHARED_SIGNALS.index_task_done.emit(completed.result())
        future.add_done_callback(done)

    def _on_index_task_done(self, payload):
        owner, identity, path, result, error = payload
        if owner != id(self) or self._closing:
            return
        self._index_inflight.discard(identity)
        kind, key, version = identity
        if kind == "backup" and not result:
            self._pending_index_backups.setdefault(key, (path, version))
            self._needs_index_backup = True
            self._update_status(f"索引备份失败，将重试: {error}")
        elif kind == "cleanup" and not result:
            self._update_status(f"旧索引备份清理失败: {error}")
        elif kind == "check":
            if key != self._canonical_path(self._json_path) or version != self._file_version(path):
                return
            if error:
                self._update_status(f"索引检查失败: {error}")
            elif not result:
                index = self._index_backup_mgr.resolve_index_file_path(path) if self._index_backup_mgr else path
                index_key = self._canonical_path(index)
                if index_key not in self._index_damage_notified:
                    self._index_damage_notified.add(index_key)
                    self._update_status("警告：索引文件可能已损坏，方案可能未写入索引！")

    def _try_index_backup(self) -> None:
        if not self._pending_index_backups and self._needs_index_backup and self._json_path:
            self._pending_index_backups[self._canonical_path(self._json_path)] = (self._json_path, self._file_version(self._json_path))
        pending = list(self._pending_index_backups.values())
        self._pending_index_backups.clear()
        self._needs_index_backup = False
        for path, version in pending:
            self._submit_index_job("backup", path, version)

    def _schedule_index_damage_check(self) -> None:
        if not self._json_path:
            return
        self._start_index_backup_timer()
        path, version = self._json_path, self._file_version(self._json_path)
        self._pending_index_backups[self._canonical_path(path)] = (path, version)
        self._needs_index_backup = True
        self._pending_check_scheme_path = (path, version)
        self._index_check_timer.start()

    def _index_check_timer_tick(self) -> None:
        pending = self._pending_check_scheme_path
        self._pending_check_scheme_path = None
        if not pending:
            return
        path, version = pending
        if self._canonical_path(path) != self._canonical_path(self._json_path) or version != self._file_version(path):
            return
        self._submit_index_job("check", path, version)

    def _start_file_monitor(self, file_path):
        """同时监听父目录，以恢复原子替换后失效的文件监听。"""
        previous = self._file_watcher.files() + self._file_watcher.directories()
        if previous:
            self._file_watcher.removePaths(previous)
        if file_path:
            folder = os.path.dirname(file_path)
            if os.path.isdir(folder):
                self._file_watcher.addPath(folder)
            if os.path.isfile(file_path):
                self._file_watcher.addPath(file_path)

    def _on_external_directory_changed(self, directory):
        path = self._json_path
        if path and self._canonical_path(directory) == self._canonical_path(os.path.dirname(path)):
            self._queue_external_file_changed(path)

    def _queue_external_file_changed(self, path):
        """对真实文件事件防抖，避免一次保存中间版本反复弹框。"""
        if self._closing or self._canonical_path(path) != self._canonical_path(self._json_path):
            return
        self._pending_external_path = path
        self._external_change_timer.start()

    def _flush_external_change(self):
        path, self._pending_external_path = self._pending_external_path, None
        if path and not self._closing:
            self._on_external_file_changed(path)

    def _on_external_file_changed(self, path):
        key = self._canonical_path(path)
        if key != self._canonical_path(self._json_path) or self._file_prompt_active:
            return
        if os.path.isfile(path) and path not in self._file_watcher.files():
            self._file_watcher.addPath(path)
        version = self._file_version(path)
        if (version is None or self._accepted_file_versions.get(key) == version
                or self._observed_file_versions.get(key) == version):
            return
        # 记录已提示版本用于去重；拒绝加载不等于接受覆盖磁盘的新内容。
        self._observed_file_versions[key] = version
        self._pending_check_scheme_path = None
        self._index_check_timer.stop()
        self._file_prompt_active = True
        try:
            if MessageDialog.question(self, "文件变更", "检测到当前 Json 文件被其他程序修改，是否重新加载？重新加载会清空撤销历史。"):
                self._load_scheme_path(path, confirm=False)
        finally:
            self._file_prompt_active = False

    def _check_unsaved_changes(self):
        """以真实历史判断切换保护，按钮仅作显示。"""
        if self._backup_mgr.get_snapshot_count() > 1:
            return MessageDialog.question(self, "切换方案",
                "当前方案有撤销/重做历史，切换或重新加载会清空这些历史。是否继续？")
        return True

def _in(default, width):
    inp = QLineEdit(default); inp.setMinimumWidth(width)
    inp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    inp.setValidator(QDoubleValidator(inp))
    return inp
