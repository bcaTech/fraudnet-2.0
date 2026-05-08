# fraudnet-model-registry

Object-storage-backed model registry. Models live as opaque blobs +
manifest JSON in MinIO/S3; a champion pointer per model_id selects what
serves.

## Layout

```
s3://{bucket}/
  models/{model_id}/
    versions/{version}/artifact.bin
    versions/{version}/manifest.json
    champion.json
```

## Use

```python
from fraudnet.registry import ModelRegistry

registry = ModelRegistry.from_env()
manifest = registry.publish(
    model_id="behavioural-number-lgbm",
    version="2026.05.08-120000",
    artifact=booster_bytes,
    artifact_format="lightgbm",
    metrics={"auc_train": 0.92},
)
champion = registry.champion(model_id="behavioural-number-lgbm")
artifact = registry.fetch_artifact(model_id=champion.model_id, version=champion.version)
```
