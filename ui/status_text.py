"""状态栏 / 提示文案 — **全项目唯一来源**（对应 C# ``StatusNotifier``）。

迁移前「按键已存在」这一个语义在项目里有 **5 种写法**，连全角半角冒号、
有没有方括号都不统一::

    已存在：按键 D（坐标、按键、类型完全一致）      # adb_touch_form
    按键 [D] 已存在，无需注入                       # key_preset_form
    按键 [D] 已存在，禁止重复生成                   # main_window（连续模式查重）
    按键 [D] 已存在 | 类型: 点击按键                # main_window（检查按键结果）
    已存在: D，禁止重复生成                          # quick_touch_form

统一后的约定：**前缀一律 ``按键 [X] 已存在``**，需要补充细节时用括号追加，
而不是另起一种写法。

⚠️ 注意这 5 处**不是同一件事**，只是同一类事实，所以不能全部套同一句话：
    - 2 处是「查重命中、阻止生成」→ 用默认文案（``已存在，禁止重复生成``）
    - 1 处是「预设已在目标里、跳过注入」→ 动作是跳过而非阻止，只能挂 ``无需注入``
    - 1 处是「检查按键」的查询结果 → 必须带上类型，否则用户看不到查询结果
    - 1 处是「三重匹配命中」→ 带上匹配依据，便于排查为什么被判成重复

本模块只产出文本，**不依赖 Qt**，便于单测。
"""

# 查重命中的标准后缀
KEY_EXISTS_REASON = "已存在，禁止重复生成"

# 追加明细时使用的固定短语，避免各处再写出新的变体
DETAIL_NO_INJECT = "无需注入"
DETAIL_EXACT_MATCH = "坐标、按键、类型完全一致"


def key_exists(name: str, detail: str = "") -> str:
    """「按键已存在」统一文案。

    Args:
        name: 按键名（调用方自行决定大小写，本函数不改写）。
        detail: 追加说明，如 :data:`DETAIL_EXACT_MATCH` / ``f"类型: {t}"``。
            留空则用标准文案「禁止重复生成」。

    Returns:
        ``按键 [D] 已存在，禁止重复生成``
        或 ``按键 [D] 已存在（坐标、按键、类型完全一致）``
    """
    if detail:
        return f"按键 [{name}] 已存在（{detail}）"
    return f"按键 [{name}] {KEY_EXISTS_REASON}"


def key_created(name: str, detail: str = "") -> str:
    """「按键已生成」统一文案（前缀一律 ``已生成按键 [X]``）。

    Args:
        name: 按键名。
        detail: 追加说明（如带坐标、带按键数），同 :func:`key_exists`。

    Note:
        带坐标 / 带按键数的两处是**复合消息**（``生成: D at (800,450)``、
        ``ADB 按键已生成 | 按键数: 12``），它们保留了各自的补充信息，
        只统一前缀，不强行合并成一句话。
    """
    if detail:
        return f"已生成按键 [{name}]（{detail}）"
    return f"已生成按键 [{name}]"


def listening(enabled: bool, *, continuous: bool = False) -> str:
    """键盘监听开关提示（quick_touch_form 的两种模式）。

    Returns:
        ``提示：已开启键盘监听（单次）`` / ``提示：已开启键盘监听（连续）``
        / ``提示：已关闭键盘监听``
    """
    if not enabled:
        return "提示：已关闭键盘监听"
    return f"提示：已开启键盘监听（{'连续' if continuous else '单次'}）"
