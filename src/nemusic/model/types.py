"""数据模型：把接口返回的原始 JSON 归一化成本项目内部对象。

兼容两种字段命名（PC 接口混用）：
- 歌曲：``ar``/``al``/``dt``（新）与 ``artists``/``album``/``duration``（旧）
- 详情：``songs[].ar`` 为对象列表，``privileges[]`` 单独返回
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


def format_duration(ms: int | float | None) -> str:
    """毫秒 → ``mm:ss``（超过 1 小时用 ``h:mm:ss``）。"""
    if not ms or ms < 0:
        return "00:00"
    total = int(ms // 1000)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _first(raw: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = raw.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


@dataclass(slots=True)
class Artist:
    id: int
    name: str
    alias: tuple[str, ...] = ()
    avatar: str = ""
    album_size: int = 0
    music_size: int = 0

    @classmethod
    def from_api(cls, raw: dict) -> "Artist":
        return cls(
            id=int(_first(raw, "id", "artistId", default=0) or 0),
            name=str(_first(raw, "name", "artistName", default="") or ""),
            alias=tuple(raw.get("alias") or raw.get("alia") or ()),
            avatar=str(_first(raw, "picUrl", "img1v1Url", "avatar", default="") or ""),
            album_size=int(raw.get("albumSize") or 0),
            music_size=int(raw.get("musicSize") or 0),
        )


@dataclass(slots=True)
class Song:
    """歌曲。``privilege`` 保存原始权限对象（供后续判断可播性）。"""

    id: int
    name: str
    artist_names: tuple[str, ...] = ()
    artist_ids: tuple[int, ...] = ()
    album: str = ""
    album_id: int = 0
    duration_ms: int = 0
    fee: int = 0
    mvid: int = 0
    cover: str = ""
    privilege: dict | None = None
    #: 本地推断：是否已收藏（由 library 状态回填，接口本身不返回）
    starred: bool = False

    # ---------------------------------------------------------- 展示
    @property
    def artist_text(self) -> str:
        return " / ".join(self.artist_names) if self.artist_names else "未知歌手"

    @property
    def duration_text(self) -> str:
        return format_duration(self.duration_ms)

    @property
    def key(self) -> str:
        """缓存文件名用的唯一键。"""
        return str(self.id)

    # ---------------------------------------------------------- 解析
    @classmethod
    def from_api(cls, raw: dict, privilege: dict | None = None) -> "Song":
        artists_raw = _first(raw, "ar", "artists", default=[]) or []
        album_raw = _first(raw, "al", "album", default={}) or {}
        if isinstance(album_raw, str):  # 少数接口把专辑名直接给成字符串
            album_raw = {"name": album_raw}
        priv = privilege if privilege is not None else _first(raw, "privilege", default=None)
        return cls(
            id=int(_first(raw, "id", "songId", default=0) or 0),
            name=str(_first(raw, "name", "songName", default="") or ""),
            artist_names=tuple(str(a.get("name", "")) for a in artists_raw if isinstance(a, dict)),
            artist_ids=tuple(int(a.get("id") or 0) for a in artists_raw if isinstance(a, dict)),
            album=str(album_raw.get("name", "") or ""),
            album_id=int(album_raw.get("id") or 0),
            duration_ms=int(_first(raw, "dt", "duration", "durationMs", default=0) or 0),
            fee=int(raw.get("fee") or 0),
            mvid=int(_first(raw, "mvId", "mvid", default=0) or 0),
            cover=str(_first(album_raw, "picUrl", "blurPicUrl", "pic", default="") or _first(raw, "cover", default="") or ""),
            privilege=priv if isinstance(priv, dict) else None,
        )

    @classmethod
    def list_from_api(cls, items: Iterable[dict], privileges: Iterable[dict] | None = None) -> list["Song"]:
        """批量解析（``privileges`` 与 ``songs`` 顺序对应）。"""
        privs = list(privileges or [])
        out: list[Song] = []
        for i, raw in enumerate(items):
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            out.append(cls.from_api(raw, privs[i] if i < len(privs) else None))
        return out


@dataclass(slots=True)
class Album:
    id: int
    name: str
    artist: str = ""
    artist_id: int = 0
    cover: str = ""
    size: int = 0
    publish_time: int = 0

    @classmethod
    def from_api(cls, raw: dict) -> "Album":
        artist_raw = raw.get("artist") or raw.get("ar") or {}
        if isinstance(artist_raw, list):
            artist_raw = artist_raw[0] if artist_raw else {}
        return cls(
            id=int(_first(raw, "id", "albumId", default=0) or 0),
            name=str(_first(raw, "name", "albumName", default="") or ""),
            artist=str(artist_raw.get("name", "") if isinstance(artist_raw, dict) else ""),
            artist_id=int(artist_raw.get("id") or 0 if isinstance(artist_raw, dict) else 0),
            cover=str(_first(raw, "picUrl", "blurPicUrl", "coverImgUrl", default="") or ""),
            size=int(raw.get("size") or 0),
            publish_time=int(raw.get("publishTime") or 0),
        )


@dataclass(slots=True)
class Playlist:
    id: int
    name: str
    cover: str = ""
    track_count: int = 0
    creator: str = ""
    creator_id: int = 0
    description: str = ""
    #: 5 = 我喜欢的音乐（SPEC §2.3）
    special_type: int = 0
    play_count: int = 0
    track_ids: list[int] = field(default_factory=list)
    tracks: list[Song] = field(default_factory=list)
    subscribed: bool = False

    @property
    def is_star_playlist(self) -> bool:
        return self.special_type == 5

    @classmethod
    def from_api(cls, raw: dict, *, with_tracks: bool = False, privileges: Iterable[dict] | None = None) -> "Playlist":
        """``privileges`` 在 playlist/detail 响应里是 ``playlist`` 的**兄弟键**，
        不在 playlist 对象里；调用方拿到顶层那份后从这里传进来（见 api.playlist_detail）。"""
        creator_raw = raw.get("creator") or {}
        pl = cls(
            id=int(raw.get("id") or 0),
            name=str(raw.get("name") or ""),
            cover=str(_first(raw, "coverImgUrl", "picUrl", "cover", default="") or ""),
            track_count=int(_first(raw, "trackCount", "songCount", default=0) or 0),
            creator=str(creator_raw.get("nickname", "") if isinstance(creator_raw, dict) else ""),
            creator_id=int(creator_raw.get("userId") or 0 if isinstance(creator_raw, dict) else 0),
            description=str(_first(raw, "description", "desc", default="") or ""),
            special_type=int(raw.get("specialType") or 0),
            play_count=int(raw.get("playCount") or 0),
            subscribed=bool(raw.get("subscribed")),
        )
        tids = raw.get("trackIds") or []
        pl.track_ids = [int(t.get("id") or 0) for t in tids if isinstance(t, dict) and t.get("id")]
        if with_tracks and raw.get("tracks"):
            privs = list(privileges) if privileges is not None else raw.get("privileges")
            pl.tracks = Song.list_from_api(raw["tracks"], privs)
        return pl


@dataclass(slots=True)
class Account:
    uid: int = 0
    nickname: str = ""
    avatar: str = ""
    vip_type: int = 0
    signature: str = ""

    @classmethod
    def from_api(cls, raw: dict) -> "Account":
        profile = raw.get("profile") or {}
        return cls(
            uid=int(profile.get("userId") or raw.get("userId") or 0),
            nickname=str(profile.get("nickname") or ""),
            avatar=str(profile.get("avatarUrl") or ""),
            vip_type=int(profile.get("vipType") or 0),
            signature=str(profile.get("signature") or ""),
        )


@dataclass(slots=True)
class QrStatus:
    """二维码轮询结果（SPEC §4 AC-1.2）。"""

    code: int
    message: str = ""
    nickname: str = ""
    avatar: str = ""

    @property
    def waiting(self) -> bool:
        return self.code == 801

    @property
    def scanned(self) -> bool:
        return self.code == 802

    @property
    def expired(self) -> bool:
        return self.code == 800

    @property
    def ok(self) -> bool:
        return self.code == 803


@dataclass(slots=True)
class SearchPage:
    keyword: str
    search_type: int
    offset: int = 0
    limit: int = 30
    total: int = 0
    #: 元素类型随 search_type 变化：Song / Artist / Album / Playlist
    items: list = field(default_factory=list)

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


@dataclass(slots=True)
class SongUrl:
    """取流结果（SPEC §2.3）。``url`` 为 None 表示无权限/无版权。"""

    song_id: int
    url: str | None = None
    level: str = ""
    br: int = 0
    size: int = 0
    md5: str = ""
    type: str = "mp3"
    fee: int = 0
    free_trial: bool = False
    code: int = 0
    message: str = ""

    @property
    def playable(self) -> bool:
        return bool(self.url)

    @classmethod
    def from_api(cls, raw: dict, level_requested: str = "") -> "SongUrl":
        return cls(
            song_id=int(raw.get("id") or 0),
            url=raw.get("url") or None,
            level=str(raw.get("level") or level_requested or ""),
            br=int(raw.get("br") or 0),
            size=int(raw.get("size") or 0),
            md5=str(raw.get("md5") or ""),
            type=str(raw.get("type") or "mp3"),
            fee=int(raw.get("fee") or 0),
            free_trial=bool(raw.get("freeTrialInfo")),
            code=int(raw.get("code") or 0),
            message=str(raw.get("message") or ""),
        )


@dataclass(slots=True)
class LyricLine:
    time_ms: int
    text: str
    translation: str = ""


@dataclass(slots=True)
class LyricDoc:
    song_id: int = 0
    lines: list[LyricLine] = field(default_factory=list)
    #: 作词/作曲等元信息（不参与滚动，置顶显示）
    credits: list[str] = field(default_factory=list)

    @property
    def has_translation(self) -> bool:
        return any(line.translation for line in self.lines)
