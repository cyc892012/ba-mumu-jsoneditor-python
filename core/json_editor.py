"""
MuMu 键位 JSON 编辑器 — Python 移植版
对应 C# MuMuJsonEditor 静态类，操作 MuMu 模拟器键位 JSON 字符串。
"""

import base64
import ctypes
import gzip
import json
import math
import os
import re
import threading
from enum import Enum
from typing import Any, Optional
from core.coord_model import rel_to_px, snap_px

# ============================================================================
# 常量
# ============================================================================

TYPE_CLICK = "Click"
TYPE_MACRO = "Macro"
TYPE_BUNCH_CLICK = "BunchClick"

_VALID_TYPES = {TYPE_CLICK, TYPE_MACRO, TYPE_BUNCH_CLICK}

# ============================================================================
# 坐标转换方向枚举
# ============================================================================


class CoordinateConversionDirection(Enum):
    """鼠标屏幕坐标 → 模拟器内部坐标"""
    MOUSE_TO_SIMULATOR = "MouseToSimulator"
    """键位坐标 → 另一分辨率的键位坐标"""
    KEYMAP_TO_KEYMAP = "KeymapToKeymap"


# ============================================================================
# 内部辅助函数
# ============================================================================

_CURVE_REL_PATTERN = re.compile(r"curve_rel:mouse;\(([^,]+),([^)]+)\)")
_write_state = threading.local()


def get_last_write_error() -> str:
    """当前线程最近一次原子写失败原因，兼容原 bool 接口。"""
    return getattr(_write_state, "error", "")


def _parse_json(json_str: str) -> dict:
    """解析 JSON 字符串为 dict。"""
    return json.loads(json_str)


def _serialize_json(obj: Any) -> str:
    """序列化为 JSON 字符串：ensure_ascii=False、4 空格缩进、LF、无尾随换行，与 C# PrecisionJsonWriter 对齐。"""
    return json.dumps(obj, ensure_ascii=False, indent=4, allow_nan=False)


def _degrade(ex: Exception, default: Any) -> Any:
    """记录一次「被降级吞掉」的异常，并返回兜底值。返回值与 C# 版降级行为保持一致。

    调用方统一写法（避免 27 处各写一遍日志，也避免因漏写而继续静默）：:

        except Exception as ex:
            return _degrade(ex, -1)

    - 兜底值直接传字面量即可：每次调用都会重新求值，不存在共享可变对象的问题。
    - LogService 延迟导入：保持 core 的 import 轻量，且不引入 core → infra 的静态依赖。
    - LogService.error_ex 自带堆栈，函数名与行号都在里面，无需在此手工拼接。
    """
    try:
        from infra.log_service import LogService
        LogService.error_ex("JsonEditor", ex)
    except Exception:
        # 日志自身不可用也不能让调用方崩：降级到静默返回兜底值
        pass
    return default


# ============================================================================
# 读取 — 全项目唯一的「读文件 + 解析 JSON」入口
# ============================================================================


def read_json_file(file_path: str) -> tuple:
    """读取并解析 JSON 文件，返回 ``(原始文本, 解析结果)``。

    统一了 ``open(..., encoding="utf-8-sig")`` 这个编码约定（兼容带 BOM 的文件），
    过去这个三元组在 UI 层被手写了 4 处，抄漏一次就会读不了带 BOM 的键位文件。

    **刻意不吞异常**：调用方需要拿到异常消息来显示（如
    ``f"自动同步加载失败: {e}"``），所以这里让 ``OSError`` / ``JSONDecodeError``
    原样抛出。只要兜底值的地方请改用 ``*_from_file`` 系列（它们走 ``_degrade`` 记日志）。

    Returns:
        ``(content, parsed)`` —— 原始文本（写入时直接复用可保持字节不变）+ 解析后的对象。

    Raises:
        OSError: 文件不存在 / 无权限。
        json.JSONDecodeError: 内容不是合法 JSON。
    """
    with open(file_path, "r", encoding="utf-8-sig") as f:
        content = f.read()
    return content, json.loads(content)


# ============================================================================
# 落盘 — 全项目唯一的原子写实现
# ============================================================================


def write_text_atomic(file_path: str, text: str) -> bool:
    """原子写入文本文件：UTF-8（无 BOM）+ LF 换行 + .tmp → replace。

    **键位 / 索引 / 配置这类必须与 C# 版逐字节对齐的文件，一律经此落盘。**
    这样 `newline="\\n"`（LF）这个约定全项目只有一处实现，不会因为某个 UI 面板
    自己写文件时漏掉参数而让 MuMu 读不了键位文件。

    Args:
        file_path: 目标文件绝对路径；父目录不存在会自动创建。
        text: 已序列化好的文本（键位 JSON 请先用 _serialize_json 生成）。

    Returns:
        True 表示写入成功；False 表示失败（异常已记入日志）。
    """
    _write_state.error = ""
    tmp_path = None
    try:
        dir_name = os.path.dirname(file_path)
        if dir_name and not os.path.isdir(dir_name):
            os.makedirs(dir_name, exist_ok=True)

        tmp_path = file_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

        # 替换失败必须保留原文件，不能退化为可能写到一半的覆盖复制。
        if os.path.isfile(file_path):
            os.replace(tmp_path, file_path)
        else:
            os.rename(tmp_path, file_path)
        return True
    except Exception as ex:
        _write_state.error = str(ex)
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return _degrade(ex, False)


