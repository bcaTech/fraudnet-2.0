"""Train the brain-content TF-IDF + Logistic Regression classifier.

Uses a small seed corpus of smishing templates and benign messages. The
fitted Pipeline is published as a single pickle to the model registry as
`content-tfidf-lr` and promoted to champion.

Run:
    uv run python scripts/train_content.py
"""

from __future__ import annotations

import os
import pickle
import sys
from datetime import datetime, timezone

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline


# Wire up workspace imports.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("packages/obs/src", "packages/model-registry/src"):
    p = os.path.join(ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from fraudnet.registry import ModelRegistry  # noqa: E402

CONTENT_MODEL_ID = "content-tfidf-lr"

# Seed corpus. Real production training uses MoMo support tickets + the
# customer-report queue; this gets the model bootstrapped with a sensible
# baseline.
SMISHING = [
    "Congratulations! You have won GHS 5000. Click here to claim your prize",
    "URGENT: Your MoMo account has been suspended. Verify now at bit.ly/momo-verify",
    "You won the MTN lottery! Send your wallet PIN to claim GHS 10000",
    "Your account will be deactivated. Click http://winaprize.example to reactivate",
    "MoMo: Please confirm your details urgently to avoid suspension",
    "Dear customer, claim your unclaimed prize of GHS 8000 within 24 hours",
    "Your number is the lucky winner. Verify your wallet to receive GHS 5000",
    "Congratulations winner! Kindly send PIN to claim your prize",
    "Account suspended due to suspicious activity. Click to verify identity",
    "FINAL NOTICE: claim your GHS prize now or forfeit. Click link",
    "We have credited GHS 1500 to your wallet. Verify by sending PIN",
    "Your account has been hacked. Send your PIN to secure",
    "MTN bonus: send 500 to 5050 to receive 5000 free airtime",
    "Tax refund GHS 3500 ready. Click bit.ly/scam to claim within 24 hours",
    "You are 1 of 5 winners. Send your wallet ID and PIN to claim",
    "URGENT! Confirm your wallet credentials to avoid suspension",
    "Lottery winner notification: claim your prize at scam-momo.com",
    "Your MoMo subscription will expire today. Verify here to extend",
    "Bank credit alert: kindly verify your details to receive GHS 4500",
    "Last chance: claim GHS 7500 prize before midnight",
]

BENIGN = [
    "Your transfer of GHS 50 to John was successful",
    "Hi mum, will send you the money tonight after work",
    "Your airtime balance is GHS 5.50",
    "Reminder: your appointment is tomorrow at 10am",
    "MTN: thank you for your subscription. Enjoy your data bundle",
    "Your data bundle has been activated successfully",
    "Hello, please come pick the kids at 3pm",
    "Meeting rescheduled to Thursday 2pm. See you there",
    "Power outage in your area until 6pm. Sorry for the inconvenience",
    "Happy birthday! Hope you have a wonderful day",
    "Your delivery has arrived at the office",
    "Class is cancelled today. Please pass on the message",
    "Lunch at 1? I'll be at the usual place",
    "Got your message, will call you back in 30 minutes",
    "Please send the invoice when you get a chance",
    "Salary credited GHS 2400. Reference SAL-04-2026",
    "Welcome to our service. We hope you enjoy",
    "Your monthly statement is now available online",
    "Driver is 5 minutes away from pickup point",
    "Match starts at 7pm tonight, dont miss it",
    "Your tax filing has been received. Reference TX-2026-04",
]


def build_dataset() -> tuple[list[str], np.ndarray]:
    docs = SMISHING + BENIGN
    labels = np.array([1] * len(SMISHING) + [0] * len(BENIGN))
    return docs, labels


def train_pipeline(docs: list[str], y: np.ndarray) -> tuple[Pipeline, dict[str, float]]:
    pipeline = Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    min_df=1,
                    max_df=0.95,
                    sublinear_tf=True,
                    lowercase=True,
                ),
            ),
            (
                "lr",
                LogisticRegression(
                    C=2.0,
                    class_weight="balanced",
                    max_iter=1000,
                    solver="liblinear",
                ),
            ),
        ]
    )
    pipeline.fit(docs, y)
    proba = pipeline.predict_proba(docs)[:, 1]
    auc = _auc(y, proba)
    metrics = {
        "auc_train": float(auc),
        "n_documents": float(len(docs)),
        "positive_count": float(y.sum()),
    }
    return pipeline, metrics


def _auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    pos = y_score[y_true == 1]
    neg = y_score[y_true == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    n_total = 0
    n_correct = 0.0
    for ps in pos:
        n_total += len(neg)
        n_correct += float(np.sum(ps > neg)) + 0.5 * float(np.sum(ps == neg))
    return n_correct / n_total


def main() -> int:
    print("==> building dataset")
    docs, y = build_dataset()
    print(f"   {len(docs)} documents, positives={int(y.sum())}")

    print("==> training tf-idf + logistic regression")
    pipeline, metrics = train_pipeline(docs, y)
    print(f"   metrics={metrics}")

    print("==> publishing to registry")
    artifact = pickle.dumps(pipeline)
    version = datetime.now(timezone.utc).strftime("%Y.%m.%d-%H%M%S")
    registry = ModelRegistry.from_env()
    registry.publish(
        model_id=CONTENT_MODEL_ID,
        version=version,
        artifact=artifact,
        artifact_format="tfidf-lr-pickle",
        metrics=metrics,
        notes="Trained by scripts/train_content.py on the bootstrap smishing corpus.",
        promote_to_champion=True,
    )
    print(f"  ✓ {CONTENT_MODEL_ID}@{version} (auc_train={metrics.get('auc_train', 0):.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
