# 本地字体

运行时仅加载 `src/douyin_wiki/webapp/static/fonts/`，不连接字体服务。Inter 在所有页面加载，Space Grotesk 用于 UI 标题；Newsreader 与 Doku Serif SC 的 `@font-face` 独立放在 `reading.css`，只在文章页附加，离开文章页移除。专题、设置、任务与资料库均使用 UI 字体。

- [Inter](https://github.com/rsms/inter)：`src/douyin_wiki/webapp/static/fonts/InterVariable.woff2`，保留上游文件与 OFL 许可。
- [Newsreader](https://github.com/google/fonts/tree/main/ofl/newsreader)：可变 TTF 用 `fontTools.ttLib.TTFont` 设置 `flavor = "woff2"` 后保存。
- [Noto Serif SC](https://github.com/notofonts/noto-cjk/tree/main/Serif/SubsetOTF/SC)：Regular、SemiBold；来源文件哈希见 `source-sha256.json`。子集字体内部 family / PostScript name 改为 `Doku Serif SC` / `DokuSerifSC`，避免使用上游保留字体名称。
- 字表来自 [通用规范汉字表整理数据](https://github.com/jaywcjlove/table-of-general-standard-chinese-characters/blob/main/data/characters.txt)，取按字序排列的前 6500 字，校验总数和去重数均为 6500。数据许可见 `charset-LICENSE.txt`。

静态原型 `docs/prototypes/readwise/index.html` 复用上述应用字体目录，不另复制字体；三个字体许可文件与发布字体一同保存在应用目录。源文件、字表许可、来源及输出哈希仍由本目录记录。

裁剪方法（将下载的源字体放在当前工作目录）：

```sh
uv run --with fonttools --with brotli pyftsubset NotoSerifSC-Regular.otf \
  --text-file=scripts/fonts/charset-gb6500.txt \
  --unicodes='U+0020-007E,U+00A0-00FF,U+2000-206F,U+3000-303F,U+FF00-FFEF' \
  --layout-features='*' --flavor=woff2 \
  --output-file=src/douyin_wiki/webapp/static/fonts/DokuSerifSC-400.woff2
```

600 字重使用 `NotoSerifSC-SemiBold.otf`。生成后用 `TTFont` 将 `name` 表中 1/2/3/4/6/16/17 记录的 `Noto Serif SC` 和 `NotoSerifSC` 分别替换为 `Doku Serif SC` 和 `DokuSerifSC` 后保存。发布文件哈希见 `output-sha256.json`。

2026-09-30 实测：400 字重 1,853,600 bytes；600 字重 1,895,980 bytes。两个中文子集与 Inter、Newsreader 合计约 4.31 MB。常用字表外字符由系统宋体回落。

## Space Grotesk（2026-10-21）

- 官方来源：[floriankarsten/space-grotesk](https://github.com/floriankarsten/space-grotesk/tree/03507d024a01282884232081fc6011c09ff4e849)，固定提交 `03507d024a01282884232081fc6011c09ff4e849`。
- 原文件 `fonts/woff2/SpaceGrotesk[wght].woff2` 原样保存为 `SpaceGrotesk-Variable.woff2`，可变字重 300–700，`font-display: swap`。中文回落 PingFang SC；文章阅读字体和加载策略不变。
- SIL Open Font License 1.1，许可随运行资源保存为 `SpaceGrotesk-OFL.txt`；不请求外部字体服务。
- 文件 49,256 bytes；SHA-256：`8e085aa438094f11487a836652edd5c054fa6a96f63fc7c282105ee3a4b08c07`。

Dou-wiki 字标另用 `SpaceGrotesk-700.woff2`，从上述固定版本的可变字体提取 700 字重，不引入其他字重或字体服务。生成时使用 fontTools 4.67.0 的 `instantiateVariableFont(font, {"wght": 700}, inplace=True, updateFontNames=True)`，读取源文件时设置 `recalcTimestamp=False`，保持 woff2 格式。输出不含可变轴，`OS/2.usWeightClass` 为 700，内部字体名为 Space Grotesk Bold；文件 31,580 bytes，SHA-256：`39fd46fc12aeba9509107bcb32c65084b9b2c3e61df37d1d7e97198ca03f20e3`，沿用同一份 `SpaceGrotesk-OFL.txt`。该静态字重使用 `font-display: swap`；现有可变字体仍供 UI 标题使用，正文和阅读字体保持原样。

许可文本统一为 LF 换行并移除行尾空白，许可正文保持原文；除上述 700 字重派生文件外，woff2 字节与上游原文件一致。
