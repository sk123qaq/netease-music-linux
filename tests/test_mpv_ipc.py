"""mpv IPC 集成测试（离线：本地合成音频，不联网）。

验证（对应 AC-5.1/AC-5.3/AC-6.3 与 SPEC §8 E16 的坑）：
- mpv 能启动、IPC 能握手、能观察属性
- 本地文件能播放，``time-pos`` 会推进
- ``stream-record`` 正常扩展名会写文件；**用 ``.part`` 后缀 mpv 静默不写**（坑 1 回归）
- 关停后无残留 socket / 进程

缺少 mpv 或 ffmpeg 时整体 skip。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import qt_app, redirect_config  # noqa: E402

try:
    from PySide6.QtCore import QCoreApplication  # noqa: F401 - 仅用于能力检测

    HAS_QT = True
except ImportError:  # pragma: no cover
    HAS_QT = False

from nemusic.player import mpv as mpv_module  # noqa: E402


def _make_tone(path: Path, seconds: float = 2.0) -> bool:
    """用 ffmpeg 生成一段正弦波 mp3（离线测试素材）。"""
    if shutil.which("ffmpeg") is None:
        return False
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-codec:a", "libmp3lame", "-b:a", "128k", str(path)],
        capture_output=True,
    )
    return proc.returncode == 0 and path.exists() and path.stat().st_size > 1000


def wait_until(app, predicate, timeout_ms: int = 20000) -> bool:
    """在 Qt 事件循环里等条件成立。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return predicate()


