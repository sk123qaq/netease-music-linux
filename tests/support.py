"""测试公共辅助：把配置/缓存/日志路径重定向到临时目录，避免污染真实用户数据。

用法::

    from support import redirect_config
    redirect_config(tmpdir)        # 在 setUp 里调用
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 让 `import nemusic` 生效（无需安装）
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import nemusic.config as config  # noqa: E402


def redirect_config(tmpdir: str | None = None) -> Path:
    """把所有落盘路径指向临时目录，返回该目录。

    注意：这些常量在其它模块里是**动态读取**的（``config.COOKIE_FILE`` 等），
    因此直接改模块属性即可全局生效。
    """
    root = Path(tmpdir or tempfile.mkdtemp(prefix="nemusic-test-"))
    config.CONFIG_DIR = root
    config.CONFIG_FILE = root / "config.json"
    config.COOKIE_FILE = root / "cookies.json"
    config.SEARCH_HISTORY_FILE = root / "search_history.json"
    config.CACHE_DIR = root / "cache"
    config.AUDIO_CACHE_DIR = root / "cache" / "audio"
    config.PLAYLIST_CACHE_DIR = root / "cache" / "playlist"
    config.LYRIC_CACHE_DIR = root / "cache" / "lyric"
    config.COVER_CACHE_DIR = root / "cache" / "cover"
    config.BACKGROUND_DIR = root / "backgrounds"
    config.LOG_DIR = root / "logs"
    config.LOG_FILE = root / "logs" / "app.log"
    config.RUNTIME_DIR = root / "run"
    config.ensure_dirs()
    return root


def reset_config_paths() -> None:
    """恢复为真实路径（测试收尾用）。"""
    import importlib

    importlib.reload(config)


def qt_app():
    """整个测试会话共用的 **QApplication**（必须在任何 QWidget 之前创建）。

    踩过的坑：mpv 测试先用 ``QCoreApplication``，界面测试再创建 QWidget 时
    Qt 会直接 abort（``QWidget: Cannot create a QWidget without QApplication``）。
    所以这里统一创建一次 QApplication（offscreen），两类测试共用。
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def pump_until(predicate, timeout: float = 5.0):
    """跑 Qt 事件循环直到 ``predicate()`` 为真（配合 AsyncTask 的排队信号）。

    返回 True 表示条件达成。异步任务的结果通过排队连接回到主线程，
    不 processEvents 就永远收不到（见 tests/test_async_task.py）。
    """
    import time

    app = qt_app()
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            app.processEvents()
            return True
        time.sleep(0.005)
    app.processEvents()
    return False
