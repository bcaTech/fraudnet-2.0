from __future__ import annotations

from ingest_sms.normaliser import normalise


def test_empty_body() -> None:
    n = normalise("")
    assert n.body_hash.startswith("sha256:")
    assert n.urls == ()


def test_body_hash_stable_across_whitespace() -> None:
    a = normalise("Hello   world!")
    b = normalise("Hello world!")
    assert a.body_hash == b.body_hash


def test_template_hash_collides_for_same_template() -> None:
    a = normalise("Send GHS 100 to 0241234567 to claim GHS 1000 prize")
    b = normalise("Send GHS 250 to 0207654321 to claim GHS 5000 prize")
    assert a.template_hash == b.template_hash, "template should collide regardless of variables"
    assert a.body_hash != b.body_hash, "body hashes differ"


def test_template_hash_differs_for_different_templates() -> None:
    a = normalise("You won GHS 1000")
    b = normalise("Your account has been blocked")
    assert a.template_hash != b.template_hash


def test_url_extraction() -> None:
    n = normalise("Click https://Bit.ly/scam-Path/A1 now! and https://other.com/p.")
    assert "https://bit.ly/scam-Path/A1" in n.urls  # path case preserved, host lowered
    assert "https://other.com/p" in n.urls  # trailing dot stripped


def test_url_extraction_dedups() -> None:
    n = normalise("Visit https://x.com and again https://x.com.")
    assert n.urls == ("https://x.com",)


def test_msisdn_redacted_in_template() -> None:
    n_local = normalise("Send to 0241234567 now")
    n_e164 = normalise("Send to +233241234567 now")
    assert n_local.template_hash == n_e164.template_hash
