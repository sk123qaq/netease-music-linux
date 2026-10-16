"""LRC 歌词解析（SPEC AC-4.6：行级高亮 + 翻译副行）。

数据来源 ``/api/song/lyric``（见 ``net/api.py::lyric``），返回结构::

    {"lrc": {"lyric": "[00:00.00]..."}, "tlyric": {"lyric": "[00:00.00]翻译"}, ...}

两个必须处理的变体：
1. 网易云的时间戳既有 ``[mm:ss.xx]`` 也有 ``[mm:ss:xx]``（百分号前是冒号），
   早期文件还见过 ``[mm:ss.xxx]`` 三位毫秒；
2. 一行可以挂多个时间戳 ``[00:01.00][00:05.00]同一句``，要拆成两行；
3. 纯音乐返回 ``[00:00.00]纯音乐，请欣赏``，按无滚动歌词处理（界面给提示）；
   完全不带时间戳的 payload 也存在（整篇只有「作词 : xx」这类制作信息），
   这些行归到 credits 置顶显示，其余无时间戳杂行忽略。
"""

from __future__ import annotations

import re
from typing import Any

from .types import LyricDoc, LyricLine

#: 时间戳：分钟可到 3 位（超长歌），百分号前允许 . 或 :
_TIME_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
#: 元信息行：[ti:] / [ar:] / [al:] / [by:] / [offset:]
_META_RE = re.compile(r"\[(ti|ar|al|by|offset|au|length|re|ve|sign):([^\]]*)\]", re.IGNORECASE)
_META_LABEL = {"ti": "歌曲", "ar": "歌手", "al": "专辑", "by": "制作", "au": "作词", "length": "时长", "re": "编辑", "ve": "版本"}
#: 网易云把作词/作曲/编曲等信息放在**开头几秒的时间戳行**里（不是 ``[ti:]`` 头），
#: 例如 ``[00:00.00] 作词 : 宝石Gem``。这些不该参与滚动，抽出来当 credits。
_CREDIT_RE = re.compile(
    r"^\s*(作词|作曲|编曲|混音|母带|制作人|监制|吉他|贝斯|鼓|钢琴|键盘|和声|和音|录音|录音师|混音师|发行|词|曲|原唱|演唱|制作|OP|SP)\s*[:：]"
)
#: 只抽取**前 10 秒**内的制作信息行，避免误伤正文里以"作曲:"开头的歌词
CREDIT_LEAD_MS = 10_000


def _to_ms(minute: str, second: str, fraction: str | None) -> int:
    """LRC 百分位 → 毫秒。1 位=十分秒，2 位=百分之一秒，3 位=毫秒。"""
    frac = (fraction or "0")[:3]
    ms = int(frac) * 100 if len(frac) == 1 else int(frac) * 10 if len(frac) == 2 else int(frac)
    return int(minute) * 60_000 + int(second) * 1000 + ms


def _split_credits(lines: list[LyricLine], credits: list[str]) -> tuple[list[LyricLine], list[str]]:
    """把开头几秒的「作词 : xxx」这类制作信息行从滚动歌词里抽出来。"""
    kept: list[LyricLine] = []
    extra: list[str] = []
    for item in lines:
        if item.time_ms <= CREDIT_LEAD_MS and _CREDIT_RE.match(item.text):
            text = " ".join(item.text.split())  # 归一化空白
            if text not in extra:
                extra.append(text)
            continue
        kept.append(item)
    return kept, credits + extra


