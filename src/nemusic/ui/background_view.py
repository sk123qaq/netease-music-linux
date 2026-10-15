"""外观页：背景预设与自定义图片（用户要求的"设置下面的背景页"）。

布局（左导航第 5 项，紧跟"设置"）：

- **主题**：浅色 / 深色切换
- **背景预设**：5 种风格，每种一张预览缩略图，图下方「应用」按钮
- **我的图片**：用户上传的图片，缩略图下方「应用」+「删除」
- **上传图片**：选本地文件，拷到 ``~/.config/netease-music-linux/backgrounds/``

约定：
- 上传/删除是磁盘 I/O，走 ``AsyncTask`` 子线程（SPEC §4）；
- 缩略图预览用的就是**真实生效的那张背景**，不是另做一份示意图；
- 预设自带配套主题，自定义图片沿用当前主题。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QSlider,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import config as app_config
from ..util import log
from ..util.async_task import AsyncTask
from . import theme

_log = log.get("ui.background_view")

#: 支持的图片格式（按扩展名过滤）
IMAGE_FILTER = "图片 (*.png *.jpg *.jpeg *.bmp *.webp *.gif)"
#: 上传文件大小上限（30 MB）：太大的图解码慢且没必要
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
THUMB_W, THUMB_H = 168, 96


class BackgroundView(QWidget):
    """外观 / 背景设置页。"""

    #: 外观变化（theme, background）——主窗口据此重刷样式表
    appearance_changed = Signal(str, str)

    def __init__(self, config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._preset_thumbs: dict[str, QLabel] = {}
        self._custom_cards: dict[str, QWidget] = {}
        #: 文件名 → 建议主题（上传时在子线程算好，之后应用只查表，不再读盘）
        self._brightness: dict[str, str] = {}
        self._busy = False
        self._blur_task = None
        self.blur_slider = None
        self.blur_value = None
        self._build()
        self.refresh()

    # ============================================================ 构建
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 12)
        outer.setSpacing(14)

        title = QLabel("外观")
        title.setObjectName("pageTitle")
        sub = QLabel("选择一种背景风格，或上传自己的图片")
        sub.setObjectName("pageHint")
        outer.addWidget(title)
        outer.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = QWidget()
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 4, 8, 0)
        body.setSpacing(20)

        body.addLayout(self._build_theme_section())
        body.addLayout(self._build_preset_section())
        body.addLayout(self._build_upload_section())
        body.addStretch(1)

        scroll.setWidget(page)
        outer.addWidget(scroll, 1)

        self.status = QLabel("")
        self.status.setObjectName("settingsHint")
        outer.addWidget(self.status)

    def _section(self, title: str, hint: str = "") -> tuple[QVBoxLayout, QVBoxLayout]:
        box = QVBoxLayout()
        box.setSpacing(10)
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
        inner.setSpacing(8)
        box.addLayout(inner)
        return box, inner

    def _build_theme_section(self) -> QVBoxLayout:
        box, inner = self._section("主题", "浅色为白底，深色为暗底")
        row = QHBoxLayout()
        row.setSpacing(10)
        self._theme_buttons: dict[str, QPushButton] = {}
        for key, label in (("light", "浅色"), ("dark", "深色")):
            button = QPushButton(label)
            button.setObjectName("presetButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=key: self._on_theme(k))
            self._theme_buttons[key] = button
            row.addWidget(button)
        row.addStretch(1)
        inner.addLayout(row)
        return box

    def _build_preset_section(self) -> QVBoxLayout:
        box, inner = self._section("背景预设", "5 种风格，点「应用」立即生效")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        columns = 3
        for index, preset in enumerate(theme.PRESETS):
            card = self._preset_card(preset)
            grid.addWidget(card, index // columns, index % columns)
        inner.addLayout(grid)
        return box

    def _preset_card(self, preset: theme.Preset) -> QWidget:
        card = QFrame()
        card.setObjectName("presetCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        thumb = QLabel()
        thumb.setObjectName("presetThumb")
        thumb.setFixedSize(THUMB_W, THUMB_H)
        thumb.setAlignment(Qt.AlignCenter)
        # 预览用的就是真实生效的那段 QSS，所见即所得
        thumb.setStyleSheet("background: %s;" % preset.css)
        self._preset_thumbs[preset.key] = thumb
        layout.addWidget(thumb, 0, Qt.AlignHCenter)

        name = QLabel(preset.name)
        name.setObjectName("presetName")
        name.setAlignment(Qt.AlignCenter)
        layout.addWidget(name)
        hint = QLabel(preset.hint)
        hint.setObjectName("presetHint")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        apply_button = QPushButton("应用")
        apply_button.setObjectName("presetButton")
        apply_button.setCursor(Qt.PointingHandCursor)
        apply_button.clicked.connect(lambda _=False, k=preset.key: self._apply_preset(k))
        layout.addWidget(apply_button, 0, Qt.AlignHCenter)
        return card

    def _build_upload_section(self) -> QVBoxLayout:
        box, inner = self._section("我的图片", "上传后会保存在本机配置目录")

        self.custom_grid = QGridLayout()
        self.custom_grid.setHorizontalSpacing(14)
        self.custom_grid.setVerticalSpacing(14)
        inner.addLayout(self.custom_grid)

        row = QHBoxLayout()
        row.setSpacing(10)
        upload = QPushButton("上传图片…")
        upload.setObjectName("primaryButton")
        upload.setCursor(Qt.PointingHandCursor)
        upload.clicked.connect(self._on_upload)
        row.addWidget(upload)
        hint = QLabel("支持 png / jpg / webp / bmp，单张不超过 30 MB")
        hint.setObjectName("settingsHint")
        row.addWidget(hint)
        row.addStretch(1)
        inner.addLayout(row)

        # 模糊度：花照片（带字的截图之类）直接当背景，界面文字会没法读
        blur_box = QHBoxLayout()
        blur_box.setSpacing(10)
        blur_label = QLabel("背景模糊度")
        blur_label.setObjectName("settingsName")
        blur_box.addWidget(blur_label)
        self.blur_slider = QSlider(Qt.Horizontal)
        self.blur_slider.setObjectName("settingsSlider")
        self.blur_slider.setRange(0, 100)
        self.blur_slider.setSingleStep(5)
        self.blur_slider.setPageStep(10)
        self.blur_slider.setFixedWidth(220)
        self.blur_slider.setCursor(Qt.PointingHandCursor)
        self.blur_slider.setValue(_clamp(self.config.blur_strength))
        self.blur_slider.valueChanged.connect(self._on_blur_changed)
        blur_box.addWidget(self.blur_slider)
        self.blur_value = QLabel()
        self.blur_value.setObjectName("settingsHint")
        self.blur_value.setFixedWidth(52)
        blur_box.addWidget(self.blur_value)
        blur_box.addStretch(1)
        inner.addLayout(blur_box)

        hint = QLabel("0 用原图，100 最糊；换模糊度会重新生成一份底图")
        hint.setObjectName("settingsHint")
        inner.addWidget(hint)
        return box

    def _on_blur_changed(self, value: int) -> None:
        """改模糊度：存配置、重新生成底图、刷样式表。"""
        value = _clamp(value)
        if value == self.config.blur_strength:
            return
        self.config.blur_strength = value
        self.config.save()
        self.blur_value.setText(_blur_label(value))
        self._regenerate_blur()

    # ============================================================ 数据
    def refresh(self) -> None:
        """按当前配置刷新选中态与"我的图片"列表。"""
        self._ensure_blur()
        current = self.config.background
        for key, thumb in self._preset_thumbs.items():
            thumb.setProperty("current", key == current)
            self._repolish(thumb)
        for key, button in self._theme_buttons.items():
            button.setChecked(key == self.config.theme)
        if self.blur_slider is not None:
            self.blur_slider.blockSignals(True)
            self.blur_slider.setValue(_clamp(self.config.blur_strength))
            self.blur_slider.blockSignals(False)
            self.blur_value.setText(_blur_label(self.config.blur_strength))
        self._reload_custom()

    def _ensure_blur(self) -> None:
        """正在用的自定义图却没有对应模糊版时补一份（含模糊功能上线前传的图）。"""
        if self._blur_task is not None or self.config.blur_strength <= 0:
            return
        if not self.config.background.startswith("custom:"):
            return
        path = app_config.BACKGROUND_DIR / self.config.background[len("custom:"):]
        if not path.is_file() or _blurred_path(path).is_file():
            return
        self._blur_task = AsyncTask(_blur_job, str(path), _divisor(self.config.blur_strength))
        self._blur_task.done.connect(self._on_blur_ready)
        self._blur_task.failed.connect(self._on_failed)
        self._blur_task.start()

    def _regenerate_blur(self) -> None:
        """改了模糊度：按新参数重出底图（0 就干脆用原图）。"""
        if self.config.blur_strength <= 0:
            self._show("已改用原图作背景")
            self.appearance_changed.emit(self.config.theme, self.config.background)
            return
        if self._blur_task is not None:
            return
        if not self.config.background.startswith("custom:"):
            self._show("自定义背景才能调模糊度")
            return
        path = app_config.BACKGROUND_DIR / self.config.background[len("custom:"):]
        if not path.is_file():
            return
        self._show("正在重新生成背景…")
        self._blur_task = AsyncTask(_blur_job, str(path), _divisor(self.config.blur_strength))
        self._blur_task.done.connect(self._on_blur_ready)
        self._blur_task.failed.connect(self._on_failed)
        self._blur_task.start()

    def _on_blur_ready(self, suggested: str) -> None:
        self._blur_task = None
        # 主题由图片亮度定，和导入时走同一套判断，别沿用当前主题
        if suggested and suggested != self.config.theme:
            self.config.theme = suggested
            self.config.save()
        # 底图换成了模糊版，重新刷一遍样式表
        self._show(f"背景模糊度 {self.config.blur_strength}%")
        self.appearance_changed.emit(self.config.theme, self.config.background)

    def _reload_custom(self) -> None:
        """重建已上传图片卡片（增删都在子线程做完后调用）。"""
        # 先清空旧卡片
        while self.custom_grid.count():
            item = self.custom_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._custom_cards.clear()

        directory = app_config.BACKGROUND_DIR
        files: list = []
        if directory.is_dir():
            files = sorted((p for p in directory.iterdir()
                            if p.is_file() and p.suffix.lower() in _EXTS
                            # 模糊版是内部产物，别当成用户上传的图列出来
                            and not p.stem.endswith(_BLUR_SUFFIX)),
                           key=lambda p: p.name)
        columns = 3
        for index, path in enumerate(files):
            key = "custom:%s" % path.name
            card = self._custom_card(path)
            self._custom_cards[key] = card
            self.custom_grid.addWidget(card, index // columns, index % columns)
        if not files:
            empty = QLabel("还没有上传过图片")
            empty.setObjectName("settingsHint")
            self.custom_grid.addWidget(empty, 0, 0)

    def _custom_card(self, path) -> QWidget:
        card = QFrame()
        card.setObjectName("presetCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        thumb = QLabel()
        thumb.setObjectName("presetThumb")
        thumb.setFixedSize(THUMB_W, THUMB_H)
        thumb.setAlignment(Qt.AlignCenter)
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            thumb.setText("无法预览")
        else:
            thumb.setPixmap(_cover(pixmap, THUMB_W, THUMB_H))
        key = "custom:%s" % path.name
        thumb.setProperty("current", self.config.background == key)
        self._repolish(thumb)
        layout.addWidget(thumb, 0, Qt.AlignHCenter)

        name = QLabel(path.name)
        name.setObjectName("presetName")
        name.setAlignment(Qt.AlignCenter)
        name.setMaximumWidth(THUMB_W + 16)
        layout.addWidget(name)

        size = QLabel(_human(path.stat().st_size))
        size.setObjectName("presetHint")
        size.setAlignment(Qt.AlignCenter)
        layout.addWidget(size)

        row = QHBoxLayout()
        row.setSpacing(6)
        apply_button = QPushButton("应用")
        apply_button.setObjectName("presetButton")
        apply_button.setCursor(Qt.PointingHandCursor)
        apply_button.clicked.connect(lambda _=False, n=path.name: self._apply_custom(n))
        row.addWidget(apply_button)
        delete_button = QPushButton("删除")
        delete_button.setObjectName("presetButton")
        delete_button.setCursor(Qt.PointingHandCursor)
        delete_button.clicked.connect(lambda _=False, n=path.name: self._on_delete(n))
        row.addWidget(delete_button)
        layout.addLayout(row)
        return card

    # ============================================================ 交互
    def _apply_preset(self, key: str) -> None:
        preset = theme.PRESET_BY_KEY.get(key)
        if preset is None:
            return
        self.config.background = key
        self.config.theme = preset.theme  # 预设自带配套主题
        self._save_and_emit(f"已应用背景「{preset.name}」")

    def _apply_custom(self, name: str) -> None:
        self.config.background = "custom:%s" % name
        suggested = self._brightness.get(name)
        if suggested is not None and suggested != self.config.theme:
            # 深色照片配浅色主题会把图片糊住，反之亦然；按亮度自动换一套
            self.config.theme = suggested
            self._save_and_emit(f"已应用 {name}，并切换到{theme.THEME_LABELS[suggested]}主题")
            return
        self._save_and_emit(f"已应用背景图片 {name}")

    def _on_theme(self, key: str) -> None:
        """切主题。

        背景透出来之后，文字可读性完全取决于背景与主题是否配套
        （白底配深色文字、暗底配浅色文字）。所以手动切主题时，
        顺手把背景换成该主题的默认预设，避免出现「浅色文字配白底」这种
        什么都看不见的组合。
        """
        self.config.theme = key
        custom = self.config.background.startswith("custom:")
        if not custom:
            preset = theme.DEFAULT_PRESET_BY_THEME.get(key)
            if preset and self.config.background != preset:
                self.config.background = preset
        message = f"已切换到{theme.THEME_LABELS.get(key, key)}主题"
        if custom:
            # 自定义背景不动，但要知道它可能和主题不配套
            message += "；自定义背景仍保留，如看不清可换回预设"
        else:
            message += f"，背景换成「{theme.PRESET_BY_KEY[self.config.background].name}」"
        self._save_and_emit(message)

    def _save_and_emit(self, message: str) -> None:
        self.config.save()
        self.refresh()
        self.appearance_changed.emit(self.config.theme, self.config.background)
        self._show(message)

    def _on_upload(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "选择背景图片", "", IMAGE_FILTER)
        if not path:
            return
        source = Path(path)
        try:
            if source.stat().st_size > MAX_UPLOAD_BYTES:
                self._show("图片太大了（上限 30 MB）", warn=True)
                return
        except OSError as exc:
            self._show(f"读取失败：{exc}", warn=True)
            return
        if source.suffix.lower() not in _EXTS:
            self._show("不支持的图片格式", warn=True)
            return
        self._busy = True
        self._show("正在导入图片…")
        task = AsyncTask(self._copy_into_place, str(source))
        task.done.connect(self._on_uploaded)
        task.failed.connect(self._on_failed)
        self._task = task  # 持有引用，否则信号投递不到
        task.start()

    def _copy_into_place(self, source: str) -> tuple[str, str]:
        """子线程：拷进配置目录（重名自动加序号），生成模糊版，并给主题建议。

        亮度用缩小到 16x16 的图算，成本可忽略；放在这里是因为反正已经读过文件了，
        应用时只查表，不再产生额外的磁盘 I/O。
        """
        app_config.BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
        src = Path(source)
        stem, suffix = src.stem, src.suffix.lower()
        target = app_config.BACKGROUND_DIR / f"{stem}{suffix}"
        index = 1
        while target.exists():
            target = app_config.BACKGROUND_DIR / f"{stem}-{index}{suffix}"
            index += 1
        shutil.copy2(src, target)
        # 先定主题再压色：两者必须一致，否则会出现「深色文字压在暗图上」
        suggested = _suggest_theme(target)
        _make_blurred(target, dark=(suggested == "dark"),
                      divisor=_divisor(self.config.blur_strength))
        return target.name, suggested

    def _on_uploaded(self, payload: tuple) -> None:
        name, suggested = payload
        self._busy = False
        self._brightness[name] = suggested
        self._reload_custom()
        self._apply_custom(name)  # 导进来就直接用上

    def _on_delete(self, name: str) -> None:
        """删除已上传的图片；如果正在用它当背景，先退回默认预设。"""
        self._busy = True
        self._show(f"正在删除 {name}…")
        task = AsyncTask(self._delete_file, name)
        task.done.connect(self._on_deleted)
        task.failed.connect(self._on_failed)
        self._delete_task = task
        task.start()

    @staticmethod
    def _delete_file(name: str) -> str:
        target = app_config.BACKGROUND_DIR / name
        if target.is_file():
            target.unlink()
        blurred = _blurred_path(target)
        if blurred.is_file():
            blurred.unlink()
        return name

    def _on_deleted(self, name: str) -> None:
        self._busy = False
        if self.config.background == "custom:%s" % name:
            # 正在用的图被删了：退回默认预设，别留一个指向不存在文件的配置
            self.config.background = theme.DEFAULT_PRESET
            self.config.theme = theme.PRESET_BY_KEY[theme.DEFAULT_PRESET].theme
            self.config.save()
            self.appearance_changed.emit(self.config.theme, self.config.background)
        self.refresh()
        self._show(f"已删除 {name}")

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self._show(f"操作失败：{message}", warn=True)

    # ============================================================ 工具
    def _show(self, message: str, *, warn: bool = False) -> None:
        self.status.setText(message)
        self.status.setProperty("warn", warn)
        self._repolish(self.status)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    def sizeHint(self) -> QSize:  # pragma: no cover - 仅布局参考
        return QSize(760, 640)


#: 支持的图片扩展名
_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}


#: 模糊版文件名后缀（不含扩展名）
_BLUR_SUFFIX = "_blur"

#: 模糊度 100 对应的缩小倍率（越大越糊）
_MAX_BLUR_DIVISOR = 12


def _clamp(value: int) -> int:
    return max(0, min(100, int(value)))


def _divisor(strength: int) -> int:
    """模糊度(0~100) → 缩小倍率。0 表示不模糊（调用方直接用原图）。"""
    return max(1, 1 + round(_clamp(strength) / 100 * _MAX_BLUR_DIVISOR))


def _blur_label(strength: int) -> str:
    if strength <= 0:
        return "原图"
    if strength < 25:
        return "轻微"
    if strength < 55:
        return "适中"
    if strength < 80:
        return "较糊"
    return "很糊"


def _blurred_path(path) -> Path:
    """模糊版文件的路径：和原图同目录，名字加 _blur，固定用 jpg（照片小得多）。"""
    path = Path(path)  # 调用方有时传 str（子线程那边），统一成 Path
    return path.with_name(f"{path.stem}_blur.jpg")


def _blur_job(path_str: str, divisor: int = 14) -> str:
    """子线程：算亮度建议 + 生成模糊版。返回建议主题（供上层决定要不要切）。"""
    path = Path(path_str)
    suggested = _suggest_theme(path)
    _make_blurred(path, dark=(suggested == "dark"), divisor=max(1, divisor))
    return suggested


def _make_blurred(path, *, dark: bool, divisor: int = 14) -> None:
    """生成模糊版背景：先糊掉细节，再朝主题基色压一档。

    模糊用「缩到 1/BLUR_FACTOR 再放大回去」实现，纯 Qt、快，不引第三方依赖。
    压色是为了对付照片里那些亮得发白的区块——不压的话浅色文字压上去直接消失。
    （失败不影响原图使用，静默跳过。）
    """
    from PySide6.QtGui import QImage, QPainter

    image = QImage(str(path))
    if image.isNull():
        return
    # 太大的图先缩一遍，省内存也省磁盘
    if image.width() > 1920:
        image = image.scaledToWidth(1920, Qt.SmoothTransformation)
    if image.width() < 4 or image.height() < 4:
        return
    divisor = max(1, divisor)
    small = image.scaled(max(2, image.width() // divisor),
                         max(2, image.height() // divisor),
                         Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    blurred = small.scaled(image.width(), image.height(),
                           Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    tint = QColor(16, 17, 20) if dark else QColor(255, 255, 255)
    painter = QPainter(blurred)
    painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
    painter.fillRect(blurred.rect(), QColor(tint.red(), tint.green(), tint.blue(), 115))
    painter.end()
    target = _blurred_path(path)
    try:
        if not blurred.save(str(target), "JPEG", 92):
            _log.warning("背景模糊版保存失败：%s", target)
    except Exception as exc:  # noqa: BLE001 - 模糊失败不该影响导入，但要留痕
        _log.warning("背景模糊版生成异常 %s：%s", path, exc)


def _suggest_theme(path) -> str:
    """按图片平均亮度建议主题：暗图配深色，亮图配浅色。"""
    from PySide6.QtGui import QImage

    image = QImage(str(path))
    if image.isNull():
        return "light"
    small = image.scaled(16, 16, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(
        QImage.Format.Format_RGB32)
    total = 0
    count = 0
    for y in range(small.height()):
        for x in range(small.width()):
            color = small.pixelColor(x, y)
            total += int(0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue())
            count += 1
    if not count:
        return "light"
    return "dark" if (total / count) < 128 else "light"


def _cover(pixmap: QPixmap, width: int, height: int) -> QPixmap:
    """裁成填充式缩略图（不变形）。"""
    return pixmap.scaled(width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)


def _human(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024 or unit == "GB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"
