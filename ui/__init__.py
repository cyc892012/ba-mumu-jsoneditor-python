"""UI 包 —— 子窗体惰性导入。

历史实现在这里一次性 ``import`` 了 10 个窗体模块。由于任何
``from ui.xxx import ...`` 都会先执行本文件，启动时被迫加载了
keyboard / pynput / dataclasses / inspect / typing 等一堆重模块
（实测约 265ms，其中 keyboard 单项约 82ms），而这些窗体全是
「点开菜单才创建」的，启动期一个都用不上。

改为 PEP 562 惰性属性：既保持 ``ui.MainWindow`` 这类写法可用，
又让启动只加载真正需要的 ``ui.main_window``。
"""

_LAZY_MODULES = {
    "BaseDialog": ".base_dialog",
    "MainWindow": ".main_window",
    "AdbTouchForm": ".adb_touch_form",
    "QuickTouchForm": ".quick_touch_form",
    "RangeClearForm": ".range_clear_form",
    "KeyPresetForm": ".key_preset_form",
    "MouseTrackForm": ".mouse_track_form",
    "WideScreenForm": ".wide_screen_form",
    "IndexBackupForm": ".index_backup_form",
    "LogViewerForm": ".log_viewer_form",
    "UndoHistoryForm": ".undo_history_form",
}

__all__ = list(_LAZY_MODULES)


def __getattr__(name):
    module = _LAZY_MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    value = getattr(importlib.import_module(module, __name__), name)
    globals()[name] = value          # 缓存，之后再访问不再走 __getattr__
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_MODULES))
