from __future__ import annotations

import pytest

from ingest_data.normaliser import canonicalise_domain, canonicalise_ip


class TestCanonicaliseDomain:
    def test_lowercases_and_strips_trailing_dot(self) -> None:
        out = canonicalise_domain("Login.MTN-MoMo.Example.COM.")
        assert out.fqdn == "login.mtn-momo.example.com"
        assert out.registrable == "example.com"

    def test_idn_homoglyph_collapsed_to_a_label(self) -> None:
        # 'mοmo' uses Greek omicron; A-label punycode form is xn--mmo-rzc.
        out = canonicalise_domain("mοmo.example.com")
        assert out.fqdn.startswith("xn--")
        assert out.fqdn.endswith(".example.com")

    def test_short_domain_registrable_falls_back_to_self(self) -> None:
        out = canonicalise_domain("localhost")
        assert out.fqdn == "localhost"
        assert out.registrable == "localhost"

    @pytest.mark.parametrize("bad", ["", " ", ".", "a..b"])
    def test_rejects_bad_domain(self, bad: str) -> None:
        with pytest.raises(ValueError):
            canonicalise_domain(bad)

    def test_rejects_overlong_domain(self) -> None:
        with pytest.raises(ValueError):
            canonicalise_domain("a" * 254)


class TestCanonicaliseIp:
    def test_ipv4(self) -> None:
        assert canonicalise_ip("203.0.113.42") == "203.0.113.42"

    def test_ipv6_compressed(self) -> None:
        assert canonicalise_ip("2001:0db8:0000:0000:0000:0000:0000:0001") == "2001:db8::1"

    def test_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            canonicalise_ip("not-an-ip")
