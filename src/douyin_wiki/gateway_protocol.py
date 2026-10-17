"""Opt-in Gateway v2 transport, independent of the persistent analysis format."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    create_model,
)

from .analysis_rules import CONTENT_TAG_RULES
from .errors import InvalidAnalysisError, JobStateError
from .gateway_sources import (
    OCR_DEDUP_WINDOW_MS,
    SOURCE_PROJECTION_VERSION,
    SOURCE_UNIT_MAX_CHARS,
    SUBTITLE_MIN_LENGTH,
    SUBTITLE_WINDOW_MS,
    SourceCatalog,
)
from .models import (
    AnalysisResult,
    ChapterEvidence,
    JobRecord,
    JobStatus,
    KnowledgeAtom,
    ReminderCandidate,
    TimelineChapter,
    TranscriptCorrection,
)


def validate_gateway_protocol(value: Any) -> Literal[1, 2]:
    if type(value) is not int or value not in (1, 2):
        raise ValueError("gateway_protocol 只接受整数 1 或 2")
    return value


GatewayProtocol = Annotated[Literal[1, 2], BeforeValidator(validate_gateway_protocol)]
SourceRefs = Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)]
LEGACY_ANALYSIS_FIELDS = frozenset(
    {
        "summary",
        "core_points",
        "evidence",
        "steps",
        "applicable_scenarios",
        "risks",
        "claims",
        "ai_judgment",
    }
)
CORRECTION_RULES = [
    "timestamp_precision=window 表示整段只有粗略时间范围；不得自行细分或编造句子时间。"
    "保留配乐歌词原稿；不能仅凭语言不同删句，也不能将歌词自动视为作者发言。",
    "完整检查原稿，根据原话上下文和对应 OCR 校正识别错误、同音字和断句；"
    "不得摘要、删句、扩写或补充原视频没有的内容。数字、金额、人名缺少依据时保留原表述。",
    "只提交有改动片段的完整替换文本；corrections=[] 表示已检查且无需修改。"
    "未提交片段由服务端原样保留；提交必须携带当前 context_revision。",
    "用当前模型直接校正，不请求人工校对，不增加独立复核调用。",
    "灵感必须逐字保留，不得改写或补造。",
]
ANALYSIS_RULES = [
    "区分口播、旁白与配乐歌词；无法确认说话者时说明来源不明，不把歌词当成作者立场。"
    "时间精度为 window 的来源只支持整个窗口定位，分析必须说明范围，不声称精确句子时间。",
    "来源单元就是完整分析材料；区分原话、正文、OCR、AI 推断和用户灵感。"
    "existing_knowledge 仅供关联与矛盾判断，不能充当本作品证据。灵感逐字保留。",
    "事实、数字、日期、参数和方法写入 knowledge_atoms；非推断原子必须有 source_refs，"
    "或非空显式 quote 与对应定位。source_refs 按原文顺序引用同类、同容器、连续原文；"
    "不要跨图片或混合来源。混合来源继续使用显式引文。",
    "source_refs 由服务端还原原文与定位；若同时填写 quote/来源/定位，必须与还原结果一致。"
    "编号只在当前 job_id/context_revision 内有效；提交必须携带当前版本。",
    "推断使用 atom_type=inference 与 provenance=ai_inference，不使用来源编号。"
    "来源可定位不代表支持该论断，必须根据原材料判断。",
    "content_card.kind 必须与 content_type 相同；提醒时间不明确时标记需澄清。",
]


def _fields(model: type[BaseModel]) -> dict[str, tuple[Any, Any]]:
    return {name: (field.annotation, deepcopy(field)) for name, field in model.model_fields.items()}


_evidence_fields = _fields(ChapterEvidence)
# Default factories keep standard source defaults out of the wire schema. Validation
# defaults are never serialized back into the raw input used for reference expansion.
_evidence_fields.update(
    {
        "timestamp_ms": (int | None, Field(default=None, ge=0)),
        "quote": (str | None, Field(default=None, min_length=1)),
        "evidence_type": (
            ChapterEvidence.model_fields["evidence_type"].annotation,
            Field(default_factory=lambda: ChapterEvidence.model_fields["evidence_type"].default),
        ),
        "source_refs": (SourceRefs, Field(default_factory=list)),
    }
)
RefChapterEvidence = create_model("RefChapterEvidence", **_evidence_fields)
RefTimelineChapter = create_model(
    "RefTimelineChapter",
    __base__=TimelineChapter,
    evidence=(list[RefChapterEvidence], Field(default_factory=list, max_length=6)),
)
RefKnowledgeAtom = create_model(
    "RefKnowledgeAtom",
    __base__=KnowledgeAtom,
    provenance=(
        KnowledgeAtom.model_fields["provenance"].annotation,
        Field(default_factory=lambda: KnowledgeAtom.model_fields["provenance"].default),
    ),
    source_refs=(SourceRefs, Field(default_factory=list)),
)
RefReminderCandidate = create_model(
    "RefReminderCandidate",
    __base__=ReminderCandidate,
    source_quote=(str | None, None),
    source_refs=(SourceRefs, Field(default_factory=list)),
)
_analysis_fields = {
    name: value
    for name, value in _fields(AnalysisResult).items()
    if name not in LEGACY_ANALYSIS_FIELDS
}
_analysis_fields.update(
    {
        "chapters": (list[RefTimelineChapter], deepcopy(AnalysisResult.model_fields["chapters"])),
        "knowledge_atoms": (
            list[RefKnowledgeAtom],
            deepcopy(AnalysisResult.model_fields["knowledge_atoms"]),
        ),
        "reminders": (
            list[RefReminderCandidate],
            deepcopy(AnalysisResult.model_fields["reminders"]),
        ),
    }
)
CompactAnalysis = create_model(
    "CompactAnalysis", __config__=ConfigDict(extra="forbid"), **_analysis_fields
)


def _compact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    def strip_titles(node: Any) -> Any:
        if isinstance(node, list):
            return [strip_titles(item) for item in node]
        if not isinstance(node, dict):
            return node
        return {
            key: (
                {name: strip_titles(value) for name, value in child.items()}
                if key == "properties"
                else strip_titles(child)
            )
            for key, child in node.items()
            if key != "title"
        }

    result = strip_titles(schema)
    definitions = result.pop("$defs", {})
    reachable: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                collect(child)
        elif isinstance(node, dict):
            ref = node.get("$ref", "")
            if ref.startswith("#/$defs/"):
                name = ref.removeprefix("#/$defs/")
                if name not in reachable:
                    reachable.add(name)
                    collect(definitions[name])
            for child in node.values():
                collect(child)

    collect(result)
    if reachable:
        result["$defs"] = {name: value for name, value in definitions.items() if name in reachable}
    return result


def compact_correction_schema() -> dict[str, Any]:
    return _compact_schema({"type": "array", "items": TranscriptCorrection.model_json_schema()})


def compact_analysis_schema() -> dict[str, Any]:
    schema = CompactAnalysis.model_json_schema()
    definitions = schema["$defs"]
    nonnull_refs = {
        "required": ["source_refs"],
        "properties": {"source_refs": {"type": "array", "minItems": 1}},
    }
    definitions["RefChapterEvidence"]["anyOf"] = [
        nonnull_refs,
        {
            "required": ["quote", "timestamp_ms"],
            "properties": {
                "quote": {"type": "string", "minLength": 1},
                "timestamp_ms": {"type": "integer"},
            },
        },
    ]
    definitions["RefKnowledgeAtom"]["anyOf"] = [
        nonnull_refs,
        {
            "required": ["quote"],
            "properties": {"quote": {"type": "string", "minLength": 1}},
            "anyOf": [
                {"required": ["timestamp_ms"], "properties": {"timestamp_ms": {"type": "integer"}}},
                {"required": ["image_index"], "properties": {"image_index": {"type": "integer"}}},
                {"required": ["provenance"], "properties": {"provenance": {"const": "post_text"}}},
            ],
        },
        {
            "required": ["atom_type", "provenance"],
            "properties": {
                "atom_type": {"const": "inference"},
                "provenance": {"const": "ai_inference"},
            },
            "not": {"required": ["source_refs"]},
        },
    ]
    definitions["RefReminderCandidate"]["anyOf"] = [
        nonnull_refs,
        {
            "required": ["source_quote"],
            "properties": {"source_quote": {"type": "string", "minLength": 1}},
        },
    ]
    definitions["RefKnowledgeAtom"]["allOf"] = [
        {
            "if": {
                "anyOf": [
                    {
                        "required": ["atom_type"],
                        "properties": {"atom_type": {"const": "inference"}},
                    },
                    {
                        "required": ["provenance"],
                        "properties": {"provenance": {"const": "ai_inference"}},
                    },
                ]
            },
            "then": {
                "required": ["atom_type", "provenance"],
                "properties": {
                    "atom_type": {"const": "inference"},
                    "provenance": {"const": "ai_inference"},
                },
                "not": {"required": ["source_refs"]},
            },
        }
    ]
    return _compact_schema(schema)


def validate_compact_analysis_shape(analysis: dict[str, Any]) -> None:
    # Validate without serializing defaults back into the caller's payload.
    try:
        CompactAnalysis.model_validate(analysis)
    except ValidationError as error:
        issues = error.errors(include_input=False, include_context=False, include_url=False)
        ref_issues = [issue for issue in issues if "source_refs" in issue["loc"]]
        if ref_issues:
            raise JobStateError(
                "来源编号列表格式无效",
                details={
                    "reason": "invalid_source_ref",
                    "errors": ref_issues[:8],
                },
            ) from error
        raise InvalidAnalysisError.from_validation(error) from error

    def fail(path: str, message: str) -> None:
        raise JobStateError(message, details={"reason": "invalid_source_ref", "path": path})

    for index, atom in enumerate(analysis.get("knowledge_atoms", [])):
        path = f"knowledge_atoms[{index}]"
        inference = atom.get("atom_type") == "inference" or atom.get("provenance") == "ai_inference"
        if inference:
            if "source_refs" in atom:
                fail(path, "AI 推断不能使用来源编号")
        elif not atom.get("source_refs") and not (
            str(atom.get("quote") or "").strip()
            and (
                atom.get("timestamp_ms") is not None
                or atom.get("image_index") is not None
                or atom.get("provenance") == "post_text"
            )
        ):
            fail(path, "非推断知识原子必须提供来源编号或带定位的非空引文")
    for index, chapter in enumerate(analysis.get("chapters", [])):
        for evidence_index, evidence in enumerate(chapter.get("evidence", [])):
            if not evidence.get("source_refs") and not (
                str(evidence.get("quote") or "").strip()
                and evidence.get("timestamp_ms") is not None
            ):
                fail(f"chapters[{index}].evidence[{evidence_index}]", "章节证据缺少引文与定位")
    for index, reminder in enumerate(analysis.get("reminders", [])):
        if not reminder.get("source_refs") and not str(reminder.get("source_quote") or "").strip():
            fail(f"reminders[{index}]", "提醒必须提供来源编号或非空 source_quote")


def gateway_phase(job: JobRecord) -> str:
    if "phase" in job.result:
        return str(job.result["phase"])
    if (
        job.artifacts.get("metadata", {}).get("source_kind") == "image_note"
        or "transcript_corrected" in job.artifacts
    ):
        return "analysis"
    return "transcript_correction"


def _rules(phase: str, *, is_image_note: bool) -> list[str]:
    if phase == "transcript_correction":
        return list(CORRECTION_RULES)
    return [
        *CONTENT_TAG_RULES,
        *ANALYSIS_RULES,
        *(
            ["静态图文 chapters 必须为空；用真实 image_index 定位，不能伪造视频时间戳。"]
            if is_image_note
            else [
                "视频按内容展开顺序生成 chapters，每章有原话或画面证据；"
                "正文只供理解与提醒，不能作为视频章节或知识原子证据。"
            ]
        ),
    ]


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def context_revision(job: JobRecord) -> str:
    serialized = job.model_dump(mode="json")
    artifacts = serialized["artifacts"]
    phase = gateway_phase(job)
    rule_version = _digest(
        {
            "tag_rules": CONTENT_TAG_RULES,
            "correction": _rules("transcript_correction", is_image_note=False),
            "video": _rules("analysis", is_image_note=False),
            "image": _rules("analysis", is_image_note=True),
            "correction_schema": compact_correction_schema(),
            "analysis_schema": compact_analysis_schema(),
            "source_rules": [
                SOURCE_PROJECTION_VERSION,
                SUBTITLE_MIN_LENGTH,
                SUBTITLE_WINDOW_MS,
                OCR_DEDUP_WINDOW_MS,
                SOURCE_UNIT_MAX_CHARS,
            ],
        }
    )
    return _digest(
        {
            "gateway_protocol": 2,
            "phase": phase,
            "job_id": job.id,
            "updated_at": serialized["updated_at"],
            "transcript_raw": artifacts.get("transcript_raw"),
            "transcript_corrected": artifacts.get("transcript_corrected"),
            "ocr": artifacts.get("ocr"),
            "metadata": artifacts.get("metadata"),
            "media_provenance": artifacts.get("media_provenance"),
            "inspirations": serialized["request"]["inspirations"],
            "rules": rule_version,
        }
    )


def require_context_revision(job: JobRecord, revision: str | None) -> None:
    current = context_revision(job)
    if not revision or not revision.strip():
        raise JobStateError(
            "紧凑提交必须携带 context_revision",
            details={
                "reason": "context_revision_required",
                "phase": gateway_phase(job),
                "context_revision": current,
            },
        )
    if revision != current:
        raise JobStateError(
            "材料版本已变化，请读取当前阶段上下文",
            details={
                "reason": "context_revision_mismatch",
                "phase": gateway_phase(job),
                "context_revision": current,
            },
        )


def _batch_contexts(job: JobRecord) -> dict[str, Any]:
    allowed = {
        "id",
        "parent_job_id",
        "batch_silent",
        "collection_id",
        "collection_name",
        "creator_id",
        "creator_name",
        "nickname",
        "folder_path",
        "work_id",
        "ordinal",
        "source",
    }
    return {
        name: {key: value for key, value in job.artifacts[name].items() if key in allowed}
        for name in ("favorites_context", "creator_context", "collection_context")
        if job.artifacts.get(name)
    }


def build_compact_context(
    job: JobRecord, *, catalog: SourceCatalog, related_claims: list[dict[str, Any]]
) -> dict[str, Any]:
    metadata = job.artifacts.get("metadata", {}) or {}
    phase = gateway_phase(job)
    if phase not in {"transcript_correction", "analysis"}:
        raise JobStateError("当前阶段不提供紧凑分析上下文", details={"phase": phase})
    is_image_note = metadata.get("source_kind") == "image_note"
    keys = {
        "video_id",
        "source_kind",
        "title",
        "author",
        "published_at",
        "duration_seconds",
        "original_url",
        "canonical_url",
    }
    selected_metadata = {key: value for key, value in metadata.items() if key in keys}
    if is_image_note:
        selected_metadata["image_count"] = len(
            metadata.get("image_paths") or metadata.get("images") or []
        )
    if metadata.get("description") and metadata["description"] != metadata.get("post_text"):
        selected_metadata["description"] = metadata["description"]
    provenance_keys = {
        "provider",
        "model",
        "backend",
        "engine",
        "language",
        "confidence_status",
        "confidence_note",
        "warning",
        "warnings",
        "fallback_reason",
        "revision",
        "vad_model",
        "vad_revision",
        "alignment_model",
        "alignment_revision",
        "segmentation",
        "timestamp_kind",
        "recognition_window_seconds",
        "alignment_target_seconds",
        "alignment_window_limit_seconds",
        "boundary_limit_count",
        "boundary_limit_max_delta_ms",
        "itn",
        "degraded_windows",
    }
    provenance = {
        name: {key: value for key, value in info.items() if key in provenance_keys}
        for name, info in (job.artifacts.get("media_provenance") or {}).items()
        if isinstance(info, dict)
    }
    for warning in ("ocr_warning", "asr_warning", "transcript_skipped"):
        if warning in job.artifacts:
            provenance[warning] = job.artifacts[warning]
    result = {
        "gateway_protocol": 2,
        "job_id": job.id,
        "status": job.status.value,
        "phase": phase,
        "context_revision": context_revision(job),
        "gateway_context": job.request.gateway_context.model_dump(mode="json")
        if job.request.gateway_context
        else None,
        "metadata": selected_metadata,
        "media_provenance": provenance,
        "inspirations_verbatim": [
            item.model_dump(mode="json") for item in job.request.inspirations
        ],
        "rules": _rules(phase, is_image_note=is_image_note),
        **_batch_contexts(job),
    }
    if phase == "transcript_correction":
        result.update(
            {
                "transcript": [
                    {key: segment[key] for key in ("id", "start_ms", "end_ms", "text")}
                    | (
                        {
                            key: segment.get(key)
                            for key in (
                                "timestamp_precision",
                                "timestamp_start_ms",
                                "timestamp_end_ms",
                                "alignment_reason",
                            )
                        }
                        if segment.get("timestamp_precision") == "window"
                        else {}
                    )
                    for segment in job.artifacts.get("transcript_raw", [])
                ],
                "ocr": catalog.ocr,
                "post_text": metadata.get("post_text"),
                "correction_schema": compact_correction_schema(),
            }
        )
    else:
        prior_keys = {"entry_id", "claim_id", "claim_text", "entry_title"}
        result.update(
            {
                "sources": catalog.sources,
                "analysis_schema": compact_analysis_schema(),
                "existing_knowledge": [
                    {key: value for key, value in row.items() if key in prior_keys}
                    for row in related_claims[:12]
                ],
            }
        )
    return result


def compact_job_payload(job: JobRecord) -> dict[str, Any]:
    phase = gateway_phase(job)
    readable = job.status in {
        JobStatus.AWAITING_AGENT_ANALYSIS,
        JobStatus.NEEDS_REVIEW,
    } and phase in {"transcript_correction", "analysis"}
    terminal = job.status in {
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_WARNINGS,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
    allowed = {
        "entry_id",
        "title",
        "summary",
        "original_url",
        "source_path",
        "warnings",
        "auth_scope",
        "reason",
        "duration_minutes",
        "shared_capture_job_id",
        "shared_job_status",
    }
    result = {key: value for key, value in job.result.items() if key in allowed}
    metadata = job.artifacts.get("metadata", {}) or {}
    for key in ("title", "original_url"):
        if key not in result and metadata.get(key):
            result[key] = metadata[key]
    if terminal and "summary" not in result:
        summary = (job.artifacts.get("analysis") or {}).get("one_liner")
        if summary:
            result["summary"] = summary
    return {
        "gateway_protocol": 2,
        "job_id": job.id,
        "status": job.status.value,
        "phase": phase,
        "progress": job.progress,
        "updated_at": job.updated_at.isoformat(),
        "context_revision": context_revision(job) if readable else None,
        "next_tool": "get_analysis_context"
        if readable
        else None
        if terminal
        else "list_job_events",
        "gateway_context": job.request.gateway_context.model_dump(mode="json")
        if job.request.gateway_context
        else None,
        "result": result,
        "error_code": job.error_code,
        "error_message": job.error_message,
        **_batch_contexts(job),
    }
