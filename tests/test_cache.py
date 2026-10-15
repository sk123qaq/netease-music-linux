"""缓存管理：完整性校验、LRU 淘汰、统计与清理（AC-5.1~5.6）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import redirect_config  # noqa: E402

from nemusic.config import Config  # noqa: E402
from nemusic.state.cache import CacheManager  # noqa: E402


class TestAudioPaths(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = redirect_config()
        self.cache = CacheManager(Config.load())

    def test_part_path_has_valid_extension(self) -> None:
        """坑 1 回归：临时文件必须带 mpv 认得出来的扩展名（否则静默不写）。"""
        part = self.cache.audio_part_path(123, "exhigh", "mp3")
        self.assertTrue(part.name.endswith(".mp3"))
        self.assertTrue(part.name.startswith("."))  # 隐藏，避免被当成正式缓存
        self.assertNotIn(".part", part.name)

    def test_lookup_empty(self) -> None:
        self.assertIsNone(self.cache.audio_lookup(1))

    def test_lookup_ignores_empty_file(self) -> None:
        path = self.cache.audio_final_path(7, "standard", "mp3")
        path.write_bytes(b"")
        self.assertIsNone(self.cache.audio_lookup(7))

    def test_lookup_finds_any_level(self) -> None:
        self.cache.audio_final_path(9, "higher", "mp3").write_bytes(b"x" * 100)
        found = self.cache.audio_lookup(9)
        self.assertIsNotNone(found)
        self.assertIn("higher", found.name)

    def test_lookup_ignores_part_files(self) -> None:
        """回归：隐藏的录制中临时文件不能被当成缓存命中。

        曾经 ``glob(f"{id}_*")`` 命中了 ``.{id}_exhigh.mp3``，导致播放半成品、
        且永远不再录制（AC-5.1/5.3）。
        """
        part = self.cache.audio_part_path(1, "exhigh", "mp3")
        part.write_bytes(b"x" * 1024)
        self.assertIsNone(self.cache.audio_lookup(1))
        # 正式文件出现后才能命中
        final = self.cache.audio_final_path(1, "exhigh", "mp3")
        final.write_bytes(b"x" * 2048)
        self.assertEqual(self.cache.audio_lookup(1), final)

    def test_lookup_disabled_by_config(self) -> None:
        self.cache.audio_final_path(11, "standard", "mp3").write_bytes(b"x" * 100)
        self.cache.cfg.cache_audio_enabled = False
        self.assertIsNone(self.cache.audio_lookup(11))


class TestCommit(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = redirect_config()
        self.cache = CacheManager(Config.load())

    def _part(self, song_id: int = 1, size: int = 1000, level: str = "standard", ext: str = "mp3") -> Path:
        part = self.cache.audio_part_path(song_id, level, ext)
        part.write_bytes(b"m" * size)
        return part

    def test_commit_complete_file(self) -> None:
        part = self._part(size=1000)
        final = self.cache.commit_audio(part, 1, "standard", "mp3", expected_size=1000)
        self.assertIsNotNone(final)
        self.assertTrue(final.exists())
        self.assertFalse(part.exists())
        self.assertEqual(final.stat().st_size, 1000)

    def test_commit_within_tolerance(self) -> None:
        part = self._part(size=1005)
        self.assertIsNotNone(self.cache.commit_audio(part, 1, "standard", "mp3", expected_size=1000))

    def test_played_to_end_allows_truncated_tail(self) -> None:
        """回归：听完整首但尾包丢失（差 1~2%）仍应缓存。

        实测网络流播完时 mpv 可能报 error、录到的字节比 API 声明少 1.86%，
        按 ±1% 判会把 98% 完整的歌丢掉。
        """
        part = self._part(size=8_912_896)
        final = self.cache.commit_audio(part, 1, "exhigh", "mp3", expected_size=9_081_645, played_to_end=True)
        self.assertIsNotNone(final)
        self.assertTrue(final.exists())

    def test_truncated_tail_still_discarded_without_confirmation(self) -> None:
        """没确认听完时仍然严格：差 1.86% 一律丢弃（可能是被切断的半首歌）。"""
        part = self._part(size=8_912_896)
        self.assertIsNone(self.cache.commit_audio(part, 1, "exhigh", "mp3", expected_size=9_081_645))
        self.assertFalse(part.exists())

    def test_severely_truncated_discarded_even_if_played(self) -> None:
        """确认听完也不放过差太多的（超过 5% 说明内容真的缺了）。"""
        part = self._part(size=8_000_000)
        self.assertIsNone(self.cache.commit_audio(part, 1, "exhigh", "mp3", expected_size=9_081_645, played_to_end=True))

    def test_discard_when_incomplete(self) -> None:
        """AC-5.2：中途切歌写出的半首歌必须被丢弃。"""
        part = self._part(size=400)
        self.assertIsNone(self.cache.commit_audio(part, 1, "standard", "mp3", expected_size=1000))
        self.assertFalse(part.exists())
        self.assertFalse(self.cache.audio_final_path(1, "standard", "mp3").exists())

    def test_discard_empty_file(self) -> None:
        part = self._part(size=0)
        self.assertIsNone(self.cache.commit_audio(part, 1, "standard", "mp3", expected_size=1000))
        self.assertFalse(part.exists())

    def test_commit_without_expected_size(self) -> None:
        part = self._part(size=10)
        self.assertIsNotNone(self.cache.commit_audio(part, 1, "standard", "mp3"))

    def test_commit_missing_part(self) -> None:
        self.assertIsNone(self.cache.commit_audio(self.cache.audio_part_path(2, "standard", "mp3"), 2, "standard", "mp3"))

    def test_discard_is_idempotent(self) -> None:
        part = self._part()
        self.cache.discard(part)
        self.cache.discard(part)  # 不抛异常

    def test_sweep_parts_keeps_current_recording(self) -> None:
        """回归：切歌竞态下 mpv 会在丢弃之后才建文件，兜底清扫必须保留当前录制文件。

        真机复现：连点几首歌后缓存目录残留一堆完整大小的 ``.mp3``。
        """
        keep = self.cache.audio_part_path(1, "exhigh", "mp3")
        keep.write_bytes(b"x" * 100)
        orphan = self.cache.audio_part_path(2, "exhigh", "mp3")
        orphan.write_bytes(b"x" * 100)
        final = self.cache.audio_final_path(3, "exhigh", "mp3")
        final.write_bytes(b"x" * 100)
        removed = self.cache.sweep_parts(keep=keep)
        self.assertEqual(removed, 1)
        self.assertTrue(keep.exists())     # 正在录制的不能动
        self.assertFalse(orphan.exists())  # 孤儿清掉
        self.assertTrue(final.exists())    # 正式缓存不能动

    def test_sweep_parts_without_keep_clears_all(self) -> None:
        for i in (1, 2):
            self.cache.audio_part_path(i, "exhigh", "mp3").write_bytes(b"x")
        self.assertEqual(self.cache.sweep_parts(), 2)
        self.assertEqual(list(self.tmp.glob("cache/audio/.*")), [])

    def test_sweep_parts(self) -> None:
        self._part(song_id=3)
        self._part(song_id=4)
        self.cache.audio_final_path(5, "standard", "mp3").write_bytes(b"keep")
        self.assertEqual(self.cache.sweep_parts(), 2)
        self.assertTrue(self.cache.audio_final_path(5, "standard", "mp3").exists())


class TestLruTrim(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = redirect_config()
        self.cache = CacheManager(Config.load())

    def _make(self, song_id: int, size: int, mtime: float) -> Path:
        path = self.cache.audio_final_path(song_id, "standard", "mp3")
        path.write_bytes(b"x" * size)
        os.utime(path, (mtime, mtime))
        return path

    def test_no_trim_under_limit(self) -> None:
        self._make(1, 100, time.time())
        self.assertEqual(self.cache.trim_audio(limit_bytes=1000), 0)

    def test_trim_removes_oldest_first(self) -> None:
        now = time.time()
        old = self._make(1, 400, now - 300)
        mid = self._make(2, 400, now - 200)
        new = self._make(3, 400, now - 100)
        removed = self.cache.trim_audio(limit_bytes=800)
        self.assertEqual(removed, 1)
        self.assertFalse(old.exists())  # 最旧的先去
        self.assertTrue(mid.exists())
        self.assertTrue(new.exists())

    def test_trim_respects_config_limit(self) -> None:
        now = time.time()
        self._make(1, 600, now - 200)
        self._make(2, 600, now - 100)
        self.cache.cfg.cache_limit_bytes = 700
        self.assertGreaterEqual(self.cache.trim_audio(), 1)
        self.assertLessEqual(self.cache.audio_size_bytes(), 700)


class TestJsonCache(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = redirect_config()
        self.cache = CacheManager(Config.load())

    def test_playlist_roundtrip(self) -> None:
        self.cache.put_json("playlist", 123, {"name": "我喜欢的音乐", "tracks": [1, 2]})
        got = self.cache.get_json("playlist", 123, ttl=3600)
        self.assertEqual(got["name"], "我喜欢的音乐")
        path = self.tmp / "cache" / "playlist" / "playlist_123.json"
        self.assertTrue(path.exists())

    def test_ttl_expiry(self) -> None:
        self.cache.put_json("lyric", 5, {"lrc": "x"})
        path = self.tmp / "cache" / "lyric" / "lyric_5.json"
        old = time.time() - 10_000
        os.utime(path, (old, old))
        self.assertIsNone(self.cache.get_json("lyric", 5, ttl=3600))
        self.assertIsNotNone(self.cache.get_json("lyric", 5, ttl=0))  # ttl=0 表示不过期

    def test_corrupt_json_returns_none(self) -> None:
        path = self.tmp / "cache" / "lyric" / "lyric_9.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{oops", encoding="utf-8")
        self.assertIsNone(self.cache.get_json("lyric", 9, ttl=100))

    def test_unknown_kind_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.cache.get_json("bogus", 1, ttl=1)


class TestStatsAndClear(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = redirect_config()
        self.cache = CacheManager(Config.load())

    def _populate(self) -> None:
        self.cache.audio_final_path(1, "standard", "mp3").write_bytes(b"a" * 500)
        self.cache.audio_final_path(2, "standard", "mp3").write_bytes(b"b" * 500)
        self.cache.put_json("playlist", 1, {"x": 1})
        self.cache.put_json("lyric", 1, {"y": 2})

    def test_stats(self) -> None:
        self._populate()
        stats = self.cache.stats()
        self.assertEqual(stats["audio"]["count"], 2)
        self.assertEqual(stats["audio"]["bytes"], 1000)
        self.assertEqual(stats["playlist"]["count"], 1)
        self.assertEqual(stats["lyric"]["count"], 1)
        self.assertIn("mb", stats["audio"])

    def test_stats_ignores_part_files(self) -> None:
        self.cache.audio_part_path(3, "standard", "mp3").write_bytes(b"z" * 999)
        self.assertEqual(self.cache.stats()["audio"]["count"], 0)

    def test_clear_single_kind(self) -> None:
        self._populate()
        count, size = self.cache.clear("audio")
        self.assertEqual((count, size), (2, 1000))
        self.assertEqual(self.cache.stats()["audio"]["count"], 0)
        self.assertEqual(self.cache.stats()["lyric"]["count"], 1)  # 不影响其它类

    def test_clear_all(self) -> None:
        self._populate()
        count, size = self.cache.clear("all")
        self.assertEqual(count, 4)
        self.assertGreater(size, 1000)
        self.assertEqual(sum(s["count"] for s in self.cache.stats().values()), 0)

    def test_format_size(self) -> None:
        self.assertEqual(self.cache.format_size(512), "512 B")
        self.assertEqual(self.cache.format_size(1024 * 1024 * 3), "3.0 MB")
        self.assertIn("GB", self.cache.format_size(1024**3 * 2))

    def test_open_dir_creates(self) -> None:
        path = self.cache.open_dir("lyric")
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
