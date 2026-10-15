"""「我喜欢的音乐」状态（AC-3.1/3.5）：分页、批量补全、顺序、缓存兜底、取消收藏。"""

from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import qt_app, redirect_config  # noqa: E402

from nemusic import config as app_config  # noqa: E402
from nemusic.model.types import Playlist, Song  # noqa: E402
from nemusic.state.library import DETAIL_BATCH, TRACK_PAGE, LibraryState  # noqa: E402

STAR_ID = 900000001


class FakeSession:
    def __init__(self, logged_in: bool = True, uid: int = 1234) -> None:
        self.logged_in = logged_in
        self.uid = uid
        self.star_playlist_id = 0


class FakeApi:
    """按 id 生成曲目；记录调用次数用于验证分页/分批。"""

    def __init__(self, *, track_ids: list[int] | None = None, fail: str | None = None) -> None:
        self.track_ids = track_ids if track_ids is not None else list(range(1, 61))
        self.fail = fail
        self.detail_calls: list[list[int]] = []
        self.page_calls: list[tuple[int, int]] = []

    def star_playlist(self, uid: int) -> Playlist | None:
        if self.fail:
            raise RuntimeError(self.fail)
        return Playlist(id=STAR_ID, name="我喜欢的音乐", track_count=len(self.track_ids),
                        special_type=5, track_ids=list(self.track_ids),
                        tracks=[Song(id=i, name=f"歌{i}") for i in self.track_ids[:2]])

    def playlist_detail(self, playlist_id: int, *, limit: int = 1000, offset: int = 0) -> Playlist:
        """注意：**忽略 offset**，永远返回全量 trackIds —— 真实服务端就是这样，
        分页循环会被它骗进死循环（见 test_requests_playlist_once）。"""
        if self.fail:
            raise RuntimeError(self.fail)
        self.page_calls.append((limit, offset))
        ids = list(self.track_ids)
        return Playlist(id=playlist_id, name="我喜欢的音乐", track_count=len(self.track_ids),
                        special_type=5, track_ids=list(ids),
                        tracks=[Song(id=i, name=f"歌{i}") for i in ids[:2]])

    def song_detail(self, song_ids):
        if self.fail:
            raise RuntimeError(self.fail)
        batch = list(song_ids)
        self.detail_calls.append(batch)
        return [Song(id=i, name=f"歌{i}", artist_names=("歌手",), duration_ms=200000) for i in batch]


def run(state: LibraryState, *, force: bool = False, timeout: float = 5.0) -> tuple[list, list]:
    """先连信号再触发加载（缓存命中时 reload 会同步发射，连晚了就漏掉）。"""
    app = qt_app()
    loaded: list = []
    failed: list = []
    state.songs_loaded.connect(loaded.append)
    state.failed.connect(failed.append)
    state.reload(force=force)
    deadline = time.time() + timeout
    while time.time() < deadline and not (loaded or failed):
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    return loaded, failed


class TestLibraryLoad(unittest.TestCase):
    def setUp(self) -> None:
        redirect_config()

    def test_not_logged_in(self) -> None:
        state = LibraryState(FakeApi(), FakeSession(logged_in=False))
        errors: list[str] = []
        state.failed.connect(errors.append)
        state.reload()
        self.assertEqual(errors, ["请先登录后查看我喜欢的音乐"])
        self.assertEqual(state.songs, [])

    def test_loads_all_tracks_in_order(self) -> None:
        api = FakeApi(track_ids=list(range(10, 0, -1)))  # 逆序，验证按 trackIds 顺序
        state = LibraryState(api, FakeSession())
        loaded, _failed = run(state)
        self.assertEqual(len(loaded), 1)
        self.assertEqual([s.id for s in state.songs], list(range(10, 0, -1)))
        self.assertTrue(all(state.is_liked(s.id) for s in state.songs))
        self.assertIsNone(state.is_liked(99999))
        self.assertEqual(state.playlist.id, STAR_ID)
        self.assertEqual(state.session.star_playlist_id, STAR_ID)

    def test_long_list_batches_details(self) -> None:
        """超过一批（300）时，song_detail 要分批补全。"""
        ids = list(range(1, DETAIL_BATCH * 2 + 5))
        api = FakeApi(track_ids=ids)
        state = LibraryState(api, FakeSession())
        run(state)
        self.assertEqual(len(api.detail_calls), 3)  # 300 + 300 + 5
        self.assertEqual([s.id for s in state.songs], ids)

    def test_batches_detail_over_300(self) -> None:
        ids = list(range(1, DETAIL_BATCH * 2 + 5))
        api = FakeApi(track_ids=ids)
        state = LibraryState(api, FakeSession())
        run(state)
        self.assertEqual(len(api.detail_calls), 3)  # 300 + 300 + 5
        self.assertEqual(len(state.songs), len(ids))

    def test_uses_first_page_tracks_without_refetch(self) -> None:
        """接口首批自带 2 首曲目，不应重复请求。"""
        api = FakeApi(track_ids=[1, 2, 3, 4, 5])
        state = LibraryState(api, FakeSession())
        run(state)
        self.assertEqual(len(api.detail_calls), 1)
        self.assertEqual(api.detail_calls[0], [3, 4, 5])

    def test_requests_playlist_once(self) -> None:
        """回归：该接口忽略 s 偏移，若按 offset 分页会拿到同一份全量 → 死循环空转。"""
        ids = list(range(1, TRACK_PAGE * 2 + 7))  # 比一页还多
        api = FakeApi(track_ids=ids)
        state = LibraryState(api, FakeSession())
        run(state)
        self.assertEqual(len(api.page_calls), 1)
        self.assertEqual([s.id for s in state.songs], ids)

    def test_dedupes_track_ids(self) -> None:
        """trackIds 里有重复时，列表里同一首歌只出现一次。"""
        ids = [1, 2, 2, 3, 1]
        api = FakeApi(track_ids=ids)
        state = LibraryState(api, FakeSession())
        run(state)
        self.assertEqual([s.id for s in state.songs], [1, 2, 3])

    def test_failure_reports_message(self) -> None:
        state = LibraryState(FakeApi(fail="网络不通"), FakeSession())
        _loaded, errors = run(state)
        self.assertEqual(errors, ["网络不通"])
        self.assertEqual(state.songs, [])
        self.assertFalse(state.loading)


