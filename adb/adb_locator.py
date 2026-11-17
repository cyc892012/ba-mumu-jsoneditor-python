"""只读定位现有 ADB，最后才使用随程序分发的副本；不会启动外部进程。"""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from itertools import islice
import os
from pathlib import Path
import re
import shutil
import sys
import time

try:
    import winreg
except ImportError:
    winreg = None


MUMU_PROCESSES = frozenset(name.casefold() for name in (
    "MuMuPlayer.exe", "MuMuNxMain.exe", "MuMuNxDevice.exe", "MuMuNxService.exe",
    "MuMuVMMHeadless.exe", "MuMuVMMSVC.exe", "NemuPlayer.exe", "crashpad_handler.exe",
))
SOURCE_MESSAGES = {
    "manual": "已使用手动指定的 adb",
    "mumu_process": "已使用 MuMu 自带的 adb（运行进程）",
    "mumu_registry": "已使用 MuMu 自带的 adb（注册表）",
    "mumu_directory": "已使用 MuMu 自带的 adb（安装目录）",
    "program": "已使用程序目录附近的 adb",
    "path": "已使用系统 PATH 中的 adb",
    "bundled": "已使用摸点小助手内置的 adb",
}


@dataclass(frozen=True)
class AdbLocation:
    path: str
    source: str
    install_dir: str | None = None

    @property
    def message(self):
        return SOURCE_MESSAGES[self.source]


def _iter_mumu_processes():
    """通过 Toolhelp 枚举名称，再以有限查询权限获取路径；关闭所有句柄。"""
    if os.name != "nt":
        return []
    result = []
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)

        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for name in ("Process32FirstW", "Process32NextW"):
            func = getattr(kernel, name)
            func.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
            func.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot in (None, 0, ctypes.c_void_p(-1).value):
            return result
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                name = entry.szExeFile.casefold()
                if name in MUMU_PROCESSES:
                    handle = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                    if handle:
                        try:
                            size = wintypes.DWORD(32768)
                            buffer = ctypes.create_unicode_buffer(size.value)
                            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                                if name != "crashpad_handler.exe" or "mumu" in buffer.value.casefold():
                                    result.append((name, buffer.value))
                        finally:
                            kernel.CloseHandle(handle)
                more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
    except (OSError, AttributeError):
        pass
    return result


def _parents(path):
    path = Path(path)
    yield path
    yield from path.parents


def find_mumu_install_dir(start_path):
    """由程序或 ADB 路径上溯，识别各代 MuMu 的目录结构。"""
    start = Path(start_path)
    directory = start.parent if start.suffix.casefold() == ".exe" else start
    parents = tuple(_parents(directory))
    for root in parents:
        if any((root / part).is_dir() for part in ("nx_main", "nx_device", "emulator/nemu")):
            return str(root)
    for root in parents:
        if (root / "shell/adb.exe").is_file() or (root / "shell/MuMuManager.exe").is_file():
            return str(root)
    return None


def _find_adb_in_root(root):
    root = Path(root)
    for relative in ("nx_main/adb.exe", "shell/adb.exe"):
        path = root / relative
        if path.is_file():
            return str(path)
    try:
        with os.scandir(root / "nx_device") as entries:
            versions = sorted(islice(entries, 128), key=lambda entry: entry.name.casefold())
        for version in versions:
            path = Path(version.path) / "shell/adb.exe"
            if path.is_file():
                return str(path)
    except OSError:
        pass
    for relative in ("emulator/nemu/vmonitor/bin/adb_server.exe", "emulator/nemu/vmonitor/bin/adb.exe"):
        path = root / relative
        if path.is_file():
            return str(path)
    return None


def _process_roots():
    for name, path in _iter_mumu_processes():
        if name.casefold() not in MUMU_PROCESSES:
            continue
        if name.casefold() == "crashpad_handler.exe" and "mumu" not in path.casefold():
            continue
        root = find_mumu_install_dir(path)
        if root:
            yield root


def _registry_path(value):
    """兼容带引号、带空格、图标索引和卸载命令参数。"""
    value = os.path.expandvars(str(value or "")).strip()
    if value.startswith('"'):
        return value.split('"', 2)[1]
    match = re.match(r"(.*?\.exe)(?:\s|,|$)", value, re.IGNORECASE)
    return match.group(1) if match else value.rstrip(", ")


