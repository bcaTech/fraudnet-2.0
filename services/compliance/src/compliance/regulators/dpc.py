"""DPC — Data Protection Commission breach / PII exposure notification.

The pack is structured around the breach (or the absence of one).
The audit log identifies cross-purpose accesses and policy violations;
those are surfaced here. If the period had no breach events, the
report is a clean attestation.
"""

from __future__ import annotations

from typing import Any

from compliance.regulators.base import Field, RegulatorReport, Section
from compliance.regulators.corpus import PeriodCorpus


_BREACH_ACTIONS = {
    "data.export",
    "data.purpose_violation",
    "data.unauthorised_access",
    "data.cross_tenant_access",
}


def _breach_audit_events(corpus: PeriodCorpus) -> list[dict[str, Any]]:
    return [a for a in corpus.audit_events if a.get("action") in _BREACH_ACTIONS]


def dpc_report(corpus: PeriodCorpus) -> RegulatorReport:
    breaches = _breach_audit_events(corpus)
    has_breach = bool(breaches)

    header = Section(
        title="Controller Identification",
        fields=(
            Field(name="controller", label="Data controller", value="MTN Ghana"),
            Field(name="dpo_name", label="DPO name", value=None, needs_review=True),
            Field(name="dpo_contact", label="DPO contact", value=None, needs_review=True),
            Field(name="period_start", label="Period start",
                  value=corpus.period_start.isoformat()),
            Field(name="period_end", label="Period end",
                  value=corpus.period_end.isoformat()),
        ),
    )

    summary = Section(
        title="Breach Status",
        fields=(
            Field(name="has_breach", label="Breach occurred", value=has_breach),
            Field(name="breach_event_count", label="Suspected breach events",
                  value=len(breaches)),
            Field(name="data_subjects_estimated", label="Affected data subjects (est.)",
                  value=None, needs_review=has_breach,
                  note="Required if has_breach=True. Estimate based on subjects in the affected events."),
            Field(name="data_categories", label="Categories of data involved",
                  value=None, needs_review=has_breach,
                  note="MSISDN, IMEI, wallet activity, etc."),
            Field(name="discovery_at", label="Discovery timestamp",
                  value=(breaches[0]["event_ts"].isoformat()
                         if breaches and breaches[0].get("event_ts") else None),
                  needs_review=has_breach),
            Field(name="containment_taken", label="Containment measures",
                  value=None, needs_review=has_breach,
                  note="Describe rotation / revocation / system-level controls applied."),
        ),
    )

    detail_fields: list[Field] = []
    for i, ev in enumerate(breaches[:20]):
        detail_fields.append(
            Field(
                name=f"breach_event_{i}",
                label=f"Breach event {i + 1}",
                value={
                    "action": ev.get("action"),
                    "actor_kind": ev.get("actor_kind"),
                    "resource_kind": ev.get("resource_kind"),
                    "purpose": ev.get("purpose"),
                    "request_id": ev.get("request_id"),
                    "event_ts": (
                        ev["event_ts"].isoformat() if ev.get("event_ts") else None
                    ),
                },
            )
        )
    detail = Section(title="Breach Detail", fields=tuple(detail_fields))

    attest = Section(
        title="Attestation",
        fields=(
            Field(name="reviewed_by", label="Reviewed by",
                  value=None, needs_review=True),
            Field(name="reviewer_signature_at", label="Signature timestamp",
                  value=None, needs_review=True),
        ),
    )

    return RegulatorReport(
        regulator="dpc",
        template_id="dpc-breach-notification-2025-1",
        period_start=corpus.period_start.isoformat(),
        period_end=corpus.period_end.isoformat(),
        sections=(header, summary, detail, attest),
        metadata={"has_breach": has_breach, "breach_count": len(breaches)},
    )
