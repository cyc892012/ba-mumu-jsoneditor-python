"""UI 设计令牌与共用样式 — 睡衣日奈主题。"""
from pathlib import Path

# ── 配色表 ──────────────────────────────────
INK = "#3B3340"
INK_2 = "#7A6E80"
INK_3 = "#A89DAD"
BG = "#FFF8FA"
CARD = "#FFFFFF"
LINE = "#F1DDE4"
LINE_STRONG = "#E6C7D2"
PINK_50 = "#FDEFF3"
PINK_100 = "#FBDDE6"
PINK_300 = "#F4AFC3"
PINK_500 = "#E5809F"
PINK_700 = "#A9466B"
CREAM_50 = "#FFF9EC"
CREAM_100 = "#FFF1D2"
CREAM_300 = "#EED39A"
CREAM_700 = "#6E5220"
VIOLET = "#8B6CD6"
VIOLET_50 = "#F2EDFC"
VIOLET_700 = "#5E43B0"
MINT = "#4FAE84"
MINT_50 = "#E6F5EE"
AMBER = "#D9962E"
AMBER_50 = "#FDF3E1"
RED = "#D9566B"
RED_50 = "#FCE9EC"

# 旧名称保留，供现有窗口继续引用。
BG_MAIN = BG
BG_CARD = CARD
BG_CARD_ALT = CARD
BORDER_CARD = LINE
BORDER_INPUT = LINE_STRONG

BTN_PRIMARY = PINK_300
BTN_PRIMARY_HOVER = PINK_500
BTN_SECONDARY = CREAM_100
BTN_SECONDARY_HOVER = "#FFE9B8"
BTN_DANGER = RED
BTN_DISABLED_BG = PINK_50
BTN_DISABLED_TEXT = INK_3

ACCENT = VIOLET
TITLE_BAR_START = "#F6B7CA"
TITLE_BAR_MIDDLE = "#F9D2CB"
TITLE_BAR_END = "#FAE3B6"
TITLE_TEXT = "#5A2F42"

TEXT_PRIMARY = INK
TEXT_SECONDARY = INK_2
TEXT_ON_ACCENT = TITLE_TEXT

STATUS_BG = "#FBE7EE"
MATCH_BG = MINT_50
CURRENT_ROW_BG = PINK_50
DROP_SELECTED = PINK_50

# ── 字体 ────────────────────────────────────
FONT_FAMILY = "Microsoft YaHei UI"
FONT_SIZE = 9

# ── 圆角 ────────────────────────────────────
RADIUS_CARD = 10
RADIUS_BTN = 6
RADIUS_INPUT = 6

# ── 控件高度 ──────────────────────────────
H_INPUT = 26
H_BTN = 26
H_BTN_SM = 20

# ── 间距 ────────────────────────────────────
PAD = 4


# ═══════════════════════════════════════════
#  按钮样式 — 直接注入，不依赖全局 QSS 选择器
# ═══════════════════════════════════════════
BTN_BASE = (
    f"font-family: \"{FONT_FAMILY}\";"
    f"font-size: {FONT_SIZE}pt;"
    f"color: {TEXT_PRIMARY};"
    f"border: 1px solid {LINE_STRONG};"
    f"border-radius: {RADIUS_BTN}px;"
    f"padding: 0 8px;"
    f"min-height: {H_BTN - 2}px;"
)

BTN_PRIMARY_STYLE = BTN_BASE + (
    "background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #F7BACC, stop:1 #F19DB7);"
    f"border-color: {PINK_500}; color: #4A1E30; font-weight: bold;"
)

BTN_SECONDARY_STYLE = BTN_BASE + (
    f"background: {BTN_SECONDARY};"
    f"border-color: {CREAM_300}; color: {CREAM_700};"
)

BTN_DANGER_STYLE = BTN_BASE + (
    f"background: {CARD}; border-color: #EBA6B1; color: {RED};"
)

