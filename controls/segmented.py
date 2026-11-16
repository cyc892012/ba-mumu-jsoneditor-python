"""可移入 controls 的分段控件；保留业务文本，允许另设简短按钮文案。"""
from PyQt5.QtCore import Qt, QEvent, QSignalBlocker, pyqtSignal
from PyQt5.QtWidgets import QWidget, QComboBox, QPushButton, QButtonGroup, QHBoxLayout, QCheckBox

import theme


CAPTION_ROLE = Qt.UserRole + 731


class SegmentedControl(QWidget):
    """非编辑型 QComboBox 的常用 API；实际 Qt 模型负责索引、数据和查找语义。

    currentText/itemText/findText 使用完整业务文本，setItemCaption 只影响按钮。
    支持 rebuild_combo、QSignalBlocker，以及鼠标、方向键、Home/End、空格。
    不提供弹出菜单、可编辑框或完整 QComboBox 类型替代能力。
    """

    currentIndexChanged = pyqtSignal(int)
    currentTextChanged = pyqtSignal(str)
    activated = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._combo = QComboBox(self)
        self._combo.hide()
        self._buttons = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(2)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(f'''
            SegmentedControl {{ background: #F5ECF0; border: none; border-radius: 7px; }}
            SegmentedControl QPushButton {{ background: transparent; border: 1px solid transparent;
                border-radius: 5px; padding: 1px 8px; color: {theme.INK_2};
                font-family: "{theme.FONT_FAMILY}"; font-size: 12px; min-height: 18px; }}
            SegmentedControl QPushButton:checked {{ background: {theme.CARD};
                color: {theme.PINK_700}; border-color: {theme.LINE}; font-weight: bold; }}
            SegmentedControl QPushButton:hover:enabled {{ border-color: {theme.PINK_300}; }}
            SegmentedControl QPushButton:focus {{ border-color: {theme.VIOLET}; }}
            SegmentedControl QPushButton:disabled {{ color: {theme.INK_3}; background: {theme.BG}; }}
        ''')
        self._combo.currentIndexChanged.connect(self._on_index_changed)
        self._combo.currentTextChanged.connect(self.currentTextChanged.emit)
        model = self._combo.model()
        for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset, model.dataChanged):
            signal.connect(self._rebuild_buttons)

    def _rebuild_buttons(self, *_):
        self.setFocusProxy(None)
        for button in self._buttons:
            self._group.removeButton(button)
            self._layout.removeWidget(button)
            button.hide()
            button.deleteLater()
        self._buttons = []
        for index in range(self.count()):
            caption = self.itemData(index, CAPTION_ROLE)
            button = QPushButton(self.itemText(index) if caption is None else str(caption), self)
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(self.itemText(index))
            button.setAccessibleName(self.itemText(index))
            button.setEnabled(self.isItemEnabled(index))
            button.installEventFilter(self)
            self._group.addButton(button, index)
            button.clicked.connect(lambda checked=False, i=index: self._activate(i))
            self._layout.addWidget(button)
            self._buttons.append(button)
        self._sync_buttons()
        self.updateGeometry()

    def _sync_buttons(self):
        # -1 也必须清除旧选中项；暂时取消互斥只用于同步，不对外发按钮信号。
        self._group.setExclusive(False)
        for index, button in enumerate(self._buttons):
            with QSignalBlocker(button):
                button.setChecked(index == self.currentIndex())
                button.setFocusPolicy(Qt.StrongFocus if index == self.currentIndex() else Qt.ClickFocus)
        self._group.setExclusive(True)
        if 0 <= self.currentIndex() < len(self._buttons):
            self.setFocusProxy(self._buttons[self.currentIndex()])
        else:
            self.setFocusProxy(None)

    def _selection_updated(self):
        """子类同步内部状态；外部 blockSignals 时仍然执行。"""

    def _on_index_changed(self, index):
        self._sync_buttons()
        self._selection_updated()
        self.currentIndexChanged.emit(index)

    def _activate(self, index):
        if self.isEnabled() and self.isItemEnabled(index):
            self.setCurrentIndex(index)
            self.activated.emit(index)

    def eventFilter(self, watched, event):
        if watched in self._buttons and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End):
                choices = [i for i in range(self.count()) if self.isItemEnabled(i)]
                if choices:
                    if event.key() == Qt.Key_Home:
                        target = choices[0]
                    elif event.key() == Qt.Key_End:
                        target = choices[-1]
                    else:
                        step = -1 if event.key() in (Qt.Key_Left, Qt.Key_Up) else 1
                        if self.layoutDirection() == Qt.RightToLeft and event.key() in (Qt.Key_Left, Qt.Key_Right):
                            step *= -1
                        at = choices.index(self.currentIndex()) if self.currentIndex() in choices else -1
                        target = choices[(at + step) % len(choices)]
                    self._activate(target)
                    self._buttons[target].setFocus(Qt.TabFocusReason)
                return True
        return super().eventFilter(watched, event)

    def currentIndex(self):
        return self._combo.currentIndex()

    def currentText(self):
        return self._combo.currentText()

    def currentData(self, role=Qt.UserRole):
        return self._combo.currentData(role)

    def setCurrentIndex(self, index):
        self._combo.setCurrentIndex(index)

    def setCurrentText(self, text):
        self._combo.setCurrentText(text)

    def count(self):
        return self._combo.count()

    def addItem(self, *args, **kwargs):
        self._combo.addItem(*args, **kwargs)

    def addItems(self, texts):
        self._combo.addItems(texts)

    def insertItem(self, *args, **kwargs):
        self._combo.insertItem(*args, **kwargs)

    def removeItem(self, index):
        self._combo.removeItem(index)

    def clear(self):
        self._combo.clear()

    def itemText(self, index):
        return self._combo.itemText(index)

    def setItemText(self, index, text):
        self._combo.setItemText(index, text)

    def itemData(self, index, role=Qt.UserRole):
        return self._combo.itemData(index, role)

    def setItemData(self, index, value, role=Qt.UserRole):
        self._combo.setItemData(index, value, role)

    def findData(self, value, role=Qt.UserRole, flags=Qt.MatchExactly | Qt.MatchCaseSensitive):
        return self._combo.findData(value, role, flags)

    def findText(self, text, flags=Qt.MatchExactly | Qt.MatchCaseSensitive):
        return self._combo.findText(text, flags)

    def setItemCaption(self, index, caption):
        self.setItemData(index, caption, CAPTION_ROLE)

    def isItemEnabled(self, index):
        item = self._combo.model().item(index)
        return bool(item is not None and item.isEnabled())

    def setItemEnabled(self, index, enabled):
        item = self._combo.model().item(index)
        if item is not None:
            item.setEnabled(enabled)


