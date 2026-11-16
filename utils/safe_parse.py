"""安全数值解析 — 对应 C# SafeParseHelper"""
import math


def try_parse_int(text: str) -> tuple[bool, int]:
    """尝试将字符串解析为 int"""
    try:
        return True, int(text)
    except (ValueError, TypeError):
        return False, 0


def try_parse_float(text: str) -> tuple[bool, float]:
    """尝试将字符串解析为 float"""
    try:
        value = float(text)
        return (True, value) if math.isfinite(value) else (False, 0.0)
    except (ValueError, TypeError):
        return False, 0.0


def get_int_or_default(text: str, default: int = 0) -> int:
    """解析 int，失败返回默认值"""
    ok, val = try_parse_int(text)
    return val if ok else default


def get_float_or_default(text: str, default: float = 0.0) -> float:
    """解析 float，失败返回默认值"""
    ok, val = try_parse_float(text)
    return val if ok else default
