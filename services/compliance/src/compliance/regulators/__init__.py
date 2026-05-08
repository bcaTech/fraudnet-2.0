"""Per-regulator export formatters.

Each regulator gets its own templated pack:
  - NCA  (National Communications Authority)            → telecom incidents
  - DPC  (Data Protection Commission)                   → data breaches
  - BoG  (Bank of Ghana)                                → mobile money fraud
  - CSA  (Cyber Security Authority)                     → cyber incidents
  - GFIC (Ghana Financial Intelligence Centre)          → suspicious activity reports

A formatter takes the raw audit/alert/decision corpus for a date range
and returns:
  - a structured JSON payload for API submission, and
  - a PDF for the regulator's submission packet.

Fields the auto-fill cannot determine (incident root cause narrative,
DPO contact, regulator-side reference numbers) are flagged via
`needs_review` for the human reviewer.
"""

from compliance.regulators.base import (
    Field,
    RegulatorReport,
    REGULATOR_TEMPLATES,
)
from compliance.regulators.bog import bog_report
from compliance.regulators.csa import csa_report
from compliance.regulators.dpc import dpc_report
from compliance.regulators.gfic import gfic_report
from compliance.regulators.nca import nca_report
from compliance.regulators.pdf import render_report_pdf

# Mapping consumed by the API layer.
REPORT_BUILDERS = {
    "nca": nca_report,
    "dpc": dpc_report,
    "bog": bog_report,
    "csa": csa_report,
    "gfic": gfic_report,
}


__all__ = [
    "Field",
    "REGULATOR_TEMPLATES",
    "REPORT_BUILDERS",
    "RegulatorReport",
    "bog_report",
    "csa_report",
    "dpc_report",
    "gfic_report",
    "nca_report",
    "render_report_pdf",
]
