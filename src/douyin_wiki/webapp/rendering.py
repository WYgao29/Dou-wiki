from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import nh3
from markdown_it import MarkdownIt

from .catalog import LibraryCatalog

_MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("table")
_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "del",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "hr",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "loading"},
}


def _remove_private_lines(markdown: str) -> str:
    lines: list[str] = []
    cover_removed = False
    for line in markdown.splitlines():
        if "原始采集记录" in line or "machine_data_path" in line:
            continue
        if not cover_removed and re.match(r"^\s*!\[[^]]*(?:封面|缩略图|第\s*1\s*张)[^]]*]", line):
            cover_removed = True
            continue
        lines.append(line)
    return "\n".join(lines)


def _wikilinks(markdown: str, catalog: LibraryCatalog) -> str:
    by_source = {Path(item.source_path).stem: item for item in catalog.list_items()}

    def replace(match: re.Match[str]) -> str:
        target, label = (
            match.group(1).split("|", 1) if "|" in match.group(1) else (match.group(1), "")
        )
        name = Path(target).stem
        item = by_source.get(name)
        text = label or name
        return f"[{text}](/articles/{item.entry_id})" if item else text

    return re.sub(r"\[\[([^]]+)]]", replace, markdown)


def _rewrite_url(url: str, source_path: Path, catalog: LibraryCatalog) -> str:
    decoded = unquote(url.strip().strip("<>"))
    parsed = urlsplit(decoded)
    if parsed.scheme in {"http", "https", "mailto"} or decoded.startswith("#"):
        return decoded
    if (
        not parsed.scheme
        and not parsed.query
        and not parsed.fragment
        and re.fullmatch(r"/articles/([A-Za-z0-9_-]+)", parsed.path)
    ):
        entry_id = parsed.path.removeprefix("/articles/")
        return parsed.path if catalog.get(entry_id) else "#"
    if parsed.scheme or decoded.startswith("/"):
        return "#"
    target = (source_path.parent / parsed.path).resolve()
    try:
        relative = target.relative_to(catalog.vault_path)
    except ValueError:
        return "#"
    if target.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}:
        safe = catalog.safe_media_path(str(relative))
        return catalog.media_url(str(relative)) if safe else "#"
    if target.suffix.lower() == ".md":
        for item in catalog.list_items():
            if Path(item.source_path) == relative:
                return f"/articles/{item.entry_id}"
        return "#"
    return "#"


def _render_article_markdown(value: str) -> str:
    tokens = _MARKDOWN.parse(value)
    # Work on parsed quotes so examples inside fenced code remain literal.
    for index in range(len(tokens) - 1, -1, -1):
        opening = tokens[index]
        if opening.type != "blockquote_open":
            continue
        if [token.type for token in tokens[index + 1 : index + 4]] != [
            "paragraph_open",
            "inline",
            "paragraph_close",
        ]:
            continue
        inline = tokens[index + 2]
        title, _, body = inline.content.partition("\n")
        marker = re.fullmatch(r"\[![^]\r\n]+][ \t]*(.*)", title)
        if not marker:
            continue
        caption = re.sub(
            r"^(?:\*\*)?AI[ \t]*推断(?:\*\*)?(?:[ \t]*[:：·][ \t]*|[ \t]*$)",
            "",
            marker.group(1).strip(),
            count=1,
        )
        end = next(
            cursor
            for cursor in range(index + 4, len(tokens))
            if tokens[cursor].type == "blockquote_close" and tokens[cursor].level == opening.level
        )
        remaining = tokens[index + 4 : end]
        plain_paragraphs = all(
            token.type in {"paragraph_open", "inline", "paragraph_close"} for token in remaining
        )
        content = "\n".join(
            [body, *(token.content for token in remaining if token.type == "inline")]
        )
        if caption == "与灵感的关系" and plain_paragraphs and content.strip() in {"", "无"}:
            del tokens[index : end + 1]
            continue
        header = "**AI 推断**" + (f" · {caption}" if caption else "")
        replacement = _MARKDOWN.parse(header + (f"\n\n{body}" if body else ""))
        for token in replacement:
            token.level += tokens[index + 1].level
        tokens[index + 1 : index + 4] = replacement
    return _MARKDOWN.renderer.render(tokens, _MARKDOWN.options, {})


_INSPIRATION_SECTION = re.compile(r"(?ms)^## 灵感[ \t]*\n+(?P<body>.*?)(?=^## |\Z)")
_RELEVANCE_CALLOUT = re.compile(r"(?m)^> \[!note\] AI 推断：与灵感的关系\n(?:^>.*(?:\n|$))*\n?")


def _strip_empty_inspiration(markdown: str) -> str:
    """Drop an empty 灵感 block and its AI-inference callout from the reader view."""
    match = _INSPIRATION_SECTION.search(markdown)
    if match is None:
        return markdown
    lines = []
    for line in _RELEVANCE_CALLOUT.sub("", match.group("body")).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lines.append(stripped)
    if lines and any(line not in {"- 未填写", "未填写"} for line in lines):
        return markdown
    stripped = markdown[: match.start()] + markdown[match.end() :]
    return _RELEVANCE_CALLOUT.sub("", stripped)


def render_article(markdown: str, *, source_path: str, catalog: LibraryCatalog) -> str:
    value = _wikilinks(_remove_private_lines(_strip_empty_inspiration(markdown)), catalog)
    source = (catalog.vault_path / source_path).resolve()

    def image(match: re.Match[str]) -> str:
        alt, url, title = match.group(1), match.group(2), match.group(3) or ""
        rewritten = _rewrite_url(url, source, catalog)
        suffix = f' "{title}"' if title else ""
        return f"![{alt}]({rewritten}{suffix})"

    def link(match: re.Match[str]) -> str:
        label, url, title = match.group(1), match.group(2), match.group(3) or ""
        rewritten = _rewrite_url(url, source, catalog)
        suffix = f' "{title}"' if title else ""
        return f"[{label}]({rewritten}{suffix})"

    value = re.sub(r"!\[([^]]*)]\(([^\s)]+)(?:\s+[\"']([^\"']*)[\"'])?\)", image, value)
    value = re.sub(r"(?<!!)\[([^]]+)]\(([^\s)]+)(?:\s+[\"']([^\"']*)[\"'])?\)", link, value)
    rendered = _render_article_markdown(value)
    return nh3.clean(
        rendered,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )


def render_chat(markdown: str) -> str:
    return nh3.clean(
        _MARKDOWN.render(markdown),
        tags=_ALLOWED_TAGS - {"img"},
        attributes={"a": _ALLOWED_ATTRIBUTES["a"]},
        url_schemes={"http", "https"},
        link_rel="noopener noreferrer",
    )
