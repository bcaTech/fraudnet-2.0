from __future__ import annotations

import pytest

from brain_content.ott_domain_analysis import (
    FirstSeenTracker,
    OttDomainAnalyser,
    detect_brand_lookalike,
    is_newly_registered,
    is_url_shortener,
)


class TestBrandLookalike:
    def test_legitimate_passes_through(self) -> None:
        is_l, target, dist = detect_brand_lookalike("mtn.com.gh")
        assert not is_l
        assert target is None and dist is None

    def test_subdomain_of_legitimate_passes(self) -> None:
        is_l, _, _ = detect_brand_lookalike("login.mtn.com.gh")
        # subdomain of mtn.com.gh — registrable matches
        assert not is_l

    def test_brand_keyword_in_unrelated_registrable(self) -> None:
        is_l, target, _ = detect_brand_lookalike("mtnmomo-secure.attacker.com")
        assert is_l
        assert target in {"mtn", "momo"}

    def test_close_edit_distance_flags_lookalike(self) -> None:
        # mtn → m1n is distance 1
        is_l, target, dist = detect_brand_lookalike("m1n.tld")
        assert is_l
        assert target == "mtn.com" or target == "mtn.com.gh"
        assert dist == 1

    def test_far_away_does_not_flag(self) -> None:
        is_l, _, _ = detect_brand_lookalike("totally-different.xyz")
        assert not is_l


class TestUrlShortener:
    @pytest.mark.parametrize("d", ["bit.ly", "tinyurl.com", "t.co"])
    def test_known_shorteners(self, d: str) -> None:
        assert is_url_shortener(d)

    def test_subdomain_of_shortener(self) -> None:
        assert is_url_shortener("foo.bit.ly")

    def test_unrelated(self) -> None:
        assert not is_url_shortener("example.com")


class TestNewlyRegistered:
    def test_first_seen_is_nrd(self) -> None:
        tracker = FirstSeenTracker()
        is_nrd, age = is_newly_registered("new-domain.tld", tracker, now_ms=1_000_000)
        assert is_nrd
        assert age == 0

    def test_old_first_seen_is_not_nrd(self) -> None:
        tracker = FirstSeenTracker()
        # Pre-record at t=0
        tracker.observe("old-domain.tld", now_ms=0)
        # Check 60 days later
        sixty_days_ms = 60 * 24 * 60 * 60 * 1000
        is_nrd, age = is_newly_registered(
            "old-domain.tld", tracker, window_days=30, now_ms=sixty_days_ms
        )
        assert not is_nrd
        assert age == sixty_days_ms // 1000


class TestOttDomainAnalyser:
    def test_lookalike_with_shortener_is_strongest(self) -> None:
        a = OttDomainAnalyser()
        # bit.ly is a shortener but no brand keyword. Use a constructed
        # case where a known shortener subdomain has a brand keyword.
        v = a.analyse("mtn-rewards.bit.ly")
        # Substring "mtn" in label flags brand-lookalike; bit.ly suffix
        # flags shortener.
        assert v.is_brand_lookalike
        assert v.is_url_shortener
        assert v.is_suspicious

    def test_pure_nrd_is_suspicious_but_excluded_from_blocklist(self) -> None:
        a = OttDomainAnalyser()
        v = a.analyse("brand-new.example", now_ms=1_000_000)
        assert v.is_newly_registered
        # Soft signal — not added to suspicious_domains hot list.
        assert "brand-new.example" not in a.suspicious_domains

    def test_lookalike_added_to_suspicious_domains(self) -> None:
        a = OttDomainAnalyser()
        a.analyse("mtnmomo-fake.attacker.com")
        assert "mtnmomo-fake.attacker.com" in a.suspicious_domains

    def test_evidence_is_serialisable(self) -> None:
        a = OttDomainAnalyser()
        v = a.analyse("mtnmomo-fake.attacker.com")
        ev = v.to_evidence()
        # All values are primitives ready for the Avro signal envelope.
        for k, val in ev.items():
            assert isinstance(k, str)
            assert isinstance(val, (str, int, float, bool))
