"""MuMu 键位 JSON 结构校验器 — 对应 6.12.2 节设计

使用 jsonschema 进行结构校验，jsonschema 不存在时降级为 JSON 语法校验。
"""
import json
from typing import Optional

# MuMu 键位方案最小合法结构
MINIMAL_SCHEMA = {
    "type": "object",
    "required": ["keymaps"],
    "properties": {
        "keymaps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type", "key", "icon"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["Click", "Macro", "BunchClick"],
                    },
                    "key": {
                        "type": "object",
                        "required": ["text", "virtual_key"],
                        "properties": {
                            "text": {"type": "string"},
                            "virtual_key": {"type": "integer"},
                            "device": {"type": "string"},
                            "scan_code": {"type": "integer"},
                        },
                    },
                    "icon": {
                        "type": "object",
                        "required": ["rel_position"],
                        "properties": {
                            "rel_position": {
                                "type": "object",
                                "required": ["rel_x", "rel_y"],
                                "properties": {
                                    "rel_x": {"type": "number"},
                                    "rel_y": {"type": "number"},
                                },
                            },
                            "background_color": {"type": "string"},
                            "description": {"type": "string"},
                            "visibility": {"type": "boolean"},
                            "radius_correction": {"type": "integer"},
                        },
                    },
                },
            },
        }
    },
}


class KeymapValidator:
    """MuMu 键位方案 JSON 结构校验器"""

    def __init__(self):
        try:
            import jsonschema
            self._validator = jsonschema.Draft7Validator(MINIMAL_SCHEMA)
            self._available = True
        except ImportError:
            self._available = False
            self._validator = None

    def validate(self, json_text: str) -> Optional[str]:
        """校验 JSON 结构。返回 None=合法，否则返回错误消息。"""
        # 1. JSON 语法校验
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            return f"JSON 格式错误: {e}"

        # 2. 结构校验（jsonschema 可选）
        if self._available and self._validator:
            errors = list(self._validator.iter_errors(data))
            if errors:
                msgs = "; ".join(e.message for e in errors[:3])
                return f"结构校验失败: {msgs}"

        return None
