"""包名 / 键位文件名前缀 — **全项目唯一来源**。

对应 C#：``Form1.PackageNameTypes``（业务表）+ ``Form1.MatchPackageFromPath``
（反推包名）+ ``Form1_Public.StripPackageName``（剥离前缀）。

迁移前这张表在项目里有 **4 份 4 种形式**：
    - ``MainWindow._PACKAGE_PREFIXES``（dict，6 项）+ ``_KNOWN_PREFIXES``（list，4 项）
    - ``key_preset_form._PACKAGE_PREFIXES``（list，4 项，用于剥离）
    - ``wide_screen_form._PACKAGE_NAMES``（list[tuple]，6 项，用于初始化下拉框）
    - ``MainWindow._match_package_from_path`` 里硬编码的 ``["官服","B服","日服","国际服"]``

**加一个新服务器过去要改 4 处**，漏一处就出现"某个面板认不出服务器"的怪现象。

═══ 长前缀优先（重要契约）═══
``REAL_PACKAGE_PREFIXES`` 按**长度降序**排列，这不是随手排的：

    C# 原实现是
        PackageNameTypes.Values
            .Where(v => v != "other" && v != "萌新666sssaaa")
            .OrderByDescending(v => v.Length)          // ← 显式按长度倒序
            .FirstOrDefault(v => fileName.StartsWith(v.TrimEnd('-')))
            ?? "other"

即 C# **先匹配最长的前缀**。因为 "官服" 的前缀 ``com.RoamingStar.BlueArchive-`` 是
"B服" 前缀 ``com.RoamingStar.BlueArchive.bilibili-`` 的**真前缀**，一旦顺序颠倒，
B服文件会被误判成官服。**改这张表时不要重排。** 有测试钉住这一点。
"""
import os
from typing import Dict, List, Optional, Tuple

# 显示名 → 文件名前缀（与 C# PackageNameTypes 完全一致）
PACKAGE_PREFIX_BY_NAME: Dict[str, str] = {
    "官服": "com.RoamingStar.BlueArchive-",
    "B服": "com.RoamingStar.BlueArchive.bilibili-",
    "日服": "com.YostarJP.BlueArchive-",
    "国际服": "com.nexon.bluearchive-",
    "其他": "other",
    "宇宙服": "萌新666sssaaa",
}

# 供下拉框初始化：[(显示名, 前缀)]，顺序与业务表一致
PACKAGE_ITEMS: List[Tuple[str, str]] = list(PACKAGE_PREFIX_BY_NAME.items())

# 伪前缀：不参与文件名匹配
OTHER_PREFIX = "other"
UNIVERSE_PREFIX = "萌新666sssaaa"

# 「其他」的显示名（与 PACKAGE_PREFIX_BY_NAME 的键一致）
OTHER_PACKAGE_NAME = "其他"

# 真实包名前缀，**长度降序（长前缀优先）** —— 见模块文档，不要重排
REAL_PACKAGE_PREFIXES: List[str] = [
    "com.RoamingStar.BlueArchive.bilibili-",
    "com.RoamingStar.BlueArchive-",
    "com.YostarJP.BlueArchive-",
    "com.nexon.bluearchive-",
]

# 已知前缀集合（供"是否属于已知服务器"的成员判断；顺序无意义）
KNOWN_PREFIXES: List[str] = REAL_PACKAGE_PREFIXES

# 反推包名时遍历的显示名顺序（与 C# Where 过滤后的顺序一致）
REAL_PACKAGE_NAMES: Tuple[str, ...] = ("官服", "B服", "日服", "国际服")


def strip_package_prefix(filename: str) -> str:
    """去掉文件名开头的包名前缀，保留扩展名。

    >>> strip_package_prefix("com.YostarJP.BlueArchive-13判测试.json")
    '13判测试.json'
    """
    if not filename:
        return filename
    name, ext = os.path.splitext(filename)
    for prefix in REAL_PACKAGE_PREFIXES:      # 长前缀优先
        if name.startswith(prefix):
            return name[len(prefix):] + ext
    return filename


