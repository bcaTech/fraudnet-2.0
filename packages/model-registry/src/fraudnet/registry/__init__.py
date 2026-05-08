"""FraudNet model registry.

Models are versioned blobs in MinIO (or any S3-compatible store). Each
model has:
  - a stable `model_id` (e.g. "behavioural-lgbm")
  - a `version` string (semver-ish — caller's choice)
  - a `champion` symlink pointed at the version currently serving traffic

The registry is intentionally simple: object storage is the source of
truth, the manifest JSON next to each artefact carries its metadata, and
the champion pointer lives at `models/{model_id}/champion.json`.
"""

from fraudnet.registry.client import ModelManifest, ModelRegistry, RegistryError

__all__ = ["ModelManifest", "ModelRegistry", "RegistryError"]
