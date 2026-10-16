"""「我喜欢的音乐」状态（AC-3.1~3.5）：全量分页 → 批量补曲目 → 本地缓存兜底。

数据链路（SPEC §2.3）::

    star_playlist(uid) ──▶ trackIds（全量，可能 >1000）
                              │  每 300 个一批
                              ▼
                       /api/v3/song/detail ──▶ Song 列表

约定（SPEC §4 规则 1）：网络请求全部走 ``AsyncTask`` 在子线程完成，
本对象只在主线程更新状态并发信号。
"""

from __future__ import annotations

import json
import time
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QObject, Signal

from .. import config as app_config
from ..model.types import Playlist, Song
from ..net.api import Api
from ..util import log
from ..util.async_task import AsyncTask

_log = log.get("state.library")

#: 一次补全多少首（接口单次上限实测 300~1000，取 300 稳一点）
DETAIL_BATCH = 300
#: trackIds 一次拉多少（实测该接口忽略 s 偏移，只会返回全量，故只请求一次）
TRACK_PAGE = 1000


class LibraryState(QObject):
    """我喜欢的音乐（含收藏状态回填）。"""

    #: 加载状态变化（bool）
    loading_changed = Signal(bool)
    #: 曲目列表就绪（list[Song]，可能来自缓存）
    songs_loaded = Signal(list)
    #: 失败（中文原因）；此时可能已回退到缓存
    failed = Signal(str)
    #: 收藏状态变化（song_id, liked）——与 controller.like_changed 同语义
    like_changed = Signal(object, bool)
    #: 歌单元信息（名称/封面/曲目数）
    playlist_changed = Signal(object)

    def __init__(
        self,
        api: Api,
        session,
        parent: QObject | None = None,
        *,
        cache_ttl: float | None = None,
    ) -> None:
        super().__init__(parent)
        self.api = api
        self.session = session
        self.songs: list[Song] = []
        self.playlist: Playlist | None = None
        self.loading = False
        #: 收藏状态缓存（song_id -> bool），由收藏接口/列表共同维护
        self.liked: dict[int, bool] = {}
        self._generation = 0
        self._ttl = app_config.CACHE_TTL["playlist"] if cache_ttl is None else cache_ttl

    # ============================================================ 只读
    @property
    def logged_in(self) -> bool:
        return bool(self.session and self.session.logged_in)

    def is_liked(self, song_id: int) -> bool | None:
        """None 表示未知（尚未加载/未登录）。"""
        return self.liked.get(int(song_id))

    def song_ids(self) -> list[int]:
        return [s.id for s in self.songs]

    # ============================================================ 加载
    def reload(self, *, force: bool = False) -> None:
        """加载我喜欢的音乐（先试缓存，再走网络）。"""
        if not self.logged_in:
            self.failed.emit("请先登录后查看我喜欢的音乐")
            return
        if self.loading:
            return
        if not force:
            cached = self._load_cache()
            if cached is not None:
                songs, playlist = cached
                _log.info("使用缓存的收藏列表：%d 首", len(songs))
                self._apply(songs, playlist)
                return
        self.loading = True
        self.loading_changed.emit(True)
        self._generation += 1
        generation = self._generation
        task = AsyncTask(self._fetch_all)
        task.done.connect(lambda payload: self._on_fetched(payload, generation))
        task.start()

    def _fetch_all(self) -> tuple:
        """子线程：拉歌单元信息 + 全量 trackIds + 分批补曲目。"""
        try:
            uid = self.session.uid
            playlist = self.api.star_playlist(uid)
            if playlist is None:
                return None, None, "没找到「我喜欢的音乐」歌单"
            # trackIds：一次拉全（实测该接口忽略 s 偏移，分页只会拿到同一份全量列表，
            # 所以这里只请求一次，超长歌单用 limit=1000 兜底）
            page = self.api.playlist_detail(playlist.id, limit=1000, offset=0)
            track_ids: list[int] = list(dict.fromkeys(page.track_ids))  # 去重保序
            if not track_ids:
                return [], playlist, None
            # 首批自带的 tracks 直接用，其余分批补全（c 参数必须等于 ids，见 api.song_detail）
            songs: dict[int, Song] = {s.id: s for s in page.tracks}
            missing = [i for i in track_ids if i not in songs]
            for start in range(0, len(missing), DETAIL_BATCH):
                batch = missing[start : start + DETAIL_BATCH]
                if batch:
                    for song in self.api.song_detail(batch):
                        songs[song.id] = song
            ordered = [songs[i] for i in track_ids if i in songs]
            return ordered, playlist, None
        except Exception as exc:  # noqa: BLE001 - 交给主线程统一提示
            _log.warning("加载收藏列表失败：%s", exc)
            return None, None, getattr(exc, "user_message", str(exc))

    def _on_fetched(self, payload: tuple, generation: int) -> None:
        self.loading = False
        self.loading_changed.emit(False)
        songs, playlist, message = payload
        if generation != self._generation:
            _log.debug("丢弃过期的收藏列表结果")
            return
        if songs is None:
            cached = self._load_cache(ignore_ttl=True)
            if cached is not None:
                _log.info("网络失败，回退到缓存收藏列表")
                self._apply(cached[0], cached[1])
            self.failed.emit(message or "加载失败")
            return
        self._apply(songs, playlist)
        self._save_cache(songs, playlist)

    def clear(self) -> None:
        """登出时清空用户数据（含收藏状态），避免下一个账号看到上一位的歌单。"""
        self._generation += 1  # 丢弃还在飞的请求
        self.songs = []
        self.playlist = None
        self.liked = {}
        self.playlist_changed.emit(None)
        self.songs_loaded.emit([])

    def _apply(self, songs: list[Song], playlist: Playlist | None) -> None:
        self.songs = songs
        self.playlist = playlist
        if playlist is not None:
            self.session.star_playlist_id = playlist.id
        # 收藏列表里的歌天然就是"已收藏"
        self.liked = {s.id: True for s in songs}
        self.playlist_changed.emit(playlist)
        self.songs_loaded.emit(songs)

    # ============================================================ 收藏
    def set_liked(self, song_id: int, liked: bool) -> None:
        """本地立即生效（接口调用由 controller 负责）。"""
        song_id = int(song_id)
        self.liked[song_id] = liked
        self.like_changed.emit(song_id, liked)
        if not liked:
            # 取消收藏后从"我喜欢的音乐"里移除，保持列表与云端一致
            self.songs = [s for s in self.songs if s.id != song_id]
            self.songs_loaded.emit(self.songs)

    def refresh_liked(self, checker: Callable[[Sequence[int]], Iterable[int]] | None = None) -> None:
        """用外部判断器批量回填收藏状态（例如搜索页用）。"""
        if checker is None or not self.songs:
            return
        try:
            starred = set(checker(self.song_ids()))
        except Exception as exc:  # noqa: BLE001
            _log.warning("回填收藏状态失败：%s", exc)
            return
        self.liked = {s.id: (s.id in starred) for s in self.songs}

    # ============================================================ 缓存
    def _cache_path(self):
        return app_config.PLAYLIST_CACHE_DIR / f"star_{self.session.uid if self.session else 0}.json"

    def _save_cache(self, songs: list[Song], playlist: Playlist | None) -> None:
        """收藏列表落本地缓存（**含完整元数据**，断网也能用，AC-3.5）。"""
        path = self._cache_path()
        payload = {
            "saved_at": time.time(),
            "playlist_id": playlist.id if playlist else 0,
            "name": playlist.name if playlist else "",
            "track_count": playlist.track_count if playlist else len(songs),
            "cover": playlist.cover if playlist else "",
            # 存完整字段：回退缓存时不允许再走网络（那就不是"降级"了）
            "songs": [
                {
                    "id": s.id,
                    "name": s.name,
                    "artist_names": list(s.artist_names),
                    "artist_ids": list(s.artist_ids),
                    "album": s.album,
                    "album_id": s.album_id,
                    "duration_ms": s.duration_ms,
                    "cover": s.cover,
                    "mvid": s.mvid,
                    "fee": s.fee,
                }
                for s in songs
            ],
        }
        try:
            app_config.write_private(path, json.dumps(payload, ensure_ascii=False))
        except OSError as exc:  # pragma: no cover
            _log.warning("写收藏列表缓存失败：%s", exc)

    def _load_cache(self, ignore_ttl: bool = False) -> tuple[list[Song], Playlist | None] | None:
        path = self._cache_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not ignore_ttl and time.time() - float(raw.get("saved_at") or 0) > self._ttl:
            return None
        raw_songs = raw.get("songs") or []
        if not raw_songs:
            return None
        songs = [
            Song(
                id=int(item.get("id") or 0),
                name=str(item.get("name") or ""),
                artist_names=tuple(item.get("artist_names") or ()),
                artist_ids=tuple(int(i) for i in item.get("artist_ids") or ()),
                album=str(item.get("album") or ""),
                album_id=int(item.get("album_id") or 0),
                duration_ms=int(item.get("duration_ms") or 0),
                cover=str(item.get("cover") or ""),
                mvid=int(item.get("mvid") or 0),
                fee=int(item.get("fee") or 0),
            )
            for item in raw_songs
            if int(item.get("id") or 0)
        ]
        if not songs:
            return None
        playlist = Playlist(
            id=int(raw.get("playlist_id") or 0),
            name=str(raw.get("name") or "我喜欢的音乐"),
            track_count=int(raw.get("track_count") or len(songs)),
            cover=str(raw.get("cover") or ""),
            track_ids=[s.id for s in songs],
            special_type=5,
        )
        return songs, playlist

    def clear_cache(self) -> None:
        path = self._cache_path()
        try:
            path.unlink()
        except FileNotFoundError:
            pass


__all__ = ["LibraryState", "DETAIL_BATCH", "TRACK_PAGE"]
