"""Common types for regulator reports.

A regulator report is a list of `Field`s grouped by `Section`. Each
Field carries:
  - the regulator's canonical field name
  - a value (auto-filled where possible)
  - `needs_review` flag if the human reviewer must complete it
  - free-text `note` for reviewer guidance

This shape is intentionally rigid — once a regulator publishes a
template, fields are not optional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    value: Any = None
    needs_review: bool = False
    note: str | None = None


@dataclass(frozen=True)
class Section:
    title: str
    fields: tuple[Field, ...]


@dataclass(frozen=True)
class RegulatorReport:
    regulator: str
    template_id: str        # e.g. "nca-telecom-fraud-incident-2025-1"
    period_start: str       # ISO date
    period_end: str
    sections: tuple[Section, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def review_field_count(self) -> int:
        return sum(1 for s in self.sections for f in s.fields if f.needs_review)


# Regulator metadata: friendly name + template currently in use.
# Updated as regulators publish revisions.
REGULATOR_TEMPLATES: dict[str, dict[str, str]] = {
    "nca": {
        "name": "National Communications Authority",
        "template_id": "nca-telecom-fraud-incident-2025-1",
        "submission_url": "https://nca.org.gh/submissions",
        "description": "Telecom fraud incident report — voice/SMS/OTT scope.",
    },
    "dpc": {
        "name": "Data Protection Commission",
        "template_id": "dpc-breach-notification-2025-1",
        "submission_url": "https://dataprotection.org.gh/breaches",
        "description": "Data breach / PII exposure notification.",
    },
    "bog": {
        "name": "Bank of Ghana",
        "template_id": "bog-mobile-money-fraud-2025-1",
        "submission_url": "https://bog.gov.gh/momo-fraud",
        "description": "Mobile money fraud and suspicious-transaction report.",
    },
    "csa": {
        "name": "Cyber Security Authority",
        "template_id": "csa-cyber-incident-2025-1",
        "submission_url": "https://csa.gov.gh/incidents",
        "description": "Cybersecurity incident report (CIRT-Gh feed).",
    },
    "gfic": {
        "name": "Ghana Financial Intelligence Centre",
        "template_id": "gfic-sar-2025-1",
        "submission_url": "https://gfic.gov.gh/sar",
        "description": "Suspicious activity report.",
    },
}