BTN_GHOST_STYLE = BTN_BASE + (
    f"background: {CARD}; border-color: {LINE_STRONG}; color: {INK_2};"
)

# 禁用态保留各角色色相，将背景、边框、文字一同淡化。
BUTTON_STATES = {
    "primary": (
        "background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #F5AEC3, stop:1 #EC8FAC);",
        "background: #E88CA9; border-color: #A9466B;",
        "background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FCE4EC, stop:1 #F9D6E2); border-color: #F3CBD8; color: #B799A5;",
    ),
    "secondary": (
        f"background: {BTN_SECONDARY_HOVER};",
        "background: #F7DDA6; border-color: #D9B976;",
        "background: #FFF9EC; border-color: #F7EBCD; color: #BFAF91;",
    ),
    "ghost": (
        f"background: {PINK_50}; color: {INK};",
        f"background: {PINK_100}; border-color: {PINK_300};",
        "background: #FFFFFF; border-color: #F4E8ED; color: #C5BDC8;",
    ),
    "danger": (
        f"background: {RED_50}; border-color: {RED};",
        "background: #F9D2DB; border-color: #BA3F55; color: #BA3F55;",
        "background: #FFFFFF; border-color: #F7DADF; color: #EBAFBB;",
    ),
}

# ── 输入框通用样式 ──────────────────────────
INPUT_STYLE = (
    f"background: {CARD};"
    f"border: 1px solid {BORDER_INPUT};"
    f"border-radius: {RADIUS_INPUT}px;"
    f"padding: 1px 4px;"
    f"font-family: \"{FONT_FAMILY}\";"
    f"font-size: {FONT_SIZE}pt;"
    f"color: {TEXT_PRIMARY};"
)

# ── 下拉框通用样式 ──────────────────────────
COMBO_STYLE = INPUT_STYLE + (
    f"min-height: {H_INPUT - 4}px;"
)

# ── 卡片样式 ────────────────────────────────
CARD_STYLE = (
    "QFrame#Card {"
    "background: rgba(255, 255, 255, 0.88);"
    f"border: 1px solid {BORDER_CARD};"
    f"border-radius: {RADIUS_CARD}px;"
    "}"
)

# ── 复选框样式 ──────────────────────────────
CHECKBOX_STYLE = (
    f"font-family: \"{FONT_FAMILY}\";"
    f"font-size: {FONT_SIZE}pt;"
    f"color: {INK_2};"
    f"spacing: 2px;"
)

# ── 列表/表格样式 ───────────────────────────
TABLE_STYLE = (
    "QTableView, QTableWidget {"
    f"background: {BG_CARD_ALT};"
    f"border: 1px solid {BORDER_CARD};"
    f"border-radius: 6px;"
    f"font-family: \"{FONT_FAMILY}\";"
    f"font-size: {FONT_SIZE}pt;"
    f"color: {TEXT_PRIMARY};"
    f"alternate-background-color: {BG_MAIN};"
    f"selection-background-color: {PINK_50}; selection-color: {INK};"
    f"gridline-color: {LINE};"
    "}"
)

STATUS_QSS = f"""
QStatusBar {{
    background: {STATUS_BG}; border: none; border-top: 1px solid #F3D0DC;
    min-height: 23px; max-height: 23px; padding: 0 2px;
    color: {INK_2}; font-family: "{FONT_FAMILY}"; font-size: 9pt; font-weight: normal;
}}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{
    background: transparent; border: none; color: {INK_2};
    font-family: "{FONT_FAMILY}"; font-size: 9pt; font-weight: normal;
}}
QStatusBar QPushButton {{
    background: {CARD}; border: 1px solid {PINK_500}; border-radius: 9px;
    color: {PINK_700}; padding: 0 7px; min-height: 18px;
    font-family: "{FONT_FAMILY}"; font-size: 9pt; font-weight: normal;
}}
QStatusBar QPushButton:hover {{ background: {PINK_50}; }}
QStatusBar QPushButton:pressed {{ background: {PINK_100}; }}
QStatusBar QPushButton:disabled {{ border-color: #F3CBD8; color: #D1A7B8; }}
"""

