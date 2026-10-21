# 抖库 · douyin-wiki

**A local Douyin knowledge base for AI agents.** Turn Douyin (Chinese TikTok) videos and image notes into searchable, source-linked Obsidian Markdown on your own Mac — driven by your agent over MCP.

[English](README.md) · [简体中文](README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) ![Python 3.12–3.13](https://img.shields.io/badge/python-3.12%E2%80%933.13-blue) ![Platform: macOS](https://img.shields.io/badge/platform-macOS%20(Apple%20Silicon)-lightgrey)

> 📸 **[Screenshot / demo GIF placeholder]** — agent receives a Douyin share text → job runs → resulting Obsidian page with timeline and evidence links.

---

## Why

Short videos carry a lot of useful knowledge (tutorials, explanations, recommendations), but it is locked inside a feed: hard to search, hard to cite, and gone when you need it. Pasting links into a chat model doesn't help — the model can't watch the video, and nothing is kept.

抖库 does the heavy media work **locally** (download, speech recognition, OCR), then hands only the text to the model your agent is *already* using to correct and analyze it. The result is a durable, traceable knowledge base you own: every claim links back to the original work with a timestamp or image index.

## Features

All of the following are implemented in `src/douyin_wiki/`:

- **Agent-first MCP server** (`douyin-wiki-mcp`, STDIO) — tools such as `capture_douyin`, `capture_douyin_creator`, `capture_douyin_collection`, `get_job`, `get_analysis_context`, `submit_transcript_correction`, `submit_gateway_analysis`, `search_knowledge`, `get_entry`, `list_job_events`.
- **Capture from share text** — extracts the short link from a pasted Douyin share message and detects video vs. image note.
- **Creators, collections and favorites** — inventory a creator's public works, a full paginated collection, or your favorites; pick what to import; durable, resumable batch jobs.
- **Local media pipeline** — dedicated Playwright browser profile (never touches your daily Chrome profile) + `yt-dlp` + `ffmpeg`.
- **Local transcription & OCR** — Qwen3-ASR + Silero VAD + Qwen3 ForcedAligner on Apple Silicon (MLX); OCR via RapidOCR or macOS Vision. Models load from pinned revisions, safetensors only, no remote code.
- **Gateway analysis (no API keys in the tool)** — the worker pauses after transcription; the calling agent's own model corrects the transcript and submits a structured analysis. The original video is never uploaded to the model.
- **Obsidian output** — immutable `raw/` records, one-screen `wiki/sources/` pages, concept/entity wikilinks, content-type knowledge cards, and a chapter-by-chapter "timeline" for videos, with cover images.
- **Evidence search** — SQLite FTS5 + optional local embeddings (`bge-small-zh-v1.5`, falls back to character n-grams), returning evidence packs with source links and timestamps / image numbers.
- **Local Web UI** (bound to `127.0.0.1`) — library, reader, keyframe gallery, keyword/semantic search, tags, batches and jobs management.
- **Maintenance** — weekly stale-content and orphan-page checks, media retention policies, optional macOS Reminders.

## Quickstart (development install)

Requirements: macOS (Apple Silicon for Qwen3-ASR), Python 3.12–3.13, [`uv`](https://github.com/astral-sh/uv), `ffmpeg`.

```bash
git clone https://github.com/WYgao29/douyin-wiki.git
cd douyin-wiki
uv sync --locked --extra dev --extra embeddings --extra asr --extra ocr --extra mlx
uv run --no-sync python -m playwright install chromium
uv run --no-sync douyin-wiki init      # interactive: create a new Vault or attach an existing one
uv run --no-sync douyin-wiki doctor    # checks deps, models, browser and Vault
```

Download the pinned speech models once (transcription itself stays offline):

```bash
uv run --no-sync hf download mlx-community/Qwen3-ASR-1.7B-bf16 --revision e1f6c266914abc5a46e8756e02580f834a6cf8a7 --include '*.safetensors' --include '*.json' --include '*.txt'
uv run --no-sync hf download mlx-community/silero-vad --revision 7bc17f22d3c0451bd3a6cd71e759b009271ff49a --include '*.safetensors' --include '*.json' --include '*.txt'
uv run --no-sync hf download mlx-community/Qwen3-ForcedAligner-0.6B-8bit --revision 0e1a68e91d815300c7c9754b2a7639378b23db15 --include '*.safetensors' --include '*.json' --include '*.txt'
```

Then authorize Douyin in the dedicated browser and start the worker:

```bash
uv run --no-sync douyin-wiki auth douyin
uv run --no-sync douyin-wiki worker run --forever
uv run --no-sync douyin-wiki web open   # optional local UI
```

For production, install the built wheel into a separate runtime outside the repo — see [README.zh-CN.md](README.zh-CN.md#本地发行包与运行安装) and [`docs/agents/quickstart.md`](docs/agents/quickstart.md).

## Use it from your agent

Register the STDIO MCP server in your client (Claude Code, Codex, OpenClaw, Hermes, …):

```json
{
  "command": "/abs/path/runtime/.venv/bin/python",
  "args": ["-B", "-m", "douyin_wiki.mcp_server"],
  "env": {
    "DOUYIN_WIKI_CONFIG": "/abs/path/config.toml",
    "DOUYIN_WIKI_BROWSER_PROFILE": "/abs/path/browser-profile"
  }
}
```

Typical flow:

1. `capture_douyin` with the user's share text (and their notes as `inspirations`) → returns a `job_id`.
2. Poll `get_job(job_id, gateway_protocol=2)`.
3. For videos: `get_analysis_context` → `submit_transcript_correction`; then `get_analysis_context` → `submit_gateway_analysis`.
4. On completion, `get_entry` for the result; later, `search_knowledge` to answer questions with cited evidence.

Full contract: [`docs/gateway-agents.md`](docs/gateway-agents.md), [`docs/agents/integration-contract.md`](docs/agents/integration-contract.md).

## Roadmap

- [ ] English docs and an English-language agent guide
- [ ] Prebuilt wheel releases on GitHub
- [ ] Demo Vault and end-to-end demo video
- [ ] Broader platform support beyond macOS / Apple Silicon (ASR + OCR backends)
- [ ] More agent clients documented and tested out of the box
- [ ] Automated test suite in CI

## Contributing

Issues and PRs welcome. Please read [`AGENTS.md`](AGENTS.md) and [`docs/architecture.md`](docs/architecture.md) first; lint with `ruff`. Keep changes local-first: no media is uploaded to models, and no API keys are stored by the tool.

## License

[MIT](LICENSE) © 2026 WYgao29. Model weights are not distributed; they follow their upstream licenses (Qwen3-ASR/ForcedAligner: Apache-2.0; Silero VAD: MIT).

---

## 中文简介

抖库是一个面向 AI Agent 的 macOS 本地抖音知识库。Agent 通过 MCP 接收抖音分享文本与灵感，本地 Worker 完成下载、Qwen3-ASR 转写和 OCR，再由 Agent 当前会话模型校正与分析，最终生成可追溯到原作品时间戳的 Obsidian Markdown。支持单条作品、博主、合集与收藏导入，原视频不上传给模型，工具不保存任何模型 API 密钥。完整中文说明见 [README.zh-CN.md](README.zh-CN.md)，详细文档见 [`docs/`](docs/README.md)。
