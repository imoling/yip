// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";

// 深浅色跟着系统走
const media = window.matchMedia("(prefers-color-scheme: dark)");
const apply = () => (document.documentElement.dataset.theme = media.matches ? "dark" : "light");
apply();
media.addEventListener("change", apply);
document.documentElement.dataset.platform = window.__SA_PLATFORM__ || "web";

// 文件拖到窗口别处时不要让页面跳到文件本身
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
