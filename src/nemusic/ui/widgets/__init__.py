"""控件辅助：远程图片 → QPixmap（封面/头像）。

M1 只需要头像；M4 的封面复用同一函数。所有网络调用都在子线程里发生，
本模块只做"字节 → QPixmap"的转换，调用方负责在拿到字节后回到主线程。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPainterPath, QPixmap

from ...config import TIMEOUT, USER_AGENT
from ...util import log

_log = log.get("ui.widgets")


def remote_bytes(url: str, timeout: float | None = None) -> bytes:
    """阻塞下载图片字节（请在子线程调用）。失败返回空字节。"""
    if not url:
        return b""
    try:
        import requests

        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout or TIMEOUT[1])
        resp.raise_for_status()
        return resp.content
    except Exception as exc:  # noqa: BLE001 - 图片失败不应影响主流程
        _log.debug("图片下载失败 %s：%s", url, exc)
        return b""


def pixmap_from_bytes(data: bytes) -> QPixmap:
    pm = QPixmap()
    if data:
        pm.loadFromData(data)
    return pm


def rounded_pixmap(pm: QPixmap, size: int, radius: int) -> QPixmap:
    """裁剪为圆角正方形（等比缩放居中裁切）。"""
    if pm.isNull():
        return pm
    scaled = pm.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (scaled.width() - size) // 2)
    y = max(0, (scaled.height() - size) // 2)
    cropped = scaled.copy(x, y, size, size)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return out


def circular_pixmap(pm: QPixmap, size: int) -> QPixmap:
    """圆形裁切（头像）。"""
    return rounded_pixmap(pm, size, size // 2)
