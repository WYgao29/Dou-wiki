# Readwise 风格静态原型

打开 `index.html`，或在仓库根目录运行：

```sh
python3 -m http.server 8767 --bind 127.0.0.1
```

浏览器打开 `http://127.0.0.1:8767/docs/prototypes/readwise/`。

此原型保留为早期交互演示；当前应用以 [设计系统](../../web-ui-design-system.md) 为准。原型使用初版设计方案的亮暗配色、240 / 自适应 / 360 三栏、48px 栏头、88px 资料行、4px 基数间距、6/4px 圆角。为适应中栏宽度，将来源与筛选排序放在独立的 48px 工具行。

字体：本地 Inter Variable、Newsreader；中文使用方案允许的系统 Songti SC / 思源宋体回落。macOS 截图使用 Songti SC。字体复用应用的 `static/fonts/`，许可随字体保留；`assets/` 仅保留 Lucide 许可。封面为 CSS 绘制的示例封面，文章、计数和对话均为演示数据。

可操作：选择列表项查看信息、打开阅读、返回资料库、切换右栏 Tab、搜索示例资料、来源筛选、列表/专辑墙切换、亮暗主题和侧栏开合。未实现的业务操作会显示原型提示，不调用后端。

截图和验证记录位于 `docs/screenshots/redesign/prototype/`。从仓库根目录重新生成：

```sh
uv run python docs/prototypes/readwise/capture.py
```

输出为 1440×900 视口、2 倍像素截图。截图为可重新生成的本地产物，不提交到 Git。
