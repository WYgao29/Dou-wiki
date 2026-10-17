from __future__ import annotations


class DouyinWikiError(Exception):
    """Base error carrying a stable machine-readable code."""

    code = "douyin_wiki_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class InvalidShareTextError(DouyinWikiError):
    code = "invalid_share_text"


class FeatureDisabledError(DouyinWikiError):
    code = "feature_disabled"


class CookieRequiredError(DouyinWikiError):
    code = "cookie_required"


class VideoUnavailableError(DouyinWikiError):
    code = "video_unavailable"


class VideoTooLongError(DouyinWikiError):
    code = "video_too_long"


class RegionRestrictedError(DouyinWikiError):
    code = "region_restricted"


class BrowserAuthRequiredError(DouyinWikiError):
    code = "browser_auth_required"


class LivePhotoUnsupportedError(DouyinWikiError):
    code = "live_photo_unsupported"


class ExternalToolError(DouyinWikiError):
    code = "external_tool_error"


class ModelServiceError(ExternalToolError):
    code = "model_unavailable"


class ModelConnectionError(ModelServiceError):
    code = "model_connection_error"


class ModelTimeoutError(ModelServiceError):
    code = "model_timeout"


class ModelConfigurationError(DouyinWikiError):
    code = "model_not_configured"


class JobStateError(DouyinWikiError):
    code = "invalid_job_state"


class InvalidAnalysisError(DouyinWikiError, ValueError):
    code = "invalid_analysis"

    @classmethod
    def from_validation(cls, error):
        issues = []
        for issue in error.errors(include_input=False, include_url=False)[:8]:
            detail = {
                "path": ".".join(str(part) for part in issue["loc"]) or "$",
                "type": issue["type"],
                "message": issue["msg"],
            }
            detail.update(
                {
                    key: value
                    for key, value in issue.get("ctx", {}).items()
                    if key in {"max_length", "min_length", "actual_length", "ge", "le"}
                    and isinstance(value, (int, float))
                }
            )
            issues.append(detail)
        return cls("分析参数无效，请按字段限制修正后重新提交", details={"errors": issues})


class JobCancelledError(JobStateError):
    """The worker reached a safe persistence checkpoint after cancellation."""


class JobLeaseLostError(JobStateError):
    code = "job_lease_lost"


class EntryNotFoundError(DouyinWikiError):
    code = "entry_not_found"