def _get_keymaps(json_obj: dict) -> list:
    """获取 keymaps 数组，不存在则返回空列表。"""
    value = json_obj.get("keymaps", []) if isinstance(json_obj, dict) else []
    return value if isinstance(value, list) else []


def _key_field(item) -> dict:
    """畸形条目不能中断其余有效按键的查询与统计。"""
    value = item.get("key") if isinstance(item, dict) else None
    return value if isinstance(value, dict) else {}


def _get_key_by_virtual_key(json_obj: dict, virtual_key: int) -> Optional[dict]:
    """根据 virtual_key 查找按键对象，未找到返回 None。"""
    for item in _get_keymaps(json_obj):
        key_field = item.get("key") if isinstance(item, dict) else None
        if isinstance(key_field, dict) and key_field.get("virtual_key") == virtual_key:
            return item
    return None


def _get_key_by_text(json_obj: dict, key_text: str) -> Optional[dict]:
    """根据 key.text 查找按键对象，未找到返回 None。"""
    for item in _get_keymaps(json_obj):
        key_field = item.get("key") if isinstance(item, dict) else None
        if isinstance(key_field, dict) and key_field.get("text") == key_text:
            return item
    return None


def _get_key_index_by_virtual_key(json_obj: dict, virtual_key: int) -> int:
    """根据 virtual_key 查找按键在 keymaps 中的索引，未找到返回 -1。"""
    keymaps = _get_keymaps(json_obj)
    for i, item in enumerate(keymaps):
        key_field = item.get("key") if isinstance(item, dict) else None
        if isinstance(key_field, dict) and key_field.get("virtual_key") == virtual_key:
            return i
    return -1


def _get_key_index_by_text(json_obj: dict, key_text: str) -> int:
    """根据 key.text 查找按键在 keymaps 中的索引，未找到返回 -1。"""
    keymaps = _get_keymaps(json_obj)
    for i, item in enumerate(keymaps):
        key_field = item.get("key") if isinstance(item, dict) else None
        if isinstance(key_field, dict) and key_field.get("text") == key_text:
            return i
    return -1


def _parse_curve_rel_coords(press_actions: list) -> Optional[tuple[str, str]]:
    """从 press_actions 中解析 curve_rel:mouse;(x,y) 坐标。"""
    if not press_actions:
        return None
    for action in press_actions:
        if isinstance(action, str) and action.startswith("curve_rel:mouse;("):
            m = _CURVE_REL_PATTERN.match(action)
            if m:
                return (m.group(1).strip(), m.group(2).strip())
            break
    return None


def _set_rel_position(pos_obj: Optional[dict], x: float, y: float) -> None:
    """设置 rel_position / rel_work_position 的 rel_x、rel_y。"""
    if pos_obj is None:
        return
    pos_obj["rel_x"] = x
    pos_obj["rel_y"] = y


def get_scan_code(virtual_key: int) -> int:
    """虚拟键码 → 扫描码，对应 C# MuMuJsonEditor.GetScanCode（MapVirtualKey(vk, 0)）。

    仅 Windows 有效；调用失败返回 0。
    """
    if not virtual_key:
        return 0
    try:
        return int(ctypes.windll.user32.MapVirtualKeyW(int(virtual_key), 0))
    except Exception as ex:
        return _degrade(ex, 0)


# ============================================================================
# 按键查找
# ============================================================================


def find_key_by_virtual_key(json_str: str, virtual_key: int) -> int:
    """根据 virtual_key 查找按键在 keymaps 中的索引，未找到返回 -1。"""
    if not json_str or virtual_key < 0:
        return -1
    try:
        return _get_key_index_by_virtual_key(_parse_json(json_str), virtual_key)
    except Exception as ex:
        return _degrade(ex, -1)


def find_key_by_text(json_str: str, key_text: str) -> int:
    """根据 key.text 查找按键在 keymaps 中的索引，未找到返回 -1。"""
    if not json_str or not key_text:
        return -1
    try:
        return _get_key_index_by_text(_parse_json(json_str), key_text)
    except Exception as ex:
        return _degrade(ex, -1)


