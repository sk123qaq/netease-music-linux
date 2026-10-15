"""播放队列与播放模式（纯逻辑，无 Qt，可直接单测）。

四种模式（SPEC §2.5 / AC-4.7）：
- ``order``        顺序播放：到底停止（返回 None）
- ``list_loop``    列表循环：到底回到开头
- ``single_loop``  单曲循环：始终返回当前索引
- ``shuffle``      随机播放：按一个"洗牌顺序"走完一轮再重洗，轮内不重复
"""

from __future__ import annotations

import random
from typing import Iterable, Iterator, Sequence

from ..model.types import Song

MODES = ("order", "list_loop", "single_loop", "shuffle")
MODE_LABELS = {"order": "顺序播放", "list_loop": "列表循环", "single_loop": "单曲循环", "shuffle": "随机播放"}


class PlayQueue:
    """播放队列：列表快照 + 当前索引 + 模式。"""

    def __init__(self, items: Sequence[Song] | None = None, index: int = 0, mode: str = "list_loop", seed: int | None = None) -> None:
        self._items: list[Song] = list(items or [])
        self._index: int = index if self._items else -1
        self._mode: str = mode if mode in MODES else "list_loop"
        self._rng = random.Random(seed)
        self._shuffle_order: list[int] | None = None
        self._normalise_index()

    # ------------------------------------------------------------ 只读
    @property
    def items(self) -> list[Song]:
        return self._items

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def index(self) -> int:
        return self._index

    @property
    def current(self) -> Song | None:
        if 0 <= self._index < len(self._items):
            return self._items[self._index]
        return None

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Song]:
        return iter(self._items)

    @property
    def shuffle_order(self) -> list[int]:
        """当前随机顺序（测试与 UI 调试用）。"""
        self._ensure_shuffle_order()
        assert self._shuffle_order is not None
        return list(self._shuffle_order)

    # ------------------------------------------------------------ 修改
    def set_items(self, items: Iterable[Song], index: int = 0) -> None:
        """整体替换队列（如切换到另一个列表）。"""
        self._items = list(items)
        self._index = index if self._items else -1
        self._shuffle_order = None
        self._normalise_index()

    def clear(self) -> None:
        """清空队列（登出用）。"""
        self._items = []
        self._index = -1
        self._shuffle_order = None
        self._normalise_index()

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"未知播放模式：{mode}")
        self._mode = mode
        if mode == "shuffle":
            self._shuffle_order = None  # 进入随机模式时重洗

    def set_index(self, index: int) -> bool:
        """跳到指定索引（越界返回 False）。"""
        if 0 <= index < len(self._items):
            self._index = index
            return True
        return False

    def move_to(self, song_id: int) -> bool:
        """按歌曲 id 定位（用于列表里点击某首歌）。"""
        for i, song in enumerate(self._items):
            if song.id == song_id:
                self._index = i
                return True
        return False

    def append(self, songs: Iterable[Song]) -> None:
        self._items.extend(songs)
        self._shuffle_order = None
        if self._index < 0 and self._items:
            self._index = 0

    # ------------------------------------------------------------ 上/下一首
    def next_index(self) -> int | None:
        """下一个索引；None 表示播放结束（仅 ``order`` 模式会返回 None）。"""
        n = len(self._items)
        if n == 0:
            return None
        if self._mode == "single_loop":
            return self._index
        if self._mode == "shuffle":
            return self._next_shuffle()
        if self._index + 1 < n:
            return self._index + 1
        return 0 if self._mode == "list_loop" else None

    def prev_index(self) -> int | None:
        """上一个索引；None 表示没有上一首。"""
        n = len(self._items)
        if n == 0:
            return None
        if self._mode == "single_loop":
            return self._index
        if self._mode == "shuffle":
            return self._prev_shuffle()
        if self._index > 0:
            return self._index - 1
        return n - 1 if self._mode == "list_loop" else None

    def has_next(self) -> bool:
        return self.next_index() is not None

    # ------------------------------------------------------------ 内部
    def _normalise_index(self) -> None:
        if not self._items:
            self._index = -1
        elif not (0 <= self._index < len(self._items)):
            self._index = 0

    def _ensure_shuffle_order(self) -> None:
        n = len(self._items)
        if (
            self._shuffle_order is None
            or len(self._shuffle_order) != n
            or sorted(self._shuffle_order) != list(range(n))
        ):
            self._shuffle_order = list(range(n))
            self._rng.shuffle(self._shuffle_order)

    def _next_shuffle(self) -> int:
        n = len(self._items)
        if n == 1:
            return 0
        self._ensure_shuffle_order()
        assert self._shuffle_order is not None
        try:
            pos = self._shuffle_order.index(self._index)
        except ValueError:
            pos = -1
        if pos + 1 >= n:  # 一轮走完 → 重洗，且避免立刻重复当前
            current = self._index
            self._shuffle_order = list(range(n))
            self._rng.shuffle(self._shuffle_order)
            if self._shuffle_order[0] == current and n > 1:
                self._shuffle_order.append(self._shuffle_order.pop(0))
            pos = -1
        return self._shuffle_order[pos + 1]

    def _prev_shuffle(self) -> int:
        n = len(self._items)
        if n == 1:
            return 0
        self._ensure_shuffle_order()
        assert self._shuffle_order is not None
        try:
            pos = self._shuffle_order.index(self._index)
        except ValueError:
            pos = 0
        return self._shuffle_order[pos - 1]
