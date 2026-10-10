"""Shared content-tag instructions for Gateway analyses."""

PROMPT_VERSION = "v2.4-content-tags"

MAX_ANALYSIS_TAGS = 3

CONTENT_TAG_RULES = (
    "tags 回答“这篇作品在讲什么”，只根据本篇中心内容生成；"
    "不参考已有标签清单，不向近似名称或固定分类靠拢。",
    f"通常生成 2 个标签，最多 {MAX_ANALYSIS_TAGS} 个；中心内容不够时可以少写或为空，"
    "不要为了凑数使用“AI”“AI Agent”等宽泛背景词。",
    "第一个标签写作品最具体的中心对象，使用作品自己的说法；"
    "作为中心对象的专名可以使用，例如 FDE、Data Agent、Jev模型。",
    "第二个标签写作品真正讨论的问题、方法或判断，须能区分不同文章的内容；"
    "第三个标签只用于另一个同等重要的中心内容。",
    "仅顺带提到的产品、专名、类比和例子不作为标签；"
    "体裁放入 content_type / facets，实体放入 entities，概念放入 concepts，"
    "不把这些字段中的名字自动复制为标签。",
    "标签示例：菲尔兹奖得主抵制 AI → [菲尔兹奖声明, 目标错位]；"
    "Palantir FDE 全解析 → [FDE, 驻场交付]；"
    "Data Agent 实测 → [Data Agent, 专用与通用]；"
    "Jev模型营销复盘 → [Jev模型, 营销套路, 开源对比]。",
)
