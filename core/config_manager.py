"""配置管理器 — 对应 C# ConfigManager

功能：
- 9 字段 config.json 读写（与 C# 版键名完全一致）
- 原子写入（.tmp → replace/move）
- 降级路径（程序目录 → LocalAppData）
- safe_load：损坏配置自动备份 + 重置默认值
- FindKey/ResetKey 以字符串形式存储（Python 版简化，无 Keys 枚举依赖）
"""
import json
import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from core.json_editor import write_text_atomic
from core.key_names import canonical_hotkey_key

# 数据根目录解析 — 统一源码版与打包 exe 的行为
# 优先级：环境变量 MOMO_DATA_DIR > exe 所在目录(打包) > 项目根(源码)
def resolve_app_dir() -> str:
    import sys as _sys
    env_dir = os.environ.get("MOMO_DATA_DIR", "").strip()
    if env_dir:
        return env_dir
    if getattr(_sys, 'frozen', False):
        return str(Path(_sys.executable).parent)
    return str(Path(__file__).parent.parent)


DEFAULT_CONFIG: Dict[str, object] = {
    "ConfigVersion": 1,
    "FX": "1280",
    "FY": "720",
    "FindKey": "D",
    "ResetKey": "F",
    "JsonFolderPath": "",
    "Resolution4String": "",
    "AdbPort": "16384",
    "AdbPath": "",
    "AdbPortsHistory": "16384",
}

_CONFIG_KEYS = list(DEFAULT_CONFIG.keys())


