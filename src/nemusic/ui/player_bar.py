"""底部播放条（对照 SPEC §1.2 参考图布局）。

M1 只搭结构：按钮/滑块的**信号契约先定下来**，逻辑在 M4 接入播放控制器。
控件在无播放内容时处于禁用态，避免误导。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..model.types import Song, format_duration
from ..util import icons

MODE_ORDER = ("order", "list_loop", "single_loop", "shuffle")
MODE_ICON = {"order": "order", "list_loop": "list_loop", "single_loop": "single_loop", "shuffle": "shuffle"}
MODE_LABEL = {"order": "顺序播放", "list_loop": "列表循环", "single_loop": "单曲循环", "shuffle": "随机播放"}


class PlayerBar(QWidget):
    """播放条。对外只暴露信号，内部不直接调用网络/播放器。"""

    #: 上一首 / 播放暂停 / 下一首
    prev_clicked = Signal()
    play_pause_clicked = Signal()
    next_clicked = Signal()
    #: 拖动进度条（毫秒）；拖动中也会发，用于预览
    seek_requested = Signal(int)
    seek_preview = Signal(int)
    #: 音量（0-100）
    volume_changed = Signal(int)
    mute_toggled = Signal(bool)
    #: 播放模式切换（order/list_loop/single_loop/shuffle）
    mode_changed = Signal(str)
    #: 点击收藏（收藏/取消由上层决定）与封面（进歌词页）
    like_clicked = Signal()
    cover_clicked = Signal()
    #: 点击歌词按钮（等价于点封面）
    lyrics_clicked = Signal()

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._song: Song | None = None
        self._dragging = False
        self.setObjectName("playerBar")
        self.setFixedHeight(76)
        self._build()
        self.set_play_mode(config.play_mode)
        self.set_volume(config.volume, config.muted)
        self.set_enabled_controls(False)

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(12)

        # 主控三键
        self.btn_prev = self._circle_button("prev", "上一首")
        self.btn_play = self._circle_button("play", "播放/暂停", big=True)
        self.btn_next = self._circle_button("next", "下一首")
        self.btn_prev.clicked.connect(self.prev_clicked)
        self.btn_play.clicked.connect(self.play_pause_clicked)
        self.btn_next.clicked.connect(self.next_clicked)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            controls.addWidget(b)
        root.addLayout(controls)

        # 封面（点击进歌词页）
        self.btn_cover = QPushButton()
        self.btn_cover.setObjectName("coverButton")
        self.btn_cover.setFixedSize(52, 52)
        self.btn_cover.setToolTip("点击查看歌词")
        self.btn_cover.setCursor(Qt.PointingHandCursor)
        self.btn_cover.clicked.connect(self.cover_clicked)
        root.addWidget(self.btn_cover)

        # 标题 / 歌手 / 进度
        centre = QVBoxLayout()
        centre.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.lbl_title = QLabel("未在播放")
        self.lbl_title.setObjectName("trackTitle")
        self.lbl_artist = QLabel("")
        self.lbl_artist.setObjectName("trackArtist")
        self.btn_lyric = QPushButton("词")
        self.btn_lyric.setObjectName("lyricButton")
        self.btn_lyric.setToolTip("滚动歌词")
        self.btn_lyric.setCursor(Qt.PointingHandCursor)
        self.btn_lyric.clicked.connect(self.lyrics_clicked)
        title_row.addWidget(self.lbl_title, 0)
        title_row.addWidget(self.lbl_artist, 0)
        title_row.addWidget(self.btn_lyric, 0)
        title_row.addStretch(1)
        centre.addLayout(title_row)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)
        self.lbl_pos = QLabel("00:00")
        self.lbl_pos.setObjectName("timeLabel")
        self.sld_progress = QSlider(Qt.Horizontal)
        self.sld_progress.setObjectName("progressSlider")
        self.sld_progress.setRange(0, 0)
        self.sld_progress.setPageStep(5000)
        self.sld_progress.sliderPressed.connect(self._on_seek_start)
        self.sld_progress.sliderReleased.connect(self._on_seek_end)
        self.sld_progress.sliderMoved.connect(self._on_seek_move)
        self.lbl_dur = QLabel("00:00")
        self.lbl_dur.setObjectName("timeLabel")
        progress_row.addWidget(self.lbl_pos)
        progress_row.addWidget(self.sld_progress, 1)
        progress_row.addWidget(self.lbl_dur)
        centre.addLayout(progress_row)
        root.addLayout(centre, 1)

        # 右侧功能组
        self.btn_like = self._flat_button("heart_outline", "收藏 / 取消收藏")
        self.btn_like.clicked.connect(self.like_clicked)
        self.btn_mode = self._flat_button(MODE_ICON[self._config.play_mode], "播放模式")
        self.btn_mode.clicked.connect(self._cycle_mode)
        self.btn_mute = self._flat_button("volume", "静音")
        self.btn_mute.clicked.connect(self._toggle_mute)
        self.sld_volume = QSlider(Qt.Horizontal)
        self.sld_volume.setObjectName("volumeSlider")
        self.sld_volume.setRange(0, 100)
        self.sld_volume.setFixedWidth(90)
        self.sld_volume.valueChanged.connect(self._on_volume)
        root.addWidget(self.btn_like)
        root.addWidget(self.btn_mode)
        root.addWidget(self.btn_mute)
        root.addWidget(self.sld_volume)

    def _circle_button(self, name: str, tip: str, big: bool = False) -> QPushButton:
        btn = QPushButton()
        size = 40 if big else 32
        btn.setObjectName("circleButton")
        btn.setFixedSize(size, size)
        btn.setIcon(icons.icon(name, "#f2f2f2", 22 if big else 16))
        btn.setToolTip(tip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setEnabled(False)
        return btn

    def _flat_button(self, name: str, tip: str) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("flatButton")
        btn.setFixedSize(30, 30)
        btn.setIcon(icons.icon(name, "#cfcfcf", 18))
        btn.setToolTip(tip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setEnabled(False)
        return btn

    # ------------------------------------------------------------ 对外状态
    def set_enabled_controls(self, enabled: bool) -> None:
        """无播放内容时禁用除音量/模式以外的控件。"""
        for w in (
            self.btn_prev,
            self.btn_play,
            self.btn_next,
            self.sld_progress,
            self.btn_cover,
            self.btn_lyric,
            self.btn_like,
        ):
            w.setEnabled(enabled)

    def set_song(self, song: Song | None, *, starred: bool = False) -> None:
        """展示当前歌曲。"""
        self._song = song
        if song is None:
            self.lbl_title.setText("未在播放")
            self.lbl_artist.setText("")
            self.set_cover(None)
            self.sld_progress.setRange(0, 0)
            self.lbl_pos.setText("00:00")
            self.lbl_dur.setText("00:00")
            self.set_enabled_controls(False)
            return
        self.set_cover(None)  # 先回落占位图，别闪一下上一首的封面
        self.lbl_title.setText(song.name)
        self.lbl_artist.setText(song.artist_text)
        self.lbl_dur.setText(song.duration_text)
        self.sld_progress.setRange(0, max(song.duration_ms, 0))
        self.set_enabled_controls(True)
        self.set_starred(starred)

    def set_cover(self, pixmap: QPixmap | None) -> None:
        """显示专辑封面（无图时回落到默认音符图标）。"""
        if pixmap is None or pixmap.isNull():
            self.btn_cover.setIcon(icons.icon("music", "#8a8a8a", 24))
            self.btn_cover.setIconSize(QSize(24, 24))
            self.btn_cover.setText("")
            return
        size = self.btn_cover.size()
        scaled = pixmap.scaled(
            max(size.width() - 6, 16), max(size.height() - 6, 16), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        self.btn_cover.setIcon(QIcon(scaled))
        self.btn_cover.setIconSize(QSize(max(size.width() - 6, 16), max(size.height() - 6, 16)))
        self.btn_cover.setText("")

    def set_starred(self, starred: bool) -> None:
        """收藏状态（红心 / 空心）。"""
        name = "heart" if starred else "heart_outline"
        color = "#e8556d" if starred else "#cfcfcf"
        self.btn_like.setIcon(icons.icon(name, color, 18))
        self.btn_like.setToolTip("取消收藏" if starred else "收藏到我喜欢的音乐")

    def set_playing(self, playing: bool) -> None:
        self.btn_play.setIcon(icons.icon("pause" if playing else "play", "#f2f2f2", 22))

    def set_position(self, position_ms: int, duration_ms: int | None = None) -> None:
        """更新进度（拖动中不覆盖用户操作）。"""
        if self._dragging:
            return
        if duration_ms and duration_ms > 0:
            self.sld_progress.setRange(0, duration_ms)
            self.lbl_dur.setText(format_duration(duration_ms))
        self.sld_progress.setValue(max(0, position_ms))
        self.lbl_pos.setText(format_duration(position_ms))

    def set_volume(self, volume: int, muted: bool = False) -> None:
        self.sld_volume.blockSignals(True)
        self.sld_volume.setValue(volume)
        self.sld_volume.blockSignals(False)
        self.btn_mute.setIcon(icons.icon("mute" if muted or volume == 0 else "volume", "#cfcfcf", 18))

    def set_play_mode(self, mode: str) -> None:
        if mode not in MODE_ICON:
            mode = "list_loop"
        self._config.play_mode = mode
        self.btn_mode.setIcon(icons.icon(MODE_ICON[mode], "#cfcfcf", 18))
        self.btn_mode.setToolTip(MODE_LABEL[mode])

    # ------------------------------------------------------------ 内部槽
    def _on_seek_start(self) -> None:
        self._dragging = True

    def _on_seek_move(self, value: int) -> None:
        self.lbl_pos.setText(format_duration(value))
        self.seek_preview.emit(value)

    def _on_seek_end(self) -> None:
        value = self.sld_progress.value()
        self._dragging = False
        self.seek_requested.emit(value)

    def _cycle_mode(self) -> None:
        idx = (MODE_ORDER.index(self._config.play_mode) + 1) % len(MODE_ORDER)
        mode = MODE_ORDER[idx]
        self.set_play_mode(mode)
        self.mode_changed.emit(mode)

    def _toggle_mute(self) -> None:
        self._config.muted = not self._config.muted
        self.set_volume(self._config.volume, self._config.muted)
        self.mute_toggled.emit(self._config.muted)

    def _on_volume(self, value: int) -> None:
        self._config.volume = value
        if value > 0 and self._config.muted:
            self._config.muted = False
        self.set_volume(value, self._config.muted)
        self.volume_changed.emit(value)
