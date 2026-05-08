"""stream-graph — per-event Memgraph mutations.

Translates each voice / sms / momo event into one or more graph mutations
(node upserts + edge creates) and writes via the BufferedGraphWriter in
fraudnet.graph. Also publishes a corresponding GraphMutationV1 to
`graph.mutations.v1` so other services can subscribe to graph events
without needing a Memgraph reader.

Per CLAUDE.md §12: graph is **per-event**, not windowed. Watermark drift
here corrupts everything downstream; the runner aborts on persistent late
events rather than accept silent loss.
"""

__version__ = "0.1.0"
