"""Cyber Security Authority — incident report (CIRT-Gh feed).

Cyber-classified events: smishing, malicious URLs, OTT phishing
campaigns, watchlist matches with cyber-criminal categorisation.
"""

from __future__ import annotations

from compliance.regulators.base import Field, RegulatorReport, Section
from compliance.regulators.corpus import (
    PeriodCorpus,
    corpus_summary,
    short_subject,
)


_CYBER_KINDS = {
    "sms.malicious_url",
    "sms.template_smishing",
    "sms.known_bad_body",
    "sms.known_bad_template",
    "sms.ott_lookalike",
    "sms.url_shortener_abuse",
    "ott.suspicious_domain",
    "data.dns_blocklist_hit",
}


def csa_report(corpus: PeriodCorpus) -> RegulatorReport:
    cyber_alerts = [
        a
        for a in corpus.alerts
        if (a.get("details") or {}).get("signal_kind") in _CYBER_KINDS
        or a.get("type") == "ott"
    ]
    summary = corpus_summary(corpus)

    header = Section(
        title="Reporting Operator",
        fields=(
            Field(name="operator", label="Operator", value="MTN Ghana"),
            Field(name="period_start", label="Period start",
                  value=corpus.period_start.isoformat()),
            Field(name="period_end", label="Period end",
                  value=corpus.period_end.isoformat()),
            Field(name="cirt_contact", label="CIRT-Gh contact",
                  value=None, needs_review=True),
        ),
    )

    summary_section = Section(
        title="Cyber Incident Summary",
        fields=(
            Field(name="cyber_alerts_total", label="Cyber-classified alerts",
                  value=len(cyber_alerts)),
            Field(name="urls_blocked", label="URLs blocked (DNS sinkhole)",
                  value=summary.get("action_url_block", 0)),
            Field(name="smishing_volume", label="Smishing template alerts",
                  value=sum(
                      1 for a in cyber_alerts
                      if (a.get("details") or {}).get("signal_kind", "")
                      .startswith("sms.")
                  )),
            Field(name="ott_volume", label="OTT lookalike / phishing alerts",
                  value=sum(
                      1 for a in cyber_alerts
                      if a.get("type") == "ott"
                  )),
        ),
    )

    detail_fields: list[Field] = []
    for i, a in enumerate(cyber_alerts[:40]):
        detail_fields.append(
            Field(
                name=f"cyber_incident_{i}",
                label=f"Cyber incident {i + 1}",
                value={
                    "subject": short_subject(a.get("subject_kind"), a.get("subject_id")),
                    "severity": a.get("severity"),
                    "signal_kind": (a.get("details") or {}).get("signal_kind"),
                    "domain": (a.get("details") or {}).get("domain"),
                    "created_at": (a["created_at"].isoformat()
                                   if a.get("created_at") else None),
                },
            )
        )
    incidents = Section(title="Cyber Incidents", fields=tuple(detail_fields))

    return RegulatorReport(
        regulator="csa",
        template_id="csa-cyber-incident-2025-1",
        period_start=corpus.period_start.isoformat(),
        period_end=corpus.period_end.isoformat(),
        sections=(header, summary_section, incidents),
        metadata={"cyber_alerts": len(cyber_alerts)},
    )
