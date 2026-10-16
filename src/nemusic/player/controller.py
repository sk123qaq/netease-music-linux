"""播放控制器：把 队列 / 取流 / mpv / 缓存 / 登录态 串起来（SPEC §2.5）。

职责边界：
- 只管播放，不碰 UI；所有状态变化通过信号广播。
- 取流、缓存 IO 都在工作线程（``AsyncTask``）；mpv 命令在主线程（事件驱动）。

关键行为（都有验收条目）：
- 缓存命中 → 直接播本地文件（AC-5.3）
- 未命中 → 取流（音质自动降级）→ 边播边录 → 播完校验后提交（AC-5.1/5.2）
- 播放中拖动进度会导致录制文件不可信 → 只丢弃缓存，不影响播放
- URL 过期 / 播放出错 → 自动重取一次（AC-4.8）
- 播完自动下一首；顺序模式到底停止并提示（AC-4.1）
"""

from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtCore import QObject, QTimer, Signal

from ..config import Config
from ..model.types import Song, SongUrl, format_duration
from ..net.api import Api
from ..state.cache import CacheManager
from ..state.session_state import SessionState
from ..util import log
from ..util.async_task import AsyncTask
from .mpv import MpvPlayer, available as mpv_available
from .queue import PlayQueue

_log = log.get("player.controller")

#: 判定「听完整首」的宽容度：位置距结束 5 秒以内都算播完。
#: 网络流尾包丢失时字节数会差 1~2%，但位置已经到底，此时仍应缓存。
END_GRACE_MS = 5000
#: 孤儿临时文件清扫间隔（秒）。切歌竞态会让 mpv 在丢弃之后才建文件，只能靠兜底清扫。
PART_SWEEP_INTERVAL_MS = 10_000


