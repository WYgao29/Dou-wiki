from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

import httpx

from ..config import LLMSettings, llm_is_configured
from ..errors import ExternalToolError, ModelConfigurationError
from ..secrets import get_secret


@dataclass(slots=True)
class ChatChunk:
    text: str = ""
    usage: dict[str, int | None] | None = None


class ChatProvider(Protocol):
    model: str
    configured: bool

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[ChatChunk]: ...


class OpenAICompatibleChatProvider:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        self.model = settings.model
        stored_key = get_secret(settings.api_key_env)
        # Loopback servers may be unauthenticated (for example LM Studio/Ollama),
        # but some local servers such as OMLX still require an API key. Preserve a
        # configured key while allowing local endpoints to work without one.
        self.api_key = stored_key
        self.configured = llm_is_configured(settings, self.api_key)

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[ChatChunk]:
        if not self.configured:
            raise ModelConfigurationError(
                "AI 对话尚未配置。请填写模型名称；云端接口还需要 API Key。"
            )
        url = f"{self.settings.base_url.rstrip('/')}/chat/completions"
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            emitted = False
            completed = False
            try:
                async with (
                    httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client,
                    client.stream("POST", url, headers=headers, json=body) as response,
                ):
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload_text = line[5:].strip()
                        if payload_text == "[DONE]":
                            completed = True
                            break
                        if not payload_text:
                            continue
                        payload = json.loads(payload_text)
                        usage = payload.get("usage")
                        choices = payload.get("choices") or []
                        content = ""
                        if choices:
                            content = str(choices[0].get("delta", {}).get("content") or "")
                            finish_reason = choices[0].get("finish_reason")
                            if finish_reason in {"length", "content_filter"}:
                                raise ExternalToolError(
                                    "模型回答未完整生成，请重试。",
                                    details={"finish_reason": finish_reason},
                                )
                            if finish_reason is not None:
                                completed = True
                        if content or usage:
                            emitted = emitted or bool(content)
                            yield ChatChunk(text=content, usage=usage)
                    if not completed:
                        raise httpx.RemoteProtocolError("模型响应在完成标记前结束")
                return
            except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
                last_error = exc
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    if status in {401, 403}:
                        raise ModelConfigurationError(
                            "模型接口拒绝了 API Key，请检查密钥和模型权限。"
                        ) from exc
                    if status not in {408, 429} and status < 500:
                        break
                if emitted or attempt >= self.settings.max_retries:
                    break
                await asyncio.sleep(2**attempt)
        raise ExternalToolError("AI 对话调用失败，请稍后重试。", details={"cause": str(last_error)})
