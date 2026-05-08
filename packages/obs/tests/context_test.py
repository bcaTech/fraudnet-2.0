from __future__ import annotations

import asyncio

from fraudnet.obs.context import (
    bind_context,
    clear_context,
    get_request_id,
    new_request_id,
    set_request_id,
)


def test_request_id_round_trip() -> None:
    clear_context()
    rid = new_request_id()
    set_request_id(rid)
    assert get_request_id() == rid
    clear_context()
    assert get_request_id() is None


def test_request_id_is_isolated_per_task() -> None:
    """ContextVars carry through asyncio TaskGroup boundaries correctly."""
    clear_context()
    set_request_id("outer")

    seen: list[str | None] = []

    async def child(tag: str) -> None:
        set_request_id(tag)
        await asyncio.sleep(0)
        seen.append(get_request_id())

    async def runner() -> None:
        # Each task gets its own copy of the ContextVar.
        await asyncio.gather(child("inner-a"), child("inner-b"))

    asyncio.run(runner())
    assert set(seen) == {"inner-a", "inner-b"}


def test_bind_context_partial() -> None:
    clear_context()
    bind_context(request_id="r", tenant_id="mtn-ghana")
    assert get_request_id() == "r"