@unittest.skipUnless(HAS_QT, "缺少 PySide6")
@unittest.skipUnless(mpv_module.available(), "系统未安装 mpv")
class TestMpvIpc(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()  # 与界面测试共用同一个 QApplication（见 support.qt_app 注释）
        cls.tmp = Path(tempfile.mkdtemp(prefix="nemusic-mpv-test-"))
        cls.tone = cls.tmp / "tone.mp3"
        cls.has_tone = _make_tone(cls.tone)
        if not cls.has_tone:
            raise unittest.SkipTest("ffmpeg 不可用或生成测试音频失败")

    def setUp(self) -> None:
        redirect_config(str(self.tmp / "cfg"))

    def _player(self, **kwargs):
        sock = self.tmp / f"s-{time.time_ns()}.sock"
        return mpv_module.MpvPlayer(socket_path=sock, extra_args=("--ao=null",), **kwargs)

    def test_handshake_and_observers(self) -> None:
        player = self._player()
        self.addCleanup(player.shutdown)
        ready = []
        player.ready.connect(lambda: ready.append(True))
        self.assertTrue(player.start(), "mpv 应能启动")
        self.assertTrue(wait_until(self.app, lambda: bool(ready), 8000), "IPC 应在超时内就绪")
        self.assertTrue(player.running)

    def test_play_local_file_advances(self) -> None:
        player = self._player()
        self.addCleanup(player.shutdown)
        positions: list[float] = []
        player.position_changed.connect(positions.append)
        loaded: list[str] = []
        player.file_loaded.connect(lambda p: loaded.append(p or "loaded"))
        self.assertTrue(player.start())
        self.assertTrue(wait_until(self.app, lambda: player._connected, 8000))
        player.load(str(self.tone))
        self.assertTrue(wait_until(self.app, lambda: any(p > 0.3 for p in positions), 15000), f"位置应推进：{positions[-3:]}")
        self.assertTrue(loaded)

    def test_stream_record_writes_with_valid_extension(self) -> None:
        """AC-5.1：边播边存必须真的落盘（正确扩展名）。"""
        target = self.tmp / "rec-ok.mp3"
        player = self._player()
        self.addCleanup(player.shutdown)
        self.assertTrue(player.start())
        self.assertTrue(wait_until(self.app, lambda: player._connected, 8000))
        player.load(str(self.tone), record_to=target)
        self.assertTrue(
            wait_until(self.app, lambda: target.exists() and target.stat().st_size > 1000, 15000),
            "stream-record 应写出文件",
        )
        self.assertGreater(target.stat().st_size, 1000)

    def test_stream_record_silent_with_bad_extension(self) -> None:
        """坑 1 回归：``.part`` 后缀 mpv 不写文件（所以临时文件也必须带正确扩展名）。"""
        target = self.tmp / "rec-bad.part"
        if target.exists():
            target.unlink()
        player = self._player()
        self.addCleanup(player.shutdown)
        self.assertTrue(player.start())
        self.assertTrue(wait_until(self.app, lambda: player._connected, 8000))
        player.load(str(self.tone), record_to=target)
        # 播完（或超时）后，该文件都不应出现
        wait_until(self.app, lambda: False, 3000)
        self.assertFalse(target.exists(), "未知扩展名不应产生文件（这正是我们不用 .part 的原因）")

    def test_pause_toggle(self) -> None:
        player = self._player()
        self.addCleanup(player.shutdown)
        states: list[bool] = []
        player.paused_changed.connect(states.append)
        self.assertTrue(player.start())
        self.assertTrue(wait_until(self.app, lambda: player._connected, 8000))
        player.load(str(self.tone))
        self.assertTrue(wait_until(self.app, lambda: any(p is False for p in states) or player._connected, 5000))
        player.set_pause(True)
        self.assertTrue(wait_until(self.app, lambda: True in states, 5000), f"应收到暂停事件：{states}")
        player.set_pause(False)
        self.assertTrue(wait_until(self.app, lambda: states and states[-1] is False, 5000))

    def test_ended_event_on_eof(self) -> None:
        player = self._player()
        self.addCleanup(player.shutdown)
        reasons: list[str] = []
        player.ended.connect(reasons.append)
        self.assertTrue(player.start())
        self.assertTrue(wait_until(self.app, lambda: player._connected, 8000))
        player.load(str(self.tone))
        self.assertTrue(wait_until(self.app, lambda: bool(reasons), 25000), "2 秒音频应在超时内播完")
        self.assertEqual(reasons[0], "eof")

    def test_shutdown_cleans_socket_and_process(self) -> None:
        """AC-6.3：退出后无 socket 残留、进程退出。"""
        player = self._player()
        self.assertTrue(player.start())
        self.assertTrue(wait_until(self.app, lambda: player._connected, 8000))
        sock_path = player.socket_path
        proc = player._proc
        self.assertTrue(sock_path.exists())
        player.shutdown()
        self.assertFalse(sock_path.exists(), "socket 文件应被清理")
        self.assertIsNotNone(proc)
        self.assertIsNotNone(proc.poll(), "mpv 进程应已退出")

    def test_load_before_connection_is_queued(self) -> None:
        """坑 2 回归：mpv 刚启动（IPC 还没握手）时发 load，命令必须排队而不是被丢掉。

        这是端到端验证（tests/e2e_playback.py）发现的：程序刚启动就点播放会“静默不播”。
        """
        player = self._player()
        self.addCleanup(player.shutdown)
        positions: list[float] = []
        player.position_changed.connect(positions.append)
        self.assertTrue(player.start())
        # 不给任何等待，立刻 load（模拟用户秒点播放）
        player.load(str(self.tone))
        self.assertTrue(
            wait_until(self.app, lambda: any(p > 0.2 for p in positions), 20000),
            f"排队命令应在握手后补发并开始播放：{positions[-3:]}",
        )

    def test_volume_and_mute_do_not_crash(self) -> None:
        player = self._player()
        self.addCleanup(player.shutdown)
        self.assertTrue(player.start())
        self.assertTrue(wait_until(self.app, lambda: player._connected, 8000))
        player.set_volume(35)
        player.set_mute(True)
        player.set_mute(False)
        player.set_volume(80)
        self.assertEqual(player.volume, 80)
        self.assertFalse(player.muted)


if __name__ == "__main__":
    unittest.main()
