"""端点参数构造与数据解析测试（不联网）。

对应验收：AC-2.1~2.5、AC-4.7 的参数面；以及 §8 E8（``encodeType`` 必填）。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nemusic.model.types import (  # noqa: E402
    Account,
    Album,
    Artist,
    Playlist,
    QrStatus,
    SearchPage,
    Song,
    SongUrl,
    format_duration,
)
from nemusic.net import endpoints as ep  # noqa: E402
from nemusic.net.api import Api  # noqa: E402


class FakeSession:
    """记录调用参数的假会话。"""

    def __init__(self, responses: dict[str, dict] | None = None) -> None:
        self.calls: list[dict] = []
        self.responses = responses or {}
        self.logged_in = True
        self.saved = 0

    def request(self, endpoint, data=None, *, params=None, path=None, need_login=None, allow_eapi_retry=True, ok_codes=None):
        self.calls.append(
            {
                "path": path or endpoint.path,
                "endpoint": endpoint,
                "data": dict(data or {}),
                "params": dict(params or {}),
                "method": endpoint.method,
                "ok_codes": ok_codes,
            }
        )
        key = path or endpoint.path
        for k, v in self.responses.items():
            if k in key:
                return v
        return {"code": 200}

    def save_cookies(self) -> None:
        self.saved += 1

    def set_cookie(self, name: str, value: str) -> None:  # 供 qr_poll 的 cookie 分支
        self.cookies = getattr(self, "cookies", {})
        self.cookies[name] = value


class TestSearchParams(unittest.TestCase):
    def test_song_search(self) -> None:
        session = FakeSession({"search/get/web": {"code": 200, "result": {"songs": [], "songCount": 0}}})
        Api(session).search("周杰伦", ep.SEARCH_TYPE_SONG, offset=30, limit=30)
        call = session.calls[0]
        self.assertEqual(call["path"], "/api/search/get/web")
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["data"]["s"], "周杰伦")
        self.assertEqual(call["data"]["type"], 1)
        self.assertEqual(call["data"]["offset"], 30)
        self.assertEqual(call["data"]["limit"], 30)

    def test_all_four_types(self) -> None:
        for search_type in (1, 10, 100, 1000):
            session = FakeSession()
            Api(session).search("x", search_type)
            self.assertEqual(session.calls[0]["data"]["type"], search_type)

    def test_unsupported_type(self) -> None:
        with self.assertRaises(ValueError):
            Api(FakeSession()).search("x", 999)

    def test_results_parsed_per_type(self) -> None:
        song_payload = {
            "code": 200,
            "result": {
                "songs": [{"id": 1, "name": "晴天", "ar": [{"id": 2, "name": "周杰伦"}], "al": {"id": 3, "name": "叶惠美"}, "dt": 269000}],
                "songCount": 274,
            },
        }
        page = Api(FakeSession({"search": song_payload})).search("晴天")
        self.assertIsInstance(page.items[0], Song)
        self.assertEqual(page.items[0].artist_text, "周杰伦")
        self.assertEqual(page.total, 274)
        self.assertTrue(page.has_more)

        artist_payload = {"code": 200, "result": {"artists": [{"id": 6452, "name": "周杰伦", "albumSize": 30}], "artistCount": 82}}
        page = Api(FakeSession({"search": artist_payload})).search("周杰伦", ep.SEARCH_TYPE_ARTIST)
        self.assertIsInstance(page.items[0], Artist)
        self.assertEqual(page.items[0].album_size, 30)

        album_payload = {"code": 200, "result": {"albums": [{"id": 18909, "name": "Partners", "artist": {"id": 1, "name": "A"}, "size": 10}], "albumCount": 592}}
        page = Api(FakeSession({"search": album_payload})).search("x", ep.SEARCH_TYPE_ALBUM)
        self.assertIsInstance(page.items[0], Album)

        pl_payload = {"code": 200, "result": {"playlists": [{"id": 6792103822, "name": "精选", "trackCount": 50}], "playlistCount": 464}}
        page = Api(FakeSession({"search": pl_payload})).search("x", ep.SEARCH_TYPE_PLAYLIST)
        self.assertIsInstance(page.items[0], Playlist)
        self.assertEqual(page.items[0].track_count, 50)


class TestOtherEndpoints(unittest.TestCase):
    def test_artist_top_song(self) -> None:
        session = FakeSession({"artist/top/song": {"code": 200, "songs": [{"id": 9, "name": "s"}]}})
        songs = Api(session).artist_top_songs(6452)
        self.assertEqual(session.calls[0]["method"], "GET")
        self.assertEqual(session.calls[0]["params"]["id"], 6452)
        self.assertEqual(len(songs), 1)

    def test_album_path_format(self) -> None:
        session = FakeSession({"v1/album/32311": {"code": 200, "album": {"id": 32311, "name": "神的游戏"}, "songs": [{"id": 1, "name": "a"}]}})
        album, songs = Api(session).album_songs(32311)
        self.assertEqual(session.calls[0]["path"], "/api/v1/album/32311")
        self.assertEqual(album.name, "神的游戏")
        self.assertEqual(len(songs), 1)

    def test_playlist_detail_params(self) -> None:
        session = FakeSession({"v6/playlist/detail": {"code": 200, "playlist": {"id": 1, "name": "p", "trackIds": [{"id": 11}, {"id": 22}], "trackCount": 2}}})
        pl = Api(session).playlist_detail(1, limit=1000, offset=0)
        self.assertEqual(session.calls[0]["data"], {"id": 1, "n": 1000, "s": 0, "newStyle": "true"})
        self.assertEqual(pl.track_ids, [11, 22])

    def test_like_true_false(self) -> None:
        for liked, expected in ((True, "true"), (False, "false")):
            session = FakeSession()
            Api(session).like(123, liked, user_id=456)
            data = session.calls[0]["data"]
            self.assertEqual(data["trackId"], 123)
            self.assertEqual(data["userid"], 456)
            self.assertEqual(data["like"], expected)

    def test_song_url_must_include_encode_type(self) -> None:
        session = FakeSession({"player/url/v1": {"code": 200, "data": [{"id": 5, "url": "http://x/1.mp3", "br": 128000, "size": 100, "type": "mp3", "level": "standard", "code": 200}]}})
        result = Api(session).song_url(5, "exhigh")
        data = session.calls[0]["data"]
        self.assertEqual(data["ids"], "[5]")
        self.assertEqual(data["level"], "exhigh")
        self.assertIn("encodeType", data)  # SPEC §8 E8：缺失必 400
        self.assertTrue(result.playable)
        self.assertEqual(result.type, "mp3")

    def test_song_url_ladder_falls_back(self) -> None:
        """exhigh 无地址 → 降级到 standard。"""
        session = FakeSession(
            {
                "player/url/v1": {"code": 200, "data": [{"id": 5, "url": None, "code": 404, "level": "exhigh"}]},
            }
        )
        api = Api(session)
        calls = []

        def fake_url(song_id, level):
            calls.append(level)
            if level == "standard":
                return SongUrl(song_id=song_id, url="http://x/a.mp3", level=level, code=200)
            return SongUrl(song_id=song_id, url=None, level=level, code=404)

        api.song_url = fake_url  # type: ignore[method-assign]
        result = api.song_url_best(5)
        self.assertEqual(calls, ["exhigh", "higher", "standard"])
        self.assertTrue(result.playable)

    def test_song_detail_ids_json(self) -> None:
        session = FakeSession({"v3/song/detail": {"code": 200, "songs": [{"id": 7, "name": "n", "ar": [], "al": {}}]}})
        songs = Api(session).song_detail([7, 8])
        self.assertEqual(json.loads(session.calls[0]["data"]["ids"]), [{"id": 7}, {"id": 8}])
        self.assertEqual(len(songs), 1)

    def test_song_detail_c_must_equal_ids(self) -> None:
        """回归：``c`` 必须和 ``ids`` 同一份 JSON。

        传 ``"[]"`` 时服务端不报错，只返回 ``{"songs": [], "code": 200}``——
        真机上表现为"我喜欢的音乐"永远加载不出歌（见 acceptance.md）。
        """
        session = FakeSession({"v3/song/detail": {"code": 200, "songs": [{"id": 7, "name": "n", "ar": [], "al": {}}]}})
        Api(session).song_detail([7, 8])
        data = session.calls[0]["data"]
        self.assertEqual(data["c"], data["ids"])
        self.assertNotEqual(data["c"], "[]")

    def test_song_detail_empty_ids_skips_request(self) -> None:
        session = FakeSession({"v3/song/detail": {"code": 200, "songs": []}})
        self.assertEqual(Api(session).song_detail([]), [])
        self.assertEqual(session.calls, [])

    def test_lyric_params(self) -> None:
        session = FakeSession({"song/lyric": {"code": 200, "lrc": {"lyric": "[00:00]a"}}})
        Api(session).lyric(42)
        params = session.calls[0]["params"]
        self.assertEqual(params["id"], 42)
        self.assertEqual((params["lv"], params["kv"], params["tv"]), (-1, -1, -1))

    def test_star_playlist_detection(self) -> None:
        payload = {
            "code": 200,
            "playlist": [
                {"id": 111, "name": "别的", "specialType": 0},
                {"id": 222, "name": "我喜欢的音乐", "specialType": 5},
            ],
        }
        star = Api(FakeSession({"user/playlist": payload})).star_playlist(1)
        self.assertIsNotNone(star)
        self.assertEqual(star.id, 222)
        self.assertTrue(star.is_star_playlist)


class TestQrFlow(unittest.TestCase):
    def test_unikey_and_content(self) -> None:
        session = FakeSession({"qrcode/unikey": {"code": 200, "unikey": "abc-123"}})
        key = Api(session).qr_unikey()
        self.assertEqual(key, "abc-123")
        self.assertEqual(session.calls[0]["data"], {"type": 1})
        self.assertEqual(
            Api.qr_content(key), "https://music.163.com/login?codekey=abc-123"
        )

    def test_unikey_missing_raises(self) -> None:
        with self.assertRaises(Exception):
            Api(FakeSession({"qrcode/unikey": {"code": 200}})).qr_unikey()

    def test_poll_status_mapping(self) -> None:
        for code, waiting, scanned, expired, ok in (
            (801, True, False, False, False),
            (802, False, True, False, False),
            (800, False, False, True, False),
            (803, False, False, False, True),
        ):
            session = FakeSession({"qrcode/client/login": {"code": code, "nickname": "n", "avatarUrl": "a"}})
            status = Api(session).qr_poll("k")
            self.assertEqual((status.waiting, status.scanned, status.expired, status.ok), (waiting, scanned, expired, ok))
            self.assertEqual(session.calls[0]["params"], {"key": "k", "type": 1})
            # 回归：800/801/802/803 必须作为“状态”放行，不能被当成业务错误
            self.assertEqual(session.calls[0]["ok_codes"], {800, 801, 802, 803})

    def test_poll_success_sets_cookie(self) -> None:
        session = FakeSession({"qrcode/client/login": {"code": 803, "cookie": "MUSIC_U=token123; __csrf=abc"}})
        for _ in range(60):  # 轮询上限（真实场景由 UI 计时器控制）
            status = Api(session).qr_poll("k")
            if status.ok:
                break
        self.assertTrue(status.ok)
        self.assertEqual(session.cookies["MUSIC_U"], "token123")
        self.assertEqual(session.cookies["__csrf"], "abc")


class TestTypeHelpers(unittest.TestCase):
    def test_format_duration(self) -> None:
        self.assertEqual(format_duration(0), "00:00")
        self.assertEqual(format_duration(269000), "04:29")
        self.assertEqual(format_duration(3_600_000), "1:00:00")

    def test_song_from_legacy_fields(self) -> None:
        song = Song.from_api({"id": 1, "name": "n", "artists": [{"id": 2, "name": "a"}], "album": {"id": 3, "name": "b"}, "duration": 1000})
        self.assertEqual(song.artist_names, ("a",))
        self.assertEqual(song.album, "b")
        self.assertEqual(song.duration_ms, 1000)

    def test_account_and_status(self) -> None:
        acc = Account.from_api({"profile": {"userId": 9, "nickname": "x", "avatarUrl": "u", "vipType": 11}})
        self.assertEqual((acc.uid, acc.nickname, acc.vip_type), (9, "x", 11))
        self.assertTrue(QrStatus(code=801).waiting)
        self.assertFalse(SearchPage("k", 1).has_more)


if __name__ == "__main__":
    unittest.main()
