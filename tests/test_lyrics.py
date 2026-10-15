"""歌词状态层（AC-4.4/4.6）：缓存优先、失败回落、子线程只出信号。"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import pump_until, redirect_config  # noqa: E402

from nemusic.model.lyric_index import LyricIndex  # noqa: E402
from nemusic.net.api import Api  # noqa: E402
from nemusic.state.cache import CacheManager  # noqa: E402
from nemusic.state.lyrics import LyricState  # noqa: E402

PAYLOAD = {"lrc": {"lyric": "[00:00.00]第一句\n[00:05.00]第二句"}, "tlyric": {"lyric": "[00:00.00]first"}}


class FakeApi(Api):
    """假 Api：只实现 ``lyric``（可注入异常），其余走真实父类但不被调用。"""

    def __init__(self, payload=None, exc=None):
        super().__init__()
        self.payload = payload
        self.exc = exc
        self.calls = 0

    def lyric(self, song_id: int):  # type: ignore[override]
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.payload if self.payload is not None else PAYLOAD


def run(state: LyricState, song_id: int, *, force: bool = False, timeout: float = 5.0):
    """触发请求并等工作线程结果回到主线程，返回 (song_id, LyricDoc) 列表。"""
    got: list = []

    def _collect(song_id, doc):
        got.append((song_id, doc))

    state.loaded.connect(_collect)
    state.request(song_id, force=force)
    assert pump_until(lambda: bool(got), timeout), "loaded 信号没到"
    return got[-1]


class TestLyricState(unittest.TestCase):
    def setUp(self) -> None:
        redirect_config()
        self.cache = CacheManager()

    def _state(self, api):
        return LyricState(api, self.cache)

    def test_fetches_and_caches(self) -> None:
        api = FakeApi()
        state = self._state(api)
        song_id, doc = run(state, 800000001)
        self.assertEqual(song_id, 800000001)
        self.assertEqual([line.text for line in doc.lines], ["第一句", "第二句"])
        self.assertEqual(doc.lines[0].translation, "first")
        self.assertEqual(api.calls, 1)
        # 落盘了
        self.assertIsNotNone(self.cache.get_json("lyric", 800000001, 60))

    def test_second_request_uses_cache(self) -> None:
        api = FakeApi()
        state = self._state(api)
        run(state, 1)
        state.request(1)
        time.sleep(0.2)
        self.assertEqual(api.calls, 1, "第二次应该命中缓存，不再请求")

    def test_force_refetches(self) -> None:
        api = FakeApi()
        state = self._state(api)
        run(state, 1)
        run(state, 1, force=True)
        self.assertEqual(api.calls, 2)

    def test_request_failure_falls_back_to_stale_cache(self) -> None:
        self.cache.put_json("lyric", 9, PAYLOAD)
        self.cache.get_json("lyric", 9, 60)
        state = self._state(FakeApi(exc=RuntimeError("503")))
        song_id, doc = run(state, 9)
        self.assertEqual(song_id, 9)
        self.assertEqual(len(doc.lines), 2, "网络失败要回落到过期缓存")

    def test_request_failure_without_cache_gives_empty_doc(self) -> None:
        state = self._state(FakeApi(exc=RuntimeError("503")))
        song_id, doc = run(state, 3)
        self.assertEqual((song_id, len(doc.lines)), (3, 0))

    def test_placeholder_payload_gives_empty_doc(self) -> None:
        state = self._state(FakeApi(payload={"lrc": {"lyric": "[00:00.00]纯音乐，请欣赏"}}))
        _song_id, doc = run(state, 5)
        self.assertEqual(doc.lines, [])

    def test_index_for_reads_cache_sync(self) -> None:
        self.cache.put_json("lyric", 11, PAYLOAD)
        idx = LyricState(FakeApi(), self.cache).index_for(11)
        self.assertIsInstance(idx, LyricIndex)
        self.assertEqual(idx.index_at(6000), 1)

    def test_bad_id_ignored(self) -> None:
        api = FakeApi()
        state = self._state(api)
        state.request(0)
        state.request(-1)
        pump_until(lambda: True, timeout=0.2)
        self.assertEqual(api.calls, 0)


if __name__ == "__main__":
    unittest.main()
