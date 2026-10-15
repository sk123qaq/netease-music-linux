"""设置页：缓存占用与清理、缓存上限、音质策略、账号信息（AC-5.4~5.6）。

设计约束（SPEC §4）：
- 统计/清理都是磁盘 I/O，**必须**在子线程里做，结果用 Qt 信号回主线程；
- 本页只读 ``CacheManager`` 与 ``Config``，不直接碰网络；
- 清理期间禁用按钮，避免连点。
"""

from __future__ import annotations

import shutil
import subprocess

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..config import QUALITY_LADDER
from ..state.cache import CacheManager, format_size
from ..util import icons, log
from ..util.async_task import AsyncTask

_log = log.get("ui.settings_view")

#: 缓存类别：kind → (显示名, 说明)
CACHE_KINDS: tuple[tuple[str, str, str], ...] = (
    ("audio", "音频", "播完整首才落盘；超上限按 LRU 淘汰"),
    ("playlist", "歌单 / 我喜欢", "我喜欢的音乐与专辑歌单详情"),
    ("lyric", "歌词", "行级歌词与翻译"),
    ("cover", "封面", "播放条与歌词页的专辑封面"),
)

#: 音质阶梯 → 展示名
LEVEL_LABELS = {"exhigh": "极高（320 kbps）", "higher": "较高（192 kbps）", "standard": "标准（128 kbps）"}

OPEN_CMD = {"xdg-open": ["xdg-open"], "gio": ["gio", "open"], "nautilus": ["nautilus"]}


