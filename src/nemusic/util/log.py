"""日志：同时输出到 stderr 与 ~/.config/netease-music-linux/logs/app.log。

约定（SPEC §7.5）：**禁止打印完整 cookie / MUSIC_U / token**，用 ``mask()`` 处理。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .. import config

_LOGGER_NAME = "nemusic"
_configured = False

_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def setup(level: int = logging.INFO) -> logging.Logger:
    """初始化根日志器（幂等）。"""
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger
    logger.setLevel(level)
    logger.propagate = False

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(stream)

    try:
        config.ensure_dirs()
        fileh = RotatingFileHandler(config.LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fileh.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(fileh)
    except OSError:  # 磁盘只读等场景下仅保留 stderr
        pass

    _configured = True
    return logger


def get(name: str = "") -> logging.Logger:
    """取子日志器，如 ``get("net.api")``。"""
    setup()
    return logging.getLogger(f"{_LOGGER_NAME}.{name}" if name else _LOGGER_NAME)


def mask(value: str | None, keep: int = 6) -> str:
    """脱敏：只保留前 ``keep`` 个字符。用于 cookie/token。"""
    if not value:
        return "<empty>"
    return value[:keep] + "…" + f"(len={len(value)})"
