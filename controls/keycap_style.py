"""只装饰现有 KeyCaptureEdit，不替换捕获事件、回调或 vk 属性。"""
from PyQt5.QtCore import QObject

import theme


class _KeycapStyle(QObject):
    def __init__(self, edit):
        super().__init__(edit)
        self._edit = edit
        edit.setProperty("p2Keycap", True)
        edit.setMinimumHeight(32)
        edit.setStyleSheet(edit.styleSheet() + f'''
            QLineEdit[p2Keycap="true"], QLineEdit[p2Keycap="true"]:read-only {{
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #FFFFFF,stop:1 #FBF4F7);
                color: {theme.INK}; border: 1px solid #D8C2CB; border-bottom-width: 3px;
                border-radius: 7px; padding: 1px 4px; font-family: "Consolas";
                font-size: 15px; font-weight: bold; }}
            QLineEdit[p2Keycap="true"][p2KeycapEmpty="true"],
            QLineEdit[p2Keycap="true"][p2KeycapEmpty="true"]:read-only {{
                border: 1px dashed {theme.LINE_STRONG}; color: {theme.INK_2};
                font-family: "{theme.FONT_FAMILY}"; font-size: 11px; font-weight: normal; }}
            QLineEdit[p2Keycap="true"]:focus {{ border-color: {theme.VIOLET}; }}
            QLineEdit[p2Keycap="true"]:disabled {{ color: {theme.INK_3}; background: {theme.BG}; }}
        ''')
        edit.textChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self, *_):
        self._edit.setProperty("p2KeycapEmpty", not bool(self._edit.text()))
        self._edit.style().unpolish(self._edit)
        self._edit.style().polish(self._edit)
        self._edit.update()


def apply_keycap_style(edit):
    """幂等安装；调用方为主窗口单字符预留至少 45×32 的布局空间。"""
    if not hasattr(edit, "_p2_keycap_style"):
        edit._p2_keycap_style = _KeycapStyle(edit)
    return edit._p2_keycap_style
