#!/usr/bin/env bash
# 开发启动：直接跑源码树，不需要安装到 venv。
# 用法：scripts/dev.sh [--offscreen]
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"

if [ "${1:-}" = "--offscreen" ]; then
  echo "[dev] offscreen 冒烟模式"
  exec env QT_QPA_PLATFORM=offscreen "$PY" -m nemusic --self-check
fi
exec "$PY" -m nemusic "$@"
