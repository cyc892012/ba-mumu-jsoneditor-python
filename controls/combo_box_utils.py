"""下拉框 / 勾选框 辅助 — 对应 C# ``ComboBoxInitializer``。

迁移前「重建下拉框」这个动作在项目里被手写了 **12 处**，每次都要记得：

    1. ``blockSignals(True)`` —— 否则 addItem/clear 会触发 currentIndexChanged，
       把「程序在填数据」误当成「用户切换了选项」，连锁触发刷新甚至写盘
    2. ``clear()``
    3. 逐个 addItem
    4. ``blockSignals(False)``
    5. （可选）恢复原选中项 —— **有的调用点恢复、有的不恢复**，容易抄漏

第 1 步和第 5 步都是"忘了就出怪 bug"的类型，所以收口到这里。

C# 的 ``ComboBoxInitializer`` 用 DataSource 绑定（``BindFileItems`` +
``RestoreSelection``）；PyQt 没有等价的 DataSource 概念，对应实现就是
:func:`rebuild_combo`。
"""
from typing import Any, Iterable, Optional, Sequence, Tuple, Union

from PyQt5.QtWidgets import QCheckBox, QComboBox

# C# 的 ``ComboBoxInitializer.PredefinedKeyTypes`` 就放在这个类里，故一并收口于此。
# 类型值本身（Click/Macro）的归属方仍是 core/json_editor.py。
from core.json_editor import TYPE_CLICK, TYPE_MACRO

# (显示名, 类型值) —— 顺序即下拉框显示顺序，与 C# 一致
KEY_TYPE_ITEMS: Tuple[Tuple[str, str], ...] = (
    ("点击按键", TYPE_CLICK),
    ("宏指牌按键", TYPE_MACRO),
)

# 供 ``addItems()`` 直接使用
KEY_TYPE_DISPLAY_NAMES: Sequence[str] = tuple(name for name, _ in KEY_TYPE_ITEMS)

ComboItem = Union[str, Tuple[str, Any]]


def rebuild_combo(
    combo: QComboBox,
    items: Iterable[ComboItem],
    *,
    restore_data: Optional[Any] = None,
    restore_text: Optional[str] = None,
) -> None:
    """重建下拉框内容，全程屏蔽信号。

    Args:
        combo: 目标下拉框。
        items: ``[显示文本]`` 或 ``[(显示文本, itemData)]``。传 ``(text, data)``
            时 data 进 ``itemData``（调用方后续用 ``currentData()`` 取）。
        restore_data: 若非 None，重建后按 ``itemData`` 匹配恢复选中项；
            找不到时**保持默认第一项**（不额外处理）。
        restore_text: 若非 None 且未给 ``restore_data``，按显示文本匹配恢复；
            找不到时调用 ``setCurrentText``（可编辑框会直接填入文本，
            不可编辑框无匹配则不动作 —— 与两个原始调用点行为一致）。

    Note:
        ``restore_data`` 与 ``restore_text`` 都不给时**不恢复选中项**，
        即停留在第一项。这与 ``wide_screen_form`` 的历史行为一致；
        ``main_window`` 的历史行为是恢复，所以它传 ``restore_data``。
    """
    combo.blockSignals(True)
    combo.clear()
    for item in items:
        if isinstance(item, (tuple, list)):
            text, data = item[0], item[1]
            combo.addItem(text, data)
        else:
            combo.addItem(item)

    if restore_data is not None:
        for i in range(combo.count()):
            if combo.itemData(i) == restore_data:
                combo.setCurrentIndex(i)
                break
    elif restore_text is not None:
        idx = combo.findText(restore_text)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setCurrentText(restore_text)

    combo.blockSignals(False)


def set_checked_silently(checkbox: QCheckBox, checked: bool) -> None:
    """设置勾选框状态但不触发 ``toggled`` 信号。

    用于「单次/连续」这类**互斥勾选**：取消另一个时必须屏蔽信号，
    否则会被当成用户操作，反过来又把当前这个取消掉。
    """
    checkbox.blockSignals(True)
    checkbox.setChecked(checked)
    checkbox.blockSignals(False)


def key_type_display_name(key_type: str) -> str:
    """类型值 → 中文显示名；未知类型按「点击按键」处理。

    对应 C# ``text = keyType == Macro ? "宏指牌按键" : "点击按键"`` 的 else 分支。
    """
    for name, value in KEY_TYPE_ITEMS:
        if value == key_type:
            return name
    return KEY_TYPE_ITEMS[0][0]


def key_type_value(display_name: str) -> str:
    """中文显示名 → 类型值；未知显示名按「点击按键」处理。"""
    for name, value in KEY_TYPE_ITEMS:
        if name == display_name:
            return value
    return KEY_TYPE_ITEMS[0][1]