class PlaybackController(QObject):
    """播放的唯一入口。"""

    song_changed = Signal(object)  # Song | None
    playing_changed = Signal(bool)
    progress = Signal(int, int)  # 位置 ms, 总时长 ms
    mode_changed = Signal(str)
    # 注意：id 一律用 object 承载——网易云歌单 id 会超过 2^31，
    # Signal(int) 会直接 OverflowError（真机探测抓到过）。
    like_changed = Signal(object, bool)  # song_id, liked
    queue_changed = Signal()
    error = Signal(str)
    notice = Signal(str)
    #: 缓存事件（调试/测试用）：(song_id, "hit"|"stored"|"discarded")
    cache_event = Signal(object, str)
    #: 播放源信息（调试用）：(song_id, level, br, source)
    source_changed = Signal(object, str, int, str)

    def __init__(
        self,
        api: Api,
        config: Config,
        cache: CacheManager,
        session_state: SessionState | None = None,
        parent: QObject | None = None,
        *,
        player: MpvPlayer | None = None,
        like_checker: Callable[[int], bool | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.api = api
        self.config = config
        self.cache = cache
        self.session_state = session_state
        self.queue = PlayQueue(mode=config.play_mode)
        self.player = player or MpvPlayer(self)
        #: 由 library 注入：判断某歌是否已收藏（返回 None 表示未知）
        self.like_checker = like_checker

        self._duration_ms = 0
        self._position_ms = 0
        self._playing = False
        self._loading = False
        self._retry_used = False
        self._seeked = False
        self._record: tuple | None = None  # (part_path, song_id, level, ext, expected_size)
        self._prefetch: dict[int, SongUrl] = {}
        #: 本次播放里已经失败过的队列索引（防止失败兜底原地打转，见 _auto_next_after_failure）
        self._failed_indexes: set[int] = set()
        self._liked: dict[int, bool] = {}
        #: 兜底清扫：清掉切歌竞态产生的孤儿临时文件（见 CacheManager.sweep_parts）
        self._sweeper = QTimer(self)
        self._sweeper.setInterval(PART_SWEEP_INTERVAL_MS)
        self._sweeper.timeout.connect(self._sweep_parts)

        self.player.position_changed.connect(self._on_position)
        self.player.duration_changed.connect(self._on_duration)
        self.player.paused_changed.connect(self._on_paused)
        self.player.ended.connect(self._on_ended)
        self.player.failed.connect(self._on_player_failed)
        self.player.ready.connect(self._on_player_ready)
        self.player.start()
        self._sweeper.start()

    # ============================================================ 只读
    @property
    def current_song(self) -> Song | None:
        return self.queue.current

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def loading(self) -> bool:
        return self._loading

    @property
    def mode(self) -> str:
        return self.queue.mode

    @property
    def position_ms(self) -> int:
        """当前播放位置（歌词页对齐用；未播放为 0）。"""
        return self._position_ms

    def is_liked(self, song_id: int | None = None) -> bool:
        """收藏状态：优先问 library，其次用本地乐观缓存。"""
        song_id = song_id if song_id is not None else (self.current_song.id if self.current_song else None)
        if song_id is None:
            return False
        if song_id in self._liked:
            return self._liked[song_id]
        if self.like_checker is not None:
            known = self.like_checker(song_id)
            if known is not None:
                self._liked[song_id] = known
                return known
        return False

    # ============================================================ 排队播放
    def play_songs(self, songs: Iterable[Song], index: int = 0, *, context: str = "") -> None:
        """用一批歌替换队列并从 ``index`` 开始播放。"""
        items = list(songs)
        if not items:
            self.error.emit("没有可播放的歌曲")
            return
        index = max(0, min(index, len(items) - 1))
        self._failed_indexes.clear()
        self.queue.set_items(items, index)
        self.queue_changed.emit()
        if context:
            _log.info("开始播放：%s（%d 首，第 %d 首）", context, len(items), index + 1)
        self.play_at(index)

    def play_at(self, index: int) -> None:
        """播放队列中的第 index 首。"""
        if not self.queue.set_index(index):
            self.error.emit("播放位置无效")
            return
        self._start_current()

    def play_song(self, song: Song, queue: Iterable[Song] | None = None) -> None:
        """播放单曲（可选带上它所在列表作为队列）。"""
        items = list(queue) if queue is not None else [song]
        index = next((i for i, s in enumerate(items) if s.id == song.id), 0)
        self.play_songs(items, index)

    # ============================================================ 播放控制
    def stop(self) -> None:
        """停下并丢掉当前曲目/队列（登出用）：不再有"当前歌曲"，也不会自动续播。"""
        self.queue.clear()
        self._liked = {}
        self._failed_indexes.clear()
        self._seeked = False
        self._duration_ms = 0
        self._playing = False
        try:
            self.player.stop()
        except Exception as exc:  # noqa: BLE001 - 登出不能因为播放器异常而失败
            _log.warning("停止播放失败（忽略，继续登出）：%s", exc)
        self.song_changed.emit(None)
        self.playing_changed.emit(False)

    def toggle_pause(self) -> None:
        if self.current_song is None:
            return
        self.player.toggle_pause()

    def pause(self) -> None:
        self.player.set_pause(True)

    def resume(self) -> None:
        self.player.set_pause(False)

    def next(self) -> None:
        index = self.queue.next_index()
        if index is None:
            self._playing = False
            self.playing_changed.emit(False)
            self.notice.emit("已播放到列表末尾")
            return
        self.play_at(index)

    def prev(self) -> None:
        index = self.queue.prev_index()
        if index is None:
            self.notice.emit("已经是第一首了")
            return
        self.play_at(index)

    def seek(self, position_ms: int) -> None:
        """拖动进度（会标记本次录制不可信，避免缓存半首歌）。"""
        if self.current_song is None:
            return
        self._seeked = True
        self.player.seek(position_ms)
        self.progress.emit(int(position_ms), self._duration_ms)

    def set_volume(self, volume: int) -> None:
        self.config.volume = int(volume)
        self.player.set_volume(volume)

    def set_mute(self, muted: bool) -> None:
        self.config.muted = bool(muted)
        self.player.set_mute(muted)

    def set_mode(self, mode: str) -> None:
        self.queue.set_mode(mode)
        self.config.play_mode = mode
        self.mode_changed.emit(mode)

    def cycle_mode(self) -> None:
        from .queue import MODES

        self.set_mode(MODES[(MODES.index(self.queue.mode) + 1) % len(MODES)])

    # ============================================================ 收藏
    def toggle_like(self, song: Song | None = None) -> None:
        """收藏 / 取消收藏（AC-2.3）。需要登录。"""
        target = song or self.current_song
        if target is None:
            return
        self.set_like(target, not self.is_liked(target.id))

    def set_like(self, target: Song, liked: bool) -> None:
        """按目标状态收藏/取消收藏（搜索结果页点红心用）。"""
        if self.session_state is not None and not self.session_state.logged_in:
            self.error.emit("请先登录后再收藏")
            return
        uid = self.session_state.uid if self.session_state else self.config.uid
        if not uid:
            self.error.emit("请先登录后再收藏")
            return

        def work() -> tuple[int, bool, Exception | None]:
            try:
                self.api.like(target.id, liked, uid)
            except Exception as exc:  # noqa: BLE001
                return target.id, liked, exc
            return target.id, liked, None

        task = AsyncTask(work)
        task.done.connect(self._on_like_done)
        task.start()

    def _on_like_done(self, payload: tuple) -> None:
        song_id, liked, exc = payload
        if exc is not None:
            self.error.emit(f"收藏失败：{getattr(exc, 'user_message', exc)}")
            return
        self._liked[song_id] = liked
        self.like_changed.emit(song_id, liked)
        self.notice.emit("已加入我喜欢的音乐" if liked else "已取消收藏")

    def set_like_checker(self, checker: Callable[[int], bool | None] | None) -> None:
        self.like_checker = checker

    # ============================================================ 播放实现
    def _start_current(self) -> None:
        song = self.current_song
        if song is None:
            return
        # 上一首的临时录制文件（若有）先丢弃，避免留下半首歌
        self._finish_record("stop")
        self._duration_ms = song.duration_ms
        self._position_ms = 0
        self._seeked = False
        self._retry_used = False
        self.song_changed.emit(song)
        self.progress.emit(0, self._duration_ms)

        cached = self.cache.audio_lookup(song.id)
        if cached is not None:
            _log.info("缓存命中，直接播放本地文件：%s", cached.name)
            self._failed_indexes.clear()
            self.cache_event.emit(song.id, "hit")
            self.source_changed.emit(song.id, "", 0, "cache")
            self.player.load(str(cached))
            self._playing = True
            self.playing_changed.emit(True)
            self._prefetch_next()
            return

        prefetched = self._prefetch.pop(song.id, None)
        if prefetched is not None and prefetched.playable:
            _log.info("使用预取的播放地址：%s", song.name)
            self._play_url(song, prefetched)
            return
        self._fetch_and_play(song)

    def _fetch_and_play(self, song: Song) -> None:
        """取流（工作线程）→ 播放（主线程）。

        注意：``work`` 把歌曲一起返回，回调连到**绑定方法** ``_on_url_fetched``。
        这样 Qt 会排队到主线程执行；若图省事连 lambda，PySide6 会在工作线程里直接调用，
        触碰播放器/UI 属于未定义行为（见 util/async_task.py 模块头坑 2）。
        """
        self._loading = True
        _log.info("取流中：%s（id=%s）", song.name, song.id)

        def work() -> tuple[Song, SongUrl | None, Exception | None]:
            try:
                return song, self.api.song_url_best(song.id), None
            except Exception as exc:  # noqa: BLE001 - 统一交给主线程处理
                return song, None, exc

        task = AsyncTask(work)
        task.done.connect(self._on_url_fetched)
        task.start()

    def _on_url_fetched(self, payload: tuple) -> None:
        """取流结果回到主线程后统一处理。"""
        song, result, exc = payload
        self._loading = False
        if exc is not None:
            _log.info("取流失效：%s（%s）", song.name, exc)
            self.error.emit(f"获取播放地址失败：{getattr(exc, 'user_message', exc)}")
            self._auto_next_after_failure(song)
            return
        assert result is not None
        _log.info("取流结果：%s playable=%s code=%s level=%s", song.name, result.playable, result.code, result.level)
        if song.id != (self.current_song.id if self.current_song else -1):
            return  # 期间已切歌，丢弃
        if not result.playable:
            self.error.emit(f"《{song.name}》无法播放：{result.message or '无版权或需要会员'}")
            self._auto_next_after_failure(song)
            return
        self._play_url(song, result)

    def _play_url(self, song: Song, result: SongUrl) -> None:
        record_to = None
        if self.config.cache_audio_enabled and result.size > 0:
            ext = result.type or "mp3"
            part = self.cache.audio_part_path(song.id, result.level, ext)
            self.cache.discard(part)  # 清掉同名残留
            record_to = part
            self._record = (part, song.id, result.level, ext, result.size)
        else:
            self._record = None
        _log.info("开始播放：%s（%s %dkbps，%s）", song.name, result.level or "?", result.br // 1000, "缓存中" if record_to else "不缓存")
        self._failed_indexes.clear()
        self.source_changed.emit(song.id, result.level, result.br, "cache-part" if record_to else "stream")
        self.player.load(result.url or "", record_to=record_to)
        self._playing = True
        self.playing_changed.emit(True)
        self._prefetch_next()

    def _prefetch_next(self) -> None:
        """预取下一首的播放地址（只取 URL，不加载，降低切换延迟）。"""
        index = self.queue.next_index()
        if index is None or index == self.queue.index:
            return
        song = self.queue.items[index] if 0 <= index < len(self.queue) else None
        if song is None or song.id in self._prefetch:
            return
        if self.cache.audio_lookup(song.id) is not None:
            return  # 下一首已在缓存里，无需预取

        def work() -> tuple[int, SongUrl | None]:
            try:
                result: SongUrl | None = self.api.song_url_best(song.id)
            except Exception:  # noqa: BLE001 - 预取失败无需打扰用户
                result = None
            return song.id, (result if result and result.playable else None)

        task = AsyncTask(work)
        task.done.connect(self._on_prefetched)
        task.start()

    def _on_prefetched(self, payload: tuple) -> None:
        song_id, result = payload
        self._prefetch[song_id] = result  # type: ignore[assignment]
        if result is not None:
            _log.info("已预取地址：song=%s level=%s", song_id, result.level)

    def _auto_next_after_failure(self, failed: Song | None = None) -> None:
        """播放失败后的兜底：往后找一首**这次没失败过**的；都试过了就停下并提示。

        ``failed`` 是失败的那首歌。取流是异步的，回包可能比用户切歌晚到：
        那不是当前这首的失败，直接丢掉（否则会把正在正常播放的这首算成失败）。

        order 之外的三种模式都能绕回同一个索引（单曲循环/列表循环/随机），
        所以不能只看“next_index() 不是 None”就继续：所有候选都不可播时会原地重试
        （真机量到 1 秒 271 次 error 信号 + 272 次 song_changed）。
        """
        current = self.current_song
        if failed is not None and (current is None or failed.id != current.id):
            _log.debug("丢弃迟到的失败回包：song=%s（当前 %s）", failed.id, current.id if current else None)
            return
        if 0 <= self.queue.index < len(self.queue):
            self._failed_indexes.add(self.queue.index)
        index = self.queue.next_index()
        candidate = index is not None and 0 <= index < len(self.queue)
        if not candidate:
            self._playing = False
            self.playing_changed.emit(False)
            return
        if index in self._failed_indexes:
            self._playing = False
            self.playing_changed.emit(False)
            # 单曲失败的具体原因已经由调用方报过，这里只补一句“整列都不行”，不再弹第二个错
            self.notice.emit("列表里没有可播放的歌曲")
            return
        self.next()

    # ============================================================ mpv 回调
    def _on_player_ready(self) -> None:
        self.player.set_volume(self.config.volume)
        self.player.set_mute(self.config.muted)

    def _on_player_failed(self, message: str) -> None:
        _log.error("播放器不可用：%s", message)
        self.error.emit(message)

    def _on_position(self, seconds: float) -> None:
        self._position_ms = int(seconds * 1000)
        self.progress.emit(self._position_ms, self._duration_ms)

    def _on_duration(self, seconds: float) -> None:
        if seconds > 0:
            self._duration_ms = int(seconds * 1000)
            self.progress.emit(-1, self._duration_ms)  # -1 表示"只更新时长"

    def _on_paused(self, paused: bool) -> None:
        self._playing = not paused
        self.playing_changed.emit(self._playing)

    def _on_ended(self, reason: str) -> None:
        """一首歌结束（eof 自然结束 / stop 被切 / error 出错 / quit 退出）。"""
        song = self.current_song
        heard_all = self._heard_all()
        self._finish_record(reason)
        if reason == "eof" or (reason == "error" and heard_all):
            # 注意：网络流自然播完时 mpv 也可能报 error（尾包丢失），
            # 位置已经到底，此时应当像 eof 一样继续下一首，而不是重头再播一遍。
            self._playing = False
            self.playing_changed.emit(False)
            self.next()
        elif reason == "error":
            if song is not None and not self._retry_used:
                self._retry_used = True
                _log.info("播放出错，重取地址后重试一次：%s", song.name)
                self.notice.emit("播放中断，正在重试…")
                self._prefetch.pop(song.id, None)
                self._fetch_and_play(song)
            else:
                self.error.emit("播放失败（地址可能已过期）")
                self._auto_next_after_failure()
        # stop / quit：用户主动操作，不自动跳下一首

    def _heard_all(self) -> bool:
        """这一首是否已经听到底（位置判据，见 _finish_record）。"""
        return self._duration_ms > 0 and self._position_ms >= self._duration_ms - END_GRACE_MS

    # ============================================================ 缓存收尾
    def _finish_record(self, reason: str) -> None:
        """结束一次录制并决定是否提交缓存。

        关键：**以文件是否完整为准**，不依赖 mpv 的 ``end-file`` reason。
        实测（见 docs/acceptance.md）网络流自然播完时 mpv 也可能报 ``error``；
        而真正该丢弃的半首歌会被大小校验（±1%）挡下来，所以这里统一尝试提交。
        用户拖动过进度时一律丢弃（拖动会让录制内容缺段，不可信）。
        """
        record = self._record
        self._record = None
        if record is None:
            return
        part, song_id, level, ext, expected = record
        heard_all = self._heard_all()
        _log.info(
            "录制结束：song=%s reason=%s heard_all=%s seeked=%s 期望=%s 字节（位置 %dms/%dms）",
            song_id, reason, heard_all, self._seeked, expected, self._position_ms, self._duration_ms,
        )
        if not self._seeked:
            # 判据是「**听完整首了吗**」而不是 mpv 的 end-file reason：
            # 实测网络流自然播完时 mpv 也可能报 error（尾包丢失），字节数会差 1~2%，
            # 但播放位置已经到底；反之真被切断的歌位置差得远，位置判据能挡掉。
            final = self.cache.commit_audio(part, song_id, level, ext, expected, played_to_end=heard_all)
            self.cache_event.emit(song_id, "stored" if final else "discarded")
        else:
            self.cache.discard(part)
            self.cache_event.emit(song_id, "discarded")
            _log.info("播放中拖动过进度，不缓存本次音频：%s", song_id)
        # 顺手清掉竞态孤儿（此刻没有正在录制的文件，全部可清）
        self._sweep_parts()

    def _sweep_parts(self) -> None:
        """清掉不属于当前录制的临时文件（保留 ``_record`` 里那个）。"""
        keep = self._record[0] if self._record else None
        removed = self.cache.sweep_parts(keep=keep)
        if removed:
            _log.info("清扫孤儿临时缓存：%d 个", removed)

    # ============================================================ 杂项
    def shutdown(self) -> None:
        """退出：清理临时缓存文件与 mpv 进程（AC-6.3）。"""
        self._sweeper.stop()
        self._finish_record("stop")
        self.cache.sweep_parts()
        self.player.shutdown()

    @staticmethod
    def mpv_available() -> bool:
        return mpv_available()

    def describe_state(self) -> str:
        """一次性状态串（日志/调试）。"""
        song = self.current_song
        if song is None:
            return "空闲"
        return f"{song.name} - {song.artist_text} [{format_duration(self._duration_ms)}] mode={self.queue.mode}"
