from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .analysis_rules import CONTENT_TAG_RULES, MAX_ANALYSIS_TAGS, PROMPT_VERSION
from .errors import (
    InvalidAnalysisError,
    JobStateError,
)
from .gateway_protocol import (
    GatewayProtocol,
    _batch_contexts,
    build_compact_context,
    gateway_phase,
    require_context_revision,
    validate_compact_analysis_shape,
    validate_gateway_protocol,
)
from .gateway_sources import build_source_catalog, expand_source_references
from .models import (
    AnalysisResult,
    EntryRecord,
    GatewayContext,
    JobRecord,
    JobStatus,
    OCRObservation,
    ReviewIssue,
    SourceKind,
    TranscriptCorrection,
    TranscriptSegment,
)
from .review import deduplicate_review_issues, detect_review_issues
from .time_utils import utc_now
from .timing import apply_timing, audio_timing
from .vault import safe_filename


class AnalysisMixin:
    def _validate_analysis_tags(
        self, analysis: AnalysisResult, *, job_id: str | None = None
    ) -> None:
        """Enforce new-analysis limits without restricting historical storage."""
        tags = list(dict.fromkeys(tag.strip() for tag in analysis.tags if tag.strip()))
        if len(tags) > MAX_ANALYSIS_TAGS:
            if job_id is not None:
                # Do not replay an invalid analysis or its model checkpoints on retry.
                self.database.update_job(
                    job_id,
                    remove_artifacts={"analysis", "analysis_candidate"},
                )
            raise JobStateError(
                f"分析标签最多 {MAX_ANALYSIS_TAGS} 个，请按作品中心内容精简后重新提交",
                details={"tag_count": len(tags), "max_tags": MAX_ANALYSIS_TAGS},
            )
        analysis.tags = tags

    def get_analysis_context(
        self, job_id: str, *, gateway_protocol: GatewayProtocol = 1
    ) -> dict[str, Any]:
        protocol = validate_gateway_protocol(gateway_protocol)
        job = self.database.get_job(job_id)
        if job.status not in {
            JobStatus.AWAITING_AGENT_ANALYSIS,
            JobStatus.NEEDS_REVIEW,
        }:
            raise JobStateError("只有处于“待 AI 处理”或“需要人工复核”状态的任务可读取分析上下文")
        artifacts = job.artifacts
        transcript = artifacts.get("transcript_corrected") or artifacts.get("transcript_raw", [])
        metadata = dict(artifacts.get("metadata", {}))
        metadata.pop("existing_tags", None)
        context_text = "\n".join(
            [
                *[item.text for item in job.request.inspirations],
                *[str(item.get("text", "")) for item in transcript],
                str(metadata.get("post_text") or ""),
                *[str(item.get("text", "")) for item in artifacts.get("ocr", [])],
            ]
        )
        if protocol == 2:
            phase = gateway_phase(job)
            if phase not in {"transcript_correction", "analysis"}:
                raise JobStateError("当前阶段不提供紧凑分析上下文", details={"phase": phase})
            return build_compact_context(
                job,
                catalog=build_source_catalog(artifacts, phase=phase),
                related_claims=self.indexer.find_related_claims(context_text)
                if phase == "analysis"
                else [],
            )
        is_image_note = metadata.get("source_kind") == SourceKind.IMAGE_NOTE.value
        return {
            "job_id": job.id,
            **_batch_contexts(job),
            "status": job.status.value,
            "phase": job.result.get("phase", "transcript_correction"),
            "gateway_context": job.request.gateway_context.model_dump(mode="json")
            if job.request.gateway_context
            else None,
            "metadata": metadata,
            "media_provenance": artifacts.get("media_provenance", {}),
            "inspirations_verbatim": [
                item.model_dump(mode="json") for item in job.request.inspirations
            ],
            "transcript_raw": artifacts.get("transcript_raw", []),
            "transcript_corrected": artifacts.get("transcript_corrected"),
            "ocr": artifacts.get("ocr", []),
            "review_issues": [
                issue.model_dump(mode="json")
                for issue in self.database.get_review_issues(job_id, open_only=True)
            ],
            "existing_knowledge": self.indexer.find_related_claims(context_text),
            "analysis_schema": AnalysisResult.model_json_schema(),
            "rules": [
                *CONTENT_TAG_RULES,
                "粗略时间来源必须说明整个窗口范围，不得编造精确句子时间；"
                "区分口播、旁白、配乐歌词，身份不明时说明限制，不自动归为作者观点。",
                "根据原话上下文和对应 OCR 直接给出最贴近原意的校正版，不请求人工校对，"
                "不增加独立复核调用；数字、金额、人名缺少依据时保留原表述。",
                "灵感必须逐字保留，不得改写或补造。",
                *(
                    []
                    if is_image_note
                    else [
                        "校正可根据前后文修复识别错误、同音字和断句，"
                        "不得摘要、删句或补充原视频没有的内容。"
                        "提交必须覆盖全部原稿 ID，未修改的片段也须回传原文。"
                    ]
                ),
                "分析必须区分作品原话、作品正文、OCR、AI 推断和用户灵感。",
                "事实、数字、日期、参数和方法应写入 knowledge_atoms，"
                "并尽可能带 quote，以及 timestamp_ms 或 image_index。",
                *(
                    [
                        "视频必须按内容展开顺序生成时间轴图解 chapters；章节起点应定位主题开始，"
                        "每章必须包含可核验的语音或画面证据。"
                    ]
                    if not is_image_note
                    else ["静态图文没有视频时间轴，chapters 必须为空数组。"]
                ),
                *(
                    ["图文不得伪造 00:00 时间戳，必须用 image_index 定位原图。"]
                    if is_image_note
                    else []
                ),
                "content_card.kind 必须与 content_type 相同；提醒时间不明确时必须标记需澄清。",
            ],
        }

    def submit_transcript_correction(
        self,
        job_id: str,
        corrections: list[TranscriptCorrection],
        *,
        producer: str,
        model: str = "agent",
        review_issues: list[ReviewIssue] | None = None,
        gateway_protocol: GatewayProtocol = 1,
        context_revision: str | None = None,
    ) -> JobRecord:
        protocol = validate_gateway_protocol(gateway_protocol)
        job = self.database.get_job(job_id)
        if job.status != JobStatus.AWAITING_AGENT_ANALYSIS:
            raise JobStateError("只有处于“待 AI 处理”状态的任务可提交逐字稿校正")
        if (
            "transcript_corrected" in job.artifacts
            or job.artifacts.get("metadata", {}).get("source_kind") == SourceKind.IMAGE_NOTE.value
        ):
            raise JobStateError("当前任务已进入分析阶段，不可再次提交逐字稿校正")
        if protocol == 2:
            if gateway_phase(job) != "transcript_correction":
                raise JobStateError("当前阶段不可提交逐字稿校正")
            require_context_revision(job, context_revision)
        raw = [
            TranscriptSegment.model_validate(item)
            for item in job.artifacts.get("transcript_raw", [])
        ]
        known_ids = {segment.id for segment in raw}
        if protocol == 2 and len(known_ids) != len(raw):
            raise JobStateError("原稿片段 ID 重复，不能应用校正补丁")
        by_id: dict[int, str] = {}
        for correction in corrections:
            if correction.id not in known_ids:
                raise JobStateError(f"未知逐字稿片段 id: {correction.id}")
            if correction.id in by_id:
                raise JobStateError(f"重复逐字稿片段 id: {correction.id}")
            text = correction.text.strip()
            if not text:
                raise JobStateError("校正文本不能仅包含空白")
            by_id[correction.id] = text
        if protocol == 1 and set(by_id) != known_ids:
            missing_ids = sorted(known_ids - set(by_id))
            raise JobStateError(f"校正结果必须覆盖全部片段，缺少 ID: {missing_ids}")
        corrected = [
            segment.model_copy(update={"text": by_id.get(segment.id, segment.text)})
            for segment in raw
        ]
        notes = deduplicate_review_issues([*detect_review_issues(raw), *(review_issues or [])])
        artifact_update = {
            "transcript_corrected": (
                [
                    {**source, "text": item.text}
                    for source, item in zip(
                        job.artifacts.get("transcript_raw", []), corrected, strict=True
                    )
                ]
                if protocol == 2
                else [item.model_dump(mode="json") for item in corrected]
            ),
            "transcript_edits": [
                {
                    "id": source.id,
                    "start_ms": source.start_ms,
                    "end_ms": source.end_ms,
                    "raw_text": source.text,
                    "corrected_text": item.text,
                }
                for source, item in zip(raw, corrected, strict=True)
                if source.text != item.text
            ],
            "correction_notes": [item.model_dump(mode="json") for item in notes],
            "review_issues": [],
            "correction_producer": producer,
            "correction_model": model,
        }
        updated = self.database.update_job(
            job_id,
            progress=0.68,
            artifacts=artifact_update,
            result={"phase": "analysis"},
            expected_updated_at=job.updated_at,
            expected_statuses={JobStatus.AWAITING_AGENT_ANALYSIS},
            unlock=True,
        )
        self.database.emit_job_event(job_id, JobStatus.AWAITING_AGENT_ANALYSIS, updated.result)
        return updated

    def submit_gateway_analysis(
        self,
        job_id: str,
        analysis: dict[str, Any],
        *,
        producer: str,
        model: str = "agent",
        gateway_protocol: GatewayProtocol = 1,
        context_revision: str | None = None,
    ) -> JobRecord:
        protocol = validate_gateway_protocol(gateway_protocol)
        job = self.database.get_job(job_id)
        if job.status != JobStatus.AWAITING_AGENT_ANALYSIS:
            raise JobStateError("只有处于“待 AI 处理”状态的任务可提交分析")
        source_kind = job.artifacts.get("metadata", {}).get("source_kind", "video")
        if (
            source_kind != SourceKind.IMAGE_NOTE.value
            and "transcript_corrected" not in job.artifacts
        ):
            raise JobStateError("请先提交逐字稿校正")
        if self.database.get_review_issues(job_id, open_only=True):
            raise JobStateError("逐字稿仍有未解决疑点")
        if protocol == 2:
            if gateway_phase(job) != "analysis":
                raise JobStateError("请读取当前分析阶段上下文")
            require_context_revision(job, context_revision)
            validate_compact_analysis_shape(analysis)
            analysis = expand_source_references(
                analysis,
                build_source_catalog(job.artifacts, phase="analysis"),
                source_kind=source_kind,
            )
        try:
            validated = AnalysisResult.model_validate(analysis)
        except ValidationError as exc:
            raise InvalidAnalysisError.from_validation(exc) from exc
        self._validate_analysis_tags(validated)
        self.validate_analysis_evidence(validated, job.artifacts)
        reanalyze_entry_id = job.artifacts.get("reanalyze_entry_id")
        video_id = job.artifacts.get("resolved", {}).get("video_id", "")
        source_entry_id = reanalyze_entry_id or f"dy-{video_id}"
        normalized = validated.model_dump(mode="json")
        normalized["contradictions"] = self.normalize_contradictions(
            normalized.get("contradictions", []), source_entry_id
        )
        return self.database.requeue_job(
            job_id,
            expected_updated_at=job.updated_at,
            result={"normalization_warnings": ["要点超过 5 条，已按协议 1 保留前 5 条"]}
            if protocol == 1 and len(analysis.get("takeaways", [])) > 5
            else None,
            artifacts={
                "analysis": normalized,
                "analysis_producer": producer,
                "analysis_model": model,
            },
        )

    def approve_job(self, job_id: str) -> JobRecord:
        job = self.database.get_job(job_id)
        if job.status != JobStatus.WAITING_CONFIRMATION:
            raise JobStateError("只有处于“等待用户确认”状态的任务可以批准")
        return self.database.requeue_job(
            job_id,
            artifacts={
                "ai_analysis_approved": True,
                "cloud_analysis_approved": True,
                "long_video_approved": True,
            },
        )

    def resolve_review(
        self,
        job_id: str,
        resolutions: dict[str, str] | None = None,
        *,
        accept_uncertain: bool = False,
    ) -> JobRecord:
        job = self.database.get_job(job_id)
        if job.status != JobStatus.NEEDS_REVIEW:
            raise JobStateError("只有处于“需要人工复核”状态的任务可以提交校对")
        issues = self.database.get_review_issues(job_id, open_only=True)
        if not accept_uncertain and set(resolutions or {}) != {issue.id for issue in issues}:
            raise JobStateError("必须解决全部疑点，或明确 accept_uncertain=true")
        resolved_values = resolutions or {issue.id: issue.raw_text for issue in issues}
        self.database.resolve_review_issues(job_id, resolved_values)
        return self.database.requeue_job(job_id, artifacts={"review_resolved": True})

    def submit_analysis(
        self,
        entry_id: str,
        analysis: dict[str, Any],
        *,
        producer: str,
        model: str = "agent",
    ) -> EntryRecord:
        with self.vault.entry_operations_locked():
            return self._submit_analysis_locked(entry_id, analysis, producer=producer, model=model)

    def _submit_analysis_locked(
        self,
        entry_id: str,
        analysis: dict[str, Any],
        *,
        producer: str,
        model: str,
    ) -> EntryRecord:
        entry = self.database.get_entry(entry_id)
        data = self.database.get_entry_data(entry_id)
        current_analysis = data.get("analysis", {})
        incoming = dict(analysis)
        # A v1 integration may edit only `summary` on a payload that also contains
        # v2 compatibility fields. Treat that as an intentional one-liner update.
        if (
            incoming.get("summary")
            and incoming.get("summary") != current_analysis.get("summary")
            and incoming.get("one_liner") == current_analysis.get("one_liner")
        ):
            incoming["one_liner"] = incoming["summary"]
        try:
            validated = AnalysisResult.model_validate(incoming)
        except ValidationError as exc:
            raise InvalidAnalysisError.from_validation(exc) from exc
        self._validate_analysis_tags(validated)
        self.validate_analysis_evidence(validated, data)
        validated = AnalysisResult.model_validate(
            {
                **validated.model_dump(mode="json"),
                "contradictions": self.normalize_contradictions(
                    validated.model_dump(mode="json").get("contradictions", []), entry.id
                ),
            }
        )
        data["analysis"] = validated.model_dump(mode="json")
        validated.tags = self.tags.resolve(validated.tags)
        data["analysis"]["tags"] = validated.tags
        data.pop("fact_checks", None)
        data["provider"] = f"agent:{producer}"
        data["model"] = model
        data["prompt_version"] = f"external:{PROMPT_VERSION}"
        entry = entry.model_copy(
            update={
                "title": safe_filename(validated.title),
                "summary": validated.one_liner,
                "tags": validated.tags,
                "updated_at": utc_now(),
            }
        )
        chunks, relations, reminders = self.prepare_entry_bundle(entry, data)
        return self.persist_entry_documents_and_bundle_locked(
            entry,
            data,
            chunks,
            relations,
            reminders,
            action="analysis",
            log_summary=f"由 {producer} 提交重分析",
            commit_message=f"analysis: {entry.video_id} {producer}",
            require_existing=True,
        )

    def reanalyze_entry(
        self,
        entry_id: str,
        *,
        force: bool = False,
        gateway_context: GatewayContext | None = None,
    ) -> JobRecord:
        entry = self.database.get_entry(entry_id)
        data = self.database.get_entry_data(entry_id)
        return self.database.create_reanalysis_job(
            entry,
            force=force,
            gateway_context=gateway_context,
            data=data,
        )

    def reanalyze_all(
        self,
        *,
        force: bool = False,
        gateway_context: GatewayContext | None = None,
    ) -> dict[str, Any]:
        queued: list[str] = []
        skipped: list[str] = []
        for entry in self.database.list_entries():
            data = self.database.get_entry_data(entry.id)
            if data.get("analysis", {}).get("analysis_version") == 2 and not force:
                skipped.append(entry.id)
                continue
            job = self.database.create_reanalysis_job(
                entry,
                force=force,
                gateway_context=gateway_context,
                data=data,
            )
            queued.append(job.id)
        return {"queued_job_ids": queued, "skipped_entry_ids": skipped, "force": force}

    async def process_reanalysis(self, job: JobRecord) -> JobRecord:
        cancelled = self.finish_cancel_if_requested(job)
        if cancelled is not None:
            return cancelled
        entry_id = str(job.artifacts.get("reanalyze_entry_id", ""))
        entry = self.database.get_entry(entry_id)
        current_data = self.database.get_entry_data(entry_id)
        if current_data.get("analysis", {}).get("analysis_version") == 2 and not job.artifacts.get(
            "force"
        ):
            return self.database.update_job(
                job.id,
                status=JobStatus.COMPLETED,
                progress=1,
                result={"entry_id": entry.id, "skipped": True, "reason": "already_v2"},
                unlock=True,
            )

        artifacts = dict(job.artifacts)
        if "analysis" not in artifacts:
            return self.database.update_job(
                job.id,
                status=JobStatus.AWAITING_AGENT_ANALYSIS,
                progress=0.7,
                result={"phase": "analysis", "next_tool": "get_analysis_context"},
                unlock=True,
            )

        self.database.update_job(job.id, status=JobStatus.ANALYZING, progress=0.72)
        validated = AnalysisResult.model_validate(artifacts["analysis"])
        self._validate_analysis_tags(validated, job_id=job.id)
        self.validate_analysis_evidence(validated, {**current_data, **artifacts})
        normalized = validated.model_dump(mode="json")
        normalized["contradictions"] = self.normalize_contradictions(
            normalized.get("contradictions", []), entry.id
        )
        validated = AnalysisResult.model_validate(normalized)
        with self.vault.entry_operations_locked():
            self.check_job_cancelled(job)
            # Agent analysis arrives before this lock. Reload both
            # projections so concurrent user mutations are merged into the
            # final analysis write instead of being overwritten by the snapshot
            # captured before analysis started.
            latest_entry = self.database.get_entry(entry_id)
            latest_data = self.database.get_entry_data(entry_id)
            latest_data["analysis"] = validated.model_dump(mode="json")
            latest_data["provider"] = f"agent:{artifacts.get('analysis_producer', 'gateway')}"
            latest_data["model"] = artifacts.get("analysis_model", "agent")
            latest_data["prompt_version"] = f"external:{PROMPT_VERSION}"
            updated = latest_entry.model_copy(
                update={
                    "title": safe_filename(validated.title),
                    "summary": validated.one_liner,
                    "tags": validated.tags,
                    "updated_at": utc_now(),
                }
            )
            chunks, relations, reminders = self.prepare_entry_bundle(updated, latest_data)
            self.persist_entry_documents_and_bundle_locked(
                updated,
                latest_data,
                chunks,
                relations,
                reminders,
                action="reanalyze-v2",
                log_summary=(f"复用现有逐字稿与 OCR，由 {latest_data['provider']} 生成 v2 分析"),
                commit_message=f"reanalyze-v2: {updated.video_id} {updated.title}",
            )
            source_kind = latest_data.get("metadata", {}).get("source_kind", SourceKind.VIDEO.value)
            creator_folder = str(latest_data.get("creator", {}).get("folder_path") or "")
            warnings = (
                [artifacts["analysis_evidence_warning"]]
                if artifacts.get("analysis_evidence_warning")
                else []
            )
            return self.database.update_job(
                job.id,
                status=JobStatus.COMPLETED_WITH_WARNINGS if warnings else JobStatus.COMPLETED,
                progress=1,
                result={
                    "entry_id": updated.id,
                    "source_path": updated.source_path,
                    "machine_data_path": str(
                        Path(creator_folder) / ".data" / "sources" / f"{updated.video_id}.md"
                        if creator_folder
                        else Path("wiki") / ".data" / "sources" / f"{updated.video_id}.md"
                    ),
                    "summary": updated.summary,
                    "reused_transcript": source_kind == SourceKind.VIDEO.value,
                    "reused_ocr": True,
                    "warnings": warnings,
                },
                unlock=True,
            )

    @staticmethod
    def detect_image_review_issues(
        observations: list[OCRObservation], post_text: str
    ) -> list[ReviewIssue]:
        normalized_post = re.sub(r"\s+", "", post_text).lower()
        critical = re.compile(
            r"(?:\d|[%￥¥$€]|(?:年|月|日|号|点|时|分)|[A-Za-z][A-Za-z0-9._+-]{1,})"
        )
        issues: list[ReviewIssue] = []
        for index, observation in enumerate(observations):
            confidence = observation.confidence
            normalized_text = re.sub(r"\s+", "", observation.text).lower()
            if (
                confidence is None
                or confidence >= 0.65
                or not critical.search(observation.text)
                or (normalized_text and normalized_text in normalized_post)
            ):
                continue
            issues.append(
                ReviewIssue(
                    id=f"image-{observation.image_index or 0}-{index}",
                    image_index=observation.image_index,
                    raw_text=observation.text,
                    reason="图片中的数字、日期或专有名词 OCR 置信度较低，且作品正文没有相同依据",
                    suggestions=[],
                )
            )
        return issues

    @staticmethod
    def apply_image_review_resolutions(
        observations: list[OCRObservation], issues: list[ReviewIssue]
    ) -> list[OCRObservation]:
        resolved = {
            (issue.image_index, issue.raw_text): issue.resolution
            for issue in issues
            if issue.resolution
        }
        return [
            observation.model_copy(
                update={
                    "text": resolved.get(
                        (observation.image_index, observation.text), observation.text
                    )
                }
            )
            for observation in observations
        ]

    def prune_unverified_analysis_evidence(
        self,
        analysis: AnalysisResult,
        context: dict[str, Any],
        *,
        audit: list[dict[str, Any]] | None = None,
    ) -> tuple[AnalysisResult, int]:
        """Keep only model citations that pass the existing source evidence checks."""
        # Compatibility claims describe the same atoms, including their repaired
        # locators. Never carry a separate pre-pruning snapshot into publication.
        analysis = AnalysisResult.model_validate(
            {**analysis.model_dump(mode="python"), "claims": []}
        )
        try:
            self.validate_analysis_evidence(analysis, context)
            return analysis, 0
        except JobStateError:
            pass

        base = analysis.model_copy(update={"chapters": [], "knowledge_atoms": [], "reminders": []})

        def error(candidate: AnalysisResult) -> str | None:
            try:
                self.validate_analysis_evidence(candidate, context)
                return None
            except JobStateError as exc:
                return str(exc)

        def record(kind: str, item: Any, reason: str, *, chapter: Any = None) -> None:
            if audit is not None:
                audit.append(
                    {
                        "kind": kind,
                        "id": getattr(item, "id", None),
                        "chapter_title": getattr(chapter, "title", None),
                        "chapter_start_ms": getattr(chapter, "start_ms", None),
                        "timestamp_ms": getattr(item, "timestamp_ms", None),
                        "quote": getattr(item, "quote", None),
                        "reason": reason,
                    }
                )

        def repair_timestamp(item: Any, provenance: str) -> Any:
            raw_quote = getattr(item, "quote", "") or ""
            quote = _normalize_evidence_text(raw_quote)
            if not quote or provenance not in {"audio", "ocr", "audio+ocr"}:
                return item
            matching: list[int] = []
            if provenance in {"audio", "audio+ocr"}:
                transcript = context.get("transcript_corrected") or context.get(
                    "transcript_raw", []
                )
                matching.extend(start for start, _ in _audio_quote_ranges(transcript, raw_quote))
            if provenance in {"ocr", "audio+ocr"}:
                for source in context.get("ocr", []):
                    timestamp = (
                        source.get("timestamp_ms")
                        if isinstance(source, dict)
                        else source.timestamp_ms
                    )
                    text = str(source.get("text", "") if isinstance(source, dict) else source.text)
                    if timestamp is not None and _evidence_quote_matches(text, raw_quote):
                        matching.append(int(timestamp))
            if not matching:
                return item
            original = getattr(item, "timestamp_ms", None)
            best = min(matching, key=lambda value: abs(value - (original or 0)))
            return item.model_copy(update={"timestamp_ms": best})

        removed = 0
        chapters = []
        for chapter in analysis.chapters:
            evidence = []
            for item in chapter.evidence:
                candidate_chapter = chapter.model_copy(update={"evidence": [item]})
                reason = error(base.model_copy(update={"chapters": [candidate_chapter]}))
                if reason and "时间戳" in reason:
                    repaired = repair_timestamp(item, item.evidence_type)
                    repaired_chapter = chapter.model_copy(update={"evidence": [repaired]})
                    if error(base.model_copy(update={"chapters": [repaired_chapter]})) is None:
                        item = repaired
                        reason = None
                if reason is None:
                    evidence.append(item)
                else:
                    removed += 1
                    record("chapter_evidence", item, reason, chapter=chapter)
            if evidence:
                chapters.append(chapter.model_copy(update={"evidence": evidence}))
            else:
                removed += 1
                record("chapter", chapter, "章节没有可核验证据", chapter=chapter)

        atoms = []
        seen_atom_ids: set[str] = set()
        for atom in analysis.knowledge_atoms:
            if atom.provenance in {"audio", "ocr", "audio+ocr"} and atom.image_index is not None:
                atom = atom.model_copy(update={"image_index": None})
            if atom.provenance == "ai_inference" and atom.atom_type != "inference":
                atom = atom.model_copy(update={"atom_type": "inference"})
            reason = error(base.model_copy(update={"knowledge_atoms": [atom]}))
            if atom.id in seen_atom_ids:
                reason = f"知识原子 id 重复：{atom.id}"
            if reason and "时间戳" in reason:
                repaired = repair_timestamp(atom, atom.provenance)
                if error(base.model_copy(update={"knowledge_atoms": [repaired]})) is None:
                    atom = repaired
                    reason = None
            if reason is None:
                atoms.append(atom)
                seen_atom_ids.add(atom.id)
            else:
                removed += 1
                record("knowledge_atom", atom, reason)

        reminders = []
        for reminder in analysis.reminders:
            reason = error(base.model_copy(update={"reminders": [reminder]}))
            if reason is None:
                reminders.append(reminder)
            else:
                removed += 1
                record("reminder", reminder, reason)

        cleaned = AnalysisResult.model_validate(
            {
                **analysis.model_dump(mode="python"),
                "chapters": chapters,
                "knowledge_atoms": atoms,
                "reminders": reminders,
                "claims": [],
            }
        )
        self.validate_analysis_evidence(cleaned, context)
        return cleaned, removed

    def validate_analysis_evidence(self, analysis: AnalysisResult, context: dict[str, Any]) -> None:
        """Reject source claims whose locator or quote cannot be traced to captured evidence."""
        metadata = context.get("metadata", {}) or {}
        source_kind = metadata.get("source_kind", SourceKind.VIDEO.value)
        is_image_note = source_kind == SourceKind.IMAGE_NOTE.value
        transcript_values = context.get("transcript_corrected") or context.get("transcript_raw", [])
        transcript_text = "\n".join(
            str(item.get("text", "") if isinstance(item, dict) else item.text)
            for item in transcript_values
        )
        ocr_values = context.get("ocr", [])
        ocr_text = "\n".join(
            str(item.get("text", "") if isinstance(item, dict) else item.text)
            for item in ocr_values
        )
        post_text = str(metadata.get("post_text") or "")
        transcript_ranges = [
            (
                int(item.get("start_ms", 0) if isinstance(item, dict) else item.start_ms),
                int(item.get("end_ms", 0) if isinstance(item, dict) else item.end_ms),
            )
            for item in transcript_values
        ]
        ocr_timestamps = {
            int(value)
            for item in ocr_values
            if (value := item.get("timestamp_ms") if isinstance(item, dict) else item.timestamp_ms)
            is not None
        }
        ocr_image_indices = {
            int(value)
            for item in ocr_values
            if (value := item.get("image_index") if isinstance(item, dict) else item.image_index)
            is not None
        }
        allowed = (
            {"post_text", "image_ocr", "post_text+image_ocr", "ai_inference"}
            if is_image_note
            else {"audio", "ocr", "audio+ocr", "ai_inference"}
        )
        image_count = len(metadata.get("image_paths") or metadata.get("images") or [])
        duration_ms = int(float(metadata.get("duration_seconds") or 0) * 1000)
        errors: list[str] = []
        atom_ids: set[str] = set()

        def validate_item(item: Any, *, label: str, atom: bool = False) -> None:
            provenance = item.provenance if atom else item.evidence_type
            timestamp_ms = getattr(item, "timestamp_ms", None)
            image_index = getattr(item, "image_index", None)
            quote = item.quote
            if provenance not in allowed:
                errors.append(f"{label} 使用了与作品类型不符的来源 {provenance}")
                return
            if atom and ((item.atom_type == "inference") != (provenance == "ai_inference")):
                errors.append(f"{label} 的 inference 类型与 ai_inference 来源不一致")
            if is_image_note:
                if timestamp_ms is not None:
                    errors.append(f"{label} 是图文证据，不能包含 timestamp_ms")
                if provenance in {"image_ocr", "post_text+image_ocr"} and image_index is None:
                    errors.append(f"{label} 缺少图片编号")
                elif provenance in {"image_ocr", "post_text+image_ocr"} and (
                    image_index not in ocr_image_indices
                ):
                    errors.append(f"{label} 的图片编号没有对应 OCR 证据")
                if image_index is not None and (image_count == 0 or image_index > image_count):
                    errors.append(f"{label} 的图片编号超出采集范围")
            else:
                if image_index is not None:
                    errors.append(f"{label} 是视频证据，不能包含 image_index")
                if provenance != "ai_inference" and timestamp_ms is None:
                    errors.append(f"{label} 缺少视频时间戳")
                elif timestamp_ms is not None and provenance != "ai_inference":
                    audio_match = any(
                        start - 1000 <= timestamp_ms <= end + 1000
                        for start, end in transcript_ranges
                    )
                    ocr_match = any(
                        abs(timestamp_ms - observed) <= 1500 for observed in ocr_timestamps
                    )
                    locator_matches = {
                        "audio": audio_match,
                        "ocr": ocr_match,
                        "audio+ocr": audio_match or ocr_match,
                    }
                    if not locator_matches.get(provenance, False):
                        errors.append(f"{label} 的时间戳没有对应 ASR/OCR 证据")
                if duration_ms and timestamp_ms is not None and timestamp_ms > duration_ms + 5000:
                    errors.append(f"{label} 的时间戳超出视频时长")
            if quote and provenance != "ai_inference":
                local_audio = "\n".join(
                    str(source.get("text", "") if isinstance(source, dict) else source.text)
                    for source, (start, end) in zip(
                        transcript_values, transcript_ranges, strict=True
                    )
                    if timestamp_ms is not None and start - 1000 <= timestamp_ms <= end + 1000
                )
                local_ocr = "\n".join(
                    str(source.get("text", "") if isinstance(source, dict) else source.text)
                    for source in ocr_values
                    if (
                        is_image_note
                        and (
                            source.get("image_index")
                            if isinstance(source, dict)
                            else source.image_index
                        )
                        == image_index
                    )
                    or (
                        not is_image_note
                        and timestamp_ms is not None
                        and (
                            observed := source.get("timestamp_ms")
                            if isinstance(source, dict)
                            else source.timestamp_ms
                        )
                        is not None
                        and abs(timestamp_ms - int(observed)) <= 1500
                    )
                )
                located_text = {
                    "audio": local_audio,
                    "ocr": local_ocr,
                    "audio+ocr": f"{local_audio}\n{local_ocr}",
                    "post_text": post_text,
                    "image_ocr": local_ocr,
                    "post_text+image_ocr": f"{post_text}\n{local_ocr}",
                }
                quote_matches = _evidence_quote_matches(located_text.get(provenance, ""), quote)
                if provenance in {"audio", "audio+ocr"}:
                    audio_quote_matches = timestamp_ms is not None and any(
                        start - 1000 <= timestamp_ms <= end + 1000
                        for start, end in _audio_quote_ranges(transcript_values, quote)
                    )
                    quote_matches = (
                        audio_quote_matches
                        if provenance == "audio"
                        else quote_matches or audio_quote_matches
                    )
                if not quote_matches:
                    errors.append(f"{label} 的引文无法在指定时间戳/图片的 ASR/OCR/正文中定位")

        if is_image_note and analysis.chapters:
            errors.append("静态图文不能包含视频时间轴图解")
        for index, chapter in enumerate(analysis.chapters, start=1):
            if duration_ms and chapter.start_ms > duration_ms + 5000:
                errors.append(f"时间轴章节 {index} 的开始时间超出视频时长")
            if chapter.end_ms is not None and duration_ms and chapter.end_ms > duration_ms + 5000:
                errors.append(f"时间轴章节 {index} 的结束时间超出视频时长")
            if not chapter.evidence:
                errors.append(f"时间轴章节 {index} 缺少可核验证据")
            for evidence_index, evidence in enumerate(chapter.evidence, start=1):
                validate_item(
                    evidence,
                    label=f"时间轴章节 {index} 证据 {evidence_index}",
                )
        for index, atom in enumerate(analysis.knowledge_atoms, start=1):
            if atom.id in atom_ids:
                errors.append(f"知识原子 id 重复：{atom.id}")
            atom_ids.add(atom.id)
            validate_item(atom, label=f"知识原子 {atom.id or index}", atom=True)
        corpus = f"{transcript_text}\n{ocr_text}\n{post_text}"
        for reminder in analysis.reminders:
            if reminder.source_quote and not _evidence_quote_matches(corpus, reminder.source_quote):
                errors.append(f"提醒候选 {reminder.id} 的依据无法在原始证据中定位")
        if errors:
            raise JobStateError("分析证据校验失败：" + "；".join(errors[:8]))
        for reminder in analysis.reminders:
            apply_timing(reminder, audio_timing(transcript_values, None, reminder.source_quote))
        for atom in analysis.knowledge_atoms:
            apply_timing(
                atom,
                audio_timing(
                    transcript_values if atom.provenance in {"audio", "audio+ocr"} else [],
                    atom.timestamp_ms,
                    atom.quote,
                ),
            )
        for chapter in analysis.chapters:
            for evidence in chapter.evidence:
                apply_timing(
                    evidence,
                    audio_timing(
                        transcript_values
                        if evidence.evidence_type in {"audio", "audio+ocr"}
                        else [],
                        evidence.timestamp_ms,
                        evidence.quote,
                    ),
                )
            coarse = [item for item in chapter.evidence if item.timestamp_precision == "window"]
            info = audio_timing([], None)
            if coarse:
                # The coarse label describes the whole chapter, including its
                # declared extent, precise evidence and any wider source window.
                bounds = [
                    value
                    for value in (
                        chapter.start_ms,
                        chapter.end_ms,
                        *(item.timestamp_ms for item in chapter.evidence),
                        *(item.timestamp_start_ms for item in coarse),
                        *(item.timestamp_end_ms for item in coarse),
                    )
                    if value is not None
                ]
                info.update(
                    timestamp_precision="window",
                    timestamp_start_ms=min(bounds),
                    timestamp_end_ms=max(bounds),
                    alignment_reason=",".join(sorted({item.alignment_reason for item in coarse})),
                )
            apply_timing(chapter, info)
        analysis.synchronize_claim_timing()


