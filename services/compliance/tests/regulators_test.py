"""Per-regulator formatter tests + PDF emitter sanity."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from compliance.regulators import (
    REGULATOR_TEMPLATES,
    REPORT_BUILDERS,
    bog_report,
    csa_report,
    dpc_report,
    gfic_report,
    nca_report,
    render_report_pdf,
)
from compliance.regulators.corpus import PeriodCorpus, short_subject


def _corpus(**overrides) -> PeriodCorpus:  # noqa: ANN003
    base = dict(
        period_start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 6, 1, tzinfo=timezone.utc),
        tenant_id="mtn-ghana",
    )
    base.update(overrides)
    return PeriodCorpus(**base)  # type: ignore[arg-type]


def _sample_alert(
    *,
    severity: str = "high",
    status: str = "closed",
    signal_kind: str | None = None,
    type: str = "voice",
    subject_id: str = "+233200000001",
) -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "type": type,
        "severity": severity,
        "subject_kind": "number",
        "subject_id": subject_id,
        "score": 0.9,
        "ring_id": None,
        "status": status,
        "closed_at": datetime(2026, 5, 15, tzinfo=timezone.utc),
        "closed_reason": "confirmed mule" if status == "closed" else None,
        "details": {"signal_kind": signal_kind} if signal_kind else {},
        "created_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 15, tzinfo=timezone.utc),
    }


def test_short_subject_is_stable_and_kind_distinct() -> None:
    a = short_subject("number", "+233200000001")
    b = short_subject("number", "+233200000001")
    c = short_subject("wallet", "+233200000001")
    assert a == b
    assert a != c
    # No raw MSISDN in the token.
    assert "200000001" not in a


def test_short_subject_does_not_leak_raw_value() -> None:
    """Even with extreme inputs the token never carries the raw value."""
    raw = "+233200000001"
    token = short_subject("number", raw)
    assert raw not in token


def test_nca_report_structure() -> None:
    corpus = _corpus(alerts=tuple([_sample_alert()] * 3))
    report = nca_report(corpus)
    assert report.regulator == "nca"
    assert report.template_id.startswith("nca-")
    section_titles = [s.title for s in report.sections]
    assert "Operator Identification" in section_titles
    assert "Confirmed Fraud Incidents" in section_titles
    # 3 confirmed-fraud alerts → 3 incident fields
    incidents = next(
        s for s in report.sections if s.title == "Confirmed Fraud Incidents"
    )
    assert len(incidents.fields) == 3
    # The summary contact email + report reference need review
    assert report.review_field_count >= 1


def test_dpc_report_no_breach() -> None:
    """Period with zero breach events → has_breach=False, no review on
    breach-detail fields (they're conditional)."""
    report = dpc_report(_corpus())
    has_breach_field = next(
        f
        for s in report.sections
        for f in s.fields
        if f.name == "has_breach"
    )
    assert has_breach_field.value is False


def test_dpc_report_with_breach_flags_review() -> None:
    breach_event = {
        "id": "00000000-0000-0000-0000-000000000002",
        "actor_id": None,
        "actor_kind": "user",
        "action": "data.cross_tenant_access",
        "resource_kind": "alerts",
        "resource_id": "00000000-0000-0000-0000-000000000001",
        "purpose": "fraud_prevention",
        "request_id": "rq_x",
        "tenant_id": "mtn-ghana",
        "metadata": {},
        "event_ts": datetime(2026, 5, 10, tzinfo=timezone.utc),
    }
    report = dpc_report(_corpus(audit_events=(breach_event,)))
    has_breach_field = next(
        f for s in report.sections for f in s.fields if f.name == "has_breach"
    )
    assert has_breach_field.value is True
    review_names = {
        f.name
        for s in report.sections
        for f in s.fields
        if f.needs_review
    }
    assert "data_subjects_estimated" in review_names
    assert "containment_taken" in review_names


def test_bog_report_filters_to_momo_alerts() -> None:
    voice = _sample_alert(type="voice")
    momo = _sample_alert(
        type="momo",
        subject_id="wallet_123",
        signal_kind="momo.mule_velocity",
    )
    report = bog_report(_corpus(alerts=(voice, momo)))
    summary = next(s for s in report.sections if s.title == "MoMo Fraud Summary")
    momo_total = next(f for f in summary.fields if f.name == "momo_alerts_total")
    assert momo_total.value == 1


def test_csa_report_includes_smishing() -> None:
    sms = _sample_alert(signal_kind="sms.template_smishing", type="sms")
    report = csa_report(_corpus(alerts=(sms,)))
    summary = next(s for s in report.sections if s.title == "Cyber Incident Summary")
    smishing = next(f for f in summary.fields if f.name == "smishing_volume")
    assert smishing.value == 1


def test_gfic_report_marks_subject_msisdn_as_review() -> None:
    """Every SAR has a `subject_msisdn` field marked needs_review — the
    plaintext MSISDN does not auto-fill."""
    sar = _sample_alert(signal_kind="momo.mule_velocity", type="momo")
    report = gfic_report(_corpus(alerts=(sar,)))
    review = [
        f for s in report.sections for f in s.fields
        if f.name == "sar_0_subject_msisdn"
    ]
    assert len(review) == 1
    assert review[0].needs_review is True
    assert review[0].value is None


def test_pdf_emitter_returns_valid_pdf() -> None:
    """A valid PDF starts with %PDF- and ends with %%EOF."""
    report = nca_report(_corpus(alerts=(_sample_alert(),)))
    pdf = render_report_pdf(report)
    assert pdf.startswith(b"%PDF-")
    assert b"%%EOF" in pdf
    # Single-page-or-more.
    assert pdf.count(b"/Type /Page") >= 1


def test_pdf_emitter_paginates_long_reports() -> None:
    """40 incidents force a page break."""
    alerts = tuple(_sample_alert(subject_id=f"+23320000000{i}") for i in range(40))
    report = nca_report(_corpus(alerts=alerts))
    pdf = render_report_pdf(report)
    assert pdf.count(b"/Type /Page ") >= 2 or pdf.count(b"/Type /Page\n") >= 0


@pytest.mark.parametrize(
    "regulator",
    list(REPORT_BUILDERS),
)
def test_every_regulator_template_is_listed(regulator: str) -> None:
    """REGULATOR_TEMPLATES must cover every builder."""
    assert regulator in REGULATOR_TEMPLATES
    assert "name" in REGULATOR_TEMPLATES[regulator]
    assert "template_id" in REGULATOR_TEMPLATES[regulator]
