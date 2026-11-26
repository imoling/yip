---
name: web-game
description: 做能双击打开就玩的网页小游戏或小工具（贪吃蛇、俄罗斯方块、打字练习、计时器这类）；不适用：要后端、要登录、要联网数据的网站
license: Apache-2.0
metadata:
  version: 1.0.0
  author: imoling
---

# 做网页小游戏

交付物是一个文件夹，入口 `index.html`，双击就能在浏览器里玩，不依赖构建、不联网、不装包。

## 怎么做

1. **先定规则再写代码。** 用三五行写清：怎么操作（哪些键、能不能点）、怎么得分、怎么输、怎么重开。主人原话里提到的玩法一条都不能漏。
2. **一个文件起步。** `index.html` 里放 `<canvas>` 或 DOM，样式和脚本先内联；超过 400 行再拆成 `style.css`、`game.js`，用相对路径引用。不用 CDN、不用 import map、不用 npm。
3. **游戏循环用 `requestAnimationFrame` 加固定步长**（比如每 120 ms 走一格），别用 `setInterval` 直接改状态。状态集中在一个对象里，`update()` 只改状态，`draw()` 只画。
4. **必须有的界面元素**：
   - 开始前的提示（怎么操作，一行）
   - 实时分数；最高分存 `localStorage`
   - 暂停（空格或 P）
   - 结束画面：本局分数 + 「再来一局」按钮，按回车也能重开
5. **键盘**：方向键和 WASD 都认；`keydown` 里对方向键 `preventDefault()`，免得页面跟着滚。贪吃蛇类要防「同一帧内连按两次方向直接掉头撞死」：把输入排进队列，每步只取一个，且不许反向。
6. **适配**：画布按窗口大小等比缩放，`devicePixelRatio` 处理高清屏；手机上给四个方向按钮或支持滑动。

## 交付前自查

用 `check_page` 打开 `index.html`，带一段脚本真的玩几步：

```js
const k = (key) => document.dispatchEvent(new KeyboardEvent('keydown', { key }));
k('Enter'); k('ArrowUp');
await new Promise(r => setTimeout(r, 2000));
return document.body.innerText.slice(0, 300);
```

逐条核对：
- console 没有报错
- 按键后状态真的变了（分数、位置、文字）
- 能输：撞墙或撞自己后出现结束画面
- 能重开：重开后分数归零
- 刷新后最高分还在

有一条不过就改完再查，别交「应该能玩」的东西。

## 注意

- 不要另写 README 或说明文档，玩法提示做在游戏界面里。
- 颜色对比要够，文字不小于 14 px。
- 最后回复写清：文件在哪、怎么玩（按键）、做了哪些自查。