def find_type_by_virtual_key(json_str: str, virtual_key: int) -> str:
    """根据 virtual_key 返回按键类型（Click/Macro/BunchClick），未找到返回空串。"""
    if not json_str:
        return ""
    try:
        key = _get_key_by_virtual_key(_parse_json(json_str), virtual_key)
        return key.get("type", "") if key else ""
    except Exception as ex:
        return _degrade(ex, "")


def find_type_by_text(json_str: str, key_text: str) -> str:
    """根据 key.text 返回按键类型，未找到返回空串。"""
    if not json_str or not key_text:
        return ""
    try:
        key = _get_key_by_text(_parse_json(json_str), key_text)
        return key.get("type", "") if key else ""
    except Exception as ex:
        return _degrade(ex, "")


def check_type(json_str: str, virtual_key: int) -> bool:
    """检查按键类型是否为 Click / Macro / BunchClick 之一。"""
    t = find_type_by_virtual_key(json_str, virtual_key)
    return t in _VALID_TYPES


# ============================================================================
# 坐标修改（ReKey）
# ============================================================================


def re_key_by_virtual_key(json_str: str, virtual_key: int, x: str, y: str) -> Optional[str]:
    """根据 virtual_key 修改按键坐标，返回新 JSON 字符串；Macro 找不到 curve_rel 返回 None。"""
    if not json_str:
        return None
    try:
        dx = float(x)
        dy = float(y)
        if not (math.isfinite(dx) and math.isfinite(dy)):
            return None
    except (ValueError, TypeError):
        return json_str

    try:
        obj = _parse_json(json_str)
        key = _get_key_by_virtual_key(obj, virtual_key)
        if key is None:
            return json_str

        key_type = key.get("type", "")
        if key_type == TYPE_CLICK or key_type == TYPE_BUNCH_CLICK:
            _set_rel_position(key.get("icon", {}).get("rel_position"), dx, dy)
            _set_rel_position(key.get("rel_work_position"), dx, dy)
            return _serialize_json(obj)
        elif key_type == TYPE_MACRO:
            press_actions = key.get("press_actions")
            if not press_actions:
                return None
            for i, action in enumerate(press_actions):
                match = _CURVE_REL_PATTERN.match(action) if isinstance(action, str) else None
                if match:
                    # 只改首个坐标段，保留用户曲线的其余点与动作后缀。
                    press_actions[i] = f"curve_rel:mouse;({x},{y})" + action[match.end():]
                    return _serialize_json(obj)
            return None
        return _serialize_json(obj)
    except Exception as ex:
        return _degrade(ex, None)


def re_key_by_text(json_str: str, key_text: str, x: str, y: str) -> Optional[str]:
    """根据 key.text 修改按键坐标，返回新 JSON 字符串；Macro 找不到 curve_rel 返回 None。"""
    if not json_str or not key_text:
        return None
    try:
        dx = float(x)
        dy = float(y)
        if not (math.isfinite(dx) and math.isfinite(dy)):
            return None
    except (ValueError, TypeError):
        return json_str

    try:
        obj = _parse_json(json_str)
        key = _get_key_by_text(obj, key_text)
        if key is None:
            return json_str

        key_type = key.get("type", "")
        if key_type == TYPE_CLICK or key_type == TYPE_BUNCH_CLICK:
            _set_rel_position(key.get("icon", {}).get("rel_position"), dx, dy)
            _set_rel_position(key.get("rel_work_position"), dx, dy)
            return _serialize_json(obj)
        elif key_type == TYPE_MACRO:
            press_actions = key.get("press_actions")
            if not press_actions:
                return None
            for i, action in enumerate(press_actions):
                match = _CURVE_REL_PATTERN.match(action) if isinstance(action, str) else None
                if match:
                    # 只改首个坐标段，保留用户曲线的其余点与动作后缀。
                    press_actions[i] = f"curve_rel:mouse;({x},{y})" + action[match.end():]
                    return _serialize_json(obj)
            return None
        return _serialize_json(obj)
    except Exception as ex:
        return _degrade(ex, None)


# ============================================================================
# 坐标读取
# ============================================================================


def read_key_by_virtual_key(json_str: str, virtual_key: int) -> tuple[str, str]:
    """根据 virtual_key 读取按键坐标 (x, y)，未找到返回 ("0", "0")。"""
    if not json_str:
        return ("0", "0")
    try:
        obj = _parse_json(json_str)
        key = _get_key_by_virtual_key(obj, virtual_key)
        if key is None:
            return ("0", "0")

        key_type = key.get("type", "")
        if key_type == TYPE_CLICK or key_type == TYPE_BUNCH_CLICK:
            rwp = key.get("rel_work_position")
            if rwp is None:
                return ("0", "0")
            return (str(rwp.get("rel_x", 0.0)), str(rwp.get("rel_y", 0.0)))
        elif key_type == TYPE_MACRO:
            press_actions = key.get("press_actions")
            coords = _parse_curve_rel_coords(press_actions)
            return coords if coords else ("0", "0")
        return ("0", "0")
    except Exception as ex:
        return _degrade(ex, ("0", "0"))


