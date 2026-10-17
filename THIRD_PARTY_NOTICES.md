# 第三方代码说明

前端使用了 Waku 的少量通用交互代码，按本项目的原生 DOM、安全渲染和任务读取协议改编。

- 仓库：https://github.com/ShenSeanChen/waku-agent
- 固定提交：4b69b321324750f231fdfd72a83b38ad7301dcc3
- ui.js 的 `_menuKey`：改编为 app/web_application/static/ui.js 的 `moveFocus`。
- main.js 的 `startTimers`、`stopTimers` 与可见性处理：改编为 app/web_application/static/process.js 的详情轮询管理。

本项目样式自行实现。没有复制其品牌名称、标志或 LICENSE-BRAND 排除的 design 目录。实际复制的文件均保留许可注释。

## MIT License

Copyright (c) 2026 Sean Chen (ShenSeanChen)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
