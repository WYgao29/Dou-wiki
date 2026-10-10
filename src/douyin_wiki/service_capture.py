from __future__ import annotations

import asyncio
import shutil
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any

from .analysis_rules import PROMPT_VERSION
from .errors import (
    ExternalToolError,
    JobStateError,
)
from .models import (
    AnalysisResult,
    EntryRecord,
    JobRecord,
    JobStatus,
    OCRObservation,
    ReminderCandidate,
    RetentionPolicy,
    SourceKind,
    TranscriptSegment,
    VideoMetadata,
)
from .review import (
    apply_review_resolutions,
    transcript_confidence_info,
)
from .time_utils import beijing_date, utc_now
from .vault import safe_filename


class CaptureMixin:
    async def process_media_restore(self, job: JobRecord) -> JobRecord:
        entry_id = str(job.artifacts.get("entry_id") or "")
        entry = self.database.get_entry(entry_id)
        if not entry.favorite:
            return self.database.update_job(
                job.id,
                status=JobStatus.COMPLETED,
                progress=1,
                result={"entry_id": entry.id, "media_restored": False, "skipped": True},
                unlock=True,
            )
        raw_parent = Path(entry.raw_path).parent
        raw_root = raw_parent.parent if raw_parent.name == "records" else raw_parent
        assets_dir = self.config.vault_path / raw_root / "assets" / entry.video_id
        self.database.update_job(job.id, status=JobStatus.DOWNLOADING, progress=0.2)
        async with self.download_semaphore:
            self.check_job_cancelled(job)
            metadata = await self.downloader.download(
                entry.canonical_url,
                entry.video_id,
                assets_dir,
            )
        with self.vault.entry_operations_locked():
            self.check_job_cancelled(job)
            current = self.database.get_entry(entry.id)
            data = self.database.get_entry_data(entry.id)
            downloaded = metadata.model_dump(mode="json")
            latest_metadata = dict(data.get("metadata") or downloaded)
            for field in ("media_path", "thumbnail_path", "thumbnail_kind", "image_paths"):
                latest_metadata[field] = downloaded[field]
            data["metadata"] = latest_metadata
            now = utc_now()
            keep = current.favorite
            updated = current.model_copy(
                update={
                    "media_status": "present",
                    "retention": (RetentionPolicy.KEEP if keep else RetentionPolicy.TEMPORARY),
                    "media_expires_at": (
                        None if keep else now + timedelta(days=self.config.media.retention_days)
                    ),
                    "updated_at": now,
                }
            )
            chunks, relations, reminders = self.prepare_entry_bundle(updated, data)
            self.persist_entry_documents_and_bundle_locked(
                updated,
                data,
                chunks,
                relations,
                reminders,
                action="media_restore",
                log_summary="恢复收藏视频媒体",
                commit_message=f"media: restore {current.video_id} {current.title}",
            )
            return self.database.update_job(
                job.id,
                status=JobStatus.COMPLETED,
                progress=1,
                result={"entry_id": entry.id, "media_restored": True},
                unlock=True,
            )

    async def process_capture(self, job: JobRecord) -> JobRecord:
        if job.artifacts.get("shared_capture_job_id"):
            return self.capture_sharing.refresh(job.id)
        request = job.request
        artifacts = dict(job.artifacts)

        if "resolved" not in artifacts:
            self.database.update_job(job.id, status=JobStatus.RESOLVING, progress=0.03)
            resolved = await self.resolver.resolve(request.share_text)
            artifacts["resolved"] = {
                "original_url": resolved.original_url,
                "canonical_url": resolved.canonical_url,
                "video_id": resolved.video_id,
                "redirect_chain": list(resolved.redirect_chain),
                "source_kind": resolved.source_kind.value,
            }
            self.database.update_job(
                job.id, artifacts={"resolved": artifacts["resolved"]}, progress=0.08
            )
        resolved_data = artifacts["resolved"]
        video_id = resolved_data["video_id"]
        cancelled = self.finish_cancel_if_requested(job)
        if cancelled is not None:
            return cancelled

        async with self._work_capture_locked(video_id):
            return await self._process_resolved_capture(job, artifacts, resolved_data)

    @asynccontextmanager
    async def _work_capture_locked(self, work_id: str):
        lock = self.vault.work_capture_locked(work_id)
        acquisition = asyncio.create_task(asyncio.to_thread(lock.__enter__))
        try:
            await asyncio.shield(acquisition)
        except asyncio.CancelledError:
            # Cancellation cannot stop the executor thread.  Wait for a
            # blocked flock to finish, then release it before propagating the
            # cancellation so a later capture cannot inherit a leaked lock.
            while not acquisition.done():
                try:
                    await asyncio.shield(acquisition)
                except asyncio.CancelledError:
                    continue
            if not acquisition.cancelled():
                try:
                    acquisition.result()
                except BaseException:
                    pass
                else:
                    await asyncio.to_thread(lock.__exit__, None, None, None)
            raise
        try:
            yield
        except BaseException as exc:
            await asyncio.to_thread(lock.__exit__, type(exc), exc, exc.__traceback__)
            raise
        else:
            await asyncio.to_thread(lock.__exit__, None, None, None)

    async def _process_resolved_capture(
        self,
        job: JobRecord,
        artifacts: dict[str, Any],
        resolved_data: dict[str, Any],
    ) -> JobRecord:
        request = job.request
        work_dir = self.config.work_dir / job.id
        work_dir.mkdir(parents=True, exist_ok=True)
        video_id = resolved_data["video_id"]

        if not artifacts.get("creator_context"):
            known_creator = self.database.find_creator_for_work(video_id)
            if known_creator:
                artifacts["creator_context"] = self.creator_context(
                    known_creator.id, known_creator.folder_path, video_id
                )
                self.database.update_job(
                    job.id, artifacts={"creator_context": artifacts["creator_context"]}
                )

        if resolved_data.get("source_kind", SourceKind.VIDEO.value) == SourceKind.IMAGE_NOTE.value:
            return await self._process_image_note_capture(job, artifacts, resolved_data)

        with self.vault.entry_operations_locked():
            self.check_job_cancelled(job)
            # Re-read after the work lock so a preceding capture can win the
            # first-write race and its user data is never lost.
            existing = self.database.find_entry_by_video_id(video_id)
            if artifacts.get("reacquire_entry_id"):
                self.database.get_entry(artifacts["reacquire_entry_id"])
            if existing:
                for inspiration in self.capture_sharing.inspirations(job):
                    existing = self.add_inspiration_locked(existing.id, inspiration)
                should_reacquire = (
                    artifacts.get("reacquire_entry_id") == existing.id
                    or (
                        existing.media_status == "removed"
                        and request.options.retention != RetentionPolicy.DISCARD
                    )
                )
                if not should_reacquire:
                    existing = self.repair_entry_if_needed_locked(existing)
                    return self.database.update_job(
                        job.id,
                        status=JobStatus.COMPLETED,
                        progress=1,
                        result={
                            "entry_id": existing.id,
                            "duplicate": True,
                            "source_path": existing.source_path,
                        },
                        unlock=True,
                    )
                if artifacts.get("reacquire_entry_id") != existing.id:
                    artifacts["reacquire_entry_id"] = existing.id
                    self.database.update_job(
                        job.id, artifacts={"reacquire_entry_id": existing.id}
                    )
        shared = self.capture_sharing.join_if_active(job.id, video_id)
        if shared is not None:
            return self.capture_sharing.refresh(shared.id)
        effective_inspirations = existing.inspirations if existing else request.inspirations

        creator_context = artifacts.get("creator_context") or {}
        creator_folder = str(creator_context.get("folder_path") or "")
        assets_dir = (
            self.config.vault_path / creator_folder / "raw" / "assets" / video_id
            if creator_folder
            else self.config.vault_path / "raw" / "assets" / video_id
        )
        checkpoint_video = Path(str(artifacts.get("video_path") or ""))
        video_checkpoint_valid = (
            bool(artifacts.get("video_path"))
            and checkpoint_video.is_file()
            and checkpoint_video.stat().st_size > 0
        )
        if "metadata" not in artifacts or not video_checkpoint_valid:
            self.database.update_job(job.id, status=JobStatus.DOWNLOADING, progress=0.12)
            async with self.download_semaphore:
                self.check_job_cancelled(job)
                metadata = await self.downloader.download(
                    resolved_data["canonical_url"], video_id, assets_dir
                )
            artifacts.update(
                {"metadata": metadata.model_dump(mode="json"), "video_path": metadata.media_path}
            )
            self.database.update_job(
                job.id,
                artifacts={
                    "metadata": artifacts["metadata"],
                    "video_path": artifacts["video_path"],
                },
                progress=0.28,
            )
        metadata = VideoMetadata.model_validate(artifacts["metadata"])
        if not creator_folder:
            creator_context, metadata, assets_dir = self.adopt_creator_capture(
                metadata,
                work_id=video_id,
                original_url=str(resolved_data["original_url"]),
                canonical_url=str(resolved_data["canonical_url"]),
                current_dir=assets_dir,
                media_folder="assets",
            )
            if creator_context:
                creator_folder = str(creator_context["folder_path"])
                artifacts["creator_context"] = creator_context
                artifacts["metadata"] = metadata.model_dump(mode="json")
                artifacts["video_path"] = metadata.media_path
                self.database.update_job(
                    job.id,
                    artifacts={
                        "creator_context": creator_context,
                        "metadata": artifacts["metadata"],
                        "video_path": artifacts["video_path"],
                    },
                )
        video_path = Path(artifacts["video_path"])
        if metadata.duration_seconds is None:
            metadata.duration_seconds = await self.media.probe_duration(video_path)
            artifacts["metadata"] = metadata.model_dump(mode="json")
            self.database.update_job(job.id, artifacts={"metadata": artifacts["metadata"]})

        if "transcript_raw" not in artifacts:
            self.database.update_job(job.id, status=JobStatus.TRANSCRIBING, progress=0.34)
            audio_path = assets_dir / "audio.wav"
            frames_dir = assets_dir / "frames"
            async with self.media_semaphore:
                self.check_job_cancelled(job)
                # Prefer audio.mp4; also accept other retained audio.* from CDN merge.
                retained_audio = [
                    path
                    for path in sorted(
                        assets_dir.glob("audio.*"),
                        key=lambda item: (
                            0 if item.suffix.lower() == ".mp4" else 1,
                            item.name,
                        ),
                    )
                    if path.is_file()
                    and path.suffix.lower()
                    not in {".wav", ".json", ".part", ".jpg", ".jpeg", ".png", ".webp"}
                ]
                audio_sources = [*retained_audio, video_path]
                audio_ready = False
                for source in audio_sources:
                    if not source.exists():
                        continue
                    try:
                        await self.media.extract_audio(source, audio_path)
                    except ExternalToolError as exc:
                        audio_path.unlink(missing_ok=True)
                        if source == video_path:
                            detail = str(exc.details.get("stderr", "")).lower()
                            no_audio = "does not contain any stream" in detail or "没有音轨" in str(
                                exc
                            )
                            if not no_audio:
                                raise
                        continue
                    if audio_path.is_file() and audio_path.stat().st_size > 0:
                        audio_ready = True
                        break
                    audio_path.unlink(missing_ok=True)
                if not audio_ready:
                    artifacts["transcript_skipped"] = "no_audio_available"
                try:
                    transcript = (
                        await self.transcriber.transcribe(audio_path, work_dir / "asr")
                        if audio_ready
                        else []
                    )
                    asr_provenance = (
                        deepcopy(getattr(self.transcriber, "provenance", {})) if audio_ready else {}
                    )
                    frames = await self.media.extract_frames(
                        video_path,
                        frames_dir,
                        duration_seconds=metadata.duration_seconds or 0,
                        interval_seconds=self.config.media.frame_interval_seconds,
                        scene_threshold=self.config.media.scene_threshold,
                        max_frames=self.config.media.max_frames,
                    )
                    try:
                        ocr = await self.ocr.recognize(frames)
                    except ExternalToolError as exc:
                        ocr = []
                        artifacts["ocr_warning"] = str(exc)
                finally:
                    if audio_path.exists():
                        audio_path.unlink()
            artifacts["transcript_raw"] = [item.model_dump(mode="json") for item in transcript]
            artifacts["ocr"] = [item.model_dump(mode="json") for item in ocr]
            artifacts["media_provenance"] = {
                "asr": asr_provenance,
                "ocr": getattr(self.ocr, "provenance", {}),
            }
            self.database.update_job(
                job.id,
                artifacts={
                    "transcript_raw": artifacts["transcript_raw"],
                    "ocr": artifacts["ocr"],
                    "media_provenance": artifacts["media_provenance"],
                    **(
                        {"ocr_warning": artifacts["ocr_warning"]}
                        if artifacts.get("ocr_warning")
                        else {}
                    ),
                    **(
                        {"transcript_skipped": artifacts["transcript_skipped"]}
                        if artifacts.get("transcript_skipped")
                        else {}
                    ),
                },
                progress=0.58,
            )
        transcript_raw = [
            TranscriptSegment.model_validate(item) for item in artifacts["transcript_raw"]
        ]
        # Also enrich resumed jobs from their stored transcript, without changing
        # any existing review issue or acknowledging it on the user's behalf.
        confidence_info = transcript_confidence_info(transcript_raw)
        provenance = dict(artifacts.get("media_provenance") or {})
        asr_provenance = {**(provenance.get("asr") or {}), **confidence_info}
        if confidence_info and provenance.get("asr") != asr_provenance:
            provenance["asr"] = asr_provenance
            artifacts["media_provenance"] = provenance
            self.database.update_job(job.id, artifacts={"media_provenance": provenance})
        ocr_items = artifacts.get("ocr", [])

        if artifacts.get("reacquire_entry_id"):
            self.database.get_entry(artifacts["reacquire_entry_id"])
        if "transcript_corrected" not in artifacts:
            return self.database.update_job(
                job.id,
                status=JobStatus.AWAITING_AGENT_ANALYSIS,
                progress=0.62,
                result={
                    "phase": "transcript_correction",
                    "next_tool": "get_analysis_context",
                },
                unlock=True,
            )

        corrected = [
            TranscriptSegment.model_validate(item) for item in artifacts["transcript_corrected"]
        ]
        review_issues = self.database.get_review_issues(job.id)
        if artifacts.get("review_resolved") and review_issues:
            corrected = apply_review_resolutions(corrected, review_issues)
            artifacts["transcript_corrected"] = [item.model_dump(mode="json") for item in corrected]
            self.database.update_job(
                job.id,
                artifacts={"transcript_corrected": artifacts["transcript_corrected"]},
                progress=0.68,
            )

        if "analysis" not in artifacts:
            return self.database.update_job(
                job.id,
                status=JobStatus.AWAITING_AGENT_ANALYSIS,
                progress=0.7,
                result={"phase": "analysis", "next_tool": "get_analysis_context"},
                unlock=True,
            )

        self.database.update_job(
            job.id, status=JobStatus.ANALYZING,
            progress=max(self.database.get_job(job.id).progress, 0.7),
        )

        now = utc_now()
        validated_analysis = AnalysisResult.model_validate(artifacts["analysis"])
        self._validate_analysis_tags(validated_analysis, job_id=job.id)
        self.validate_analysis_evidence(
            validated_analysis,
            {**artifacts, "metadata": metadata.model_dump(mode="json")},
        )
        analysis_data = validated_analysis.model_dump(mode="json")
        analysis_data["contradictions"] = self.normalize_contradictions(
            analysis_data.get("contradictions", []), f"dy-{video_id}"
        )
        title = safe_filename(analysis_data.get("title") or metadata.title)
        entry_id = f"dy-{video_id}"
        date_prefix = beijing_date(metadata.published_at or now)
        filename = f"{date_prefix}_{title}_{video_id}.md"
        raw_relative = (
            existing.raw_path
            if existing
            else str(
                Path(creator_folder) / "raw" / "records" / filename
                if creator_folder
                else Path("raw") / filename
            )
        )
        source_relative = (
            existing.source_path
            if existing
            else str(
                Path(creator_folder) / "sources" / filename
                if creator_folder
                else Path("wiki") / "sources" / filename
            )
        )
        cover_path = self.persist_video_cover(
            video_id,
            assets_dir,
            thumbnail_path=metadata.thumbnail_path,
            creator_folder=creator_folder or None,
        )
        expiry = (
            now + timedelta(days=self.config.media.retention_days)
            if request.options.retention == RetentionPolicy.TEMPORARY
            else None
        )
        entry = EntryRecord(
            id=entry_id,
            video_id=video_id,
            title=title,
            original_url=resolved_data["original_url"],
            canonical_url=resolved_data["canonical_url"],
            raw_path=raw_relative,
            source_path=source_relative,
            status="active",
            media_status="present",
            retention=request.options.retention,
            media_expires_at=expiry,
            summary=analysis_data.get("one_liner") or analysis_data.get("summary", ""),
            inspirations=effective_inspirations,
            tags=analysis_data.get("tags", []),
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        data: dict[str, Any] = {
            "share_text": request.share_text,
            "inspirations": [item.model_dump(mode="json") for item in effective_inspirations],
            "metadata": metadata.model_dump(mode="json"),
            "transcript_raw": artifacts["transcript_raw"],
            "transcript_corrected": artifacts["transcript_corrected"],
            "transcript_edits": artifacts.get("transcript_edits", []),
            "correction_notes": artifacts.get("correction_notes", []),
            "ocr": ocr_items,
            "review_issues": [item.model_dump(mode="json") for item in review_issues]
            or artifacts.get("review_issues", []),
            "analysis": analysis_data,
            "relations": [],
            "provider": f"agent:{artifacts.get('analysis_producer', 'gateway')}",
            "model": artifacts.get("analysis_model", "agent"),
            "prompt_version": f"external:{PROMPT_VERSION}",
            "cover_path": cover_path,
            "cover_kind": metadata.thumbnail_kind or ("fallback" if cover_path else None),
            "creator": (
                {
                    "id": creator_context.get("id"),
                    "folder_path": creator_folder,
                    "parent_job_id": creator_context.get("parent_job_id"),
                }
                if creator_folder
                else {}
            ),
        }
        reminders = [
            ReminderCandidate.model_validate(item) for item in analysis_data.get("reminders", [])
        ]
        with self.vault.entry_operations_locked():
            self.check_job_cancelled(job)
            if existing:
                entry = self.merge_reacquisition_state(entry, data, existing.id)
            entry = self.capture_sharing.apply_inspirations(job, entry, data)
            if entry.retention == RetentionPolicy.DISCARD:
                self.trash_assets(entry, mark_database=False)
                entry = entry.model_copy(update={"media_status": "removed"})
            chunks, relations, reminders = self.prepare_entry_bundle(
                entry, data, reminders=reminders
            )
            self.persist_entry_documents_and_bundle(
                entry,
                data,
                chunks,
                relations,
                reminders,
                action="ingest",
                log_summary=entry.summary,
                commit_message=f"ingest: {video_id} {entry.title}",
            )

            warnings = []
            if artifacts.get("ocr_warning"):
                warnings.append(f"OCR 未完成：{artifacts['ocr_warning']}")
            if artifacts.get("transcript_skipped") == "no_audio_available":
                warnings.append("视频无音轨，未生成逐字稿；分析仅依据画面文字和作品信息")
            if artifacts.get("analysis_evidence_warning"):
                warnings.append(artifacts["analysis_evidence_warning"])
            status = JobStatus.COMPLETED_WITH_WARNINGS if warnings else JobStatus.COMPLETED
            return self.database.update_job(
                job.id,
                status=status,
                progress=1,
                result={
                    "entry_id": entry.id,
                    "source_path": entry.source_path,
                    "raw_path": entry.raw_path,
                    "summary": entry.summary,
                    "ai_judgment": analysis_data.get("ai_judgment", ""),
                    "reminder_candidates": analysis_data.get("reminders", []),
                    "warnings": warnings,
                    "media_provenance": artifacts.get("media_provenance", {}),
                    "reacquired": bool(existing),
                },
                unlock=True,
            )

    async def _process_image_note_capture(
        self,
        job: JobRecord,
        artifacts: dict[str, Any],
        resolved_data: dict[str, Any],
    ) -> JobRecord:
        """Process a static image work without invoking any video-only adapter."""
        request = job.request
        work_id = str(resolved_data["video_id"])
        with self.vault.entry_operations_locked():
            self.check_job_cancelled(job)
            # The work lock makes this re-read authoritative for a waiter.
            existing = self.database.find_entry_by_video_id(work_id)
            if artifacts.get("reacquire_entry_id"):
                self.database.get_entry(artifacts["reacquire_entry_id"])
            if existing:
                for inspiration in self.capture_sharing.inspirations(job):
                    existing = self.add_inspiration_locked(existing.id, inspiration)
                existing_data = self.database.get_entry_data(existing.id)
                if (
                    artifacts.get("reacquire_entry_id") != existing.id
                    and self.image_note_files_intact(existing_data)
                ):
                    existing = self.repair_entry_if_needed_locked(existing)
                    return self.database.update_job(
                        job.id,
                        status=JobStatus.COMPLETED,
                        progress=1,
                        result={
                            "entry_id": existing.id,
                            "duplicate": True,
                            "source_kind": SourceKind.IMAGE_NOTE.value,
                            "source_path": existing.source_path,
                        },
                        unlock=True,
                    )
                if artifacts.get("reacquire_entry_id") != existing.id:
                    artifacts["reacquire_entry_id"] = existing.id
                    self.database.update_job(
                        job.id, artifacts={"reacquire_entry_id": existing.id}
                    )
        shared = self.capture_sharing.join_if_active(job.id, work_id)
        if shared is not None:
            return self.capture_sharing.refresh(shared.id)
        effective_inspirations = existing.inspirations if existing else request.inspirations

        creator_context = artifacts.get("creator_context") or {}
        creator_folder = str(creator_context.get("folder_path") or "")
        images_dir = (
            self.config.vault_path / creator_folder / "raw" / "images" / work_id
            if creator_folder
            else self.config.vault_path / "raw" / "images" / work_id
        )
        metadata: VideoMetadata | None = None
        if "metadata" in artifacts:
            candidate = VideoMetadata.model_validate(artifacts["metadata"])
            if self.metadata_image_paths_exist(candidate):
                metadata = candidate
        if metadata is None:
            self.database.update_job(job.id, status=JobStatus.DOWNLOADING, progress=0.15)
            async with self.download_semaphore:
                self.check_job_cancelled(job)
                metadata = await self.image_note_downloader.download(
                    resolved_data["canonical_url"], work_id, images_dir
                )
            metadata.original_url = resolved_data["original_url"]
            metadata.canonical_url = resolved_data["canonical_url"]
            metadata.source_kind = SourceKind.IMAGE_NOTE
            artifacts["metadata"] = metadata.model_dump(mode="json")
            self.database.update_job(
                job.id, artifacts={"metadata": artifacts["metadata"]}, progress=0.42
            )

        if not creator_folder:
            creator_context, metadata, images_dir = self.adopt_creator_capture(
                metadata,
                work_id=work_id,
                original_url=str(resolved_data["original_url"]),
                canonical_url=str(resolved_data["canonical_url"]),
                current_dir=images_dir,
                media_folder="images",
            )
            if creator_context:
                creator_folder = str(creator_context["folder_path"])
                artifacts["creator_context"] = creator_context
                artifacts["metadata"] = metadata.model_dump(mode="json")
                self.database.update_job(
                    job.id,
                    artifacts={
                        "creator_context": creator_context,
                        "metadata": artifacts["metadata"],
                    },
                )

        absolute_images = [self.vault_path(value) for value in metadata.image_paths]
        images_complete = absolute_images and all(
            path.is_file() and path.stat().st_size for path in absolute_images
        )
        if not images_complete:
            raise JobStateError("图文采集完成，但原图文件不完整")

        if "ocr" not in artifacts:
            self.database.update_job(job.id, status=JobStatus.EXTRACTING, progress=0.48)
            async with self.media_semaphore:
                self.check_job_cancelled(job)
                try:
                    raw_ocr = await self.ocr.recognize(
                        [(index, path) for index, path in enumerate(absolute_images, start=1)]
                    )
                except ExternalToolError as exc:
                    raw_ocr = []
                    artifacts["ocr_warning"] = str(exc)
            image_index_by_path = {
                str(path): index for index, path in enumerate(absolute_images, start=1)
            }
            ocr_items: list[OCRObservation] = []
            for observation in raw_ocr:
                image_index = (
                    observation.image_index
                    or observation.source_index
                    or image_index_by_path.get(
                        str(Path(observation.image_path).resolve())
                        if observation.image_path
                        else ""
                    )
                )
                if image_index is None and observation.timestamp_ms:
                    image_index = int(observation.timestamp_ms)
                ocr_items.append(
                    observation.model_copy(
                        update={
                            "timestamp_ms": None,
                            "image_index": image_index,
                            "image_path": self.vault_relative(
                                absolute_images[(image_index or 1) - 1]
                            ),
                        }
                    )
                )
            artifacts["ocr"] = [item.model_dump(mode="json") for item in ocr_items]
            artifacts["media_provenance"] = {"ocr": getattr(self.ocr, "provenance", {})}
            self.database.update_job(
                job.id,
                artifacts={
                    "ocr": artifacts["ocr"],
                    "media_provenance": artifacts["media_provenance"],
                    **(
                        {"ocr_warning": artifacts["ocr_warning"]}
                        if artifacts.get("ocr_warning")
                        else {}
                    ),
                },
                progress=0.58,
            )
        ocr_models = [self.ocr_model(item) for item in artifacts.get("ocr", [])]

        issues = self.detect_image_review_issues(ocr_models, metadata.post_text or "")
        if issues and not artifacts.get("review_resolved"):
            artifacts["ocr_quality_notes"] = [item.model_dump(mode="json") for item in issues]
            self.database.update_job(
                job.id, artifacts={"ocr_quality_notes": artifacts["ocr_quality_notes"]}
            )
        if artifacts.get("review_resolved"):
            resolved_issues = self.database.get_review_issues(job.id)
            ocr_models = self.apply_image_review_resolutions(ocr_models, resolved_issues)
            artifacts["ocr"] = [item.model_dump(mode="json") for item in ocr_models]
            self.database.update_job(job.id, artifacts={"ocr": artifacts["ocr"]})

        if artifacts.get("reacquire_entry_id"):
            self.database.get_entry(artifacts["reacquire_entry_id"])
        if "analysis" not in artifacts:
            return self.database.update_job(
                job.id,
                status=JobStatus.AWAITING_AGENT_ANALYSIS,
                progress=0.68,
                result={"phase": "analysis", "next_tool": "get_analysis_context"},
                unlock=True,
            )

        self.database.update_job(job.id, status=JobStatus.ANALYZING, progress=0.7)
        now = utc_now()
        validated_analysis = AnalysisResult.model_validate(artifacts["analysis"])
        self._validate_analysis_tags(validated_analysis, job_id=job.id)
        self.validate_analysis_evidence(
            validated_analysis,
            {**artifacts, "metadata": metadata.model_dump(mode="json")},
        )
        analysis_data = validated_analysis.model_dump(mode="json")
        entry_id = f"dy-{work_id}"
        analysis_data["contradictions"] = self.normalize_contradictions(
            analysis_data.get("contradictions", []), entry_id
        )
        title = safe_filename(analysis_data.get("title") or metadata.title)
        date_prefix = beijing_date(metadata.published_at or now)
        filename = f"{date_prefix}_{title}_{work_id}.md"
        raw_relative = (
            existing.raw_path
            if existing
            else str(
                Path(creator_folder) / "raw" / "records" / filename
                if creator_folder
                else Path("raw") / filename
            )
        )
        source_relative = (
            existing.source_path
            if existing
            else str(
                Path(creator_folder) / "sources" / filename
                if creator_folder
                else Path("wiki") / "sources" / filename
            )
        )

        relative_images = [self.vault_relative(path) for path in absolute_images]
        stored_metadata = metadata.model_copy(
            update={
                "image_paths": relative_images,
                "thumbnail_path": relative_images[0],
                "thumbnail_kind": "image_note_first_image",
                "source_kind": SourceKind.IMAGE_NOTE,
            }
        )
        entry = EntryRecord(
            id=entry_id,
            video_id=work_id,
            title=title,
            original_url=resolved_data["original_url"],
            canonical_url=resolved_data["canonical_url"],
            raw_path=raw_relative,
            source_path=source_relative,
            status="active",
            media_status="present",
            retention=RetentionPolicy.KEEP,
            media_expires_at=None,
            summary=analysis_data.get("one_liner") or analysis_data.get("summary", ""),
            inspirations=effective_inspirations,
            tags=analysis_data.get("tags", []),
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        review_issues = self.database.get_review_issues(job.id)
        data: dict[str, Any] = {
            "share_text": request.share_text,
            "inspirations": [item.model_dump(mode="json") for item in effective_inspirations],
            "metadata": stored_metadata.model_dump(mode="json"),
            "ocr": [item.model_dump(mode="json") for item in ocr_models],
            "ocr_quality_notes": artifacts.get("ocr_quality_notes", []),
            "review_issues": [item.model_dump(mode="json") for item in review_issues]
            or artifacts.get("review_issues", []),
            "analysis": analysis_data,
            "relations": [],
            "provider": f"agent:{artifacts.get('analysis_producer', 'gateway')}",
            "model": artifacts.get("analysis_model", "agent"),
            "prompt_version": f"external:{PROMPT_VERSION}",
            "cover_path": relative_images[0],
            "cover_kind": "image_note_first_image",
            "creator": (
                {
                    "id": creator_context.get("id"),
                    "folder_path": creator_folder,
                    "parent_job_id": creator_context.get("parent_job_id"),
                }
                if creator_folder
                else {}
            ),
        }
        reminders = [
            ReminderCandidate.model_validate(item) for item in analysis_data.get("reminders", [])
        ]
        with self.vault.entry_operations_locked():
            self.check_job_cancelled(job)
            if existing:
                entry = self.merge_reacquisition_state(entry, data, existing.id)
            entry = self.capture_sharing.apply_inspirations(job, entry, data)
            chunks, relations, reminders = self.prepare_entry_bundle(
                entry, data, reminders=reminders
            )
            self.persist_entry_documents_and_bundle(
                entry,
                data,
                chunks,
                relations,
                reminders,
                action="ingest",
                log_summary=entry.summary,
                commit_message=f"ingest-image-note: {work_id} {entry.title}",
            )
            warnings: list[str] = []
            if artifacts.get("ocr_warning"):
                warnings.append(f"OCR 未完成：{artifacts['ocr_warning']}")
            status = JobStatus.COMPLETED_WITH_WARNINGS if warnings else JobStatus.COMPLETED
            return self.database.update_job(
                job.id,
                status=status,
                progress=1,
                result={
                    "entry_id": entry.id,
                    "source_kind": SourceKind.IMAGE_NOTE.value,
                    "source_path": entry.source_path,
                    "raw_path": entry.raw_path,
                    "image_count": len(relative_images),
                    "summary": entry.summary,
                    "reminder_candidates": analysis_data.get("reminders", []),
                    "warnings": warnings,
                    "media_provenance": artifacts.get("media_provenance", {}),
                    "reacquired": bool(existing),
                },
                unlock=True,
            )

    def merge_reacquisition_state(
        self, entry: EntryRecord, data: dict[str, Any], existing_id: str
    ) -> EntryRecord:
        """Keep user mutations made while media and analysis were running."""
        current = self.database.get_entry(existing_id)
        current_data = self.database.get_entry_data(existing_id)
        data["inspirations"] = [item.model_dump(mode="json") for item in current.inspirations]
        if "reminder_states" in current_data:
            data["reminder_states"] = current_data["reminder_states"]
        expires_at = current.media_expires_at
        now = utc_now()
        if current.retention == RetentionPolicy.TEMPORARY:
            if expires_at is None or expires_at <= now:
                expires_at = now + timedelta(days=self.config.media.retention_days)
        else:
            expires_at = None
        return entry.model_copy(
            update={
                "favorite": current.favorite,
                "retention": current.retention,
                "media_expires_at": expires_at,
                "inspirations": current.inspirations,
                "created_at": current.created_at,
            }
        )

    def persist_video_cover(
        self,
        video_id: str,
        assets_dir: Path,
        *,
        thumbnail_path: str | None = None,
        replace_existing: bool = False,
        creator_folder: str | None = None,
    ) -> str | None:
        covers_dir = (
            self.config.vault_path / creator_folder / "raw" / "covers"
            if creator_folder
            else self.config.vault_path / "raw" / "covers"
        )
        covers_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(covers_dir.glob(f"{video_id}.*"))
        if existing and not replace_existing:
            return str(existing[0].relative_to(self.config.vault_path))

        image_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
        candidates: list[Path] = []
        if thumbnail_path:
            candidates.append(Path(thumbnail_path))
        candidates.extend(
            path for path in assets_dir.glob("original.*") if path.suffix.lower() in image_suffixes
        )
        candidates.extend(path for path in (assets_dir / "frames").glob("*.jpg") if path.is_file())
        source = next(
            (
                path
                for path in candidates
                if path.is_file() and path.suffix.lower() in image_suffixes
            ),
            None,
        )
        if source is None:
            return str(existing[0].relative_to(self.config.vault_path)) if existing else None
        suffix = ".jpg" if source.suffix.lower() == ".jpeg" else source.suffix.lower()
        target = covers_dir / f"{video_id}{suffix}"
        shutil.copy2(source, target)
        return str(target.relative_to(self.config.vault_path))
