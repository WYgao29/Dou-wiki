from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import EntryNotFoundError
from .models import ResearchTopic, TopicArtifact
from .time_utils import utc_now


class TopicTrashMixin:
    @property
    def _topic_trash_root(self) -> Path:
        return self.config.vault_path / ".douyin-wiki" / "trash" / "topics"

    def _topic_trash_item_dir(self, trash_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", trash_id):
            raise ValueError("废纸篓条目编号无效")
        path = self._topic_trash_root / trash_id
        self.vault._reject_vault_symlinks(path, field="topic_trash")
        return path

    def _topic_directory(self, topic_id: str) -> Path:
        self.vault._safe_id(topic_id, field="topic.id")
        path = self.config.vault_path / "topics" / topic_id
        self._check_topic_tree(path)
        return path

    def _check_topic_tree(self, path: Path) -> None:
        self.vault._reject_vault_symlinks(path, field="topic_files")
        if path.exists() and not path.is_dir():
            raise ValueError("专题目录不是文件夹")
        for child in path.rglob("*"):
            if child.is_symlink():
                raise ValueError("专题包含符号链接，无法移动")

    def _topic_exists(self, topic_id: str) -> bool:
        try:
            self.database.get_topic(topic_id)
        except KeyError:
            return False
        return True

    def _read_topic_trash(self, trash_id: str) -> tuple[Path, dict[str, Any]]:
        directory = self._topic_trash_item_dir(trash_id)
        path = directory / "manifest.json"
        self.vault._reject_vault_symlinks(path, field="topic_trash_manifest")
        if not path.is_file():
            raise EntryNotFoundError("废纸篓专题不存在")
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(manifest, dict)
                or manifest.get("version") != 1 or manifest.get("kind") != "topic"
                or manifest.get("trash_id") != trash_id
            ):
                raise ValueError("废纸篓专题格式无效")
            topic = ResearchTopic.model_validate(manifest["topic"])
            self.vault._safe_id(topic.id, field="topic.id")
            if datetime.fromisoformat(manifest["deleted_at"]).tzinfo is None:
                raise ValueError("废纸篓专题的删除时间无效")
            if any(int(manifest[field]) < 0 for field in ("file_count", "size_bytes")):
                raise ValueError("废纸篓专题的文件信息无效")
            if not isinstance(manifest["has_directory"], bool):
                raise ValueError("废纸篓专题的目录信息无效")
            for item in manifest["artifacts"]:
                artifact = TopicArtifact.model_validate(item)
                self.vault._safe_id(artifact.id, field="artifact.id")
                if artifact.topic_id != topic.id:
                    raise ValueError("废纸篓笔记不属于该专题")
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("废纸篓专题已损坏") from exc
        return directory, manifest

    @staticmethod
    def _topic_trash_item(manifest: dict[str, Any]) -> dict[str, Any]:
        topic = manifest["topic"]
        return {
            "kind": "topic", "source_kind": "topic", "trash_id": manifest["trash_id"],
            "topic_id": topic["id"], "title": topic["title"],
            "source_count": len(topic["sources"]), "note_count": len(manifest["artifacts"]),
            "deleted_at": manifest["deleted_at"], "file_count": manifest["file_count"],
            "size_bytes": manifest["size_bytes"], "warnings": manifest.get("warnings", []),
        }

    def list_trashed_topics(self) -> list[dict[str, Any]]:
        self.vault._reject_vault_symlinks(self._topic_trash_root, field="topic_trash")
        items = []
        for path in self._topic_trash_root.glob("*/manifest.json"):
            try:
                _, manifest = self._read_topic_trash(path.parent.name)
                if self._trash_phase(manifest) in {"completed", "database_deleted"}:
                    items.append(self._topic_trash_item(manifest))
            except (OSError, ValueError, EntryNotFoundError):
                continue
        return sorted(items, key=lambda item: item["deleted_at"], reverse=True)

    def _finalize_topic_trash(self, topic_id: str, *, restored: bool) -> list[str]:
        try:
            if restored:
                self.persist_topic(self.database.get_topic(topic_id))
            else:
                with self.vault.locked():
                    index = self.vault.write_topics_index(self.database.list_topics())
                    self.commit_vault(
                        [self._topic_directory(topic_id), index], f"trash: topic {topic_id}"
                    )
        except Exception as exc:
            return [f"专题操作已完成，但专题文档更新失败：{exc}"]
        return []

    def trash_topic(self, topic_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("删除专题前需要用户明确确认")
        with self.vault.entry_operations_locked():
            topic = self.database.get_topic(topic_id)
            artifacts = self.database.list_topic_artifacts(topic_id)
            source = self._topic_directory(topic_id)
            trash_id = uuid.uuid4().hex
            directory = self._topic_trash_item_dir(trash_id)
            payload = directory / "files" / topic.id
            manifest = {
                "version": 1, "kind": "topic", "trash_id": trash_id, "phase": "prepared",
                "topic": topic.model_dump(mode="json"),
                "artifacts": [item.model_dump(mode="json") for item in artifacts],
                "deleted_at": utc_now().isoformat(), "has_directory": source.exists(),
                "file_count": sum(item.is_file() for item in source.rglob("*")),
                "size_bytes": self._path_size(source) if source.exists() else 0,
                "warnings": [],
            }
            self._write_trash_manifest(directory, manifest)
            try:
                if source.exists():
                    payload.parent.mkdir(parents=True)
                    shutil.move(str(source), str(payload))
                manifest["phase"] = "files_moved"
                self._write_trash_manifest(directory, manifest)
                self.database.delete_topic_projection(topic_id)
                manifest["phase"] = "database_deleted"
                self._write_trash_manifest(directory, manifest)
            except Exception:
                if not self._topic_exists(topic_id):
                    self.database.restore_topic_bundle(topic, artifacts)
                if payload.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(payload), str(source))
                shutil.rmtree(directory)
                raise
            manifest["warnings"] = self._finalize_topic_trash(topic_id, restored=False)
            manifest["phase"] = "completed"
            try:
                self._write_trash_manifest(directory, manifest)
            except OSError as exc:
                manifest["warnings"].append(f"专题已删除，恢复日志将在重启后补全：{exc}")
            return self._topic_trash_item(manifest)

    def restore_trashed_topic(self, trash_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("恢复专题前需要用户明确确认")
        with self.vault.entry_operations_locked():
            directory, manifest = self._read_topic_trash(trash_id)
            if self._trash_phase(manifest) not in {"completed", "database_deleted"}:
                raise ValueError("专题正在恢复或维护，请稍后重试")
            topic = ResearchTopic.model_validate(manifest["topic"])
            artifacts = [TopicArtifact.model_validate(item) for item in manifest["artifacts"]]
            target = self._topic_directory(topic.id)
            payload = directory / "files" / topic.id
            self._check_topic_tree(payload)
            if self._topic_exists(topic.id) or target.exists():
                raise ValueError("已经存在同一专题或同名目录，无法恢复")
            if manifest["has_directory"] and not payload.is_dir():
                raise ValueError("废纸篓中的专题文件缺失，无法恢复")
            manifest["phase"] = "restoring"
            self._write_trash_manifest(directory, manifest)
            try:
                if payload.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(payload), str(target))
                restored = self.database.restore_topic_bundle(topic, artifacts)
                manifest["phase"] = "restored"
                self._write_trash_manifest(directory, manifest)
            except Exception:
                if self._topic_exists(topic.id):
                    self.database.delete_topic_projection(topic.id)
                if target.exists():
                    payload.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), str(payload))
                manifest["phase"] = "completed"
                self._write_trash_manifest(directory, manifest)
                raise
            warnings = self._finalize_topic_trash(topic.id, restored=True)
            if len(restored.sources) < len(topic.sources):
                warnings.append("部分来源文章已删除；已恢复专题和笔记，并保留仍然存在的来源。")
            if not warnings:
                try:
                    shutil.rmtree(directory)
                except OSError as exc:
                    warnings.append(f"专题已恢复，废纸篓残留将在重启后清理：{exc}")
            return {"status": "已恢复", "topic_id": topic.id, "title": topic.title,
                    "kind": "topic", "warnings": warnings}

    def permanently_delete_trashed_topic(
        self, trash_id: str, *, confirmed: bool = False
    ) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("彻底删除前需要用户明确确认")
        with self.vault.entry_operations_locked():
            directory, manifest = self._read_topic_trash(trash_id)
            if self._trash_phase(manifest) != "completed":
                raise ValueError("专题正在恢复或维护，暂时不能彻底删除")
            manifest["phase"] = "purging"
            self._write_trash_manifest(directory, manifest)
            shutil.rmtree(directory)
            return {"status": "已彻底删除", **self._topic_trash_item(manifest)}

    def recover_topic_trash_operations_locked(self) -> dict[str, Any]:
        report: dict[str, Any] = {"recovered": [], "completed": [], "warnings": []}
        self.vault._reject_vault_symlinks(self._topic_trash_root, field="topic_trash")
        for path in sorted(self._topic_trash_root.glob("*/manifest.json")):
            try:
                directory, manifest = self._read_topic_trash(path.parent.name)
                topic = ResearchTopic.model_validate(manifest["topic"])
                target = self._topic_directory(topic.id)
                payload = directory / "files" / topic.id
                self._check_topic_tree(payload)
                phase = self._trash_phase(manifest)
                exists = self._topic_exists(topic.id)
                if phase == "purging":
                    shutil.rmtree(directory)
                elif phase in {"restoring", "restored"}:
                    if exists:
                        if payload.exists() and not target.exists():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(payload), str(target))
                        warnings = self._finalize_topic_trash(topic.id, restored=True)
                        if warnings:
                            report["warnings"].extend(warnings)
                            continue
                        shutil.rmtree(directory)
                    elif phase == "restored":
                        shutil.rmtree(directory)
                    else:
                        if target.exists() and not payload.exists():
                            payload.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(target), str(payload))
                        manifest["phase"] = "completed"
                        self._write_trash_manifest(directory, manifest)
                elif phase in {"prepared", "files_moved", "database_deleted"}:
                    if exists:
                        if payload.exists():
                            if target.exists():
                                raise ValueError("专题恢复位置已有文件")
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(payload), str(target))
                        shutil.rmtree(directory)
                    else:
                        manifest["warnings"] = self._finalize_topic_trash(topic.id, restored=False)
                        manifest["phase"] = "completed"
                        self._write_trash_manifest(directory, manifest)
                elif phase == "completed" and manifest.get("warnings") and not exists:
                    manifest["warnings"] = self._finalize_topic_trash(topic.id, restored=False)
                    self._write_trash_manifest(directory, manifest)
                else:
                    continue
                report["completed"].append(manifest["trash_id"])
            except Exception as exc:
                report["warnings"].append(f"专题废纸篓恢复失败：{path.parent.name}：{exc}")
        return report