def read_key_by_text(json_str: str, key_text: str) -> tuple[str, str]:
    """根据 key.text 读取按键坐标 (x, y)，未找到返回 ("0", "0")。"""
    if not json_str or not key_text:
        return ("0", "0")
    try:
        obj = _parse_json(json_str)
        key = _get_key_by_text(obj, key_text)
        if key is None:
            return ("0", "0")

        key_type = key.get("type", "")
        if key_type == TYPE_CLICK or key_type == TYPE_BUNCH_CLICK:
            rwp = key.get("rel_work_position")
            if rwp is None:
                return ("0", "0")
            return (str(rwp.get("rel_x", 0.0)), str(rwp.get("rel_y", 0.0)))
        elif key_type == TYPE_MACRO:
            press_actions = key.get("press_actions")
            coords = _parse_curve_rel_coords(press_actions)
            return coords if coords else ("0", "0")
        return ("0", "0")
    except Exception as ex:
        return _degrade(ex, ("0", "0"))


def try_read_key_coordinates(json_str: str, vk=None, key_text=None) -> Optional[tuple[str, str]]:
    """读取有效坐标；失败返回 None，合法零坐标仍返回坐标。"""
    try:
        obj = _parse_json(json_str)
        key = (_get_key_by_virtual_key(obj, vk) if vk is not None
               else _get_key_by_text(obj, key_text))
        if key is None:
            return None
        if key.get("type") in (TYPE_CLICK, TYPE_BUNCH_CLICK):
            position = key.get("rel_work_position")
            if not isinstance(position, dict) or not all(k in position for k in ("rel_x", "rel_y")):
                return None
            coords = (str(position["rel_x"]), str(position["rel_y"]))
        elif key.get("type") == TYPE_MACRO:
            coords = _parse_curve_rel_coords(key.get("press_actions"))
        else:
            return None
        if coords and all(math.isfinite(float(v)) for v in coords):
            return coords
    except Exception as ex:
        return _degrade(ex, None)
    return None


def read_key_from_file_by_virtual_key(file_path: str, vk: int) -> Optional[str]:
    """按真实 VK 读取完整预设键块，保留宏与扩展字段。"""
    try:
        _, obj = read_json_file(file_path)
        key = _get_key_by_virtual_key(obj, vk)
        return _serialize_json(key) if key is not None else None
    except Exception as ex:
        return _degrade(ex, None)


def delete_keys_by_indexes(json_str: str, indexes) -> str:
    """按原数组身份删除，避免同 VK 的区外按键被连带删除。"""
    try:
        obj = _parse_json(json_str)
        keymaps = _get_keymaps(obj)
        valid = {i for i in indexes if isinstance(i, int) and not isinstance(i, bool)
                 and 0 <= i < len(keymaps)}
        if not valid:
            return json_str
        for i in sorted(valid, reverse=True):
            keymaps.pop(i)
        return _serialize_json(obj)
    except Exception as ex:
        return _degrade(ex, json_str)


# ============================================================================
# JSON 读取/写入
# ============================================================================


def read_keys(json_str: str) -> str:
    """返回 keymaps 数组的 JSON 字符串。"""
    if not json_str:
        return "[]"
    try:
        obj = _parse_json(json_str)
        keymaps = obj.get("keymaps", [])
        return _serialize_json(keymaps)
    except Exception as ex:
        return _degrade(ex, "[]")


def write_keys(keys_json: str, target_json: str) -> Optional[str]:
    """将按键 JSON（单个对象或数组）写入目标 JSON 的 keymaps 中，返回新 JSON 字符串。

    写入策略（对应 C# MuMuJsonEditor.WriteKeys 的「虚拟键唯一」语义）：
    - 若新键的 virtual_key（>0）已在 keymaps 中存在，则**替换**该键（更新坐标/类型等），
      而非追加重复键。这样保证「一个物理键 → 一个映射」的不变量，避免重复 vk 产生非法键位。
    - virtual_key 为 0/缺失时（退化键）按原有行为直接追加。
    - 全新的 virtual_key 则追加。
    """
    if not keys_json or not target_json:
        return None
    try:
        target = _parse_json(target_json)
        if not isinstance(target, dict):
            return None
        if not isinstance(target.get("keymaps"), list):
            target["keymaps"] = []
        keymaps = target["keymaps"]

        new_keys = json.loads(keys_json.strip())
        if isinstance(new_keys, dict):
            items = [new_keys]
        elif isinstance(new_keys, list):
            items = new_keys
        else:
            return None

        # 建立 virtual_key → 现有索引 的映射（仅 vk>0 参与替换）
        existing_by_vk: dict = {}
        for idx, item in enumerate(keymaps):
            key_field = item.get("key") if isinstance(item, dict) else None
            vk = key_field.get("virtual_key") if isinstance(key_field, dict) else None
            if vk is not None and vk != 0:
                existing_by_vk.setdefault(vk, idx)

        for nk in items:
            if not isinstance(nk, dict) or not isinstance(nk.get("key"), dict):
                return None
            vk = (nk.get("key") or {}).get("virtual_key")
            if vk is not None and vk != 0 and vk in existing_by_vk:
                keymaps[existing_by_vk[vk]] = nk
            else:
                keymaps.append(nk)

        return _serialize_json(target)
    except Exception as ex:
        return _degrade(ex, None)


