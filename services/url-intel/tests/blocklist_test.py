from __future__ import annotations

import pytest

from url_intel.blocklist import Blocklist, in_memory_blocklist, normalise_domain


class TestNormaliseDomain:
    def test_strips_scheme_and_path(self) -> None:
        assert normalise_domain("https://Bad.Example.com/phish?x=1") == "bad.example.com"

    def test_strips_port(self) -> None:
        assert normalise_domain("evil.example:8443/path") == "evil.example"

    def test_handles_bare_domain(self) -> None:
        assert normalise_domain("scam.example") == "scam.example"

    def test_empty(self) -> None:
        assert normalise_domain("") == ""


class TestBlocklistAdd:
    @pytest.mark.asyncio
    async def test_add_valid_domain(self) -> None:
        bl = in_memory_blocklist()
        ok, reason = await bl.add(domain="phish.example", source="manual")
        assert ok is True
        assert reason == "added"

    @pytest.mark.asyncio
    async def test_add_url_normalises(self) -> None:
        bl = in_memory_blocklist()
        ok, _ = await bl.add(domain="https://phish.example/x", source="manual")
        assert ok is True
        assert "phish.example" in await bl.export_all()

    @pytest.mark.asyncio
    async def test_invalid_domain_rejected(self) -> None:
        bl = in_memory_blocklist()
        ok, reason = await bl.add(domain="not_a_domain", source="manual")
        assert ok is False
        assert reason == "invalid_domain"

    @pytest.mark.asyncio
    async def test_allow_listed_domain_rejected(self) -> None:
        bl = in_memory_blocklist(allow_list=["google.com"])
        ok, reason = await bl.add(domain="google.com", source="manual")
        assert ok is False
        assert reason == "allow_listed"

    @pytest.mark.asyncio
    async def test_allow_listed_subdomain_rejected(self) -> None:
        bl = in_memory_blocklist(allow_list=["mtn.com.gh"])
        ok, reason = await bl.add(domain="momo.mtn.com.gh", source="manual")
        assert ok is False
        assert reason == "allow_listed"


class TestBlocklistCheck:
    @pytest.mark.asyncio
    async def test_check_unknown(self) -> None:
        bl = in_memory_blocklist()
        r = await bl.check("safe.example")
        assert r.blocked is False
        assert r.allow_listed is False

    @pytest.mark.asyncio
    async def test_check_blocked_exact(self) -> None:
        bl = in_memory_blocklist()
        await bl.add(domain="bad.example", source="manual", category="phishing", confidence=0.99)
        r = await bl.check("https://bad.example/x")
        assert r.blocked is True
        assert r.matched == "bad.example"
        assert r.entry is not None
        assert r.entry.category == "phishing"

    @pytest.mark.asyncio
    async def test_check_blocked_subdomain(self) -> None:
        bl = in_memory_blocklist()
        await bl.add(domain="bad.example", source="manual")
        r = await bl.check("login.bad.example")
        assert r.blocked is True
        assert r.matched == "bad.example"

    @pytest.mark.asyncio
    async def test_check_allow_listed_overrides(self) -> None:
        bl = in_memory_blocklist(allow_list=["whitelist.example"])
        # Even if somehow on the list, allow-list wins on check.
        await bl._client.sadd(bl.SET_KEY, "whitelist.example")  # type: ignore[attr-defined]
        r = await bl.check("whitelist.example")
        assert r.blocked is False
        assert r.allow_listed is True


class TestBlocklistRemove:
    @pytest.mark.asyncio
    async def test_remove_existing(self) -> None:
        bl = in_memory_blocklist()
        await bl.add(domain="bad.example", source="manual")
        assert await bl.remove("bad.example") is True
        assert (await bl.check("bad.example")).blocked is False

    @pytest.mark.asyncio
    async def test_remove_unknown_is_noop(self) -> None:
        bl = in_memory_blocklist()
        assert await bl.remove("unknown.example") is False


class TestExport:
    @pytest.mark.asyncio
    async def test_export_returns_sorted(self) -> None:
        bl = in_memory_blocklist()
        await bl.add(domain="b.example", source="m")
        await bl.add(domain="a.example", source="m")
        await bl.add(domain="c.example", source="m")
        assert await bl.export_all() == ["a.example", "b.example", "c.example"]
