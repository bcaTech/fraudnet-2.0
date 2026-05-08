from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from decisions.hot_reload import PolicyHotReloader, _validate
from decisions.policy import Policy, load_all


_BASE_YAML = """
id: t-1
version: "v1"
rules:
  - id: r1
    match: { signal_kind: x }
    action: act
    tier: tier2
default:
  action: investigation.queue
  tier: tier3
"""

_BASE_YAML_V2 = """
id: t-1
version: "v2"
rules:
  - id: r1
    match: { signal_kind: x }
    action: act
    tier: tier2
  - id: r2
    match: { signal_kind: y }
    action: act2
    tier: tier1
default:
  action: investigation.queue
  tier: tier3
"""

_INVALID_YAML = """
id: t-1
version: "v3"
rules:
  - id: ""
    match: { signal_kind: x }
    action: act
    tier: tier2
default:
  action: investigation.queue
  tier: tier3
"""


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "default.yaml"
    p.write_text(content)
    return p


def _build_reloader(directory: Path) -> tuple[PolicyHotReloader, SimpleNamespace, SimpleNamespace]:
    initial = load_all(directory)
    dispatcher = SimpleNamespace(_policy=initial)
    runner = SimpleNamespace(_policy=initial)
    reloader = PolicyHotReloader(directory=directory, dispatcher=dispatcher, runner=runner)
    reloader.record_initial(initial)
    return reloader, dispatcher, runner


def test_reload_swaps_policy_and_appends_history(tmp_path: Path) -> None:
    _write(tmp_path, _BASE_YAML)
    reloader, dispatcher, runner = _build_reloader(tmp_path)
    assert len(reloader.history) == 1

    _write(tmp_path, _BASE_YAML_V2)
    version = reloader.reload_now()
    assert version is not None
    assert version.version == "v2"
    assert len(reloader.history) == 2
    assert dispatcher._policy.version == "v2"
    assert runner._policy.version == "v2"


def test_reload_rejects_invalid_policy(tmp_path: Path) -> None:
    _write(tmp_path, _BASE_YAML)
    reloader, dispatcher, _runner = _build_reloader(tmp_path)
    _write(tmp_path, _INVALID_YAML)
    assert reloader.reload_now() is None
    # current policy unchanged
    assert dispatcher._policy.version == "v1"
    assert len(reloader.history) == 1


def test_reload_noop_when_fingerprint_unchanged(tmp_path: Path) -> None:
    _write(tmp_path, _BASE_YAML)
    reloader, _d, _r = _build_reloader(tmp_path)
    assert reloader.reload_now() is None
    assert len(reloader.history) == 1


def test_history_is_capped(tmp_path: Path) -> None:
    _write(tmp_path, _BASE_YAML)
    reloader, _d, _r = _build_reloader(tmp_path)
    reloader._history.clear()  # reset for test  # noqa: SLF001
    reloader.record_initial(load_all(tmp_path))

    for i in range(20):
        new_yaml = _BASE_YAML_V2.replace('"v2"', f'"v{i + 2}"')
        _write(tmp_path, new_yaml)
        reloader.reload_now()
    assert len(reloader.history) <= reloader.HISTORY_CAP


def test_validate_catches_duplicate_rule_ids() -> None:
    raw = {
        "id": "t",
        "version": "1",
        "rules": [
            {"id": "x", "match": {}, "action": "a", "tier": "tier2"},
            {"id": "x", "match": {}, "action": "b", "tier": "tier2"},
        ],
    }
    p = Policy.from_dict(raw)
    try:
        _validate(p)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("expected ValueError")
