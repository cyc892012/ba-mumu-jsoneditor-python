"""安全播种随程序分发的基础键位预设。"""
import json
import logging
import os
import tempfile
import sys
from pathlib import Path


BUILTIN_PRESET_VERSION = 1
MARKER_FILENAME = ".builtin_presets"
_logger = logging.getLogger(__name__)


def get_builtin_presets_dir() -> str:
    """返回源码或 PyInstaller 包中的 presets 资源目录。"""
    if not getattr(sys, "frozen", False):
        return str(Path(__file__).resolve().parent.parent / "presets")

    candidates = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(Path(meipass) / "presets")

    exe_dir = Path(sys.executable).resolve().parent
    candidates.extend((exe_dir / "_internal" / "presets", exe_dir / "presets"))
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)
    return str(candidates[0]) if candidates else str(exe_dir / "_internal" / "presets")


def _log_error(message: str, exception: Exception) -> None:
    try:
        from infra.log_service import LogService
        LogService.error_ex("BuiltinPresets", exception, message)
    except Exception:
        _logger.exception("%s: %s", message, exception)


def _log_warning(message: str) -> None:
    try:
        from infra.log_service import LogService
        LogService.warn("BuiltinPresets", message)
    except Exception:
        _logger.warning(message)


def _read_marker(marker_path: Path) -> dict:
    if not marker_path.exists():
        return {}

    with marker_path.open("r", encoding="utf-8") as stream:
        marker = json.load(stream)
    if (not isinstance(marker, dict)
            or marker.get("version") != BUILTIN_PRESET_VERSION
            or not isinstance(marker.get("files"), dict)):
        raise ValueError("内置键位播种标记格式无效")

    seeded = marker["files"]
    for name, version in seeded.items():
        if (not isinstance(name, str) or not name or Path(name).name != name
                or type(version) is not int or version < 1):
            raise ValueError("内置键位播种标记包含无效条目")
    return seeded


def _write_payload(fd: int, payload: bytes) -> None:
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError("临时文件写入未完成")
            remaining = remaining[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _install_without_overwrite(source: Path, destination: Path) -> bool:
    """先完整写临时文件，再原子改名落位，保证不覆盖现存目标。"""
    fd, temporary = tempfile.mkstemp(prefix=".builtin_preset_", suffix=".tmp",
                                     dir=str(destination.parent))
    try:
        _write_payload(fd, source.read_bytes())
        fd = None
        try:
            os.rename(temporary, str(destination))
            return True
        except FileExistsError:
            return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _write_marker_atomic(marker_path: Path, seeded: dict) -> None:
    marker = {
        "version": BUILTIN_PRESET_VERSION,
        "files": dict(sorted(seeded.items())),
    }
    payload = json.dumps(marker, ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".builtin_presets_", suffix=".tmp",
                                     dir=str(marker_path.parent))
    try:
        _write_payload(fd, payload.encode("utf-8"))
        fd = None
        os.replace(temporary, str(marker_path))
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def seed_builtin_presets(data_dir: str) -> None:
    """把内置键位补到 data_dir，保留用户文件及已删除文件的状态。"""
    try:
        destination_dir = Path(data_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)
        marker_path = destination_dir / MARKER_FILENAME
        try:
            seeded = _read_marker(marker_path)
        except Exception as exc:
            _log_error("播种标记损坏，已停止本次播种以保护用户数据", exc)
            return

        source_dir = Path(get_builtin_presets_dir())
        if not source_dir.is_dir():
            _log_warning("内置键位资源目录不存在: {}".format(source_dir))
            return

        changed = False
        for source in sorted(source_dir.iterdir(), key=lambda item: item.name):
            if not source.is_file() or source.suffix.lower() != ".json":
                continue
            name = source.name
            if name in seeded:
                continue

            destination = destination_dir / name
            if os.path.lexists(str(destination)):
                seeded[name] = BUILTIN_PRESET_VERSION
                changed = True
                continue

            try:
                _install_without_overwrite(source, destination)
                seeded[name] = BUILTIN_PRESET_VERSION
                changed = True
            except Exception as exc:
                _log_error("播种内置键位失败: {}".format(name), exc)

        if changed:
            try:
                _write_marker_atomic(marker_path, seeded)
            except Exception as exc:
                _log_error("写入内置键位播种标记失败", exc)
    except Exception as exc:
        _log_error("内置键位播种异常", exc)
