# 本地字体

运行时仅加载 `src/douyin_wiki/webapp/static/fonts/`，不连接字体服务。Inter 在所有页面加载；Newsreader 与 Doku Serif SC 的 `@font-face` 独立放在 `reading.css`，只在文章页附加，离开文章页移除。专题、设置、任务与资料库均使用 UI 字体。

- [Inter](https://github.com/rsms/inter)：`docs/font-files/InterVariable.woff2`，保留上游文件与 OFL 许可。
- [Newsreader](https://github.com/google/fonts/tree/main/ofl/newsreader)：可变 TTF 用 `fontTools.ttLib.TTFont` 设置 `flavor = "woff2"` 后保存。
- [Noto Serif SC](https://github.com/notofonts/noto-cjk/tree/main/Serif/SubsetOTF/SC)：Regular、SemiBold；来源文件哈希见 `source-sha256.json`。子集字体内部 family / PostScript name 改为 `Doku Serif SC` / `DokuSerifSC`，避免使用上游保留字体名称。
- 字表来自 [通用规范汉字表整理数据](https://github.com/jaywcjlove/table-of-general-standard-chinese-characters/blob/main/data/characters.txt)，取按字序排列的前 6500 字，校验总数和去重数均为 6500。数据许可见 `charset-LICENSE.txt`。

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
