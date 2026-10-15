"""异步任务：把阻塞调用（网络/磁盘）丢到线程池，结果通过 Qt 信号回主线程。

约定（SPEC §4 硬性约定 1）：**网络与磁盘 I/O 永在子线程，UI 线程只做渲染**。

用法::

    task = AsyncTask(self._fetch_songs, keyword="周杰伦")
    task.done.connect(self._on_songs)      # 成功：回调参数 = 返回值
    task.failed.connect(self._on_error)    # 失败：回调参数 = Exception
    task.start()

两个必须记住的坑（都有回归测试盯着，见 ``tests/test_async_task.py``）：

1. **信号的接收者要活到投递完成**。信号从工作线程发出时是"排队投递"（queued），
   如果接收它的 QObject 在 ``run()`` 返回后立刻被销毁（``QRunnable`` 会 autoDelete），
   排在事件队列里的事件会被丢弃 —— 现象是"任务明明跑完了，回调永远不触发"。
   所以本模块用 ``_KEEPALIVE`` 强引用住任务，直到主线程确认处理完（``_retire``）。
2. **回调要连到 QObject 的绑定方法**（如控件/store 的方法），这样 Qt 会自动排队到主线程执行；
   连到普通函数/lambda 时 PySide6 会在**工作线程**里直接执行，可能触碰 UI 导致崩溃。
   需要携带额外参数时，让 ``fn`` 返回元组，再由绑定方法拆包。
"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QMetaObject, QObject, QRunnable, QThreadPool, Qt, Signal, Slot

from ..util import log

_log = log.get("util.async_task")

#: 正在运行/尚未投递完成的任务（强引用，防止被 GC 掉导致信号丢失）
_KEEPALIVE: dict[int, "AsyncTask"] = {}


def live_task_count() -> int:
    """当前仍在保活的任务数（测试/诊断用）。"""
    return len(_KEEPALIVE)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)

    def __init__(self, task_key: int) -> None:
        super().__init__()
        self._task_key = task_key

    @Slot()
    def _retire(self) -> None:
        """主线程处理完 done/failed 之后才允许释放任务（见模块头注释坑 1）。"""
        _KEEPALIVE.pop(self._task_key, None)


class AsyncTask(QRunnable):
    """在线程池里跑一个函数。信号对象独立于 QRunnable（QRunnable 非 QObject）。"""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = _Signals(id(self))
        self._key = id(self)
        self.setAutoDelete(True)

    # 便捷转发，便于调用方少记一层属性
    @property
    def is_running(self) -> bool:
        """是否仍在运行（或结果还没被主线程处理完）。

        调用方用来防止同一件事并发跑两份（如设置页连点「清空」）。
        """
        return self._key in _KEEPALIVE

    @property
    def done(self) -> Signal:
        return self.signals.done

    @property
    def failed(self) -> Signal:
        return self.signals.failed

    def run(self) -> None:  # 线程池线程内执行
        name = getattr(self._fn, "__name__", repr(self._fn))
        _log.debug("任务开始：%s", name)
        try:
            try:
                result = self._fn(*self._args, **self._kwargs)
            except Exception as exc:  # noqa: BLE001 - 任何异常都要转成信号，避免静默崩溃
                _log.warning("任务失败 %s: %s\n%s", name, exc, traceback.format_exc(limit=4))
                try:
                    self.signals.failed.emit(exc)
                except RuntimeError:  # 接收者已销毁
                    pass
            else:
                try:
                    _log.debug("任务完成：%s", name)
                    self.signals.done.emit(result)
                except RuntimeError:
                    pass
        finally:
            # 关键：**在 emit 之后**才排队释放请求。
            # 顺序保证来自 Qt：同一接收者的排队事件按 FIFO 投递，
            # 所以 done/failed 一定先于 _retire 被主线程处理（见模块头坑 1）。
            try:
                QMetaObject.invokeMethod(self.signals, "_retire", Qt.QueuedConnection)
            except RuntimeError:
                # 主线程已经把 _Signals 销毁（进程正在退出）：
                # 信号已经投递过了（或没人要了），这里只需放开强引用。
                _KEEPALIVE.pop(self._key, None)

    def start(self) -> "AsyncTask":
        """入池执行。任务会被保活到信号投递完成。"""
        _KEEPALIVE[self._key] = self
        QThreadPool.globalInstance().start(self)
        return self


def run_async(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> AsyncTask:
    """一行写法：``run_async(api.search, "关键词")``。"""
    return AsyncTask(fn, *args, **kwargs)
