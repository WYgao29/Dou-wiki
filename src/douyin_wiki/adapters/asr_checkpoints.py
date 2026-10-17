"""Atomic, content-addressed staging of complete recognition/alignment windows."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


class AsrCheckpoints:
    def __init__(self, audio: Path, output: Path | None, settings) -> None:
        self.directory = None
        self.recognition_version = None
        self.alignment_version = digest(
            {
                "version": 3,
                "model": settings.alignment_model,
                "revision": settings.alignment_model_revision,
                "segment_seconds": settings.asr_segment_seconds,
                "itn": settings.asr_itn,
                "policy": settings.alignment_failure_policy,
            }
        )
        if output is None or not audio.is_file():
            return
        self.recognition_version = digest(
            {
                "version": 1,
                "audio": file_digest(audio),
                "model": settings.asr_model,
                "revision": settings.asr_model_revision,
                "vad": settings.vad_model,
                "vad_revision": settings.vad_model_revision,
                "language": settings.asr_language,
            }
        )
        self.directory = output / self.recognition_version

    def _path(self, stage, start, samples, text=""):
        key = digest(
            [stage, start, samples, text, self.alignment_version if stage == "alignment" else None]
        )
        return self.directory / f"{stage}-{key}.json" if self.directory else None

    def read(self, stage, start, samples, text=""):
        path = self._path(stage, start, samples, text)
        if path is None:
            return None
        try:
            envelope = json.loads(path.read_text())
            payload = envelope["payload"]
            if isinstance(payload, dict) and digest(payload) == envelope["digest"]:
                return payload
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def write(self, stage, start, samples, payload, text=""):
        path = self._path(stage, start, samples, text)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(
                    {"payload": payload, "digest": digest(payload)},
                    handle,
                    ensure_ascii=False,
                    allow_nan=False,
                )
            os.replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