def _normalize_evidence_text(value: str) -> str:
    """Use context-independent Unicode casing before removing separators."""
    return re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)


def _evidence_quote_positions(source: str, quote: str) -> Iterator[int]:
    """Find quotes that consume complete original characters after normalization."""
    normalized_quote = _normalize_evidence_text(quote)
    if not normalized_quote:
        return
    normalized_source = _normalize_evidence_text(source)
    position = normalized_source.find(normalized_quote)
    if position < 0:
        return
    interior_offsets: set[int] = set()
    offset = 0
    for character in source:
        folded = character.casefold()
        if len(folded) == 1:
            # The normalizer retains Unicode alphanumerics and removes underscores.
            offset += folded.isalnum()
        else:
            length = len(_normalize_evidence_text(character))
            interior_offsets.update(range(offset + 1, offset + length))
            offset += length
    while position >= 0:
        if (
            position not in interior_offsets
            and position + len(normalized_quote) not in interior_offsets
        ):
            yield position
        position = normalized_source.find(normalized_quote, position + 1)


def _evidence_quote_matches(source: str, quote: str) -> bool:
    return next(_evidence_quote_positions(source, quote), None) is not None


def _audio_quote_ranges(transcript: list[Any], quote: str) -> list[tuple[int, int]]:
    """Locate contiguous quotes and return the ranges where each quote starts."""
    texts = [
        str(item.get("text", "") if isinstance(item, dict) else item.text) for item in transcript
    ]
    ends = []
    offset = 0
    for text in texts:
        offset += len(_normalize_evidence_text(text))
        ends.append(offset)
    ranges = []
    seen = set()
    for position in _evidence_quote_positions("".join(texts), quote):
        # bisect_right skips empty segments and assigns a boundary to the next one.
        item = transcript[bisect_right(ends, position)]
        start = int(item.get("start_ms", 0) if isinstance(item, dict) else item.start_ms)
        end = int(item.get("end_ms", 0) if isinstance(item, dict) else item.end_ms)
        if (start, end) not in seen:
            ranges.append((start, end))
            seen.add((start, end))
    return ranges