HINT_INFO_STYLE = (
    f"QLabel {{ background: {VIOLET_50}; color: {VIOLET_700}; border: none; "
    f"border-left: 3px solid {VIOLET}; border-radius: 6px; padding: 5px 8px; "
    f'font-family: "{FONT_FAMILY}"; font-size: 9pt; }}'
)
HINT_WARN_STYLE = (
    f"QLabel {{ background: {AMBER_50}; color: #8A5A10; border: none; "
    f"border-left: 3px solid {AMBER}; border-radius: 6px; padding: 5px 8px; "
    f'font-family: "{FONT_FAMILY}"; font-size: 9pt; }}'
)

# 图标在源码及 PyInstaller 的 ui/assets 中均使用同一相对位置。
CHECK_ICON_PATH = (Path(__file__).resolve().parent / "ui" / "assets" / "icons" / "check.svg").as_posix()

# ── 全局 QSS（仅基础控件）──────────────────
GLOBAL_QSS = f"""
QWidget {{
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_SIZE}pt;
    color: {TEXT_PRIMARY};
}}

QLineEdit {{
    {INPUT_STYLE}
    min-height: {H_INPUT - 4}px;
}}

QComboBox {{
    {COMBO_STYLE}
}}
QLineEdit:read-only {{ background: #F8F4F6; }}
/* 捕获框通过按键输入，显式覆盖只读伪状态的背景优先级。 */
KeyCaptureEdit, KeyCaptureEdit:read-only {{ background: {CARD}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {VIOLET}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {INK_3}; }}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 18px;
    border: none;
}}
QComboBox QAbstractItemView {{
    background: {BG_MAIN};
    border: 1px solid {BORDER_CARD};
    selection-background-color: {DROP_SELECTED};
    selection-color: {TEXT_PRIMARY};
}}

QCheckBox {{
    {CHECKBOX_STYLE}
    background: transparent; border: none;
}}
QCheckBox::indicator {{
    width: 14px; height: 14px; border-radius: 4px;
    background: {CARD}; border: 1px solid #B8899E;
}}
QCheckBox::indicator:hover {{ border-color: {VIOLET}; }}
QCheckBox::indicator:checked {{
    background: {VIOLET}; border-color: {VIOLET}; image: url("{CHECK_ICON_PATH}");
}}
QCheckBox::indicator:disabled {{ border-color: {LINE}; background: {BG}; }}
QCheckBox::indicator:checked:disabled {{ background: #CDBFEA; image: url("{CHECK_ICON_PATH}"); }}

QTabWidget::pane {{
    background: rgba(255,255,255,0.88);
    border: 1px solid {BORDER_CARD};
    border-radius: {RADIUS_CARD}px;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_SECONDARY};
    padding: 4px 12px;
    font-size: {FONT_SIZE}pt;
    border: none;
}}
QTabBar::tab:selected {{
    color: {TEXT_PRIMARY};
    background: {BG_CARD};
    border-bottom: 2px solid {VIOLET};
}}

QHeaderView::section {{
    background: #FCEEF2;
    color: {INK_2};
    font-weight: normal;
    border: none;
    border-bottom: 1px solid {LINE};
    padding: 2px 4px;
    font-size: {FONT_SIZE}pt;
}}

QScrollBar:vertical {{
    background: {BG_MAIN};
    width: 8px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_CARD};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: {BTN_PRIMARY};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: {BG}; height: 8px; border: none; }}
QScrollBar::handle:horizontal {{ background: {LINE_STRONG}; border-radius: 4px; min-width: 20px; }}
QScrollBar::handle:horizontal:hover {{ background: {PINK_300}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
{STATUS_QSS}
"""
