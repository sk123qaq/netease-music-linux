"""播放控制器的缓存提交决策（AC-5.1/5.2/5.3）。

只测纯逻辑：用假 cache / 假 player 驱动 ``_finish_record``，
不启动真 mpv（mpv IPC 由 test_mpv_ipc.py 覆盖）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import redirect_config  # noqa: E402

from nemusic.config import Config  # noqa: E402
from nemusic.model.types import Song  # noqa: E402
from nemusic.player.controller import END_GRACE_MS, PlaybackController  # noqa: E402
from nemusic.state.cache import CacheManager  # noqa: E402


class FakeCache:
    def __init__(self) -> None:
        self.commits: list[dict] = []
        self.discards: list[Path] = []
        self.sweeps: list = []
        self.commit_result = Path("/tmp/fake-cache.mp3")

    def commit_audio(self, part, song_id, level, ext, expected_size=0, *, played_to_end=False):
        """按真实 CacheManager 的语义回答：没确认听完就当不完整（返回 None）。"""
        self.commits.append(dict(part=part, song_id=song_id, level=level, ext=ext,
                                 expected_size=expected_size, played_to_end=played_to_end))
        if not played_to_end and expected_size:
            self.discards.append(part)  # 真实实现里 commit_audio 会自己删掉不完整的文件
            return None
        return self.commit_result

    def discard(self, part) -> None:
        self.discards.append(part)

    def sweep_parts(self, keep=None) -> int:
        self.sweeps.append(keep)
        return 0


class _FakeSignal:
    """记录连接关系即可，控制器只 connect/不 emit。"""

    def connect(self, _slot) -> None:  # noqa: D102
        pass

    def emit(self, *_args) -> None:  # noqa: D102
        pass


class FakePlayer:
    """不需要真 mpv：控制器只连信号、调 start()。"""

    def __init__(self) -> None:
        self.position_changed = _FakeSignal()
        self.duration_changed = _FakeSignal()
        self.paused_changed = _FakeSignal()
        self.ended = _FakeSignal()
        self.failed = _FakeSignal()
        self.ready = _FakeSignal()

    def start(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


def make_controller() -> tuple[PlaybackController, FakeCache]:
    cache = FakeCache()
    ctl = PlaybackController(api=None, config=Config.load(), cache=cache, session_state=None, player=FakePlayer())  # type: ignore[arg-type]
    return ctl, cache


def arm(ctl: PlaybackController, *, duration_ms: int = 226_000, position_ms: int = 226_000,
       expected: int = 9_081_645) -> None:
    """模拟一次正在录制的播放。"""
    ctl._duration_ms = duration_ms
    ctl._position_ms = position_ms
    ctl._seeked = False
    ctl._record = (Path("/tmp/.1_exhigh.mp3"), 1, "exhigh", "mp3", expected)


class TestFinishRecord(unittest.TestCase):
    def setUp(self) -> None:
        redirect_config()

    def test_natural_end_commits(self) -> None:
        ctl, cache = make_controller()
        arm(ctl)
        events: list[tuple[int, str]] = []
        ctl.cache_event.connect(lambda sid, kind: events.append((sid, kind)))
        ctl._finish_record("eof")
        self.assertEqual(events, [(1, "stored")])
        self.assertTrue(cache.commits[0]["played_to_end"])
        self.assertEqual(cache.discards, [])

    def test_error_at_natural_end_still_commits(self) -> None:
        """回归：mpv 在网络流自然播完时也可能报 error，不能因此丢掉缓存。"""
        ctl, cache = make_controller()
        arm(ctl)
        events: list[tuple[int, str]] = []
        ctl.cache_event.connect(lambda sid, kind: events.append((sid, kind)))
        ctl._finish_record("error")
        self.assertEqual(events, [(1, "stored")])
        self.assertTrue(cache.commits[0]["played_to_end"])

    def test_stop_midway_discards(self) -> None:
        """AC-5.2：切歌时只听了 10 秒 → 不提交。"""
        ctl, cache = make_controller()
        arm(ctl, position_ms=10_000)
        events: list[tuple[int, str]] = []
        ctl.cache_event.connect(lambda sid, kind: events.append((sid, kind)))
        ctl._finish_record("stop")
        self.assertEqual(events, [(1, "discarded")])
        self.assertEqual(len(cache.commits), 1)          # 尝试过提交
        self.assertFalse(cache.commits[0]["played_to_end"])  # 但没确认听完
        self.assertEqual(len(cache.discards), 1)         # 最终被丢弃

    def test_grace_window_counts_as_finished(self) -> None:
        """差 3 秒（在 5 秒宽容度内）算播完。"""
        ctl, cache = make_controller()
        arm(ctl, position_ms=226_000 - 3000)
        ctl._finish_record("eof")
        self.assertTrue(cache.commits[0]["played_to_end"])

    def test_beyond_grace_window_is_incomplete(self) -> None:
        """差 30 秒 → 明显没听完，不算播完。"""
        ctl, cache = make_controller()
        arm(ctl, position_ms=226_000 - 30_000)
        ctl._finish_record("eof")
        self.assertFalse(cache.commits[0]["played_to_end"])

    def test_unknown_duration_never_confirms(self) -> None:
        ctl, cache = make_controller()
        arm(ctl, duration_ms=0, position_ms=226_000)
        ctl._finish_record("eof")
        self.assertFalse(cache.commits[0]["played_to_end"])

    def test_seeked_always_discards(self) -> None:
        """拖过进度 → 录制内容缺段，一律丢弃（即使位置到底）。"""
        ctl, cache = make_controller()
        arm(ctl)
        ctl._seeked = True
        ctl._finish_record("eof")
        self.assertEqual(cache.commits, [])
        self.assertEqual(len(cache.discards), 1)

    def test_no_record_is_noop(self) -> None:
        ctl, cache = make_controller()
        ctl._record = None
        ctl._finish_record("eof")
        self.assertEqual(cache.commits, [])
        self.assertEqual(cache.discards, [])

    def test_grace_constant(self) -> None:
        self.assertEqual(END_GRACE_MS, 5000)


class TestPartSweep(unittest.TestCase):
    """孤儿临时文件兜底清扫（切歌竞态，见 cache.sweep_parts）。用真实 CacheManager。"""

    def setUp(self) -> None:
        self.tmp = redirect_config()
        self.cache = CacheManager(Config.load())

    def _controller(self):
        from nemusic.player.controller import PlaybackController

        return PlaybackController(api=None, config=Config.load(), cache=self.cache,  # type: ignore[arg-type]
                                  session_state=None, player=FakePlayer())

    def _part(self, song_id: int) -> Path:
        path = self.cache.audio_part_path(song_id, "exhigh", "mp3")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
        return path

    def test_sweep_keeps_current_recording(self) -> None:
        ctl = self._controller()
        arm(ctl)
        current = ctl._record[0]
        current.write_bytes(b"x" * 10)
        orphan = self._part(999)
        ctl._sweep_parts()
        self.assertTrue(current.exists())
        self.assertFalse(orphan.exists())

    def test_sweep_without_recording_clears_all(self) -> None:
        ctl = self._controller()
        ctl._record = None
        self._part(1)
        self._part(2)
        ctl._sweep_parts()
        self.assertEqual(list(self.cache.audio_part_path(1, "exhigh", "mp3").parent.glob(".*")), [])

    def test_sweep_never_touches_committed_files(self) -> None:
        final = self.cache.audio_final_path(5, "exhigh", "mp3")
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"x" * 10)
        self._part(1)
        ctl = self._controller()
        ctl._sweep_parts()
        self.assertTrue(final.exists())
        self.assertFalse(self.cache.audio_part_path(1, "exhigh", "mp3").exists())

    def test_sweep_interval_constant(self) -> None:
        from nemusic.player.controller import PART_SWEEP_INTERVAL_MS

        self.assertEqual(PART_SWEEP_INTERVAL_MS, 10_000)


if __name__ == "__main__":
    unittest.main()
