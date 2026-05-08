"""Policy hot-reload.

Watches the policy directory for YAML changes. On change:
  1. Parse + validate the new YAML.
  2. If valid, atomically swap it as the active policy.
  3. Append a `PolicyVersion` to the in-memory history (capped).

If parsing or validation fails, keep the current policy and log a
warning — never break a running decision pipeline because of an
operator typo.

The watcher is a thin wrapper over watchdog so test paths can drive
`reload_now()` directly without needing inotify.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Callable, Deque

from fraudnet.obs import counter, get_logger

from decisions.dispatcher import DecisionDispatcher
from decisions.policy import Policy, load_all
from decisions.runner import DecisionRunner

_log = get_logger("decisions.hot_reload")

_POLICY_RELOADS = counter(
    "decisions_policy_reloads_total",
    "Policy reload outcomes.",
    labelnames=("outcome",),
)


@dataclass(frozen=True)
class PolicyVersion:
    """One entry in the reload history."""

    id: str
    version: str
    fingerprint: str
    rule_count: int
    loaded_at_ms: int
    source_files: tuple[str, ...]


class PolicyHotReloader:
    """Manages the live policy + its reload history.

    The decisions service holds one of these. Calling `reload_now()`
    parses the directory, validates the result, and on success swaps
    the policy on `dispatcher` and `runner`. The previous policy stays
    in `history` so investigators can see what was active when an
    audited decision was emitted.
    """

    HISTORY_CAP = 10

    def __init__(
        self,
        *,
        directory: Path,
        dispatcher: DecisionDispatcher,
        runner: DecisionRunner,
        history_cap: int = HISTORY_CAP,
    ) -> None:
        self._directory = directory
        self._dispatcher = dispatcher
        self._runner = runner
        self._history: Deque[PolicyVersion] = deque(maxlen=history_cap)
        self._lock = threading.Lock()
        self._observer = None  # type: ignore[var-annotated]
        self._on_change_callbacks: list[Callable[[Policy], None]] = []

    @property
    def history(self) -> list[PolicyVersion]:
        return list(self._history)

    def record_initial(self, policy: Policy) -> None:
        """Seed the history with the initial loaded policy."""
        self._history.append(_to_version(policy, self._directory))

    def reload_now(self) -> PolicyVersion | None:
        """Parse + validate + swap. Returns the new PolicyVersion on
        success, or None if validation failed."""
        with self._lock:
            try:
                new_policy = load_all(self._directory)
                _validate(new_policy)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "decisions.policy_reload_failed",
                    error=str(exc),
                    directory=str(self._directory),
                )
                _POLICY_RELOADS.labels(outcome="rejected").inc()
                return None

            current_fp = self._current_fingerprint()
            if new_policy.fingerprint() == current_fp:
                _POLICY_RELOADS.labels(outcome="noop").inc()
                return None

            self._swap(new_policy)
            version = _to_version(new_policy, self._directory)
            self._history.append(version)
            _POLICY_RELOADS.labels(outcome="applied").inc()
            _log.info(
                "decisions.policy_reloaded",
                policy_id=new_policy.id,
                version=new_policy.version,
                fingerprint=new_policy.fingerprint(),
                rule_count=len(new_policy.rules),
            )
            for cb in self._on_change_callbacks:
                try:
                    cb(new_policy)
                except Exception as exc:  # noqa: BLE001
                    _log.warning("decisions.policy_callback_failed", error=str(exc))
            return version

    def on_change(self, cb: Callable[[Policy], None]) -> None:
        self._on_change_callbacks.append(cb)

    def start(self) -> None:
        """Spin up the watchdog observer. Falls back to a polling thread
        if the platform doesn't support inotify."""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            _log.warning("decisions.watchdog_missing — hot-reload disabled")
            return

        outer = self

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event) -> None:  # noqa: ANN001
                if not getattr(event, "is_directory", False) and str(event.src_path).endswith((".yaml", ".yml")):
                    outer.reload_now()

            on_created = on_modified
            on_moved = on_modified

        observer = Observer()
        observer.schedule(_Handler(), str(self._directory), recursive=False)
        observer.start()
        self._observer = observer
        _log.info("decisions.policy_watcher_started", directory=str(self._directory))

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _current_fingerprint(self) -> str:
        return self._dispatcher._policy.fingerprint()  # noqa: SLF001

    def _swap(self, new_policy: Policy) -> None:
        # Mutate the in-place attributes the dispatcher and runner reference.
        # This avoids tearing down the runner's Kafka consumer.
        self._dispatcher._policy = new_policy  # noqa: SLF001
        self._runner._policy = new_policy  # noqa: SLF001


def _validate(policy: Policy) -> None:
    """Cheap structural checks. Raises on failure."""
    if not policy.id:
        raise ValueError("policy.id is empty")
    if not policy.version:
        raise ValueError("policy.version is empty")
    seen_ids: set[str] = set()
    for rule in policy.rules:
        if not rule.id:
            raise ValueError("rule.id is empty")
        if rule.id in seen_ids:
            raise ValueError(f"duplicate rule id: {rule.id}")
        seen_ids.add(rule.id)
        if not rule.action:
            raise ValueError(f"rule {rule.id} has no action")
        if rule.suppression_window_s < 0:
            raise ValueError(f"rule {rule.id} has negative suppression_window_s")


def _to_version(policy: Policy, directory: Path) -> PolicyVersion:
    files = tuple(sorted(p.name for p in directory.glob("*.yaml")))
    return PolicyVersion(
        id=policy.id,
        version=policy.version,
        fingerprint=policy.fingerprint(),
        rule_count=len(policy.rules),
        loaded_at_ms=int(time() * 1000),
        source_files=files,
    )