class _ModeCheckBox(QCheckBox):
    """保留旧窗口复选框引用；状态只由分段控件统一提交。"""

    def __init__(self, owner, mode, text):
        super().__init__(text, owner)
        self._owner = owner
        self._mode = mode
        self.hide()

    def setChecked(self, checked):
        if checked:
            self._owner.setMode(self._mode)
        elif self.isChecked():
            self._owner.setMode("off")

    def setCheckState(self, state):
        self.setChecked(state != Qt.Unchecked)

    def toggle(self):
        self.setChecked(not self.isChecked())

    def nextCheckState(self):
        self.toggle()


class GenerationModeControl(SegmentedControl):
    """关闭/单次/连续；mode 值为 off/once/continuous，初始关闭。

    onceCheckBox/continuousCheckBox 是真正的 QCheckBox，支持旧 isChecked、
    setChecked、toggled、stateChanged、blockSignals 和 set_checked_silently。
    所有状态先原子同步，再按「取消旧模式→开启新模式」发信号。
    不在控件里启动键盘监听；业务窗口负责处理模式变化。
    """

    modeChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._once = _ModeCheckBox(self, "once", "生成一次")
        self._continuous = _ModeCheckBox(self, "continuous", "生成多次")
        self.addItem("关闭", "off")
        self.addItem("单次", "once")
        self.addItem("连续", "continuous")
        self.currentIndexChanged.connect(lambda _: self.modeChanged.emit(self.mode()))

    def mode(self):
        return self.currentData() or "off"

    def setMode(self, mode):
        index = self.findData(mode)
        if index < 0:
            raise ValueError(f"未知生成模式：{mode!r}")
        self.setCurrentIndex(index)

    def onceCheckBox(self):
        return self._once

    def continuousCheckBox(self):
        return self._continuous

    def completeOnce(self):
        """成功处理一次捕获后调用；连续模式不会受影响。"""
        if self.mode() == "once":
            self.setMode("off")

    def _selection_updated(self):
        if not hasattr(self, "_once"):
            return
        changed = []
        for checkbox, mode in ((self._once, "once"), (self._continuous, "continuous")):
            checked = self.mode() == mode
            if checkbox.isChecked() != checked:
                with QSignalBlocker(checkbox):
                    QCheckBox.setChecked(checkbox, checked)
                changed.append((checkbox, checked))
        for checkbox, checked in sorted(changed, key=lambda pair: pair[1]):
            if not self.signalsBlocked():
                checkbox.stateChanged.emit(Qt.Checked if checked else Qt.Unchecked)
                checkbox.toggled.emit(checked)