def parse_lrc(text: str) -> tuple[list[LyricLine], list[str]]:
    """解析 LRC 文本 → (行列表, 元信息)。

    元信息（作词/作曲等）不参与滚动，由界面置顶显示。
    """
    if not text or not text.strip():
        return [], []
    lines: list[LyricLine] = []
    credits: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        meta = _META_RE.match(line)
        if meta:
            key, value = meta.group(1).lower(), meta.group(2).strip()
            if key != "offset" and value:
                credits.append(f"{_META_LABEL.get(key, key)}：{value}")
            continue
        stamps = list(_TIME_RE.finditer(line))
        if not stamps:
            # 真机存在整篇不带时间戳的 payload，行内容就是制作信息（作词/作曲…）：
            # 归到 credits 置顶显示，不要整行丢掉；其余无时间戳杂行仍忽略。
            if _CREDIT_RE.match(line):
                text = " ".join(line.split())
                if text not in credits:
                    credits.append(text)
            continue
        body = _TIME_RE.sub("", line).strip()
        for m in stamps:
            lines.append(LyricLine(time_ms=_to_ms(m.group(1), m.group(2), m.group(3)), text=body))
    lines.sort(key=lambda item: item.time_ms)
    # 同一时刻的重复行去重（保留第一个）
    dedup: list[LyricLine] = []
    for item in lines:
        if dedup and dedup[-1].time_ms == item.time_ms and dedup[-1].text == item.text:
            continue
        dedup.append(item)
    return _split_credits(dedup, credits)


def merge_translation(lines: list[LyricLine], translated: list[LyricLine]) -> list[LyricLine]:
    """按时间戳把翻译合并到正文行上（网易云两侧时间戳一致）。

    对齐不上时退化为"就近匹配"（差 50ms 以内），避免整篇翻译丢失。
    """
    if not translated:
        return lines
    by_time = {item.time_ms: item.text for item in translated}
    used: set[int] = set()
    out: list[LyricLine] = []
    for item in lines:
        text = by_time.get(item.time_ms)
        if text is None:
            for delta in (10, 20, 50, 100):
                text = by_time.get(item.time_ms + delta) or by_time.get(item.time_ms - delta)
                if text and (item.time_ms + delta) not in used:
                    break
            else:
                text = None
        if text:
            used.add(item.time_ms)
            out.append(LyricLine(time_ms=item.time_ms, text=item.text, translation=text))
        else:
            out.append(item)
    return out


#: 服务端表示"没有歌词"的占位文本（出现这些当成无歌词）
_NO_LYRIC_HINTS = ("纯音乐", "純音樂", "暂无歌词", "无歌词", "没有歌词", "请欣赏")


def _is_placeholder(lines: list[LyricLine]) -> bool:
    """纯音乐 / 暂无歌词这类占位文本。

    真机形态（歌曲 2038191895）：占位行在**第 1 行**，后面跟着曲尾制作名单
    （钢琴 : 邓垚 / 出品：网易云音乐 …）。旧判据要求**每一行**都是占位词（all），
    于是 19 行制作名单被当成滚动歌词、"暂无歌词"提示永不出现。
    现在的判据：首行命中即算占位，或整篇都是占位词。
    """
    if not lines:
        return False
    if any(hint in lines[0].text for hint in _NO_LYRIC_HINTS):
        return True
    return all(any(hint in line.text for hint in _NO_LYRIC_HINTS) for line in lines)


def parse_payload(song_id: int, payload: dict[str, Any] | None) -> LyricDoc:
    """把 ``/api/song/lyric`` 的响应解析成 ``LyricDoc``。"""
    doc = LyricDoc(song_id=int(song_id or 0))
    if not isinstance(payload, dict):
        return doc
    lrc_raw = (payload.get("lrc") or {}).get("lyric") or ""
    tlyric_raw = (payload.get("tlyric") or {}).get("lyric") or ""
    lines, credits = parse_lrc(lrc_raw)
    tlines, _tcredits = parse_lrc(tlyric_raw)
    doc.lines = merge_translation(lines, tlines)
    doc.credits = credits
    # 纯音乐/暂无歌词：服务端会给一行占位文本，按无歌词处理（界面给专门提示）
    if _is_placeholder(doc.lines):
        doc.lines = []
        doc.credits = []
    return doc


__all__ = ["parse_lrc", "merge_translation", "parse_payload"]
