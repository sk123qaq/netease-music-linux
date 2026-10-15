"""可复用的曲目表格（「我喜欢的音乐」与「专辑/歌单详情」共用）。

列：``#`` / 歌曲 / 歌手 / 专辑 / 时长 / ♥。只负责渲染与交互，
播放与收藏通过信号交出去。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ...model.types import Song
from ...util import icons


class _LikeButton(QPushButton):
    """行尾收藏按钮。"""

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


#: 主色（当前播放行高亮）；与 ui/theme.py 的 ACCENT 保持一致
ACCENT_COLOR = QColor("#e8556d")

#: 固定宽度列（#/时长/♥），列宽自适应时先扣掉它们
COL_INDEX, COL_TIME, COL_LIKE = 42, 60, 34


class TrackTable(QTableWidget):
    """曲目表格：双击播放 / 行尾收藏 / 当前行高亮。"""

    #: 双击某一行（row）
    row_activated = Signal(int)
    #: 点了收藏按钮（song, 期望状态）
    like_clicked = Signal(object, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("trackTable")
        self.setColumnCount(6)
        self.setHorizontalHeaderLabels(["#", "歌曲", "歌手", "专辑", "时长", ""])
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setShowGrid(False)
        self.setAlternatingRowColors(False)
        self.verticalHeader().setVisible(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalHeader().setDefaultSectionSize(36)
        header = self.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        # 注意：这两列**不能**用 ResizeToContents —— 它会在数据到达后把列
        # 撑回内容宽度，把 _fit_columns 的适配覆盖掉，横向滚动条又回来了。
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        for col in (0, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        self.setColumnWidth(0, 42)
        self.setColumnWidth(4, 60)
        self.setColumnWidth(5, 34)

        self.songs: list[Song] = []
        self._liked: dict[int, bool] = {}
        self._buttons: dict[int, _LikeButton] = {}
        self._current_id = 0
        self._fitting = False

        self.cellDoubleClicked.connect(lambda row, _col: self.row_activated.emit(row))
        self.itemSelectionChanged.connect(self.clearSelection)

    # ------------------------------------------------------------ 文字颜色
    #: 次要文字所在列（用半透明弱一档，不写死颜色 → 自动适配浅色/深色）
    _DIM_COLUMNS = frozenset({0, 2, 3, 4})

    def _ink(self, column: int) -> QColor:
        """从 palette 取文字色。

        实测：QSS 的 ``QTableWidget { color: ... }`` 会反映到
        ``palette().color(QPalette.Text)``，但**已创建的 item 的 foreground
        不会自动跟随**，所以每次建 item / 切主题都要重新取一次。
        """
        color = QColor(self.palette().color(QPalette.Text))
        if column in self._DIM_COLUMNS:
            color.setAlphaF(0.62)
        return color

    def refresh_colors(self) -> None:
        """主题切换后重新上色（并重放当前行高亮，否则粉色会被冲掉）。"""
        for row in range(self.rowCount()):
            for column in range(self.columnCount()):
                item = self.item(row, column)
                if item is not None:
                    item.setForeground(self._ink(column))
        self.highlight(self._current_id)

    # ------------------------------------------------------------ 列宽自适应
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._fit_columns()

    def _fit_columns(self) -> None:
        """按视口宽度重新分配列宽，**保证不出现横向滚动条**。

        真机反馈：窗口未最大化时底部冒出横向滚动条，而且深色下几乎看不见。
        根因是歌手/专辑两列用 ``ResizeToContents`` —— 内容多宽就多宽，永不收缩，
        六列最小宽度之和一旦超过视口就必然横向滚动。
        改成：歌手/专辑按内容但各设上限，剩余全给歌曲列。
        """
        if self._fitting:
            return
        self._fitting = True
        try:
            viewport = self.viewport().width()
            if viewport <= 0:
                return
            fixed = COL_INDEX + COL_TIME + COL_LIKE + 6  # 6px 边框/滚动条余量
            rest = viewport - fixed
            if rest < 120:  # 窗口太窄，交给 Qt 自己处理
                return
            cap = max(90, int(rest * 0.34))
            artist = min(self._content_width(2), cap)
            album = min(self._content_width(3), cap)
            song = max(90, rest - artist - album)
            self.setColumnWidth(1, song)
            self.setColumnWidth(2, artist)
            self.setColumnWidth(3, album)
        finally:
            self._fitting = False

    def _content_width(self, column: int) -> int:
        """某列内容需要的宽度（含内边距）。"""
        header_hint = self.horizontalHeader().sectionSizeHint(column)
        content_hint = self.sizeHintForColumn(column)
        return max(header_hint, content_hint) + 14

    # ============================================================ 数据
    def set_songs(self, songs: list[Song], liked: dict[int, bool] | None = None) -> None:
        """整表重绘。``liked`` 为 song_id → 是否已收藏。"""
        self.songs = list(songs)
        self._liked = dict(liked or {})
        self._buttons = {}
        self.setRowCount(0)
        self.setRowCount(len(self.songs))
        for row, song in enumerate(self.songs):
            self.setItem(row, 0, self._text(str(row + 1), Qt.AlignCenter, "#8f8f95"))
            self.setItem(row, 1, self._text(song.name, Qt.AlignVCenter, "#f0f0f2"))
            self.setItem(row, 2, self._text(song.artist_text, Qt.AlignVCenter, "#b8b8be"))
            self.setItem(row, 3, self._text(song.album, Qt.AlignVCenter, "#b8b8be"))
            self.setItem(row, 4, self._text(song.duration_text, Qt.AlignCenter, "#8f8f95"))
            button = _LikeButton(song, bool(self._liked.get(song.id)))
            button.clicked.connect(lambda _=None, s=song, b=button: self._on_like(s, b))
            self.setCellWidget(row, 5, button)
            self._buttons[song.id] = button
        self.highlight(self._current_id)
        # 数据变了内容宽度也变，必须重新适配：
        # 否则 ResizeToContents 会把歌手/专辑列撑回内容宽度，又出现横向滚动
        self._fit_columns()

    def clear(self) -> None:
        self.songs = []
        self._liked = {}
        self._buttons = {}
        self._current_id = 0
        self.setRowCount(0)

    # ============================================================ 交互
    def _on_like(self, song: Song, _button: _LikeButton) -> None:
        self.like_clicked.emit(song, not bool(self._liked.get(song.id)))

    def set_liked(self, song_id: int, liked: bool) -> None:
        """外部同步收藏状态（控制器确认后调用）。"""
        self._liked[int(song_id)] = liked
        button = self._buttons.get(int(song_id))
        if button is not None:
            button.set_liked(liked)

    def highlight(self, song_id: int) -> None:
        """高亮当前播放行（当前行用主色，加粗歌名）。"""
        self._current_id = int(song_id)
        for row, song in enumerate(self.songs):
            is_current = song.id == self._current_id
            for col in range(5):
                item = self.item(row, col)
                if item is None:
                    continue
                if is_current:
                    item.setForeground(ACCENT_COLOR)
                else:
                    item.setForeground(self._ink(col))
                font = item.font()
                font.setBold(is_current and col == 1)
                item.setFont(font)

    def _text(self, value: str, align, column: int) -> QTableWidgetItem:
        item = QTableWidgetItem(value)
        item.setTextAlignment(int(align))
        item.setForeground(self._ink(column))
        return item


__all__ = ["TrackTable"]
