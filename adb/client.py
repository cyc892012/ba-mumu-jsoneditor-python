"""ADB 客户端 — 对应 C# AdbClient

功能：
- subprocess.Popen 封装 ADB 命令
- connect/disconnect 连接管理
- get_screen_size 获取屏幕分辨率
- start_getevent/stop_getevent 触摸事件采集
- get_sensor_max_values/get_mouse_max_values 传感器/鼠标分辨率
- resolve_adb_path 自动检测 ADB 路径
- query_mumu12_instances 检测 MuMu12 实例
"""
import subprocess
import re
import os
from pathlib import Path
from typing import Optional, Tuple, List
from infra.log_service import LogService


def _program_files_roots() -> List[Path]:
    values = (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"))
    return list(dict.fromkeys(Path(value) for value in values if value))


class AdbClient:
    """ADB 客户端 — 对应 C# AdbClient"""

    def __init__(self, adb_path: str, device_serial: str):
        self._adb_path = adb_path
        self._device_serial = device_serial
        self._getevent_process: Optional[subprocess.Popen] = None
        self.is_connected: bool = False

    def set_device(self, adb_path: str, device_serial: str) -> None:
        self._adb_path = adb_path
        self._device_serial = device_serial

    @property
    def device_serial(self) -> str:
        return self._device_serial

    def execute(self, arguments: str, timeout_ms: int) -> str:
        """执行 ADB 命令 — 返回 stdout+stderr"""
        if not self._adb_path or not Path(self._adb_path).exists():
            raise RuntimeError("ADB 路径无效。")

        try:
            result = subprocess.run(
                [self._adb_path] + arguments.split(),
                capture_output=True, text=True,
                timeout=timeout_ms / 1000.0, encoding='utf-8', errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            output = result.stdout
            if result.stderr:
                output += '\n' + result.stderr
            return output
        except subprocess.TimeoutExpired:
            raise TimeoutError("ADB 命令执行超时。")

    def connect(self) -> bool:
        """连接 ADB 设备"""
        try:
            result = self.execute(f"connect {self._device_serial}", 5000)
            self.is_connected = (
                "connected to" in result or "already connected" in result
            )
            return self.is_connected
        except TimeoutError as ex:
            LogService.error("AdbClient", str(ex))
            self.is_connected = False
            return False
        except Exception as ex:
            LogService.error_ex("AdbClient", ex, "ADB连接失败")
            self.is_connected = False
            return False

    def disconnect(self) -> None:
        """断开 ADB 连接"""
        try:
            self.execute(f"disconnect {self._device_serial}", 3000)
        except Exception as ex:
            LogService.error_ex("AdbClient", ex, "ADB断开连接失败")
        self.is_connected = False
        self.stop_getevent()

    def get_screen_size(self) -> str:
        """获取屏幕分辨率 — 返回 "WxH" 格式字符串"""
        try:
            result = self.execute(
                f"-s {self._device_serial} shell wm size", 5000
            )
            idx = result.rfind(':')
            if idx >= 0:
                return result[idx + 1:].strip()

            # 尝试最后一行
            lines = result.strip().splitlines()
            last_line = lines[-1].strip() if lines else ""
            if last_line.startswith("Override size:"):
                return last_line[len("Override size:"):].strip()
            return last_line
        except Exception as ex:
            LogService.error_ex("AdbClient", ex, "获取屏幕分辨率失败")
            return ""

    def start_getevent(self) -> Optional[subprocess.Popen]:
        """启动 getevent -lt 进程 — 返回 Popen 对象"""
        if not self._adb_path or not Path(self._adb_path).exists():
            return None

        self.stop_getevent()

        self._getevent_process = subprocess.Popen(
            [self._adb_path, '-s', self._device_serial, 'shell', 'getevent', '-lt'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return self._getevent_process

    def stop_getevent(self) -> None:
        """停止 getevent 进程"""
        if self._getevent_process is not None:
            try:
                self._getevent_process.kill()
                self._getevent_process.wait(3)
            except Exception:
                pass
            self._getevent_process = None

    def get_sensor_max_values(self) -> Tuple[int, int]:
        """获取传感器最大坐标值 — 返回 (maxX, maxY)"""
        max_x, max_y = 0, 0
        try:
            result = self.execute(
                f"-s {self._device_serial} shell getevent -p /dev/input/event4",
                5000
            )
            lines = result.splitlines()
            for line in lines:
                if "0035" in line:
                    val = self._extract_max_from_param_line(line)
                    if val > max_x:
                        max_x = val
                elif "0036" in line:
                    val = self._extract_max_from_param_line(line)
                    if val > max_y:
                        max_y = val
            return max_x, max_y
        except Exception as ex:
            LogService.error_ex("AdbClient", ex, "获取传感器分辨率失败")
            return 0, 0

    def get_mouse_max_values(self) -> Tuple[int, int]:
        """获取鼠标集成最大坐标值 — 返回 (maxX, maxY)"""
        max_x, max_y = 0, 0
        try:
            result = self.execute(
                f"-s {self._device_serial} shell getevent -p /dev/input/event3",
                5000
            )
            lines = result.splitlines()
            for line in lines:
                if "0000  :" in line or "0000," in line:
                    val = self._extract_max_from_param_line(line)
                    if val > max_x:
                        max_x = val
                elif "0001  :" in line or "0001," in line:
                    val = self._extract_max_from_param_line(line)
                    if val > max_y:
                        max_y = val
            return max_x, max_y
        except Exception as ex:
            LogService.error_ex("AdbClient", ex, "获取鼠标分辨率失败")
            return 0, 0

    @staticmethod
    def _extract_max_from_param_line(line: str) -> int:
        """从 getevent -p 输出行中提取 max 值"""
        idx = line.lower().find("max ")
        if idx < 0:
            return 0
        rest = line[idx + 4:]
        # 截断到下一个分隔符
        end_idx = len(rest)
        for sep in [',', ' ', '\t']:
            pos = rest.find(sep)
            if pos >= 0 and pos < end_idx:
                end_idx = pos
        rest = rest[:end_idx].strip()
        try:
            return int(rest)
        except (ValueError, TypeError):
            return 0

    # ── 静态工具方法 ──

    @staticmethod
    def resolve_adb_path() -> Optional[str]:
        """自动检测 ADB 可执行文件路径。

        按优先级依次搜索：
        1. 系统盘和用户本地应用数据目录
        2. MuMu 模拟器安装目录下的 adb.exe
        3. 系统 PATH 环境变量
        返回找到的第一个有效路径，未找到返回 None。
        """
        # 1. 常见路径由当前 Windows 环境变量解析
        common = []
        system_drive = os.environ.get("SystemDrive", "").strip()
        if system_drive:
            common.append(str(Path(system_drive + os.sep) / "adb" / "adb.exe"))
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            common.append(str(Path(local_app_data) / "adb" / "adb.exe"))
        for p in common:
            if Path(p).exists():
                LogService.info("AdbClient", f"ADB 路径（常见路径）: {p}")
                return p

        # 2. MuMu / NemuVbox 安装目录搜索
        mumu_bases = [
            root / folder
            for folder in ("NemuVbox", "MuMu", "MuMu Player 12")
            for root in _program_files_roots()
        ]
        for base in mumu_bases:
            if base.exists():
                for root, dirs, files in os.walk(str(base)):
                    if "adb.exe" in files:
                        found = str(Path(root) / "adb.exe")
                        LogService.info("AdbClient", f"ADB 路径（MuMu目录）: {found}")
                        return found

        # 3. PATH 环境变量
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            adb_path = Path(path_dir) / "adb.exe"
            if adb_path.exists():
                LogService.info("AdbClient", f"ADB 路径（PATH）: {adb_path}")
                return str(adb_path)

        return None

    @staticmethod
    def query_mumu12_instances() -> List[Tuple[str, int]]:
        """查询 MuMu12 模拟器实例列表。

        通过 MuMuManager.exe info -v all 获取实例信息，
        解析 name 和 adb_port，返回 (名称, 端口) 列表。
        未找到或不可用时返回空列表。
        """
        if os.name != 'nt':
            return []

        # 搜索 MuMuManager.exe
        mumu_manager_path = None
        search_dirs = [
            root / folder
            for folder in ("MuMu", "MuMu Player 12", "MuMuPlayer-12.0")
            for root in _program_files_roots()
        ]

        for base in search_dirs:
            if base.exists():
                for root, dirs, files in os.walk(str(base)):
                    if "MuMuManager.exe" in files:
                        mumu_manager_path = str(Path(root) / "MuMuManager.exe")
                        break
                if mumu_manager_path:
                    break

        if not mumu_manager_path:
            # 尝试 ProgramData 目录
            program_data = os.environ.get("ProgramData", "").strip()
            program_data_roots = (
                [Path(program_data) / "MuMu", Path(program_data) / "MuMuPlayer"]
                if program_data else []
            )
            for base in program_data_roots:
                if base.exists():
                    for root, dirs, files in os.walk(str(base)):
                        if "MuMuManager.exe" in files:
                            mumu_manager_path = str(Path(root) / "MuMuManager.exe")
                            break
                    if mumu_manager_path:
                        break

        if not mumu_manager_path:
            LogService.warn("AdbClient", "未找到 MuMuManager.exe")
            return []

        LogService.info("AdbClient", f"MuMuManager 路径: {mumu_manager_path}")

        # 运行 info -v all 获取实例列表
        instances: List[Tuple[str, int]] = []
        try:
            result = subprocess.run(
                [mumu_manager_path, "info", "-v", "all"],
                capture_output=True, text=True, timeout=10,
                encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW
            )
            output = result.stdout

            # 解析每行或段落中的 name 和 adb_port
            current_name: Optional[str] = None
            for line in output.splitlines():
                line_lower = line.lower().strip()
                # MuMuManager 输出常见格式:
                #   name: MuMu12-1
                #   adb_port: 16384
                # 或者:
                #   MuMu12-1  16384
                if 'name' in line_lower or 'display_name' in line_lower:
                    parts = re.split(r'[:：]', line, maxsplit=1)
                    if len(parts) >= 2:
                        current_name = parts[1].strip()
                elif 'adb_port' in line_lower or ('port' in line_lower and 'adb' in line_lower):
                    parts = re.split(r'[:：]', line, maxsplit=1)
                    if len(parts) >= 2:
                        m = re.search(r'(\d+)', parts[1])
                        if m:
                            current_port = int(m.group(1))
                            if current_name:
                                instances.append((current_name, current_port))
                            current_name = None

            # 如果上述解析无结果，尝试按行解析 "name port" 格式
            if not instances:
                for line in output.splitlines():
                    stripped = line.strip()
                    parts = stripped.split()
                    if len(parts) >= 2:
                        try:
                            port = int(parts[-1])
                            if 1000 < port < 65535:
                                name = ' '.join(parts[:-1])
                                instances.append((name, port))
                        except ValueError:
                            pass

        except FileNotFoundError:
            LogService.warn("AdbClient", "MuMuManager.exe 不存在")
        except subprocess.TimeoutExpired:
            LogService.warn("AdbClient", "MuMuManager 命令超时")
        except Exception as ex:
            LogService.error_ex("AdbClient", ex, "查询 MuMu12 实例失败")

        if instances:
            LogService.info("AdbClient", f"找到 {len(instances)} 个 MuMu 实例: {instances}")
        return instances
