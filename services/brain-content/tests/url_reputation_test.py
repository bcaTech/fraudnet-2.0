from __future__ import annotations

from brain_content.url_reputation import StaticBlocklist, domain_of


def test_domain_of_strips_scheme_and_path() -> None:
    assert domain_of("https://Bit.ly/X") == "bit.ly/x"  # case-folded; path retained
    assert domain_of("scam-momo.com") == "scam-momo.com"


def test_blocklist_exact_domain_hit() -> None:
    bl = StaticBlocklist(bad_domains={"scam-momo.com"})
    v = bl.check("https://scam-momo.com/path")
    assert v is not None
    assert v.category == "phishing"


def test_blocklist_subdomain_hit_with_lower_confidence() -> None:
    bl = StaticBlocklist(bad_domains={"scam-momo.com"})
    direct = bl.check("https://scam-momo.com")
    sub = bl.check("https://login.scam-momo.com/x")
    assert direct is not None and sub is not None
    assert sub.confidence < direct.confidence


def test_blocklist_no_hit() -> None:
    bl = StaticBlocklist(bad_domains={"scam.example"})
    assert bl.check("https://safe-bank.com") is None