class SettingsView(QWidget):
    """设置页。信号用于通知外部（主窗口）做后续动作。"""

    #: 请求退出登录（主窗口弹确认 + 调 session_state.logout_async）
    logout_requested = Signal()
    #: 缓存上限变化（主窗口可同步状态栏提示）
    limit_changed = Signal(int)

    def __init__(self, cache: CacheManager, config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cache = cache
        self.config = config
        self._rows: dict[str, dict[str, QLabel]] = {}
        self._buttons: list[QPushButton] = []
        self._task: AsyncTask | None = None
        self._clear_task: AsyncTask | None = None
        self._trim_task: AsyncTask | None = None
        self._build()
        self._wire()
        self.refresh_async()

    # ============================================================ 构建
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 12)
        outer.setSpacing(14)

        title = QLabel("设置")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = QWidget()
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 4, 8, 0)
        body.setSpacing(18)

        body.addLayout(self._build_cache_section())
        body.addLayout(self._build_limit_section())
        body.addLayout(self._build_quality_section())
        body.addLayout(self._build_account_section())
        body.addStretch(1)

        scroll.setWidget(page)
        outer.addWidget(scroll, 1)

        self.status = QLabel("")
        self.status.setObjectName("settingsHint")
        outer.addWidget(self.status)

    def _section(self, title: str, hint: str) -> tuple[QVBoxLayout, QVBoxLayout]:
        """返回 (小节容器, 内容布局)。"""
        box = QVBoxLayout()
        box.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        head.addWidget(label)
        head.addStretch(1)
        if hint:
            tip = QLabel(hint)
            tip.setObjectName("settingsHint")
            head.addWidget(tip)
        box.addLayout(head)
        inner = QVBoxLayout()
        inner.setSpacing(6)
        box.addLayout(inner)
        return box, inner

    def _build_cache_section(self) -> QVBoxLayout:
        box, inner = self._section("缓存占用", "")

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        for col, text in enumerate(("类别", "占用", "文件数", "")):
            if text:
                head = QLabel(text)
                head.setObjectName("settingsHead")
                grid.addWidget(head, 0, col)
        for row, (kind, name, _hint) in enumerate(CACHE_KINDS, start=1):
            label = QLabel(name)
            label.setObjectName("settingsName")
            size = QLabel("…")
            size.setObjectName("settingsValue")
            count = QLabel("…")
            count.setObjectName("settingsValue")
            button = QPushButton("清空")
            button.setObjectName("secondaryButton")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=kind: self._on_clear(k))
            grid.addWidget(label, row, 0)
            grid.addWidget(size, row, 1)
            grid.addWidget(count, row, 2)
            grid.addWidget(button, row, 3)
            self._rows[kind] = {"size": size, "count": count}
            self._buttons.append(button)
        inner.addLayout(grid)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        clear_all = QPushButton("清空全部缓存")
        clear_all.setObjectName("primaryButton")
        clear_all.setCursor(Qt.PointingHandCursor)
        clear_all.clicked.connect(lambda: self._on_clear("all"))
        self._buttons.append(clear_all)
        actions.addWidget(clear_all)

        open_dir = QPushButton("打开缓存目录")
        open_dir.setObjectName("secondaryButton")
        open_dir.setCursor(Qt.PointingHandCursor)
        open_dir.clicked.connect(self._on_open_dir)
        self._buttons.append(open_dir)
        actions.addWidget(open_dir)
        actions.addStretch(1)
        inner.addLayout(actions)
        return box

    def _build_limit_section(self) -> QVBoxLayout:
        box, inner = self._section("音频缓存上限", "超过后按「最近最少使用」淘汰")

        row = QHBoxLayout()
        row.setSpacing(12)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setObjectName("settingsSlider")
        self.slider.setRange(50, 8192)  # 50 MB ~ 8 GB
        self.slider.setSingleStep(50)
        self.slider.setPageStep(256)
        self.slider.setValue(int(self.config.cache_limit_mb))
        self.slider.setFixedWidth(320)
        row.addWidget(self.slider)
        self.limit_value = QLabel(format_size(self.config.cache_limit_bytes))
        self.limit_value.setObjectName("settingsValue")
        self.limit_value.setMinimumWidth(90)
        row.addWidget(self.limit_value)
        row.addStretch(1)
        inner.addLayout(row)

        hint = QLabel("上限只约束音频缓存；歌单/歌词/封面体积很小，不受影响。")
        hint.setObjectName("settingsHint")
        inner.addWidget(hint)
        return box

    def _build_quality_section(self) -> QVBoxLayout:
        box, inner = self._section("音质策略", "自动降级，无需手动选择")

        ladder = QLabel(" → ".join(LEVEL_LABELS.get(level, level) for level in QUALITY_LADDER))
        ladder.setObjectName("settingsValue")
        inner.addWidget(ladder)

        hint = QLabel("优先取最高音质；无版权或该音质下架时自动往下试，失败会提示原因并跳到下一首。")
        hint.setObjectName("settingsHint")
        hint.setWordWrap(True)
        inner.addWidget(hint)
        return box

    def _build_account_section(self) -> QVBoxLayout:
        box, inner = self._section("账号", "cookie 仅保存在本机")

        self.account_label = QLabel("未登录")
        self.account_label.setObjectName("settingsValue")
        inner.addWidget(self.account_label)

        row = QHBoxLayout()
        row.setSpacing(10)
        logout = QPushButton("退出登录")
        logout.setObjectName("secondaryButton")
        logout.setCursor(Qt.PointingHandCursor)
        logout.clicked.connect(self.logout_requested.emit)
        self._buttons.append(logout)
        row.addWidget(logout)
        row.addStretch(1)
        inner.addLayout(row)
        return box

    def _wire(self) -> None:
        self.slider.valueChanged.connect(self._on_limit_changed)

    # ============================================================ 统计 / 清理
    def refresh_async(self) -> None:
        """后台统计缓存占用（SPEC §4：磁盘 I/O 在子线程）。"""
        if self._task is not None and self._task.is_running:
            return
        self._set_busy(True)
        self._task = AsyncTask(self.cache.stats)
        self._task.done.connect(self._on_stats)  # 绑定方法，不能用 lambda（AsyncTask 保活）
        self._task.failed.connect(self._on_failed)
        self._task.start()

    def _on_stats(self, stats: dict) -> None:
        self._apply_stats(stats)
        self._set_busy(False)

    def _on_failed(self, message: str) -> None:
        self._set_busy(False)
        self._show(f"统计缓存失败：{message}", warn=True)

    def _apply_stats(self, stats: dict) -> None:
        for kind, row in self._rows.items():
            info = stats.get(kind) or {}
            count = int(info.get("count") or 0)
            size = float(info.get("bytes") or 0)
            row["count"].setText(f"{count} 个")
            row["size"].setText(format_size(size))

    def _set_busy(self, busy: bool) -> None:
        for button in self._buttons:
            button.setEnabled(not busy)
        if busy:
            for row in self._rows.values():
                row["size"].setText("…")
                row["count"].setText("…")

    def _on_clear(self, kind: str) -> None:
        """清理一类（或全部）缓存。"""
        self._show(f"正在清理{self._label(kind)}…")
        self._set_busy(True)
        task = AsyncTask(self._clear_kind, kind)
        task.done.connect(self._on_cleared)
        task.failed.connect(self._on_failed)
        # 这里必须持有引用，否则任务对象被回收后 done/failed 投递不到
        self._clear_task = task
        task.start()

    def _clear_kind(self, kind: str) -> tuple[str, int, int]:
        """子线程：真删文件。"""
        count, size = self.cache.clear(kind)
        return kind, count, size

    def _on_cleared(self, payload: tuple) -> None:
        kind, count, size = payload
        self._show(f"已清理{self._label(kind)}：{count} 个文件，释放 {format_size(size)}")
        self.refresh_async()

    @staticmethod
    def _label(kind: str) -> str:
        if kind == "all":
            return "全部缓存"
        return {k: n for k, n, _ in CACHE_KINDS}.get(kind, kind)

    def _on_open_dir(self) -> None:
        directory = self.cache.open_dir("audio")
        opened = False
        for _name, argv in OPEN_CMD.items():
            if shutil.which(argv[0]) is None:
                continue
            try:
                subprocess.Popen(argv + [str(directory)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                opened = True
                break
            except OSError:  # pragma: no cover
                continue
        if opened:
            self._show(f"已请求打开：{directory}")
        else:
            self._show(f"请手动打开：{directory}", warn=True)

    # ============================================================ 上限
    def _on_limit_changed(self, value: int) -> None:
        self.config.set_cache_limit_mb(value)
        self.config.save()
        self.limit_value.setText(format_size(self.config.cache_limit_bytes))
        self.limit_changed.emit(self.config.cache_limit_bytes)
        # 上限可能被调小：立刻淘汰一次，不用等下次播放（AC-5.6）
        if self._trim_task is None or not self._trim_task.is_running:
            self._trim_task = AsyncTask(self.cache.trim_audio)
            self._trim_task.done.connect(self._on_trimmed)
            self._trim_task.failed.connect(self._on_failed)
            self._trim_task.start()

    def _on_trimmed(self, removed: int) -> None:
        if removed:
            self._show(f"已按 LRU 淘汰 {removed} 个音频缓存（上限 {format_size(self.config.cache_limit_bytes)}）")
            self.refresh_async()

    # ============================================================ 账号 / 提示
    def set_account(self, account) -> None:
        if account is None:
            self.account_label.setText("未登录")
            return
        parts = [f"{account.nickname or '用户'}（UID {account.uid}）"]
        if account.vip_type:
            parts.append("VIP")
        self.account_label.setText(" · ".join(parts))

    def _show(self, message: str, *, warn: bool = False) -> None:
        self.status.setText(message)
        self.status.setProperty("warn", warn)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def sizeHint(self) -> QSize:  # pragma: no cover - 仅布局参考
        return QSize(760, 620)
