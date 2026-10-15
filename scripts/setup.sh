#!/usr/bin/env bash
# 创建项目虚拟环境并安装依赖。
# 说明：使用 --system-site-packages 复用系统已装的 PySide6/requests，避免重复下载 100MB+ 的 Qt 轮子。
#      PyPI 官方源在本机不可达，默认使用阿里云镜像（可用 PIP_INDEX_URL 覆盖）。
set -euo pipefail
cd "$(dirname "$0")/.."

INDEX="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
VENV=".venv"
#: 必须用系统 Python（它自带 PySide6/requests）；uv 默认可能选别的版本，
#: 那样 --system-site-packages 就看不见系统的 PySide6 了。
PY_BIN="${PYTHON:-}"
if [ -z "$PY_BIN" ]; then
  for cand in /usr/bin/python3 python3; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import PySide6' >/dev/null 2>&1; then
      PY_BIN="$(command -v "$cand")"
      break
    fi
  done
fi
[ -n "$PY_BIN" ] || { echo "[setup] 找不到自带 PySide6 的 python（可设 PYTHON=/path/to/python3）"; exit 1; }
echo "[setup] 使用 Python: $PY_BIN ($($PY_BIN -V 2>&1))"

if command -v uv >/dev/null 2>&1; then
  echo "[setup] 使用 uv 创建 venv（复用系统包）"
  uv venv --python "$PY_BIN" --system-site-packages --allow-existing "$VENV"
  VIRTUAL_ENV="$PWD/$VENV" uv pip install -i "$INDEX" segno
  # 可选测试依赖（失败不致命）
  VIRTUAL_ENV="$PWD/$VENV" uv pip install -i "$INDEX" zxing-cpp || echo "[setup] zxing-cpp 安装失败（二维码回环测试将 skip）"
else
  echo "[setup] 未找到 uv，回退 python -m venv"
  "$PY_BIN" -m venv --system-site-packages "$VENV"
  "$VENV/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$VENV/bin/python" -m pip install -i "$INDEX" segno
  "$VENV/bin/python" -m pip install -i "$INDEX" zxing-cpp || echo "[setup] zxing-cpp 安装失败（二维码回环测试将 skip）"
fi

# 校验关键依赖可见性
for mod in PySide6 requests segno; do
  "$VENV/bin/python" -c "import $mod" 2>/dev/null || { echo "[setup] 依赖 $mod 不可用！"; exit 1; }
done

echo "[setup] 完成。运行 ./run.sh 启动。"
"$VENV/bin/python" -c "import sys, segno; print('[setup] python', sys.version.split()[0], '| segno', segno.__version__)"
