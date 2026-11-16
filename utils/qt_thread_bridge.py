"""通用跨线程信号桥 — 把后台线程的回调安全地转发到 Qt 主线程执行。

背景（血泪教训，详见 Agent接手提示词.md「坑 1」）：
    从「没有 Qt 事件循环的后台线程」调用 ``QTimer.singleShot(0, fn)``，
    ``fn`` 根本不会被执行 —— 不报错、不告警，被静默丢弃。
    原因：定时器的线程归属是调用方线程，该线程没有事件循环 ⇒ 永不触发。
    对照实测：pyqtSignal 桥正常执行，且落在主线程。

用法::

    class MyForm(BaseDialog):
        def __init__(self, parent=None):
            super().__init__(parent, "标题")
            # 作为自己的子对象 —— 宿主销毁时桥一起销毁，
            # 排队中的回调由 Qt 自动丢弃，不会打到已删除的 C++ 对象上
            self._bridge = ThreadBridge(self)
            some_listener.on_event(self._on_event)   # 该回调来自后台线程

        def _on_event(self, *args):
            # 这里可能运行在后台线程！禁止直接操作 Qt 控件
            self._bridge.post(self._on_event_ui, *args)

        def _on_event_ui(self, *args):
            # 这里保证在主线程，可以安全操作控件
            self._label.setText(...)

注意：``post()`` 是异步的（投递到主线程事件队列下一个循环执行），
所以不要在调用后立刻假设 UI 已经更新。
"""
from PyQt5.QtCore import QObject, Qt, pyqtSignal, pyqtSlot


class ThreadBridge(QObject):
    """跨线程信号桥：``post()`` 可从任意线程调用，回调总在桥所在线程执行。"""

    # (可调用对象, 位置参数元组) —— 用 object 承载任意 Python 对象
    _invoke = pyqtSignal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 显式 QueuedConnection：保证回调在接收者线程（通常是主线程）的事件循环里执行，
        # 绝不在调用它的后台钩子线程里就地执行。
        self._invoke.connect(self._dispatch, Qt.QueuedConnection)

    @pyqtSlot(object, object)
    def _dispatch(self, fn, args) -> None:
        try:
            fn(*args)
        except RuntimeError:
            # 宿主 QWidget 已被销毁（wrapped C/C++ object ... has been deleted），
            # 排队中的回调晚到了 —— 静默忽略，不要让它冒泡成崩溃
            pass

    def post(self, fn, *args) -> None:
        """从任意线程投递回调；``fn`` 将在桥所在线程（通常是主线程）执行。

        桥是宿主的子对象，宿主销毁时桥一并销毁。而全局键盘钩子的注册可能晚于
        面板销毁时仍在触发（钩子回调不随窗口关闭自动注销），此时 emit 会抛
        RuntimeError —— 静默忽略，绝不让它冒泡到后台钩子线程里。
        """
        try:
            self._invoke.emit(fn, args)
        except RuntimeError:
            # 桥/宿主已销毁，回调无处投递，直接丢弃
            pass
