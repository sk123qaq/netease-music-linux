#!/usr/bin/env bash
# 一键启动：确保 venv 与依赖就绪后启动 GUI。
# 用法：./run.sh [--offscreen]   （--offscreen 用于无头冒烟测试）
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo "[run.sh] 首次运行：创建虚拟环境并安装依赖…"
  ./scripts/setup.sh
fi

# 依赖缺失（例如 venv 被删过依赖）时自动补装
if ! "$PY" -c "import segno" >/dev/null 2>&1; then
  echo "[run.sh] 缺少 segno，正在安装…"
  "$VENV/bin/python" -m pip install -q -i "${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}" segno
fi

OFFSCREEN=""
if [ "${1:-}" = "--offscreen" ]; then
  OFFSCREEN="1"
fi

export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
if [ -n "$OFFSCREEN" ]; then
  echo "[run.sh] 以 offscreen 模式启动（不显示窗口）"
  QT_QPA_PLATFORM=offscreen "$PY" -m nemusic --self-check
else
  exec "$PY" -m nemusic "$@"
fi
