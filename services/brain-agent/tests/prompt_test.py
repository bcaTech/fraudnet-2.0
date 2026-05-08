"""Prompt rendering + redaction. The wire-format guarantee for what
leaves this service via the LLM."""

from __future__ import annotations

from brain_agent.prompt import (
    EvidencePackage,
    redact_account,
    redact_imei,
    redact_msisdn,
    redact_wallet,
    redact_for_prompt,
    render_user_prompt,
)


def test_redactors_are_deterministic() -> None:
    a = redact_msisdn("+233200000001")
    b = redact_msisdn("+233200000001")
    assert a == b
    assert a.startswith("NUM_")


def test_redactors_distinguish_kinds() -> None:
    """Same plaintext, different kind → different token. Defends against
    a prompt that confuses a number with a wallet of the same value."""
    n = redact_msisdn("12345")
    w = redact_wallet("12345")
    d = redact_imei("12345")
    a = redact_account("12345")
    assert len({n, w, d, a}) == 4


def test_redact_msisdn_in_free_text() -> None:
    txt = "Caller +233200000001 sent SMS to 233500000099"
    out = redact_for_prompt(txt)
    assert "+233200000001" not in out
    assert "233500000099" not in out
    assert "NUM_" in out


def test_render_user_prompt_includes_not_available() -> None:
    """The model must always see what was missing — that's how it gets
    `data_gaps` right."""
    pkg = EvidencePackage(
        target_kind="alert",
        target_id="00000000-0000-0000-0000-000000000001",
        redacted_target="ALERT_abcdef01",
        not_available=["feature_snapshots", "subgraph"],
    )
    rendered = render_user_prompt(pkg)
    assert '"not_available"' in rendered
    assert "feature_snapshots" in rendered
    assert "subgraph" in rendered


def test_render_user_prompt_does_not_leak_msisdn() -> None:
    """If the caller built the package correctly, raw MSISDN should not
    be in the rendered prompt — only redacted tokens should appear."""
    pkg = EvidencePackage(
        target_kind="alert",
        target_id="00000000-0000-0000-0000-000000000001",
        redacted_target=redact_msisdn("+233200000001"),
        alert={"id": "abc", "subject_id": redact_msisdn("+233200000001")},
    )
    rendered = render_user_prompt(pkg)
    assert "+233200000001" not in rendered
    assert "NUM_" in rendered
