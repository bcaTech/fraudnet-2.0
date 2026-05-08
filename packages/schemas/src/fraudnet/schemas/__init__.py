"""FraudNet 2.0 canonical schemas.

Single source of truth for cross-service types. Every type that crosses a
service boundary — Kafka payload, REST body, gRPC message, database row —
is defined here. Service code imports from this package; nothing imports from
service code into this package.

Conventions:
  - All public types are Pydantic v2 models with strict validation.
  - All datetimes are timezone-aware UTC. Display layer formats locally.
  - All IDs are UUIDv7 (time-ordered) where the type is FraudNet-internal.
  - All MSISDNs are E.164.
  - Versioning by suffix (`*V1`); breaking changes get a new class.
"""

from fraudnet.schemas.audit import AuditEventV1
from fraudnet.schemas.errors import (
    AuthError,
    ConflictError,
    ErrorCode,
    ErrorEnvelope,
    FraudNetError,
    NotFoundError,
    ServiceError,
    ValidationError,
)
from fraudnet.schemas.events import (
    DataEventV1,
    DecisionDispatchedV1,
    GraphMutationV1,
    IntelEventV1,
    MoMoEventV1,
    MoMoEventType,
    MotifDetectedV1,
    SmsEventV1,
    VoiceEventV1,
)
from fraudnet.schemas.types import (
    EntityKind,
    LatencyTier,
    MSISDN,
    Purpose,
    RingType,
    RiskScore,
    Severity,
    Subject,
    Tenancy,
)

__all__ = [
    # audit
    "AuditEventV1",
    # types
    "EntityKind",
    "LatencyTier",
    "MSISDN",
    "Purpose",
    "RingType",
    "RiskScore",
    "Severity",
    "Subject",
    "Tenancy",
    # events
    "DataEventV1",
    "DecisionDispatchedV1",
    "GraphMutationV1",
    "IntelEventV1",
    "MoMoEventV1",
    "MoMoEventType",
    "MotifDetectedV1",
    "SmsEventV1",
    "VoiceEventV1",
    # errors
    "AuthError",
    "ConflictError",
    "ErrorCode",
    "ErrorEnvelope",
    "FraudNetError",
    "NotFoundError",
    "ServiceError",
    "ValidationError",
]
