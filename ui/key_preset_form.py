"""
基础键位预设
"""
import json
import math
import os
from PyQt5.QtCore import QEvent, Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QHBoxLayout, QVBoxLayout,
    QLabel, QFrame, QSizePolicy,
    QFileDialog, QMenu,
)
from .base_dialog import BaseDialog
from .message_dialog import MessageDialog
from .key_overwrite import request_overwrite
from .status_text import DETAIL_NO_INJECT, key_exists
from controls import RoundedButton, SearchableComboBox, KeyCaptureEdit, rebuild_combo
from core.package_names import strip_package_prefix
from core import json_editor
from core.json_editor import read_json_file, read_key_from_file, write_keys, write_text_atomic
import theme

# 包名表已收口到 core/package_names.py（长前缀优先的契约在那里统一维护）。


# 按键捕获框已统一到 controls/key_capture_edit.py（本项目曾有 4 份重复实现）。
# 本面板允许捕获 Esc，保留扩展键；不改变公共捕获组件。


class KeyPresetForm(BaseDialog):
    def set_overwrite_guard(self, callback, epoch_getter=None):
        self._overwrite_guard = callback
        self._overwrite_epoch_getter = epoch_getter

    def __init__(self, parent=None):
        super().__init__(parent, "基础键位预设")
        self.setFixedSize(390, 300)
        self._json_str = ""
        self._json_path = ""
        self._sync_cb = None  # Callable[[str], bool]；历史 None 返回仍兼容
        self._apply_coord_cb = None  # Callable[[str, str], None] — 回填 JSX/JSY 到主窗口
        self._captured_key = ""
        self._captured_vk = 0
        self._build()
        # 共享控件由监督提供此信号；旧组件仍可初始化，不越界修公共文件。
        popup_signal = getattr(self._preset_cb, "popupAboutToShow", None)
        if popup_signal is not None:
            popup_signal.connect(self._refresh_preset_list)
        self._refresh_preset_list()

    def _build(self):
        self.setFixedSize(390, 300)
        ct = self.content_layout
        ct.setContentsMargins(8, 6, 8, 6)
        ct.setSpacing(5)
        self._preset_actions = {}

        selector = self._preset_card()
        selection = QVBoxLayout(selector)
        selection.setContentsMargins(8, 5, 8, 5)
        selection.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("基础键位"))
        self._preset_cb = SearchableComboBox()
        self._preset_cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._preset_cb.combo.setMinimumWidth(0)
        self._preset_cb.combo.setFixedHeight(theme.H_INPUT)
        self._preset_cb.combo.setSizeAdjustPolicy(self._preset_cb.combo.AdjustToMinimumContentsLengthWithIcon)
        self._preset_cb.combo.setMinimumContentsLength(8)
        self._preset_cb.combo.view().setTextElideMode(Qt.ElideMiddle)
        self._preset_cb.currentTextChanged.connect(self._update_preset_hint)
        row.addWidget(self._preset_cb, 1)
        selection.addLayout(row)
        self._preset_hint = QLabel()
        self._preset_hint.setTextFormat(Qt.PlainText)
        self._preset_hint.setWordWrap(False)
        self._preset_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._preset_hint.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; background: transparent; border: none;")
        self._preset_hint.installEventFilter(self)
        selection.addWidget(self._preset_hint)
        ct.addWidget(selector)

        ct.addWidget(self._preset_group("写入", [
            ("写入所有", "primary", "_inject_all_keys"),
            ("写入单个", "secondary", "_inject_single_key"),
        ]))
        ct.addWidget(self._preset_group("清理", [
            ("键位去重", "secondary", "_dedup_against_preset"),
            ("右下清空", "secondary", "_clear_range_rd"),
        ]))
        ct.addWidget(self._preset_group("管理", [
            ("导入", "secondary", "_import_to_preset"),
            ("打开", "secondary", "_on_open_preset"),
            ("删除", "danger", "_delete_preset"),
        ]))

        read_card = self._preset_card()
        read = QHBoxLayout(read_card)
        read.setContentsMargins(8, 4, 8, 4)
        read.setSpacing(6)
        read.addWidget(QLabel("预设读取"))
        # 保留较长键名的宽度；捕获事件和回调继续使用原控件。
        self._preset_read_ie = KeyCaptureEdit(extended_keys=True,
            on_captured=self._on_key_captured, width=96, escape_clears=False)
        self._preset_read_ie.setFixedHeight(theme.H_INPUT)
        read.addWidget(self._preset_read_ie)
        button = RoundedButton("读取", "secondary")
        button.setFixedHeight(theme.H_BTN)
        button.clicked.connect(self._on_read_from_preset)
        self._preset_actions["_on_read_from_preset"] = button
        read.addWidget(button)
        read.addStretch()
        ct.addWidget(read_card)
        ct.addStretch()

    # ========================================================================
    # 公开接口
    # ========================================================================

    def set_json_data(self, json_str: str):
        self._json_str = json_str or ""

    def set_json_path(self, json_path: str):
        self._json_path = json_path or ""

    def set_sync_callback(self, callback):
        self._sync_cb = callback

    def set_apply_coord_callback(self, callback):
        """设置回填 JSX/JSY 到主窗口的回调（对应 C# ReadPP2 → SetJSXY）"""
        self._apply_coord_cb = callback

    # ========================================================================
    # 预设文件路径
    # ========================================================================

    @property
    def _presets_dir(self):
        # 程序根目录下的 data 目录（对应 C# AppDomain.BaseDirectory/data）
        # 与 config 同源解析（源码版 / exe 版 / MOMO_DATA_DIR 一致）
        from core.config_manager import resolve_app_dir
        return os.path.join(resolve_app_dir(), "data")

    def _refresh_preset_list(self):
        """扫描 data 目录下所有 .json 文件，填充下拉框。"""
        selected = self._preset_cb.currentText()
        files = []
        if os.path.isdir(self._presets_dir):
            try:
                files = sorted([
                    f for f in os.listdir(self._presets_dir)
                    if f.lower().endswith(".json")
                ])
            except OSError as exc:
                self._update_status(f"读取预设目录失败: {exc}")
                return
        blocked = self._preset_cb.combo.signalsBlocked()
        try:
            rebuild_combo(self._preset_cb, files, restore_text=selected if selected in files else None)
            # Qt 更新当前项 tooltip 也可能发出文本变化；与列表重建一起静默完成。
            self._preset_cb.blockSignals(True)
            for index in range(self._preset_cb.count()):
                self._preset_cb.combo.setItemData(index, self._preset_cb.itemText(index), Qt.ToolTipRole)
        finally:
            self._preset_cb.blockSignals(blocked)
        self._update_preset_hint()
        if not os.path.isdir(self._presets_dir):
            self._update_status("预设目录不存在，请导入或保存预设以创建目录")
        else:
            self._update_status(f"共 {len(files)} 个预设按键")

    def _get_preset_path(self):
        name = self._preset_cb.currentText()
        if not name:
            return ""
        return os.path.join(self._presets_dir, name)

    def _on_key_captured(self, name, vk):
        self._captured_key = name
        self._captured_vk = vk
        if name:
            self._update_status(f"已捕获按键: {name} (VK: {vk if self._valid_vk(vk) else '不可用，按文本匹配'})")

    @staticmethod
    def _strip_package_name(filename: str) -> str:
        """去掉文件名开头的包名前缀（对应 C# StripPackageName）。"""
        return strip_package_prefix(filename)

    # ========================================================================
    # 1. 写入所有按键
    # ========================================================================

    def _inject_all_keys(self):
        if not self._json_str:
            self._update_status("请先加载 JSON 文件")
            return
        preset_path = self._get_preset_path()
        if not os.path.isfile(preset_path):
            self._update_status("预设文件不存在，请先选择基础键位")
            return

        try:
            _, preset_obj = read_json_file(preset_path)
            preset_keymaps = self._scheme_keymaps(preset_obj)
            target_keymaps = self._scheme_keymaps(self._json_str)
        except Exception as exc:
            self._update_status(f"读取按键数据失败: {exc}")
            return
        if not preset_keymaps:
            self._update_status("预设文件中无按键数据")
            return

        existing = {self._key_identity(km) for km in target_keymaps}
        pending = []
        skipped = 0

        for km in preset_keymaps:
            if not isinstance(km, dict) or not isinstance(km.get("key"), dict):
                continue
            identity = self._key_identity(km)
            if identity is not None and identity in existing and getattr(self, "_overwrite_guard", None) is None:
                skipped += 1
                continue
            pending.append(km)
            if identity is not None:
                existing.add(identity)

        # 正式主窗注入确认桥；未连接确认桥的旧嵌入接口继续安全跳过冲突。
        if not pending:
            self._update_status(f"写入所有: 无变化, 跳过 {skipped} 个重复")
            return
        original = self._json_str
        if not request_overwrite(self, original, pending) or self._json_str != original:
            return
        updated = write_keys(json.dumps(pending, ensure_ascii=False, allow_nan=False), original)
        if self._commit_candidate(updated):
            self._update_status(f"写入所有: 注入 {len(pending)} 个, 跳过 {skipped} 个重复")

    # ========================================================================
    # 2. 写入单个按键
    # ========================================================================

    def _inject_single_key(self):
        if not self._json_str:
            self._update_status("请先加载 JSON 文件")
            return
        key_text = self._captured_key
        if not key_text:
            self._update_status("请先在下方捕获按键")
            return
        preset_path = self._get_preset_path()
        if not os.path.isfile(preset_path):
            self._update_status("预设文件不存在，请先选择基础键位")
            return

        try:
            _, preset_obj = read_json_file(preset_path)
            self._scheme_keymaps(preset_obj)
            target = self._scheme_keymaps(self._json_str)
            vk = self._captured_vk
            if self._valid_vk(vk):
                key_block = json_editor.read_key_from_file_by_virtual_key(preset_path, vk)
                identity = ("vk", vk)
            else:
                key_block = read_key_from_file(preset_path, key_text)
                identity = ("text", key_text)
            duplicate = (any(self._key_identity(km) == identity for km in target)
                         if self._valid_vk(vk) else any(
                             isinstance(km, dict) and isinstance(km.get("key"), dict)
                             and km["key"].get("text") == key_text for km in target))
        except Exception as exc:
            self._update_status(f"读取按键失败: {exc}")
            return
        if duplicate and getattr(self, "_overwrite_guard", None) is None:
            self._update_status(key_exists(key_text, DETAIL_NO_INJECT))
            return
        if key_block is None:
            self._update_status(f"预设中未找到按键 [{key_text}]")
            return

        original = self._json_str
        if not request_overwrite(self, original, key_block) or self._json_str != original:
            return
        updated = write_keys(key_block, original)
        if updated is None:
            self._update_status(f"写入按键 [{key_text}] 失败")
            return

        if self._commit_candidate(updated):
            self._update_status(f"写入单个: 注入按键 [{key_text}] 完成")

    # ========================================================================
    # 3. 键位去重
    # ========================================================================

    def _dedup_against_preset(self):
        if not self._json_str:
            self._update_status("请先加载 JSON 文件")
            return
        preset_path = self._get_preset_path()
        if not os.path.isfile(preset_path):
            self._update_status("预设文件不存在，请先选择基础键位")
            return

        try:
            _, preset_obj = read_json_file(preset_path)
            preset_keys = self._scheme_keymaps(preset_obj)
            target = self._scheme_keymaps(self._json_str)
            identities = {identity for km in preset_keys
                          if (identity := self._key_identity(km)) is not None}
            indexes = [i for i, km in enumerate(target)
                       if self._key_identity(km) in identities]
        except Exception as exc:
            self._update_status(f"读取按键数据失败: {exc}")
            return
        if not indexes:
            self._update_status("键位去重: 无重复键位")
            return
        if not MessageDialog.question(self, "确认键位去重",
                               f"将删除当前方案中与预设重复的 {len(indexes)} 个按键，是否继续？",
                               confirm="确认", cancel="取消", danger=True):
            return
        try:
            updated = json_editor.delete_keys_by_indexes(self._json_str, indexes)
        except Exception as exc:
            self._update_status(f"键位去重失败: {exc}")
            return
        if self._commit_candidate(updated):
            self._update_status(f"键位去重: 删除 {len(indexes)} 个重复按键")

    # ========================================================================
    # 4. 右下清空
    # ========================================================================

    def _clear_range_rd(self):
        if not self._json_str:
            self._update_status("请先加载 JSON 文件")
            return

        try:
            keymaps = self._scheme_keymaps(self._json_str)
            indexes = []
            for index, item in enumerate(keymaps):
                icon = item.get("icon") if isinstance(item, dict) else None
                position = icon.get("rel_position") if isinstance(icon, dict) else None
                if not isinstance(position, dict):
                    continue
                x, y = float(position["rel_x"]), float(position["rel_y"])
                if not math.isfinite(x) or not math.isfinite(y):
                    raise ValueError("按键坐标不是有限数")
                # 保留原右下区域开区间，按原数组索引删除，VK0 与区外同键互不影响。
                if 0.661 < x < 1.0 and 0.798 < y < 1.0:
                    indexes.append(index)
        except Exception as exc:
            self._update_status(f"右下清空: 读取按键失败: {exc}")
            return
        if not indexes:
            self._update_status("右下清空: 该区域无按键")
            return
        if not MessageDialog.question(self, "确认右下清空",
                               f"此功能针对 16:9 方案，将清空右下区域的 {len(indexes)} 个按键。是否继续？\n"
                               "其他比例请先核对区域；成功后可通过主窗口撤销。",
                               confirm="确认", cancel="取消", danger=True):
            return
        try:
            updated = json_editor.delete_keys_by_indexes(self._json_str, indexes)
        except Exception as exc:
            self._update_status(f"右下清空失败: {exc}")
            return
        if self._commit_candidate(updated):
            self._update_status(f"右下清空: 清空 {len(indexes)} 个按键")

    # ========================================================================
    # 5. 导入当前 JSON 到预设库
    # ========================================================================

    def _import_to_preset(self):
        if not self._json_str:
            self._update_status("请先加载 JSON 文件")
            return
        if not self._json_path:
            self._update_status("主窗口未加载文件")
            return

        filename = self._strip_package_name(os.path.basename(self._json_path))
        if not filename or filename.lower() == ".json" or not filename.lower().endswith(".json"):
            self._update_status("无效的文件名")
            return

        dest = os.path.join(self._presets_dir, filename)
        try:
            self._scheme_keymaps(self._json_str)
            if not self._confirm_preset_overwrite(dest):
                return
            os.makedirs(self._presets_dir, exist_ok=True)
            if not write_text_atomic(dest, self._json_str):
                self._update_status("导入失败: 写入预设文件出错（详见日志）")
                return
        except Exception as exc:
            self._update_status(f"导入失败: {exc}")
            return

        self._refresh_preset_list()
        self._preset_cb.setCurrentText(filename)
        self._update_status(f"已导入预设: {filename}")

    # ========================================================================
    # 6. 删除预设文件
    # ========================================================================

    def _delete_preset(self):
        if not os.path.isdir(self._presets_dir):
            self._update_status("预设目录不存在")
            return

        try:
            files = sorted([
                f for f in os.listdir(self._presets_dir)
                if f.lower().endswith(".json")
            ])
        except Exception:
            self._update_status("读取预设目录失败")
            return

        if not files:
            self._update_status("预设目录为空")
            return

        item, ok = MessageDialog.get_item(
            self, "删除预设", "选择要删除的预设文件:", files,
            files.index(self._preset_cb.currentText()) if self._preset_cb.currentText() in files else 0,
            False, confirm="选择",
        )
        if not ok or not item:
            return

        reply = MessageDialog.question(
            self, "确认删除",
            f"确定要删除预设文件 [{item}] 吗？此操作不可撤销。",
            confirm="删除预设", cancel="取消", danger=True,
        )
        if not reply:
            return

        try:
            os.remove(os.path.join(self._presets_dir, item))
        except Exception as e:
            self._update_status(f"删除失败: {e}")
            return

        self._refresh_preset_list()
        self._update_status(f"已删除预设: {item}")

    # ========================================================================
    # r1 按钮：打开预设文件
    # ========================================================================

    def _on_open_preset(self):
        try:
            os.makedirs(self._presets_dir, exist_ok=True)
        except OSError as exc:
            self._update_status(f"打开预设失败: {exc}")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "选择预设文件", self._presets_dir,
            "JSON 文件 (*.json);;所有文件 (*)",
        )
        if not path:
            return

        filename = os.path.basename(path)
        dest = os.path.join(self._presets_dir, filename)
        try:
            if not filename.lower().endswith(".json"):
                raise ValueError("请选择 JSON 预设文件")
            # 保留 BOM 和原换行；解析时兼容 BOM，不改写源预设。
            with open(path, "r", encoding="utf-8", newline="") as src:
                content = src.read()
            self._scheme_keymaps(content)
            if os.path.normcase(os.path.realpath(path)) != os.path.normcase(os.path.realpath(dest)):
                if not self._confirm_preset_overwrite(dest):
                    return
                if not write_text_atomic(dest, content):
                    self._update_status("导入预设失败: 写入出错（详见日志）")
                    return
        except Exception as exc:
            self._update_status(f"读取或导入预设失败: {exc}")
            return

        self._refresh_preset_list()
        self._preset_cb.setCurrentText(filename)
        self._update_status(f"已打开预设: {filename}")

    def _on_open_presets_directory(self):
        """保留选择导入，同时补齐原版打开预设目录的入口。"""
        try:
            os.makedirs(self._presets_dir, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(self._presets_dir)):
                raise OSError("系统未能打开预设目录")
        except Exception as exc:
            self._update_status(f"打开预设目录失败: {exc}")

    # ========================================================================
    # r1 按钮：删除当前预设
    # ========================================================================

    def _on_delete_preset_r1(self):
        preset_name = self._preset_cb.currentText()
        if not preset_name:
            self._update_status("请先选择基础键位")
            return
        preset_path = os.path.join(self._presets_dir, preset_name)

        reply = MessageDialog.question(
            self, "确认删除",
            f"确定要删除预设文件 [{preset_name}] 吗？此操作不可撤销。",
            confirm="删除预设", cancel="取消", danger=True,
        )
        if not reply:
            return

        if os.path.isfile(preset_path):
            try:
                os.remove(preset_path)
            except Exception as e:
                self._update_status(f"删除文件失败: {e}")
                return

        self._refresh_preset_list()
        self._update_status(f"已删除预设: {preset_name}")

    # ========================================================================
    # r2 按钮：从预设读取单个按键坐标并回填主窗口
    # ========================================================================

    def _on_read_from_preset(self):
        key_text = self._captured_key
        if not key_text:
            self._update_status("请先捕获按键")
            return
        preset_path = self._get_preset_path()
        if not os.path.isfile(preset_path):
            self._update_status("预设文件不存在，请先选择基础键位")
            return

        try:
            preset_json, preset_obj = read_json_file(preset_path)
            self._scheme_keymaps(preset_obj)
            vk = self._captured_vk
            coordinates = json_editor.try_read_key_coordinates(
                preset_json, vk=vk if self._valid_vk(vk) else None,
                key_text=None if self._valid_vk(vk) else key_text)
            if coordinates is None:
                self._update_status(f"预设中未找到按键 [{key_text}] 的可读坐标 (VK: {vk})")
                return
            if self._apply_coord_cb is None:
                self._update_status("读取失败: 主窗口未设置坐标回填回调")
                return
            rel_x, rel_y = coordinates
            self._apply_coord_cb(rel_x, rel_y)
        except Exception as exc:
            self._update_status(f"读取或回填坐标失败: {exc}")
            return
        self._update_status(f"已读取按键 [{key_text}] (VK: {vk}) 坐标: {rel_x}, {rel_y}")

    # ========================================================================
    # 内部辅助
    # ========================================================================

    @staticmethod
    def _valid_vk(vk):
        return isinstance(vk, int) and not isinstance(vk, bool) and vk > 0

    @classmethod
    def _key_identity(cls, item):
        key = item.get("key") if isinstance(item, dict) else None
        if not isinstance(key, dict):
            return None
        vk = key.get("virtual_key")
        if cls._valid_vk(vk):
            return ("vk", vk)
        text = key.get("text")
        return ("text", text) if isinstance(text, str) and text else None

    @staticmethod
    def _scheme_keymaps(value):
        if isinstance(value, str):
            value = json.loads(value.lstrip("\ufeff"))
        if not isinstance(value, dict) or not isinstance(value.get("keymaps"), list):
            raise ValueError("JSON 必须为对象且 keymaps 必须为数组")
        # 拒绝非有限数，避免把输入错误误当合法空数据或写入新预设。
        json.dumps(value, allow_nan=False)
        return value["keymaps"]

    def _confirm_preset_overwrite(self, path):
        if not os.path.exists(path):
            return True
        return MessageDialog.question(
            self, "确认覆盖预设", f"预设文件 [{os.path.basename(path)}] 已存在，是否覆盖？\n原预设文件内容将被替换。",
            confirm="覆盖", cancel="取消", danger=True)

    def _commit_candidate(self, candidate):
        if not candidate:
            self._update_status("更新失败: 无法构造按键数据")
            return False
        if candidate == self._json_str:
            self._update_status("按键数据无变化")
            return False
        before = self._json_str
        if not self._write_json_to_file(candidate):
            self._json_str = before
            return False
        self._json_str = candidate
        return True

    def _write_json_to_file(self, candidate=None):
        """先由主窗原子写回；明确失败时不推进本地状态，兼容历史 None 回调。"""
        if self._sync_cb is None:
            self._update_status("写回失败: 主窗口未设置保存回调")
            return False
        try:
            if self._sync_cb(self._json_str if candidate is None else candidate) is False:
                self._update_status("写回失败: 主窗口未保存，按键数据未提交")
                return False
        except Exception as exc:
            self._update_status(f"写回失败: {exc}")
            return False
        return True

    def _update_status(self, message: str):
        self.status_bar.showMessage(message, 5000)


    def _preset_card(self):
        card = QFrame()
        card.setObjectName("PresetCandidateCard")
        card.setStyleSheet(
            f"QFrame#PresetCandidateCard {{background: {theme.CARD};"
            f"border: 1px solid {theme.LINE}; border-radius: 8px;}}")
        return card

    def _preset_group(self, title, actions):
        card = self._preset_card()
        row = QHBoxLayout(card)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)
        label = QLabel(title)
        label.setFixedWidth(32)
        label.setStyleSheet(f"color: {theme.VIOLET_700}; font-weight: bold; background: transparent;")
        row.addWidget(label)
        for text, role, method_name in actions:
            button = RoundedButton(text, role)
            button.setFixedHeight(theme.H_BTN)
            if method_name == "_on_open_preset":
                menu = QMenu(button)
                menu.addAction("选择并导入预设文件", self._on_open_preset)
                menu.addAction("打开预设文件夹", self._on_open_presets_directory)
                button.setMenu(menu)
            else:
                button.clicked.connect(getattr(self, method_name))
            self._preset_actions[method_name] = button
            row.addWidget(button, 1)
        return card

    def _update_preset_hint(self, *_):
        name = self._preset_cb.currentText()
        self._preset_cb.setToolTip(name or "暂无预设")
        if hasattr(self, "_preset_hint"):
            text = "当前预设：" + name if name else "暂无预设，可通过管理中的「导入」或「打开」添加。"
            # 按布局后的真实内容区和当前字体省略，不用窗口宽度估算标签宽度。
            width = max(0, self._preset_hint.contentsRect().width() - 2 * self._preset_hint.margin()
                        - max(0, self._preset_hint.indent()))
            self._preset_hint.setText(self._preset_hint.fontMetrics().elidedText(
                text, Qt.ElideMiddle, width))
            self._preset_hint.setToolTip(text)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "_preset_hint", None) and event.type() in (
                QEvent.Resize, QEvent.Show, QEvent.FontChange, QEvent.StyleChange, QEvent.ContentsRectChange):
            self._update_preset_hint()
        return super().eventFilter(watched, event)
