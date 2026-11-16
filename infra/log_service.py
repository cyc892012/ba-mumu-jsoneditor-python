"""日志服务 — 对应 C# LogService (static class)

格式: [yyyy-MM-dd HH:mm:ss.fff] [LEVEL] [Source] message
约束: 最多 10 个文件，单文件最大 2MB，定时 3 秒刷新缓冲
"""
import logging
import os
import glob as _glob
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional

_logger: Optional[logging.Logger] = None
_initialized: bool = False
_write_lock: threading.Lock = threading.Lock()
_buffer: list[str] = []
_log_dir: str = ""
_current_log_file: str = ""
_file_sequence: int = 1
_flush_timer: Optional[threading.Timer] = None
_lifecycle_lock = threading.RLock()
_generation = 0

MAX_LOG_FILES = 10
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB
FLUSH_INTERVAL_SEC = 3.0
BUFFER_MAX_LINES = 200  # 缓冲区行数上限，触发强制刷新


def _schedule_flush(generation=None) -> None:
    """安排下一次定时刷新"""
    global _flush_timer
    with _lifecycle_lock:
        generation = _generation if generation is None else generation
        if not _initialized or generation != _generation:
            return
        _flush_timer = threading.Timer(FLUSH_INTERVAL_SEC, _flush_callback, args=(generation,))
        _flush_timer.daemon = True
        _flush_timer.start()


def _flush_callback(generation=None) -> None:
    """定时器回调"""
    with _lifecycle_lock:
        if not _initialized or (generation is not None and generation != _generation):
            return
        flush()
        _schedule_flush(_generation)


def initialize(log_dir: str = "") -> None:
    """初始化日志服务"""
    with _lifecycle_lock:
        _initialize_locked(log_dir)


def _initialize_locked(log_dir: str) -> None:
    global _logger, _initialized, _log_dir, _current_log_file, _file_sequence, _generation

    if _initialized:
        return

    _initialized = True
    _generation += 1

    try:
        if log_dir:
            _log_dir = log_dir
        else:
            # 与 config 同源解析（源码版 / exe 版 / MOMO_DATA_DIR 一致）
            from core.config_manager import resolve_app_dir
            _log_dir = str(Path(resolve_app_dir()) / "Logs")

        os.makedirs(_log_dir, exist_ok=True)

        _cleanup_old_logs()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _file_sequence = 1
        _current_log_file = os.path.join(_log_dir, f"log_{timestamp}.txt")

        _logger = logging.getLogger("MuMuHelper")
        _logger.setLevel(logging.DEBUG)

        # 仅用缓冲 + 手动写文件，不使用 RotatingFileHandler
        # （因为需要自定义文件名格式和轮转逻辑）

        _schedule_flush()

    except Exception as ex:
        print(f"[LogService] 初始化失败: {ex}")
        _initialized = False


def shutdown() -> None:
    """关闭日志服务"""
    global _flush_timer, _initialized, _generation
    with _lifecycle_lock:
        _initialized = False
        _generation += 1
        if _flush_timer is not None:
            _flush_timer.cancel()
            _flush_timer = None
        flush()


def _write(level_str: str, source: str, message: str) -> None:
    """写入缓冲"""
    if not _initialized:
        return

    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    entry = f"[{time_str}] [{level_str}] [{source}] {message}"

    with _write_lock:
        if not _initialized:
            return
        _buffer.append(entry)
        if len(_buffer) >= BUFFER_MAX_LINES:
            _flush_internal()


def info(source: str, message: str) -> None:
    _write("INFO", source, message)


def warn(source: str, message: str) -> None:
    _write("WARN", source, message)


def error(source: str, message: str) -> None:
    """记录错误（字符串版）"""
    _write("ERROR", source, message)


def error_ex(source: str, exception: Exception, context: str = "") -> None:
    """记录错误（异常版）— 包含异常类型、消息、堆栈"""
    import traceback
    exc_type = type(exception).__name__
    exc_msg = str(exception)
    exc_tb = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
    if context:
        msg = f"[{context}] {exc_type}: {exc_msg}\n{exc_tb}"
    else:
        msg = f"{exc_type}: {exc_msg}\n{exc_tb}"
    _write("ERROR", source, msg)


def debug(source: str, message: str) -> None:
    _write("DEBUG", source, message)


def flush() -> None:
    """强制刷新缓冲到文件"""
    with _write_lock:
        _flush_internal()


def _flush_internal() -> None:
    """内部刷新（调用方已持有 _write_lock）"""
    global _current_log_file, _file_sequence

    if not _buffer:
        return

    try:
        _ensure_file_size()
        with open(_current_log_file, "a", encoding="utf-8") as f:
            for entry in _buffer:
                f.write(entry + "\n")
        _buffer.clear()
    except Exception as ex:
        print(f"[LogService] 写入日志文件失败: {ex}")


def _ensure_file_size() -> None:
    """检查当前日志文件大小，超限则创建新文件"""
    global _current_log_file, _file_sequence
    try:
        if not os.path.exists(_current_log_file):
            return
        if os.path.getsize(_current_log_file) < MAX_FILE_SIZE:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _file_sequence += 1
        _current_log_file = os.path.join(
            _log_dir, f"log_{timestamp}_{_file_sequence:03d}.txt"
        )
    except Exception:
        pass


def _cleanup_old_logs() -> None:
    """清理旧日志，最多保留 MAX_LOG_FILES 个"""
    try:
        log_files = sorted(
            _glob.glob(os.path.join(_log_dir, "log_*.txt"))
        )
        if len(log_files) < MAX_LOG_FILES:
            return

        while len(log_files) >= MAX_LOG_FILES:
            try:
                os.remove(log_files[0])
                log_files.pop(0)
            except Exception:
                break
    except Exception as ex:
        print(f"[LogService] 清理旧日志失败: {ex}")


class LogService:
    """类包装器 — 委托到模块级函数，兼容两种调用风格"""

    @staticmethod
    def initialize(log_dir: str = "") -> None:
        initialize(log_dir)

    @staticmethod
    def shutdown() -> None:
        shutdown()

    @staticmethod
    def info(source: str, message: str) -> None:
        info(source, message)

    @staticmethod
    def warn(source: str, message: str) -> None:
        warn(source, message)

    @staticmethod
    def error(source: str, message: str) -> None:
        error(source, message)

    @staticmethod
    def error_ex(source: str, exception: Exception, context: str = "") -> None:
        error_ex(source, exception, context)

    @staticmethod
    def debug(source: str, message: str) -> None:
        debug(source, message)
