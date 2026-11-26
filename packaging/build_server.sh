#!/usr/bin/env bash
# Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
# 把后端打成 onedir：dist/iml-yip-server/iml-yip-server（中间文件在 build/）。
#
#   packaging/build_server.sh            # 打包
#   packaging/build_server.sh --check    # 打包后起一次服务，确认 health 和 --run-script 能用
#
# 前提：.venv 里装了 requirements.txt 和 pyinstaller（uv pip install --python .venv/bin/python pyinstaller）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
OUT="$ROOT/dist/iml-yip-server"
BIN="$OUT/iml-yip-server"

"$PY" -m PyInstaller --version >/dev/null 2>&1 || {
  echo "缺 PyInstaller：uv pip install --python .venv/bin/python pyinstaller" >&2; exit 1; }

echo "==> PyInstaller：iml-yip-server"
"$PY" -m PyInstaller --noconfirm --clean --log-level WARN \
  --workpath "$ROOT/build" --distpath "$ROOT/dist" "$ROOT/packaging/yip-server.spec"
echo "==> 产物：${OUT}（$(du -sh "$OUT" | cut -f1)）"

[ "${1:-}" = "--check" ] || exit 0

echo "==> 自检"
SCRIPT="$(find "$OUT/_internal/workers" -path '*search-competitor-price/scripts/main.py' | head -1)"
"$BIN" --run-script "$SCRIPT" --input 查定价 --constraints '["定价只看企业版"]' | grep -q 企业版
echo "    --run-script 正常"

DATA="$(mktemp -d "$HOME/Library/Caches/iml-sa-check.XXXX")"
PORT="$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
TOKEN="$("$PY" -c 'import secrets; print(secrets.token_hex(16))')"
SA_DATA_DIR="$DATA" SA_API_TOKEN="$TOKEN" SA_PARENT_PID=$$ "$BIN" --port "$PORT" >"$DATA/server.log" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT
for _ in $(seq 1 120); do
  if curl -sf --noproxy '*' -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/api/health" >/dev/null; then
    echo "    health 正常（端口 ${PORT}，数据目录 ${DATA}）"; exit 0
  fi
  sleep 0.5
done
echo "服务 60 秒没起来，日志：$DATA/server.log" >&2
tail -20 "$DATA/server.log" >&2
exit 1
