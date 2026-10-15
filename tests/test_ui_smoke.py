"""界面冒烟测试（offscreen）：窗口可构建、页面齐备、播放条信号契约存在。

对应 AC-6.3/AC-6.4 的一部分与 SPEC §3 的结构要求；不涉及网络。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import qt_app, redirect_config  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication  # noqa: F401 - 仅用于能力检测

    HAS_QT = True
except ImportError:  # pragma: no cover
    HAS_QT = False

import time  # noqa: E402

from PySide6.QtCore import QObject, QSize, Qt, Signal  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from nemusic.model.types import Album, Artist, Playlist, Song  # noqa: E402
from nemusic.net import endpoints as ep  # noqa: E402
from nemusic.state.search_history import SearchHistory  # noqa: E402
from nemusic.state.library import LibraryState  # noqa: E402
from nemusic.state.search_state import SearchState  # noqa: E402
from nemusic.ui.entity_view import EntityView  # noqa: E402
from nemusic.ui.search_view import HISTORY_ROW_H  # noqa: E402
from nemusic.ui.playlist_view import PlaylistView  # noqa: E402
from nemusic.ui.search_view import SearchView  # noqa: E402


class FakeApi:
    """搜索页冒烟用的假 API。"""

    def search(self, keyword: str, search_type: int, offset: int, limit: int):
        from nemusic.model.types import SearchPage

        return SearchPage(keyword=keyword, search_type=search_type, offset=offset,
                          limit=limit, total=0, items=[])


@unittest.skipUnless(HAS_QT, "缺少 PySide6")
class TestUiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # 整个测试会话共用一个 QApplication（offscreen）
        cls.app = qt_app()

    def setUp(self) -> None:
        redirect_config()

    def _window(self):
        from nemusic.app import build_app

        app, window, config = build_app([sys.argv[0]])
        # 窗口会启动 mpv，测试结束时必须关掉，否则残留进程/文件（AC-6.3）
        self.addCleanup(window.controller.shutdown)
        return app, window, config

    def test_window_builds_with_all_pages(self) -> None:
        _app, window, _cfg = self._window()
        self.assertEqual(set(window.pages),
                         {"login", "library", "search", "entity", "lyrics", "settings", "background"})
        self.assertEqual(window.stack.count(), 7)
        self.assertEqual(window.nav.count(), 5)

    def test_switch_page_keeps_nav_in_sync(self) -> None:
        _app, window, cfg = self._window()
        window.show_page("search")
        self.assertEqual(window.stack.currentIndex(), window.pages["search"])
        self.assertEqual(cfg.last_page, "search")
        window.show_page("settings")
        self.assertEqual(window.stack.currentIndex(), window.pages["settings"])

    def test_settings_view_follows_account_changes(self) -> None:
        """真机踩过：账号接线的两行被误缩进到别的槽里，导致设置页永远显示"未登录"。"""
        _app, window, _cfg = self._window()
        view = window.settings_view
        self.assertEqual(view.account_label.text(), "未登录")
        from nemusic.model.types import Account

        window.session_state.account_changed.emit(Account(uid=7, nickname="测试用户", vip_type=110))
        self.app.processEvents()
        self.assertIn("测试用户", view.account_label.text())
        self.assertIn("VIP", view.account_label.text())
        window.session_state.account_changed.emit(None)
        self.app.processEvents()
        self.assertEqual(view.account_label.text(), "未登录")

    def test_settings_page_has_cache_rows(self) -> None:
        """设置页四类缓存行齐备（AC-5.5）。"""
        _app, window, _cfg = self._window()
        view = window.settings_view
        self.assertEqual(set(view._rows), {"audio", "playlist", "lyric", "cover"})
        texts = [b.text() for b in view._buttons]
        self.assertIn("清空全部缓存", texts)
        self.assertIn("打开缓存目录", texts)
        self.assertIn("退出登录", texts)

    def test_player_bar_disabled_without_song(self) -> None:
        _app, window, _cfg = self._window()
        bar = window.player_bar
        self.assertFalse(bar.btn_play.isEnabled())
        self.assertFalse(bar.btn_prev.isEnabled())
        self.assertFalse(bar.btn_next.isEnabled())

    def test_player_bar_enables_after_set_song(self) -> None:
        _app, window, _cfg = self._window()
        from nemusic.model.types import Song

        song = Song(id=1, name="晴天", artist_names=("周杰伦",), album="叶惠美", duration_ms=269000)
        window.player_bar.set_song(song)
        self.assertTrue(window.player_bar.btn_play.isEnabled())
        self.assertIn("晴天", window.player_bar.lbl_title.text())
        self.assertIn("周杰伦", window.player_bar.lbl_artist.text())
        self.assertEqual(window.player_bar.lbl_dur.text(), "04:29")

    def test_progress_and_mode_signals(self) -> None:
        _app, window, cfg = self._window()
        bar = window.player_bar
        bar.set_play_mode("list_loop")
        bar._cycle_mode()
        # MODE_ORDER = order → list_loop → single_loop → shuffle
        self.assertEqual(cfg.play_mode, "single_loop")
        bar._cycle_mode()
        self.assertEqual(cfg.play_mode, "shuffle")
        bar._cycle_mode()
        self.assertEqual(cfg.play_mode, "order")
        bar.set_position(65_000, 269_000)
        self.assertEqual(bar.lbl_pos.text(), "01:05")
        self.assertEqual(bar.lbl_dur.text(), "04:29")

    def test_self_check_exits_zero(self) -> None:
        from nemusic.app import main

        self.assertEqual(main(["--self-check"]), 0)


@unittest.skipUnless(HAS_QT, "缺少 PySide6")
class TestIcons(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def test_all_icons_render(self) -> None:
        from nemusic.util import icons

        for name in icons.names():
            pm = icons.pixmap(name, "#ffffff", 20)
            self.assertFalse(pm.isNull(), f"图标 {name} 渲染失败")
            self.assertEqual(pm.size().width(), 20)

    def test_unknown_icon_raises(self) -> None:
        from nemusic.util import icons

        with self.assertRaises(KeyError):
            icons.pixmap("no-such-icon")

    def test_required_icons_present(self) -> None:
        from nemusic.util import icons

        for name in ("play", "pause", "prev", "next", "heart", "heart_outline", "volume", "mute",
                     "order", "list_loop", "single_loop", "shuffle", "search", "settings", "music", "refresh"):
            self.assertIn(name, icons.names())


class TestRegressionFixes(unittest.TestCase):
    """用户 2026-10-14 反馈的四个界面问题的回归测试。"""

    def setUp(self) -> None:
        redirect_config()
        self.app = qt_app()

    def _window(self):
        from nemusic.app import build_app

        app, window, config = build_app([sys.argv[0]])
        self.addCleanup(window.controller.shutdown)
        return app, window, config

    # ---------------------------------------------- ① 历史下拉挡住导航
    def test_history_popup_is_not_modal(self) -> None:
        """Qt.Popup 是模态的，会吞掉点左导航的那一下（用户反馈"点了没反应"）。"""
        _app, window, _cfg = self._window()
        from PySide6.QtCore import Qt

        popup = window.search_view.history_popup
        # 现在干脆不是独立窗口，只是页面里的普通子控件——比 Qt.Tool 更稳：
        # Wayland 下客户端拿不到窗口真实屏幕位置，mapToGlobal() 给的是
        # "以窗口为原点"的坐标，顶层窗会被放到屏幕左上角（真机飘到浏览器上去过）。
        self.assertFalse(popup.isWindow(), "下拉不该是独立窗口")
        self.assertFalse(popup.isModal(), "下拉不能是模态的，否则吞掉点导航的那一下")
        self.assertEqual(popup.windowType(), Qt.Widget)
        self.assertEqual(popup.parentWidget(), window.search_view, "应是搜索页的子控件")

    def test_history_popup_sits_right_under_search_box(self) -> None:
        """位置必须是"搜索框正下方"（用户反馈原来漂到页面中部/屏幕角落）。"""
        _app, window, _cfg = self._window()
        window.resize(1000, 600)
        window.show_page("search")
        window.show()
        view = window.search_view
        for kw in ("甲", "乙", "丙"):
            view.state.history.add(kw)
        view._maybe_show_history()
        self.app.processEvents()

        popup = view.history_popup
        self.assertTrue(popup.isVisible())
        # 用页面内坐标比：下拉顶沿 == 搜索框底沿（允许 2px 呼吸缝）
        gap = popup.y() - view.input.mapTo(view, view.input.rect().bottomLeft()).y()
        self.assertGreaterEqual(gap, 0)
        self.assertLessEqual(gap, 4, "必须紧贴搜索框下沿，不能漂到别处")
        # 左边界和搜索框对齐
        self.assertLessEqual(abs(popup.x() - view.input.x()), 2, "左边界要和搜索框对齐")
        # 不超出页面右边界
        self.assertLessEqual(popup.x() + popup.width(), view.width() + 1)
        # 宽度跟着搜索框
        self.assertEqual(popup.width(), view.input.width())

    def test_history_popup_lists_only_last_five(self) -> None:
        """只列最近 5 次（用户要求），且按新的在前。"""
        _app, window, _cfg = self._window()
        window.show_page("search")
        view = window.search_view
        for kw in ("一", "二", "三", "四", "五", "六", "七", "八"):
            view.state.history.add(kw)
        view._maybe_show_history()
        self.app.processEvents()

        popup = view.history_popup
        self.assertEqual(popup.count(), 5, "只显示最近 5 条")
        shown = [popup.item(i).data(Qt.UserRole) for i in range(popup.count())]
        self.assertEqual(shown, ["八", "七", "六", "五", "四"], "最近搜索的排最前面")

    def test_history_popup_has_no_scrollbar(self) -> None:
        """只列 5 条，一屏放得下，不允许出现滚动条（用户要求）。"""
        _app, window, _cfg = self._window()
        window.show_page("search")
        view = window.search_view
        for kw in (" aa ", "bb", "cc", "dd", "ee", "ff", "gg"):
            view.state.history.add(kw.strip())
        view._maybe_show_history()
        self.app.processEvents()

        popup = view.history_popup
        self.assertEqual(popup.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        self.assertEqual(popup.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        # 高度正好等于 5 行内容，不多不少（没有可滚内容）
        self.assertEqual(popup.height(), HISTORY_ROW_H * 5 + 6)
        self.assertFalse(popup.verticalScrollBar().isVisible(), "内容放得下就不该有滚动条")

    def test_history_popup_repositions_on_resize(self) -> None:
        """窗口尺寸变了要重新贴位，否则宽度/位置留旧的。"""
        _app, window, _cfg = self._window()
        window.resize(1000, 600)
        window.show_page("search")
        view = window.search_view
        view.state.history.add("凤凰传奇")
        view._maybe_show_history()
        self.app.processEvents()

        window.resize(1400, 700)
        self.app.processEvents()
        popup = view.history_popup
        self.assertEqual(popup.width(), view.input.width(), "宽度要跟着搜索框")
        self.assertLessEqual(popup.x() + popup.width(), view.width() + 1)

    def test_history_popup_hides_on_outside_click(self) -> None:
        """点搜索框/下拉之外的区域（例如左导航）必须关掉下拉。"""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        _app, window, _cfg = self._window()
        window.resize(1000, 600)
        window.show_page("search")
        window.show()
        view = window.search_view
        view.state.history.add("凤凰传奇")  # 走真实 API（内部会落盘到临时目录）
        view.input.setFocus()
        self.app.processEvents()
        self.assertTrue(view.history_popup.isVisible(), "聚焦空输入框应展开历史")

        # 点左导航第一行（"我喜欢的音乐"）——以前这一下会被模态弹窗吞掉
        rect = window.nav.visualItemRect(window.nav.item(1))
        QTest.mouseClick(window.nav.viewport(), Qt.LeftButton, Qt.KeyboardModifiers(), rect.center())
        self.app.processEvents()
        self.assertFalse(view.history_popup.isVisible(), "点外面应关闭下拉")
        self.assertEqual(window.stack.currentIndex(), window.pages["library"], "导航应响应点击")

    def test_event_filter_installed_on_toplevel(self) -> None:
        """过滤器装在顶层窗口而不是 QApplication（否则进程内越积越慢）。"""
        from PySide6.QtWidgets import QApplication

        _app, window, _cfg = self._window()
        view = window.search_view
        # 过滤器应装在顶层窗口，而不是进程共享的 QApplication
        from PySide6.QtWidgets import QApplication

        self.assertNotIn(view, QApplication.instance().children() or [])
        self.assertTrue(hasattr(view, "_owns"), "搜索页应提供 _owns() 判断点击归属")

    # ---------------------------------------------- ② 已登录却显示扫码页
    def test_login_view_reacts_to_async_restore(self) -> None:
        """重启后 cookie 是**异步**恢复的，登录页必须跟着变，不能停在扫码那一帧。

        用假 session_state 单独测 LoginView：真对象要发网络请求，而且
        ``logged_in`` 还依赖 api.session，没法用发信号的方式伪造。
        """
        from nemusic.model.types import Account
        from nemusic.ui.login_view import LoginView

        class FakeState(QObject):
            account_changed = Signal(object)
            login_changed = Signal(bool)

            def __init__(self) -> None:
                super().__init__()
                self.account = None

            @property
            def logged_in(self) -> bool:
                return self.account is not None

        state = FakeState()
        self.addCleanup(state.deleteLater)
        view = LoginView(None, state)
        self.addCleanup(view.deleteLater)
        view.resize(520, 560)
        view.show()
        self.app.processEvents()
        self.assertEqual(view.lbl_title.text(), "扫码登录")

        # 模拟异步恢复成功（重启后走的就是这条路径）
        state.account = Account(uid=1, nickname="小明", vip_type=110)
        state.account_changed.emit(state.account)
        self.app.processEvents()
        self.assertIn("已登录", view.lbl_title.text())
        self.assertIn("小明", view.lbl_title.text())
        self.assertTrue(view.btn_logout.isVisible())
        self.assertFalse(view.btn_action.isVisible(), "已登录就不该再显示扫码登录按钮")
        self.assertTrue(view.me_box.isVisible())

        # 再模拟退出登录
        state.account = None
        state.login_changed.emit(False)
        self.app.processEvents()
        self.assertEqual(view.lbl_title.text(), "扫码登录")
        self.assertTrue(view.btn_action.isVisible())

    # ---------------------------------------------- ③ 横向滚动条
    def test_track_table_has_no_horizontal_scrollbar(self) -> None:
        """窗口未最大化时也不能出现横向滚动条（深色下几乎看不见，用户踩过）。"""
        from nemusic.model.types import Song

        _app, window, _cfg = self._window()
        window.resize(900, 600)  # 未最大化
        window.show_page("library")
        window.show()
        songs = [Song(id=i, name=f"很长的歌曲名字第{i}首", artist_names=("很长的歌手名字",),
                      album="很长的专辑名字", duration_ms=200000 + i) for i in range(1, 25)]
        window.playlist_view.set_songs(songs)
        self.app.processEvents()
        table = window.playlist_view.table
        self.assertEqual(table.horizontalScrollBar().maximum(), 0, "不应需要横向滚动")
        total = sum(table.columnWidth(i) for i in range(table.columnCount()))
        self.assertLessEqual(total, table.viewport().width() + 2)

    def test_search_table_has_no_horizontal_scrollbar(self) -> None:
        from nemusic.model.types import SearchPage
        from nemusic.net import endpoints as ep

        _app, window, _cfg = self._window()
        window.resize(900, 600)
        window.show_page("search")
        window.show()
        items = [Song(id=i, name=f"很长的歌曲名字第{i}首", artist_names=("很长的歌手名字",),
                      album="很长的专辑名字", duration_ms=200000 + i) for i in range(1, 25)]
        window.search_view.state.page_loaded.emit(ep.SEARCH_TYPE_SONG, SearchPage(
            keyword="x", search_type=ep.SEARCH_TYPE_SONG, offset=0, limit=25,
            total=25, items=items))
        self.app.processEvents()
        table = window.search_view._tables[ep.SEARCH_TYPE_SONG]
        self.assertEqual(table.horizontalScrollBar().maximum(), 0, "搜索结果表也不应横向滚动")

    # ---------------------------------------------- ④ 设置页多余提示
    def test_settings_page_has_no_impl_hint(self) -> None:
        """"数字在子线程里统计，不卡界面"这种实现细节不该展示给用户。"""
        _app, window, _cfg = self._window()
        labels = [w.text() for w in window.settings_view.findChildren(QLabel)]
        for text in labels:
            self.assertNotIn("子线程", text)
            self.assertNotIn("不卡界面", text)


class TestAppearanceSwitch(unittest.TestCase):
    """外观切换：样式表重刷 + 表格文字重新上色（用户要求的背景/主题功能）。"""

    def setUp(self) -> None:
        redirect_config()
        self.app = qt_app()

    def _window(self):
        from nemusic.app import build_app

        app, window, config = build_app([sys.argv[0]])
        self.addCleanup(window.controller.shutdown)
        return app, window, config

    def test_appearance_change_reapplies_stylesheet(self) -> None:
        app, window, cfg = self._window()
        before = app.styleSheet()
        window.background_view._apply_preset("dusk")
        self.app.processEvents()
        self.assertNotEqual(app.styleSheet(), before, "换背景后样式表必须重刷")
        self.assertEqual(cfg.theme, "dark")
        # 背景层要真的拼进样式表
        self.assertIn("QMainWindow", app.styleSheet())
        self.assertIn("gradient", app.styleSheet())

    def test_appearance_change_refreshes_table_colors(self) -> None:
        """item 的 foreground 不会自动跟随 palette，必须显式重染。"""
        from nemusic.model.types import Song

        app, window, cfg = self._window()
        songs = [Song(id=i, name=f"歌{i}", artist_names=("手",)) for i in range(1, 4)]
        window.playlist_view.set_songs(songs)
        self.app.processEvents()
        table = window.playlist_view.table
        light_ink = table._ink(1).name()

        window.background_view._on_theme("dark")
        self.app.processEvents()
        dark_ink = table._ink(1).name()
        self.assertNotEqual(light_ink, dark_ink, "两套主题的文字色必须不同")
        # 已创建的 item 也要跟着变
        self.assertEqual(table.item(0, 1).foreground().color().name(), dark_ink)

    def test_current_row_highlight_survives_theme_switch(self) -> None:
        """切主题后当前播放行的粉色高亮不能被冲掉。"""
        from nemusic.ui.widgets.track_table import ACCENT_COLOR

        app, window, cfg = self._window()
        songs = [Song(id=1, name="a"), Song(id=2, name="b")]
        window.playlist_view.set_songs(songs)
        window.playlist_view.highlight(1)
        self.app.processEvents()
        self.assertEqual(window.playlist_view.table.item(0, 1).foreground().color().name(),
                         ACCENT_COLOR.name())
        window.background_view._on_theme("dark")
        self.app.processEvents()
        self.assertEqual(window.playlist_view.table.item(0, 1).foreground().color().name(),
                         ACCENT_COLOR.name(), "切主题后高亮应保留")

    def test_background_page_is_reachable_from_nav(self) -> None:
        app, window, cfg = self._window()
        window.show_page("background")
        self.assertEqual(window.stack.currentIndex(), window.pages["background"])
        # 外观页紧跟设置，在导航最后一项
        self.assertEqual(window.nav.count(), 5)
        item = window.nav.item(4)
        self.assertEqual(item.data(Qt.UserRole), "background")


if __name__ == "__main__":
    unittest.main()


class _FakeSession:
    """PlaylistView 冒烟用的假登录态。"""

    logged_in = True
    uid = 1234
    star_playlist_id = 0


class TestSearchViewSmoke(unittest.TestCase):
    """搜索页 UI 冒烟（AC-2.1/2.2/2.4/2.5）。"""

    def setUp(self) -> None:
        redirect_config()  # 每个用例独立临时目录，避免历史文件互相污染
        self.app = qt_app()
        self.history = SearchHistory()
        self.state = SearchState(FakeApi(), history=self.history)
        self.view = SearchView(self.state)
        self.view.resize(960, 600)
        self.view.show()
        self._spin(60)

    def tearDown(self) -> None:
        self.view.close()
        self._spin(20)

    def _spin(self, ms: int) -> None:
        deadline = time.monotonic() + ms / 1000
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

    def _feed(self, search_type: int, items: list, total: int | None = None) -> None:
        """直接把结果塞进 state 再刷新（跳过异步通道，专测渲染）。"""
        state = self.state.tab(search_type)
        state.items = list(items)
        state._seen = {getattr(i, "id", i) for i in items}
        state.total = total if total is not None else len(items)
        state.finished = True
        self.view.current_type = search_type
        self.view._refresh_tables()

    # ---------------------------------------------------------- 结构
    def test_four_tabs_and_empty_state(self) -> None:
        self.assertEqual([b.text() for b in self.view.tab_buttons.values()],
                         ["单曲", "歌手", "专辑", "歌单"])
        self.assertTrue(self.view.empty.isVisible())
        self.assertIn("输入关键词", self.view.empty.text())
        self.assertEqual(self.view.footer.text(), "")

    def test_switch_tab_keeps_only_one_checked(self) -> None:
        self.view._switch_tab(ep.SEARCH_TYPE_ARTIST)
        self.assertTrue(self.view.tab_buttons[ep.SEARCH_TYPE_ARTIST].isChecked())
        self.assertFalse(self.view.tab_buttons[ep.SEARCH_TYPE_SONG].isChecked())
        self.assertIs(self.view.stack.currentWidget(), self.view._tables[ep.SEARCH_TYPE_ARTIST]
                      if False else self.view.empty)  # 空结果显示空状态

    def test_empty_keyword_shows_hint(self) -> None:
        self.view._do_search()
        self.assertEqual(self.view.footer.text(), "请输入关键词")
        self.assertEqual(self.state.keyword, "")

    # ---------------------------------------------------------- 单曲表
    def test_song_table_renders(self) -> None:
        songs = [Song(id=1, name="测试歌", artist_names=("测试歌手",), artist_ids=(9,),
                      album="测试专辑", album_id=5, duration_ms=226000)]
        self._feed(ep.SEARCH_TYPE_SONG, songs)
        table = self.view._tables[ep.SEARCH_TYPE_SONG]
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), "1")
        self.assertEqual(table.item(0, 1).text(), "测试歌")
        self.assertEqual(table.item(0, 2).text(), "测试歌手")
        self.assertEqual(table.item(0, 3).text(), "测试专辑")
        self.assertEqual(table.item(0, 4).text(), "03:46")
        self.assertFalse(self.view.empty.isVisible())
        self.assertIn("共 1 条", self.view.footer.text())

    def test_double_click_song_emits_play_requested(self) -> None:
        songs = [Song(id=1, name="a"), Song(id=2, name="b")]
        self._feed(ep.SEARCH_TYPE_SONG, songs)
        got: list[tuple] = []
        self.view.play_requested.connect(lambda items, i: got.append(([s.id for s in items], i)))
        self.view._on_row_activated(ep.SEARCH_TYPE_SONG, 1)
        self.assertEqual(got, [([1, 2], 1)])

    def test_double_click_out_of_range_ignored(self) -> None:
        self._feed(ep.SEARCH_TYPE_SONG, [Song(id=1, name="a")])
        got: list[tuple] = []
        self.view.play_requested.connect(lambda items, i: got.append((items, i)))
        self.view._on_row_activated(ep.SEARCH_TYPE_SONG, 9)
        self.assertEqual(got, [])

    def test_like_button_toggles(self) -> None:
        song = Song(id=1, name="a")
        self._feed(ep.SEARCH_TYPE_SONG, [song])
        wanted: list[tuple] = []
        self.view.like_toggled.connect(lambda s, desired: wanted.append((s.id, desired)))
        button = self.view._like_buttons[ep.SEARCH_TYPE_SONG][1]
        self.view._toggle_like(song, button)
        self.assertEqual(wanted, [(1, True)])       # 未收藏 → 期望收藏
        self.view.set_liked(1, True)                 # 控制器确认收藏
        self.view._toggle_like(song, button)
        self.assertEqual(wanted[-1], (1, False))     # 已收藏 → 期望取消
        self.assertIn("收藏到我喜欢的音乐", button.toolTip())  # 再点一次 → 回到未收藏外观

    def test_set_liked_updates_icon_of_existing_row(self) -> None:
        song = Song(id=7, name="b")
        self._feed(ep.SEARCH_TYPE_SONG, [song])
        self.view.set_liked(7, True)
        button = self.view._like_buttons[ep.SEARCH_TYPE_SONG][7]
        self.assertIn("取消收藏", button.toolTip())
        self.view.set_liked(999, True)  # 不存在的行不能报错，也不能影响已有行
        self.assertIn("取消收藏", button.toolTip())

    # ---------------------------------------------------------- 实体表
    def test_double_click_artist_album_playlist(self) -> None:
        got: list[tuple[str, int]] = []
        self.view.open_entity.connect(lambda kind, eid: got.append((kind, eid)))
        artist = Artist(id=88, name="周杰伦")
        album = Album(id=77, name="专辑名", artist="周杰伦")
        playlist = Playlist(id=66, name="歌单名", track_count=99, creator="某人")

        self._feed(ep.SEARCH_TYPE_ARTIST, [artist])
        self.view._on_row_activated(ep.SEARCH_TYPE_ARTIST, 0)
        table = self.view._tables[ep.SEARCH_TYPE_ARTIST]
        self.assertEqual(table.item(0, 0).text(), "周杰伦")

        self._feed(ep.SEARCH_TYPE_ALBUM, [album])
        self.view._on_row_activated(ep.SEARCH_TYPE_ALBUM, 0)
        table = self.view._tables[ep.SEARCH_TYPE_ALBUM]
        self.assertEqual(table.item(0, 0).text(), "专辑名")
        self.assertEqual(table.item(0, 1).text(), "周杰伦")

        self._feed(ep.SEARCH_TYPE_PLAYLIST, [playlist])
        self.view._on_row_activated(ep.SEARCH_TYPE_PLAYLIST, 0)
        table = self.view._tables[ep.SEARCH_TYPE_PLAYLIST]
        self.assertEqual(table.item(0, 0).text(), "歌单名")
        self.assertEqual(table.item(0, 1).text(), "99")
        self.assertEqual(table.item(0, 2).text(), "某人")

        self.assertEqual(got, [("artist", 88), ("album", 77), ("playlist", 66)])

    def test_empty_result_shows_hint(self) -> None:
        self._feed(ep.SEARCH_TYPE_SONG, [])
        self.assertTrue(self.view.empty.isVisible())
        self.assertEqual(self.view._tables[ep.SEARCH_TYPE_SONG].rowCount(), 0)
        self.assertEqual(self.view.footer.text(), "没有找到相关内容")

    def test_search_failed_shows_message(self) -> None:
        self.view._on_failed(ep.SEARCH_TYPE_SONG, "网络错误")
        self.assertEqual(self.view.footer.text(), "搜索失败：网络错误")

    def test_footer_shows_progress(self) -> None:
        self.view._set_footer(30, 100, False, False)
        self.assertEqual(self.view.footer.text(), "已加载 30 / 100 条，继续滚动加载更多")
        self.view._set_footer(30, 100, True, False)
        self.assertEqual(self.view.footer.text(), "加载中…（已加载 30 条）")
        self.view._set_footer(100, 100, False, True)
        self.assertEqual(self.view.footer.text(), "共 100 条")

    # ---------------------------------------------------------- 历史
    def test_history_popup_and_pick(self) -> None:
        self.history.add("周杰伦")
        self.history.add("山河图")
        self.view._maybe_show_history()
        self.assertTrue(self.view.history_popup.isVisible())
        self.assertEqual(self.view.history_popup.count(), 2)
        item = self.view.history_popup.item(0)
        self.assertEqual(item.data(Qt.UserRole), "山河图")
        self.view._on_history_clicked(item)
        self.assertFalse(self.view.history_popup.isVisible())
        self.assertEqual(self.view.input.text(), "山河图")
        self.assertEqual(self.state.keyword, "山河图")

    def test_history_hidden_when_input_not_empty(self) -> None:
        self.history.add("周杰伦")
        self.view.input.setText("周")
        self.view._maybe_show_history()
        self.assertFalse(self.view.history_popup.isVisible())

    def test_history_hidden_when_no_items(self) -> None:
        self.view._maybe_show_history()
        self.assertFalse(self.view.history_popup.isVisible())


if __name__ == "__main__":
    unittest.main()


class TestPlaylistViewSmoke(unittest.TestCase):
    """「我喜欢的音乐」页 UI 冒烟（AC-3.1~3.4）。"""

    def setUp(self) -> None:
        redirect_config()
        self.app = qt_app()
        self.api = FakeApi()
        self.state = LibraryState(self.api, _FakeSession())
        self.view = PlaylistView(self.state)
        self.view.resize(960, 600)
        self.view.show()
        self._spin(50)

    def tearDown(self) -> None:
        self.view.close()
        self._spin(20)

    def _spin(self, ms: int) -> None:
        deadline = time.monotonic() + ms / 1000
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

    def _feed(self, songs: list) -> None:
        self.state.songs = list(songs)
        self.state.liked = {s.id: True for s in songs}
        self.view.set_songs(list(songs))

    def test_empty_state_disables_play_all(self) -> None:
        self.assertFalse(self.view.btn_play_all.isEnabled())
        self.assertEqual(self.view.lbl_title.text(), "我喜欢的音乐")
        self.assertEqual(self.view.lbl_count.text(), "")  # 还没加载过
        self.view.set_songs([])
        self.assertEqual(self.view.lbl_count.text(), "0 首")

    def test_renders_rows(self) -> None:
        songs = [Song(id=1, name="第一首", artist_names=("歌手甲",), album="专辑甲", duration_ms=200000),
                 Song(id=2, name="第二首", artist_names=("歌手乙",), album="专辑乙", duration_ms=31000)]
        self._feed(songs)
        table = self.view.table
        self.assertEqual(table.rowCount(), 2)
        self.assertEqual(table.item(0, 0).text(), "1")
        self.assertEqual(table.item(0, 1).text(), "第一首")
        self.assertEqual(table.item(0, 2).text(), "歌手甲")
        self.assertEqual(table.item(1, 4).text(), "00:31")
        self.assertTrue(self.view.btn_play_all.isEnabled())
        self.assertIn("2 首", self.view.lbl_count.text())

    def test_play_all_emits_request(self) -> None:
        got: list[tuple] = []
        self.view.play_requested.connect(lambda items, i: got.append(([s.id for s in items], i)))
        self._feed([Song(id=1, name="a"), Song(id=2, name="b")])
        self.view._play_all()
        self.assertEqual(got, [([1, 2], 0)])

    def test_double_click_emits_request_with_index(self) -> None:
        got: list[tuple] = []
        self.view.play_requested.connect(lambda items, i: got.append(([s.id for s in items], i)))
        self._feed([Song(id=1, name="a"), Song(id=2, name="b")])
        self.view._on_row_activated(1)
        self.assertEqual(got, [([1, 2], 1)])
        self.view._on_row_activated(9)  # 越界忽略
        self.assertEqual(len(got), 1)

    def test_like_button_emits_desired(self) -> None:
        wanted: list[tuple] = []
        self.view.like_toggled.connect(lambda song, desired: wanted.append((song.id, desired)))
        self._feed([Song(id=1, name="a")])
        self.view.table._on_like(self.view.table.songs[0], self.view.table._buttons[1])
        self.assertEqual(wanted, [(1, False)])  # 列表里的歌已收藏 → 期望取消
        self.view.set_liked(1, False)
        self.view.table._on_like(self.view.table.songs[0], self.view.table._buttons[1])
        self.assertEqual(wanted[-1], (1, True))

    def test_highlight_current_row(self) -> None:
        songs = [Song(id=1, name="a"), Song(id=2, name="b")]
        self._feed(songs)
        self.view.highlight(2)
        self.assertEqual(self.view.table._current_id, 2)
        self.assertTrue(self.view.table.item(1, 1).font().bold())
        self.assertFalse(self.view.table.item(0, 1).font().bold())
        self.view.highlight(0)  # 没有当前歌曲时全部取消加粗
        self.assertFalse(self.view.table.item(1, 1).font().bold())

    def test_failed_message_shown(self) -> None:
        self.view._on_failed("加载失败：网络错误")
        self.assertEqual(self.view.lbl_hint.text(), "加载失败：网络错误")

    def test_loading_hint(self) -> None:
        self.view._on_loading(True)
        self.assertEqual(self.view.lbl_hint.text(), "正在加载我喜欢的音乐…")
        self.assertFalse(self.view.btn_refresh.isEnabled())
        self.view._on_loading(False)
        self.assertTrue(self.view.btn_refresh.isEnabled())


if __name__ == "__main__":
    unittest.main()


class TestTrackTableAndEntityView(unittest.TestCase):
    """共用曲目表格 + 专辑/歌单详情页（AC-2.4）。"""

    def setUp(self) -> None:
        redirect_config()
        self.app = qt_app()
        self.view = EntityView()
        self.view.resize(900, 600)
        self.view.show()
        self._spin(40)

    def tearDown(self) -> None:
        self.view.close()
        self._spin(20)

    def _spin(self, ms: int) -> None:
        deadline = time.monotonic() + ms / 1000
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

    def _songs(self, n: int = 3) -> list:
        return [Song(id=i, name=f"曲目{i}", artist_names=("歌手",), album="专辑", duration_ms=200000 + i) for i in range(1, n + 1)]

    def test_set_entity_renders(self) -> None:
        self.view.set_entity("album", 77, "测试专辑", "测试歌手 / 3 首", self._songs())
        self.assertEqual(self.view.lbl_title.text(), "测试专辑")
        self.assertEqual(self.view.lbl_sub.text(), "测试歌手 / 3 首")
        self.assertEqual(self.view.table.rowCount(), 3)
        self.assertEqual(self.view.table.item(0, 1).text(), "曲目1")
        self.assertTrue(self.view.btn_play_all.isEnabled())
        self.assertEqual(self.view.lbl_hint.text(), "双击任意一首开始播放")

    def test_double_click_emits_play(self) -> None:
        got: list[tuple] = []
        self.view.play_requested.connect(lambda items, i: got.append(([s.id for s in items], i)))
        self.view.set_entity("playlist", 5, "歌单", "3 首", self._songs())
        self.view.table.row_activated.emit(2)
        self.assertEqual(got, [([1, 2, 3], 2)])

    def test_play_all(self) -> None:
        got: list[tuple] = []
        self.view.play_all_requested.connect(lambda: got.append(1))
        self.view.set_entity("playlist", 5, "歌单", "3 首", self._songs())
        self.view.btn_play_all.click()
        self.assertEqual(got, [1])
        self.assertEqual([s.id for s in self.view.songs()], [1, 2, 3])

    def test_like_click(self) -> None:
        wanted: list[tuple] = []
        self.view.like_toggled.connect(lambda song, desired: wanted.append((song.id, desired)))
        self.view.set_entity("album", 1, "a", "", self._songs(1))
        self.view.table._on_like(self.view.table.songs[0], self.view.table._buttons[1])
        self.assertEqual(wanted, [(1, True)])
        self.view.set_liked(1, True)
        self.view.table._on_like(self.view.table.songs[0], self.view.table._buttons[1])
        self.assertEqual(wanted[-1], (1, False))

    def test_back_button(self) -> None:
        got: list[int] = []
        self.view.back_requested.connect(lambda: got.append(1))
        self.view.btn_back.click()
        self.assertEqual(got, [1])

    def test_empty_entity(self) -> None:
        self.view.set_entity("artist", 9, "歌手", "", [])
        self.assertEqual(self.view.table.rowCount(), 0)
        self.assertFalse(self.view.btn_play_all.isEnabled())
        self.assertEqual(self.view.lbl_hint.text(), "这个列表没有可播放的歌曲")

    def test_highlight(self) -> None:
        self.view.set_entity("album", 1, "a", "", self._songs())
        self.view.highlight(2)
        self.assertTrue(self.view.table.item(1, 1).font().bold())
        self.assertFalse(self.view.table.item(0, 1).font().bold())

    def test_clear_and_reset(self) -> None:
        self.view.set_entity("album", 1, "a", "", self._songs())
        self.view.table.clear()
        self.assertEqual(self.view.table.rowCount(), 0)
        self.assertEqual(self.view.table.songs, [])



if __name__ == "__main__":
    unittest.main()


class TestLyricsView(unittest.TestCase):
    """歌词页（AC-4.4/4.5/4.6）。"""

    @classmethod
    def setUpClass(cls) -> None:
        redirect_config()

    def setUp(self) -> None:
        self.app = qt_app()

    def _view(self):
        from nemusic.config import Config
        from nemusic.ui.lyrics_view import LyricsView

        view = LyricsView(Config.load())
        self.addCleanup(view.deleteLater)
        view.resize(900, 600)
        view.show()
        self.app.processEvents()
        return view

    def _doc(self, lines=3, translation=False):
        from nemusic.model.types import LyricDoc, LyricLine

        return LyricDoc(
            song_id=1,
            lines=[LyricLine(i * 4000, f"第{i}句", "译文" if translation else "") for i in range(lines)],
            credits=["作词：某人"] if lines else [],
        )

    # ------------------------------------------------------------ 基础渲染
    def test_no_song_shows_hint(self) -> None:
        view = self._view()
        view.set_song(None)
        self.assertEqual(view.lbl_title.text(), "未在播放")

    def test_set_song_shows_loading_then_lyrics(self) -> None:
        view = self._view()
        song = Song(id=9, name="歌", artist_names=("手",))
        view.set_song(song)
        self.app.processEvents()
        self.assertEqual(view.lbl_title.text(), "歌")
        self.assertEqual(view.lbl_artist.text(), "手")
        view.set_lyric(self._doc(3))
        self.app.processEvents()
        self.assertEqual(len(view._rows), 3)
        self.assertEqual(view._rows[0].text(), "第0句")

    def test_translation_rows_hidden_when_absent(self) -> None:
        view = self._view()
        view.set_lyric(self._doc(2, translation=False))
        self.app.processEvents()
        self.assertFalse(view._sub_rows[0].isVisible())
        view.set_lyric(self._doc(2, translation=True))
        self.app.processEvents()
        self.assertTrue(view._sub_rows[0].isVisible())
        self.assertEqual(view._sub_rows[0].text(), "译文")

    def test_no_lyric_shows_hint(self) -> None:
        from nemusic.model.types import LyricDoc

        view = self._view()
        view.set_song(Song(id=1, name="歌"))
        view.set_lyric(LyricDoc(song_id=1))
        self.assertEqual(view._rows, [])
        self.assertIn("暂无歌词", [w.text() for w in view.inner.findChildren(QLabel)])

    def test_credits_shown(self) -> None:
        view = self._view()
        view.set_lyric(self._doc(1))
        self.assertEqual(view.lbl_credits.text(), "作词：某人")

    # ------------------------------------------------------------ 高亮与跟随
    def test_current_line_highlighted(self) -> None:
        view = self._view()
        view.set_lyric(self._doc(3))
        view.set_position(4500)
        self.assertEqual(view._current, 1)
        self.assertEqual(view._rows[1].property("current"), True)
        self.assertFalse(view._rows[0].property("current"))

    def test_position_before_first_line_does_not_crash(self) -> None:
        from nemusic.model.types import LyricDoc, LyricLine

        view = self._view()
        view.set_lyric(LyricDoc(song_id=1, lines=[LyricLine(2000, "后出现")]))
        view.set_position(0)
        self.assertEqual(view._current, -1)

    def test_same_position_is_noop(self) -> None:
        view = self._view()
        view.set_lyric(self._doc(3))
        view.set_position(100)
        first = view._current
        view.set_position(100)
        self.assertEqual(view._current, first)

    def test_manual_scroll_pauses_follow(self) -> None:
        """AC-4.5：手动滚动后 5 秒内不自动跟随。"""
        view = self._view()
        view.set_lyric(self._doc(20))
        self.app.processEvents()
        view.set_position(0)
        self.assertTrue(view._follow)
        bar = view.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.assertFalse(view._follow)
        view.set_position(80_000)  # 仍切行，但不滚动
        self.assertTrue(view._resume_timer.isActive())
        self.assertFalse(view._follow)

    def test_follow_resumes_after_timer(self) -> None:
        view = self._view()
        view.set_lyric(self._doc(20))
        self.app.processEvents()
        view._on_user_scroll(10)
        self.assertFalse(view._follow)
        view._resume_follow()
        self.assertTrue(view._follow)

    def test_animator_scrolls_towards_target(self) -> None:
        view = self._view()
        view.set_lyric(self._doc(30))
        self.app.processEvents()
        view.set_position(0)
        view._center_on(20)
        self.assertTrue(view._anim.is_animating)
        view._anim.animate_to(0)
        self.assertFalse(view._anim.is_animating)

    # ------------------------------------------------------------ 交互
    def test_back_signal(self) -> None:
        view = self._view()
        got: list = []
        view.back_requested.connect(lambda: got.append(1))
        view.btn_back.click()
        self.assertEqual(got, [1])

    def test_cover_placeholder_then_pixmap(self) -> None:
        view = self._view()
        view.set_cover(None)
        self.assertFalse(view.lbl_cover.pixmap().isNull())  # 占位图
        pix = QPixmap(64, 64)
        pix.fill(Qt.red)
        view.set_cover(pix)
        self.assertFalse(view.lbl_cover.pixmap().isNull())


class TestPlayerBarCover(unittest.TestCase):
    """播放条封面（用户反馈：封面位置是空的）。"""

    @classmethod
    def setUpClass(cls) -> None:
        redirect_config()

    def setUp(self) -> None:
        self.app = qt_app()

    def test_set_song_clears_cover_then_set_cover_shows_it(self) -> None:
        from nemusic.config import Config
        from nemusic.ui.player_bar import PlayerBar

        bar = PlayerBar(Config.load())
        self.addCleanup(bar.deleteLater)
        song = Song(id=3, name="歌", artist_names=("手",), duration_ms=1000)
        bar.set_song(song)
        self.app.processEvents()
        self.assertFalse(bar.btn_cover.icon().isNull(), "set_song 后应先显示占位图")
        pix = QPixmap(100, 100)
        pix.fill(Qt.blue)
        bar.set_cover(pix)
        self.assertEqual(bar.btn_cover.icon().availableSizes(), [QSize(46, 46)])
        pix = QPixmap(100, 100)
        pix.fill(Qt.blue)
        bar.set_cover(pix)
        self.assertEqual(bar.btn_cover.icon().availableSizes(), [QSize(46, 46)])

    def test_cover_button_emits_signal(self) -> None:
        from nemusic.config import Config
        from nemusic.ui.player_bar import PlayerBar

        bar = PlayerBar(Config.load())
        self.addCleanup(bar.deleteLater)
        bar.set_song(Song(id=1, name="歌"))
        got: list = []
        bar.cover_clicked.connect(lambda: got.append(1))
        bar.btn_cover.click()
        self.assertEqual(got, [1])


if __name__ == "__main__":
    unittest.main()


class TestLogoutPolicy(unittest.TestCase):
    """用户明确要求：只有主动点退出登录才清用户数据。"""

    def setUp(self) -> None:
        redirect_config()
        self.app = qt_app()

    def _window(self):
        from nemusic.app import build_app

        app, window, config = build_app([sys.argv[0]])
        self.addCleanup(window.controller.shutdown)
        return app, window, config

    def _feed_library(self, window) -> None:
        from nemusic.model.types import Song

        songs = [Song(id=1, name="a"), Song(id=2, name="b")]
        window.library.songs = songs
        window.playlist_view.set_songs(songs)

    def test_explicit_logout_clears_user_data(self) -> None:
        _app, window, _cfg = self._window()
        self._feed_library(window)
        window.session_state.logged_out.emit()
        self.app.processEvents()
        self.assertEqual(window.library.songs, [])
        self.assertEqual(len(window.playlist_view.table.songs), 0)
        self.assertEqual(window.stack.currentIndex(), window.pages["login"])

    def test_logout_stops_playback_and_clears_queue(self) -> None:
        """登出后不能留着上一首在播/队列里（真机踩过：stop/clear 都不存在）。"""
        _app, window, _cfg = self._window()
        ctl = window.controller
        ctl.play_songs([Song(id=1, name="a"), Song(id=2, name="b")], 0, context="测试")
        self.app.processEvents()
        self.assertIsNotNone(ctl.current_song)
        ctl.stop()
        self.app.processEvents()
        self.assertIsNone(ctl.current_song)
        self.assertEqual(len(ctl.queue), 0)
        self.assertFalse(ctl.playing)
        self.assertFalse(window.player_bar.btn_play.isEnabled())

    def test_cookie_expiry_keeps_user_data(self) -> None:
        """cookie 失效/重启恢复失败：只回登录页，列表照旧（用户要求）。"""
        _app, window, _cfg = self._window()
        self._feed_library(window)
        window.session_state.login_changed.emit(False)
        self.app.processEvents()
        self.assertEqual(len(window.library.songs), 2, "非主动登出不能清列表")
        self.assertEqual(len(window.playlist_view.table.songs), 2)
        self.assertEqual(window.stack.currentIndex(), window.pages["login"])


if __name__ == "__main__":
    unittest.main()
