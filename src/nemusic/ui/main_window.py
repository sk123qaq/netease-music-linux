"""主窗口：左导航 + 中央页面栈 + 底部播放条（SPEC §3 / AC-3.1 起逐步填充）。

M1 只实现登录页；「搜索 / 我喜欢的音乐 / 设置」先放占位页，后续里程碑替换。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_TITLE
from ..config import Config
from ..model.types import Song
from ..net.api import Api
from ..net import endpoints as ep
from ..player.controller import PlaybackController
from ..state.cache import CacheManager
from ..state.cover_loader import CoverLoader
from ..state.library import LibraryState
from ..state.lyrics import LyricState
from ..state.search_history import SearchHistory
from ..state.search_state import SearchState
from ..state.session_state import SessionState
from ..util import icons, log
from ..util.async_task import AsyncTask
from .background_view import BackgroundView
from .entity_view import KIND_LABELS, EntityView
from .login_view import LoginView
from .lyrics_view import LyricsView
from .player_bar import PlayerBar
from .playlist_view import PlaylistView
from .search_view import SearchView
from .settings_view import SettingsView

_log = log.get("ui.main_window")

#: 导航项：key → (标题, 图标名, 里程碑说明)
NAV_ITEMS = [
    ("login", "登录", "music", "扫码登录与账号信息"),
    ("library", "我喜欢的音乐", "heart", "完整列表、顺序播放、点播"),
    ("search", "搜索", "search", "单曲/歌手/专辑/歌单 四 tab、历史、滚动加载"),
    ("settings", "设置", "settings", "缓存占用与清理、缓存上限、音质策略"),
    ("background", "外观", "image", "背景预设 / 上传自己的图片 / 浅色深色主题"),
]
NAV_WIDTH = 190


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self, api: Api, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.config = config
        self.session_state = SessionState(api, config, self)
        self.cache = CacheManager(config)
        self.controller = PlaybackController(api, config, self.cache, self.session_state, self)
        self.history = SearchHistory()
        self.lyrics = LyricState(api, self.cache)
        self.search_state = SearchState(api, self, history=self.history)
        self.library = LibraryState(api, self.session_state, self)

        self.setWindowTitle(APP_TITLE)
        self.resize(*config.window_geometry[2:4])
        self._build()
        self._wire()
        self._restore_geometry()

        # 启动即尝试复用 cookie，避免重复扫码（AC-1.3）
        self._restore_session_async()

    # ============================================================ 构建
    def _build(self) -> None:
        central = QWidget()
        # 必须起名：否则 `QWidget { background: ... }` 会给它画一层不透明底色，
        # 把 QMainWindow 上的背景渐变/图片整个盖掉（用户反馈"换了没反应"）。
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左导航
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(NAV_WIDTH)
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setFocusPolicy(Qt.NoFocus)
        for key, title, icon_name, tip in NAV_ITEMS:
            item = QListWidgetItem(icons.icon(icon_name, "#d0d0d0", 18), title)
            item.setData(Qt.UserRole, key)
            item.setToolTip(tip)
            item.setSizeHint(QSize(0, 42))
            self.nav.addItem(item)
        root.addWidget(self.nav)

        # 右侧：页面栈 + 播放条
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")
        self.pages: dict[str, int] = {}

        self.login_view = LoginView(self.api, self.session_state)
        self._add_page("login", self.login_view)
        self.search_view = SearchView(self.search_state)
        self._add_page("search", self.search_view)
        self.playlist_view = PlaylistView(self.library)
        self._add_page("library", self.playlist_view)
        self.entity_view = EntityView()
        self._add_page("entity", self.entity_view)
        self.lyrics_view = LyricsView(self.config)
        self._add_page("lyrics", self.lyrics_view)
        self.settings_view = SettingsView(self.cache, self.config)
        self._add_page("settings", self.settings_view)
        self.background_view = BackgroundView(self.config)
        self._add_page("background", self.background_view)

        right.addWidget(self.stack, 1)

        self.player_bar = PlayerBar(self.config)
        right.addWidget(self.player_bar)
        root.addLayout(right, 1)

        self.status = self.statusBar()
        self.status.showMessage("未登录")

        #: 封面加载器（子线程下载 + 磁盘缓存；token 用歌曲 id 防串台）
        self.cover_loader = CoverLoader(self.cache)
        self._cover_token: object = None

    def _add_page(self, key: str, widget: QWidget) -> None:
        # 同上：页面根控件也要透明，否则背景透不上来
        widget.setObjectName("page")
        self.pages[key] = self.stack.addWidget(widget)

    def _wire(self) -> None:
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.login_view.login_succeeded.connect(self._on_login_succeeded)
        self.session_state.account_changed.connect(self._on_account_changed)
        self.session_state.login_changed.connect(self._on_login_changed)
        self._wire_player()
        self._wire_library()
        self._wire_entity()
        self._wire_lyrics()
        self._wire_settings()
        self._wire_background()
        self.nav.setCurrentRow(0)

    def _wire_player(self) -> None:
        """播放条 ↔ 播放控制器（信号契约见 player_bar 模块头）。"""
        bar, ctl = self.player_bar, self.controller
        bar.play_pause_clicked.connect(ctl.toggle_pause)
        bar.prev_clicked.connect(ctl.prev)
        bar.next_clicked.connect(ctl.next)
        bar.seek_requested.connect(ctl.seek)
        bar.volume_changed.connect(ctl.set_volume)
        bar.mute_toggled.connect(ctl.set_mute)
        bar.mode_changed.connect(ctl.set_mode)
        bar.like_clicked.connect(lambda: ctl.toggle_like())
        bar.cover_clicked.connect(self._open_lyrics)
        bar.lyrics_clicked.connect(self._open_lyrics)

        ctl.song_changed.connect(self._on_song_changed)
        ctl.playing_changed.connect(bar.set_playing)
        ctl.progress.connect(self._on_progress)
        ctl.like_changed.connect(self._on_like_changed)
        ctl.error.connect(lambda msg: self.notice(msg, warn=True))
        ctl.notice.connect(self.notice)
        ctl.mode_changed.connect(lambda _m: self.config.save())
        ctl.set_mode(self.config.play_mode)
        bar.set_play_mode(self.config.play_mode)
        bar.set_volume(self.config.volume, self.config.muted)
        self._wire_search()

    def _wire_library(self) -> None:
        """我喜欢的音乐 ↔ 播放控制器（AC-3.2/3.3/3.4）。"""
        view = self.playlist_view
        view.play_requested.connect(lambda items, index: self.controller.play_songs(items, index, context="我喜欢的音乐"))
        view.like_toggled.connect(lambda song, desired: self.controller.set_like(song, desired))
        view.refresh_requested.connect(lambda: self.library.reload(force=True))
        self.library.like_changed.connect(self._on_like_changed)
        self.controller.like_changed.connect(view.set_liked)
        # library 是“已收藏”的唯一权威：播放条/搜索页/详情页的 ♥ 都问它，
        # 收藏结果回包后再把状态写回 library（否则取消收藏后重新渲染又变实心）。
        self.controller.set_like_checker(self.library.is_liked)
        self.controller.like_changed.connect(self.library.set_liked)

    def _wire_entity(self) -> None:
        """专辑/歌单/歌手详情页（AC-2.4）。"""
        view = self.entity_view
        view.play_requested.connect(lambda items, index: self.controller.play_songs(items, index, context=view.lbl_title.text()))
        view.like_toggled.connect(lambda song, desired: self.controller.set_like(song, desired))
        view.play_all_requested.connect(lambda: self.controller.play_songs(view.songs(), 0, context=view.lbl_title.text()))
        view.back_requested.connect(lambda: self.show_page("search"))
        self.controller.like_changed.connect(view.set_liked)
        view.set_like_checker(self.library.is_liked)

    def _wire_search(self) -> None:
        """搜索页 ↔ 播放控制器。"""
        view = self.search_view
        view.play_requested.connect(self._play_search_result)
        view.like_toggled.connect(lambda song, desired: self.controller.set_like(song, desired))
        view.open_entity.connect(self._open_entity)
        self.controller.like_changed.connect(self._on_like_changed)
        view.set_like_checker(self.library.is_liked)

    def _play_search_result(self, items: list, index: int) -> None:
        keyword = self.search_state.keyword or ""
        self.controller.play_songs(items, index, context=f"搜索「{keyword}」")

    def _open_entity(self, kind: str, entity_id: int) -> None:
        """双击歌手/专辑/歌单行 → 拉曲目 → 进详情页并播放第一首（AC-2.4）。"""
        if kind not in KIND_LABELS:
            return
        label = KIND_LABELS[kind]
        self.notice(f"正在载入{label}曲目…")
        self._pending_entity = (kind, entity_id)

        def work() -> tuple[str, list, str, str]:
            try:
                if kind == "artist":
                    songs = self.api.artist_top_songs(entity_id)
                    return kind, songs, "", ""
                if kind == "album":
                    album, songs = self.api.album_songs(entity_id)
                    return kind, songs, album.name, album.artist
                detail = self.api.playlist_detail(entity_id, limit=1000)
                songs = list(detail.tracks)
                known = {s.id for s in songs}
                missing = [i for i in detail.track_ids if i not in known]
                for start in range(0, min(len(missing), 1000), 300):
                    batch = missing[start : start + 300]
                    if batch:
                        songs.extend(self.api.song_detail(batch))
                order = {sid: i for i, sid in enumerate(detail.track_ids)}
                songs.sort(key=lambda s: order.get(s.id, len(order)))
                return kind, songs, detail.name, detail.creator
            except Exception as exc:  # noqa: BLE001 - 交给主线程统一提示
                return kind, exc, "", ""  # type: ignore[list-item]

        task = AsyncTask(work)
        task.done.connect(self._on_entity_loaded)
        task.start()

    def _on_entity_loaded(self, payload: tuple) -> None:
        kind, songs, title, subtitle = payload
        if isinstance(songs, Exception):
            self.notice(f"载入失败：{getattr(songs, 'user_message', songs)}", warn=True)
            return
        if not songs:
            self.notice("这个列表没有可播放的歌曲", warn=True)
            return
        kind, entity_id = getattr(self, "_pending_entity", (kind, 0))
        count = f"{len(songs)} 首"
        sub = " / ".join(p for p in (subtitle, count) if p)
        self.entity_view.set_entity(kind, entity_id, title or KIND_LABELS.get(kind, "详情"), sub, songs)
        self.show_page("entity")
        self.notice(f"已载入 {count}")
        self.controller.play_songs(songs, 0, context=title or KIND_LABELS.get(kind, "详情"))

    def _wire_lyrics(self) -> None:
        """歌词页 ↔ 歌词状态 / 播放进度（AC-4.4/4.5）。"""
        view, lyrics = self.lyrics_view, self.lyrics
        view.back_requested.connect(self._on_lyrics_back)
        view.seek_requested.connect(self.controller.seek)
        lyrics.loaded.connect(self._on_lyric_loaded)
        self.cover_loader.ready.connect(self._on_cover_ready)

    def _wire_settings(self) -> None:
        """设置页 ↔ 缓存管理器 / 登录态（AC-5.4~5.6）。"""
        view = self.settings_view
        view.logout_requested.connect(self._on_logout_requested)
        view.limit_changed.connect(self._on_cache_limit_changed)
        # 账号信息随登录态刷新，别让设置页一直显示"未登录"
        self.session_state.account_changed.connect(view.set_account)
        self.session_state.logged_out.connect(self._on_logged_out)

    def _wire_background(self) -> None:
        """外观页 → 重刷全局样式表。"""
        self.background_view.appearance_changed.connect(self._on_appearance_changed)

    def _on_appearance_changed(self, theme_name: str, background: str) -> None:
        """换主题/背景：整表重刷。QSS 是全局的，所以装在 QApplication 上。"""
        from . import theme as theme_mod

        backdrop = theme_mod.backdrop_css_for(
            background, str(self.config.background_path) if self.config.background_path else None)
        # 自定义照片可能很花，纱罩浓一档；预设本身和主题配套，淡一点就够
        veil = theme_mod.VEIL_CUSTOM if background.startswith("custom:") else theme_mod.VEIL_PRESET
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme_mod.build_stylesheet(theme_name, backdrop, veil))
        # QSS 会更新 palette，但**已创建的表格 item 的 foreground 不会自动跟随**，
        # 必须显式重新上色，否则切到浅色后表格文字还是深色那套（实测踩过）。
        for table in self._all_tables():
            table.refresh_colors()
        _log.info("外观已切换：theme=%s background=%s", theme_name, background)

    def _all_tables(self):
        """所有含 QTableWidgetItem 的表格（切主题后要重新上色的）。"""
        tables = [self.playlist_view.table, self.entity_view.table]
        tables.extend(self.search_view._tables.values())
        return [t for t in tables if t is not None]

    def _on_cache_limit_changed(self, limit_bytes: int) -> None:
        self.notice(f"音频缓存上限已设为 {CacheManager.format_size(limit_bytes)}")

    def _on_logout_requested(self) -> None:
        """退出登录：cookie 清理在子线程做，主线程只切页面。"""
        answer = QMessageBox.question(self, "退出登录", "确定要退出当前账号吗？\n（本地缓存与登录状态会清除）")
        if answer != QMessageBox.Yes:
            return
        self.notice("正在退出登录…")
        # logout_async 内部已在子线程清 cookie；成功后走 login_changed 切回登录页
        self.session_state.logout_async()

    def _on_lyrics_back(self) -> None:
        """返回到进入歌词页之前的那个导航页。"""
        target = self.config.last_page
        if target in self.pages and target != "lyrics":
            self.show_page(target)
        else:
            self.show_page("library")

    def _on_lyric_loaded(self, song_id, doc) -> None:
        current = self.controller.current_song
        if current is None or current.id != song_id:
            return  # 已经切歌了，丢弃
        self.lyrics_view.set_lyric(doc)
        # 立刻按当前进度对齐一次，避免歌词比进度慢半句
        self.lyrics_view.set_position(self.controller.position_ms)

    def _on_song_changed(self, song) -> None:
        if song is None:
            self.player_bar.set_song(None)
            return
        self.playlist_view.highlight(song.id)
        self.entity_view.highlight(song.id)
        self.player_bar.set_song(song)
        self.player_bar.set_starred(self.controller.is_liked(song.id))
        self.setWindowTitle(f"{song.name} - {song.artist_text} · {APP_TITLE}")
        self._cover_token = song.id
        self.cover_loader.request(song.cover, song.id)
        self.lyrics_view.set_song(song)
        self.lyrics.request(song.id)

    def _on_cover_ready(self, token, pixmap) -> None:
        """封面就绪。token 不是当前歌曲就丢掉，避免连点切歌时串图。"""
        if token != self._cover_token:
            return
        self.player_bar.set_cover(pixmap)
        self.lyrics_view.set_cover(pixmap)

    def _open_lyrics(self) -> None:
        """点封面/「词」按钮进歌词页（AC-4.4）。"""
        if self.controller.current_song is None:
            self.notice("还没有在播放的歌曲")
            return
        self.show_page("lyrics")

    def _on_progress(self, position_ms: int, duration_ms: int) -> None:
        if position_ms < 0:  # 只更新时长
            self.player_bar.set_position(self.player_bar.sld_progress.value(), duration_ms)
        else:
            self.player_bar.set_position(position_ms, duration_ms)
            self.lyrics_view.set_position(position_ms, duration_ms)

    def _on_like_changed(self, song_id: int, liked: bool) -> None:
        current = self.controller.current_song
        if current is not None and current.id == song_id:
            self.player_bar.set_starred(liked)
        self.search_view.set_liked(song_id, liked)

    def notice(self, message: str, warn: bool = False) -> None:
        """统一的状态栏提示（warn 时写日志）。"""
        if warn:
            _log.warning("提示：%s", message)
        self.status.showMessage(message, 8000 if warn else 5000)

    def _restore_geometry(self) -> None:
        try:
            x, y, w, h = self.config.window_geometry
            self.setGeometry(int(x), int(y), int(w), int(h))
        except (TypeError, ValueError):
            self.resize(1180, 720)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """退出前保存窗口几何并清理播放器（SPEC §5 / AC-6.3）。"""
        geo = self.geometry()
        self.config.window_geometry = [geo.x(), geo.y(), geo.width(), geo.height()]
        self.config.save()
        try:
            self.controller.shutdown()
        except Exception as exc:  # noqa: BLE001 - 退出阶段不抛异常
            _log.warning("播放器清理失败：%s", exc)
        super().closeEvent(event)

    # ============================================================ 槽
    def _on_nav_changed(self, row: int) -> None:
        item = self.nav.item(row)
        if item is None:
            return
        key = item.data(Qt.UserRole)
        self.show_page(key)

    def show_page(self, key: str) -> None:
        index = self.pages.get(key)
        if index is None:
            return
        self.stack.setCurrentIndex(index)
        self.config.last_page = key
        rows = [i for i, it in enumerate(self._nav_items()) if it.data(Qt.UserRole) == key]
        if rows:
            # 导航页：同步左侧选中态
            if self.nav.currentRow() != rows[0]:
                self.nav.setCurrentRow(rows[0])
        else:
            # 非导航页（专辑/歌单详情）：不能默认跳第 0 行，
            # 否则 currentRowChanged 会把页面切回"登录"（真机踩过，见 acceptance.md）。
            # 取消选中即可：item(-1) 为 None，_on_nav_changed 会直接返回。
            self.nav.setCurrentRow(-1)

    def _nav_items(self):
        return [self.nav.item(i) for i in range(self.nav.count())]

    def _on_login_succeeded(self, account) -> None:
        self.status.showMessage(f"已登录：{account.nickname or '用户'}（UID {account.uid}）")
        _log.info("登录成功：uid=%s nickname=%s", account.uid, account.nickname)
        self.show_page("library")
        self.library.reload()  # 登录后立刻拉收藏列表

    def _on_account_changed(self, account) -> None:
        if account is None:
            self.status.showMessage("未登录")
        else:
            self.status.showMessage(f"已登录：{account.nickname or '用户'}（UID {account.uid}）")

    def _on_login_changed(self, logged_in: bool) -> None:
        """cookie 失效/重启恢复失败也会走到这里，**不清用户数据**（用户明确要求）。"""
        if not logged_in:
            self.show_page("login")

    def _on_logged_out(self) -> None:
        """用户主动退出登录：清掉上一位用户的收藏列表与页面残留，别串号。"""
        self.library.clear()
        self.playlist_view.set_songs([])
        self.entity_view.set_songs([])
        self.search_view.clear_results()
        self.controller.stop()
        self.player_bar.set_song(None)
        self.lyrics_view.set_song(None)
        self.show_page("login")
        self.notice("已退出登录")

    # ============================================================ 启动恢复
    def _restore_session_async(self) -> None:
        if not self.api.session.logged_in:
            self.login_view.set_message("尚未登录（登录后可播放与收藏）")
            return
        self.login_view.set_message("正在恢复上次登录状态…")
        # 子线程只做请求，登录态落地与信号都在主线程（SPEC §4 规则 1）
        task = AsyncTask(self.session_state.fetch_account)
        task.done.connect(self._on_restore_done)
        task.start()

    def _on_restore_done(self, payload: tuple) -> None:
        ok = self.session_state.apply_fetched(payload)
        if ok:
            self.status.showMessage(f"已登录：{self.session_state.nickname}（UID {self.session_state.uid}）")
            self.show_page("library")
            self.library.reload()
        else:
            self.login_view.set_message("登录状态已失效，请重新扫码登录", warn=True)
            self.show_page("login")
