"""
鼠标回溯
"""
import re
from PyQt5.QtCore import QTimer, QEvent, Qt
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QLineEdit, QGridLayout,
)
from PyQt5.QtGui import QIntValidator, QFont
from .base_dialog import BaseDialog
from .wide_screen_form import _ElidedLabel
from controls import RoundedButton, CardPanel, KeyCaptureEdit
import theme
from input.mouse_controller import get_cursor_pos, move_mouse_to
from input.hotkey_manager import HotKeyManager
from core.config_manager import ConfigManager
from utils.safe_parse import try_parse_int
from utils.qt_thread_bridge import ThreadBridge


# 按键捕获框已统一到 controls/key_capture_edit.py（本项目曾有 4 份重复实现）。
# 本面板的差异：Esc 记为 "Esc"（不用于清空）、无占位文本。


class MouseTrackForm(BaseDialog):
    def __init__(self, parent=None):
        super().__init__(parent, "鼠标回溯")
        self.setFixedSize(260, 230)
        self.setFont(QFont("Microsoft YaHei UI", 9))
        # 跨线程信号桥：热键回调来自 keyboard 钩子线程，必须经它转发到主线程（见坑 1）
        self._bridge = ThreadBridge(self)
        self._timer = None
        self._hotkey_mgr = HotKeyManager()
        self._hotkey_generation = 0
        self._hotkeys_active = False
        self._config = ConfigManager.shared()
        self._bindings = ("D", "F")
        self._editing_key = None
        self._has_saved_coord = False
        self._status_callback = None  # 主窗口状态栏回调（对应 C# StatusCallback）
        self._build()
        # 启动时从配置加载快捷键
        for edit in (self._cur_x, self._cur_y, self._saved_x, self._saved_y):
            edit.setText("0")
            edit.setValidator(QIntValidator(edit))
        for edit in (self._saved_x, self._saved_y):
            edit.textEdited.connect(self._on_saved_coord_edited)
        for edit in (self._save_key_edit, self._recall_key_edit):
            edit.installEventFilter(self)
            edit.setToolTip("双击后按键可编辑；合法改键立即生效，点击保存后持久化")
            edit._on_captured = lambda name, vk: self._on_key_captured()
        self._load_from_config()

    def _build(self):
        self.status_bar.setSizeGripEnabled(False)
        ct = self.content_layout
        ct.setContentsMargins(8, 4, 8, 4)
        ct.setSpacing(3)
        self._enable_cb = QCheckBox("启用鼠标坐标捕捉")
        self._enable_cb.toggled.connect(self._on_enable_toggled)
        ct.addWidget(self._enable_cb)
        cards = QHBoxLayout()
        cards.setSpacing(6)
        edits = []
        for title, values in (("当前鼠标坐标", ("1200", "600")), ("已保存坐标", ("800", "450"))):
            card = CardPanel(title)
            card.content_layout.setContentsMargins(5, 3, 5, 3)
            card.content_layout.setSpacing(2)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(2)
            for row, (axis, value) in enumerate(zip(("X", "Y"), values)):
                grid.addWidget(QLabel(axis), row, 0)
                edit = QLineEdit(value)
                edit.setMinimumWidth(35)
                edit.setFixedHeight(26)
                edit.setStyleSheet("min-height: 24px; padding: 0 3px;")
                edit.textChanged.connect(edit.setToolTip)
                grid.addWidget(edit, row, 1)
                edits.append(edit)
            card.content_layout.addLayout(grid)
            cards.addWidget(card, 1)
        self._cur_x, self._cur_y, self._saved_x, self._saved_y = edits
        ct.addLayout(cards)
        keys = QGridLayout()
        keys.setHorizontalSpacing(4)
        keys.setVerticalSpacing(3)
        self._save_key_edit = KeyCaptureEdit(text="D", width=90, placeholder="", extended_keys=True)
        self._recall_key_edit = KeyCaptureEdit(text="F", width=90, placeholder="", extended_keys=True)
        self._btn_save = RoundedButton("保存", "secondary")
        self._btn_recall = RoundedButton("读取", "secondary")
        self._btn_save.clicked.connect(self._on_save_clicked)
        self._btn_recall.clicked.connect(self._on_recall_clicked)
        for row, title, edit, button in ((0, "保存快捷键", self._save_key_edit, self._btn_save),
                                         (1, "回溯快捷键", self._recall_key_edit, self._btn_recall)):
            keys.addWidget(QLabel(title), row, 0)
            keys.addWidget(edit, row, 1)
            keys.addWidget(button, row, 2)
            edit.textChanged.connect(edit.setToolTip)
        ct.addLayout(keys)
        self._status_label = _ElidedLabel("Ctrl+D 保存 / Ctrl+F 回溯")
        self.status_bar.addWidget(self._status_label, 1)
        for edit in edits:
            edit.textChanged.connect(self._update_match_feedback)
        self._update_match_feedback()

    # ── 公开接口：状态栏回调 ──────────────────────────────
    def set_status_callback(self, callback) -> None:
        """设置主窗口状态栏回调（对应 C# StatusCallback）"""
        self._status_callback = callback

    # ── 鼠标追踪 ──────────────────────────────────────────────

    def eventFilter(self, watched, event):
        if watched in (self._save_key_edit, self._recall_key_edit):
            if event.type() == QEvent.MouseButtonDblClick:
                self._editing_key = watched
                self._status_label.setText("请按新快捷键；合法改键立即生效，保存后持久化")
            elif event.type() == QEvent.FocusOut:
                self._editing_key = None
            elif event.type() == QEvent.KeyPress and watched is not self._editing_key:
                self._status_label.setText("快捷键已锁定，请双击后编辑")
                return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _key_name(value):
        from core.key_names import canonical_hotkey_key
        return canonical_hotkey_key(value)

    def _validated_bindings(self):
        values = (self._save_key_edit.text().strip(), self._recall_key_edit.text().strip())
        names = tuple(self._key_name(value) for value in values)
        if names[0] == names[1]:
            raise ValueError("快捷键冲突：保存与回溯键相同")
        return values

    def _set_binding_texts(self, values):
        self._save_key_edit.setText(values[0])
        self._recall_key_edit.setText(values[1])

    def _on_key_captured(self):
        old = self._bindings
        try:
            new = self._validated_bindings()
            if self._enable_cb.isChecked() and not self._register_hotkeys():
                self._set_binding_texts(old)
                return
            self._bindings = new
            self._status_label.setText(f"快捷键已生效但未保存: {new[0]} / {new[1]}")
        except ValueError as ex:
            self._set_binding_texts(old)
            self._status_label.setText(str(ex))
        finally:
            self._editing_key = None

    def _on_enable_toggled(self, checked: bool) -> None:
        if checked:
            if not self._register_hotkeys():
                self._enable_cb.blockSignals(True)
                self._enable_cb.setChecked(False)
                self._enable_cb.blockSignals(False)
                return
            if self._timer is None:
                self._timer = QTimer(self)
                self._timer.timeout.connect(self._poll_mouse)
            self._timer.start(50)
        else:
            if self._timer is not None:
                self._timer.stop()
            self._unregister_hotkeys()

    def _poll_mouse(self) -> None:
        try:
            x, y = get_cursor_pos()
            self._cur_x.setText(str(x))
            self._cur_y.setText(str(y))
            self._update_match_feedback()
        except Exception as ex:
            self._enable_cb.setChecked(False)
            self._status_label.setText(f"坐标捕捉失败，已停止: {ex}")

    def _update_match_feedback(self):
        for current, saved in ((self._cur_x, self._saved_x), (self._cur_y, self._saved_y)):
            ok_current, value = try_parse_int(current.text())
            ok_saved, expected = try_parse_int(saved.text())
            matched = self._has_saved_coord and ok_current and ok_saved and value == expected
            current.setProperty("p3Matched", matched)
            current.setStyleSheet("QLineEdit { background: white; min-height: 24px; padding: 0 3px; "
                                 f"border: 1px solid {theme.MINT if matched else theme.BORDER_INPUT}; "
                                 "border-radius: 6px; }"
                                 f"QLineEdit:focus {{ border-color: {theme.MINT if matched else theme.VIOLET}; }}")
            current.setToolTip(current.text() + (" · 与已保存坐标匹配" if matched else ""))
            current.setAccessibleDescription("与已保存坐标匹配" if matched else "与已保存坐标不匹配")

    def _register_pair(self, values):
        # 固定注册时的代次；迟到的旧后端回调不能借用重开后的新代次。
        generation = self._hotkey_generation
        for value, callback in zip(values, (self._do_save, self._do_recall)):
            queued_callback = lambda callback=callback, generation=generation: callback(generation)
            if not self._hotkey_mgr.register(f"ctrl+{self._key_name(value)}", queued_callback):
                return False
        self._hotkeys_active = True
        return True

    def _register_hotkeys(self) -> bool:
        old = self._bindings
        try:
            values = self._validated_bindings()
        except ValueError as ex:
            self._set_binding_texts(old)
            self._status_label.setText(str(ex))
            return False
        was_enabled = bool(self._timer and self._timer.isActive())
        self._unregister_hotkeys()
        try:
            if not self._register_pair(values):
                raise ValueError("系统热键注册失败")
        except Exception as ex:
            self._unregister_hotkeys()
            try:
                restored = was_enabled and self._register_pair(old)
            except Exception:
                restored = False
            self._set_binding_texts(old)
            if was_enabled and not restored:
                self._unregister_hotkeys()
                self._enable_cb.setChecked(False)
            self._status_label.setText(f"快捷键未更改: {ex}" if restored else f"快捷键注册失败，捕捉已停止: {ex}")
            return False
        self._bindings = values
        return True

    def _unregister_hotkeys(self) -> None:
        # 先关闭本轮生命周期，再注销后端；排队或并发迟到的回调均失效。
        self._hotkeys_active = False
        self._hotkey_generation += 1
        self._hotkey_mgr.unregister_all()

    def _accept_hotkey_callback(self, generation) -> bool:
        return (self._enable_cb.isChecked() and self._hotkeys_active
                and (generation is None or generation == self._hotkey_generation))

    def _do_save(self, generation=None) -> None:
        generation = self._hotkey_generation if generation is None else generation
        self._bridge.post(self._do_save_ui, generation)

    def _do_save_ui(self, generation=None) -> None:
        if not self._accept_hotkey_callback(generation):
            return
        ok_x, x = try_parse_int(self._cur_x.text())
        ok_y, y = try_parse_int(self._cur_y.text())
        if not ok_x or not ok_y:
            self._status_label.setText("保存坐标失败: 请输入整数坐标")
            return
        self._saved_x.setText(str(x))
        self._saved_y.setText(str(y))
        self._has_saved_coord = True
        self._update_match_feedback()
        self._status_label.setText(f"坐标已保存: ({x}, {y})")
        self._notify_status("坐标已保存")

    def _notify_status(self, message):
        if self._status_callback:
            try:
                self._status_callback(message)
            except Exception:
                pass

    def _on_saved_coord_edited(self):
        self._has_saved_coord = all(try_parse_int(edit.text())[0] for edit in (self._saved_x, self._saved_y))
        self._update_match_feedback()

    def _do_recall(self, generation=None) -> None:
        generation = self._hotkey_generation if generation is None else generation
        self._bridge.post(self._do_recall_ui, generation)

    def _do_recall_ui(self, generation=None) -> None:
        if not self._accept_hotkey_callback(generation):
            return
        if not self._has_saved_coord:
            self._status_label.setText("回溯失败: 尚未保存坐标")
            return
        ok_x, x = try_parse_int(self._saved_x.text())
        ok_y, y = try_parse_int(self._saved_y.text())
        if not ok_x or not ok_y:
            self._status_label.setText("回溯失败: 坐标须为整数")
            return
        try:
            move_mouse_to(x, y)
        except Exception as ex:
            self._status_label.setText(f"回溯失败: {ex}")
            return
        message = f"鼠标已回溯至: ({x}, {y})"
        self._status_label.setText(message)
        self._notify_status(message)

    def _on_save_clicked(self) -> None:
        try:
            values = self._validated_bindings()
        except ValueError as ex:
            self._set_binding_texts(self._bindings)
            self._status_label.setText(str(ex))
            return
        if self._enable_cb.isChecked() and not self._register_hotkeys():
            return
        old = (self._config.find_key, self._config.reset_key)
        try:
            with self._config.batch():
                self._config.find_key, self._config.reset_key = values
            if self._config.save() is False:
                raise ValueError("配置写入失败")
        except Exception as ex:
            with self._config.batch():
                self._config.find_key, self._config.reset_key = old
            self._status_label.setText(f"快捷键已生效但未保存: {ex}")
            return
        self._bindings = values
        self._status_label.setText(f"快捷键已保存: {values[0]} / {values[1]}")

    def _on_recall_clicked(self) -> None:
        if self._config.reload() is False:
            self._status_label.setText("快捷键读取失败")
            return
        if self._load_from_config():
            self._status_label.setText(f"快捷键已读取: {self._bindings[0]} / {self._bindings[1]}")

    def _load_from_config(self) -> bool:
        old = self._bindings
        self._set_binding_texts((self._config.find_key, self._config.reset_key))
        try:
            values = self._validated_bindings()
            if self._enable_cb.isChecked() and not self._register_hotkeys():
                return False
        except ValueError as ex:
            self._set_binding_texts(old)
            self._status_label.setText(f"快捷键读取失败: {ex}")
            return False
        self._bindings = values
        self._status_label.setText(f"Ctrl+{values[0]} 保存 / Ctrl+{values[1]} 回溯；尚未保存坐标")
        return True

    def closeEvent(self, event) -> None:
        self._unregister_hotkeys()
        self._enable_cb.setChecked(False)
        if self._timer is not None:
            self._timer.stop()
        self._editing_key = None
        super().closeEvent(event)
