"""歌词状态：切歌时取歌词、写缓存、失败回落缓存（AC-4.4/4.6）。

与其它状态层保持同一套约定（SPEC §4 硬性约定 1）：
- 网络请求在子线程，主线程只收 ``LyricDoc``；
- 磁盘缓存优先，缓存过期（默认 7 天）才重新请求；
- 请求失败时**回落到过期缓存**，再不行给空 ``LyricDoc``（界面显示"暂无歌词"），
  绝不让歌词问题影响播放。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from .. import config as app_config
from ..model.lyric_index import LyricIndex
from ..model.types import LyricDoc
from ..net.api import Api
from ..util import async_task, log
from .cache import CacheManager

_log = log.get("state.lyrics")


class _Signals(QObject):
    #: (song_id, LyricDoc)
    loaded = Signal(object, object)


class LyricState(QObject):
    """歌词加载器（全局一个即可）。"""

    def __init__(self, api: Api, cache: CacheManager | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.cache = cache or CacheManager()
        self.signals = _Signals()
        #: 正在请求的 song_id（避免同一首重复请求）
        self._pending: set[int] = set()

    @property
    def loaded(self):
        return self.signals.loaded

    # ------------------------------------------------------------ 对外
    def request(self, song_id: int, *, force: bool = False) -> None:
        """请求某首歌的歌词。"""
        song_id = int(song_id or 0)
        if song_id <= 0 or song_id in self._pending:
            return
        self._pending.add(song_id)
        task = async_task.AsyncTask(self._load, song_id, force)
        task.signals.done.connect(self._on_loaded)
        task.signals.failed.connect(self._on_failed)
        task.start()

    def index_for(self, song_id: int) -> LyricIndex:
        """同步取缓存里的歌词索引（启动时/无网络时用）。"""
        payload = self.cache.get_json("lyric", song_id, app_config.LYRIC_CACHE_TTL)
        return LyricIndex.from_payload(song_id, payload)

    # ------------------------------------------------------------ 子线程
    def _load(self, song_id: int, force: bool) -> tuple[int, LyricDoc]:
        doc: LyricDoc | None = None
        if not force:
            payload = self.cache.get_json("lyric", song_id, app_config.LYRIC_CACHE_TTL)
            if payload is not None:
                doc = LyricIndex.from_payload(song_id, payload).doc
        if doc is None:
            try:
                payload = self.api.lyric(song_id)
            except Exception as exc:  # noqa: BLE001 - 歌词失败不该影响播放
                _log.debug("歌词请求失败 song=%s：%s", song_id, exc)
                payload = self.cache.get_json("lyric", song_id, -1)  # 过期缓存兜底
                if payload is None:
                    return song_id, LyricDoc(song_id=song_id)
            else:
                self.cache.put_json("lyric", song_id, payload)
            doc = LyricIndex.from_payload(song_id, payload).doc
        return song_id, doc

    # ------------------------------------------------------------ 主线程
    def _on_loaded(self, payload) -> None:
        song_id, doc = payload
        self._pending.discard(song_id)
        self.signals.loaded.emit(song_id, doc)

    def _on_failed(self, exc) -> None:  # pragma: no cover - 极端情况
        _log.debug("歌词任务异常：%s", exc)


__all__ = ["LyricState"]
