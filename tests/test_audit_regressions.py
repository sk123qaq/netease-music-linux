"""2026-10 审计回归用例：一个类对应一条缺陷，全部先失败、修好后通过。

覆盖的缺陷（详见 PR 描述）：
- 搜索页的信号接线落在 hideEvent 里 → 未 hide 过就没接线，hide 几次就重复连几次
- 登出把 liked/_liked 从 dict 换成 set() → 收藏回包抛 TypeError
- like_checker / 行级 ♥ 没有任何数据来源 → 已收藏的歌显示成未收藏
- 不可播歌曲的失败兜底会自转（单曲队列 + 列表循环）
- 纯音乐占位判据用 all() → 带制作名单的纯音乐被当歌词滚
- 详情页回包没有请求号 → 迟到回包抢页面并开始播放
- 搜索用全局 generation → 同关键词下别的 tab 把有效回包挤掉
- playlist_detail 的 privileges 取错层级 → 恒为 None
- AES 启动自检用 assert → -O 下静默失效
- 5xx 从不重试；eapi 重试线索里三个词永远无效
- 无时间戳的制作信息行被整行丢弃
- 搜索清空时发出错误的 tab 号（脚手架残留）
- check_agents_map 只认裸路径，带注解的行静默跳过
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import pump_until, qt_app, redirect_config  # noqa: E402

from nemusic.model.types import Playlist, SearchPage, Song, SongUrl  # noqa: E402
from nemusic.net import endpoints as ep  # noqa: E402
from nemusic.state.library import LibraryState  # noqa: E402
from nemusic.state.search_state import SearchState  # noqa: E402
from nemusic.ui.search_view import SearchView  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class _FailApi:
    """只用来构造 SearchState；用例自己往 state 里塞数据，不走网络。"""

    def search(self, *_args, **_kwargs):
        raise RuntimeError("本用例不应触发真实搜索")


def _window():
    from nemusic.app import build_app

    app, window, _config = build_app([sys.argv[0]])
    return app, window


class TestSearchPageWiring(unittest.TestCase):
    """搜索页的表格/tab 接线必须在构造时就存在，且不能随导航次数叠加。"""

    def setUp(self) -> None:
        redirect_config()
        self.app = qt_app()
        self.state = SearchState(_FailApi())  # type: ignore[arg-type]
        self.view = SearchView(self.state)
        self.view.resize(960, 600)
        self.view.show()
        self.app.processEvents()
        self.plays: list[int] = []
        self.view.play_requested.connect(lambda items, index: self.plays.append(index))
        self._feed([Song(id=1, name="a"), Song(id=2, name="b")])

    def _feed(self, items: list) -> None:
        tab = self.state.tab(ep.SEARCH_TYPE_SONG)
        tab.items = list(items)
        tab.total = len(items)
        tab.finished = True
        self.view.current_type = ep.SEARCH_TYPE_SONG
        self.view._refresh_tables()
        self.app.processEvents()

    def _double_click_first_row(self) -> int:
        self.plays.clear()
        self.view._tables[ep.SEARCH_TYPE_SONG].cellDoubleClicked.emit(0, 0)
        self.app.processEvents()
        return len(self.plays)

    def test_double_click_plays_once_before_any_hide(self) -> None:
        """没被隐藏过也要能用（接线在构造里，不在 hideEvent 里）。"""
        self.assertEqual(self._double_click_first_row(), 1)

    def test_double_click_plays_once_after_navigating_away_and_back(self) -> None:
        """导航离开再回来不能把同一条信号连第二遍。"""
        for _ in range(3):
            self.view.hide()
            self.app.processEvents()
            self.view.show()
            self.app.processEvents()
        self.assertEqual(self._double_click_first_row(), 1)


class _FakeSignal:
    def __init__(self) -> None:
        self.slots: list = []

    def connect(self, slot) -> None:
        self.slots.append(slot)

    def emit(self, *args) -> None:
        for slot in list(self.slots):
            slot(*args)


class _FakePlayer:
    """不需要真 mpv（同 tests/test_playback_cache.py 的做法）。"""

    def __init__(self) -> None:
        for name in ("position_changed", "duration_changed", "paused_changed", "ended", "failed", "ready"):
            setattr(self, name, _FakeSignal())

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def set_volume(self, _volume) -> None:
        pass

    def set_mute(self, _muted) -> None:
        pass

    def shutdown(self) -> None:
        pass


class _FakeSessionState:
    logged_in = True
    uid = 42


def _controller(*, url_result: SongUrl | None = None):
    """离线播放控制器：取流结果由调用方给定。"""
    from nemusic.config import Config
    from nemusic.player.controller import PlaybackController

    class _Api:
        def like(self, *_args, **_kwargs) -> None:
            return None

        def song_url_best(self, song_id, ladder=None) -> SongUrl:
            return url_result or SongUrl(song_id=song_id, playable=False, code=460, message="无版权")

    ctl = PlaybackController(  # type: ignore[arg-type]
        _Api(), Config.load(), _NoCache(), _FakeSessionState(), player=_FakePlayer()
    )
    return ctl


class _NoCache:
    def audio_lookup(self, _song_id):
        return None

    def audio_part_path(self, song_id, level, ext):
        return Path(f"/tmp/nemusic-no-cache-{song_id}-{level}.{ext}")

    def discard(self, _part) -> None:
        pass

    def commit_audio(self, *_args, **_kwargs):
        return None

    def sweep_parts(self, keep=None) -> int:
        return 0


class TestLikeStateAfterLogout(unittest.TestCase):
    """登出清状态可以重置成空，但不能把 dict 换成 set（类型不一致会抛异常）。"""

    def setUp(self) -> None:
        redirect_config()
        self.app = qt_app()

    def test_library_clear_leaves_liked_as_mapping(self) -> None:
        library = LibraryState(api=None, session=None)  # type: ignore[arg-type]
        library.clear()
        library.set_liked(5, False)
        library.set_liked(7, True)
        self.assertFalse(library.is_liked(5))
        self.assertTrue(library.is_liked(7))

    def test_like_result_landing_after_logout_still_updates_state(self) -> None:
        """点红心（请求在途）后立刻退出登录：回包必须落地，不能抛异常。"""
        ctl = _controller()
        self.addCleanup(ctl.shutdown)
        events: list[tuple] = []
        ctl.like_changed.connect(lambda song_id, liked: events.append((song_id, liked)))

        ctl.set_like(Song(id=5, name="x"), True)
        ctl.stop()  # 用户立刻退出登录
        self.assertTrue(pump_until(lambda: bool(events), 5.0), "收藏回包没有落地（回调里抛异常了）")
        self.assertEqual(events, [(5, True)])
        self.assertTrue(ctl.is_liked(5))


class TestUnplayableSongDoesNotSpin(unittest.TestCase):
    """所有候选都不可播时必须停下来，不能原地打转。"""

    def setUp(self) -> None:
        redirect_config()
        self.app = qt_app()

    def _run(self, songs: list[Song], seconds: float = 0.4) -> dict:
        ctl = _controller()
        self.addCleanup(ctl.shutdown)
        counts = {"changed": 0, "notices": 0}
        ctl.song_changed.connect(lambda _song: counts.__setitem__("changed", counts["changed"] + 1))
        ctl.notice.connect(lambda _msg: counts.__setitem__("notices", counts["notices"] + 1))
        ctl.play_songs(songs, 0, context="用例")
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        counts["playing"] = ctl.playing
        return counts

    def test_single_unplayable_song_stops(self) -> None:
        counts = self._run([Song(id=5, name="a")])
        self.assertFalse(counts["playing"], "不可播时不该还停在播放中")
        self.assertLessEqual(counts["changed"], 1, "同一首歌被反复重播")
        self.assertGreaterEqual(counts["notices"], 1, "停下来要给出提示，不能静默")

    def test_all_unplayable_songs_are_tried_once_then_stop(self) -> None:
        counts = self._run([Song(id=1, name="a"), Song(id=2, name="b"), Song(id=3, name="c")])
        self.assertFalse(counts["playing"])
        self.assertLessEqual(counts["changed"], 3, "跑完一轮就该停，不能循环重来")


class TestLikedStateInUi(unittest.TestCase):
    """已经收藏的歌，播放条与列表里的 ♥ 必须是实心（数据源 library.is_liked）。"""

    def setUp(self) -> None:
        redirect_config()
        self.app = qt_app()
        self.song = Song(id=5, name="已收藏的歌", artist_names=("A",))

    def _window_with_liked_song(self):
        _app, window = _window()
        self.addCleanup(window.controller.shutdown)
        window.library._apply([self.song], Playlist(id=1, name="我喜欢的音乐", track_count=1))
        return window

    def test_player_bar_shows_liked(self) -> None:
        window = self._window_with_liked_song()
        window.controller.play_songs([self.song], 0, context="用例")
        self.app.processEvents()
        self.assertEqual(window.player_bar.btn_like.toolTip(), "取消收藏")

    def test_search_row_shows_liked(self) -> None:
        window = self._window_with_liked_song()
        tab = window.search_state.tab(ep.SEARCH_TYPE_SONG)
        tab.items = [self.song]
        window.search_view.current_type = ep.SEARCH_TYPE_SONG
        window.search_view._refresh_tables()
        self.app.processEvents()
        button = window.search_view._like_buttons[ep.SEARCH_TYPE_SONG][self.song.id]
        self.assertEqual(button.toolTip(), "取消收藏")

    def test_entity_row_shows_liked(self) -> None:
        window = self._window_with_liked_song()
        window.entity_view.set_entity("album", 1, "专辑", "", [self.song])
        self.app.processEvents()
        self.assertEqual(window.entity_view.table._buttons[self.song.id].toolTip(), "取消收藏")


class TestPlaceholderDetection(unittest.TestCase):
    """纯音乐：占位行 + 曲尾制作名单，不能当滚动歌词。"""

    def test_pure_music_with_credit_tail_is_no_lyric(self) -> None:
        from nemusic.model.lrc import parse_payload

        payload = {
            "lrc": {"lyric": "[00:05.00]纯音乐，请欣赏\n[00:10.00]钢琴 : 邓垚\n[00:11.00]出品：网易云音乐 x 云上工作室"}
        }
        self.assertEqual(parse_payload(2038191895, payload).lines, [])

    def test_hint_word_inside_real_lyrics_is_kept(self) -> None:
        """提示词出现在正文中（不是首行）时不能整篇当无歌词。"""
        from nemusic.model.lrc import parse_payload

        payload = {"lrc": {"lyric": "[00:31.42]又站在你家的门口\n[00:34.57]请欣赏这阵雨"}}
        self.assertEqual(
            [line.text for line in parse_payload(1, payload).lines], ["又站在你家的门口", "请欣赏这阵雨"]
        )


class TestEntityRequestRace(unittest.TestCase):
    """详情页回包要认请求号：迟到的回包既不能抢页面，也不能开始播放。"""

    def setUp(self) -> None:
        redirect_config()
        self.app = qt_app()
        _app, self.window = _window()
        self.addCleanup(self.window.controller.shutdown)

    def _stub_album(self, delays: dict[int, float]) -> None:
        from nemusic.model.types import Album

        def album_songs(album_id: int):
            time.sleep(delays.get(album_id, 0.0))
            return Album(id=album_id, name=f"专辑{album_id}"), [Song(id=album_id * 100, name=f"曲{album_id}")]

        self.window.api.album_songs = album_songs

    def test_late_response_after_navigating_away_is_dropped(self) -> None:
        self._stub_album({7: 0.35})
        self.window._open_entity("album", 7)
        self.window.show_page("settings")
        time.sleep(1.0)  # 等慢请求回来
        self.app.processEvents()
        self.assertEqual(self.window.stack.currentIndex(), self.window.pages["settings"], "被迟到的回包拽走了")
        self.assertIsNone(self.window.controller.current_song, "不该开始播放")

    def test_last_clicked_entity_wins(self) -> None:
        self._stub_album({7: 0.45, 8: 0.0})
        self.window._open_entity("album", 7)  # 慢
        time.sleep(0.05)
        self.window._open_entity("album", 8)  # 后点、先回
        pump_until(lambda: self.window.entity_view.entity_id == 8, 3.0)
        time.sleep(0.6)
        self.app.processEvents()
        self.assertEqual(self.window.entity_view.entity_id, 8, "先点的慢回包覆盖了后点的结果")
        self.assertEqual([s.id for s in self.window.entity_view.songs()], [800])


class TestSearchTabGeneration(unittest.TestCase):
    """每个 tab 各自持请求号：别的 tab 发请求不能把自己的有效回包挤掉。"""

    def setUp(self) -> None:
        redirect_config()

    def _state(self) -> SearchState:
        return SearchState(_FailApi())  # type: ignore[arg-type]

    @staticmethod
    def _generation(state: SearchState, search_type: int) -> int:
        return state.tab(search_type).generation

    @staticmethod
    def _page(keyword: str, search_type: int, songs: list[Song]) -> SearchPage:
        return SearchPage(keyword=keyword, search_type=search_type, offset=0, limit=30, total=len(songs), items=songs)

    def test_song_page_survives_artist_search(self) -> None:
        state = self._state()
        state.search("周杰伦", ep.SEARCH_TYPE_SONG)
        song_generation = self._generation(state, ep.SEARCH_TYPE_SONG)
        state.search("周杰伦", ep.SEARCH_TYPE_ARTIST)  # 同关键词换 tab 再搜一次
        state._on_page(
            ("周杰伦", ep.SEARCH_TYPE_SONG, self._page("周杰伦", ep.SEARCH_TYPE_SONG, [Song(id=1, name="龙卷风")]), None),
            song_generation,
        )
        self.assertEqual([s.id for s in state.tab(ep.SEARCH_TYPE_SONG).items], [1], "单曲 tab 的有效回包被丢弃了")

    def test_page_from_previous_keyword_is_discarded(self) -> None:
        state = self._state()
        state.search("旧关键词", ep.SEARCH_TYPE_SONG)
        stale_generation = self._generation(state, ep.SEARCH_TYPE_SONG)
        state.search("新关键词", ep.SEARCH_TYPE_SONG)
        state._on_page(
            ("旧关键词", ep.SEARCH_TYPE_SONG, self._page("旧关键词", ep.SEARCH_TYPE_SONG, [Song(id=99, name="迟到")]), None),
            stale_generation,
        )
        self.assertEqual(state.tab(ep.SEARCH_TYPE_SONG).items, [], "换关键词后旧回包必须丢弃")


class TestPlaylistPrivileges(unittest.TestCase):
    """playlist/detail 的 privileges 是 playlist 的兄弟键，解析不能取错层级。"""

    def test_tracks_carry_privilege(self) -> None:
        from nemusic.net.api import Api

        class _Session:
            def request(self, _endpoint, _data=None, **_kwargs):
                return {
                    "playlist": {"id": 1, "name": "p", "trackCount": 1, "tracks": [{"id": 2, "name": "s"}]},
                    "privileges": [{"id": 2, "fee": 0, "pl": 320000}],
                }

        playlist = Api(session=_Session()).playlist_detail(1)  # type: ignore[arg-type]
        self.assertIsNotNone(playlist.tracks[0].privilege, "privileges 被丢在顶层了")


class TestCryptoStartupCheck(unittest.TestCase):
    """AES 自检必须在 -O 下依然拦得住（assert 会被 -O 删掉）。"""

    def test_selftest_raises_with_broken_aes_even_optimized(self) -> None:
        code = (
            "import sys; sys.path.insert(0, 'src')\n"
            "from nemusic.net import crypto\n"
            "crypto.SBOX = bytes((b + 1) % 256 for b in crypto.SBOX)\n"
            "try:\n"
            "    crypto.selftest()\n"
            "except AssertionError:\n"
            "    print('RAISED'); raise SystemExit(0)\n"
            "print('SELFTEST_PASSED_WITH_BROKEN_AES')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-O", "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60
        )
        self.assertNotIn("SELFTEST_PASSED_WITH_BROKEN_AES", proc.stdout, "S 盒已损坏，自检却通过了")
        self.assertIn("RAISED", proc.stdout)


class TestSessionRetry(unittest.TestCase):
    """eapi 线索词都要生效；5xx 要真的重试。"""

    def setUp(self) -> None:
        redirect_config()
        from nemusic.net.session import Session

        self.session = Session()

    def test_eapi_hint_words_trigger_retry(self) -> None:
        for text in ('{"msg":"需要加密"}', '{"msg":"Illegal request"}', '{"msg":"encrypt required"}'):
            self.assertTrue(self.session._should_retry_eapi(text, 200), text)

    def test_server_error_is_retried(self) -> None:
        class _Resp:
            def __init__(self, status: int, text: str) -> None:
                self.status_code, self.text = status, text

        class _Requests:
            def __init__(self) -> None:
                self.calls = 0
                self.cookies: list = []

            def request(self, *_args, **_kwargs):
                self.calls += 1
                return _Resp(503, "busy") if self.calls == 1 else _Resp(200, '{"code":200}')

        fake = _Requests()
        self.session._requests = fake  # type: ignore[assignment]
        with contextlib.suppress(Exception):
            self.session._send("GET", "/api/x", {}, {}, "用例")
        self.assertEqual(fake.calls, 2, "5xx 应该重试一次")
        self.assertEqual(self.session._send("GET", "/api/x", {}, {}, "用例")[0], '{"code":200}')


class TestUntimedCreditLines(unittest.TestCase):
    """真机上存在整篇不带时间戳的制作信息行，不能连歌名一起丢。"""

    def test_untimed_credit_line_lands_in_credits(self) -> None:
        from nemusic.model.lrc import parse_lrc

        lines, credits = parse_lrc("作词 : XX_xxdcds")
        self.assertEqual(lines, [])
        self.assertTrue(any("XX_xxdcds" in c for c in credits), f"credits={credits}")


class TestSearchClearSignal(unittest.TestCase):
    """清空某个 tab 时，信号必须带正确的 tab 号。"""

    def setUp(self) -> None:
        redirect_config()

    def test_clear_reports_its_own_tab(self) -> None:
        state = SearchState(_FailApi())  # type: ignore[arg-type]
        events: list[tuple] = []
        state.loading_changed.connect(lambda search_type, loading, finished: events.append((search_type, loading, finished)))
        state.clear(ep.SEARCH_TYPE_ARTIST)
        self.assertEqual(events, [(ep.SEARCH_TYPE_ARTIST, False, False)])


class TestAgentsMapScript(unittest.TestCase):
    """文件地图校验：首列带注解（如「`x.py` 依赖」）的行同样要检查。"""

    def _load_script(self):
        spec = importlib.util.spec_from_file_location("check_agents_map_under_test", ROOT / "scripts/check_agents_map.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_annotated_row_is_checked(self) -> None:
        import tempfile

        module = self._load_script()
        with tempfile.TemporaryDirectory(prefix="nemusic-map-") as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text(
                "| 路径 | 职责 | 状态 |\n"
                "|---|---|---|\n"
                "| `present.py` | 存在 | ✅ 已实现 |\n"
                "| `missing.py` 依赖 | 不存在 | ✅ 已实现 |\n",
                encoding="utf-8",
            )
            (root / "present.py").write_text("", encoding="utf-8")
            module.ROOT = root
            module.AGENTS = root / "AGENTS.md"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = module.main()
        text = out.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("missing.py", text, f"带注解的行没有被检查：{text}")


if __name__ == "__main__":
    unittest.main()
