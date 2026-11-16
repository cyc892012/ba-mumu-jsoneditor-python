"""索引文件备份管理器 — 对应 C# IndexFileBackupManager

功能：
- 根据方案文件路径定位索引文件并备份（SHA256 去重）
- 扫描孤儿方案（目录中存在但索引未注册的 .json 文件）
- 将孤儿方案添加回索引
- 从备份还原索引文件
- 清理旧备份

路径约定：
- 默认索引目录: 用户配置目录下的 MuMuPlayer/data/keymapConfig
- 备份目录: {base_directory}\\data\\index_backups\\{index_name}\\{timestamp}\\
"""
import hashlib
import json
import os
import shutil
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TypeVar

from core.json_editor import write_text_atomic
from infra.log_service import LogService

T = TypeVar("T")

KNOWN_SERVER_KEYS = ["官服", "B服", "日服", "国际服"]


class IndexFileBackupManager:
    """索引文件备份管理器 — 对应 C# IndexFileBackupManager"""

    def __init__(self, package_name_types: Dict[str, str], base_directory: str):
        """初始化备份管理器

        Args:
            package_name_types: 服务器类型到前缀的映射，如 {"官服":"官服", "B服":"B服", "日服":"日服", "国际服":"国际服"}
            base_directory: 程序根目录
        """
        self._base_directory = base_directory
        self._package_name_types = dict(package_name_types)
        self.last_error = ""

        # 默认索引目录
        user_profile = os.environ.get("USERPROFILE", "")
        self._index_directory = os.path.join(
            user_profile,
            "AppData", "Roaming", "Netease", "MuMuPlayer", "data", "keymapConfig",
        )

    # ── 属性 ──

    @property
    def index_directory(self) -> str:
        return self._index_directory

    @index_directory.setter
    def index_directory(self, value: str) -> None:
        self._index_directory = value or ""

    @property
    def backup_directory(self) -> str:
        return os.path.join(self._base_directory, "data", "index_backups")

    # ── 公共方法 ──

    def backup_index(self, scheme_file_path: str) -> bool:
        """通过方案文件路径定位索引文件并备份。SHA256 去重，相同则跳过。

        Args:
            scheme_file_path: 方案文件完整路径（如 {目录}\\官服-方案名.json）

        Returns:
            True 表示已创建新备份，False 表示跳过或失败
        """
        self.last_error = ""
        if not scheme_file_path:
            return False

        try:
            index_file_path = self.resolve_index_file_path(scheme_file_path)
            if index_file_path is None or not os.path.isfile(index_file_path):
                return False

            index_content = IndexFileBackupManager.retry_io(
                lambda: _read_all_text(index_file_path)
            )

            # 验证 JSON 格式
            try:
                if not isinstance(json.loads(index_content), dict):
                    return False
            except (json.JSONDecodeError, ValueError):
                return False

            # 检查方案是否在索引中
            if not IndexFileBackupManager.contains_scheme(index_content, scheme_file_path):
                return False

            # 计算哈希（SHA256，失败时降级为文件大小+修改时间）
            try:
                new_hash = IndexFileBackupManager.compute_sha256(index_content)
            except Exception:
                stat = os.stat(index_file_path)
                new_hash = f"{stat.st_size}_{int(stat.st_mtime * 10000000)}"

            # SHA256 去重
            last_hash = self.get_last_backup_hash(os.path.basename(index_file_path))
            if last_hash is not None and last_hash == new_hash:
                return False

            index_file_name = os.path.basename(index_file_path)
            index_name = os.path.splitext(index_file_name)[0]
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]
            backup_dir = os.path.join(self.backup_directory, index_name, timestamp)

            # 处理重名目录
            if os.path.isdir(backup_dir):
                suffix = 1
                while os.path.isdir(f"{backup_dir}_{suffix}"):
                    suffix += 1
                backup_dir = f"{backup_dir}_{suffix}"

            os.makedirs(backup_dir, exist_ok=True)
            tmp_path = os.path.join(backup_dir, "index.json.tmp")
            final_path = os.path.join(backup_dir, "index.json")

            IndexFileBackupManager.retry_io(
                lambda: IndexFileBackupManager.atomic_write_all_text(
                    tmp_path, final_path, index_content
                )
            )
            return True

        except Exception as ex:
            self.last_error = str(ex)
            LogService.error_ex(
                "IndexFileBackupManager", ex,
                f"BackupIndex 失败, schemeFilePath={scheme_file_path or '(null)'}"
            )
            return False

    def backup_index_direct(self, index_file_name: str) -> bool:
        """直接用索引文件名备份。

        Args:
            index_file_name: 索引文件名（如 "官服.json"）

        Returns:
            True 表示已创建新备份，False 表示跳过或失败
        """
        self.last_error = ""
        if not index_file_name:
            return False

        try:
            index_file_path = os.path.join(self._index_directory, index_file_name)
            if not os.path.isfile(index_file_path):
                self.last_error = "索引文件不存在"
                return False

            index_content = IndexFileBackupManager.retry_io(
                lambda: _read_all_text(index_file_path)
            )

            # 验证 JSON 格式
            try:
                if not isinstance(json.loads(index_content), dict):
                    self.last_error = "索引根节点必须为对象"
                    return False
            except (json.JSONDecodeError, ValueError):
                self.last_error = "索引 JSON 无效"
                return False

            # 计算哈希
            try:
                new_hash = IndexFileBackupManager.compute_sha256(index_content)
            except Exception:
                stat = os.stat(index_file_path)
                new_hash = f"{stat.st_size}_{int(stat.st_mtime * 10000000)}"

            # SHA256 去重
            last_hash = self.get_last_backup_hash(index_file_name)
            if last_hash is not None and last_hash == new_hash:
                return False

            index_name = os.path.splitext(index_file_name)[0]
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]
            backup_dir = os.path.join(self.backup_directory, index_name, timestamp)

            if os.path.isdir(backup_dir):
                suffix = 1
                while os.path.isdir(f"{backup_dir}_{suffix}"):
                    suffix += 1
                backup_dir = f"{backup_dir}_{suffix}"

            os.makedirs(backup_dir, exist_ok=True)
            tmp_path = os.path.join(backup_dir, "index.json.tmp")
            final_path = os.path.join(backup_dir, "index.json")

            IndexFileBackupManager.retry_io(
                lambda: IndexFileBackupManager.atomic_write_all_text(
                    tmp_path, final_path, index_content
                )
            )
            return True

        except Exception as ex:
            LogService.error_ex(
                "IndexFileBackupManager", ex,
                f"BackupIndexDirect 失败, indexFileName={index_file_name or '(null)'}"
            )
            self.last_error = str(ex)
            return False

    def restore_backup(self, index_file_name: str, timestamp: str) -> bool:
        """从备份还原索引文件。

        Args:
            index_file_name: 索引文件名（如 "官服.json"）
            timestamp: 备份时间戳

        Returns:
            True 表示还原成功，False 表示失败
        """
        self.last_error = ""
        index_dir = self._index_directory
        if not index_dir or not os.path.isdir(index_dir):
            self.last_error = "索引目录无效，请重新选择有效目录"
            return False
        index_file_path = os.path.join(index_dir, index_file_name)

        index_name = os.path.splitext(index_file_name)[0]
        backup_file_path = os.path.join(
            self.backup_directory, index_name, timestamp, "index.json"
        )

        if not os.path.isfile(backup_file_path):
            self.last_error = "备份文件不存在"
            return False

        try:
            content = IndexFileBackupManager.retry_io(
                lambda: _read_all_text(backup_file_path)
            )
            if not isinstance(json.loads(content), dict):
                raise ValueError("备份索引根节点必须为对象")
        except Exception as ex:
            self.last_error = str(ex)
            return False

        tmp_path = index_file_path + ".tmp"
        try:
            IndexFileBackupManager.retry_io(
                lambda: IndexFileBackupManager.atomic_write_all_text(
                    tmp_path, index_file_path, content
                )
            )
            return True
        except Exception as ex:
            self.last_error = str(ex)
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            LogService.error_ex(
                "IndexFileBackupManager", ex,
                f"RestoreBackup 失败, indexFileName={index_file_name}, timestamp={timestamp}"
            )
            return False

    def get_backups(self, index_file_name: str) -> list:
        """获取指定索引文件的所有备份信息列表。

        Args:
            index_file_name: 索引文件名（如 "官服.json"）

        Returns:
            [{"timestamp": str, "indexFileName": str, "schemeCount": int, "schemes": [...]}, ...]
        """
        result: list = []
        index_name = os.path.splitext(index_file_name)[0]
        backup_base_dir = os.path.join(self.backup_directory, index_name)

        if not os.path.isdir(backup_base_dir):
            return result

        try:
            dirs = [
                d for d in os.listdir(backup_base_dir)
                if os.path.isdir(os.path.join(backup_base_dir, d))
            ]
        except OSError:
            return result

        for dir_name in sorted(dirs, reverse=True):
            index_json_path = os.path.join(backup_base_dir, dir_name, "index.json")
            if not os.path.isfile(index_json_path):
                continue

            try:
                content = IndexFileBackupManager.retry_io(
                    lambda: _read_all_text(index_json_path)
                )
                data = json.loads(content)
                config = data.get("Config", [])
                schemes: list = []

                if isinstance(config, list):
                    for item in config:
                        if isinstance(item, dict):
                            schemes.extend(item.keys())

                result.append({
                    "timestamp": dir_name,
                    "indexFileName": index_file_name,
                    "schemeCount": len(schemes),
                    "schemes": schemes,
                })
            except Exception:
                continue

        return result

    def get_available_index_files(self) -> List[str]:
        """获取所有可用的索引文件名列表。

        Returns:
            索引文件名列表（如 ["B服.json", "官服.json", "国际服.json", "日服.json"]）
        """
        result: set = set()

        # 从备份目录获取
        if os.path.isdir(self.backup_directory):
            try:
                sub_dirs = [
                    d for d in os.listdir(self.backup_directory)
                    if os.path.isdir(os.path.join(self.backup_directory, d))
                ]
            except OSError:
                sub_dirs = []

            for sub_dir in sub_dirs:
                index_name = os.path.basename(sub_dir)
                try:
                    sub_path = os.path.join(self.backup_directory, sub_dir)
                    inner_dirs = [
                        d for d in os.listdir(sub_path)
                        if os.path.isdir(os.path.join(sub_path, d))
                    ]
                    for inner in inner_dirs:
                        if os.path.isfile(os.path.join(sub_path, inner, "index.json")):
                            result.add(f"{index_name}.json")
                            break
                except OSError:
                    continue

        # 从索引目录获取
        if os.path.isdir(self._index_directory):
            for key, value in self._package_name_types.items():
                if value in ("other", "萌新666sssaaa"):
                    continue
                prefix = value.rstrip("-")
                index_path = os.path.join(self._index_directory, f"{prefix}.json")
                if os.path.isfile(index_path):
                    result.add(f"{prefix}.json")

        unique = {name.casefold(): name for name in sorted(result)}
        return sorted(unique.values(), key=str.casefold)

    def get_last_backup_hash(self, index_file_name: str) -> Optional[str]:
        """获取最后一次备份的 SHA256 哈希。

        Args:
            index_file_name: 索引文件名（如 "官服.json"）

        Returns:
            SHA256 哈希字符串，无备份时返回 None
        """
        index_name = os.path.splitext(index_file_name)[0]
        backup_base_dir = os.path.join(self.backup_directory, index_name)

        if not os.path.isdir(backup_base_dir):
            return None

        try:
            dirs = [
                d for d in os.listdir(backup_base_dir)
                if os.path.isdir(os.path.join(backup_base_dir, d))
            ]
        except OSError:
            return None

        latest_dir: Optional[str] = None
        for dir_name in dirs:
            index_json_path = os.path.join(backup_base_dir, dir_name, "index.json")
            if not os.path.isfile(index_json_path):
                continue
            if latest_dir is None or dir_name > latest_dir:
                latest_dir = dir_name

        if latest_dir is not None:
            try:
                content = IndexFileBackupManager.retry_io(
                    lambda: _read_all_text(
                        os.path.join(backup_base_dir, latest_dir, "index.json")
                    )
                )
                return IndexFileBackupManager.compute_sha256(content)
            except Exception:
                pass

        return None

    def get_last_backup_timestamp(self, index_file_name: str) -> Optional[str]:
        """获取最后一次备份的时间戳。

        Args:
            index_file_name: 索引文件名（如 "官服.json"）

        Returns:
            时间戳字符串，无备份时返回 None
        """
        index_name = os.path.splitext(index_file_name)[0]
        backup_base_dir = os.path.join(self.backup_directory, index_name)

        if not os.path.isdir(backup_base_dir):
            return None

        try:
            dirs = [
                d for d in os.listdir(backup_base_dir)
                if os.path.isdir(os.path.join(backup_base_dir, d))
            ]
        except OSError:
            return None

        for dir_name in sorted(dirs, reverse=True):
            if os.path.isfile(os.path.join(backup_base_dir, dir_name, "index.json")):
                return dir_name

        return None

    def resolve_index_file_path(self, scheme_file_path: str) -> Optional[str]:
        """根据方案文件名前缀解析对应的索引文件路径。

        Args:
            scheme_file_path: 方案文件完整路径（如 {目录}\\官服-方案名.json）

        Returns:
            索引文件路径（如 {目录}\\官服.json），无法解析时返回 None
        """
        if not scheme_file_path:
            return None

        dir_path = os.path.dirname(scheme_file_path)
        file_name = os.path.basename(scheme_file_path)
        if not dir_path or not file_name:
            return None

        # 按前缀长度降序排序，优先匹配长前缀
        sorted_keys = sorted(
            KNOWN_SERVER_KEYS,
            key=lambda k: len(self._package_name_types.get(k, "")),
            reverse=True,
        )

        for key in sorted_keys:
            prefix = self._package_name_types.get(key)
            if prefix is None:
                continue
            prefix_without_dash = prefix.rstrip("-")
            if file_name.lower().startswith(prefix_without_dash.lower()):
                return os.path.join(dir_path, f"{prefix_without_dash}.json")

        return None

    def scan_orphan_schemes(self, index_file_name: str) -> list:
        """扫描孤儿方案（目录中存在但索引中未注册的 .json 文件）。

        Args:
            index_file_name: 索引文件名（如 "官服.json"）

        Returns:
            [{"SchemeName": str, "FilePath": str}, ...]
        """
        self.last_error = ""
        result: list = []
        if not index_file_name:
            return result

        index_file_path = os.path.join(self._index_directory, index_file_name)
        if not os.path.isfile(index_file_path):
            self.last_error = "索引文件不存在"
            return result

        prefix = os.path.splitext(index_file_name)[0]

        # 读取索引，提取已注册的方案名
        try:
            content = IndexFileBackupManager.retry_io(
                lambda: _read_all_text(index_file_path)
            )
            index_json = json.loads(content)
            if not isinstance(index_json, dict):
                raise ValueError("索引根节点必须为对象")
        except Exception as ex:
            self.last_error = str(ex)
            return result

        existing_schemes: set = set()
        config = index_json.get("Config", [])
        if isinstance(config, list):
            for item in config:
                if isinstance(item, dict):
                    existing_schemes.update(name.casefold() for name in item.keys())

        if not os.path.isdir(self._index_directory):
            return result

        # 列出目录中匹配前缀的方案文件
        try:
            all_files = os.listdir(self._index_directory)
        except OSError:
            return result

        prefix_lower = prefix.lower()
        for file_name in all_files:
            if not file_name.lower().startswith(f"{prefix_lower}-"):
                continue
            if not file_name.lower().endswith(".json"):
                continue

            # 提取方案名: 去掉 "{prefix}-" 前缀和 ".json" 后缀
            # 注意：方案名可能包含 "-"（如 "官服-某-方案.json"），
            # 我们要取 prefix 后第一个 "-" 之后、".json" 之前的全部内容
            after_prefix = file_name[len(prefix) + 1:]  # +1 跳过 "-"
            if after_prefix.lower().endswith(".json"):
                scheme_name = after_prefix[:-5]  # 去掉 ".json"
            else:
                continue

            if scheme_name.casefold() not in existing_schemes:
                result.append({
                    "SchemeName": scheme_name,
                    "FilePath": os.path.join(self._index_directory, file_name),
                })

        return result

    def add_schemes_to_index(self, index_file_name: str, schemes: list) -> int:
        """将孤儿方案添加回索引。

        Args:
            index_file_name: 索引文件名（如 "官服.json"）
            schemes: [{"SchemeName": str, "FilePath": str}, ...]

        Returns:
            成功添加的方案数量
        """
        self.last_error = ""
        if not schemes or not index_file_name:
            return 0

        index_file_path = os.path.join(self._index_directory, index_file_name)
        if not os.path.isfile(index_file_path):
            self.last_error = "索引文件不存在"
            return 0

        try:
            content = IndexFileBackupManager.retry_io(
                lambda: _read_all_text(index_file_path)
            )
            index_json = json.loads(content)
            if not isinstance(index_json, dict):
                raise ValueError("索引根节点必须为对象")
        except Exception as ex:
            self.last_error = str(ex)
            return 0

        config = index_json.get("Config")
        if not isinstance(config, list):
            config = []
            index_json["Config"] = config

        added = 0
        existing = {str(name).casefold() for item in config if isinstance(item, dict) for name in item}
        for scheme in schemes:
            if not isinstance(scheme, dict):
                continue
            scheme_name = scheme.get("SchemeName", "")
            file_path = scheme.get("FilePath", "")
            if not scheme_name or not file_path or scheme_name.casefold() in existing:
                continue
            existing.add(scheme_name.casefold())

            # 路径统一使用正斜杠
            path = file_path.replace("\\", "/")

            scheme_entry: Dict[str, Any] = {
                "configType": "Customer",
                "copiedNum": 0,
                "moveTop": False,
                "path": path,
                "status": "",
                "transparency": 100,
                "type": 0,
                "xSensitivity": 1,
                "ySensitivity": 1,
            }

            container = {scheme_name: scheme_entry}
            config.append(container)
            added += 1

        if added > 0:
            new_content = _serialize_json(index_json)
            tmp_path = index_file_path + ".tmp"
            try:
                IndexFileBackupManager.retry_io(
                    lambda: IndexFileBackupManager.atomic_write_all_text(
                        tmp_path, index_file_path, new_content
                    )
                )
            except Exception as ex:
                self.last_error = str(ex)
                LogService.error_ex("IndexFileBackupManager", ex, "补全索引写入失败")
                return 0

        return added

    def cleanup_old_backups(self, max_keep: int = 10) -> bool:
        """保留最新备份；清理失败返回 False 并保留可见原因。"""
        self.last_error = ""
        if not isinstance(max_keep, int) or isinstance(max_keep, bool) or max_keep < 1:
            self.last_error = "至少保留1个备份"
            return False
        base = os.path.realpath(os.path.abspath(self.backup_directory))
        if not os.path.isdir(base):
            return True
        errors = []

        def remove(directory):
            target = os.path.realpath(os.path.abspath(directory))
            # 在执行递归删除前核实实际目标严格位于指定备份目录内。
            if target == base or os.path.commonpath((base, target)) != base:
                raise ValueError("拒绝清理备份目录外路径")
            shutil.rmtree(target)

        try:
            subdirs = [os.path.join(base, name) for name in os.listdir(base)
                       if os.path.isdir(os.path.join(base, name))]
        except OSError as ex:
            self.last_error = str(ex)
            return False
        for subdir in subdirs:
            try:
                directories = [os.path.join(subdir, name) for name in os.listdir(subdir)
                               if os.path.isdir(os.path.join(subdir, name))]
                valid = []
                for directory in directories:
                    if os.path.isfile(os.path.join(directory, "index.json")):
                        valid.append(directory)
                    else:
                        remove(directory)
                for directory in sorted(valid, key=os.path.basename, reverse=True)[max_keep:]:
                    remove(directory)
            except Exception as ex:
                errors.append(str(ex))
                LogService.error_ex("IndexFileBackupManager", ex, "清理旧备份失败")
        self.last_error = "; ".join(errors)
        return not errors

    def check_and_notify_damage(self, scheme_file_path: str) -> bool:
        """检查方案是否已正确写入索引文件（路径精确比较）。

        Args:
            scheme_file_path: 方案文件完整路径

        Returns:
            True 表示正常，False 表示索引可能损坏（方案未写入索引）。
        """
        if not scheme_file_path or not os.path.isfile(scheme_file_path):
            return True

        index_file_path = self.resolve_index_file_path(scheme_file_path)
        if index_file_path is None or not os.path.isfile(index_file_path):
            return True

        try:
            index_content = IndexFileBackupManager.retry_io(
                lambda: _read_all_text(index_file_path)
            )
            return IndexFileBackupManager.contains_scheme(index_content, scheme_file_path)
        except Exception:
            return True

    def get_schemes_in_backup(self, index_file_name: str, timestamp: str) -> list:
        """从备份中读取方案名列表。

        Args:
            index_file_name: 索引文件名（如 "官服.json"）
            timestamp: 备份时间戳

        Returns:
            方案名列表
        """
        index_name = os.path.splitext(index_file_name)[0]
        backup_path = os.path.join(self.backup_directory, index_name, timestamp, "index.json")
        if not os.path.isfile(backup_path):
            return []

        try:
            content = IndexFileBackupManager.retry_io(
                lambda: _read_all_text(backup_path)
            )
            data = json.loads(content)
            schemes: list = []
            config = data.get("Config") if isinstance(data, dict) else None
            if isinstance(config, list):
                for item in config:
                    if isinstance(item, dict):
                        schemes.extend(item.keys())
            return schemes
        except Exception:
            return []

    def _get_backup_dir(self, index_file_name: str, timestamp: str) -> str:
        """获取指定索引文件和时间戳的备份目录路径。

        Args:
            index_file_name: 索引文件名（如 "官服.json"）
            timestamp: 备份时间戳

        Returns:
            备份目录完整路径
        """
        index_name = os.path.splitext(index_file_name)[0]
        return os.path.join(self.backup_directory, index_name, timestamp)

    # ── 静态方法 ──

    @staticmethod
    def contains_scheme(index_content: str, scheme_file_path: str) -> bool:
        """检查方案文件路径是否在索引内容中。

        Args:
            index_content: 索引文件 JSON 字符串
            scheme_file_path: 方案文件路径

        Returns:
            True 表示索引中包含该方案
        """
        try:
            data = json.loads(index_content)
            config = data.get("Config")
            if not isinstance(config, list):
                return False

            normalized_path = scheme_file_path.replace("/", "\\")

            for item in config:
                if not isinstance(item, dict):
                    continue
                for key, entry in item.items():
                    path = entry.get("path") if isinstance(entry, dict) else None
                    if path and isinstance(path, str):
                        normalized = path.replace("/", "\\")
                        if normalized.lower() == normalized_path.lower():
                            return True
            return False
        except Exception:
            return False

    @staticmethod
    def compute_sha256(content: str) -> str:
        """计算字符串的 SHA256 哈希（小写十六进制）。

        Args:
            content: 输入字符串

        Returns:
            SHA256 哈希字符串
        """
        sha256 = hashlib.sha256()
        sha256.update(content.encode("utf-8"))
        return sha256.hexdigest().lower()

    @staticmethod
    def retry_io(
        action: Callable[[], T],
        max_retries: int = 3,
        delay_ms: int = 500,
    ) -> T:
        """IO 重试包装器。在 OSError / PermissionError 时自动重试。

        Args:
            action: 要执行的 IO 操作
            max_retries: 最大重试次数
            delay_ms: 重试间隔（毫秒）

        Returns:
            action 的返回值

        Raises:
            OSError: 达到最大重试次数后仍然失败
        """
        last_exception: Optional[Exception] = None
        for i in range(max_retries):
            try:
                return action()
            except (OSError, PermissionError) as ex:
                last_exception = ex
                if i < max_retries - 1:
                    time.sleep(delay_ms / 1000.0)

        if last_exception is not None:
            raise last_exception
        raise RuntimeError("RetryIO 失败：maxRetries 为 0 或操作未抛出异常。")

    @staticmethod
    def atomic_write_all_text(tmp_path: str, final_path: str, content: str) -> None:
        """原子写入：委托到 core/json_editor.write_text_atomic（LF / 无 BOM 的唯一实现）。

        与主窗口/面板的差别：这里**故意在失败时抛出 OSError**，因为调用方 retry_io
        依赖异常来触发重试（若像其他调用点那样吞掉异常并返回 False，重试会静默失效）。

        Args:
            tmp_path: 保留参数（历史签名，调用方恒为 final_path + ".tmp"，
                与 write_text_atomic 内部推导的临时路径一致），当前不再读取。
            final_path: 最终文件路径
            content: 文件内容

        Raises:
            OSError: 写入失败（交由 retry_io 重试）
        """
        if not write_text_atomic(final_path, content):
            raise OSError(f"原子写入失败: {final_path}（详见日志）")


# ── 内部辅助函数 ──

def _read_all_text(file_path: str) -> str:
    """读取文件全部文本（UTF-8 无 BOM）。

    Args:
        file_path: 文件路径

    Returns:
        文件内容字符串
    """
    with open(file_path, "r", encoding="utf-8-sig") as f:
        return f.read()


def _serialize_json(data: Any) -> str:
    """序列化 JSON（4 空格缩进，无尾随换行，与 C# PrecisionJsonWriter 对齐）。

    Args:
        data: 要序列化的数据

    Returns:
        JSON 字符串
    """
    result = json.dumps(data, indent=4, ensure_ascii=False)
    # C# PrecisionJsonWriter 会产生尾部换行然后被 strip，但 Python json.dumps 不会。
    # 这里保持与 Python 默认行为一致。
    return result
