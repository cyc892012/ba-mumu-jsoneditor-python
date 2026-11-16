"""旧 WinForms 枚举名与当前单键热键的兼容映射，不安装系统钩子。"""
import re


def canonical_hotkey_key(value):
    value = str(value or "").strip().casefold()
    aliases = {"return": "enter", "escape": "esc", "pageup": "page up", "prior": "page up",
               "pagedown": "page down", "next": "page down", "back": "backspace",
               "oemcomma": "comma", "oemperiod": ".", "oemminus": "-", "oemplus": "plus",
               "oem1": ";", "oemsemicolon": ";", "oem2": "/", "oemquestion": "/",
               "oem3": "`", "oemtilde": "`", "oem4": "[", "oemopenbrackets": "[",
               "oem5": "\\", "oempipe": "\\", "oem6": "]", "oemclosebrackets": "]",
               "oem7": "'", "oemquotes": "'", ",": "comma", "+": "plus",
               "lwin": "left windows", "rwin": "right windows", "win": "windows",
               "capslock": "caps lock", "capital": "caps lock", "numlock": "num lock",
               "scrolllock": "scroll lock", "scroll": "scroll lock", "printscreen": "print screen"}
    if value in aliases:
        return aliases[value]
    if re.fullmatch(r"d\d", value):
        return value[1]
    if re.fullmatch(r"numpad\d", value):
        return value
    if re.fullmatch(r"f(?:[1-9]|1\d|2[0-4])", value):
        return value
    if len(value) == 1 and value.isprintable():
        return value
    if value in {"enter", "esc", "space", "tab", "backspace", "up", "down", "left", "right",
                 "home", "end", "insert", "delete", "pause", "page up", "page down", "windows",
                 "left windows", "right windows", "caps lock", "num lock", "scroll lock", "print screen",
                 "comma", "plus", "decimal", "add", "subtract", "multiply", "divide"}:
        return value
    raise ValueError("请输入有效快捷键")
