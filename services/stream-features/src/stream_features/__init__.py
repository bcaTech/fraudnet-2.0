"""stream-features — window-aggregate event streams into the feature store.

Phase 1: deployed as a regular Python consumer pod. The pipeline logic is
isolated in `pipeline.py` so it ports cleanly to PyFlink in Phase 2 (see
DECISIONS.md D-002). Consumes voice / sms / momo events; writes
NumberFeatures and WalletFeatures to Aerospike via fraudnet.features.

Watermarking: event-time semantics with a 30-second lateness allowance per
CLAUDE.md §12. Features are recomputed for any window the late event falls
into.
"""

__version__ = "0.1.0"
