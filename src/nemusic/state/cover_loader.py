"""专辑封面加载：磁盘缓存 + 子线程下载 + 防串台（SPEC §4 硬性约定 1）。

用法::

    loader = CoverLoader(cache)
    loader.ready.connect(self._on_cover)      # (token, QPixmap | None)
    loader.request(song.cover, song.id)       # token 用歌曲 id

约定：
- 网络/磁盘 I/O 永远在子线程（``AsyncTask``），主线程只拿到字节并解码成 QPixmap；
- ``token`` 用于防串台：连点几首歌时，上层只采用最后一次请求的封面
  （``ready`` 会带回 token，主窗口拿它和当前歌曲 id 比对）；
- 下载失败只影响封面显示，**绝不影响播放**（失败集合避免每首歌都卡一次超时）。
"""

from __future__ import annotations

import requests
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap

from .. import config as app_config
from ..util import async_task, log
from .cache import CacheManager

_log = log.get("state.cover")

#: 封面下载超时（秒）。封面很小，给短一点；失败就放弃，不重试。
TIMEOUT = (5, 10)


class _Signals(QObject):
    #: (token, QPixmap | None)。QPixmap 为 None 表示无封面/加载失败
    ready = Signal(object, object)


class CoverLoader(QObject):
    """封面图加载器（全局一个即可，上层共用）。"""

    def __init__(self, cache: CacheManager | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cache = cache or CacheManager()
        self.signals = _Signals()
        #: 本次运行内已失败的 URL（避免每首歌都卡一次超时）
        self._failed: set[str] = set()

    @property
    def ready(self):
        return self.signals.ready

    # ------------------------------------------------------------ 对外
    def request(self, url: str, token: object = None) -> None:
        """请求封面。``token`` 原样带回，供上层判断是否还是当前歌曲。"""
        if not url:
            self.signals.ready.emit(token, None)
            return
        task = async_task.AsyncTask(self._fetch, url, token)
        task.signals.done.connect(self._on_done)
        task.signals.failed.connect(self._on_failed)
        task.start()

    def clear_failed(self) -> None:
        self._failed.clear()

    # ------------------------------------------------------------ 子线程
    def _fetch(self, url: str, token: object) -> tuple[object, bytes | None]:
        """返回 ``(token, 图片字节)``；字节为 None 表示拿不到图。"""
        path = self.cache.cover_lookup(url)
        if path is not None:
            try:
                return token, path.read_bytes()
            except OSError:
                pass
        if url in self._failed:
            return token, None
        try:
            resp = requests.get(
                url,
                timeout=TIMEOUT,
                headers={"User-Agent": app_config.USER_AGENT, "Referer": "https://music.163.com/"},
            )
            if resp.status_code != 200 or not resp.content:
                _log.debug("封面下载失败 %s → HTTP %s（%d 字节）", url[:80], resp.status_code, len(resp.content))
                self._failed.add(url)
                return token, None
            self.cache.cover_path(url).write_bytes(resp.content)
            return token, resp.content
        except Exception as exc:  # noqa: BLE001 - 封面失败不该影响播放
            _log.debug("封面下载异常 %s：%s", url[:80], exc)
            self._failed.add(url)
            return token, None

    # ------------------------------------------------------------ 主线程
    def _on_done(self, payload) -> None:
        token, data = payload
        if data:
            pix = QPixmap()
            if pix.loadFromData(data):
                self.signals.ready.emit(token, pix)
                return
        self.signals.ready.emit(token, None)

    def _on_failed(self, exc) -> None:  # pragma: no cover - 极端情况
        _log.debug("封面任务异常：%s", exc)


__all__ = ["CoverLoader"]
