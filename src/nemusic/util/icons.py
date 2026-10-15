"""自绘图标：内联 SVG 路径 → QIcon（不依赖任何第三方图标字体或资源，见 SPEC §7.1）。

全部图标为几何自绘，24×24 视口，用 ``fill="currentColor"`` 占位后按需着色。
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

#: 图标名 → SVG path（d 属性列表）。几何自绘，与任何既有产品无关。
_PATHS: dict[str, list[str]] = {
    "prev": ["M6 5h2.4v14H6z", "M20 5v14L9.2 12z"],
    "next": ["M15.6 5H18v14h-2.4z", "M4 5l10.8 7L4 19z"],
    "play": ["M7 4.5l13 7.5-13 7.5z"],
    "pause": ["M6.5 5h3.6v14H6.5z", "M13.9 5h3.6v14h-3.6z"],
    "volume": ["M4 9.5h3.4L12 5.6v12.8L7.4 14.5H4z", "M15 9.2a3.6 3.6 0 010 5.6", "M17.6 6.6a7.2 7.2 0 010 10.8"],
    "mute": ["M4 9.5h3.4L12 5.6v12.8L7.4 14.5H4z", "M15.5 9.5l5 5", "M20.5 9.5l-5 5"],
    "list_loop": ["M7 7h8.5l-2.2-2.2L14.7 3.4 19.3 8l-4.6 4.6-1.4-1.4L15.5 9H7a3 3 0 000 6h1v2H7a5 5 0 010-10z",
                  "M17 21l-4.6-4.6 1.4-1.4L16 17.2h1a3 3 0 000-6h-1V9h1a5 5 0 010 10h-.5z"],
    "single_loop": ["M7 7h8.5l-2.2-2.2L14.7 3.4 19.3 8l-4.6 4.6-1.4-1.4L15.5 9H7a3 3 0 000 6h1v2H7a5 5 0 010-10z",
                    "M11 14.4l2-1.2v5h1.6v2h-4.6v-2h1.6v-2.4l-1.4.8z"],
    "shuffle": ["M17 3.4l4 4-4 4-1.4-1.4L17.2 8.4H15c-1.6 0-2.4.7-3.3 2l-1 1.4-1.6-1.4 1-1.4C11.3 6.6 12.7 6.4 15 6.4h2.2L15.6 4.8z",
                "M9.4 14.2l1.6 1.4-.9 1.2c-.9 1.2-1.7 1.8-3.3 1.8H4v-2h2.8c.8 0 1.1-.2 1.6-.8z",
                "M17.2 15.6H15c-1 0-1.7-.2-2.3-.7l3-3.9c.5.5 1.1.6 1.9.6h2.2l-2-2L19.2 8.4l4 4-4 4z"],
    "order": ["M4 6h11v2H4z", "M4 11h8v2H4z", "M4 16h11v2H4z", "M17.5 10.2l4 3.3-4 3.3z"],
    "heart": ["M12 20.4l-1.3-1.2C6.1 15 3 12.2 3 8.7A4.7 4.7 0 017.7 4c1.6 0 3.1.8 4.3 2.2A5.6 5.6 0 0116.3 4A4.7 4.7 0 0121 8.7c0 3.5-3.1 6.3-7.7 10.5z"],
    "heart_outline": ["M12 19.1l-.6-.5C6.8 14.5 4 12 4 8.9A3.7 3.7 0 017.7 5c1.4 0 2.7.7 3.7 1.9l.6.7.6-.7A5 5 0 0116.3 5A3.7 3.7 0 0120 8.9c0 3.1-2.8 5.6-7.4 9.7z"],
    "search": ["M10.5 4a6.5 6.5 0 104.1 11.5l4.2 4.2 1.4-1.4-4.2-4.2A6.5 6.5 0 0010.5 4zm0 2a4.5 4.5 0 110 9 4.5 4.5 0 010-9z"],
    "settings": ["M12 8.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7zm0 2a1.5 1.5 0 110 3 1.5 1.5 0 010-3z",
                 "M10.6 2.6h2.8l.4 2.3 1.6.9 2.1-1 1.4 2.4-1.8 1.5v1.8l1.8 1.5-1.4 2.4-2.1-1-1.6.9-.4 2.3h-2.8l-.4-2.3-1.6-.9-2.1 1L5.1 13l1.8-1.5V9.7L5.1 8.2l1.4-2.4 2.1 1 1.6-.9z"],
    "music": ["M9 18a2.6 2.6 0 11-1.4-2.3V6.4l9-2v9.5a2.6 2.6 0 11-1.4-2.3V7.6l-6.2 1.4z"],
    "image": ["M4 5h16v14H4z", "M4 15l4.5-4.5 3.5 3.5 3-3L20 15", "M8.6 9.4a1.4 1.4 0 103 0 1.4 1.4 0 00-3 0z"],
    "refresh": ["M12 4a8 8 0 016.9 4l-1.7 1A6 6 0 106 12H3.6A8.4 8.4 0 0112 4z", "M18.6 4.2h2.2v5h-2.2z"],
    "link": ["M9.6 14.4l4.8-4.8 1.4 1.4-4.8 4.8z",
             "M13.3 7.4l1.8-1.8a3.4 3.4 0 014.8 4.8l-2.5 2.5-1.4-1.4 2.5-2.5a1.4 1.4 0 00-2-2l-1.8 1.8z",
             "M10.7 16.6l-1.8 1.8a3.4 3.4 0 01-4.8-4.8l2.5-2.5L8 12.5l-2.5 2.5a1.4 1.4 0 002 2l1.8-1.8z"],
    "trash": ["M8 4h8l-.8 2H18v2H6V6h2.8z", "M7 9h10l-.8 11H7.8z"],
    "close": ["M6.4 5l5.6 5.6L17.6 5 19 6.4 13.4 12 19 17.6 17.6 19 12 13.4 6.4 19 5 17.6 10.6 12 5 6.4z"],
}


def svg_bytes(name: str, color: str = "#e8e8e8") -> bytes:
    """生成着色后的 SVG 字节流。"""
    if name not in _PATHS:
        raise KeyError(f"未知图标：{name}")
    body = "".join(f'<path d="{d}"/>' for d in _PATHS[name])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="24" height="24" fill="{color}">{body}</svg>'
    )
    return svg.encode("utf-8")


@lru_cache(maxsize=256)
def pixmap(name: str, color: str = "#e8e8e8", size: int = 20) -> QPixmap:
    """光栅化图标为 QPixmap（带缓存）。"""
    renderer = QSvgRenderer(QByteArray(svg_bytes(name, color)))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return pm


@lru_cache(maxsize=256)
def icon(name: str, color: str = "#e8e8e8", size: int = 20) -> QIcon:
    """取 QIcon（带缓存）。"""
    return QIcon(pixmap(name, color, size))


def names() -> list[str]:
    """可用图标名（供测试校验清单）。"""
    return sorted(_PATHS)
