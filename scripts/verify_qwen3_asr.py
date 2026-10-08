#!/usr/bin/env python3
"""Explicit local Qwen3 verification. No production config or implicit input paths.

Run in an isolated environment. For an OS-enforced offline run on macOS, wrap
this command with sandbox-exec using a deny-network policy. Outputs retain source
hashes, all display segments, raw/native alignment traces and separate timings.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import resource
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--test-vault", type=Path, required=True)
    parser.add_argument("--audio", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--network-guard", action="store_true")
    parser.add_argument("--repeat", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--idle-seconds", type=int, choices=range(0, 31), default=5)
    return parser


def parse_audio(items: list[str]) -> list[tuple[str, Path]]:
    parsed = []
    names = set()
    for item in items:
        name, separator, raw = item.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in names:
            raise ValueError(f"无效或重复素材名称：{item}")
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"音频不存在：{path}")
        parsed.append((name, path))
        names.add(name)
    return parsed


def validate_targets(output: Path, vault: Path) -> None:
    for path in (output, vault):
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise ValueError(f"验证目录必须为新的或空的目录：{path}")
    output_path, vault_path = output.resolve(), vault.resolve()
    if (
        output_path == vault_path
        or output_path in vault_path.parents
        or vault_path in output_path.parents
    ):
        raise ValueError("验证输出与测试 Vault 必须是独立目录")


def _json_default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(type(value).__name__)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_result(segments, traces, samples: int) -> None:
    cursor = 0
    for trace in traces:
        count = trace["samples"]
        if trace["start_sample"] != cursor or not 0 < count <= 600 * 16000:
            raise ValueError("识别窗口采样归属不连续或越界")
        cursor += count
        if trace["silent"]:
            continue
        if trace["generation_tokens"] >= 8192 or trace["internal_segments"] != 1:
            raise ValueError("识别预算耗尽或内部块不完整")
        char = 0
        for start, end in trace["display_source_spans"]:
            if start != char or not start < end:
                raise ValueError("展示段字符归属不连续")
            char = end
        if char != len(trace["raw_text"]):
            raise ValueError("展示段未覆盖原文")
        boundaries = trace["boundaries"]
        edges = [trace["start_sample"], *[item["sample"] for item in boundaries], cursor]
        if any(
            not 0 < end - start <= 180 * 16000 for start, end in zip(edges, edges[1:], strict=False)
        ):
            raise ValueError("主对齐窗口超出 180 秒")
        if any(item["delta_ms"] > 15000 for item in boundaries):
            raise ValueError("对齐切点偏离名义位置超过 15 秒")
    if cursor != samples:
        raise ValueError("识别窗口未覆盖音频全部采样")
    previous = 0
    duration_ms = (samples * 1000 + 8000) // 16000
    for index, segment in enumerate(segments):
        if segment.id != index or not previous <= segment.start_ms < segment.end_ms <= duration_ms:
            raise ValueError("公开段 id 或时间无效")
        if not segment.text or segment.end_ms - segment.start_ms > 30000:
            raise ValueError("公开段为空或超过 30 秒")
        if any(
            getattr(segment, field) is not None
            for field in ("confidence", "avg_logprob", "no_speech_prob")
        ):
            raise ValueError("Qwen3 不应制造置信分数")
        previous = segment.end_ms


def install_network_guard() -> dict:
    counts = {"socket": 0, "requests": 0, "httpx": 0, "urllib": 0}

    def blocked(kind):
        def call(*args, **kwargs):
            counts[kind] += 1
            raise RuntimeError(f"ASR verification blocked network attempt: {kind}")

        return call

    socket.socket.connect = blocked("socket")
    socket.socket.connect_ex = blocked("socket")
    import urllib.request

    urllib.request.urlopen = blocked("urllib")
    try:
        import requests

        requests.Session.request = blocked("requests")
    except ImportError:
        pass
    try:
        import httpx

        httpx.Client.request = blocked("httpx")
        httpx.AsyncClient.request = blocked("httpx")
    except ImportError:
        pass
    return counts


def footprint() -> dict:
    result = subprocess.run(
        ["footprint", "-p", str(os.getpid()), "--noCategories", "-f", "bytes"],
        capture_output=True,
        text=True,
        check=False,
    )
    values = {"returncode": result.returncode}
    for line in result.stdout.splitlines():
        label, separator, raw = line.partition(":")
        parts = raw.strip().split()
        if separator and label.strip() in {"phys_footprint", "phys_footprint_peak"} and parts:
            number = parts[0].replace(",", "")
            if number.isdigit():
                values[label.strip()] = int(number)
    return values


class Observer:
    def __init__(self) -> None:
        self.samples = []
        self.connections = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            sample = {"time": time.perf_counter(), **footprint()}
            self.samples.append(sample)
            found = subprocess.run(
                ["lsof", "-nP", "-a", "-p", str(os.getpid()), "-i"],
                capture_output=True,
                text=True,
                check=False,
            )
            if found.stdout.strip():
                self.connections.append(found.stdout)
            self.stop.wait(1)

    def start(self):
        self.thread.start()

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=5)


def main() -> int:
    args = make_parser().parse_args()
    audios = parse_audio(args.audio)
    validate_targets(args.output_dir, args.test_vault)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        args.output_dir / "process.json",
        {"pid": os.getpid(), "python": sys.executable, "test_vault": args.test_vault.resolve()},
    )
    network = install_network_guard() if args.network_guard else None
    started_import = time.perf_counter()
    from douyin_wiki.adapters.qwen3_asr import Qwen3Transcriber
    from douyin_wiki.config import MediaSettings

    settings = MediaSettings(
        asr_provider="qwen3",
        asr_model="mlx-community/Qwen3-ASR-1.7B-bf16",
        asr_model_revision="e1f6c266914abc5a46e8756e02580f834a6cf8a7",
        vad_model="mlx-community/silero-vad",
        vad_model_revision="7bc17f22d3c0451bd3a6cd71e759b009271ff49a",
    )
    engine = Qwen3Transcriber(settings)
    observer = Observer()
    observer.start()
    engine.runtime.ensure_ready()
    initialization = time.perf_counter() - started_import
    write_json(
        args.output_dir / "initialization.json",
        {
            "seconds_from_first_adapter_import": initialization,
            "cache_state": "local snapshots cached; OS page cache not forcibly purged",
            "memory": engine.runtime.memory_stats(),
            **footprint(),
        },
    )
    results = []
    try:
        for repeat in range(args.repeat):
            for name, audio in audios:
                case = args.output_dir / f"{name}-run{repeat + 1}"
                case.mkdir()
                digest = sha256(audio)
                sample_start = len(observer.samples)
                original_align = engine.runtime.align
                original_recognize = engine.runtime.recognize

                def record_align(audio, text, *, language, _call=original_align, _case=case):
                    tokens = _call(audio, text, language=language)
                    record = {
                        "samples": len(audio),
                        "text": text,
                        "language": language,
                        "tokens": tokens,
                    }
                    with (_case / "alignment-live.ndjson").open("a") as stream:
                        stream.write(
                            json.dumps(record, ensure_ascii=False, default=_json_default) + "\n"
                        )
                    return tokens

                def record_recognize(
                    audio,
                    *,
                    max_tokens,
                    language=None,
                    _call=original_recognize,
                    _case=case,
                    _name=name,
                ):
                    result = _call(audio, max_tokens=max_tokens, language=language)
                    with (_case / "recognition-live.ndjson").open("a") as stream:
                        stream.write(
                            json.dumps(
                                {"samples": len(audio), "max_tokens": max_tokens, "result": result},
                                ensure_ascii=False,
                                default=_json_default,
                            )
                            + "\n"
                        )
                    if max_tokens == 8192:
                        print(
                            json.dumps(
                                {
                                    "case": _name,
                                    "event": "recognized_window",
                                    "samples": len(audio),
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                    return result

                engine.runtime.align = record_align
                engine.runtime.recognize = record_recognize
                begin = time.perf_counter()
                try:
                    segments = asyncio.run(engine.transcribe(audio, case))
                finally:
                    engine.runtime.align = original_align
                    engine.runtime.recognize = original_recognize
                elapsed = time.perf_counter() - begin
                samples = round(engine.metrics["duration_seconds"] * 16000)
                validate_result(segments, engine.last_trace, samples)
                (case / "text.txt").write_text("".join(item.text for item in segments))
                (case / "raw-text.txt").write_text(
                    "".join(item.get("raw_text", "") for item in engine.last_trace)
                )
                write_json(
                    case / "segments.json", [item.model_dump(mode="json") for item in segments]
                )
                write_json(case / "trace.json", engine.last_trace)
                write_json(case / "alignment.json", engine.runtime.alignment_trace)
                observed = observer.samples[sample_start:]
                lengths = [item.end_ms - item.start_ms for item in segments]
                metrics = {
                    **engine.metrics,
                    "wall_seconds": elapsed,
                    "cold_complete_seconds": elapsed + initialization if not results else None,
                    "physical_footprint_sampled_peak_bytes": max(
                        (v.get("phys_footprint", 0) for v in observed), default=0
                    ),
                    "rss_process_high_water_bytes": resource.getrusage(
                        resource.RUSAGE_SELF
                    ).ru_maxrss,
                    "length_min_ms": min(lengths, default=0),
                    "length_max_ms": max(lengths, default=0),
                    "audio_sha256": digest,
                    "audio": str(audio),
                    "name": name,
                    "repeat": repeat + 1,
                    "provenance": engine.provenance,
                    "windows": len(engine.last_trace),
                    "alignment_retry_count": sum(
                        len(item.get("alignment_retries", ())) for item in engine.last_trace
                    ),
                }
                write_json(case / "metrics.json", metrics)
                results.append(metrics)
                write_json(
                    args.output_dir / "summary.json",
                    {
                        "initialization_seconds": initialization,
                        "results": results,
                        "network_attempts": network,
                    },
                )
                print(
                    json.dumps(
                        {
                            "case": name,
                            "repeat": repeat + 1,
                            "seconds": elapsed,
                            "rtf": metrics["process_rtf"],
                            "segments": len(segments),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        time.sleep(args.idle_seconds)
        write_json(
            args.output_dir / "idle-memory.json",
            {"seconds": args.idle_seconds, **engine.runtime.memory_stats(), **footprint()},
        )
    finally:
        observer.finish()
        write_json(
            args.output_dir / "observation.json",
            {
                "physical_footprint_samples": observer.samples,
                "pid_network_connections": observer.connections,
                "network_attempts": network,
            },
        )
    if any((network or {}).values()) or observer.connections:
        raise RuntimeError("ASR 进程出现联网尝试或连接")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
