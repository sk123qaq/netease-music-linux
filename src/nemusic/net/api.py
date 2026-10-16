"""业务 API：把端点 + 参数 + 解析收在一处（UI/状态层只调用这里）。

每个方法都是**阻塞调用**，必须在工作线程中执行（见 `util/async_task.py`）。
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from ..config import QUALITY_LADDER
from ..model.types import (
    Account,
    Album,
    Artist,
    Playlist,
    QrStatus,
    SearchPage,
    Song,
    SongUrl,
)
from ..util import log
from . import endpoints as ep
from .session import ApiError, NeedLoginError, Session, default_session

_log = log.get("net.api")


class Api:
    """网易云接口客户端。"""

    def __init__(self, session: Session | None = None) -> None:
        self.session = session or default_session()

    # ============================================================ 登录

    def qr_unikey(self) -> str:
        """申请二维码 key（✅ 实测：无需登录）。"""
        payload = self.session.request(ep.QR_UNIKEY, {"type": 1})
        key = str(payload.get("unikey") or "")
        if not key:
            raise ApiError(int(payload.get("code") or -1), "未获取到二维码 key", str(ep.QR_UNIKEY), raw=payload)
        return key

    @staticmethod
    def qr_content(key: str) -> str:
        """二维码内容（手机扫的 URL）。"""
        return ep.QR_LOGIN_URL_TEMPLATE.format(key=key)

    def qr_poll(self, key: str) -> QrStatus:
        """轮询扫码状态（800 过期 / 801 待扫 / 802 待确认 / 803 成功）。

        803 时服务端会下发登录 cookie；部分实现把 cookie 放在响应体 ``cookie`` 字段，
        这里两种都处理（见 SPEC §4 AC-1.2）。

        注意：800/801/802/803 是**状态**而非错误，必须用 ``ok_codes`` 放行，
        否则会被当成业务错误抛出（本实现初版就踩过这个坑）。
        """
        payload = self.session.request(
            ep.QR_POLL,
            params={"key": key, "type": 1},
            need_login=False,
            allow_eapi_retry=False,
            ok_codes={800, 801, 802, 803},
        )
        code = int(payload.get("code") or 0)
        status = QrStatus(
            code=code,
            message=ep.QR_STATUS.get(code, str(payload.get("message") or "")),
            nickname=str(payload.get("nickname") or ""),
            avatar=str(payload.get("avatarUrl") or ""),
        )
        if status.ok:
            cookie_text = str(payload.get("cookie") or "")
            for item in cookie_text.split(";"):
                if "=" in item:
                    name, _, value = item.partition("=")
                    name, value = name.strip(), value.strip()
                    if name and value:
                        self.session.set_cookie(name, value)
            self.session.save_cookies()
            _log.info("扫码登录成功：%s", status.nickname or "(未返回昵称)")
        return status

    def account(self) -> Account:
        """账号信息（uid / 昵称 / 头像）。"""
        payload = self.session.request(ep.ACCOUNT, {})
        return Account.from_api(payload)

    def logout(self) -> None:
        """退出登录（失败也清本地 cookie，保证可用性）。"""
        try:
            if self.session.logged_in:
                self.session.request(ep.LOGOUT, {}, allow_eapi_retry=False)
        except ApiError as exc:
            _log.info("服务端登出失败（忽略）：%s", exc)
        finally:
            self.session.clear_cookies()

    # ============================================================ 搜索

    def search(self, keyword: str, search_type: int = ep.SEARCH_TYPE_SONG, offset: int = 0, limit: int = 30) -> SearchPage:
        """分类搜索（✅ 实测 type=1/100/10/1000 均可用）。"""
        data = {"s": keyword, "type": search_type, "offset": offset, "limit": limit, "total": "true"}
        payload = self.session.request(ep.SEARCH, data)
        result = payload.get("result") or {}
        page = SearchPage(keyword=keyword, search_type=search_type, offset=offset, limit=limit)

        if search_type == ep.SEARCH_TYPE_SONG:
            page.items = Song.list_from_api(result.get("songs") or [], result.get("privileges"))
            page.total = int(result.get("songCount") or len(page.items))
        elif search_type == ep.SEARCH_TYPE_ARTIST:
            page.items = [Artist.from_api(a) for a in result.get("artists") or []]
            page.total = int(result.get("artistCount") or len(page.items))
        elif search_type == ep.SEARCH_TYPE_ALBUM:
            page.items = [Album.from_api(a) for a in result.get("albums") or []]
            page.total = int(result.get("albumCount") or len(page.items))
        elif search_type == ep.SEARCH_TYPE_PLAYLIST:
            page.items = [Playlist.from_api(p) for p in result.get("playlists") or []]
            page.total = int(result.get("playlistCount") or len(page.items))
        else:
            raise ValueError(f"不支持的搜索类型：{search_type}")
        return page

    # ============================================================ 歌手 / 专辑

    def artist_top_songs(self, artist_id: int, limit: int = 50) -> list[Song]:
        """歌手热门歌曲（✅ 实测返回 50 首）。"""
        payload = self.session.request(ep.ARTIST_TOP_SONG, params={"id": artist_id})
        songs = payload.get("songs") or []
        return Song.list_from_api(songs[:limit] if limit else songs)

    def album_songs(self, album_id: int) -> tuple[Album, list[Song]]:
        """专辑详情与曲目（✅ 实测）。"""
        path = ep.ALBUM_DETAIL.path.format(id=album_id)
        payload = self.session.request(ep.ALBUM_DETAIL, params={}, path=path)
        album_raw = payload.get("album") or {}
        album = Album.from_api(album_raw) if album_raw else Album(id=album_id, name="")
        songs = Song.list_from_api(payload.get("songs") or [])
        if payload.get("album", {}).get("size") and not album.size:
            album.size = len(songs)
        return album, songs

    # ============================================================ 歌单 / 歌曲

    def playlist_detail(self, playlist_id: int, *, limit: int = 1000, offset: int = 0) -> Playlist:
        """歌单详情（``trackIds`` 全量 + 首批 ``tracks`` 分页）。

        ``n`` 为本次返回的曲目条数，``s`` 为偏移 —— 曲目需要循环拉取（SPEC §2.3）。
        """
        payload = self.session.request(
            ep.PLAYLIST_DETAIL, {"id": playlist_id, "n": limit, "s": offset, "newStyle": "true"}
        )
        raw = payload.get("playlist") or {}
        # privileges 是 playlist 的兄弟键（不在 playlist 里），必须从顶层传进去
        return Playlist.from_api(raw, with_tracks=True, privileges=payload.get("privileges"))

    def song_detail(self, song_ids: Sequence[int]) -> list[Song]:
        """批量歌曲详情（收藏列表补全曲目用）。

        坑：``c`` 参数必须和 ``ids`` 传**同一份 JSON**。传 ``"[]"`` 时服务端不报错，
        只返回 ``{"songs": [], "code": 200}``——静默空结果（真机踩过，见 acceptance.md）。
        """
        if not song_ids:
            return []
        ids = json.dumps([{"id": int(i)} for i in song_ids], separators=(",", ":"))
        payload = self.session.request(ep.SONG_DETAIL, {"ids": ids, "c": ids})
        return Song.list_from_api(payload.get("songs") or [], payload.get("privileges"))

    def user_playlists(self, uid: int, *, limit: int = 1000, offset: int = 0) -> list[Playlist]:
        """用户歌单列表（用于定位「我喜欢的音乐」，``specialType == 5``）。"""
        payload = self.session.request(ep.USER_PLAYLISTS, {"uid": uid, "offset": offset, "limit": limit})
        return [Playlist.from_api(p) for p in payload.get("playlist") or []]

    def star_playlist(self, uid: int) -> Playlist | None:
        """「我喜欢的音乐」歌单。"""
        for pl in self.user_playlists(uid):
            if pl.is_star_playlist:
                return pl
        return None

    def like(self, track_id: int, liked: bool, user_id: int) -> None:
        """收藏 / 取消收藏（与逆向结论一致：m14 导出 ``wg`` → ``/api/song/like``）。"""
        self.session.request(
            ep.SONG_LIKE, {"trackId": int(track_id), "userid": int(user_id), "like": "true" if liked else "false"}
        )

    # ============================================================ 播放 / 歌词

    def song_url(self, song_id: int, level: str = "exhigh") -> SongUrl:
        """按指定音质取流（``encodeType`` 必填，否则 400；见 SPEC §8 E8）。"""
        payload = self.session.request(
            ep.SONG_URL, {"ids": f"[{int(song_id)}]", "level": level, "encodeType": "flac"}
        )
        data = payload.get("data") or []
        if not data:
            return SongUrl(song_id=song_id, level=level, code=int(payload.get("code") or 0), message="无数据返回")
        return SongUrl.from_api(data[0], level)

    def song_url_best(self, song_id: int, ladder: Iterable[str] | None = None) -> SongUrl:
        """音质自动降级：沿阶梯逐级尝试，返回首个可播结果（SPEC §2.3）。

        ``ladder`` 为 None 时读 ``config.QUALITY_LADDER``（运行时改它即可切换策略，
        例如端到端测试里用 ``--level standard`` 避开被服务端截断的 320k 试听流）。
        """
        ladder = QUALITY_LADDER if ladder is None else ladder
        last: SongUrl | None = None
        for level in ladder:
            try:
                result = self.song_url(song_id, level)
            except ApiError as exc:
                _log.info("取流失败（%s，音质 %s）：%s", song_id, level, exc)
                last = SongUrl(song_id=song_id, level=level, code=exc.code, message=exc.user_message)
                continue
            if result.playable:
                return result
            last = result
            _log.info("音质 %s 无可用地址（code=%s），降级重试", level, result.code)
        return last or SongUrl(song_id=song_id, message="无版权或需要会员")

    def lyric(self, song_id: int) -> dict[str, Any]:
        """歌词原始响应（解析见 `model/lrc.py`，M5 使用）。"""
        return self.session.request(
            ep.LYRIC, params={"id": int(song_id), "lv": -1, "kv": -1, "tv": -1}, need_login=False
        )


__all__ = ["Api", "NeedLoginError", "ApiError"]
