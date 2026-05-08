"""Telco domain primitive types.

Wraps raw values in semantic types so that an MSISDN can never be confused with
a wallet ID and an IMEI can never be confused with an IMSI. Validation runs at
construction time; downstream code can trust the values.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Final, Self

import phonenumbers
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, core_schema

# ---------------------------------------------------------------------------
# Identity types
# ---------------------------------------------------------------------------


class MSISDN(str):
    """Phone number in E.164 format. Enforced at parse time.

    FraudNet 2.0 is Ghana-first; default region is GH. Federated peer telco
    submissions arrive already in E.164 from the intel adapter; the type
    accepts either form on the way in and emits the canonical form on output.
    """

    DEFAULT_REGION: Final[str] = "GH"
    _CANONICAL: Final[re.Pattern[str]] = re.compile(r"^\+\d{8,15}$")

    def __new__(cls, value: str | Self) -> Self:  # noqa: D102
        if isinstance(value, cls):
            return value
        canonical = cls._canonicalise(value)
        return super().__new__(cls, canonical)

    @classmethod
    def _canonicalise(cls, raw: str) -> str:
        try:
            parsed = phonenumbers.parse(raw, cls.DEFAULT_REGION)
        except phonenumbers.NumberParseException as exc:
            raise ValueError(f"invalid MSISDN: {exc.error_type}") from exc
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError("invalid MSISDN")
        canonical = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        if not cls._CANONICAL.match(canonical):
            raise ValueError(f"non-canonical MSISDN: {canonical}")
        return canonical

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: object, _handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(min_length=4, max_length=20),
            serialization=core_schema.plain_serializer_function_ser_schema(str),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        out = handler(schema)
        out.update(
            type="string",
            pattern=r"^\+\d{8,15}$",
            example="+233241234567",
            description="E.164 phone number",
        )
        return out


# Wallet, IMEI, IMSI, account hash — opaque tokens. We keep them as plain str
# subtypes (not validated) for now; tightening happens when the source-of-truth
# validation rules are known per integration.
WalletId = Annotated[str, Field(min_length=4, max_length=64)]
IMEI = Annotated[str, Field(min_length=14, max_length=17, pattern=r"^\d{14,17}$")]
IMSI = Annotated[str, Field(min_length=14, max_length=15, pattern=r"^\d{14,15}$")]
AccountHash = Annotated[str, Field(min_length=8, max_length=128)]


# ---------------------------------------------------------------------------
# Domain enumerations
# ---------------------------------------------------------------------------


class EntityKind(StrEnum):
    """A kind of entity that can be a subject of an alert, score, or action."""

    NUMBER = "number"
    WALLET = "wallet"
    DEVICE = "device"
    ACCOUNT = "account"
    URL = "url"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class LatencyTier(StrEnum):
    """Decision dispatch tiers (CLAUDE.md §5.4)."""

    TIER1_INLINE = "tier1"  # <200ms; VoLTE tag, URL block, MoMo friction
    TIER2_NRT = "tier2"  # seconds-to-minutes; customer alerts, prompts, SOC tickets
    TIER3_INVESTIGATION = "tier3"  # NOC investigation queue, takedown workflows


class Purpose(StrEnum):
    """Purpose-limitation claim. Required on every PII-bearing query.

    Enforced both in code (audit-lib) and infrastructure (Postgres RLS via
    `current_setting('fraudnet.purpose')`). New purposes require DPO sign-off.
    """

    FRAUD_PREVENTION = "fraud_prevention"
    REGULATORY_EXPORT = "regulatory_export"
    AUDIT = "audit"
    INCIDENT_RESPONSE = "incident_response"


class RingType(StrEnum):
    VOICE_SCAM = "voice_scam"
    SMISHING = "smishing"
    MULE = "mule"
    MIXED = "mixed"


# ---------------------------------------------------------------------------
# Composite domain objects
# ---------------------------------------------------------------------------


class Subject(BaseModel):
    """A pointer to an entity that is the subject of an alert / score / action."""

    model_config = ConfigDict(frozen=True)

    kind: EntityKind
    id: str  # opaque to us; meaning depends on kind


class Tenancy(BaseModel):
    """Tenant scoping carried alongside any tenant-aware object.

    Phase 1: only the `mtn-ghana` internal tenant exists. Phase 4 introduces
    multiple B2B tenants and the API enforces tenant_id at the data layer
    (Postgres RLS) per CLAUDE.md §5.5.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(default="mtn-ghana", min_length=1)


class RiskScore(BaseModel):
    """A model output. Score range is `[0.0, 1.0]`; higher means riskier."""

    model_config = ConfigDict(frozen=True)

    value: float = Field(ge=0.0, le=1.0)
    model_id: str
    model_version: str
    computed_at_ms: int = Field(ge=0)
    feature_attribution: dict[str, float] | None = None