class ConfigManager:
    """配置管理器 — 读/写 config.json，与 C# 版兼容

    两个使用要点：

    1. **用 ``ConfigManager.shared()`` 而不是 ``ConfigManager()``。**
       每个面板各 new 一份会各持一份内存快照，而落盘写的是**整份**配置 ——
       于是「打开鼠标回溯面板保存热键」会把主窗口期间更新的 JsonFolderPath
       用旧快照覆盖回去。共享实例可消除这种配置脑裂。
    2. **连续改多个字段时用 ``with cfg.batch():``**。
       默认每个 setter 都会立即落盘（改完即持久化，不会丢配置）；
       在 batch 内则延后到退出时统一写一次，把 N 次写盘压成 1 次。
    """

    _shared: Optional["ConfigManager"] = None
    _shared_lock = threading.Lock()

    @classmethod
    def shared(cls) -> "ConfigManager":
        """返回进程内共享的配置实例（全项目统一入口，见类文档第 1 点）。"""
        with cls._shared_lock:
            if cls._shared is None:
                cls._shared = cls()
            return cls._shared

    @classmethod
    def reset_shared(cls) -> None:
        """丢弃共享实例（仅供测试使用；下次 shared() 会重新加载）。"""
        with cls._shared_lock:
            cls._shared = None

    def __init__(self, design_time: bool = False):
        self._values: Dict[str, object] = dict(DEFAULT_CONFIG)
        self._save_lock = threading.Lock()
        self._is_design_time = design_time
        self._warned_about_fallback = False
        self._active_config_path: Optional[str] = None
        # batch() 的嵌套深度 > 0 时，setter 只置脏标记、不落盘
        self._batch_lock = threading.Lock()
        self._batch_depth = 0

        # 路径计算 — 统一走 resolve_app_dir（支持 MOMO_DATA_DIR 覆盖）
        _app_dir = resolve_app_dir()
        self._primary_config_path = str(Path(_app_dir) / "config.json")
        self._fallback_config_path = str(
            Path(os.environ.get(
                "LOCALAPPDATA",
                os.path.expanduser("~")
            )) / "MuMu摸点小助手" / "config.json"
        )

        if self._is_design_time:
            return

        try:
            if not self._try_load_config():
                existing = self._find_existing_config_path()
                if existing is not None:
                    try:
                        backup_path = existing + ".corrupted_backup"
                        shutil.copy2(existing, backup_path)
                    except Exception:
                        pass
                else:
                    self._save_internal()
        except Exception:
            pass

    # ── 公共 API ──

    def save(self) -> bool:
        return self._save_internal()

    @contextmanager
    def batch(self) -> Iterator["ConfigManager"]:
        """把一批属性修改合并成一次落盘。

        默认情况下每个 setter 都会立即落盘（改完即持久化 —— 这是刻意保留的语义，
        因为有调用点只赋值不显式 save，见 ui/main_window.py 的自动检测目录 / 分辨率）。
        连续改多个字段时用本上下文，把 N 次写盘压成 1 次::

            with cfg.batch():
                cfg.fx = "1920"
                cfg.fy = "1080"
                cfg.save()      # 批次内不会立即写，退出时统一写一次

        批次退出（含异常退出）时保证写一次。
        """
        with self._batch_lock:
            self._batch_depth += 1
        try:
            yield self
        finally:
            with self._batch_lock:
                self._batch_depth -= 1
                flush_now = self._batch_depth == 0
            if flush_now:
                self._save_internal()

    def reload(self) -> bool:
        if self._is_design_time:
            return False
        try:
            path = self._find_existing_config_path()
            if path is None:
                return False
            with open(path, "r", encoding="utf-8-sig") as f:
                config = json.load(f)
            if not isinstance(config, dict):
                return False
            for k, v in config.items():
                self._values[k] = v
            self._active_config_path = path
            return True
        except Exception:
            return False

    @classmethod
    def safe_load(cls, config_path: Path, defaults: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        """安全加载配置 — 损坏时自动恢复。返回合并后的配置 dict"""
        if defaults is None:
            defaults = dict(DEFAULT_CONFIG)

        if not config_path.exists():
            return {**defaults}

        try:
            with open(config_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("config is not a dict")
        except (json.JSONDecodeError, ValueError, OSError):
            # 配置损坏 → 备份旧文件
            backup_path = config_path.with_suffix(config_path.suffix + ".corrupted_backup")
            try:
                shutil.copy2(config_path, backup_path)
            except Exception:
                pass
            return {**defaults}

        # 默认值兜底 + 用户值覆盖
        merged = {**defaults}
        merged.update(data)
        return merged

    def load_into_keyboard_handler(self, handler: Any) -> None:
        """将 FindKey/ResetKey 加载到键盘处理器"""
        handler.find_key = self.find_key
        handler.reset_key = self.reset_key

    def _string_value(self, key: str, default: str = "") -> str:
        """显式 null 视为空字符串，缺省字段继续使用默认值。"""
        value = self._values.get(key, default)
        return "" if value is None else str(value)

    def save_from_keyboard_handler(self, handler: Any) -> None:
        self.find_key = handler.find_key
        self.reset_key = handler.reset_key

    # ── 属性 — 9 个字段 ──

    @property
    def fx(self) -> str:
        return self._string_value("FX", "1280")

    @fx.setter
    def fx(self, value: str) -> None:
        self._values["FX"] = value
        self._save_internal()

    @property
    def fy(self) -> str:
        return self._string_value("FY", "720")

    @fy.setter
    def fy(self, value: str) -> None:
        self._values["FY"] = value
        self._save_internal()

    @property
    def find_key(self) -> str:
        v = self._values.get("FindKey")
        if not v:
            return "D"
        try:
            canonical_hotkey_key(v)
            return str(v).upper()
        except ValueError:
            return "D"

    @find_key.setter
    def find_key(self, value: str) -> None:
        # 验证键值：确保为非空且至少 1 个字符，转换为大写
        if value and len(value) >= 1:
            self._values["FindKey"] = value.upper()
            self._save_internal()

    @property
    def reset_key(self) -> str:
        v = self._values.get("ResetKey")
        if not v:
            return "F"
        try:
            canonical_hotkey_key(v)
            return str(v).upper()
        except ValueError:
            return "F"

    @reset_key.setter
    def reset_key(self, value: str) -> None:
        # 验证键值：确保为非空且至少 1 个字符，转换为大写
        if value and len(value) >= 1:
            self._values["ResetKey"] = value.upper()
            self._save_internal()

    @property
    def json_folder_path(self) -> str:
        return self._string_value("JsonFolderPath")

    @json_folder_path.setter
    def json_folder_path(self, value: str) -> None:
        self._values["JsonFolderPath"] = value
        self._save_internal()

    @property
    def resolution4_string(self) -> str:
        return self._string_value("Resolution4String")

    @resolution4_string.setter
    def resolution4_string(self, value: str) -> None:
        self._values["Resolution4String"] = value
        self._save_internal()

    @property
    def adb_port(self) -> str:
        return self._string_value("AdbPort", "16384")

    @adb_port.setter
    def adb_port(self, value: str) -> None:
        self._values["AdbPort"] = value
        self._save_internal()

    @property
    def adb_path(self) -> str:
        return self._string_value("AdbPath")

    @adb_path.setter
    def adb_path(self, value: str) -> None:
        self._values["AdbPath"] = value
        self._save_internal()

    @property
    def adb_ports_history(self) -> str:
        return self._string_value("AdbPortsHistory", "16384")

    @adb_ports_history.setter
    def adb_ports_history(self, value: str) -> None:
        self._values["AdbPortsHistory"] = value
        self._save_internal()

    def get(self, key: str, default: object = "") -> object:
        return self._values.get(key, default)

    # ── 内部实现 ──

    def _try_load_config(self) -> bool:
        path = self._find_existing_config_path()
        if path is None:
            return False
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                config = json.load(f)
        except Exception:
            return False
        # 合法 JSON 但非对象（如 [] / "abc" / 123）→ 视为损坏，走 __init__ 备份分支，
        # 防止后续保存时用默认值静默覆盖原文件（与 reload/safe_load 策略对齐）
        if not isinstance(config, dict):
            return False
        self._active_config_path = path
        for k, v in config.items():
            self._values[k] = v
        return True

    def _find_existing_config_path(self) -> Optional[str]:
        if self._active_config_path is not None and os.path.isfile(self._active_config_path):
            return self._active_config_path
        if os.path.isfile(self._primary_config_path):
            return self._primary_config_path
        if os.path.isfile(self._fallback_config_path):
            return self._fallback_config_path
        return None

    def _save_internal(self) -> bool:
        """落盘入口：batch() 内只登记、不写文件；退出批次时由 batch() 统一写。"""
        if self._is_design_time:
            return False

        with self._batch_lock:
            if self._batch_depth > 0:
                return True

        return self._write_now()

    def _write_now(self) -> bool:
        """真正写文件（配置写盘唯一实现）。"""
        with self._save_lock:
            try:
                data: Dict[str, object] = {"ConfigVersion": 1}
                for key in _CONFIG_KEYS:
                    if key != "ConfigVersion":
                        data[key] = self._values.get(key, "")

                json_str = json.dumps(data, indent=2, ensure_ascii=False)
                target_path = self._active_config_path or self._primary_config_path

                if not self._try_atomic_write(target_path, json_str):
                    if target_path != self._fallback_config_path and self._try_atomic_write(
                        self._fallback_config_path, json_str
                    ):
                        self._active_config_path = self._fallback_config_path
                        if not self._warned_about_fallback:
                            self._warned_about_fallback = True
                    else:
                        return False
                else:
                    self._active_config_path = target_path
                return True
            except Exception:
                return False

    @staticmethod
    def _try_atomic_write(final_path: str, json_str: str) -> bool:
        """原子写入：委托到 core/json_editor.write_text_atomic（全项目唯一实现，
        保证 LF / 无 BOM 的约定不会在这里被漏改）。"""
        return write_text_atomic(final_path, json_str)
