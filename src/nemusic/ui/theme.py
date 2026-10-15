"""外观系统：浅色/深色两套配色 + 5 种背景预设。

为什么这么做：用户反馈"页面换成白底"且"要有背景图功能"。
这里把样式表拆成三层，切换外观只改最上面两层：

1. ``BASE``      —— 与颜色无关的结构（内边距、圆角、字号、边框有无）
2. ``THEMES``    —— 浅色 / 深色各自的配色覆盖（**放在 BASE 之后**，同等特异性下后者胜）
3. ``BACKDROP``  —— 背景层，画在 QMainWindow 上；页面容器跟着变半透明才能透出来

预设（``PRESETS``）= 一套完整的外观：背景渐变 + 配套主题。
用户上传自己的图片时沿用当前主题的配色，只换背景层。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..util import log

_log = log.get("ui.theme")

#: 主色（两套主题共用，品牌红）
ACCENT = "#e8556d"
ACCENT_HOVER = "#ef6579"


@dataclass(frozen=True)
class Preset:
    """一种背景预设。"""

    key: str
    name: str
    hint: str
    theme: str
    #: 背景层 QSS（画在主窗口上）
    css: str


#: 5 种预设背景（用户要求"5 种不同风格"）
PRESETS: tuple[Preset, ...] = (
    Preset(
        key="mist",
        name="晨雾",
        hint="浅色 · 几乎纯白，最耐看",
        theme="light",
        css="qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffffff, stop:0.5 #f4f6fa, stop:1 #e2e8f2)",
    ),
    Preset(
        key="paper",
        name="纸墨",
        hint="浅色 · 米黄纸感，长看护眼",
        theme="light",
        css="qlineargradient(x1:0, y1:0, x2:0.7, y2:1, stop:0 #fdf6e8, stop:0.55 #f3e6cd, stop:1 #e0cfa8)",
    ),
    Preset(
        key="dusk",
        name="晚霞",
        hint="深色 · 橙紫渐变，适合夜间",
        theme="dark",
        css="qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #241436, stop:0.45 #55285c, stop:1 #a8455a)",
    ),
    Preset(
        key="ocean",
        name="夜海",
        hint="深色 · 蓝绿渐变，沉稳",
        theme="dark",
        css="qlineargradient(x1:0, y1:0, x2:0.8, y2:1, stop:0 #0a1622, stop:0.5 #123a48, stop:1 #1c6b63)",
    ),
    Preset(
        key="forest",
        name="远山",
        hint="浅色 · 青绿渐变，清爽",
        theme="light",
        css="qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #eef8f0, stop:0.5 #d3ecdc, stop:1 #a9d4bd)",
    ),
)

PRESET_BY_KEY = {preset.key: preset for preset in PRESETS}
DEFAULT_PRESET = "mist"

#: 主题展示名
THEME_LABELS = {"light": "浅色", "dark": "深色"}

#: 每个主题的默认预设（手动切主题时用它，保证文字可读）
DEFAULT_PRESET_BY_THEME = {"light": "mist", "dark": "dusk"}


# ---------------------------------------------------------------- 结构层
BASE = """
QWidget { font-size: 13px; }
QLabel { background: transparent; }

QListWidget#nav::item { padding-left: 14px; margin: 2px 8px; border-radius: 8px; }
QListWidget#nav::item:hover { }

QLineEdit#searchInput { border-radius: 8px; padding: 5px 12px; }
QLineEdit#searchInput:focus { }

QPushButton#tabButton { border: none; background: transparent; padding: 5px 12px;
                        border-radius: 7px; font-size: 13px; }
QPushButton#tabButton:hover { }
QPushButton#tabButton:checked { font-weight: 600; }

QListWidget#historyPopup { border-radius: 8px; outline: none; }
QListWidget#historyPopup::item { padding: 5px 12px; border-radius: 6px; }

