from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

_ID = re.compile(r"[0-9]{1,32}\Z")


def stable_id(value) -> str | None:
    if type(value) not in {int, str}:
        return None
    text = str(value)
    return text if _ID.fullmatch(text) else None


class CollectionProfile(BaseModel):
    mix_id: str
    name: str = "抖音合集"
    canonical_url: str = ""
    original_url: str = ""
    author: str = ""
    author_sec_uid: str | None = None
    thumbnail_url: str | None = None
    reported_count: int | None = Field(default=None, ge=0)
    updated_to_episode: int | None = Field(default=None, ge=0)
    folder_path: str = ""
    last_complete_at: datetime | None = None

    @field_validator("mix_id")
    @classmethod
    def validate_id(cls, value):
        if stable_id(value) is None:
            raise ValueError("mix_id must be a stable numeric ID")
        return value

    @model_validator(mode="after")
    def canonicalize(self):
        self.canonical_url = f"https://www.douyin.com/collection/{self.mix_id}"
        self.original_url = self.original_url or self.canonical_url
        return self


class CollectionWork(BaseModel):
    work_id: str
    canonical_url: str = ""
    title: str = "抖音作品"
    author: str = ""
    source_kind: Literal["video", "image_note", "article", "unknown"] = "video"
    position: int = Field(default=1, ge=1)
    episode_label: str | None = None
    published_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    thumbnail_url: str | None = None
    available: bool = True
    entry_id: str | None = None
    disposition: str = "pending"
    present_in_last_complete: bool = False

    @field_validator("work_id")
    @classmethod
    def validate_id(cls, value):
        if stable_id(value) is None:
            raise ValueError("work_id must be a stable numeric ID")
        return value

    @model_validator(mode="after")
    def canonicalize(self):
        route = {"image_note": "note", "article": "article"}.get(self.source_kind, "video")
        canonical = f"https://www.douyin.com/{route}/{self.work_id}"
        if self.canonical_url and self.canonical_url != canonical:
            raise ValueError("canonical_url must match the work ID and type")
        self.canonical_url = canonical
        return self


class CollectionInventory(BaseModel):
    profile: CollectionProfile
    account_id: str = ""
    works: list[CollectionWork] = Field(default_factory=list)
    complete: bool = False
    warnings: list[str] = Field(default_factory=list)
    request_cursor: str = "0"
    next_cursor: str | None = None
