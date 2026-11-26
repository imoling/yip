#!/usr/bin/env bash
# Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
# 打 macOS 安装包：后端打成 onedir → 放进 Tauri 的 sidecar 资源目录 → tauri build 出 .app 和 .dmg。
#
#   packaging/build_dmg.sh             # 产物在 app/src-tauri/target/release/bundle/{macos,dmg}/
#   packaging/build_dmg.sh --skip-server   # 后端没改时跳过 PyInstaller，用 dist/ 里现成的
#
# 没签名：第一次打开要在「访达」里右键 Yip →「打开」。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SIDE="$ROOT/app/src-tauri/binaries/sidecar"

if [ "${1:-}" != "--skip-server" ]; then
  "$ROOT/packaging/build_server.sh" --check
fi
[ -x "$ROOT/dist/iml-yip-server/iml-yip-server" ] || { echo "缺后端：先跑 packaging/build_server.sh" >&2; exit 1; }

echo "==> 放进 sidecar 资源目录"
rm -rf "$SIDE"
mkdir -p "$SIDE"
cp -R "$ROOT/dist/iml-yip-server/." "$SIDE/"
touch "$SIDE/.keep"

echo "==> tauri build"
cd "$ROOT/app"
[ -d node_modules ] || npm install --no-audit --no-fund
npm run tauri build -- --bundles app,dmg

OUT="$ROOT/app/src-tauri/target/release/bundle"
echo "==> 产物"
ls -lh "$OUT/dmg/"*.dmg
du -sh "$OUT/macos/Yip.app"
