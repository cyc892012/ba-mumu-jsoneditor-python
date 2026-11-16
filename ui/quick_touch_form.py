"""
全屏快速摸点
"""
from PyQt5.QtWidgets import (
    QHBoxLayout, QLabel, QCheckBox,
    QLineEdit, QApplication, QComboBox, QScrollArea, QWidget, QVBoxLayout, QGridLayout,
)
import math
from PyQt5.QtCore import Qt, QTimer, QEvent, QPoint, QSignalBlocker
from PyQt5.QtGui import QDoubleValidator, QIntValidator, QFont
from .base_dialog import BaseDialog
from .key_overwrite import request_overwrite, overwrite_epoch
from .status_text import key_created, key_exists, listening
from controls import RoundedButton, KEY_TYPE_DISPLAY_NAMES, set_checked_silently, HintLabel
from controls.segmented import GenerationModeControl
from controls.status_pill import StatusPill
from .touch_presentation import DetailLabel, ElidedStatusLabel, type_segments, comfortable_controls
import theme
from input.mouse_controller import get_cursor_pos
from input.keyboard_listener import KeyboardListener
from core.config_manager import ConfigManager
from core.coord_model import px_to_rel, landing_hint
from core.json_editor import (
    calculate_coordinates, CoordinateConversionDirection,
    create_key, write_keys, get_scan_code,
    find_key_by_virtual_key,
    TYPE_CLICK, TYPE_MACRO,
)
from utils.safe_parse import try_parse_int, try_parse_float
from utils.qt_thread_bridge import ThreadBridge


