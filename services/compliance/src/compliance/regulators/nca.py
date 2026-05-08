"""NCA — telecom fraud incident report.

Output: per-period summary of voice/SMS/OTT incidents with
confirmed-fraud counts, action volumes, and per-incident detail (up to
50 most recent confirmed-fraud alerts in the period).
"""

from __future__ import annotations

from compliance.regulators.base import Field, RegulatorReport, Section
from compliance.regulators.corpus import (
    PeriodCorpus,
    confirmed_fraud_alerts,
    corpus_summary,
    short_subject,
)


def nca_report(corpus: PeriodCorpus) -> RegulatorReport:
    summary = corpus_summary(corpus)

    # ----- Header -----
    header = Section(
        title="Operator Identification",
        fields=(
            Field(name="operator_name", label="Operator", value="MTN Ghana"),
            Field(name="reporting_period_start", label="Period start",
                  value=corpus.period_start.isoformat()),
            Field(name="reporting_period_end", label="Period end",
                  value=corpus.period_end.isoformat()),
            Field(name="contact_email", label="Compliance contact email",
                  value=None, needs_review=True,
                  note="Compliance officer email — regulator-specific contact."),
            Field(name="report_reference", label="Operator reference",
                  value=None, needs_review=True,
                  note="Internal incident-tracking reference if assigned."),
        ),
    )

    # ----- Summary -----
    summary_section = Section(
        title="Period Summary",
        fields=(
            Field(name="total_alerts", label="Total alerts", value=summary["alerts"]),
            Field(
                name="confirmed_fraud_alerts",
                label="Confirmed fraud alerts",
                value=len(confirmed_fraud_alerts(corpus)),
            ),
            Field(
                name="actions_taken",
                label="Actions taken",
                value=summary["actions"],
            ),
            Field(
                name="actions_volte_tag",
                label="VoLTE tags",
                value=summary.get("action_volte_tag", 0),
            ),
            Field(
                name="actions_url_block",
                label="URL blocks",
                value=summary.get("action_url_block", 0),
            ),
            Field(
                name="actions_momo_friction",
                label="MoMo friction prompts",
                value=summary.get("action_momo_friction", 0),
            ),
        ),
    )

    # ----- Per-incident detail (top 50 confirmed-fraud alerts) -----
    fraud = confirmed_fraud_alerts(corpus)[:50]
    incidents = Section(
        title="Confirmed Fraud Incidents",
        fields=tuple(
            Field(
                name=f"incident_{i}",
                label=f"Incident {i + 1}",
                value={
                    "subject": short_subject(a.get("subject_kind"), a.get("subject_id")),
                    "type": a.get("type"),
                    "severity": a.get("severity"),
                    "score": float(a["score"]) if a.get("score") is not None else None,
                    "created_at": (a["created_at"].isoformat()
                                   if a.get("created_at") else None),
                    "closed_at": (a["closed_at"].isoformat()
                                  if a.get("closed_at") else None),
                    "closed_reason": a.get("closed_reason"),
                },
            )
            for i, a in enumerate(fraud)
        ),
    )

    # ----- Reviewer attestation -----
    attest = Section(
        title="Attestation",
        fields=(
            Field(
                name="reviewed_by",
                label="Reviewed by",
                value=None,
                needs_review=True,
                note="Name and title of the compliance officer signing off.",
            ),
            Field(
                name="reviewer_signature_at",
                label="Signature timestamp",
                value=None,
                needs_review=True,
            ),
            Field(
                name="reviewer_notes",
                label="Reviewer notes",
                value=None,
                needs_review=True,
                note="Any context the regulator should be aware of.",
            ),
        ),
    )

    return RegulatorReport(
        regulator="nca",
        template_id="nca-telecom-fraud-incident-2025-1",
        period_start=corpus.period_start.isoformat(),
        period_end=corpus.period_end.isoformat(),
        sections=(header, summary_section, incidents, attest),
        metadata={"alert_count_in_pack": len(fraud)},
    )
