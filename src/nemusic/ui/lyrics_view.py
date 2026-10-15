"""歌词页（SPEC AC-4.4/4.5/4.6）。

交互约定：
- 点播放条封面或「词」按钮进入；点左上角「← 返回」回到来时的页面（不断播）；
- 当前行放大加粗并**居中**，用 500ms 缓动滚过去（不是生硬跳转）；
- 用户手动滚动（滚轮/拖动滚动条）后 **5 秒内不自动跟随**，让用户能看上下文；
- 有翻译时显示副行；无歌词/纯音乐给明确提示；
- 顶部显示大封面 + 歌曲/歌手 + 歌词 credits（作词/作曲等）。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..model.lyric_index import LyricIndex
from ..model.types import LyricDoc, Song
from ..util import icons

#: 自动跟随恢复延迟（AC-4.5：手动滚动 5s 恢复）
FOLLOW_RESUME_MS = 5000
#: 滚动动画时长（AC-4.4：缓动居中）
SCROLL_MS = 500
#: 单行高度（含副行）；与 _build 里的字号配合
ROW_HEIGHT = 56


class _Animator(QObject):
    """把一次滚动拆成定时小步，做出缓动手感（不引 QPropertyAnimation，省心）。"""

    def __init__(self, scrollbar, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bar = scrollbar
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)
        self._from = 0
        self._to = 0
        self._elapsed = 0
        self._total = max(SCROLL_MS, 1)

    def animate_to(self, value: int) -> None:
        self._from = self._bar.value()
        self._to = max(self._bar.minimum(), min(self._bar.maximum(), int(value)))
        self._elapsed = 0
        self._total = max(SCROLL_MS, 1)
        if self._from == self._to:
            self._timer.stop()
            return
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def is_animating(self) -> bool:
        return self._timer.isActive()

    def _step(self) -> None:
        self._elapsed += self._timer.interval()
        t = min(1.0, self._elapsed / self._total)
        # OutCubic：起步快、收尾稳
        eased = 1 - (1 - t) ** 3
        self._bar.setValue(int(self._from + (self._to - self._from) * eased))
        if t >= 1.0:
            self._timer.stop()


class LyricsView(QWidget):
    """歌词页。对外暴露信号，不自己碰网络/播放器。"""

    #: 点击返回按钮
    back_requested = Signal()
    #: 点某一行跳到该时间（毫秒）
    seek_requested = Signal(int)

    def __init__(self, config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._song: Song | None = None
        self._index = LyricIndex()
        self._rows: list[QLabel] = []
        self._sub_rows: list[QLabel] = []
        self._current = -1
        self._follow = True
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self._resume_follow)
        self._build()

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 18)
        root.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.btn_back = QPushButton()
        self.btn_back.setObjectName("flatButton")
        self.btn_back.setFixedSize(30, 30)
        self.btn_back.setIcon(icons.icon("prev", "#cfcfcf", 18))
        self.btn_back.setToolTip("返回")
        self.btn_back.setCursor(Qt.PointingHandCursor)
        self.btn_back.clicked.connect(self.back_requested)
        top.addWidget(self.btn_back)
        self.lbl_title = QLabel("未在播放")
        self.lbl_title.setObjectName("trackTitle")
        top.addWidget(self.lbl_title)
        self.lbl_artist = QLabel("")
        self.lbl_artist.setObjectName("trackArtist")
        top.addWidget(self.lbl_artist)
        top.addStretch(1)
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(28)

        # 左侧：大封面
        left = QVBoxLayout()
        left.setSpacing(10)
        self.lbl_cover = QLabel()
        self.lbl_cover.setObjectName("lyricCover")
        self.lbl_cover.setFixedSize(260, 260)
        self.lbl_cover.setAlignment(Qt.AlignCenter)
        self.lbl_cover.setPixmap(_placeholder_pixmap(260))
        left.addWidget(self.lbl_cover)
        self.lbl_credits = QLabel("")
        self.lbl_credits.setObjectName("lyricCredits")
        self.lbl_credits.setWordWrap(True)
        self.lbl_credits.setFixedWidth(260)
        left.addWidget(self.lbl_credits)
        left.addStretch(1)
        body.addLayout(left)

        # 右侧：滚动歌词
        self.scroll = QScrollArea()
        self.scroll.setObjectName("lyricScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.inner = QWidget()
        self.inner.setObjectName("lyricInner")
        self.rows_layout = QVBoxLayout(self.inner)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        self.rows_layout.addStretch(1)  # 顶部留白，让第一行能居中
        self.scroll.setWidget(self.inner)
        self._anim = _Animator(self.scroll.verticalScrollBar(), self)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_user_scroll)
        # 滚轮/点击都算用户操作：给滚动区视口和滚动条装事件过滤器
        self.scroll.viewport().installEventFilter(self)
        self.scroll.verticalScrollBar().installEventFilter(self)
        body.addWidget(self.scroll, 1)

        root.addLayout(body, 1)

    # ------------------------------------------------------------ 对外状态
    def set_song(self, song: Song | None) -> None:
        """切歌：清空歌词区（新歌词由 :meth:`set_lyric` 填入）。"""
        self._song = song
        self._index = LyricIndex()
        self._rows = []
        self._sub_rows = []
        self._current = -1
        self._follow = True
        self._resume_timer.stop()
        self._anim.stop()
        self._clear_rows()
        if song is None:
            self.lbl_title.setText("未在播放")
            self.lbl_artist.setText("")
            self.lbl_credits.setText("")
            return
        self.lbl_title.setText(song.name)
        self.lbl_artist.setText(song.artist_text)
        self._set_hint("歌词加载中…")

    def set_cover(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            self.lbl_cover.setPixmap(_placeholder_pixmap(260))
            return
        self.lbl_cover.setPixmap(
            pixmap.scaled(self.lbl_cover.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        )

    def set_lyric(self, doc: LyricDoc) -> None:
        """填入歌词并重建行（当前行复位）。"""
        self._index = LyricIndex(doc)
        self._current = -1
        self._follow = True
        self._resume_timer.stop()
        self._anim.stop()
        self._clear_rows()
        self._rows = []
        self._sub_rows = []
        self.lbl_credits.setText("　".join(doc.credits[:3]))
        if doc.lines:
            for line in doc.lines:
                row = QLabel(line.text)
                row.setObjectName("lyricLine")
                row.setWordWrap(True)
                row.setMinimumHeight(ROW_HEIGHT // 2)
                self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
                self._rows.append(row)
                sub = QLabel(line.translation)
                sub.setObjectName("lyricSubLine")
                sub.setWordWrap(True)
                sub.setVisible(bool(line.translation))
                self.rows_layout.insertWidget(self.rows_layout.count() - 1, sub)
                self._sub_rows.append(sub)
        else:
            self._set_hint("暂无歌词" if (self._song is not None or doc.song_id) else "未在播放")
        # 立刻做一次布局：否则 row.y() 还是 0，第一行居中会定位错
        self.rows_layout.activate()
        self.scroll.verticalScrollBar().setValue(0)

    def set_position(self, position_ms: int, duration_ms: int = 0) -> None:
        """进度驱动：高亮当前行并在跟随模式下居中。"""
        if self._index.empty or not self._rows:
            return
        idx = self._index.index_at(position_ms)
        if idx == self._current:
            return
        previous, self._current = self._current, idx
        for i in {previous, idx}:  # 只重染受影响的两行
            if 0 <= i < len(self._rows):
                for widget in (self._rows[i], self._sub_rows[i]):
                    widget.setProperty("current", i == idx)
                    widget.style().unpolish(widget)
                    widget.style().polish(widget)
        if self._follow:
            self._center_on(idx)

    # ------------------------------------------------------------ 内部
    def _set_hint(self, text: str) -> None:
        self._clear_rows()
        hint = QLabel(text)
        hint.setObjectName("lyricHint")
        hint.setAlignment(Qt.AlignCenter)
        hint.setMinimumHeight(120)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, hint)
        self._rows = []
        self._sub_rows = []

    def _clear_rows(self) -> None:
        while self.rows_layout.count() > 1:  # 最后一个是 stretch
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _center_on(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._rows):
            return
        row = self._rows[idx]
        target = row.y() + row.height() // 2 - self.scroll.viewport().height() // 2
        bar = self.scroll.verticalScrollBar()
        self._anim.animate_to(target)
        _ = bar  # 动画器持有

    def eventFilter(self, watched, event) -> bool:
        if watched in (self.scroll.viewport(), self.scroll.verticalScrollBar()):
            if event.type() in (QEvent.Wheel, QEvent.MouseButtonPress, QEvent.MouseMove):
                self._on_user_scroll(self.scroll.verticalScrollBar().value())
        return super().eventFilter(watched, event)

    def _on_user_scroll(self, value: int) -> None:
        """用户手动滚动：暂停跟随 5 秒（AC-4.5）。"""
        if self._anim._timer.isActive():
            return  # 动画自己造成的滚动不算用户操作
        self._follow = False
        self._anim.stop()
        self._resume_timer.start(FOLLOW_RESUME_MS)

    def _resume_follow(self) -> None:
        self._follow = True
        if self._current >= 0:
            self._center_on(self._current)

def _placeholder_pixmap(size: int) -> QPixmap:
    """无封面时的占位图（深色底 + 音符）。"""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    from PySide6.QtGui import QColor, QPainter

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#232733"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(pix.rect(), 10, 10)
    icon = icons.icon("music", "#5a6070", size // 3)
    pix_icon = icon.pixmap(icon.actualSize(QSize(size // 3, size // 3)))
    painter.drawPixmap(
        pix.rect().center().x() - pix_icon.width() // 2,
        pix.rect().center().y() - pix_icon.height() // 2,
        pix_icon,
    )
    painter.end()
    return pix


__all__ = ["LyricsView"]
