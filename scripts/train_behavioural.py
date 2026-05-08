"""Train the behavioural LightGBM scorers (number + wallet) on Aerospike
feature snapshots and seeded ring-membership labels.

Inputs:
  - Aerospike snapshots populated by `scripts/seed_demo.py`.
  - Postgres `ring_members` for positive labels.

Outputs:
  - Two LightGBM Boosters published to the model registry as
    `behavioural-number-lgbm` and `behavioural-wallet-lgbm`. Both are
    promoted to champion at publish time.

Run:
    uv run python scripts/train_behavioural.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np

try:
    import psycopg  # type: ignore[import-not-found]
except ImportError:
    try:
        import psycopg2 as psycopg  # type: ignore[import-not-found,no-redef]
    except ImportError:
        psycopg = None  # type: ignore[assignment]

import aerospike  # type: ignore[import-not-found]


# Wire up sys.path so we can import the workspace packages without uv run.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in (
    "packages/schemas/src",
    "packages/obs/src",
    "packages/feature-client/src",
    "packages/model-registry/src",
    "services/brain-behavioural/src",
):
    p = os.path.join(ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from brain_behavioural.lgbm_scorer import (  # noqa: E402
    NUMBER_FEATURE_ORDER,
    NUMBER_MODEL_ID,
    WALLET_FEATURE_ORDER,
    WALLET_MODEL_ID,
)
from fraudnet.registry import ModelRegistry  # noqa: E402


PG_DSN = os.environ.get(
    "POSTGRES_DSN",
    "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet",
)
AS_HOSTS = os.environ.get("AEROSPIKE_HOSTS", "localhost:3010")
AS_NAMESPACE = os.environ.get("AEROSPIKE_NAMESPACE", "fraudnet")


# ------------------------------------------------------------------
# Label loading
# ------------------------------------------------------------------


def load_labels() -> tuple[set[str], set[str]]:
    """Returns (positive_msisdns, positive_wallet_ids) from the rings DB."""
    if psycopg is None:
        raise RuntimeError("psycopg not installed")
    pos_numbers: set[str] = set()
    pos_wallets: set[str] = set()
    conn = psycopg.connect(PG_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT member_kind, member_id FROM ring_members "
                "WHERE member_kind IN ('number', 'wallet')"
            )
            for kind, mid in cur.fetchall():
                if kind == "number":
                    pos_numbers.add(mid)
                elif kind == "wallet":
                    pos_wallets.add(mid)
    finally:
        conn.close()
    return pos_numbers, pos_wallets


# ------------------------------------------------------------------
# Aerospike feature pull
# ------------------------------------------------------------------


def aerospike_client():
    hosts = []
    for chunk in AS_HOSTS.split(","):
        host, _, port = chunk.strip().partition(":")
        hosts.append((host, int(port or "3000")))
    return aerospike.client({"hosts": hosts}).connect()


def scan_set(client, set_name: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []

    def cb(record_tuple) -> None:
        (key, _meta, bins) = record_tuple
        # key is (namespace, set_name, primary_key, digest)
        pk = key[2]
        if pk is None:
            return
        out.append((str(pk), bins))

    scan = client.scan(AS_NAMESPACE, set_name)
    scan.foreach(cb)
    return out


# ------------------------------------------------------------------
# Number model
# ------------------------------------------------------------------


def build_number_dataset(
    rows: list[tuple[str, dict]], positives: set[str]
) -> tuple[np.ndarray, np.ndarray]:
    X: list[list[float]] = []
    y: list[int] = []
    bin_to_attr = {
        "vel_1m": "velocity_1m",
        "vel_5m": "velocity_5m",
        "vel_1h": "velocity_1h",
        "fanout_1h": "fanout_1h",
        "imei_count": "imei_count",
        "geo_entropy": "geo_entropy",
        "sms_freq_1h": "sms_freq_1h",
    }
    for msisdn, bins in rows:
        feats: dict[str, float] = {}
        for bin_name, attr in bin_to_attr.items():
            feats[attr] = float(bins.get(bin_name, 0) or 0)
        X.append([feats[name] for name in NUMBER_FEATURE_ORDER])
        y.append(1 if msisdn in positives else 0)
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int32)


def build_wallet_dataset(
    rows: list[tuple[str, dict]], positives: set[str]
) -> tuple[np.ndarray, np.ndarray]:
    X: list[list[float]] = []
    y: list[int] = []
    for wallet_id, bins in rows:
        feats = {
            "txn_velocity_1h": float(bins.get("momo_vel_1h", 0) or 0),
            "counterparty_diversity_24h": float(bins.get("counterparty_div_24h", 0) or 0),
            "value_p95_24h": float(bins.get("value_p95_24h", 0) or 0),
        }
        X.append([feats[name] for name in WALLET_FEATURE_ORDER])
        y.append(1 if wallet_id in positives else 0)
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int32)


# ------------------------------------------------------------------
# Train + publish
# ------------------------------------------------------------------


def train(
    X: np.ndarray, y: np.ndarray, *, label: str
) -> tuple[lgb.Booster, dict[str, float]]:
    if y.sum() == 0:
        raise RuntimeError(f"no positive labels for {label}")
    pos = float(y.sum())
    neg = float(len(y) - pos)
    scale_pos_weight = max(1.0, neg / max(1.0, pos))
    train_set = lgb.Dataset(X, y)
    params = {
        "objective": "binary",
        "metric": ["binary_logloss", "auc"],
        "verbosity": -1,
        "learning_rate": 0.08,
        "num_leaves": 31,
        "min_data_in_leaf": max(2, int(len(y) * 0.02)),
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 3,
        "scale_pos_weight": scale_pos_weight,
    }
    booster = lgb.train(params, train_set, num_boost_round=80)
    proba = booster.predict(X)
    metrics = {
        "auc_train": float(_auc(y, proba)),
        "positive_count": float(pos),
        "negative_count": float(neg),
        "n_features": float(X.shape[1]),
    }
    return booster, metrics


def _auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    # Avoid sklearn dep — Mann-Whitney U formulation. Equivalent for binary AUC.
    pos = y_score[y_true == 1]
    neg = y_score[y_true == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    n_correct = 0
    n_total = 0
    for ps in pos:
        n_total += len(neg)
        n_correct += int(np.sum(ps > neg)) + 0.5 * int(np.sum(ps == neg))
    return n_correct / n_total


def publish_booster(
    registry: ModelRegistry,
    *,
    booster: lgb.Booster,
    model_id: str,
    metrics: dict[str, float],
) -> None:
    artifact = booster.model_to_string().encode("utf-8")
    version = datetime.now(timezone.utc).strftime("%Y.%m.%d-%H%M%S")
    registry.publish(
        model_id=model_id,
        version=version,
        artifact=artifact,
        artifact_format="lightgbm",
        metrics=metrics,
        notes="Trained by scripts/train_behavioural.py against seeded demo data.",
        promote_to_champion=True,
    )
    print(f"  ✓ {model_id}@{version} (auc_train={metrics.get('auc_train', 0):.3f})")


def main() -> int:
    print("==> loading labels from Postgres")
    pos_numbers, pos_wallets = load_labels()
    print(f"   positives: numbers={len(pos_numbers)} wallets={len(pos_wallets)}")

    print("==> pulling Aerospike snapshots")
    client = aerospike_client()
    try:
        number_rows = scan_set(client, "numbers")
        wallet_rows = scan_set(client, "wallets")
    finally:
        client.close()
    print(f"   rows: numbers={len(number_rows)} wallets={len(wallet_rows)}")

    Xn, yn = build_number_dataset(number_rows, pos_numbers)
    Xw, yw = build_wallet_dataset(wallet_rows, pos_wallets)
    print(f"   number dataset: {Xn.shape}, positives={int(yn.sum())}")
    print(f"   wallet dataset: {Xw.shape}, positives={int(yw.sum())}")

    print("==> training")
    booster_n, metrics_n = train(Xn, yn, label="number")
    booster_w, metrics_w = train(Xw, yw, label="wallet")

    print("==> publishing to registry")
    registry = ModelRegistry.from_env()
    publish_booster(registry, booster=booster_n, model_id=NUMBER_MODEL_ID, metrics=metrics_n)
    publish_booster(registry, booster=booster_w, model_id=WALLET_MODEL_ID, metrics=metrics_w)

    print("OK: behavioural training complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
