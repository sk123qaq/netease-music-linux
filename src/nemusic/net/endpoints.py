"""端点常量表（**唯一权威**：其它模块不得出现裸 URL，见 AGENTS.md 硬性约定 2）。

路径使用 PC 客户端的明文形态 ``/api/...``（与逆向结论一致）；
``eapi`` 为 True 的端点表示支持走加密备用通道（SPEC §2.2）。
状态列见 SPEC §2.3：✅ 已实测 / ⏳ 待登录联调。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    """一个接口定义。"""

    path: str
    method: str = "POST"  # POST | GET
    need_login: bool = False
    eapi: bool = True  # 是否支持 /eapi/ 备用通道
    note: str = ""

    def __str__(self) -> str:  # 便于日志
        return f"{self.method} {self.path}"


BASE_URL = "https://music.163.com"

# ---------------------------------------------------------------- 登录 / 账号
QR_UNIKEY = Endpoint("/api/login/qrcode/unikey", method="POST", eapi=True, note="✅ 返回 unikey")
QR_POLL = Endpoint("/api/login/qrcode/client/login", method="GET", eapi=False, note="⏳ 800/801/802/803")
ACCOUNT = Endpoint("/api/nuser/account/get", method="POST", need_login=True, eapi=True, note="⏳ uid/昵称/头像")
LOGOUT = Endpoint("/api/logout", method="POST", need_login=True, eapi=True)
USER_PLAYLISTS = Endpoint("/api/user/playlist", method="POST", need_login=True, eapi=True, note="⏳ 找 specialType==5")

# ---------------------------------------------------------------- 搜索
SEARCH = Endpoint("/api/search/get/web", method="POST", note="✅ s/type/offset/limit")
SEARCH_BACKUP = Endpoint("/api/cloudsearch/pc", method="POST", note="✅ 备用")
ARTIST_TOP_SONG = Endpoint("/api/artist/top/song", method="GET", note="✅ 歌手热门 50")
ALBUM_DETAIL = Endpoint("/api/v1/album/{id}", method="GET", note="✅ 专辑曲目")

# ---------------------------------------------------------------- 歌单 / 歌曲
PLAYLIST_DETAIL = Endpoint("/api/v6/playlist/detail", method="POST", eapi=True, note="✅ trackIds 全量 + tracks 分页")
SONG_DETAIL = Endpoint("/api/v3/song/detail", method="POST", eapi=True, note="⏳ 批量歌曲详情")
SONG_LIKE = Endpoint("/api/song/like", method="POST", need_login=True, eapi=True, note="⏳ 收藏/取消收藏")

# ---------------------------------------------------------------- 播放 / 歌词
SONG_URL = Endpoint("/api/song/enhance/player/url/v1", method="POST", eapi=True, note="✅ 必须带 encodeType")
LYRIC = Endpoint("/api/song/lyric", method="GET", eapi=False, note="✅ 行级 lrc + tlyric")

#: 搜索类型编码（✅ 实测）
SEARCH_TYPE_SONG = 1
SEARCH_TYPE_ALBUM = 10
SEARCH_TYPE_ARTIST = 100
SEARCH_TYPE_PLAYLIST = 1000

SEARCH_TYPE_LABELS = {
    SEARCH_TYPE_SONG: "单曲",
    SEARCH_TYPE_ARTIST: "歌手",
    SEARCH_TYPE_ALBUM: "专辑",
    SEARCH_TYPE_PLAYLIST: "歌单",
}

#: 二维码登录 URL 模板（前端把 unikey 拼进去生成二维码内容）
QR_LOGIN_URL_TEMPLATE = "https://music.163.com/login?codekey={key}"

#: 二维码轮询返回码 → 语义（SPEC §4 AC-1.2）
QR_STATUS = {
    800: "二维码已过期",
    801: "等待扫描",
    802: "已扫描，请在手机上确认",
    803: "登录成功",
}
