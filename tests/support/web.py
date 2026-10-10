from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from douyin_wiki.config import AppConfig
from douyin_wiki.models import EntryRecord, RetentionPolicy
from douyin_wiki.service import DouyinWikiService


def local_client(app, **kwargs):
    """TestClient with Origin so mutating requests pass the local CSRF gate."""
    headers = {"Origin": "http://testserver", **(kwargs.pop("headers", {}) or {})}
    return TestClient(app, headers=headers, **kwargs)


def _web_fixture(tmp_path: Path) -> tuple[AppConfig, DouyinWikiService]:
    vault = tmp_path / "vault"
    source_dir = vault / "wiki" / "sources"
    raw_dir = vault / "raw" / "assets" / "123"
    data_dir = vault / "wiki" / ".data" / "sources"
    source_dir.mkdir(parents=True)
    raw_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    (raw_dir / "cover.webp").write_bytes(b"RIFFfakeWEBP")
    (vault / "raw" / "测试记录.md").write_text("原始记录", encoding="utf-8")
    image_dir = vault / "raw" / "images" / "123"
    image_dir.mkdir(parents=True)
    (image_dir / "001.webp").write_bytes(b"RIFFfakeIMAGE")
    cover_dir = vault / "raw" / "covers"
    cover_dir.mkdir(parents=True)
    (cover_dir / "123.jpg").write_bytes(b"fakeJPEG")
    source = source_dir / "测试文章_123.md"
    source.write_text(
        """---
type: source
video_id: '123'
author: 测试博主
published: '2026-08-23T08:00:00+08:00'
captured: '2026-08-23T09:00:00+08:00'
status: 正常
source_kind: video
cover_image: raw/assets/123/cover.webp
content_type: tutorial
tags:
  - 咖啡
inspirations:
  - text: 保留这段方法
---
# 测试文章

![封面](../../raw/assets/123/cover.webp)

## 灵感

- 保留这段方法

## 一句话

这是一条本地资料。

## 核心收获

- 关键结论

<script>alert('x')</script>

## 来源

- [原始采集记录](../../raw/测试记录.md)
- [打开原作品](https://www.douyin.com/video/123)
""",
        encoding="utf-8",
    )
    (data_dir / "123.md").write_text("机器数据，不应展示", encoding="utf-8")
    config = AppConfig(
        vault_path=vault,
        embeddings={"provider": "offline", "fallback_dimensions": 32},
    )
    service = DouyinWikiService(config)
    service.database.initialize()
    now = datetime.now(UTC)
    entry = EntryRecord(
        id="dy-123",
        video_id="123",
        title="测试文章",
        original_url="https://www.douyin.com/video/123",
        canonical_url="https://www.douyin.com/video/123",
        raw_path="raw/测试记录.md",
        source_path="wiki/sources/测试文章_123.md",
        status="active",
        media_status="present",
        retention=RetentionPolicy.KEEP,
        summary="这是一条本地资料。",
        inspirations=[{"text": "保留这段方法"}],
        tags=["咖啡"],
        created_at=now,
        updated_at=now,
    )
    service.database.upsert_entry(
        entry,
        {
            "metadata": {
                "video_id": "123",
                "original_url": "https://www.douyin.com/video/123",
                "canonical_url": "https://www.douyin.com/video/123",
                "title": "测试文章",
                "author": "测试博主",
                "source_kind": "video",
            },
            "analysis": {
                "analysis_version": 2,
                "title": "测试文章",
                "one_liner": "这是一条本地资料。",
                "takeaways": ["关键结论"],
                "chapters": [],
                "knowledge_atoms": [],
            },
        },
    )
    return config, service