# ============================================================================
# 去重 / 冲突检测
# ============================================================================


def find_key_values(json_str: str) -> list[int]:
    """返回 keymaps 中所有 virtual_key 值的列表。"""
    if not json_str:
        return []
    try:
        obj = _parse_json(json_str)
        result = []
        for item in _get_keymaps(obj):
            kf = _key_field(item)
            if kf and "virtual_key" in kf:
                result.append(kf["virtual_key"])
        return result
    except Exception as ex:
        return _degrade(ex, [])


def are_all_keys_missing(keys: list[int], target_json: str) -> bool:
    """检查所有 virtual_key 是否都不存在于目标 JSON 中。"""
    if not target_json:
        return True
    try:
        obj = _parse_json(target_json)
        existing = set()
        for item in _get_keymaps(obj):
            kf = _key_field(item)
            if kf and "virtual_key" in kf:
                existing.add(kf["virtual_key"])
        return all(k not in existing for k in keys)
    except Exception as ex:
        return _degrade(ex, True)


def find_key_texts(json_str: str) -> list[str]:
    """返回 keymaps 中所有 key.text 值的列表。"""
    if not json_str:
        return []
    try:
        obj = _parse_json(json_str)
        result = []
        for item in _get_keymaps(obj):
            kf = _key_field(item)
            if kf and "text" in kf:
                result.append(kf["text"])
        return result
    except Exception as ex:
        return _degrade(ex, [])


def find_all_repeat_key_texts(keys: list[str], target_json: str) -> list[str]:
    """返回在目标 JSON 中已存在的 key.text 列表（冲突项）。"""
    if not keys or not target_json:
        return []
    try:
        obj = _parse_json(target_json)
        existing = set()
        for item in _get_keymaps(obj):
            kf = _key_field(item)
            if kf and "text" in kf:
                existing.add(kf["text"])
        return [k for k in keys if k in existing]
    except Exception as ex:
        return _degrade(ex, [])


def find_all_repeat_key_values(keys: list[int], target_json: str) -> list[int]:
    """返回在目标 JSON 中已存在的 virtual_key 列表（冲突项）。"""
    if not keys or not target_json:
        return []
    try:
        obj = _parse_json(target_json)
        existing = set()
        for item in _get_keymaps(obj):
            kf = _key_field(item)
            if kf and "virtual_key" in kf:
                existing.add(kf["virtual_key"])
        return [k for k in keys if k in existing]
    except Exception as ex:
        return _degrade(ex, [])


# ============================================================================
# 区域搜索
# ============================================================================


def find_range_key_values(
    range_lt: tuple[float, float],
    range_rd: tuple[float, float],
    json_str: str,
    screen_w: int = 1280,
    screen_h: int = 720,
) -> list[tuple[float, float, str, int]]:
    """按实际像素落点判断开区间命中；范围参数是连续归一化屏幕边界。
    每项仍为 (rel_x, rel_y, text, virtual_key)，保留调用方的数据契约。
    """
    if not json_str:
        return []
    try:
        obj = _parse_json(json_str)
        results = []
        for item in _get_keymaps(obj):
            if not isinstance(item, dict):
                continue
            icon = item.get("icon")
            if not isinstance(icon, dict):
                continue
            rel_pos = icon.get("rel_position")
            if not isinstance(rel_pos, dict):
                continue
            rel_x = float(rel_pos.get("rel_x", 0.0))
            rel_y = float(rel_pos.get("rel_y", 0.0))
            x, y = rel_to_px(rel_x, screen_w), rel_to_px(rel_y, screen_h)
            if (range_lt[0] * (screen_w - 1) < x < range_rd[0] * (screen_w - 1)
                    and range_lt[1] * (screen_h - 1) < y < range_rd[1] * (screen_h - 1)):
                key_field = _key_field(item)
                text = key_field.get("text", "")
                virtual_key = key_field.get("virtual_key", 0)
                results.append((rel_x, rel_y, text, virtual_key))
        return results
    except Exception as ex:
        return _degrade(ex, [])


# ============================================================================
# 删除
# ============================================================================