/* QScrollArea 的视口（qt_scrollarea_viewport）也是普通 QWidget，
   会被上面的 QWidget 规则命中画一层底色，把背景盖住——必须显式透明 */
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget#qt_scrollarea_viewport { background: transparent; }
QScrollArea QWidget { background: transparent; }

/* 兜底 background 规则删掉之后，这些带调色板默认底色的容器也要显式透明，
   否则深色主题下表头会是一条系统默认的白带 */
QHeaderView { background: transparent; }
QAbstractItemView { background: transparent; }

QTableWidget { background: transparent; alternate-background-color: transparent; border: none;
              outline: none; gridline-color: transparent; selection-background-color: transparent; }
QTableWidget::item { padding: 0 6px; border: none; }
QTableWidget QTableCornerButton::section { background: transparent; border: none; }
QHeaderView::section { background: transparent; border: none; padding: 4px 6px;
                       font-size: 12px; font-weight: 400; }
QHeaderView::section:hover { background: transparent; }

QPushButton#cellButton { border: none; background: transparent; border-radius: 13px; }
QPushButton#circleButton { border-radius: 16px; background: transparent; }
QPushButton#flatButton { border: none; background: transparent; border-radius: 6px; }
QPushButton#coverButton { border-radius: 8px; }
QPushButton#lyricButton { border-radius: 4px; padding: 1px 6px; background: transparent; font-size: 12px; }

QLabel#lyricCover { border-radius: 10px; }
QLabel#lyricLine { font-size: 17px; padding: 6px 0; }
QLabel#lyricLine[current="true"] { font-size: 21px; font-weight: 600; }
QLabel#lyricSubLine { font-size: 13px; padding-bottom: 8px; }
QLabel#lyricSubLine[current="true"] { }
QLabel#lyricHint { font-size: 15px; }
QScrollArea#lyricScroll { background: transparent; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
/* 横向滚动条**不隐藏**：万一哪天真溢出，用户得看得见（以前深色下看不见被反馈过） */
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { border-radius: 5px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }

QSlider::groove:horizontal { height: 4px; border-radius: 2px; }
QSlider::sub-page:horizontal { border-radius: 2px; }
QSlider::handle:horizontal { width: 11px; height: 11px; margin: -4px 0; border-radius: 5px; }

QFrame#loginCard { border-radius: 14px; }
QLabel#loginTitle { font-size: 19px; font-weight: 600; }
QLabel#qrBox { border-radius: 10px; }
QFrame#meBox { background: transparent; }
QLabel#avatarBox { border-radius: 28px; font-size: 20px; }
QLabel#meLabel { font-size: 14px; }

QPushButton#primaryButton, QPushButton#secondaryButton {
  padding: 7px 18px; border-radius: 8px; border: 1px solid transparent; }
QPushButton#primaryButton:disabled { }

QLabel#sectionTitle { font-size: 15px; font-weight: 600; }
QLabel#settingsHead { font-size: 12px; }
QLabel#settingsName { font-size: 13px; }
QLabel#settingsValue { font-size: 13px; }
QLabel#settingsHint { font-size: 12px; }
QPushButton#secondaryButton:disabled { }
QSlider#settingsSlider::groove:horizontal { height: 4px; border-radius: 2px; }
QSlider#settingsSlider::handle:horizontal { width: 14px; margin: -6px 0; border-radius: 7px; }

/* 外观页 */
QLabel#presetName { font-size: 13px; font-weight: 600; }
QLabel#presetHint { font-size: 11px; }
QLabel#presetThumb { border-radius: 8px; }
QLabel#presetThumb[current="true"] { border: 2px solid %(accent)s; }
QPushButton#presetButton { padding: 5px 14px; border-radius: 7px; font-size: 12px; }
"""

#: 浅色主题（默认）：白底 + 深色文字。面板用半透明白，让背景透出来。
THEMES: dict[str, str] = {
    "light": """
/* 注意：这里**不给** background。一旦给了，所有被布局拉满的容器
   （QLabel / QScrollArea 视口 / 表格内部视口）都会画一层不透明底色，
   把 QMainWindow 上的背景渐变/图片盖得严严实实。需要底色的控件各自显式声明。 */