class TestLibraryCache(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = redirect_config()

    def _seed(self, ids: list[int]) -> None:
        api = FakeApi(track_ids=ids)
        state = LibraryState(api, FakeSession())
        run(state)

    def test_second_load_uses_cache(self) -> None:
        ids = list(range(1, 21))
        self._seed(ids)
        api2 = FakeApi(track_ids=ids, fail="不该再请求网络")
        state2 = LibraryState(api2, FakeSession())
        loaded, _failed = run(state2)
        self.assertEqual(len(loaded), 1)
        self.assertEqual([s.id for s in state2.songs], ids)
        self.assertEqual(api2.detail_calls, [])  # 缓存命中不再请求

    def test_network_failure_falls_back_to_cache(self) -> None:
        ids = list(range(1, 11))
        self._seed(ids)
        api = FakeApi(track_ids=ids, fail="网络断了")
        state = LibraryState(api, FakeSession())
        _loaded, failed = run(state, force=True)  # 强制走网络 → 失败 → 回退缓存
        self.assertEqual(failed, ["网络断了"])
        self.assertEqual([s.id for s in state.songs], ids)

    def test_expired_cache_ignored(self) -> None:
        ids = list(range(1, 11))
        self._seed(ids)
        path = app_config.PLAYLIST_CACHE_DIR / f"star_1234.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["saved_at"] = time.time() - 10 * 24 * 3600  # 10 天前
        path.write_text(json.dumps(raw), encoding="utf-8")
        api = FakeApi(track_ids=ids, fail="不该用过期缓存")
        state = LibraryState(api, FakeSession())
        _loaded, failed = run(state)
        self.assertEqual(failed, ["不该用过期缓存"])

    def test_corrupt_cache_tolerated(self) -> None:
        app_config.PLAYLIST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (app_config.PLAYLIST_CACHE_DIR / "star_1234.json").write_text("{坏了", encoding="utf-8")
        api = FakeApi(track_ids=[1, 2, 3])
        state = LibraryState(api, FakeSession())
        loaded, _failed = run(state)
        self.assertEqual(len(loaded), 1)

    def test_clear_cache(self) -> None:
        self._seed([1, 2, 3])
        state = LibraryState(FakeApi(), FakeSession())
        state.clear_cache()
        self.assertFalse((app_config.PLAYLIST_CACHE_DIR / "star_1234.json").exists())


class TestLibraryLike(unittest.TestCase):
    def setUp(self) -> None:
        redirect_config()

    def test_unlike_removes_from_list(self) -> None:
        api = FakeApi(track_ids=[1, 2, 3])
        state = LibraryState(api, FakeSession())
        run(state)
        changes: list[tuple] = []
        state.like_changed.connect(lambda sid, liked: changes.append((sid, liked)))
        state.set_liked(2, False)
        self.assertEqual([s.id for s in state.songs], [1, 3])
        self.assertEqual(changes, [(2, False)])
        self.assertFalse(state.is_liked(2))

    def test_like_adds_state(self) -> None:
        api = FakeApi(track_ids=[1, 2])
        state = LibraryState(api, FakeSession())
        run(state)
        state.set_liked(99, True)
        self.assertTrue(state.is_liked(99))


if __name__ == "__main__":
    unittest.main()
