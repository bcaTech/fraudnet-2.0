"""Bank of Ghana — mobile money fraud + suspicious-transaction report.

Per-period MoMo fraud volumes, ring count, takedowns filed, and
agent-fraud detections (signal_kind starting with `agent.`).
"""

from __future__ import annotations

from compliance.regulators.base import Field, RegulatorReport, Section
from compliance.regulators.corpus import (
    PeriodCorpus,
    confirmed_fraud_alerts,
    corpus_summary,
    short_subject,
)


_MOMO_TYPES = {"momo", "wallet"}


def bog_report(corpus: PeriodCorpus) -> RegulatorReport:
    summary = corpus_summary(corpus)
    momo_alerts = [
        a
        for a in corpus.alerts
        if (a.get("type") in _MOMO_TYPES) or (a.get("subject_kind") == "wallet")
    ]
    momo_confirmed = [
        a for a in confirmed_fraud_alerts(corpus)
        if (a.get("type") in _MOMO_TYPES) or (a.get("subject_kind") == "wallet")
    ]
    agent_alerts = [
        a for a in corpus.alerts
        if (a.get("details") or {}).get("signal_kind", "").startswith("agent.")
    ]

    header = Section(
        title="Reporting Institution",
        fields=(
            Field(name="institution", label="Reporting institution", value="MTN Ghana"),
            Field(name="period_start", label="Period start",
                  value=corpus.period_start.isoformat()),
            Field(name="period_end", label="Period end",
                  value=corpus.period_end.isoformat()),
            Field(name="aml_officer", label="AML officer",
                  value=None, needs_review=True),
        ),
    )

    summary_section = Section(
        title="MoMo Fraud Summary",
        fields=(
            Field(name="momo_alerts_total", label="MoMo alerts (total)",
                  value=len(momo_alerts)),
            Field(name="momo_alerts_confirmed", label="MoMo alerts confirmed fraud",
                  value=len(momo_confirmed)),
            Field(name="agent_pattern_alerts", label="Agent-pattern alerts",
                  value=len(agent_alerts)),
            Field(name="rings_active", label="Active rings", value=len(corpus.rings)),
            Field(name="actions_friction", label="MoMo friction prompts",
                  value=summary.get("action_momo_friction", 0)),
            Field(name="actions_freeze", label="Wallet freezes",
                  value=summary.get("action_freeze_account", 0)),
        ),
    )

    detail_fields: list[Field] = []
    for i, a in enumerate(momo_confirmed[:30]):
        detail_fields.append(
            Field(
                name=f"momo_incident_{i}",
                label=f"MoMo incident {i + 1}",
                value={
                    "subject": short_subject(a.get("subject_kind"), a.get("subject_id")),
                    "severity": a.get("severity"),
                    "score": float(a["score"]) if a.get("score") is not None else None,
                    "created_at": (a["created_at"].isoformat()
                                   if a.get("created_at") else None),
                    "ring_id": str(a["ring_id"]) if a.get("ring_id") else None,
                },
            )
        )
    incidents = Section(title="Confirmed Incidents", fields=tuple(detail_fields))

    sar = Section(
        title="SAR Indicator",
        fields=(
            Field(
                name="sar_required",
                label="SAR cross-filing required",
                value=None,
                needs_review=True,
                note="If any momo_confirmed incident meets BoG SAR threshold, "
                     "tick yes and ensure a parallel GFIC submission has been "
                     "lodged. Cross-reference the GFIC reference here.",
            ),
            Field(
                name="gfic_reference",
                label="GFIC SAR reference",
                value=None,
                needs_review=True,
            ),
        ),
    )

    return RegulatorReport(
        regulator="bog",
        template_id="bog-mobile-money-fraud-2025-1",
        period_start=corpus.period_start.isoformat(),
        period_end=corpus.period_end.isoformat(),
        sections=(header, summary_section, incidents, sar),
        metadata={
            "momo_total": len(momo_alerts),
            "momo_confirmed": len(momo_confirmed),
        },
    )
