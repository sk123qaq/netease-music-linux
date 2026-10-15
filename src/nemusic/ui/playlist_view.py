"""「我喜欢的音乐」列表页（AC-3.1~3.4）：全量列表 + 点播 + 播放全部 + 当前行高亮。

只做渲染与交互；数据来自 ``state/library.py``，播放交给主窗口的信号。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..model.types import Playlist, Song
from ..state.library import LibraryState
from .widgets.track_table import TrackTable


class PlaylistView(QWidget):
    """我喜欢的音乐页面。"""

    #: (songs, index) 播放请求
    play_requested = Signal(list, int)
    #: (song, desired) 收藏/取消收藏
    like_toggled = Signal(object, bool)
    #: 请求刷新（用户点"刷新"）
    refresh_requested = Signal()

    def __init__(self, state: LibraryState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._current_id = 0

        self._build()
        self._wire()

    # ============================================================ 构建
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.lbl_title = QLabel("我喜欢的音乐")
        self.lbl_title.setObjectName("pageTitle")
        head.addWidget(self.lbl_title)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("pageHint")
        head.addWidget(self.lbl_count)
        head.addStretch(1)
        self.btn_play_all = QPushButton("播放全部")
        self.btn_play_all.setObjectName("primaryButton")
        self.btn_play_all.setCursor(Qt.PointingHandCursor)
        self.btn_play_all.setEnabled(False)
        head.addWidget(self.btn_play_all)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setObjectName("secondaryButton")
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        head.addWidget(self.btn_refresh)
        root.addLayout(head)

        self.table = TrackTable()
        root.addWidget(self.table, 1)

        self.lbl_hint = QLabel("")
        self.lbl_hint.setObjectName("pageHint")
        self.lbl_hint.setAlignment(Qt.AlignCenter)
        root.addWidget(self.lbl_hint)

    def _wire(self) -> None:
        self.btn_play_all.clicked.connect(self._play_all)
        self.btn_refresh.clicked.connect(self.refresh_requested)
        self.table.row_activated.connect(self._on_row_activated)
        self.table.like_clicked.connect(lambda song, desired: self.like_toggled.emit(song, desired))
        self.state.loading_changed.connect(self._on_loading)
        self.state.songs_loaded.connect(self.set_songs)
        self.state.failed.connect(self._on_failed)
        self.state.like_changed.connect(self.set_liked)

    # ============================================================ 交互
    def _play_all(self) -> None:
        if self.table.songs:
            self.play_requested.emit(list(self.table.songs), 0)

    def _on_row_activated(self, row: int) -> None:
        songs = self.table.songs
        if 0 <= row < len(songs):
            self.play_requested.emit(list(songs), row)

    def _on_loading(self, loading: bool) -> None:
        self.btn_refresh.setEnabled(not loading)
        self.lbl_hint.setText("正在加载我喜欢的音乐…" if loading else self.lbl_hint.text())

    def _on_failed(self, message: str) -> None:
        self.lbl_hint.setText(message)

    # ============================================================ 渲染
    def set_songs(self, songs: list[Song]) -> None:
        playlist: Playlist | None = self.state.playlist
        self.lbl_count.setText(f"{len(songs)} 首" + (f" / 云端 {playlist.track_count} 首" if playlist and playlist.track_count > len(songs) else ""))
        self.btn_play_all.setEnabled(bool(songs))
        self.lbl_hint.setText("双击任意一首开始播放" if songs else "")
        self.table.set_songs(songs, {s.id: bool(self.state.is_liked(s.id)) for s in songs})
        self.table.highlight(self.table._current_id)

    def set_liked(self, song_id: int, liked: bool) -> None:
        """外部同步收藏状态。"""
        self.state.liked[int(song_id)] = liked
        self.table.set_liked(song_id, liked)

    def highlight(self, song_id: int) -> None:
        self.table.highlight(song_id)

    def sizeHint(self) -> QSize:  # noqa: N803 - Qt 命名
        return QSize(760, 480)


def _color(hex_value: str):
    from PySide6.QtGui import QColor

    return QColor(hex_value)


__all__ = ["PlaylistView"]
