// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 颜色都指向 styles.css 里的 CSS 变量，深浅色只在一处定义。
// 写法借自 OpenWorker（MIT）的 surfaces/gui/tailwind.config.js：用 color-mix 让 bg-accent/15 这类透明度写法可用。
const tok = (name) => ({ opacityValue }) =>
  opacityValue === undefined
    ? `var(${name})`
    : `color-mix(in srgb, var(${name}) calc(${opacityValue} * 100%), transparent)`;

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["selector", '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        win: tok("--win"),
        side: tok("--side"),
        card: tok("--card"),
        ink: tok("--ink"),
        ink2: tok("--ink2"),
        muted: tok("--muted"),
        faint: tok("--faint"),
        line: tok("--line"),
        accent: tok("--accent"),
        warn: tok("--warn"),
        amber: tok("--amber"),
        danger: tok("--danger"),
        you: tok("--you"),
      },
      fontFamily: {
        sans: ["-apple-system", "SF Pro Text", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "sans-serif"],
      },
    },
  },
  plugins: [],
};
