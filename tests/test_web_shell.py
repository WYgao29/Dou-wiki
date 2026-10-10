"""Server-rendered shell contracts: layout, contextual tabs and local font loading."""

from html.parser import HTMLParser

import pytest

from douyin_wiki.webapp.app import create_app
from tests.support.web import _web_fixture, local_client


class ShellMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.stack = []
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        self.elements.append((tag, attrs, tuple(self.stack)))
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append((tag, attrs.get("id")))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def element(self, element_id):
        return next(element for element in self.elements if element[1].get("id") == element_id)


@pytest.mark.parametrize(
    ("path", "tabs", "reading"),
    [
        ("/", ["info"], False),
        ("/articles/dy-123", ["info", "inspiration"], True),
        ("/topics/{topic}", ["sources"], False),
        ("/settings", ["info"], False),
        ("/no-such-page", ["info"], False),
    ],
)
def test_web_shell_contextual_tabs_and_local_fonts(tmp_path, path, tabs, reading):
    config, service = _web_fixture(tmp_path)
    topic = service.create_topic("研究专题", ["dy-123"])["topic"]["id"]
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        response = client.get(path.format(topic=topic))
        assert response.status_code == (404 if path == "/no-such-page" else 200)
        markup = ShellMarkup(response.text)
        for element_id in ["library-sidebar", "main-content", "side-panel"]:
            assert ("div", "app-shell") in markup.element(element_id)[2]
        assert markup.element("main-content")[0] == "main"
        for element_id in ["settings-view", "not-found-view"]:
            assert ("main", "main-content") in markup.element(element_id)[2]
        rendered_tabs = [
            attrs["data-panel-tab"]
            for _, attrs, parents in markup.elements
            if ("div", "panel-tabs") in parents and attrs.get("role") == "tab"
        ]
        assert rendered_tabs == tabs
        styles = [attrs for tag, attrs, _ in markup.elements if tag == "link"]
        assert all(attrs["href"].startswith("/static/") for attrs in styles)
        assert any(attrs.get("id") == "reading-fonts" for attrs in styles) == reading
        assert "Doku Serif SC" not in response.text
        assert "font-reading" not in response.text
        ui_css = client.get("/static/app.css").text
        reading_css = client.get("/static/reading.css").text
        assert "DokuSerifSC-400.woff2" not in ui_css
        assert "/static/fonts/DokuSerifSC-400.woff2" in reading_css