class QuickTouchForm(BaseDialog):
    def set_overwrite_guard(self, callback, epoch_getter=None):
        self._overwrite_guard = callback
        self._overwrite_epoch_getter = epoch_getter

    def __init__(self, parent=None):
        super().__init__(parent, "全屏快速摸点")
        self.setFont(QFont(theme.FONT_FAMILY, 9))
        self.setFixedSize(400, 260)

        self._config = ConfigManager.shared()
        # 跨线程信号桥：键盘回调来自 pynput 监听线程，必须经它转发到主线程（见坑 1）
        self._bridge = ThreadBridge(self)
        self._keyboard_listener = KeyboardListener()
        self._keyboard_listener.on_key_down(self._on_key_down)

        # JSON 数据
        self._json_str = ""
        self._json_path = ""
        self._sync_cb = None  # 写回文件后通知主窗口
        # 模拟器分辨率（0 = 未同步，_get_fx 届时回落到 config）
        self._fx = 0
        self._fy = 0
        # 最新鼠标坐标
        self._last_mx = 0
        self._last_my = 0

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._on_timer_tick)

        self._resolution_getter = None
        self._key_type_getter = None
        self._apply_coord_cb = None
        self._type_local_override = False
        self._manual_desktop = False
        self._manual_inner = False
        self._exact_inner = None
        self._editing = set()
        self._listener_paused = False
        self._listen_epoch = 0
        self._generating = False
        self._closed = False
        self._screen_origin = (0, 0)
        self._build()
        self._connect_signals()
        self._init_resolution()
        for edit in (self._res_x, self._res_y, self._cur_x, self._cur_y, self._inner_x, self._inner_y):
            edit.installEventFilter(self)
        for edit in (self._res_x, self._res_y):
            edit.setValidator(QIntValidator(2, 2147483647, edit))
            edit.textEdited.connect(self._on_desktop_edited)
        for edit in (self._cur_x, self._cur_y):
            edit.setReadOnly(True)
        for edit in (self._inner_x, self._inner_y):
            edit.textEdited.connect(self._on_inner_edited)
            edit.setToolTip("编辑后固定使用手动内部坐标；自动获取可恢复鼠标跟踪。")
        self._type_cb.currentIndexChanged.connect(self._on_local_type_changed)

    def _init_resolution(self):
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            geometry = screen.geometry()
            self._screen_origin = (geometry.x(), geometry.y())
            self._res_x.setText(str(geometry.width()))
            self._res_y.setText(str(geometry.height()))

    def _connect_signals(self):
        self._auto_btn.clicked.connect(self._on_auto_get_resolution)
        self._once_cb.toggled.connect(self._on_once_toggled)
        self._multi_cb.toggled.connect(self._on_multi_toggled)

    def _get_fx(self):
        return self._resolution()[0]

    def _get_fy(self):
        return self._resolution()[1]

    def _build(self):
        ct = self.content_layout
        ct.setContentsMargins(8, 5, 8, 5)
        ct.setSpacing(3)
        self._hint_scroll = QScrollArea()
        self._hint_scroll.setWidgetResizable(True)
        self._hint_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._hint_scroll.setFrameShape(QScrollArea.NoFrame)
        self._hint_scroll.setFixedHeight(60)
        details = QWidget()
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(0, 0, 3, 0)
        detail_layout.setSpacing(3)
        self._hint = HintLabel("F11 全屏 MuMu，建议小助手置顶。\n无法生成时，请以管理员模式启动。")
        detail_layout.addWidget(self._hint)
        self._hint_scroll.setWidget(details)
        ct.addWidget(self._hint_scroll)

        resolution = QHBoxLayout()
        resolution.setSpacing(3)
        resolution.addWidget(QLabel("桌面"))
        self._res_x, self._res_y = _in("1920"), _in("1080")
        self._res_x.setFixedWidth(60)
        self._res_y.setFixedWidth(60)
        resolution.addWidget(self._res_x)
        resolution.addWidget(QLabel("×"))
        resolution.addWidget(self._res_y)
        self._auto_btn = RoundedButton("自动获取", "secondary")
        resolution.addWidget(self._auto_btn)
        resolution.addStretch()
        self._listen_pill = StatusPill(checked_text="连续监听", unchecked_text="未监听", interactive=False, kind="info")
        resolution.addWidget(self._listen_pill)
        ct.addLayout(resolution)

        modes = QHBoxLayout()
        modes.addWidget(QLabel("生成"))
        self._generation_mode = GenerationModeControl()
        self._once_cb = self._generation_mode.onceCheckBox()
        self._multi_cb = self._generation_mode.continuousCheckBox()
        modes.addWidget(self._generation_mode)
        modes.addStretch()
        modes.addWidget(QLabel("类型"))
        self._type_cb = type_segments()
        modes.addWidget(self._type_cb)
        ct.addLayout(modes)

        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(3)
        for row, (title, xname, yname) in enumerate((
            ("鼠标坐标", "_cur_x", "_cur_y"), ("内部坐标", "_inner_x", "_inner_y"),
        )):
            grid.addWidget(QLabel(title), row, 0)
            grid.addWidget(QLabel("X"), row, 1)
            xedit, yedit = QLineEdit("0"), QLineEdit("0")
            for edit in (xedit, yedit):
                edit.setMinimumWidth(80)
                edit.setValidator(QDoubleValidator(edit))
                # 鼠标展示只读，内部坐标继续走正式的手动编辑逻辑。
                edit.setReadOnly(row == 0)
            setattr(self, xname, xedit)
            setattr(self, yname, yedit)
            grid.addWidget(xedit, row, 2)
            grid.addWidget(QLabel("Y"), row, 3)
            grid.addWidget(yedit, row, 4)
        ct.addLayout(grid)
        self._kb_status_label = DetailLabel(listening(False))
        detail_layout.addWidget(self._kb_status_label)
        self._status_perm = DetailLabel("等待输入；请先加载 JSON。")
        detail_layout.addWidget(self._status_perm)
        ct.addStretch()
        self._result_view = ElidedStatusLabel("等待输入")
        self.status_bar.addWidget(self._result_view, 1)
        self._kb_status_label.messageChanged.connect(self._result_view.setText)
        self._kb_status_label.messageChanged.connect(self._refresh_presentation)
        self._status_perm.messageChanged.connect(self._result_view.setText)
        comfortable_controls(self._res_x, self._res_y, self._cur_x, self._cur_y,
                             self._inner_x, self._inner_y, self._auto_btn, self._listen_pill,
                             *self._generation_mode._buttons, *self._type_cb._buttons)

    def _refresh_presentation(self, *_):
        active = self._keyboard_listener.is_listening
        text = "连续监听" if active and self._multi_cb.isChecked() else "单次监听" if active else "未监听"
        self._listen_pill.setStateTexts(text, text)
        self._listen_pill.setChecked(active)

    # ── 自动获取分辨率 ──

    def _on_auto_get_resolution(self):
        self._manual_desktop = False
        self._manual_inner = False
        self._exact_inner = None
        self._init_resolution()
        self._on_timer_tick()

    # ── 生成模式切换 ──

    def _on_once_toggled(self, checked):
        self._set_mode(True, checked)

    def _on_multi_toggled(self, checked):
        self._set_mode(False, checked)

    # ── 键盘回调 ──

    def _on_key_down(self, key_text, vk=0):
        self._bridge.post(self._on_key_down_ui, key_text, vk, self._listen_epoch, overwrite_epoch(self))

    def _on_key_down_ui(self, key_text, vk=0, epoch=None, overwrite_event_epoch=None):
        if overwrite_event_epoch is not None and overwrite_event_epoch != overwrite_epoch(self):
            return False
        if (not self.isVisible() or self._closed or self._generating or self._editing or
                (epoch is not None and epoch != self._listen_epoch) or
                not (self._once_cb.isChecked() or self._multi_cb.isChecked())):
            return False
        if not self._json_str or not self._json_path:
            self._status_perm.setText("请先在主窗口加载 JSON 文件")
            return False
        if not self._sync_cb:
            self._status_perm.setText("未设置写回回调，尚未写入按键。")
            return False
        name = (key_text or "").upper()
        if not name:
            self._status_perm.setText("未捕获到有效按键。")
            return False
        if getattr(self, "_overwrite_guard", None) is None and self._multi_cb.isChecked() and vk and find_key_by_virtual_key(self._json_str, vk) != -1:
            self._status_perm.setText(key_exists(name))
            return False
        self._generating = True
        try:
            x, y, fx, fy = self._generation_coordinates()
            value = create_key(self._current_key_type(), name, f"{px_to_rel(x, fx):.16f}", f"{px_to_rel(y, fy):.16f}",
                               str(get_scan_code(vk)) if vk else "0", str(vk))
            if value is None:
                self._status_perm.setText("按键创建失败，未提交。")
                return False
            original = self._json_str
            if not request_overwrite(self, original, value):
                return False
            if original != self._json_str:
                return False
            updated = write_keys(value, original)
            if updated is None:
                self._status_perm.setText("JSON 生成失败，未提交。")
                return False
            if self._sync_cb(updated) is False:
                self._status_perm.setText("写回失败，按键未提交。")
                return False
            self._json_str = updated
            if self._once_cb.isChecked():
                self._once_cb.setChecked(False)
            message = key_created(name, f"坐标 ({x:.16f},{y:.16f})")
            self._status_perm.setText(message)
            self._kb_status_label.setText(listening(self._multi_cb.isChecked(), continuous=self._multi_cb.isChecked()))
            if self._apply_coord_cb:
                try:
                    self._apply_coord_cb(f"{x:.16f}", f"{y:.16f}")
                except Exception as ex:
                    self._status_perm.setText(message + f"；主坐标回填失败：{ex}")
            return True
        except Exception as ex:
            self._status_perm.setText(f"生成或写回失败：{ex}")
            return False
        finally:
            self._generating = False

    # ── 定时器：鼠标追踪 ──

    def _on_timer_tick(self):
        if self._editing:
            return False
        try:
            fx, fy = self._resolution()
            if self._manual_inner:
                self._update_landing_hint()
                return True
            mx, my = get_cursor_pos()
            if not all(math.isfinite(value) for value in (mx, my)):
                raise ValueError("鼠标坐标不是有限数")
            screen = QApplication.screenAt(QPoint(int(mx), int(my)))
            if screen is None:
                raise ValueError("鼠标不在有效显示器内")
            geometry = screen.geometry()
            self._screen_origin = (geometry.x(), geometry.y())
            if not self._manual_desktop:
                self._res_x.setText(str(geometry.width()))
                self._res_y.setText(str(geometry.height()))
            width, height = self._parse_size(self._res_x.text()), self._parse_size(self._res_y.text())
            local_x, local_y = mx - geometry.x(), my - geometry.y()
            x, y, status = calculate_coordinates(width, height, fx, fy, local_x, local_y,
                                                  CoordinateConversionDirection.MOUSE_TO_SIMULATOR)
            if status < 0 or not all(math.isfinite(value) for value in (x, y)) or not (0 <= x <= fx - 1 and 0 <= y <= fy - 1):
                raise ValueError("鼠标未落在模拟器有效区域内")
            self._last_mx, self._last_my = mx, my
            self._cur_x.setText(str(mx))
            self._cur_y.setText(str(my))
            self._inner_x.setText(f"{x:.2f}")
            self._inner_y.setText(f"{y:.2f}")
            self._exact_inner = (x, y, fx, fy)
            return True
        except Exception as ex:
            self._exact_inner = None
            self._stop_modes(f"坐标跟踪已停止：{ex}")
            return False

    # ── 公开接口 ──

    def set_json_data(self, json_str):
        """设置当前 JSON 键位数据"""
        self._json_str = json_str

    def set_json_path(self, json_path):
        """设置当前 JSON 文件路径"""
        self._json_path = json_path

    def set_sync_callback(self, callback):
        """设置同步回调：写回文件后通知主窗口刷新"""
        self._sync_cb = callback

    # ── 关闭清理 ──

    def closeEvent(self, event):
        self._closed = True
        self._stop_modes()
        self._exact_inner = None
        self._editing.clear()
        if self._listener_paused:
            self._keyboard_listener.resume()
            self._listener_paused = False
        super().closeEvent(event)

    @staticmethod
    def _parse_size(text):
        value = float(text)
        if not math.isfinite(value) or not value.is_integer() or value <= 1:
            raise ValueError("分辨率必须是大于 1 的整数")
        return int(value)

    def _resolution(self):
        if self._resolution_getter:
            width, height = self._resolution_getter()
        else:
            width = self._fx if self._fx else self._config.fx
            height = self._fy if self._fy else self._config.fy
        return self._parse_size(width), self._parse_size(height)

    def set_resolution_getter(self, getter):
        self._resolution_getter = getter
        self._exact_inner = None

    def set_key_type_getter(self, getter):
        self._key_type_getter = getter
        self._type_local_override = False
        self._current_key_type()

    def set_apply_coord_callback(self, callback):
        """成功写回后传递内部像素坐标的 16 位字符串，不传相对坐标。"""
        self._apply_coord_cb = callback

    def _current_key_type(self):
        main_type = self._key_type_getter() if self._key_type_getter else None
        if main_type is not None and not self._type_local_override:
            macro = main_type == TYPE_MACRO or "宏" in str(main_type)
            with QSignalBlocker(self._type_cb):
                self._type_cb.setCurrentIndex(1 if macro else 0)
        return TYPE_MACRO if "宏" in self._type_cb.currentText() else TYPE_CLICK

    def _on_local_type_changed(self, index):
        self._type_local_override = True

    def _on_desktop_edited(self, text):
        self._manual_desktop = True
        self._exact_inner = None

    def _on_inner_edited(self, text):
        self._manual_inner = True
        self._exact_inner = None
        self._update_landing_hint()

    def _update_landing_hint(self):
        ok_x, x = try_parse_float(self._inner_x.text())
        ok_y, y = try_parse_float(self._inner_y.text())
        try:
            fx, fy = self._resolution()
            message = landing_hint(x, y, fx, fy) if ok_x and ok_y else "请输入完整的有限数坐标"
        except (ValueError, TypeError, OverflowError):
            message = "无法计算实际落点，请检查坐标和分辨率"
        self._status_perm.setText(message)
        # 复用既有状态行；底栏可省略显示，完整偏差在 tooltip 和详情区。
        for edit in (self._inner_x, self._inner_y):
            edit.setToolTip(message + "；自动获取可恢复鼠标跟踪。")

    def _stop_modes(self, message=None):
        self._listen_epoch += 1
        set_checked_silently(self._once_cb, False)
        set_checked_silently(self._multi_cb, False)
        self._keyboard_listener.stop_listening()
        self._timer.stop()
        self._kb_status_label.setText(listening(False))
        if message:
            self._status_perm.setText(message)

    def _set_mode(self, once, checked):
        self._listen_epoch += 1
        if checked:
            set_checked_silently(self._multi_cb if once else self._once_cb, False)
            try:
                self._current_key_type()
                self._keyboard_listener.start_listening()
                if not self._keyboard_listener.is_listening:
                    raise RuntimeError("键盘监听启动失败，请检查权限")
                self._kb_status_label.setText(listening(True, continuous=not once))
                self._timer.start()
            except Exception as ex:
                self._stop_modes(f"无法开启生成功能：{ex}")
        elif not self._once_cb.isChecked() and not self._multi_cb.isChecked():
            self._keyboard_listener.stop_listening()
            self._timer.stop()
            self._kb_status_label.setText(listening(False))

    def eventFilter(self, watched, event):
        if isinstance(watched, QLineEdit):
            if event.type() == QEvent.FocusIn:
                self._editing.add(watched)
                if not self._listener_paused:
                    self._keyboard_listener.pause()
                    self._listener_paused = True
            elif event.type() == QEvent.FocusOut:
                self._editing.discard(watched)
                if not self._editing and self._listener_paused:
                    self._keyboard_listener.resume()
                    self._listener_paused = False
        return super().eventFilter(watched, event)

    def _generation_coordinates(self):
        fx, fy = self._resolution()
        if self._manual_inner:
            ok_x, x = try_parse_float(self._inner_x.text())
            ok_y, y = try_parse_float(self._inner_y.text())
            if not (ok_x and ok_y and 0 <= x <= fx - 1 and 0 <= y <= fy - 1):
                raise ValueError("手动内部坐标无效或超出模拟器范围")
        else:
            if not self._on_timer_tick() or self._exact_inner is None:
                raise ValueError("没有有效的当前鼠标坐标")
            x, y, fx, fy = self._exact_inner
        return x, y, fx, fy

    def showEvent(self, event):
        self._closed = False
        self._current_key_type()
        super().showEvent(event)


def _in(default):
    inp = QLineEdit(default); inp.setFixedWidth(55)
    inp.setValidator(QDoubleValidator(inp))
    return inp
