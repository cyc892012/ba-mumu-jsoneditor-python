"""分辨率管理器 — 对应 C# ResolutionManager

管理四类分辨率预设（平板/手机/超宽屏/自定义），分辨率分类和持久化。
"""
from typing import Dict, Tuple

# 类型常量
TYPE_TABLET = "1"      # 平板
TYPE_PHONE = "2"       # 手机
TYPE_ULTRA_WIDE = "3"  # 超宽屏
TYPE_CUSTOM = "4"      # 自定义

# 预设分辨率字典（键: "WxH", 值: "W,H"）
TABLET_RESOLUTIONS: Dict[str, str] = {
    "1280x720": "1280,720",
    "1600x900": "1600,900",
    "1920x1080": "1920,1080",
    "2560x1440": "2560,1440",
}

PHONE_RESOLUTIONS: Dict[str, str] = {
    "720x1280": "720,1280",
    "900x1600": "900,1600",
    "1080x1920": "1080,1920",
    "1440x2560": "1440,2560",
}

ULTRA_WIDE_RESOLUTIONS: Dict[str, str] = {
    "1600x720": "1600,720",
    "1920x864": "1920,864",
    "2400x1080": "2400,1080",
    "2560x1080": "2560,1080",
    "3200x1440": "3200,1440",
    "3440x1440": "3440,1440",
}


class ResolutionManager:
    """分辨率管理器"""

    def __init__(self):
        pass

    @staticmethod
    def classify_resolution(
        fx_text: str,
        fy_text: str,
        custom_resolution_string: str = ""
    ) -> Tuple[str, Dict[str, str]]:
        """分类分辨率 — 返回 (类型码, 分辨率字典)"""
        if not fx_text or not fy_text:
            return TYPE_CUSTOM, {}

        key = f"{fx_text}x{fy_text}"

        if key in TABLET_RESOLUTIONS:
            return TYPE_TABLET, dict(TABLET_RESOLUTIONS)
        if key in PHONE_RESOLUTIONS:
            return TYPE_PHONE, dict(PHONE_RESOLUTIONS)
        if key in ULTRA_WIDE_RESOLUTIONS:
            return TYPE_ULTRA_WIDE, dict(ULTRA_WIDE_RESOLUTIONS)

        # 自定义
        result = {}
        if custom_resolution_string:
            result = ResolutionManager.string_to_resolution(custom_resolution_string)
        if key not in result:
            result[f"*{key}"] = f"{fx_text},{fy_text}"
        return TYPE_CUSTOM, result

    @staticmethod
    def get_resolution_dict_by_type(
        type_code: str,
        custom_resolution_string: str = ""
    ) -> Dict[str, str]:
        """根据类型码获取分辨率字典"""
        if type_code == TYPE_TABLET:
            return dict(TABLET_RESOLUTIONS)
        elif type_code == TYPE_PHONE:
            return dict(PHONE_RESOLUTIONS)
        elif type_code == TYPE_ULTRA_WIDE:
            return dict(ULTRA_WIDE_RESOLUTIONS)
        elif type_code == TYPE_CUSTOM:
            return ResolutionManager.string_to_resolution(custom_resolution_string)
        return {}

    @staticmethod
    def find_item(dct: Dict[str, str], key: str) -> Tuple[str, str]:
        """在字典中查找项 — 返回 (key, value)"""
        if key in dct:
            return key, dct[key]
        return "", ""

    @staticmethod
    def resolution_to_string(resolution: Dict[str, str]) -> str:
        """分辨率字典序列化为字符串 — 格式: "key1,w1,h1Vkey2,w2,h2V"（与 C# MuMuJsonEditor.ResolutionToString 一致）"""
        parts = []
        for k, v in resolution.items():
            vals = v.split(",")
            if len(vals) < 2:
                continue
            parts.append(f"{k},{vals[0]},{vals[1]}")
        return "V".join(parts) + ("V" if parts else "")

    @staticmethod
    def string_to_resolution(resolution_string: str) -> Dict[str, str]:
        """字符串反序列化为分辨率字典（V 分隔）"""
        result: Dict[str, str] = {}
        if not resolution_string:
            return result
        for entry in resolution_string.split("V"):
            entry = entry.strip()
            if not entry:
                continue
            items = entry.split(",")
            if len(items) >= 3:
                result[items[0]] = f"{items[1]},{items[2]}"
        return result
