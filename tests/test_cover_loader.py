"""封面加载（AC-2.1 播放条封面）：磁盘缓存 / 子线程下载 / token 防串台。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import pump_until, redirect_config  # noqa: E402

from nemusic.state.cache import CacheManager  # noqa: E402
from nemusic.state.cover_loader import CoverLoader  # noqa: E402

#: 一张 1x1 的 PNG（够小，够真）
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


class TestCoverLoader(unittest.TestCase):
    def setUp(self) -> None:
        redirect_config()
        self.cache = CacheManager()
        self.loader = CoverLoader(self.cache)
        self.got: list = []

        def _collect(token, pixmap):
            self.got.append((token, pixmap))

        self.loader.ready.connect(_collect)

    def _wait(self) -> bool:
        return pump_until(lambda: bool(self.got))

    def test_empty_url_emits_none_pixmap(self) -> None:
        self.loader.request("", token=7)
        self.assertEqual(self.got, [(7, None)])

    def test_download_and_cache(self) -> None:
        calls: list[str] = []

        def fake_get(url, **kwargs):
            calls.append(url)
            return _Resp(PNG_1PX)

        import nemusic.state.cover_loader as mod

        original = mod.requests.get
        mod.requests.get = fake_get
        try:
            self.loader.request("http://example.com/a.jpg", token="t1")
            self.assertTrue(self._wait(), "ready 信号没到")
        finally:
            mod.requests.get = original
        token, pix = self.got[0]
        self.assertEqual(token, "t1")
        self.assertIsNotNone(pix)
        self.assertFalse(pix.isNull())
        self.assertEqual(len(calls), 1)
        # 落盘了，第二次应命中缓存不再下载
        self.assertTrue(self.cache.cover_lookup("http://example.com/a.jpg").exists())

    def test_second_request_hits_disk_cache(self) -> None:
        import nemusic.state.cover_loader as mod

        path = self.cache.cover_path("http://example.com/b.jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG_1PX)
        calls: list[str] = []

        def fake_get(url, **kwargs):
            calls.append(url)
            raise AssertionError("命中缓存时不该下载")

        original = mod.requests.get
        mod.requests.get = fake_get
        try:
            self.loader.request("http://example.com/b.jpg", token=1)
            self.assertTrue(self._wait())
        finally:
            mod.requests.get = original
        self.assertEqual(calls, [])
        self.assertIsNotNone(self.got[0][1])

    def test_download_failure_gives_none(self) -> None:
        import nemusic.state.cover_loader as mod

        def fake_get(url, **kwargs):
            raise OSError("网络不通")

        original = mod.requests.get
        mod.requests.get = fake_get
        try:
            self.loader.request("http://bad/x.png", token="x")
            self.assertTrue(self._wait())
        finally:
            mod.requests.get = original
        self.assertEqual(self.got[0], ("x", None))

    def test_http_error_gives_none(self) -> None:
        import nemusic.state.cover_loader as mod

        def fake_get(url, **kwargs):
            return _Resp(b"", status=404)

        original = mod.requests.get
        mod.requests.get = fake_get
        try:
            self.loader.request("http://bad/y.png", token=2)
            self.assertTrue(self._wait())
        finally:
            mod.requests.get = original
        self.assertEqual(self.got[0], (2, None))

    def test_failed_url_not_retried(self) -> None:
        """失败过的 URL 本次运行内不再重试（避免每首歌都卡一次超时）。"""
        import nemusic.state.cover_loader as mod

        calls: list[str] = []

        def fake_get(url, **kwargs):
            calls.append(url)
            raise OSError("boom")

        original = mod.requests.get
        mod.requests.get = fake_get
        try:
            self.loader.request("http://bad/z.png", token=1)
            self.assertTrue(self._wait())
            self.loader.request("http://bad/z.png", token=2)
            self.assertTrue(pump_until(lambda: len(self.got) >= 2))
        finally:
            mod.requests.get = original
        self.assertEqual(len(calls), 1, "第二次应该直接放弃，不再下载")
        self.assertEqual([token for token, _ in self.got], [1, 2])

    def test_clear_failed_allows_retry(self) -> None:
        import nemusic.state.cover_loader as mod

        calls: list[str] = []

        def fake_get(url, **kwargs):
            calls.append(url)
            raise OSError("boom")

        original = mod.requests.get
        mod.requests.get = fake_get
        try:
            self.loader.request("http://bad/w.png", token=1)
            self.assertTrue(self._wait())
            self.loader.clear_failed()
            self.loader.request("http://bad/w.png", token=2)
            self.assertTrue(pump_until(lambda: len(self.got) >= 2))
        finally:
            mod.requests.get = original
        self.assertEqual(len(calls), 2, "清空失败集合后允许重试")

    def test_token_distinguishes_songs(self) -> None:
        """token 原样带回，上层用它判断封面是否还属于当前歌曲。"""
        import nemusic.state.cover_loader as mod

        original = mod.requests.get
        mod.requests.get = lambda url, **kw: _Resp(PNG_1PX)
        try:
            self.loader.request("http://a/1.png", token=111)
            self.loader.request("http://a/2.png", token=222)
            self.assertTrue(pump_until(lambda: len(self.got) >= 2))
        finally:
            mod.requests.get = original
        # 两个请求是并发跑的（线程池），完成顺序不保证，只要求两个 token 都原样带回
        self.assertEqual({token for token, _ in self.got}, {111, 222})


class _Resp:
    def __init__(self, content: bytes, status: int = 200) -> None:
        self.content = content
        self.status_code = status


if __name__ == "__main__":
    unittest.main()