def delete_key_by_virtual_key(virtual_key: int, json_str: str) -> str:
    """根据 virtual_key 删除一个按键，返回新 JSON 字符串。"""
    if not json_str:
        return json_str or ""
    try:
        obj = _parse_json(json_str)
        keymaps = _get_keymaps(obj)
        idx = _get_key_index_by_virtual_key(obj, virtual_key)
        if idx >= 0:
            keymaps.pop(idx)
        return _serialize_json(obj)
    except Exception as ex:
        return _degrade(ex, json_str)


def delete_keys_by_virtual_keys(keys: list[int], json_str: str) -> str:
    """根据 virtual_key 列表批量删除按键（逆序遍历保证安全），返回新 JSON 字符串。"""
    if not json_str or not keys:
        return json_str or ""
    try:
        obj = _parse_json(json_str)
        keymaps = _get_keymaps(obj)
        to_remove = set(keys)
        for i in range(len(keymaps) - 1, -1, -1):
            kf = _key_field(keymaps[i])
            if kf and kf.get("virtual_key") in to_remove:
                keymaps.pop(i)
        return _serialize_json(obj)
    except Exception as ex:
        return _degrade(ex, json_str)


# ============================================================================
# 创建按键
# ============================================================================


def create_key(
    key_type: str,
    key_text: str,
    key_x: str,
    key_y: str,
    scan_code: str,
    virtual_key: str,
) -> Optional[str]:
    """生成新按键的 JSON 字符串。支持 Click、BunchClick 和 Macro。"""
    if not key_type or not key_text:
        return None

    try:
        # 提前解析坐标并校验有限性：NaN/Inf 会产生损坏的键位 JSON（rel_x=nan 会使后续
        # 所有坐标比较失效）。forms 传入的是 str(float)，正常不会触发；此处兜底拒绝。
        d_key_x = float(key_x)
        d_key_y = float(key_y)
        if not (math.isfinite(d_key_x) and math.isfinite(d_key_y)):
            return None

        if key_type == TYPE_CLICK or key_type == TYPE_BUNCH_CLICK:
            return _serialize_json({
                "editor_icon_scale": 1,
                "icon": {
                    "background_color": "00000066",
                    "description": "",
                    "radius_correction": 1,
                    "rel_position": {"rel_x": d_key_x, "rel_y": d_key_y},
                    "visibility": True,
                },
                "key": {
                    "device": "keyboard",
                    "scan_code": int(scan_code),
                    "text": key_text,
                    "virtual_key": int(virtual_key),
                },
                "rel_work_position": {"rel_x": d_key_x, "rel_y": d_key_y},
                "type": key_type,
            })
        elif key_type == TYPE_MACRO:
            key_position_set = 0.03
            key_position_x = (d_key_x + key_position_set) if (d_key_x + key_position_set) < 1 else (d_key_x - key_position_set)
            key_position_y = (d_key_y + key_position_set) if (d_key_y + key_position_set) < 1 else (d_key_y - key_position_set)
            return _serialize_json({
                "editor_icon_scale": 1,
                "icon": {
                    "background_color": "00000066",
                    "description": "",
                    "radius_correction": 1,
                    "rel_position": {"rel_x": key_position_x, "rel_y": key_position_y},
                    "visibility": True,
                },
                "key": {
                    "device": "keyboard",
                    "scan_code": int(scan_code),
                    "text": key_text,
                    "virtual_key": int(virtual_key),
                },
                "press_actions": [
                    "start_loop:until_release",
                    "curve_first_point_sleep_time:1",
                    "curve_last_point_sleep_time:1",
                    f"curve_rel:mouse;({d_key_x},{d_key_y})",
                    # 与用户更新后可用的 A 宏一致；仅用于新建宏。
                    "sleep:1",
                    "stop_loop",
                ],
                "rel_work_position": {"rel_x": key_position_x, "rel_y": key_position_y},
                "release_actions": [],
                "type": "Macro",
            })
        return None
    except (ValueError, TypeError, Exception):
        return None


# ============================================================================
# 坐标转换
# ============================================================================


