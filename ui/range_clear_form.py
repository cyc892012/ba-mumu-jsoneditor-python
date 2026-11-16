"""
区域键位清空
"""
import json
import math
from PyQt5.QtWidgets import QHBoxLayout, QGridLayout, QLabel, QLineEdit, QMessageBox, QWidget, QSizePolicy
from PyQt5.QtCore import Qt, QRectF, QTimer, pyqtSignal
from PyQt5.QtGui import QDoubleValidator, QFont, QColor, QPainter, QPen
from .base_dialog import BaseDialog
from .wide_screen_form import _ElidedLabel
from controls import RoundedButton, CardPanel, HintLabel
from core.json_editor import delete_keys_by_indexes
from core.coord_model import rel_to_px
from utils.safe_parse import try_parse_float
import theme


class RangeClearForm(BaseDialog):
    def __init__(self, parent=None):
        super().__init__(parent, "区域键位清空")
        self.setFixedSize(480, 310)
        self.setFont(QFont("Microsoft YaHei UI", 9))

        # 控制引用
        self._x1 = None
        self._y1 = None
        self._x2 = None
        self._y2 = None
        self._clear_btn = None
        self._status_lbl = None

        # 状态
        self._json_str = None
        self._json_path = ""
        self._screen_w = 1280
        self._screen_h = 720
        self._sync_cb = None  # 写回文件后通知主窗口
        self._res_getter = None

        self._build()

    def _build(self):
        self.status_bar.setSizeGripEnabled(False)
        ct = self.content_layout
        ct.setContentsMargins(8, 6, 8, 6)
        ct.setSpacing(5)
        ct.addWidget(HintLabel("按像素指定选区；清空前核对数量，默认范围为全屏。", "info"))
        row = QHBoxLayout()
        row.setSpacing(6)
        inputs = CardPanel("清空范围 · 像素")
        grid = QGridLayout()
        grid.setSpacing(4)
        grid.addWidget(QLabel(""), 0, 0)
        grid.addWidget(QLabel("X"), 0, 1)
        grid.addWidget(QLabel("Y"), 0, 2)
        edits = []
        for line, title, values in ((1, "左上", ("0", "0")), (2, "右下", ("1280", "720"))):
            grid.addWidget(QLabel(title), line, 0)
            for col, value in enumerate(values, 1):
                edit = QLineEdit(value)
                edit.setMinimumWidth(62)
                edit.setValidator(QDoubleValidator(edit))
                edit.textChanged.connect(edit.setToolTip)
                grid.addWidget(edit, line, col)
                edits.append(edit)
        self._x1, self._y1, self._x2, self._y2 = edits
        inputs.content_layout.addLayout(grid)
        self._resolution_label = _ElidedLabel()
        inputs.content_layout.addWidget(self._resolution_label)
        text = QLabel("反向坐标自动排序；越过屏幕时含边缘。")
        text.setWordWrap(True)
        text.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        inputs.content_layout.addWidget(text)
        inputs.content_layout.addStretch()
        row.addWidget(inputs, 1)
        preview = CardPanel("区域预览 · 16:9")
        self._thumbnail = _RegionThumbnail()
        self._thumbnail.bounds_changed.connect(self._on_preview_bounds_changed)
        self._thumbnail.reset_requested.connect(self._reset_preview_range)
        preview.content_layout.addWidget(self._thumbnail, 1)
        legend = QLabel("粉框选区 · 红点删除 · 紫点保留")
        legend.setWordWrap(True)
        preview.content_layout.addWidget(legend)
        row.addWidget(preview, 1)
        ct.addLayout(row, 1)
        actions = QHBoxLayout()
        self._count_label = _ElidedLabel("待删除 0 个按键")
        self._count_label.setWordWrap(True)
        self._count_label.setMinimumHeight(26)
        actions.addWidget(self._count_label, 1)
        self._clear_btn = RoundedButton("区域清空", "danger")
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        actions.addWidget(self._clear_btn)
        ct.addLayout(actions)
        self._status_lbl = _ElidedLabel("将清空 0 个按键")
        self.status_bar.addWidget(self._status_lbl, 1)
        for edit in edits:
            edit.textChanged.connect(self._on_input_changed)
        self._update_count()
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(250)
        self._preview_timer.timeout.connect(self._update_count)

    # ── 公开接口 ──

    def set_json_data(self, json_str):
        """设置当前已加载的 JSON 键位数据"""
        self._json_str = json_str
        self._update_count()

    def set_json_path(self, json_path):
        """设置当前 JSON 文件路径"""
        self._json_path = json_path

    def set_sync_callback(self, callback):
        """设置同步回调：写回文件后通知主窗口刷新"""
        self._sync_cb = callback

    def set_resolution(self, w, h):
        """设置屏幕分辨率（用于像素坐标→相对坐标转换）"""
        self._screen_w = w
        self._screen_h = h
        # 同步右下角输入框默认值为当前分辨率的最大合法像素（screen-1 → rel 1.0）
        if self._x2 is not None and self._y2 is not None:
            self._x2.setText(str(max(w - 1, 0)))
            self._y2.setText(str(max(h - 1, 0)))
        self._update_count()

    def set_resolution_getter(self, callback):
        """注入主窗口当前尺寸；每次预览/删除读取同一份有效快照。"""
        self._res_getter = callback
        self._update_count()

    def _get_resolution(self):
        values = self._res_getter() if self._res_getter else (self._screen_w, self._screen_h)
        w, h = (float(value) for value in values)
        if not all(math.isfinite(v) and v > 1 and v.is_integer() for v in (w, h)):
            raise ValueError("分辨率须为大于1的整数")
        return w, h

    # ── 内部逻辑 ──

    def _on_clear_clicked(self):
        if not self._json_str:
            QMessageBox.warning(self, "提示", "请先在主窗口加载 JSON 键位文件。")
            return

        original = self._json_str
        try:
            keys = self.get_pending_keys()
            signature = self._selection_signature()
        except (ValueError, TypeError, KeyError, OverflowError) as ex:
            self.status_bar.showMessage(f"无法清空: {ex}", 5000)
            return
        count = len(keys)
        if count == 0:
            QMessageBox.information(self, "提示", "所选区域内未找到任何按键。")
            return

        reply = QMessageBox.question(
            self, "确认清空",
            f"确认清空区域内 {count} 个按键？请保留仍需使用的键位。\n"
            "成功保存后可在主窗口撤销。范围内边界为开区间；越过屏幕的边界包含屏幕边缘。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            if self._json_str != original or self._selection_signature() != signature:
                raise ValueError("确认期间数据或分辨率已变化，请重新核对")
            if self._sync_cb is None:
                raise ValueError("未连接主窗口保存接口")
            updated = delete_keys_by_indexes(original, [index for index, _ in keys])
            if updated == original:
                raise ValueError("删除未生成有效结果")
            if self._sync_cb(updated) is False:
                raise ValueError("写回文件失败")
        except Exception as ex:
            self._json_str = original
            self.status_bar.showMessage(f"清空失败: {ex}", 5000)
            return
        self._json_str = updated
        self._update_count()
        names = "、".join(name for _, name in keys)
        message = f"已清空 {count} 个按键: {names}"
        self._status_lbl.setToolTip(message)
        self.status_bar.setToolTip(message)
        self.status_bar.showMessage(message, 5000)

    def _on_input_changed(self):
        self._update_count()

    def _on_preview_bounds_changed(self, bounds):
        try:
            width, height = self._get_resolution()
        except (ValueError, TypeError, OverflowError) as ex:
            self.status_bar.showMessage(f"无法调整范围: {ex}", 5000)
            return
        # 半像素附近消除一次浮点乘法的尾差，保持四舍五入而非银行家舍入。
        values = [int(math.floor(math.nextafter(min(1, max(0, value)) * limit + 0.5, math.inf)))
                  for value, limit in zip(bounds, (width - 1, height - 1, width - 1, height - 1))]
        edits = (self._x1, self._y1, self._x2, self._y2)
        if tuple(edit.text() for edit in edits) == tuple(str(value) for value in values):
            return
        # 四个字段作为一次更新提交，避免中间范围触发反向重算。
        blocked = [edit.blockSignals(True) for edit in edits]
        try:
            for edit, value in zip(edits, values):
                edit.setText(str(value))
                edit.setToolTip(str(value))
        finally:
            for edit, previous in zip(edits, blocked):
                edit.blockSignals(previous)
        self.status_bar.clearMessage()
        self._update_count()

    def _reset_preview_range(self):
        self._on_preview_bounds_changed((0, 0, 1, 1))

    def _update_count(self):
        # 数量与危险确认/实际删除共用已修复的原数组索引，禁止按VK扩散预演。
        try:
            bounds = self._read_coords()
            matches = self.get_pending_keys() if self._json_str else []
            before = json.loads(self._json_str).get("keymaps", []) if self._json_str else []
            selected = {index for index, _ in matches}
            self._pending_delete_count = self._range_hit_count = len(selected)
            self._count_label.setText(f"待删除 {len(selected)} 个按键 · 命中 {len(matches)} 个")
            self._status_lbl.setText(f"将清空 {len(selected)} 个按键")
            width, height = self._get_resolution()
            self._resolution_label.setText(f"当前分辨率 {int(width)} × {int(height)}")
            points = []
            for index, item in enumerate(before):
                if not isinstance(item, dict):
                    continue
                icon = item.get("icon")
                pos = icon.get("rel_position") if isinstance(icon, dict) else None
                if isinstance(pos, dict):
                    points.append((rel_to_px(float(pos["rel_x"]), width) / (width - 1),
                                   rel_to_px(float(pos["rel_y"]), height) / (height - 1), index in selected))
            self._thumbnail.set_preview(bounds, points, bool(before))
        except (ValueError, TypeError, KeyError, OverflowError) as ex:
            self._pending_delete_count = self._range_hit_count = 0
            self._count_label.setText("无法预览，请检查输入")
            self._count_label.setToolTip(str(ex))
            self._status_lbl.setText(f"无法预览: {ex}")
            self._thumbnail.set_preview((0, 0, 0, 0), [], False)

    def _selection_signature(self):
        return (self._get_resolution(), *(edit.text() for edit in (self._x1, self._y1, self._x2, self._y2)))

    def get_pending_keys(self):
        """返回真实命中数组索引和名称，供计数、确认、删除及隔离预览共用。"""
        self._read_coords()
        obj = json.loads(self._json_str or '{"keymaps": []}')
        if not isinstance(obj, dict) or not isinstance(obj.get("keymaps", []), list):
            raise ValueError("键位 JSON 的 keymaps 必须为数组")
        width, height = self._get_resolution()
        # 直接使用已验证的像素输入，避免画布归一化往返改变严格开区间边界。
        x1, x2 = sorted(min(width - 1, max(0.0, float(edit.text()))) for edit in (self._x1, self._x2))
        y1, y2 = sorted(min(height - 1, max(0.0, float(edit.text()))) for edit in (self._y1, self._y2))
        edges = self._include_edges
        matches = []
        for index, item in enumerate(obj.get("keymaps", [])):
            if not isinstance(item, dict):
                continue
            icon = item.get("icon")
            pos = icon.get("rel_position") if isinstance(icon, dict) else None
            if not isinstance(pos, dict):
                continue
            try:
                x, y = float(pos["rel_x"]), float(pos["rel_y"])
            except (KeyError, ValueError, TypeError) as ex:
                raise ValueError("键位坐标格式错误") from ex
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError("键位坐标不是有限数")
            x, y = rel_to_px(x, width), rel_to_px(y, height)
            inside_x = (x1 < x or (edges[0] and x == 0)) and (x < x2 or (edges[2] and x == width - 1))
            inside_y = (y1 < y or (edges[1] and y == 0)) and (y < y2 or (edges[3] and y == height - 1))
            if inside_x and inside_y:
                key = item.get("key")
                name = str(key.get("text", "未命名")) if isinstance(key, dict) else "未命名"
                matches.append((index, name))
        return matches

    def _read_coords(self):
        """连续像素范围归一化到缩略图画布；此处是区域边界，不能吸附成键位格点。

        命中时将边界还原为连续像素，与 rel_to_px 得到的真实落点比较。
        """
        ok_x1, vx1 = try_parse_float(self._x1.text())
        ok_y1, vy1 = try_parse_float(self._y1.text())
        ok_x2, vx2 = try_parse_float(self._x2.text())
        ok_y2, vy2 = try_parse_float(self._y2.text())

        if not all((ok_x1, ok_y1, ok_x2, ok_y2)):
            raise ValueError("请完整输入四个有限数值坐标")
        width, height = self._get_resolution()
        w, h = width - 1, height - 1
        x1, x2 = sorted((vx1 / w, vx2 / w))
        y1, y2 = sorted((vy1 / h, vy2 / h))
        self._include_edges = (x1 < 0, y1 < 0, x2 > 1, y2 > 1)

        # clamp 到合法相对坐标范围 [0,1]
        x1 = min(max(x1, 0.0), 1.0)
        y1 = min(max(y1, 0.0), 1.0)
        x2 = min(max(x2, 0.0), 1.0)
        y2 = min(max(y2, 0.0), 1.0)

        # 保证左上 < 右下
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1

        return x1, y1, x2, y2


    def showEvent(self, event):
        super().showEvent(event)
        self._update_count()
        self._preview_timer.start()

    def hideEvent(self, event):
        self._preview_timer.stop()
        super().hideEvent(event)


def _in(default):
    inp = QLineEdit(default); inp.setFixedWidth(55)
    inp.setValidator(QDoubleValidator(inp))
    return inp


class _RegionThumbnail(QWidget):
    """按实际画布交互，输出归一化范围；筛选与像素取整由表单统一负责。"""

    bounds_changed = pyqtSignal(object)
    reset_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bounds = (0, 0, 1, 1)
        self.points = []
        self.has_data = False
        self._drag_mode = None
        self._hover_mode = "new"
        self._dragging = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setMinimumSize(170, 96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName("16:9 区域清空缩略图")

    def set_preview(self, bounds, points, has_data):
        self.bounds, self.points, self.has_data = bounds, points, has_data
        self.setToolTip("拖动画框；框内移动，边角调整；双击恢复全屏。红点为实际待删除按键。")
        self.setAccessibleDescription(f"相对区域：{bounds}；实际待删除点数：{sum(p[2] for p in points)}")
        self.update()

    def canvas_rect(self):
        area = QRectF(self.contentsRect()).adjusted(4, 4, -4, -4)
        width = min(area.width(), area.height() * 16 / 9)
        height = width * 9 / 16
        return QRectF(area.center().x() - width / 2, area.center().y() - height / 2, width, height)

    def _region_rect(self):
        canvas = self.canvas_rect()
        x1, y1, x2, y2 = self.bounds
        return QRectF(canvas.left() + x1 * canvas.width(), canvas.top() + y1 * canvas.height(),
                      (x2 - x1) * canvas.width(), (y2 - y1) * canvas.height())

    def _hit_test(self, pos):
        if not self.canvas_rect().contains(pos):
            return "new"
        region = self._region_rect()
        if not region.adjusted(-6, -6, 6, 6).contains(pos):
            return "new"
        # 窄框两边同时命中时，取最近的边；角优先于边，边优先于内部。
        horizontal = min(((abs(pos.x() - region.left()), "l"),
                          (abs(pos.x() - region.right()), "r")))
        vertical = min(((abs(pos.y() - region.top()), "t"),
                        (abs(pos.y() - region.bottom()), "b")))
        edges = (horizontal[1] if horizontal[0] <= 6 else "") + (vertical[1] if vertical[0] <= 6 else "")
        return edges or ("move" if region.contains(pos) else "new")

    def _update_cursor(self, pos):
        mode = self._hit_test(pos)
        self._hover_mode = mode
        cursor = {"new": Qt.CrossCursor, "move": Qt.SizeAllCursor,
                  "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
                  "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
                  "lt": Qt.SizeFDiagCursor, "rb": Qt.SizeFDiagCursor,
                  "rt": Qt.SizeBDiagCursor, "lb": Qt.SizeBDiagCursor}[mode]
        self.setCursor(cursor)
        self.update()

    def _relative_pos(self, pos):
        canvas = self.canvas_rect()
        return (min(1, max(0, (pos.x() - canvas.left()) / canvas.width())),
                min(1, max(0, (pos.y() - canvas.top()) / canvas.height())))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.canvas_rect().contains(event.localPos()):
            self._press_pos = event.localPos()
            self._drag_bounds = self.bounds
            self._drag_mode = self._hit_test(event.localPos())
            self._dragging = False
            event.accept()
        else:
            super().mousePressEvent(event)

    def _drag_to(self, pos):
        if not self._dragging:
            if math.hypot(pos.x() - self._press_pos.x(), pos.y() - self._press_pos.y()) < 4:
                return
            self._dragging = True
        x, y = self._relative_pos(pos)
        x1, y1, x2, y2 = self._drag_bounds
        mode = self._drag_mode
        if mode == "new":
            x1, y1 = self._relative_pos(self._press_pos)
            x2, y2 = x, y
        elif mode == "move":
            canvas = self.canvas_rect()
            dx = min(1 - x2, max(-x1, (pos.x() - self._press_pos.x()) / canvas.width()))
            dy = min(1 - y2, max(-y1, (pos.y() - self._press_pos.y()) / canvas.height()))
            x1, x2, y1, y2 = x1 + dx, x2 + dx, y1 + dy, y2 + dy
        else:
            # 使用按下时的对边作锚点，越过对边后自然翻转，不在反馈取整后累积误差。
            if "l" in mode:
                x1 = x
            if "r" in mode:
                x2 = x
            if "t" in mode:
                y1 = y
            if "b" in mode:
                y2 = y
        bounds = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self.bounds_changed.emit(bounds)

    def mouseMoveEvent(self, event):
        if self._drag_mode is not None and event.buttons() & Qt.LeftButton:
            self._drag_to(event.localPos())
        else:
            self._update_cursor(event.localPos())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_mode is not None:
            self._drag_to(event.localPos())
            self._drag_mode = None
            self._dragging = False
            self._update_cursor(event.localPos())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_mode = None
            self._dragging = False
            self.reset_requested.emit()
            self._update_cursor(event.localPos())
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event):
        self._hover_mode = "new"
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        canvas = self.canvas_rect()
        painter.setPen(QPen(QColor(theme.LINE_STRONG), 1))
        painter.setBrush(QColor(theme.CARD))
        painter.drawRoundedRect(canvas, 5, 5)
        painter.save()
        painter.setClipRect(canvas)
        painter.setPen(QPen(QColor(theme.LINE), 1, Qt.DotLine))
        for factor in (0.25, 0.5, 0.75):
            painter.drawLine(int(canvas.left() + canvas.width() * factor), int(canvas.top()),
                             int(canvas.left() + canvas.width() * factor), int(canvas.bottom()))
            painter.drawLine(int(canvas.left()), int(canvas.top() + canvas.height() * factor),
                             int(canvas.right()), int(canvas.top() + canvas.height() * factor))
        x1, y1, x2, y2 = self.bounds
        region = QRectF(canvas.left() + x1 * canvas.width(), canvas.top() + y1 * canvas.height(),
                        (x2 - x1) * canvas.width(), (y2 - y1) * canvas.height())
        painter.setPen(QPen(QColor(theme.PINK_700), 2.5 if self._hover_mode != "new" else 2))
        painter.setBrush(QColor(244, 175, 195, 65))
        painter.drawRect(region)
        for x, y, deleting in self.points:
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.RED if deleting else theme.VIOLET))
            painter.drawEllipse(QRectF(canvas.left() + x * canvas.width() - 3,
                                      canvas.top() + y * canvas.height() - 3, 6, 6))
        painter.restore()
        if not self.has_data:
            painter.setPen(QColor(theme.TEXT_SECONDARY))
            painter.drawText(canvas, Qt.AlignCenter, "暂无键位数据")