def package_name_from_path(file_path: str) -> str:
    """从文件路径反推服务器**显示名**；认不出返回 ``"其他"``。

    ⚠️ 返回值语义：Python 侧返回**显示名**（调用方是 ``combo.setCurrentText(...)``）；
        C# 侧 ``MatchPackageFromPath`` 返回的是 ``PackageNameTypes.Values`` 即**前缀值**
        （调用方是 ``combo.SelectedValue = ...``）。两边机制不同但各自自洽，
        **不要"对齐"成 C# 的返回类型**，否则 Python 的下拉框会选中失败。

    ⚠️ 与 C# 的另一处差异（**刻意保留 Python 现状，未改**）：
        C# 用 ``StartsWith(prefix.TrimEnd('-'))``（**开头**匹配，且去掉尾部 ``-``）；
        Python 一直用 ``find(prefix) != -1``（**包含**匹配，且含尾部 ``-``）。
        对 MuMu 真实文件名（一定以包名开头）两者结果相同；
        但形如 ``my-com.RoamingStar.BlueArchive-x.json`` 的文件，Python 会判成官服、
        C# 会判成"其他"。**要改需先确认影响面。**
    """
    name_no_ext = os.path.splitext(os.path.basename(file_path or ""))[0]
    for pkg_name in REAL_PACKAGE_NAMES:
        prefix = PACKAGE_PREFIX_BY_NAME.get(pkg_name, "")
        if prefix and name_no_ext.find(prefix) != -1:
            return pkg_name
    return OTHER_PACKAGE_NAME


def filter_keymap_files(
    filenames: List[str],
    prefix: str,
    *,
    only_json: bool = True,
) -> List[Tuple[str, str]]:
    """按包名前缀筛选键位文件，返回 ``[(显示名, 原始文件名)]``。

    对应 C# ``ComboBoxInitializer.TryGetJsonFiles`` 的筛选规则：

    - ``prefix == UNIVERSE_PREFIX``：宇宙服无基础键位 → 空
    - ``prefix == OTHER_PREFIX``：只留**不含任何**已知前缀的 → 显示名 = 文件名去扩展名
    - ``prefix`` 已知：只留**包含**该前缀的 → 显示名 = 去掉前缀
    - ``prefix`` 为空：不过滤（保留调用方原有行为）

    ⚠️ **``only_json`` 必须要调用方显式选，不能有默认值的想当然**：
        合并前两份实现**本来就不一致**，差分验证时才发现 ——

        ===========================  ============  ==============
        调用点                        是否过滤扩展名  后果
        ===========================  ============  ==============
        ``main_window``（不过滤）     否            MuMu 目录里的
                                                    ``*~RF*.TMP`` 临时文件、
                                                    ``.txt`` 会被列进下拉框
        ``wide_screen_form``（过滤）  是            只列 ``.json``
        ===========================  ============  ==============

        按「重构不改行为」的原则，这里**不做统一**，由调用方传自己的值。
        要不要把 main_window 也改成过滤，属于需要单独确认的行为变更，
        详见项目交接文档的待办区。

    Args:
        filenames: 通常是 ``os.listdir(folder)`` 的结果，可含非 .json。
        prefix: 包名前缀，来自 :data:`PACKAGE_PREFIX_BY_NAME`。
        only_json: True 时只保留 ``.json``（大小写不敏感）。

    Returns:
        ``[(display_text, filename)]``，filename 是**原始文件名**（不含目录），
        由调用方自行拼路径 —— 与两个调用点原有的做法一致。
    """
    if prefix == UNIVERSE_PREFIX:
        return []

    result: List[Tuple[str, str]] = []
    for f in filenames:
        if only_json and not f.lower().endswith(".json"):
            continue
        stem = os.path.splitext(f)[0]

        if prefix == OTHER_PREFIX:
            if not any(p in stem for p in REAL_PACKAGE_PREFIXES):
                result.append((stem, f))
        elif prefix and prefix in stem:
            result.append((stem.replace(prefix, ""), f))
        elif not prefix:
            result.append((stem, f))
    return result


def package_prefix(display_name: str, default: Optional[str] = None) -> Optional[str]:
    """显示名 → 前缀；未知显示名返回 ``default``。

    对应 C# ``PackageNameTypes.TryGetValue``；调用点过去写的是
    ``_PACKAGE_PREFIXES.get(pkg, "other")``，即默认值取 ``"other"``。
    """
    return PACKAGE_PREFIX_BY_NAME.get(display_name, default)