def calculate_coordinates(
    fx: int, fy: int, sx: int, sy: int,
    mx: float, my: float,
    direction: CoordinateConversionDirection,
    *, snap: bool = True,
) -> tuple[float, float, int]:
    """
    坐标转换核心算法。中间全程连续计算，仅最终目标像素对齐一次格点。
    snap=False 供连续理想值预览及多阶段计算使用，最后阶段才使用默认 snap=True。
    模型：小分辨率等比例缩放后居中放入大分辨率（letterbox/pillarbox）。

    - KeymapToKeymap：自动判断哪个是小/大分辨率，源→目标。
    - MouseToSimulator：FX/FY 为大（窗口），SX/SY 为小（模拟器），反向。

    返回值：(x, y, status)
    - status = -1：转换后的坐标超出目标边界
    - status = 0：正常
    """
    result = [0.0, 0.0, 0]

    if fx <= 0 or fy <= 0 or sx <= 0 or sy <= 0:
        return (result[0], result[1], result[2])

    if direction == CoordinateConversionDirection.KEYMAP_TO_KEYMAP:
        ratio1 = float(fx) / fy
        ratio2 = float(sx) / sy
        if ratio1 <= ratio2:
            small_w, small_h = fx, fy
            big_w, big_h = sx, sy
            source_is_small = True
        else:
            small_w, small_h = sx, sy
            big_w, big_h = fx, fy
            source_is_small = False
    else:
        # MouseToSimulator: FX/FY 始终是大，SX/SY 始终是小
        small_w, small_h = sx, sy
        big_w, big_h = fx, fy
        source_is_small = False

    small_aspect = small_w / small_h
    big_aspect = big_w / big_h

    if big_aspect >= small_aspect:
        fitted_h = big_h
        fitted_w = big_h * small_aspect
    else:
        fitted_w = big_w
        fitted_h = big_w / small_aspect

    scale = fitted_w / small_w
    offset_x = (big_w - fitted_w) / 2.0
    offset_y = (big_h - fitted_h) / 2.0

    if source_is_small:
        result[0] = mx * scale + offset_x
        result[1] = my * scale + offset_y
        # clamp
        if result[0] < 0:
            result[0] = 0.0
        if result[1] < 0:
            result[1] = 0.0
        if result[0] > big_w - 1:
            result[0] = big_w - 1.0
        if result[1] > big_h - 1:
            result[1] = big_h - 1.0
        result[2] = 0
    else:
        result[0] = (mx - offset_x) / scale
        result[1] = (my - offset_y) / scale
        result[2] = 0
        if (result[0] < 0 or result[1] < 0
                or result[0] > small_w - 1 or result[1] > small_h - 1):
            result[2] = -1
            if result[0] < 0:
                result[0] = 0.0
            if result[1] < 0:
                result[1] = 0.0
            if result[0] > small_w - 1:
                result[0] = small_w - 1.0
            if result[1] > small_h - 1:
                result[1] = small_h - 1.0

    if snap:
        result[0], result[1] = snap_px(result[0], sx), snap_px(result[1], sy)
    return (result[0], result[1], result[2])


def calculate_coordinates_mouse_to_simulator(
    fx: int, fy: int, sx: int, sy: int, mx: float, my: float,
) -> tuple[float, float, int]:
    """鼠标屏幕坐标 → 模拟器内部坐标（force 大→小反向）。"""
    return calculate_coordinates(fx, fy, sx, sy, mx, my, CoordinateConversionDirection.MOUSE_TO_SIMULATOR)


def calculate_coordinates_k_to_ck(
    fx: int, fy: int, sx: int, sy: int, mx: float, my: float,
    *, snap: bool = True,
) -> tuple[float, float, int]:
    """键位坐标 → 另一分辨率的键位坐标（auto-detect 大小）。"""
    return calculate_coordinates(fx, fy, sx, sy, mx, my, CoordinateConversionDirection.KEYMAP_TO_KEYMAP, snap=snap)


# ============================================================================
# 其他查询
# ============================================================================


def get_click_keys(json_str: str, screen_w: int, screen_h: int) -> list[dict]:
    """返回所有 type="Click" 的按键信息列表。
    每项为 {"key_text": str, "rel_x": float, "rel_y": float}，
    其中 rel_x/rel_y 是经 MuMu 取整后的实际像素落点。
    """
    if screen_w <= 0 or screen_h <= 0 or not json_str:
        return []
    try:
        obj = _parse_json(json_str)
        results = []
        for item in _get_keymaps(obj):
            if not isinstance(item, dict):
                continue
            if item.get("type") != TYPE_CLICK:
                continue
            rwp = item.get("rel_work_position")
            if not isinstance(rwp, dict):
                continue
            results.append({
                "key_text": _key_field(item).get("text", ""),
                "rel_x": rel_to_px(float(rwp.get("rel_x", 0.0)), screen_w),
                "rel_y": rel_to_px(float(rwp.get("rel_y", 0.0)), screen_h),
            })
        return results
    except Exception as ex:
        return _degrade(ex, [])


def read_all_keys(json_str: str) -> list[str]:
    """返回所有按键的 key.text 列表。"""
    if not json_str:
        return []
    try:
        obj = _parse_json(json_str)
        result = []
        for item in _get_keymaps(obj):
            kf = _key_field(item)
            if kf and "text" in kf:
                result.append(kf["text"])
        return result
    except Exception as ex:
        return _degrade(ex, [])


def get_key_count_from_obj(obj) -> int:
    """返回 keymaps 中含有 virtual_key 的按键总数（接受已解析对象，避免二次解析）。"""
    if not isinstance(obj, dict):
        return 0
    count = 0
    for item in _get_keymaps(obj):
        kf = _key_field(item)
        if kf and "virtual_key" in kf:
            count += 1
    return count


