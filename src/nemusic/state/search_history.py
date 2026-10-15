"""搜索历史（AC-2.6）：≤50 条、去重、新记录置顶、可单条删除/清空、持久化。

存储为 JSON 数组（简单、可读、便于排查）：
``["周杰伦", "山河图", …]``（越靠前越新）。文件损坏时退化为空历史，不阻断使用。
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QObject, Signal

from .. import config
from ..util import log

_log = log.get("state.search_history")


class SearchHistory(QObject):
    """搜索历史。纯数据 + 持久化，不碰网络。"""

    changed = Signal()

    def __init__(self, path=None, limit: int | None = None) -> None:
        super().__init__()
        self._path = path if path is not None else config.SEARCH_HISTORY_FILE
        self._limit = limit if limit is not None else config.SEARCH_HISTORY_LIMIT
        self._items: list[str] = []
        self.load()

    # ============================================================ 读
    @property
    def items(self) -> list[str]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def load(self) -> None:
        self._items = []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            _log.info("搜索历史文件损坏，按空历史处理")
            return
        if isinstance(raw, list):
            self._items = [str(x) for x in raw if isinstance(x, str)][: self._limit]
        _log.debug("已载入 %d 条搜索历史", len(self._items))

    def save(self) -> None:
        try:
            config.write_private(self._path, json.dumps(self._items, ensure_ascii=False, indent=2))
        except OSError as exc:  # pragma: no cover
            _log.warning("搜索历史保存失败：%s", exc)

    # ============================================================ 写
    def add(self, keyword: str) -> None:
        """记录一次搜索：去重 + 置顶 + 截断到上限。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return
        if keyword in self._items:
            self._items.remove(keyword)
        self._items.insert(0, keyword)
        del self._items[self._limit :]
        self.save()
        self.changed.emit()

    def remove(self, keyword: str) -> None:
        if keyword in self._items:
            self._items.remove(keyword)
            self.save()
            self.changed.emit()

    def clear(self) -> None:
        if not self._items:
            return
        self._items = []
        self.save()
        self.changed.emit()
