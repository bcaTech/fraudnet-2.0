"""GFIC — Suspicious Activity Report.

GFIC SARs require subject identification (full plaintext MSISDN /
national ID) at submission. We do *not* include those in the
auto-filled JSON — every SAR has a `needs_review` row that the
authorised compliance officer fills in via a separate authenticated
flow. The export carries the SHA-derived short_subject for
cross-referencing.
"""

from __future__ import annotations

from compliance.regulators.base import Field, RegulatorReport, Section
from compliance.regulators.corpus import (
    PeriodCorpus,
    short_subject,
)


_SAR_TRIGGER_SIGNAL_KINDS = {
    "momo.mule_velocity",
    "agent.commission_farming",
    "agent.split_txn",
    "agent.collusion",
    "agent.float_manipulation",
    "aml.watchlist_match",
}


def gfic_report(corpus: PeriodCorpus) -> RegulatorReport:
    sar_alerts = [
        a
        for a in corpus.alerts
        if (a.get("details") or {}).get("signal_kind") in _SAR_TRIGGER_SIGNAL_KINDS
    ]

    header = Section(
        title="Reporting Institution",
        fields=(
            Field(name="institution", label="Reporting institution", value="MTN Ghana"),
            Field(name="period_start", label="Period start",
                  value=corpus.period_start.isoformat()),
            Field(name="period_end", label="Period end",
                  value=corpus.period_end.isoformat()),
            Field(name="aml_officer", label="Compliance officer",
                  value=None, needs_review=True),
            Field(name="institution_reference", label="Institution reference",
                  value=None, needs_review=True),
        ),
    )

    sar_fields: list[Field] = []
    for i, a in enumerate(sar_alerts[:50]):
        details = a.get("details") or {}
        signal_kind = details.get("signal_kind")
        sar_fields.append(
            Field(
                name=f"sar_{i}",
                label=f"SAR {i + 1}",
                value={
                    "subject_short": short_subject(
                        a.get("subject_kind"), a.get("subject_id")
                    ),
                    "signal_kind": signal_kind,
                    "severity": a.get("severity"),
                    "score": float(a["score"]) if a.get("score") is not None else None,
                    "created_at": (a["created_at"].isoformat()
                                   if a.get("created_at") else None),
                    "ring_id": str(a["ring_id"]) if a.get("ring_id") else None,
                },
            )
        )
        sar_fields.append(
            Field(
                name=f"sar_{i}_subject_msisdn",
                label=f"SAR {i + 1} — subject MSISDN (regulated submission)",
                value=None,
                needs_review=True,
                note="Plaintext MSISDN required by GFIC. Only fill via "
                     "the authorised submission flow; do not commit this "
                     "field to source control.",
            )
        )

    sars = Section(title="Suspicious Activity Reports", fields=tuple(sar_fields))

    attest = Section(
        title="Attestation",
        fields=(
            Field(name="filed_by", label="Filed by",
                  value=None, needs_review=True),
            Field(name="filed_at", label="Filing timestamp",
                  value=None, needs_review=True),
        ),
    )

    return RegulatorReport(
        regulator="gfic",
        template_id="gfic-sar-2025-1",
        period_start=corpus.period_start.isoformat(),
        period_end=corpus.period_end.isoformat(),
        sections=(header, sars, attest),
        metadata={"sar_count": len(sar_alerts)},
    )
