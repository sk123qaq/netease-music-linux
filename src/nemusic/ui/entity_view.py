"""专辑 / 歌单 / 歌手详情页（AC-2.4）：从搜索结果点进来后看到具体曲目。

这是"推送"页面：不进左侧导航，由搜索结果页双击后打开，顶部可返回。
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

from ..model.types import Song
from .widgets.track_table import TrackTable

#: kind → 中文名
KIND_LABELS = {"artist": "歌手", "album": "专辑", "playlist": "歌单"}


class EntityView(QWidget):
    """专辑/歌单/歌手的曲目列表页。"""

    #: (songs, index) 播放请求
    play_requested = Signal(list, int)
    #: (song, desired) 收藏/取消收藏
    like_toggled = Signal(object, bool)
    #: 点"返回"
    back_requested = Signal()
    #: 点"播放全部"
    play_all_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = ""
        self.entity_id = 0
        self._build()
        self._wire()

    # ============================================================ 构建
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.btn_back = QPushButton("← 返回")
        self.btn_back.setObjectName("secondaryButton")
        self.btn_back.setCursor(Qt.PointingHandCursor)
        head.addWidget(self.btn_back)
        self.lbl_title = QLabel("详情")
        self.lbl_title.setObjectName("pageTitle")
        head.addWidget(self.lbl_title)
        self.lbl_sub = QLabel("")
        self.lbl_sub.setObjectName("pageHint")
        head.addWidget(self.lbl_sub)
        head.addStretch(1)
        self.btn_play_all = QPushButton("播放全部")
        self.btn_play_all.setObjectName("primaryButton")
        self.btn_play_all.setCursor(Qt.PointingHandCursor)
        self.btn_play_all.setEnabled(False)
        head.addWidget(self.btn_play_all)
        root.addLayout(head)

        self.table = TrackTable()
        root.addWidget(self.table, 1)

        self.lbl_hint = QLabel("")
        self.lbl_hint.setObjectName("pageHint")
        self.lbl_hint.setAlignment(Qt.AlignCenter)
        root.addWidget(self.lbl_hint)

    def _wire(self) -> None:
        self.btn_back.clicked.connect(self.back_requested)
        self.btn_play_all.clicked.connect(self.play_all_requested)
        self.table.row_activated.connect(self._on_row)
        self.table.like_clicked.connect(self.like_toggled)

    # ============================================================ 交互
    def _on_row(self, row: int) -> None:
        if 0 <= row < len(self.table.songs):
            self.play_requested.emit(list(self.table.songs), row)

    # ============================================================ 数据
    def set_entity(self, kind: str, entity_id: int, title: str, subtitle: str, songs: list[Song]) -> None:
        """填入一个专辑/歌单/歌手的曲目。"""
        self.kind = kind
        self.entity_id = entity_id
        self.lbl_title.setText(title or KIND_LABELS.get(kind, "详情"))
        self.lbl_sub.setText(subtitle)
        self.lbl_hint.setText("双击任意一首开始播放" if songs else "这个列表没有可播放的歌曲")
        self.btn_play_all.setEnabled(bool(songs))
        self.table.set_songs(songs)

    def set_songs(self, songs: list[Song]) -> None:
        """只换曲目（保留标题/kind），登出清空时用。"""
        self.lbl_hint.setText("双击任意一首开始播放" if songs else "这个列表没有可播放的歌曲")
        self.btn_play_all.setEnabled(bool(songs))
        self.table.set_songs(songs)

    def set_liked(self, song_id: int, liked: bool) -> None:
        self.table.set_liked(song_id, liked)

    def highlight(self, song_id: int) -> None:
        self.table.highlight(song_id)

    def songs(self) -> list[Song]:
        return list(self.table.songs)

    def sizeHint(self) -> QSize:  # noqa: N803 - Qt 命名
        return QSize(760, 480)


__all__ = ["EntityView", "KIND_LABELS"]
