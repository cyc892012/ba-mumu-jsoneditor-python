"""
可搜索下拉框 — 点击展开时浮现搜索框，输入文字实时筛选列表项
"""
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QComboBox, QLabel
from PyQt5.QtCore import Qt, pyqtSignal


class _SearchEdit(QLineEdit):
    """搜索框 — 支持 Enter 收起下拉、Up/Down 移动选中项（对应 C# 搜索框键盘导航）"""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner
        # 空态放在搜索框内，不向真实方案模型插入可误提交的占位行。
        self._empty_hint = QLabel("无匹配结果", self)
        self._empty_hint.setStyleSheet("QLabel {color:#8A5A10;background:transparent;font-size:10px;padding:0 2px;}")
        self._empty_hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._empty_hint.hide()

    def setEmptyResult(self, empty):
        self._empty_hint.setVisible(empty)
        width = self._empty_hint.sizeHint().width() + 4
        self.setTextMargins(0, 0, width + 24 if empty else 0, 0)
        self._empty_hint.setGeometry(max(0, self.width() - width - 24), 0, width, self.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setEmptyResult(not self._empty_hint.isHidden())

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # 回车 = 确认选中当前高亮项，而不是单纯关掉下拉。
            # 否则用户搜到文件后按回车，界面上什么都不会发生（文件没切换、
            # 自动同步也没取消），看起来就像「切换失效 + 文件栏空白」。
            combo = self._owner._combo
            idx = self._owner._first_visible_index(combo.currentIndex())
            if idx >= 0:
                combo.setCurrentIndex(idx)      # 触发正常加载链路
                combo.activated.emit(idx)       # 触发「用户选择」逻辑（取消自动同步）
            combo.hidePopup()
            combo.setFocus()
            event.accept()
            return
        if event.key() == Qt.Key_Down:
            self._owner._move_visible(1)
            event.accept()
            return
        if event.key() == Qt.Key_Up:
            self._owner._move_visible(-1)
            event.accept()
            return
        super().keyPressEvent(event)


class SearchableComboBox(QWidget):
    """内嵌搜索框的下拉框控件。

    搜索框默认隐藏，下拉展开时自动浮现于下拉框上方并获取焦点；
    下拉收起后搜索框隐藏但文字保留，方便下次继续筛选。
    对外 API 与 QComboBox 兼容。
    """

    currentIndexChanged = pyqtSignal(int)
    currentTextChanged = pyqtSignal(str)
    editTextChanged = pyqtSignal(str)
    activated = pyqtSignal(int)
    popupAboutToShow = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_items = []  # [(text, data), ...]
        self._popup_is_visible = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._combo = QComboBox()
        self._combo.setEditable(False)

        layout.addWidget(self._combo)

        # 搜索框：稍后在首次显示时创建并挂到顶层窗口上
        self._search = None

        # 截获下拉框展开/收起 — monkey-patch 虚拟方法
        self._combo._orig_show_popup = self._combo.showPopup
        self._combo._orig_hide_popup = self._combo.hidePopup
        self._combo.showPopup = self._on_popup_show
        self._combo.hidePopup = self._on_popup_hide

        # 转发信号
        self._combo.currentIndexChanged.connect(self.currentIndexChanged)
        self._combo.currentTextChanged.connect(self.currentTextChanged)
        self._combo.editTextChanged.connect(self.editTextChanged)
        self._combo.activated.connect(self.activated)

    def _ensure_search(self):
        """惰性创建搜索框，挂载到顶层窗口以便浮现在组合框上方"""
        if self._search is not None:
            return
        self._search = _SearchEdit(self, self.window())
        self._search.setPlaceholderText("搜索...")
        self._search.setClearButtonEnabled(True)
        self._search.hide()
        self._search.textChanged.connect(self._apply_filter)

    def _position_and_show_search(self):
        """将搜索框定位在组合框正上方（顶层窗口坐标系），等宽对齐"""
        self._ensure_search()
        global_pt = self._combo.mapToGlobal(self._combo.rect().topLeft())
        local_pt = self._search.parent().mapFromGlobal(global_pt)
        h = self._search.sizeHint().height()
        self._search.setGeometry(local_pt.x(), local_pt.y() - h,
                                  self._combo.width(), h)
        self._search.show()
        self._search.raise_()
        self._search.setFocus()

    def _hide_search(self):
        """隐藏搜索框并恢复完整列表"""
        if self._search is not None:
            self._search.hide()
        self._repopulate("")

    def hideEvent(self, event):
        """控件隐藏时同步关闭搜索浮层"""
        if self._search is not None:
            self._search.hide()
        super().hideEvent(event)

    def _on_popup_show(self):
        """下拉展开时：定位并显示搜索框，应用当前过滤文字"""
        self.popupAboutToShow.emit()
        self._popup_is_visible = True
        self._position_and_show_search()
        self._apply_filter(self._search.text() if self._search else "")
        self._combo._orig_show_popup()

    def _on_popup_hide(self):
        """下拉收起时：隐藏搜索框，恢复完整列表"""
        self._popup_is_visible = False
        self._hide_search()
        self._combo._orig_hide_popup()

    def _apply_filter(self, text):
        """根据搜索文字重新填充下拉列表"""
        self._repopulate(text)

    def _visible_rows(self):
        """当前未被过滤隐藏的行号列表"""
        view = self._combo.view()
        return [r for r in range(self._combo.count()) if not view.isRowHidden(r)]

    def _first_visible_index(self, prefer=-1):
        """可选中的行：优先 prefer（若可见），否则第一个可见行"""
        view = self._combo.view()
        n = self._combo.count()
        if 0 <= prefer < n and not view.isRowHidden(prefer):
            return prefer
        for r in range(n):
            if not view.isRowHidden(r):
                return r
        return -1

    def _move_visible(self, direction):
        """键盘导航只选择当前可见项。"""
        current = self._combo.currentIndex()
        rows = self._visible_rows()
        candidates = [r for r in rows if r > current] if direction > 0 else [r for r in rows if r < current]
        if candidates:
            self._combo.setCurrentIndex(candidates[0] if direction > 0 else candidates[-1])

    def _repopulate(self, filter_text):
        """按过滤条件**显示/隐藏行**（不再 clear + addItem 重建模型）。

        这里是本控件最容易踩的坑，历史实现必须保留说明：
        旧做法是 ``clear()`` 后逐个 ``addItem()``。而 Qt 处理「鼠标点击下拉项」
        的顺序是 **先 hidePopup、再应用选中行**——模型在 hidePopup 里被重建，
        待应用的 QModelIndex 立刻失效，结果 ``currentIndex`` 被置为 -1
        （下拉框显示一片空白），并且 ``activated`` 信号不再触发
        （于是「手动选择取消自动同步」这类逻辑全部失效）。
        改成隐藏行后模型不再变动，Qt 的选中流程可以正常走完。
        """
        filter_lower = (filter_text or "").lower()
        view = self._combo.view()
        n = self._combo.count()
        shown = 0
        for row in range(n):
            text = self._combo.itemText(row) or ""
            hit = (not filter_lower) or (filter_lower in text.lower())
            view.setRowHidden(row, not hit)
            if hit:
                shown += 1
        if self._search is not None:
            self._search.setEmptyResult(shown == 0)

    # ── 代理 QComboBox 常用 API ──

    def addItem(self, text, userData=None):
        # 过滤改为「隐藏行」后，内部 QComboBox 必须始终与 _all_items 同步，
        # 所以这里无条件 addItem（不再按 popup 是否可见跳过）。
        self._all_items.append((text, userData))
        self._combo.addItem(text, userData)
        if self._popup_is_visible and self._search is not None:
            self._repopulate(self._search.text())

    def addItems(self, texts):
        for t in texts:
            self.addItem(t)

    def clear(self):
        self._all_items.clear()
        self._combo.clear()

    def count(self):
        return self._combo.count()

    def currentText(self):
        return self._combo.currentText()

    def setCurrentText(self, text):
        self._combo.setCurrentText(text)

    def currentIndex(self):
        return self._combo.currentIndex()

    def setCurrentIndex(self, index):
        self._combo.setCurrentIndex(index)

    def currentData(self, role=Qt.UserRole):
        return self._combo.currentData(role)

    def itemData(self, index, role=Qt.UserRole):
        return self._combo.itemData(index, role)

    def itemText(self, index):
        return self._combo.itemText(index)

    def findText(self, text, flags=Qt.MatchExactly | Qt.MatchCaseSensitive):
        return self._combo.findText(text, flags)

    def setSizePolicy(self, *args):
        self._combo.setSizePolicy(*args)

    def setEditable(self, editable):
        self._combo.setEditable(editable)

    def setFixedWidth(self, w):
        self._combo.setFixedWidth(w)

    def setVisible(self, visible):
        super().setVisible(visible)

    def isVisible(self):
        return super().isVisible()

    def blockSignals(self, block):
        return self._combo.blockSignals(block)

    def signalsBlocked(self):
        return self._combo.signalsBlocked()

    def setPlaceholderText(self, text):
        self._combo.setPlaceholderText(text)

    def setToolTip(self, text):
        self._combo.setToolTip(text)

    def view(self):
        return self._combo.view()

    def model(self):
        return self._combo.model()

    def setModel(self, model):
        self._combo.setModel(model)

    def setCurrentData(self, data, role=Qt.UserRole):
        """根据 itemData 查找并选中对应项"""
        for i in range(self._combo.count()):
            if self._combo.itemData(i, role) == data:
                self._combo.setCurrentIndex(i)
                return

    # ── 组合框属性代理 ──

    @property
    def combo(self):
        """获取内部 QComboBox，用于需要直接操作原生控件的高级场景"""
        return self._combo
