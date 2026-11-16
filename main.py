"""
摸点小助手 3.0 — Python/PyQt5 版
"""
import sys, os, time
import threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_startup_trace = None
if os.environ.get("MOMO_STARTUP_TRACE") == "1":
    from utils import startup_trace as _startup_trace
    _startup_trace.mark("python_entry")

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QTimer, QObject, pyqtSignal
from PyQt5.QtGui import QIcon, QFont
if _startup_trace:
    _startup_trace.mark("qt_imported")

# 启动测速钩子（默认不激活）：设环境变量 MOMO_BENCH=1 运行，
# 窗口完全就绪后把耗时（秒）写入 %TEMP%\momo_bench_result.txt 并自动退出
_BENCH = os.environ.get("MOMO_BENCH") == "1"
_T0 = time.perf_counter() if _BENCH else 0.0


class _ExceptionBridge(QObject):
    report = pyqtSignal(str)


def install_exception_handlers(app, reporter=None):
    """保留原钩子；后台异常通过 Qt 信号在 UI 线程报告。"""
    from infra.log_service import LogService
    bridge = _ExceptionBridge(app)
    previous_sys, previous_thread = sys.excepthook, threading.excepthook
    reporting = threading.local()

    def display(message):
        if getattr(bridge, "_showing", False):
            return
        bridge._showing = True
        try:
            if reporter:
                reporter(message)
            else:
                from ui.message_dialog import MessageDialog
                MessageDialog.warning(app.activeWindow(), "程序错误", message)
        except Exception as ex:
            LogService.error_ex("ExceptionHandler", ex, "异常提示失败")
        finally:
            bridge._showing = False
    bridge.report.connect(display)

    def report(exc_type, exception, traceback):
        if getattr(reporting, "active", False):
            return
        reporting.active = True
        try:
            LogService.error_ex("UnhandledException", exception, "未处理异常")
            bridge.report.emit(f"{exc_type.__name__}: {exception}\n请查看日志了解详情。")
        finally:
            reporting.active = False

    def system_hook(exc_type, exception, traceback):
        report(exc_type, exception, traceback)
        previous_sys(exc_type, exception, traceback)

    def thread_hook(args):
        report(args.exc_type, args.exc_value, args.exc_traceback)
        previous_thread(args)

    sys.excepthook, threading.excepthook = system_hook, thread_hook
    def restore():
        if sys.excepthook is system_hook:
            sys.excepthook = previous_sys
        if threading.excepthook is thread_hook:
            threading.excepthook = previous_thread
    return restore


def _icon_candidates():
    """程序图标搜索顺序：源码 ui/assets → 打包资源目录。
    保证 .py 直跑 与 PyInstaller exe 两种运行方式表现一致。"""
    base = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(base, "ui", "assets", "app_icon.ico")]
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        cands.append(os.path.join(meipass, "ui", "assets", "app_icon.ico"))
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        cands.append(os.path.join(exe_dir, "ui", "assets", "app_icon.ico"))
        cands.append(os.path.join(exe_dir, "_internal", "ui", "assets", "app_icon.ico"))
        cands.append(os.path.join(exe_dir, "app_icon.ico"))
    return cands


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    if _startup_trace:
        _startup_trace.mark("application_created")

    # 日志服务初始化 — 必须在创建窗口/使用 LogService 之前，
    # 否则 _initialized 恒为 False，所有日志会被静默丢弃、Logs 目录也不会生成
    from infra.log_service import LogService
    LogService.initialize()
    if _startup_trace:
        _startup_trace.mark("logging_initialized")

    from core.app_version import APP_VERSION
    LogService.info("Application", f"摸点小助手 {APP_VERSION} 启动")
    restore_hooks = install_exception_handlers(app)
    exit_code = 1
    try:
        # 程序图标 — app 级别，确保任务栏和所有窗口生效
        for icon_path in _icon_candidates():
            if os.path.exists(icon_path):
                app.setWindowIcon(QIcon(icon_path))
                break

        from ui.main_window import MainWindow
        if _startup_trace:
            _startup_trace.mark("window_module_imported")
        win = MainWindow()
        if _startup_trace:
            _startup_trace.mark("window_built")
        win.show()
        if _startup_trace:
            _startup_trace.mark("show_completed")
            _startup_trace.flush()

            def trace_idle():
                _startup_trace.mark("first_idle")
                _startup_trace.flush()

            QTimer.singleShot(0, trace_idle)

        if _BENCH:
            _deadline = time.perf_counter() + 10.0

            def _bench_check():
                ready = False
                try:
                    txt = win._status_label.text() if win._status_label else ""
                    ready = txt.startswith("配置已加载")
                except Exception:
                    pass
                if ready or time.perf_counter() > _deadline:
                    out = os.path.join(os.environ.get("TEMP", "."), "momo_bench_result.txt")
                    try:
                        with open(out, "w", encoding="utf-8") as f:
                            f.write(f"{time.perf_counter() - _T0:.3f}")
                    except Exception:
                        pass
                    app.quit()
                    return
                QTimer.singleShot(20, _bench_check)

            QTimer.singleShot(60, _bench_check)

        exit_code = app.exec_()
    except Exception as ex:
        sys.excepthook(type(ex), ex, ex.__traceback__)
    finally:
        restore_hooks()
        LogService.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
