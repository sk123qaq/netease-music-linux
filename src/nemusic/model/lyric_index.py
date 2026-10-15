"""歌词行索引：给定播放位置，二分找出当前该高亮哪一行（AC-4.6）。

单独一个模块而不是塞进 UI，是为了能用纯单测覆盖边界：
- 位置在第一行之前 → 返回 -1（不高亮，避免开头就高亮第一句）；
- 位置落在两行之间 → 返回上一行；
- 空歌词 → 恒为 -1。
"""

from __future__ import annotations

import bisect

from .lrc import parse_payload
from .types import LyricDoc, LyricLine


class LyricIndex:
    """按位置查当前行号。构造时拷贝时间表，之后只读。"""

    __slots__ = ("_times", "_lines", "_doc")

    def __init__(self, doc: LyricDoc | None = None, lines: list[LyricLine] | None = None) -> None:
        source = lines if lines is not None else (doc.lines if doc else [])
        self._lines = list(source)
        self._times = [line.time_ms for line in self._lines]
        self._doc = doc

    # ------------------------------------------------------------ 查询
    def index_at(self, position_ms: int) -> int:
        """当前行号；无歌词或还没到第一行返回 -1。"""
        if not self._times:
            return -1
        # bisect_right - 1：位置恰好等于某行时间时高亮该行
        idx = bisect.bisect_right(self._times, position_ms) - 1
        return idx if idx >= 0 else -1

    def line_at(self, position_ms: int) -> LyricLine | None:
        idx = self.index_at(position_ms)
        return self._lines[idx] if idx >= 0 else None

    # ------------------------------------------------------------ 信息
    @property
    def lines(self) -> list[LyricLine]:
        return self._lines

    @property
    def doc(self) -> LyricDoc | None:
        return self._doc

    @property
    def empty(self) -> bool:
        return not self._lines

    @property
    def duration_hint(self) -> int:
        """最后一行时间（用于把进度条末尾的空白也留给最后一句）。"""
        return self._times[-1] if self._times else 0

    @classmethod
    def from_payload(cls, song_id: int, payload: dict | None) -> "LyricIndex":
        return cls(parse_payload(song_id, payload))


__all__ = ["LyricIndex"]
