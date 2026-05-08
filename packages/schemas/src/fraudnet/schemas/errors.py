"""Standard error envelope and typed exception hierarchy.

Every service exposes errors via the same envelope (CLAUDE.md §10.3). Internal
exceptions are typed; bare `Exception` is never raised by FraudNet code.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    """Machine-readable error codes.

    The set is open — services may add codes — but new codes must be defined
    here so OpenAPI consumers see them. Codes are namespaced by domain:
      <domain>.<reason>      e.g.   alerts.not_found, auth.token_expired
    """

    # Auth / authorisation
    AUTH_REQUIRED = "auth.required"
    AUTH_TOKEN_EXPIRED = "auth.token_expired"
    AUTH_INVALID_TOKEN = "auth.invalid_token"
    AUTH_FORBIDDEN = "auth.forbidden"
    AUTH_STEP_UP_REQUIRED = "auth.step_up_required"

    # Purpose limitation
    PURPOSE_MISSING = "purpose.missing"
    PURPOSE_INVALID = "purpose.invalid"

    # Resources
    NOT_FOUND = "resource.not_found"
    CONFLICT = "resource.conflict"
    GONE = "resource.gone"

    # Validation
    VALIDATION_FAILED = "validation.failed"

    # Domain
    ALERT_NOT_FOUND = "alerts.not_found"
    ALERT_ALREADY_CLAIMED = "alerts.already_claimed"
    RING_NOT_FOUND = "rings.not_found"
    TAKEDOWN_INVALID_TRANSITION = "takedowns.invalid_transition"
    MOMO_DUPLICATE = "momo.duplicate_event"

    # Infra / upstream
    UPSTREAM_UNAVAILABLE = "upstream.unavailable"
    RATE_LIMITED = "rate_limited"
    INTERNAL = "internal"


class ErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    """Wire format for any non-2xx response."""

    model_config = ConfigDict(frozen=True)

    error: ErrorBody
    request_id: str | None = None


# ---------------------------------------------------------------------------
# Typed exception hierarchy. Service code raises these; the gateway / handler
# layer maps them to ErrorEnvelope responses with the appropriate HTTP code.
# ---------------------------------------------------------------------------


class FraudNetError(Exception):
    """Base for all FraudNet-raised exceptions. Never raise bare Exception."""

    code: ErrorCode = ErrorCode.INTERNAL
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        code: ErrorCode | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code is not None:
            self.code = code

    def to_envelope(self, request_id: str | None = None) -> ErrorEnvelope:
        return ErrorEnvelope(
            error=ErrorBody(code=self.code, message=self.message, details=self.details),
            request_id=request_id,
        )


class ValidationError(FraudNetError):
    code = ErrorCode.VALIDATION_FAILED
    http_status = 400


class AuthError(FraudNetError):
    code = ErrorCode.AUTH_REQUIRED
    http_status = 401


class ForbiddenError(AuthError):
    code = ErrorCode.AUTH_FORBIDDEN
    http_status = 403


class StepUpRequiredError(AuthError):
    code = ErrorCode.AUTH_STEP_UP_REQUIRED
    http_status = 401


class PurposeMissingError(FraudNetError):
    code = ErrorCode.PURPOSE_MISSING
    http_status = 403


class NotFoundError(FraudNetError):
    code = ErrorCode.NOT_FOUND
    http_status = 404


class ConflictError(FraudNetError):
    code = ErrorCode.CONFLICT
    http_status = 409


class RateLimitedError(FraudNetError):
    code = ErrorCode.RATE_LIMITED
    http_status = 429


class ServiceError(FraudNetError):
    code = ErrorCode.INTERNAL
    http_status = 500


class UpstreamUnavailableError(ServiceError):
    code = ErrorCode.UPSTREAM_UNAVAILABLE
    http_status = 503
