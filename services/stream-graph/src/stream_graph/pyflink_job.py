"""PyFlink wrapper for stream-graph (Phase 2). See DECISIONS.md D-002."""

from __future__ import annotations


def main() -> None:  # pragma: no cover
    raise NotImplementedError(
        "PyFlink stream-graph stub — Phase 1 ships the standalone runner. Phase 2 "
        "wraps stream_graph.pipeline translators in a Flink Table-API job submitted "
        "via the Flink Kubernetes Operator."
    )


if __name__ == "__main__":  # pragma: no cover
    main()
