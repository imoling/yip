// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 开发：端口固定 1420（tauri.conf.json 的 devUrl 和后端 CORS 都按它写），/api 转给 8770 上单独跑的后端。
// base "./" 让打包后的资源用相对路径，tauri:// 下才找得到。
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import pkg from "./package.json";

export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 1420,
    strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:8770", changeOrigin: false, ws: true } },
  },
  clearScreen: false,
  envPrefix: ["VITE_", "TAURI_"],
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
});
