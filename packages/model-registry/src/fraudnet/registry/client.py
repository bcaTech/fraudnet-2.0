"""S3/MinIO-backed model registry.

Layout under `s3://{bucket}/models/{model_id}/`:

    versions/{version}/artifact.bin      — opaque model bytes
    versions/{version}/manifest.json     — metadata (created_at, metrics, hash)
    champion.json                        — { "model_id", "version" } pointer

The registry is read-mostly at serving time; brain-* services hold a
cached champion handle in-process and refresh on a TTL or on demand.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import asdict, dataclass, field
from time import time
from typing import Any

from fraudnet.obs import get_logger

_log = get_logger("fraudnet.registry")


class RegistryError(Exception):
    """Generic registry failure."""


@dataclass(frozen=True)
class ModelManifest:
    model_id: str
    version: str
    created_at_ms: int
    artifact_sha256: str
    artifact_size_bytes: int
    artifact_format: str  # 'lightgbm', 'sklearn-pickle', 'tfidf-lr-pickle', etc.
    metrics: dict[str, float] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ModelManifest:
        return cls(
            model_id=raw["model_id"],
            version=raw["version"],
            created_at_ms=int(raw["created_at_ms"]),
            artifact_sha256=raw["artifact_sha256"],
            artifact_size_bytes=int(raw["artifact_size_bytes"]),
            artifact_format=raw["artifact_format"],
            metrics={k: float(v) for k, v in raw.get("metrics", {}).items()},
            notes=raw.get("notes", ""),
        )


class ModelRegistry:
    """S3/MinIO-backed registry. Boto3 is the only required dep."""

    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        bucket: str = "fraudnet-models",
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        try:
            import boto3
            from botocore.client import Config
        except ImportError as exc:
            raise RegistryError("boto3 is required for ModelRegistry") from exc
        self._bucket = bucket
        kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": region,
            "config": Config(signature_version="s3v4"),
        }
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if access_key:
            kwargs["aws_access_key_id"] = access_key
        if secret_key:
            kwargs["aws_secret_access_key"] = secret_key
        self._s3 = boto3.client(**kwargs)

    @classmethod
    def from_env(cls) -> ModelRegistry:
        return cls(
            endpoint_url=os.environ.get("MODEL_REGISTRY_ENDPOINT", "http://localhost:9000"),
            bucket=os.environ.get("MODEL_REGISTRY_BUCKET", "fraudnet-models"),
            access_key=os.environ.get("MODEL_REGISTRY_ACCESS_KEY", "fraudnet"),
            secret_key=os.environ.get("MODEL_REGISTRY_SECRET_KEY", "fraudnet_dev_minio"),
            region=os.environ.get("MODEL_REGISTRY_REGION", "us-east-1"),
        )

    # -- write ---------------------------------------------------------

    def publish(
        self,
        *,
        model_id: str,
        version: str,
        artifact: bytes,
        artifact_format: str,
        metrics: dict[str, float] | None = None,
        notes: str = "",
        promote_to_champion: bool = True,
    ) -> ModelManifest:
        sha = hashlib.sha256(artifact).hexdigest()
        manifest = ModelManifest(
            model_id=model_id,
            version=version,
            created_at_ms=int(time() * 1000),
            artifact_sha256=sha,
            artifact_size_bytes=len(artifact),
            artifact_format=artifact_format,
            metrics=dict(metrics or {}),
            notes=notes,
        )
        artifact_key = self._artifact_key(model_id, version)
        manifest_key = self._manifest_key(model_id, version)
        self._put_object(artifact_key, artifact)
        self._put_object(
            manifest_key,
            json.dumps(manifest.to_dict()).encode(),
            content_type="application/json",
        )
        if promote_to_champion:
            self.promote(model_id=model_id, version=version)
        _log.info(
            "registry.published",
            model_id=model_id,
            version=version,
            sha256=sha[:12],
            promoted=promote_to_champion,
        )
        return manifest

    def promote(self, *, model_id: str, version: str) -> None:
        # Verify the manifest exists before flipping the pointer.
        self.fetch_manifest(model_id=model_id, version=version)
        pointer = json.dumps({"model_id": model_id, "version": version}).encode()
        self._put_object(
            self._champion_key(model_id), pointer, content_type="application/json"
        )
        _log.info("registry.promoted", model_id=model_id, version=version)

    # -- read ----------------------------------------------------------

    def champion(self, *, model_id: str) -> ModelManifest:
        raw = self._get_object_bytes(self._champion_key(model_id))
        if raw is None:
            raise RegistryError(f"no champion for model_id={model_id}")
        pointer = json.loads(raw.decode())
        return self.fetch_manifest(
            model_id=pointer["model_id"], version=pointer["version"]
        )

    def fetch_manifest(self, *, model_id: str, version: str) -> ModelManifest:
        raw = self._get_object_bytes(self._manifest_key(model_id, version))
        if raw is None:
            raise RegistryError(f"no manifest for {model_id}@{version}")
        return ModelManifest.from_dict(json.loads(raw.decode()))

    def fetch_artifact(self, *, model_id: str, version: str) -> bytes:
        raw = self._get_object_bytes(self._artifact_key(model_id, version))
        if raw is None:
            raise RegistryError(f"no artifact for {model_id}@{version}")
        return raw

    def list_versions(self, *, model_id: str) -> list[ModelManifest]:
        prefix = f"models/{model_id}/versions/"
        out: list[ModelManifest] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = item["Key"]
                if not key.endswith("manifest.json"):
                    continue
                raw = self._get_object_bytes(key)
                if raw is None:
                    continue
                out.append(ModelManifest.from_dict(json.loads(raw.decode())))
        out.sort(key=lambda m: m.created_at_ms, reverse=True)
        return out

    # -- internals -----------------------------------------------------

    def _put_object(
        self, key: str, body: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        try:
            self._ensure_bucket()
            self._s3.put_object(
                Bucket=self._bucket, Key=key, Body=io.BytesIO(body), ContentType=content_type
            )
        except Exception as exc:  # noqa: BLE001
            raise RegistryError(f"put failed for {key}: {exc}") from exc

    def _get_object_bytes(self, key: str) -> bytes | None:
        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()  # type: ignore[no-any-return]
        except self._s3.exceptions.NoSuchKey:
            return None
        except Exception as exc:  # noqa: BLE001
            # boto exceptions can wrap NoSuchKey opaquely; treat 404 as None.
            err = getattr(exc, "response", {}).get("Error", {})
            if err.get("Code") in {"NoSuchKey", "404"}:
                return None
            raise RegistryError(f"get failed for {key}: {exc}") from exc

    def _ensure_bucket(self) -> None:
        try:
            self._s3.head_bucket(Bucket=self._bucket)
        except Exception:  # noqa: BLE001
            try:
                self._s3.create_bucket(Bucket=self._bucket)
            except Exception:  # noqa: BLE001 — bucket may have been created by another writer.
                pass

    @staticmethod
    def _artifact_key(model_id: str, version: str) -> str:
        return f"models/{model_id}/versions/{version}/artifact.bin"

    @staticmethod
    def _manifest_key(model_id: str, version: str) -> str:
        return f"models/{model_id}/versions/{version}/manifest.json"

    @staticmethod
    def _champion_key(model_id: str) -> str:
        return f"models/{model_id}/champion.json"
