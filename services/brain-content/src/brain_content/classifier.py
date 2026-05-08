"""Phase 1 content classifier — keyword/pattern heuristic.

The interface (`ContentClassifier`) is what Phase 2 replaces with a
fine-tuned sentence-transformer + classifier head, behind the same
signature. Body access is purpose-gated: when the body is None, the
classifier falls back to template_hash / body_hash signals.

Heuristic features (Phase 1):
  - Known scam keywords (lottery, prize, urgent, deactivated, click)
  - URL presence + reputation hit from url_reputation
  - Template hash match against a known-bad list
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from time import time
from typing import Iterable
from uuid import uuid4

from fraudnet.schemas.signals import SignalEventV1
from fraudnet.schemas.types import EntityKind, RiskScore, Severity, Subject
from brain_content.ott_domain_analysis import OttDomainAnalyser
from brain_content.url_reputation import ReputationLookup, domain_of

MODEL_ID = "content-heuristic"
MODEL_VERSION = "0.1.0"

_URL_RE = re.compile(r"https?://[^\s<>\"')\]}]+", re.IGNORECASE)

# Seed scam-keyword set. Lowercase. Tuned against the kinds of templates
# we already see in MoMo prompts and the smishing profile in the spec.
_SCAM_KEYWORDS = frozenset(
    {
        "lottery",
        "prize",
        "winner",
        "claim",
        "urgent",
        "deactivated",
        "verify",
        "click",
        "suspended",
        "unauthorized",
        "kindly",
        "momo",
        "ghs",
        "won",
        "congratulations",
    }
)
# Threshold of distinct scam-keywords that triggers the keyword signal.
_SCAM_KEYWORD_THRESHOLD = 3


@dataclass(frozen=True)
class ClassificationResult:
    score: RiskScore
    signal_kind: str | None
    severity: Severity
    evidence: dict[str, str | int | float | bool]
    matched_urls: tuple[str, ...] = field(default_factory=tuple)


class ContentClassifier(ABC):
    @abstractmethod
    def classify(
        self,
        *,
        body: str | None,
        body_hash: str | None,
        template_hash: str | None,
    ) -> ClassificationResult: ...


class HeuristicContentClassifier(ContentClassifier):
    def __init__(
        self,
        *,
        url_reputation: ReputationLookup,
        bad_template_hashes: Iterable[str] = (),
        bad_body_hashes: Iterable[str] = (),
        ott_analyser: OttDomainAnalyser | None = None,
    ) -> None:
        self._urls = url_reputation
        self._bad_templates = {t.lower() for t in bad_template_hashes}
        self._bad_bodies = {b.lower() for b in bad_body_hashes}
        # OTT analyser: when set, the classifier checks every URL in the
        # body for brand-lookalike / shortener / NRD before falling back
        # to keyword heuristics. Lookalike + shortener is a HIGH signal
        # even without a url-reputation hit.
        self._ott = ott_analyser

    def classify(
        self,
        *,
        body: str | None,
        body_hash: str | None,
        template_hash: str | None,
    ) -> ClassificationResult:
        evidence: dict[str, str | int | float | bool] = {}

        # Fast paths first — known-bad hash lookup is sub-millisecond.
        if body_hash and body_hash.lower() in self._bad_bodies:
            evidence["match"] = "body_hash"
            return ClassificationResult(
                score=_score(0.98, evidence),
                signal_kind="sms.known_bad_body",
                severity=Severity.CRITICAL,
                evidence=evidence,
            )
        if template_hash and template_hash.lower() in self._bad_templates:
            evidence["match"] = "template_hash"
            return ClassificationResult(
                score=_score(0.93, evidence),
                signal_kind="sms.known_bad_template",
                severity=Severity.HIGH,
                evidence=evidence,
            )

        # Slow paths require the body. If we don't have it, return a low
        # score and rely on hash-based downstream signals.
        if not body:
            return ClassificationResult(
                score=_score(0.0, evidence),
                signal_kind=None,
                severity=Severity.LOW,
                evidence=evidence,
            )

        urls = tuple(_URL_RE.findall(body))
        evidence["url_count"] = len(urls)

        # Check each URL against the reputation list. First hit wins.
        for url in urls:
            v = self._urls.check(url)
            if v is not None:
                evidence["url_match"] = url
                evidence["url_category"] = v.category or "unknown"
                evidence["url_source"] = v.source
                return ClassificationResult(
                    score=_score(v.confidence, evidence),
                    signal_kind="sms.malicious_url",
                    severity=Severity.HIGH if v.confidence > 0.8 else Severity.MEDIUM,
                    evidence=evidence,
                    matched_urls=(url,),
                )

        # OTT heuristic over each URL's domain — catches lookalikes / shorteners
        # that haven't yet propagated to url_reputation. Brand-lookalike +
        # shortener is the highest-value novel-content signal.
        if self._ott is not None:
            for url in urls:
                v = self._ott.analyse(domain_of(url))
                if v.is_suspicious:
                    evidence["url_match"] = url
                    evidence.update(v.to_evidence())
                    multi = v.is_brand_lookalike and v.is_url_shortener
                    if multi or v.is_brand_lookalike:
                        confidence = 0.85 if multi else 0.78
                        return ClassificationResult(
                            score=_score(confidence, evidence),
                            signal_kind="sms.ott_lookalike",
                            severity=Severity.HIGH,
                            evidence=evidence,
                            matched_urls=(url,),
                        )
                    if v.is_url_shortener:
                        return ClassificationResult(
                            score=_score(0.55, evidence),
                            signal_kind="sms.url_shortener_abuse",
                            severity=Severity.MEDIUM,
                            evidence=evidence,
                            matched_urls=(url,),
                        )

        # Keyword heuristic
        body_l = body.lower()
        matches = {kw for kw in _SCAM_KEYWORDS if kw in body_l}
        evidence["scam_keyword_hits"] = len(matches)
        if matches:
            evidence["scam_keywords"] = ",".join(sorted(matches))

        if len(matches) >= _SCAM_KEYWORD_THRESHOLD:
            return ClassificationResult(
                score=_score(0.75, evidence),
                signal_kind="sms.template_smishing",
                severity=Severity.MEDIUM,
                evidence=evidence,
            )

        # URLs but no rep hit — soft signal worth surfacing for review.
        if urls:
            return ClassificationResult(
                score=_score(0.35, evidence),
                signal_kind=None,  # not a dispatch-worthy signal alone
                severity=Severity.LOW,
                evidence=evidence,
                matched_urls=tuple(domain_of(u) for u in urls),
            )

        return ClassificationResult(
            score=_score(0.05, evidence),
            signal_kind=None,
            severity=Severity.LOW,
            evidence=evidence,
        )


def _score(value: float, evidence: dict[str, str | int | float | bool]) -> RiskScore:
    return RiskScore(
        value=max(0.0, min(1.0, value)),
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        computed_at_ms=int(time() * 1000),
        feature_attribution={
            k: float(v) for k, v in evidence.items() if isinstance(v, (int, float))
        },
    )


def to_signal(
    *,
    result: ClassificationResult,
    sender_msisdn: str,
    source: str,
    tenant_id: str = "mtn-ghana",
) -> SignalEventV1 | None:
    if result.signal_kind is None:
        return None
    now_ms = int(time() * 1000)
    suppression_key = f"{tenant_id}:number:{sender_msisdn}:{result.signal_kind}"
    return SignalEventV1(
        event_id=f"sig_{uuid4().hex[:24]}",
        event_ts_ms=now_ms,
        ingest_ts_ms=now_ms,
        source=source,
        tenant_id=tenant_id,
        signal_kind=result.signal_kind,
        subject=Subject(kind=EntityKind.NUMBER, id=sender_msisdn),
        score=result.score,
        severity=result.severity,
        evidence=result.evidence,
        suppression_key=suppression_key,
    )
