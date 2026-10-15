"""AsyncTask 回归测试（两个坑都要盯住）。"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import qt_app  # noqa: E402

try:
    from PySide6.QtCore import QObject, Qt, Signal, Slot

    HAS_QT = True
except ImportError:  # pragma: no cover
    HAS_QT = False

from nemusic.util import async_task  # noqa: E402
from nemusic.util.async_task import AsyncTask  # noqa: E402


def pump(app, predicate, timeout_ms: int = 10000) -> bool:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


@unittest.skipUnless(HAS_QT, "缺少 PySide6")
class TestAsyncTask(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def test_result_delivered_without_keeping_reference(self) -> None:
        """坑 1 回归：调用方不保存 task 引用，回调也必须被触发。

        初版把任务对象直接交给线程池且 autoDelete，``run()`` 一返回信号接收者就被销毁，
        排队中的事件被丢弃 —— 现象是"任务跑完了但回调永不触发"（播放链路就是这么卡住的）。
        """
        got: list[int] = []
        task = AsyncTask(lambda: 42)
        task.done.connect(got.append)
        task.start()
        del task  # 模拟真实场景：方法返回后局部引用消失（只剩内部保活）
        self.assertTrue(pump(self.app, lambda: bool(got)), "回调未触发（信号被丢弃）")
        self.assertEqual(got[0], 42)

    def test_callback_runs_on_main_thread(self) -> None:
        """坑 2 回归：连到 QObject 绑定方法时，回调必须在主线程执行。"""

        class Receiver(QObject):
            hit = Signal(object)

            @Slot(object)
            def _on_done(self, value) -> None:
                self.hit.emit((value, threading.get_ident()))

        receiver = Receiver()
        received: list[tuple] = []
        receiver.hit.connect(received.append)
        task = AsyncTask(lambda: "x")
        task.done.connect(receiver._on_done)
        task.start()
        del task
        self.assertTrue(pump(self.app, lambda: bool(received)))
        self.assertEqual(received[0][0], "x")
        self.assertEqual(received[0][1], threading.get_ident(), "回调不在主线程执行")

    def test_failure_emits_failed(self) -> None:
        errors: list[Exception] = []

        def boom() -> None:
            raise ValueError("炸了")

        task = AsyncTask(boom)
        task.failed.connect(errors.append)
        task.start()
        del task
        self.assertTrue(pump(self.app, lambda: bool(errors)))
        self.assertIsInstance(errors[0], ValueError)

    def test_arguments_passed_through(self) -> None:
        got: list[tuple] = []
        task = AsyncTask(lambda a, b=0: (a, b), 1, b=2)
        task.done.connect(got.append)
        task.start()
        del task
        self.assertTrue(pump(self.app, lambda: bool(got)))
        self.assertEqual(got[0], (1, 2))

    def test_keepalive_is_released_after_delivery(self) -> None:
        """投递完成后必须释放强引用，否则任务对象永久泄漏。"""
        got: list[int] = []
        task = AsyncTask(lambda: 1)
        task.done.connect(got.append)
        task.start()
        del task
        self.assertTrue(pump(self.app, lambda: bool(got)))
        self.assertTrue(pump(self.app, lambda: async_task.live_task_count() == 0, 3000),
                        f"仍有 {async_task.live_task_count()} 个任务未释放")

    def test_heavy_load_no_loss(self) -> None:
        """并发 20 个任务，结果一个都不能少。"""
        got: list[int] = []
        for i in range(20):
            t = AsyncTask(lambda i=i: i)
            t.done.connect(got.append)
            t.start()
            del t
        self.assertTrue(pump(self.app, lambda: len(got) == 20, 20000), f"只收到 {len(got)}/20")
        self.assertEqual(sorted(got), list(range(20)))


if __name__ == "__main__":
    unittest.main()