def get_key_count(json_str: str) -> int:
    """返回 keymaps 中含有 virtual_key 的按键总数。"""
    if not json_str:
        return 0
    try:
        return get_key_count_from_obj(_parse_json(json_str))
    except Exception as ex:
        return _degrade(ex, 0)


# ============================================================================
# 分辨率字符串序列化/反序列化
# ============================================================================


def resolution_to_string(resolution: dict[str, str]) -> str:
    """序列化分辨率字典为字符串。
    输入：{"key1": "w,h", "key2": "w2,h2"}
    输出："key1,w,hVkey2,w2,h2V"
    """
    if not resolution:
        return ""
    parts = []
    for key, value in resolution.items():
        vals = value.split(",")
        if len(vals) < 2:
            continue
        parts.append(f"{key},{vals[0]},{vals[1]}")
    return "V".join(parts) + ("V" if parts else "")


def string_to_resolution(resolution_string: str) -> dict[str, str]:
    """反序列化分辨率字符串为字典。
    输入："key1,w,hVkey2,w2,h2V"
    输出：{"key1": "w,h", "key2": "w2,h2"}
    """
    if not resolution_string or not resolution_string.strip():
        return {}
    result = {}
    entries = resolution_string.strip().split("V")
    for entry in entries:
        if not entry:
            continue
        parts = entry.split(",")
        if len(parts) < 3:
            continue
        result[parts[0]] = f"{parts[1]},{parts[2]}"
    return result


# ============================================================================
# 字符串压缩
# ============================================================================


def compress_to_base64(text: str) -> str:
    """GZip 压缩 + Base64 编码。"""
    if not text:
        return ""
    compressed = gzip.compress(text.encode("utf-8"))
    return base64.b64encode(compressed).decode("ascii")


def decompress_from_base64(data: str) -> str:
    """Base64 解码 + GZip 解压。"""
    if not data:
        return ""
    compressed = base64.b64decode(data)
    return gzip.decompress(compressed).decode("utf-8")


# ============================================================================
# 文件级操作（对应 C# 中接受文件路径的重载）
# ============================================================================


def find_key_values_from_file(file_path: str) -> list[int]:
    """从文件读取 JSON 并返回所有 virtual_key 值，出错返回 []。"""
    if not file_path:
        return []
    try:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            json_str = f.read()
        return find_key_values(json_str)
    except Exception as ex:
        return _degrade(ex, [])


def find_key_texts_from_file(file_path: str) -> list[str]:
    """从文件读取 JSON 并返回所有 key.text 值，出错返回 []。"""
    if not file_path:
        return []
    try:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            json_str = f.read()
        return find_key_texts(json_str)
    except Exception as ex:
        return _degrade(ex, [])


def read_key_from_file(file_path: str, key_text: str) -> Optional[str]:
    """从 JSON 文件读取指定按键（按 key.text）的完整 JSON 块，可作为新按键插入；未找到返回 None。"""
    if not file_path or not key_text:
        return None
    try:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            json_str = f.read()
        obj = _parse_json(json_str)
        key = _get_key_by_text(obj, key_text)
        if key is None:
            return None
        return _serialize_json(key)
    except Exception as ex:
        return _degrade(ex, None)


def read_keys_from_file(file_path: str) -> Optional[str]:
    """从 JSON 文件读取 keymaps 数组的 JSON 字符串；无 keymaps 返回 None。"""
    if not file_path:
        return None
    try:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            json_str = f.read()
        obj = _parse_json(json_str)
        keymaps = obj.get("keymaps")
        if keymaps is None:
            return None
        return _serialize_json(keymaps)
    except Exception as ex:
        return _degrade(ex, None)


# ============================================================================
# keymaps 保障
# ============================================================================


def get_or_create_keymaps(json_obj: dict) -> list:
    """确保 json_obj 中存在 keymaps 数组，不存在则创建并返回。"""
    if "keymaps" not in json_obj:
        json_obj["keymaps"] = []
    return json_obj["keymaps"]


# ============================================================================
# 区域删除
# ============================================================================


def delete_range_rd_keys(json_str: str) -> str:
    """删除右下角区域（rel_x > 0.661 且 rel_y > 0.798）的按键，返回新 JSON 字符串。"""
    if not json_str:
        return json_str or ""
    try:
        obj = _parse_json(json_str)
        keymaps = _get_keymaps(obj)
        # 逆序遍历，安全删除
        for i in range(len(keymaps) - 1, -1, -1):
            item = keymaps[i]
            icon = item.get("icon")
            if not icon:
                continue
            rel_pos = icon.get("rel_position")
            if not rel_pos:
                continue
            rel_x = float(rel_pos.get("rel_x", 0.0))
            rel_y = float(rel_pos.get("rel_y", 0.0))
            if rel_x > 0.661 and rel_y > 0.798:
                keymaps.pop(i)
        return _serialize_json(obj)
    except Exception as ex:
        return _degrade(ex, json_str)