QWidget { color: #23252b; }
QMainWindow, QStackedWidget { background: transparent; }
QWidget#pageStack, QStackedWidget#pageStack { background: transparent; }
/* central 和页面根控件必须显式透明，否则 QMainWindow 上的背景永远透不上来 */
QWidget#central { background: transparent; }
/* 页面自己再蒙一层轻纱：背景看得出，同时压一压花哨图片对文字的干扰 */
QWidget#page { background: rgba(255,255,255, %(veil)s); }
QToolTip { background: #ffffff; color: #23252b; border: 1px solid #d5d7dd; }

/* 左导航：略微不透明，保证文字清晰 */
QListWidget#nav { background: rgba(255,255,255,0.72); border: none; outline: none; padding-top: 8px; }
QListWidget#nav::item { color: #5c5f68; }
QListWidget#nav::item:selected { background: rgba(232,85,109,0.14); color: #23252b; }
QListWidget#nav::item:hover { background: rgba(0,0,0,0.06); }

/* 播放条 */
QWidget#playerBar { background: rgba(255,255,255,0.86); border-top: 1px solid rgba(0,0,0,0.08); }
QLabel#trackTitle { font-size: 14px; font-weight: 600; color: #1c1e23; }
QLabel#trackArtist { color: #6b6e77; }
QLabel#timeLabel { color: #7a7d86; font-size: 12px; min-width: 42px; }
QLabel#pageTitle { font-size: 20px; font-weight: 600; color: #1c1e23; }
QLabel#pageHint { color: #7a7d86; }
QLabel#emptyHint { color: #8f939c; font-size: 14px; }

/* 搜索页 */
QLineEdit#searchInput { background: rgba(255,255,255,0.9); border: 1px solid #d5d7dd;
                        color: #1c1e23; selection-background-color: %(accent)s; }
QLineEdit#searchInput:focus { border-color: %(accent)s; }
QPushButton#tabButton { color: #6b6e77; }
QPushButton#tabButton:hover { color: #1c1e23; background: rgba(0,0,0,0.06); }
QPushButton#tabButton:checked { color: #1c1e23; background: rgba(0,0,0,0.09); }
QListWidget#historyPopup { background: #ffffff; border: 1px solid #d5d7dd; }
QListWidget#historyPopup::item { color: #3a3d44; }
QListWidget#historyPopup::item:hover { background: %(accent)s; color: #ffffff; }
QTableWidget { color: #2b2e35; }
QHeaderView::section { color: #8f939c; }
QPushButton#cellButton:hover { background: rgba(0,0,0,0.07); }
QLabel#cellLabel { background: transparent; }

QPushButton#circleButton { border: 1px solid #c9ccd3; color: #4a4d55; }
QPushButton#circleButton:hover { background: rgba(0,0,0,0.06); border-color: #a9adb6; }
QPushButton#circleButton:disabled { border-color: #dfe1e6; }
QPushButton#flatButton { color: #4a4d55; }
QPushButton#flatButton:hover { background: rgba(0,0,0,0.06); }
QPushButton#coverButton { background: rgba(0,0,0,0.05); border: 1px solid #d5d7dd; }
QPushButton#coverButton:hover { border-color: #a9adb6; }
QPushButton#lyricButton { color: #4a4d55; border: 1px solid #c9ccd3; }
QPushButton#lyricButton:hover { background: rgba(0,0,0,0.06); }

/* 歌词页 */
QLabel#lyricCover { background: rgba(0,0,0,0.06); }
QLabel#lyricCredits { color: #8f939c; font-size: 12px; }
QLabel#lyricLine { color: #6b6e77; }
QLabel#lyricLine[current="true"] { color: #14161a; }
QLabel#lyricSubLine { color: #9a9ea7; }
QLabel#lyricSubLine[current="true"] { color: #5c5f68; }
QLabel#lyricHint { color: #8f939c; }
/* 用实色：rgba 在滚动条把手上会渲染成纯黑（实测），深色主题下尤其刺眼 */
QScrollBar::handle:vertical { background: #c9ccd4; }
QScrollBar::handle:vertical:hover { background: #a9adb6; }
/* 凹槽也必须给实色：写 transparent 的话 Qt 不画，回落到系统调色板，
   在深色主题下就是一条纯黑竖条（用户截图里见过） */
QScrollBar:vertical { background: #e9ebf0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: #e9ebf0; }

/* 全部用实色：rgba 会让背景从进度条/音量条里透出来（用户截图反馈过） */
QSlider::groove:horizontal { background: #dfe1e6; }
QSlider::add-page:horizontal { background: #dfe1e6; }
QSlider::sub-page:horizontal { background: %(accent)s; }
QSlider::handle:horizontal { background: #ffffff; border: 1px solid #c9ccd3; }
QSlider#volumeSlider::sub-page:horizontal { background: #9a9ea7; }
QSlider:disabled::sub-page:horizontal { background: #e6e8ec; }

/* 登录卡片 */
QFrame#loginCard { background: rgba(255,255,255,0.9); border: 1px solid #e2e4e9; }
QLabel#loginSub { color: #6b6e77; }
QLabel#qrBox { background: #ffffff; color: #8f939c; }
QLabel#loginStatus { color: #4a4d55; }
QLabel#loginStatus[warn="true"] { color: #c9771c; }
QLabel#avatarBox { background: rgba(0,0,0,0.07); color: #6b6e77; }

QPushButton#primaryButton { background: %(accent)s; color: #ffffff; }
QPushButton#primaryButton:hover { background: %(accent_hover)s; }
QPushButton#primaryButton:disabled { background: #f0b8c1; color: #ffffff; }
QPushButton#secondaryButton { color: #4a4d55; border-color: #c9ccd3; }
QPushButton#secondaryButton:hover { background: rgba(0,0,0,0.05); }

/* 设置页 */
QLabel#sectionTitle { color: #1c1e23; }
QLabel#settingsHead { color: #8f939c; }
QLabel#settingsName { color: #3a3d44; }
QLabel#settingsValue { color: #14161a; }
QLabel#settingsHint { color: #7a7d86; }
QLabel#settingsHint[warn="true"] { color: #c9771c; }
QPushButton#secondaryButton:disabled { color: #a9adb6; border-color: #dfe1e6; }
QSlider#settingsSlider::groove:horizontal { background: #dfe1e6; }
QSlider#settingsSlider::sub-page:horizontal { background: %(accent)s; }
QSlider#settingsSlider::add-page:horizontal { background: #dfe1e6; }
QSlider#settingsSlider::handle:horizontal { background: #ffffff; border: 1px solid rgba(0,0,0,0.18); }

/* 外观页 */
QLabel#presetName { color: #1c1e23; }
QLabel#presetHint { color: #8f939c; }
QLabel#presetThumb { background: rgba(0,0,0,0.05); border: 2px solid transparent; }
QPushButton#presetButton { color: #4a4d55; border: 1px solid #c9ccd3; background: transparent; }
QPushButton#presetButton:hover { background: rgba(0,0,0,0.05); }

QStatusBar { background: rgba(255,255,255,0.72); color: #7a7d86; }
""",
    "dark": """
/* 注意：这里**不给** background。一旦给了，所有被布局拉满的容器
   （QLabel / QScrollArea 视口 / 表格内部视口）都会画一层不透明底色，
   把 QMainWindow 上的背景渐变/图片盖得严严实实。需要底色的控件各自显式声明。 */
QWidget { color: #e8e8e8; }
QMainWindow, QStackedWidget { background: transparent; }
QWidget#pageStack, QStackedWidget#pageStack { background: transparent; }
/* central 和页面根控件必须显式透明，否则 QMainWindow 上的背景永远透不上来 */
QWidget#central { background: transparent; }
/* 页面自己再蒙一层轻纱：背景看得出，同时压一压花哨图片对文字的干扰 */
QWidget#page { background: rgba(18,18,21, %(veil)s); }
QToolTip { background: #2a2b30; color: #e8e8e8; border: 1px solid #3a3b41; }

QListWidget#nav { background: rgba(16,17,20,0.82); border: none; outline: none; padding-top: 8px; }
QListWidget#nav::item { color: #b9b9bd; }
QListWidget#nav::item:selected { background: #2f3034; color: #ffffff; }
QListWidget#nav::item:hover { background: #26272b; }

QWidget#playerBar { background: rgba(28,29,33,0.9); border-top: 1px solid #303136; }
QLabel#trackTitle { font-size: 14px; font-weight: 600; color: #f4f4f6; }
QLabel#trackArtist { color: #9a9aa0; }
QLabel#timeLabel { color: #8f8f95; font-size: 12px; min-width: 42px; }
QLabel#pageTitle { font-size: 20px; font-weight: 600; color: #f2f2f5; }
QLabel#pageHint { color: #8f8f95; }
QLabel#emptyHint { color: #75757b; font-size: 14px; }

QLineEdit#searchInput { background: #2a2b30; border: 1px solid #3a3b41;
                        color: #f0f0f2; selection-background-color: %(accent)s; }
QLineEdit#searchInput:focus { border-color: %(accent)s; }
QPushButton#tabButton { color: #9a9aa0; }
QPushButton#tabButton:hover { color: #e8e8e8; background: #2f3034; }
QPushButton#tabButton:checked { color: #ffffff; background: #3a3b41; }
QListWidget#historyPopup { background: #2a2b30; border: 1px solid #3a3b41; }
QListWidget#historyPopup::item { color: #d8d8dc; }
QListWidget#historyPopup::item:hover { background: %(accent)s; color: #ffffff; }
QTableWidget { color: #e8e8e8; }
QHeaderView::section { color: #75757b; }
QPushButton#cellButton:hover { background: #33343a; }
QLabel#cellLabel { background: transparent; }

QPushButton#circleButton { border: 1px solid #4a4b52; color: #cfcfd4; }
QPushButton#circleButton:hover { background: #33343a; border-color: #5d5e66; }
QPushButton#circleButton:disabled { border-color: #3a3b41; }
QPushButton#flatButton { color: #cfcfd4; }
QPushButton#flatButton:hover { background: #33343a; }
QPushButton#coverButton { background: #303136; border: 1px solid #3c3d43; }
QPushButton#coverButton:hover { border-color: #5d5e66; }
QPushButton#lyricButton { color: #cfcfd4; border: 1px solid #4a4b52; }
QPushButton#lyricButton:hover { background: #33343a; }

QLabel#lyricCover { background: #232733; }
QLabel#lyricCredits { color: #75757b; font-size: 12px; }
QLabel#lyricLine { color: #8f8f95; }
QLabel#lyricLine[current="true"] { color: #ffffff; }
QLabel#lyricSubLine { color: #6a6a70; }
QLabel#lyricSubLine[current="true"] { color: #b9b9bd; }
QLabel#lyricHint { color: #75757b; }
QScrollBar::handle:vertical { background: #3c3d43; }
QScrollBar::handle:vertical:hover { background: #4d4e56; }
QScrollBar:vertical { background: #26272b; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: #26272b; }

QSlider::groove:horizontal { background: #3c3d43; }
QSlider::add-page:horizontal { background: #3c3d43; }
QSlider::sub-page:horizontal { background: %(accent)s; }
QSlider::handle:horizontal { background: #ffffff; }
QSlider#volumeSlider::sub-page:horizontal { background: #9a9aa0; }
QSlider:disabled::sub-page:horizontal { background: #404148; }

QFrame#loginCard { background: #27282d; border: 1px solid #35363c; }
QLabel#loginSub { color: #9a9aa0; }
QLabel#qrBox { background: #ffffff; color: #666; }
QLabel#loginStatus { color: #b9b9bd; }
QLabel#loginStatus[warn="true"] { color: #f0a24a; }
QLabel#avatarBox { background: #33343a; color: #cfcfd4; }

QPushButton#primaryButton { background: %(accent)s; color: #ffffff; }
QPushButton#primaryButton:hover { background: %(accent_hover)s; }
QPushButton#primaryButton:disabled { background: #6d3a45; color: #c9b7bb; }
QPushButton#secondaryButton { color: #cfcfd4; border-color: #4a4b52; }
QPushButton#secondaryButton:hover { background: #33343a; }

QLabel#sectionTitle { color: #e8e8ec; }
QLabel#settingsHead { color: #7d7d84; }
QLabel#settingsName { color: #d5d5da; }
QLabel#settingsValue { color: #f2f2f5; }
QLabel#settingsHint { color: #8f8f95; }
QLabel#settingsHint[warn="true"] { color: #f0a24a; }
QPushButton#secondaryButton:disabled { color: #6a6a70; border-color: #38393f; }
QSlider#settingsSlider::groove:horizontal { background: #3a3b41; }
QSlider#settingsSlider::sub-page:horizontal { background: %(accent)s; }
QSlider#settingsSlider::add-page:horizontal { background: #3a3b41; }
QSlider#settingsSlider::handle:horizontal { background: #ffffff; }

QLabel#presetName { color: #e8e8ec; }
QLabel#presetHint { color: #8f8f95; }
QLabel#presetThumb { background: #303136; border: 2px solid transparent; }
QPushButton#presetButton { color: #cfcfd4; border: 1px solid #4a4b52; background: transparent; }
QPushButton#presetButton:hover { background: #33343a; }

QStatusBar { background: #191a1d; color: #8f8f95; }
""",
}


#: 预设背景的纱罩浓度（预设本身就和主题配套，轻一点就够）
VEIL_PRESET = 0.28
#: 自定义图片的纱罩浓度（照片可能很花，浓一档保证文字可读）
VEIL_CUSTOM = 0.42


def build_stylesheet(theme: str, backdrop_css: str = "", veil: float = VEIL_PRESET) -> str:
    """拼出完整样式表：结构层 + 主题层 + 背景层。

    ``veil`` 是页面蒙在背景上的那层纱的浓度：越小背景越清楚，越大文字越好读。
    """
    theme_css = THEMES.get(theme) or THEMES["light"]
    subs = {"accent": ACCENT, "accent_hover": ACCENT_HOVER, "veil": veil}
    parts = [BASE % subs, theme_css % subs]
    if backdrop_css:
        parts.append("QMainWindow { %s }" % backdrop_css)
    return "\n".join(parts)


def backdrop_css_for(background: str, image_path: str | None = None) -> str:
    """把配置里的 background 值翻译成背景层 QSS。

    ``background`` 可以是预设 id，或 ``custom:<文件名>``。
    """
    preset = PRESET_BY_KEY.get(background)
    if preset is not None:
        # 必须带属性名！只给 "qlineargradient(...)" 的话整条声明会被 QSS 解析器丢掉，
        # QMainWindow 变成全透明，用户看到的就是"换了没反应"（踩过）。
        return "background: %s" % preset.css
    if background.startswith("custom:") and image_path:
        # border-image：随窗口拉伸，不会重复平铺
        return 'border-image: url("%s") 0 0 0 0 stretch stretch;' % image_path
    return "background: %s" % PRESET_BY_KEY[DEFAULT_PRESET].css


def theme_for(background: str) -> str:
    """某个背景该配哪套主题（预设自带；自定义图片沿用浅色）。"""
    preset = PRESET_BY_KEY.get(background)
    return preset.theme if preset is not None else "light"


__all__ = [
    "ACCENT", "ACCENT_HOVER", "PRESETS", "PRESET_BY_KEY", "DEFAULT_PRESET",
    "THEME_LABELS", "THEMES", "build_stylesheet", "backdrop_css_for", "theme_for",
]