def _registry_roots():
    if winreg is None:
        return
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for prefix in ("SOFTWARE", "SOFTWARE\\WOW6432Node"):
            key_path = prefix + r"\Microsoft\Windows\CurrentVersion\Uninstall"
            try:
                with winreg.OpenKey(hive, key_path) as base:
                    for index in range(1024):
                        try:
                            name = winreg.EnumKey(base, index)
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(base, name) as item:
                                display = winreg.QueryValueEx(item, "DisplayName")[0]
                                if "mumu" not in str(display).casefold():
                                    continue
                                for field in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                    try:
                                        value = _registry_path(winreg.QueryValueEx(item, field)[0])
                                    except OSError:
                                        continue
                                    if value:
                                        root = find_mumu_install_dir(value)
                                        if root:
                                            yield root
                        except OSError:
                            continue
            except OSError:
                continue


def _drive_roots():
    if os.name != "nt":
        return []
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetLogicalDrives.restype = wintypes.DWORD
        kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        kernel.GetDriveTypeW.restype = wintypes.UINT
        mask = kernel.GetLogicalDrives()
        # 跳过网络盘和光驱，避免离线路径阻塞扫描。
        return [Path(f"{chr(65 + i)}:/") for i in range(26)
                if mask & (1 << i) and kernel.GetDriveTypeW(f"{chr(65 + i)}:/") in (2, 3, 6)]
    except (OSError, AttributeError):
        return []


def _directory_roots():
    """仅检查已知容器的两层子目录，最多 4096 项，软时间预算 0.8 秒。"""
    deadline = time.monotonic() + 0.8
    queue = []
    for drive in _drive_roots():
        queue.extend((drive, drive / "Program Files", drive / "Program Files (x86)"))
    queue.extend(Path(os.environ[name]) for name in ("ProgramFiles", "ProgramFiles(x86)")
                 if os.environ.get(name))
    queue = [(path, 0) for path in dict.fromkeys(queue)]
    visited = set()
    count = 0
    for base, depth in queue:
        if time.monotonic() >= deadline or count >= 4096:
            break
        key = os.path.normcase(str(base))
        if key in visited:
            continue
        visited.add(key)
        try:
            with os.scandir(base) as entries:
                for entry in islice(entries, min(256, 4096 - count)):
                    count += 1
                    if time.monotonic() >= deadline:
                        return
                    name = entry.name.casefold()
                    if ("mumu" not in name and name != "netease") or not entry.is_dir(follow_symlinks=False):
                        continue
                    if "mumu" in name:
                        yield entry.path
                    if depth < 2:
                        queue.append((Path(entry.path), depth + 1))
        except OSError:
            continue


def bundled_adb_path():
    resources = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    if getattr(sys, "frozen", False) and not hasattr(sys, "_MEIPASS"):
        resources = Path(sys.executable).parent / "_internal"
    return resources / "tools/adb/adb.exe"


def locate_adb(current="", app_dir=None):
    """返回路径、来源及独立的 MuMu 安装根；全部缺失时返回 None。"""
    selected = None
    install_dir = None
    if current and Path(current).is_file():
        install_dir = find_mumu_install_dir(current)
        selected = AdbLocation(str(current), "manual", install_dir)
        if install_dir:
            return selected
    seen = set()
    for source, roots in (("mumu_process", _process_roots),
                          ("mumu_registry", _registry_roots),
                          ("mumu_directory", _directory_roots)):
        try:
            for root in roots():
                key = os.path.normcase(str(root))
                if key in seen:
                    continue
                seen.add(key)
                detected = find_mumu_install_dir(root)
                install_dir = install_dir or detected
                if selected and install_dir:
                    return AdbLocation(selected.path, selected.source, install_dir)
                path = _find_adb_in_root(root)
                if path:
                    return AdbLocation(path, source, detected or str(root))
        except (OSError, AttributeError):
            # 单个发现渠道不可用时，继续下一优先级。
            continue
    if selected:
        return selected
    if app_dir is None:
        from core.config_manager import resolve_app_dir
        app_dir = resolve_app_dir()
    for directory in _parents(app_dir):
        path = directory / "adb.exe"
        if path.is_file():
            return AdbLocation(str(path), "program", install_dir or find_mumu_install_dir(path))
    path = shutil.which("adb.exe")
    if path and Path(path).is_file():
        return AdbLocation(path, "path", install_dir or find_mumu_install_dir(path))
    path = bundled_adb_path()
    if path.is_file():
        return AdbLocation(str(path), "bundled", install_dir)
    return None
