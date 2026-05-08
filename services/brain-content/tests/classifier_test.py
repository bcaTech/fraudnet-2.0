from __future__ import annotations

from brain_content.classifier import HeuristicContentClassifier, to_signal
from brain_content.url_reputation import StaticBlocklist


def _classifier(
    *,
    bad_domains: set[str] = frozenset(),
    bad_template_hashes: set[str] = frozenset(),
    bad_body_hashes: set[str] = frozenset(),
) -> HeuristicContentClassifier:
    return HeuristicContentClassifier(
        url_reputation=StaticBlocklist(bad_domains=bad_domains),
        bad_template_hashes=bad_template_hashes,
        bad_body_hashes=bad_body_hashes,
    )


class TestFastPaths:
    def test_known_bad_body_hash(self) -> None:
        c = _classifier(bad_body_hashes={"sha256:bad"})
        r = c.classify(body=None, body_hash="sha256:bad", template_hash=None)
        assert r.signal_kind == "sms.known_bad_body"
        assert r.severity.value == "critical"

    def test_known_bad_template(self) -> None:
        c = _classifier(bad_template_hashes={"sha256:scam-template"})
        r = c.classify(body=None, body_hash=None, template_hash="sha256:scam-template")
        assert r.signal_kind == "sms.known_bad_template"

    def test_no_body_no_match_returns_zero(self) -> None:
        c = _classifier()
        r = c.classify(body=None, body_hash=None, template_hash=None)
        assert r.signal_kind is None
        assert r.score.value == 0.0


class TestUrlPath:
    def test_malicious_url_in_body_fires(self) -> None:
        c = _classifier(bad_domains={"scam-momo.com"})
        r = c.classify(
            body="Click https://scam-momo.com/win to claim your prize",
            body_hash=None,
            template_hash=None,
        )
        assert r.signal_kind == "sms.malicious_url"
        assert r.matched_urls == ("https://scam-momo.com/win",)

    def test_unknown_url_does_not_fire(self) -> None:
        c = _classifier(bad_domains={"scam-momo.com"})
        r = c.classify(
            body="Check out https://safe-bank.com",
            body_hash=None,
            template_hash=None,
        )
        assert r.signal_kind is None  # urls present but no rep hit, no keyword threshold


class TestKeywordPath:
    def test_three_or_more_scam_keywords_fires(self) -> None:
        c = _classifier()
        r = c.classify(
            body="URGENT: You are a winner of GHS 5000 prize, click to claim now",
            body_hash=None,
            template_hash=None,
        )
        assert r.signal_kind == "sms.template_smishing"
        assert int(r.evidence["scam_keyword_hits"]) >= 3

    def test_few_keywords_no_signal(self) -> None:
        c = _classifier()
        r = c.classify(
            body="Hi, your appointment is at 2pm",
            body_hash=None,
            template_hash=None,
        )
        assert r.signal_kind is None


def test_to_signal_with_suppression_key() -> None:
    c = _classifier(bad_domains={"scam.example"})
    r = c.classify(
        body="Click https://scam.example/x", body_hash=None, template_hash=None
    )
    sig = to_signal(result=r, sender_msisdn="+233241234567", source="t")
    assert sig is not None
    assert sig.suppression_key == "mtn-ghana:number:+233241234567:sms.malicious_url"


def test_to_signal_returns_none_below_threshold() -> None:
    c = _classifier()
    r = c.classify(body="hello there", body_hash=None, template_hash=None)
    sig = to_signal(result=r, sender_msisdn="+233241234567", source="t")
    assert sig is None
