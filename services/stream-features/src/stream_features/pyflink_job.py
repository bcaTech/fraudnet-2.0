"""PyFlink Table-API wrapper for stream-features.

Phase 2 deployable. Imports `pipeline.py` for the per-event transform; the
windowing semantics that the standalone runner emulates in-process map to
Flink's native sliding/tumbling windows here.

This file does not import pyflink at module level so the Phase 1 runner
package can be installed without it. Submit to Flink with:

    flink run -py services/stream-features/src/stream_features/pyflink_job.py
"""

from __future__ import annotations


def main() -> None:  # pragma: no cover — Phase 2 entrypoint
    """Entrypoint for `flink run -py ...`.

    Phase 2 task. The pseudocode below is a placeholder showing the intended
    structure; production implementation lands when the team has trained on
    the Flink Kubernetes Operator.
    """
    raise NotImplementedError(
        "PyFlink job stub — see DECISIONS.md D-002. Phase 1 ships the standalone "
        "runner in stream_features.runner. Phase 2 fills this in."
    )


if __name__ == "__main__":  # pragma: no cover
    main()
