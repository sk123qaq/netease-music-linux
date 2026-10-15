#!/usr/bin/env python3
"""校验 AGENTS.md 的文件地图与真实目录一致（对应 AC-6.5）。

规则：
- 表格首列里的路径（含 ``a/{b,c}.py`` 花括号写法）中，凡状态标为 ✅/🚧 的文件必须存在；
- 标为 📝 规划中的文件不应存在（存在则提示，说明状态列该更新了，仅警告）；
- 缺失的已实现文件 → 退出码 1。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"

#: 表格首列里的 `路径` 或 `a/{b,c}.py`；只认带扩展名或含斜杠的项
_PATH_RE = re.compile(r"^`([^`]+)`$|^`([^`]+)`\s*\|\s*([^|]*)\|")
_DONE_MARKS = ("✅", "🚧")
_PLAN_MARK = "📝"


def expand(entry: str) -> list[str]:
    """展开花括号写法：``scripts/{a.sh,b.sh}`` → 两个路径。"""
    m = re.search(r"\{([^{}]+)\}", entry)
    if not m:
        return [entry]
    out: list[str] = []
    for part in m.group(1).split(","):
        out.extend(expand(entry[: m.start()] + part.strip() + entry[m.end() :]))
    return out


def main() -> int:
    if not AGENTS.exists():
        print("找不到 AGENTS.md")
        return 1
    missing_done: list[str] = []
    stale_plan: list[str] = []
    checked = 0

    for line in AGENTS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        first, last = cells[0], cells[-1]
        m = re.match(r"^`([^`]+)`$", first)
        if not m:
            continue
        entry = m.group(1)
        if entry in {"路径", "文件"} or "." not in entry:  # 只检查具体文件，跳过目录
            continue
        status = last
        for path in expand(entry):
            target = ROOT / path
            checked += 1
            exists = target.exists()
            if any(mark in status for mark in _DONE_MARKS) and not exists:
                missing_done.append(path)
            elif _PLAN_MARK in status and exists:
                stale_plan.append(path)

    print(f"检查了 {checked} 条路径")
    if stale_plan:
        print("以下文件已存在但状态仍为“规划中”（请更新 AGENTS.md 状态列）：")
        for p in stale_plan:
            print(f"  - {p}")
    if missing_done:
        print("以下文件状态为已实现/进行中，但不存在：")
        for p in missing_done:
            print(f"  - {p}")
        return 1
    print("✅ 文件地图与目录一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
