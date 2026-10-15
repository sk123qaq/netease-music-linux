#!/usr/bin/env bash
# 自检：离线单测 + 可选联网只读探测 + AGENTS.md 文件地图一致性校验。
# 用法：scripts/selftest.sh          仅离线
#      NET=1 scripts/selftest.sh   额外跑联网只读探测（不写账号数据）
set -uo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"

fail=0
echo "==================== 1/3 离线单元测试 ===================="
"$PY" -m unittest discover -s tests -v || fail=1

echo
echo "==================== 2/3 AGENTS.md 文件地图一致性 ===================="
"$PY" scripts/check_agents_map.py || fail=1

if [ "${NET:-0}" = "1" ]; then
  echo
  echo "==================== 3/3 联网只读探测 ===================="
  "$PY" tests/integration_probe.py || fail=1
else
  echo
  echo "==================== 3/3 联网探测（已跳过，NET=1 开启）===================="
fi

echo
if [ "$fail" -eq 0 ]; then echo "✅ 全部通过"; else echo "❌ 有失败项"; fi
exit "$fail"
