"""搜索状态（AC-2.1/2.4/2.5/2.7）：分页、去重、旧请求丢弃、失败处理。

注意：``SearchState`` 走的是真实异步通道（AsyncTask → 线程池），
测试里用 ``complete()`` 等工作线程把请求发出去后，再手工回灌一页结果。
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import redirect_config  # noqa: E402

from nemusic.model.types import SearchPage, Song  # noqa: E402
from nemusic.net import endpoints as ep  # noqa: E402
from nemusic.state.search_history import SearchHistory  # noqa: E402
from nemusic.state.search_state import SearchState  # noqa: E402


class FakeApi:
    """记录调用参数并按 (type, offset) 生成假分页。"""

    def __init__(self, *, per_page: int = 30, total: int = 75, fail: bool = False, duplicate_last: bool = True) -> None:
        self.per_page = per_page
        self.total = total
        self.fail = fail
        self.duplicate_last = duplicate_last
        self.calls: list[tuple[str, int, int, int]] = []

    def build_page(self, keyword: str, search_type: int, offset: int, limit: int) -> SearchPage:
        # 每页正好 limit 条；第二页起首条与上页末条重复（模拟真实接口的重复），
        # 且不会返回超过 total 的条目
        start = max(offset - 1, 0)
        items = [Song(id=start + i, name=f"歌{start + i}") for i in range(limit) if start + i < self.total]
        return SearchPage(keyword=keyword, search_type=search_type, offset=offset, limit=limit,
                          total=self.total, items=items)

    def search(self, keyword: str, search_type: int, offset: int, limit: int):
        self.calls.append((keyword, search_type, offset, limit))
        if self.fail:
            raise RuntimeError("boom")
        return self.build_page(keyword, search_type, offset, limit)


def wait_calls(api: FakeApi, count: int = 1, timeout: float = 5.0) -> None:
    """等工作线程把请求发出去（异步通道是真实线程池）。"""
    deadline = time.time() + timeout
    while time.time() < deadline and len(api.calls) < count:
        time.sleep(0.005)
    assert len(api.calls) >= count, f"api 只被调用 {len(api.calls)}/{count} 次"


def complete(state: SearchState, api: FakeApi, timeout: float = 5.0) -> None:
    """等工作线程把**新**请求发出去，再把同样参数的一页结果回灌给 state。"""
    wait_calls(api, len(api.calls) + 1, timeout)
    keyword, search_type, offset, limit = api.calls[-1]
    state._on_page((keyword, search_type, api.build_page(keyword, search_type, offset, limit), None), state._generation)


def complete_error(state: SearchState, api: FakeApi, message: str = "boom") -> None:
    """等工作线程把请求发出去，再回灌一个失败。"""
    wait_calls(api, 1)
    keyword, search_type, _offset, _limit = api.calls[-1]
    state._on_page((keyword, search_type, None, RuntimeError(message)), state._generation)


def state_with(api: FakeApi, *, history=None) -> SearchState:
    return SearchState(api, history=history)  # type: ignore[arg-type]


class TestSearchBasics(unittest.TestCase):
    def setUp(self) -> None:
        redirect_config()

    def test_first_page_loaded(self) -> None:
        api = FakeApi()
        state = state_with(api)
        state.search("周杰伦", ep.SEARCH_TYPE_SONG)
        complete(state, api)
        tab = state.tab(ep.SEARCH_TYPE_SONG)
        self.assertEqual(tab.count, 30)
        self.assertEqual(tab.offset, 30)
        self.assertEqual(tab.total, 75)
        self.assertFalse(tab.finished)
        self.assertEqual(api.calls[0], ("周杰伦", 1, 0, 30))

    def test_load_more_appends_and_dedupes(self) -> None:
        api = FakeApi()
        state = state_with(api)
        state.search("x")
        complete(state, api)
        before = state.tab(ep.SEARCH_TYPE_SONG).count
        state.load_more(ep.SEARCH_TYPE_SONG)
        complete(state, api)
        after = state.tab(ep.SEARCH_TYPE_SONG)
        self.assertEqual(after.offset, 60)  # 按接口分页推进，不因去重而少走
        self.assertEqual(after.count, before + 29)  # 30 条里有 1 条与上页重复，被去重
        ids = [s.id for s in after.items]
        self.assertEqual(len(ids), len(set(ids)), "分页结果里有重复 id")

    def test_stops_when_total_reached(self) -> None:
        api = FakeApi(total=45)  # 一页半：第二页就够了
        state = state_with(api)
        state.search("x")
        complete(state, api)
        self.assertFalse(state.tab(ep.SEARCH_TYPE_SONG).finished)
        state.load_more(ep.SEARCH_TYPE_SONG)
        complete(state, api)
        self.assertTrue(state.tab(ep.SEARCH_TYPE_SONG).finished)
        state.load_more(ep.SEARCH_TYPE_SONG)  # 已到底，不再请求
        time.sleep(0.2)
        self.assertEqual(len(api.calls), 2)

    def test_blank_keyword_ignored(self) -> None:
        api = FakeApi()
        state = state_with(api)
        state.search("   ", ep.SEARCH_TYPE_SONG)
        self.assertEqual(api.calls, [])
        self.assertEqual(state.keyword, "")

    def test_tabs_are_isolated(self) -> None:
        api = FakeApi()
        state = state_with(api)
        state.search("x", ep.SEARCH_TYPE_SONG)
        complete(state, api)
        state.search("y", ep.SEARCH_TYPE_ARTIST)
        complete(state, api)
        self.assertEqual(state.tab(ep.SEARCH_TYPE_SONG).count, 0)  # 被重置
        self.assertEqual(state.tab(ep.SEARCH_TYPE_ARTIST).count, 30)
        self.assertEqual(len(api.calls), 2)

    def test_history_recorded(self) -> None:
        history = SearchHistory()
        api = FakeApi()
        state = state_with(api, history=history)
        state.search("周杰伦")
        complete(state, api)
        self.assertEqual(history.items, ["周杰伦"])
        state.search("山河图")
        complete(state, api)
        self.assertEqual(history.items, ["山河图", "周杰伦"])

    def test_same_keyword_not_recorded_twice(self) -> None:
        history = SearchHistory()
        api = FakeApi()
        state = state_with(api, history=history)
        state.search("a")
        complete(state, api)
        state.search("a")
        self.assertEqual(history.items, ["a"])

    def test_loading_guard(self) -> None:
        """同一 tab 正在加载时，再次 load_more 应被忽略。"""
        api = FakeApi()
        state = state_with(api)
        state.search("x")
        wait_calls(api, 1)          # 请求已发出，tab 仍处于 loading 状态
        state.load_more(ep.SEARCH_TYPE_SONG)
        time.sleep(0.2)             # 给线程一点时间，确认没有第二次请求
        self.assertEqual(len(api.calls), 1)


class TestStaleResults(unittest.TestCase):
    def setUp(self) -> None:
        redirect_config()

    def test_stale_page_is_discarded(self) -> None:
        """连续改关键词时，先发的旧请求结果不能覆盖新结果（AC-2.7）。"""
        api = FakeApi()
        state = state_with(api)
        state.search("第一个")
        first_generation = state._generation
        complete(state, api)  # 先让第一次搜索落地，第二次才不会被 loading 挡住
        state.search("第二个")
        self.assertGreater(state._generation, first_generation)
        stale = SearchPage(keyword="第一个", search_type=1, offset=0, limit=30, total=75,
                           items=[Song(id=999, name="迟到结果")])
        state._on_page(("第一个", ep.SEARCH_TYPE_SONG, stale, None), first_generation)
        self.assertEqual(state.tab(ep.SEARCH_TYPE_SONG).items, [])
        fresh = SearchPage(keyword="第二个", search_type=1, offset=0, limit=30, total=75,
                           items=[Song(id=1, name="新结果")])
        state._on_page(("第二个", ep.SEARCH_TYPE_SONG, fresh, None), state._generation)
        self.assertEqual([s.id for s in state.tab(ep.SEARCH_TYPE_SONG).items], [1])


class TestFailures(unittest.TestCase):
    def setUp(self) -> None:
        redirect_config()

    def test_failure_emits_signal_and_stops(self) -> None:
        failures: list[tuple[int, str]] = []
        api = FakeApi(fail=True)
        state = state_with(api)
        state.search_failed.connect(lambda t, m: failures.append((t, m)))
        state.search("x")
        complete_error(state, api)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][0], ep.SEARCH_TYPE_SONG)
        self.assertIn("boom", failures[0][1])
        self.assertTrue(state.tab(ep.SEARCH_TYPE_SONG).finished)
        self.assertFalse(state.tab(ep.SEARCH_TYPE_SONG).loading)

    def test_other_tab_types(self) -> None:
        api = FakeApi()
        state = state_with(api)
        for search_type in (ep.SEARCH_TYPE_ARTIST, ep.SEARCH_TYPE_ALBUM, ep.SEARCH_TYPE_PLAYLIST):
            state.search("x", search_type)
            complete(state, api)
            self.assertEqual(state.tab(search_type).count, 30)


if __name__ == "__main__":
    unittest.main()
