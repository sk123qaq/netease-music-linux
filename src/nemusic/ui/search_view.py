"""搜索页（AC-2.1~2.7）：搜索框 + 历史下拉 + 四个 tab + 无限滚动 + 播放/收藏。

只负责渲染与交互，数据来自 ``state/search_state.py``；播放/收藏通过信号交给主窗口。
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFocusEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..model.types import Song, format_duration
from ..net import endpoints as ep
from ..state.search_state import SearchState
from ..util import icons, log

_log = log.get("ui.search")


class _ElideLabel(QLabel):
    """省略号截断的单元格文本。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full = text
        self.setObjectName("cellLabel")

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        metrics = self.fontMetrics()
        self.setText(metrics.elidedText(self._full, Qt.ElideRight, max(20, self.width() - 4)))


class _LikeButton(QPushButton):
    """收藏/取消收藏按钮（AC-2.3）。"""

    def __init__(self, song: Song, liked: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.song = song
        self.setFixedSize(26, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("cellButton")
        self.set_liked(liked)

    def set_liked(self, liked: bool) -> None:
        self.setIcon(icons.icon("heart" if liked else "heart_outline", "#e8556d" if liked else "#8f8f95", 16))
        self.setToolTip("取消收藏" if liked else "收藏到我喜欢的音乐")


class _ResultTable(QTableWidget):
    """结果表格：列宽自适应 + 滚动到底部自动加载更多（AC-2.5）。"""

    reached_bottom = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setShowGrid(False)
        self.setAlternatingRowColors(False)
        self.verticalHeader().setVisible(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().valueChanged.connect(self._maybe_load_more)
        self._loading_finished = False

    def _maybe_load_more(self, value: int) -> None:
        bar = self.verticalScrollBar()
        if value >= bar.maximum() - max(40, bar.pageStep() // 2):
            if not self._loading_finished:
                self.reached_bottom.emit()

    def set_finished(self, finished: bool) -> None:
        """到底后不再触发加载。"""
        self._loading_finished = finished

    # ------------------------------------------------------------ 列宽自适应
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._fit_columns()

    def _fit_columns(self) -> None:
        """消灭横向滚动条：和 TrackTable 同一套策略（详见 track_table._fit_columns）。"""
        if getattr(self, "_fitting", False):
            return
        self._fitting = True
        try:
            viewport = self.viewport().width()
            if viewport <= 0:
                return
            columns = self.columnCount()
            fixed = sum(self.columnWidth(i) for i in range(columns)
                        if self.horizontalHeader().sectionResizeMode(i) == QHeaderView.Fixed)
            rest = viewport - fixed - 6
            if rest < 120:
                return
            stretch_cols = [i for i in range(columns)
                            if self.horizontalHeader().sectionResizeMode(i) == QHeaderView.Stretch]
            if not stretch_cols:
                return
            # 除第一列外，其余可伸缩列按内容但设上限，避免把空间吃光
            caps = {}
            for col in stretch_cols[1:]:
                caps[col] = min(self._content_width(col), max(90, int(rest * 0.34)))
            used = sum(caps.values())
            first = max(90, rest - used)
            self.setColumnWidth(stretch_cols[0], first)
            for col, width in caps.items():
                self.setColumnWidth(col, width)
        finally:
            self._fitting = False

    def _content_width(self, column: int) -> int:
        header_hint = self.horizontalHeader().sectionSizeHint(column)
        return max(header_hint, self.sizeHintForColumn(column)) + 14

    # ------------------------------------------------------------ 文字颜色
    #: 主列（歌名/歌手名/专辑名）用实色，其余列半透明弱一档
    _PRIMARY_COLUMNS = frozenset({0, 1})

    def _text(self, value: str, align, column: int) -> QTableWidgetItem:
        """建一个单元格；文字色从 palette 现取（主题自适应）。"""
        item = QTableWidgetItem(value)
        item.setTextAlignment(int(align))
        item.setForeground(self._ink(column))
        return item

    def _ink(self, column: int) -> QColor:
        """从 palette 取文字色。

        实测：QSS 的 ``QTableWidget { color: ... }`` 会反映到
        ``palette().color(QPalette.Text)``，但**已创建的 item 的 foreground
        不会自动跟随**，所以每次建 item / 切主题都要重新取一次。
        """
        color = QColor(self.palette().color(QPalette.Text))
        if column not in self._PRIMARY_COLUMNS:
            color.setAlphaF(0.62)
        return color

    def refresh_colors(self) -> None:
        """主题切换后重新上色（item 的 foreground 不会自动跟随 palette）。"""
        for row in range(self.rowCount()):
            for column in range(self.columnCount()):
                item = self.item(row, column)
                if item is not None:
                    item.setForeground(self._ink(column))
        self._fit_columns()  # 列宽也可能因为字号变化要重算

    def clear_rows(self) -> None:
        """清空结果（登出用）。QTableWidget.clear() 会被 Qt 复用，这里显式清行。"""
        self.setRowCount(0)
        self._loading_finished = False


#: 历史下拉最多显示几条（用户要求只列最近 5 次）
HISTORY_VISIBLE = 5

#: 历史下拉每行高度（和 QSS 里 historyPopup::item 的 padding 对齐）
HISTORY_ROW_H = 28


class SearchView(QWidget):
    """搜索页面。"""

    #: (songs, index) 播放请求
    play_requested = Signal(list, int)
    #: (song, desired) 收藏/取消收藏
    like_toggled = Signal(object, bool)
    #: ("artist"|"album"|"playlist", id) 载入实体曲目
    #: id 用 object：歌单 id 可能超过 2^31（Signal(int) 会 OverflowError）
    open_entity = Signal(str, object)
    #: 历史被点击（回填搜索框并搜索）
    history_picked = Signal(str)

    def __init__(self, state: SearchState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.current_type = ep.SEARCH_TYPE_SONG
        self._tables: dict[int, _ResultTable] = {}
        self._like_buttons: dict[int, dict[int, _LikeButton]] = {}
        self._is_liked: dict[int, bool] = {}

        self._build()
        self._wire()

    # ============================================================ 构建
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(10)

        # 搜索框 + 历史下拉
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setObjectName("searchInput")
        self.input.setPlaceholderText("搜索歌曲 / 歌手 / 专辑 / 歌单")
        self.input.setClearButtonEnabled(True)
        self.input.setMinimumHeight(34)
        search_row.addWidget(self.input, 1)
        self.btn_search = QPushButton("搜索")
        self.btn_search.setObjectName("primaryButton")
        self.btn_search.setCursor(Qt.PointingHandCursor)
        self.btn_search.setMinimumHeight(34)
        search_row.addWidget(self.btn_search)
        root.addLayout(search_row)

        # 历史下拉：**页面内的普通子控件**，不是独立窗口。
        # 曾经用过 Qt.Tool 顶层窗，但 Wayland 下客户端拿不到窗口真实屏幕位置，
        # mapToGlobal() 给的是"以窗口为原点"的坐标，结果下拉被放到屏幕左上角
        # （真机截图里飘到浏览器上头去了）。子控件的坐标是页面内局部的，永远对。
        # 也不用 Qt.Popup：那个标志是模态的，会抢走鼠标（点左导航没反应）。
        self.history_popup = QListWidget(self)
        self.history_popup.setObjectName("historyPopup")
        # 不抢输入框焦点，也不显示滚动条（只列最近几条，一屏放得下）
        self.history_popup.setFocusPolicy(Qt.NoFocus)
        self.history_popup.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history_popup.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history_popup.hide()

        # 四个 tab
        tab_row = QHBoxLayout()
        tab_row.setSpacing(6)
        self.tab_buttons: dict[int, QPushButton] = {}
        for search_type in (ep.SEARCH_TYPE_SONG, ep.SEARCH_TYPE_ARTIST, ep.SEARCH_TYPE_ALBUM, ep.SEARCH_TYPE_PLAYLIST):
            btn = QPushButton(ep.SEARCH_TYPE_LABELS[search_type])
            btn.setObjectName("tabButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumWidth(64)
            self.tab_buttons[search_type] = btn
            tab_row.addWidget(btn)
        tab_row.addStretch(1)
        root.addLayout(tab_row)
        self.tab_buttons[ep.SEARCH_TYPE_SONG].setChecked(True)

        # 结果区
        self.stack = QStackedWidget()
        self._tables[ep.SEARCH_TYPE_SONG] = self._song_table()
        self._tables[ep.SEARCH_TYPE_ARTIST] = self._simple_table(["歌手", "专辑数", "歌曲数"])
        self._tables[ep.SEARCH_TYPE_ALBUM] = self._simple_table(["专辑", "歌手", "曲目数"])
        self._tables[ep.SEARCH_TYPE_PLAYLIST] = self._simple_table(["歌单", "曲目数", "创建者"])
        for search_type, table in self._tables.items():
            self.stack.addWidget(table)
        # 空状态：没有结果时显示提示而不是空表格
        self.empty = QLabel("输入关键词开始搜索", self.stack)
        self.empty.setObjectName("emptyHint")
        self.empty.setAlignment(Qt.AlignCenter)
        self.stack.addWidget(self.empty)
        self.stack.setCurrentWidget(self.empty)
        root.addWidget(self.stack, 1)

        self.footer = QLabel("")
        self.footer.setObjectName("pageHint")
        self.footer.setAlignment(Qt.AlignCenter)
        root.addWidget(self.footer)

    def _song_table(self) -> _ResultTable:
        table = _ResultTable()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["#", "歌曲", "歌手", "专辑", "时长", ""])
        header = table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        # 不用 ResizeToContents：数据到达后它会把列撑回内容宽度，横向滚动条复活
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        for col in (0, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        table.setColumnWidth(0, 42)
        table.setColumnWidth(4, 60)
        table.setColumnWidth(5, 34)
        table.verticalHeader().setDefaultSectionSize(36)
        return table

    def _simple_table(self, headers: list[str]) -> _ResultTable:
        table = _ResultTable()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(headers)):
            header.setSectionResizeMode(i, QHeaderView.Interactive)
        table.verticalHeader().setDefaultSectionSize(34)
        return table

    # ============================================================ 连接
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        if self.history_popup.isVisible():
            self.history_popup.setFixedWidth(self.input.width())
            self.history_popup.move(self._popup_pos())

    def _wire(self) -> None:
        self.btn_search.clicked.connect(self._do_search)
        self.input.returnPressed.connect(self._do_search)
        self.input.textEdited.connect(self._maybe_show_history)
        self.input.installEventFilter(self)
        # 真机踩过：历史下拉用 Qt.Popup 会**抢走鼠标**
        # （现象：点"我喜欢的音乐"没反应，只能点搜索框或某条历史）。
        # 现在改成非模态工具窗 + "点在别处就关"（见 showEvent/hideEvent/eventFilter）。

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        # 只有"这一页正在显示"时才需要监听全应用点击。
        # 装在 QApplication 上是因为 Qt 的事件过滤器**只对目标对象本身生效**，
        # 不会沿父链传递（装在顶层窗口上收不到子控件的点击，实测无效）。
        # 但 QApplication 是进程共享的，所以 hideEvent 里必须卸掉，
        # 否则多个窗口会把过滤器越积越多（测试里 60 个窗口直接慢 20 倍）。
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().hideEvent(event)
        self.history_popup.hide()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        for search_type, button in self.tab_buttons.items():
            button.clicked.connect(lambda _checked=False, t=search_type: self._switch_tab(t))
        for search_type, table in self._tables.items():
            table.reached_bottom.connect(lambda t=search_type: self.state.load_more(t))
            table.cellDoubleClicked.connect(lambda row, col, t=search_type: self._on_row_activated(t, row))
            table.itemSelectionChanged.connect(lambda t=search_type: self._clear_selection(t))

        self.state.page_loaded.connect(self._on_page_loaded)
        self.state.loading_changed.connect(self._on_loading_changed)
        self.state.search_failed.connect(self._on_failed)
        self.history_popup.itemClicked.connect(self._on_history_clicked)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.input and event.type() == QEvent.FocusIn:
            self._maybe_show_history()
            return False
        # 应用级过滤：任何落在"搜索框 + 下拉框"之外的鼠标按下，都关掉下拉
        if watched is not self.input and event.type() == QEvent.MouseButtonPress:
            if self.history_popup.isVisible():
                widget = QApplication.widgetAt(event.globalPosition().toPoint())
                if widget is None or not self._owns(widget):
                    self.history_popup.hide()
        return super().eventFilter(watched, event)

    def _owns(self, widget: QWidget) -> bool:
        """判断控件是否属于搜索框或历史下拉（属于则不关）。"""
        node = widget
        while node is not None:
            if node in (self.input, self.history_popup):
                return True
            node = node.parentWidget()
        return False

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt 命名
        super().focusOutEvent(event)
        self.history_popup.hide()

    # ============================================================ 交互
    def _do_search(self) -> None:
        keyword = self.input.text().strip()
        if not keyword:
            self.footer.setText("请输入关键词")
            return
        self.history_popup.hide()
        self.state.search(keyword, self.current_type)
        self._refresh_tables()
        self.footer.setText(f"正在搜索「{keyword}」…")
        self._set_empty_hint([])

    def _switch_tab(self, search_type: int) -> None:
        self.current_type = search_type
        self.tab_buttons[search_type].setChecked(True)
        for other, button in self.tab_buttons.items():
            button.setChecked(other == search_type)
        self.stack.setCurrentWidget(self._tables[search_type])
        self._refresh_tables()
        state = self.state.tab(search_type)
        if state.items:
            self._set_footer(state.count, state.total, state.loading, state.finished)
        else:
            self.footer.setText("")

    def clear_results(self) -> None:
        """登出时清空四个 tab 的结果（避免留着上一位用户的搜索内容）。"""
        for table in self._tables.values():
            table.clear_rows()
        self._set_empty_hint([])

    def _set_empty_hint(self, items: list) -> None:
        """没有结果时显示空状态，否则显示表格。"""
        self.stack.setCurrentWidget(self.empty if not items else self._tables[self.current_type])

    def _on_row_activated(self, search_type: int, row: int) -> None:
        state = self.state.tab(search_type)
        if not (0 <= row < len(state.items)):
            return
        item = state.items[row]
        if search_type == ep.SEARCH_TYPE_SONG:
            self.play_requested.emit(list(state.items), row)
        elif search_type == ep.SEARCH_TYPE_ARTIST:
            self.open_entity.emit("artist", item.id)
        elif search_type == ep.SEARCH_TYPE_ALBUM:
            self.open_entity.emit("album", item.id)
        else:
            self.open_entity.emit("playlist", item.id)

    def _clear_selection(self, search_type: int) -> None:
        """单击不选中行（保留双击播放语义，避免视觉噪音）。"""
        table = self._tables[search_type]
        table.clearSelection()

    # ============================================================ 历史下拉
    def _maybe_show_history(self) -> None:
        if self.input.text().strip():
            self.history_popup.hide()
            return
        items = self.state.history.items if self.state.history else []
        if not items:
            self.history_popup.hide()
            return
        # 只列最近 5 条（用户要求），顺便保证一屏放得下、不出滚动条
        items = items[:HISTORY_VISIBLE]
        self.history_popup.clear()
        for keyword in items:
            entry = QListWidgetItem(keyword)
            entry.setData(Qt.UserRole, keyword)
            entry.setToolTip(keyword)
            self.history_popup.addItem(entry)
        self.history_popup.setFixedWidth(self.input.width())
        # 高度正好等于内容高度：不放滚动条，也不留一块空白
        self.history_popup.setFixedHeight(HISTORY_ROW_H * len(items) + 6)
        # 页面内局部坐标：贴着搜索框下沿
        self.history_popup.move(self._popup_pos())
        self.history_popup.raise_()  # 压在 tab 行和表格上面
        self.history_popup.show()

    def _popup_pos(self) -> QPoint:
        """下拉左上角在页面内的位置：搜索框正下方，右边界和搜索框对齐。"""
        top_left = self.input.mapTo(self, self.input.rect().bottomLeft())
        width = self.history_popup.width()
        # 别超出页面右边界
        max_x = max(0, self.width() - width)
        return QPoint(min(top_left.x(), max_x), top_left.y() + 2)

    def _on_history_clicked(self, item: QListWidgetItem) -> None:
        keyword = item.data(Qt.UserRole)
        self.history_popup.hide()
        if keyword:
            self.input.setText(keyword)
            self._do_search()

    # ============================================================ 渲染
    def _on_page_loaded(self, search_type: int, items: list) -> None:
        if search_type == self.current_type:
            self._refresh_tables()
        state = self.state.tab(search_type)
        self._set_footer(state.count, state.total, state.loading, state.finished)

    def _on_loading_changed(self, search_type: int, loading: bool, finished: bool) -> None:
        self._tables[search_type].set_finished(finished)
        state = self.state.tab(search_type)
        self._set_footer(state.count, state.total, loading, finished)

    def _on_failed(self, search_type: int, message: str) -> None:
        if search_type == self.current_type:
            self.footer.setText(f"搜索失败：{message}")

    def _set_footer(self, count: int, total: int, loading: bool, finished: bool) -> None:
        if loading:
            self.footer.setText(f"加载中…（已加载 {count} 条）")
        elif not count:
            self.footer.setText("没有找到相关内容")
        elif finished:
            self.footer.setText(f"共 {count} 条" + (f"（全部 {total} 条）" if total > count else ""))
        else:
            self.footer.setText(f"已加载 {count} / {total} 条，继续滚动加载更多")

    def _refresh_tables(self) -> None:
        """按当前 tab 重绘表格（只重绘可见 tab，切 tab 时再重绘另一个）。"""
        for search_type, table in self._tables.items():
            if search_type != self.current_type:
                continue
            items = self.state.items(search_type)
            table.setRowCount(0)
            self._like_buttons[search_type] = {}
            table.setRowCount(len(items))
            for row, item in enumerate(items):
                if search_type == ep.SEARCH_TYPE_SONG:
                    self._fill_song_row(table, row, item)
                elif search_type == ep.SEARCH_TYPE_ARTIST:
                    self._fill_artist_row(table, row, item)
                elif search_type == ep.SEARCH_TYPE_ALBUM:
                    self._fill_album_row(table, row, item)
                else:
                    self._fill_playlist_row(table, row, item)
            state = self.state.tab(search_type)
            table.set_finished(state.finished)
            table._fit_columns()  # 数据换了内容宽度也变，重新适配消灭横向滚动
            self._set_empty_hint(items)
            self._set_footer(state.count, state.total, state.loading, state.finished)

    # ---------------------------------------------------------- 各 tab 的行
    def _fill_song_row(self, table: _ResultTable, row: int, song: Song) -> None:
        liked = self._is_liked.get(song.id, False)
        table.setItem(row, 0, table._text(str(row + 1), Qt.AlignCenter, 0))
        table.setItem(row, 1, table._text(song.name, Qt.AlignVCenter, 1))
        table.setItem(row, 2, table._text(song.artist_text, Qt.AlignVCenter, 2))
        table.setItem(row, 3, table._text(song.album, Qt.AlignVCenter, 3))
        table.setItem(row, 4, table._text(song.duration_text, Qt.AlignCenter, 4))
        button = _LikeButton(song, liked)
        button.clicked.connect(lambda _=None, s=song, b=button: self._toggle_like(s, b))
        table.setCellWidget(row, 5, button)
        self._like_buttons[ep.SEARCH_TYPE_SONG][song.id] = button

    def _fill_artist_row(self, table: _ResultTable, row: int, artist) -> None:
        table.setItem(row, 0, table._text(artist.name, Qt.AlignVCenter, 0))
        table.setItem(row, 1, table._text(str(artist.album_size), Qt.AlignCenter, 1))
        table.setItem(row, 2, table._text(str(artist.music_size), Qt.AlignCenter, 2))

    def _fill_album_row(self, table: _ResultTable, row: int, album) -> None:
        table.setItem(row, 0, table._text(album.name, Qt.AlignVCenter, 0))
        table.setItem(row, 1, table._text(album.artist, Qt.AlignVCenter, 1))
        table.setItem(row, 2, table._text(str(album.size), Qt.AlignCenter, 2))

    def _fill_playlist_row(self, table: _ResultTable, row: int, playlist) -> None:
        table.setItem(row, 0, table._text(playlist.name, Qt.AlignVCenter, 0))
        table.setItem(row, 1, table._text(str(playlist.track_count), Qt.AlignCenter, 1))
        table.setItem(row, 2, table._text(playlist.creator, Qt.AlignVCenter, 2))

    # ============================================================ 收藏
    def _toggle_like(self, song: Song, button: _LikeButton) -> None:
        desired = not self._is_liked.get(song.id, False)
        button.set_liked(desired)
        self._is_liked[song.id] = desired
        self.like_toggled.emit(song, desired)

    def set_liked(self, song_id: int, liked: bool) -> None:
        """外部（播放控制器）同步收藏状态到表格图标。"""
        self._is_liked[song_id] = liked
        button = self._like_buttons.get(ep.SEARCH_TYPE_SONG, {}).get(song_id)
        if button is not None:
            button.set_liked(liked)

    # ============================================================ 杂项
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(760, 480)


__all__ = ["SearchView", "format_duration"]
