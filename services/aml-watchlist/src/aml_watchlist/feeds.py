"""External feed parsers — UN consolidated list, OFAC SDN list.

Both feeds publish to public URLs without auth. We pull, parse into
canonical row dicts, and hand to the repo for atomic-replace.

Parser robustness: real-world feeds carry malformed entries (missing
names, weird unicode, stray HTML). The parsers skip-with-warn rather
than abort the whole refresh.
"""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from fraudnet.obs import counter, get_logger

_log = get_logger("aml_watchlist.feeds")
_PARSED = counter(
    "aml_watchlist_feed_entries_parsed_total",
    "Watchlist feed entries successfully parsed.",
    labelnames=("source",),
)
_PARSE_FAILED = counter(
    "aml_watchlist_feed_entries_failed_total",
    "Watchlist feed entries that failed to parse.",
    labelnames=("source", "reason"),
)


# ---------------------------------------------------------------------------
# UN consolidated list
# ---------------------------------------------------------------------------


def parse_un_xml(xml_text: str) -> list[dict[str, Any]]:
    """Parse the UN consolidated list XML.

    The schema has INDIVIDUAL and ENTITY top-level groups. We flatten
    both into a single list with `category` set to `sanctions`.
    """
    root = ET.fromstring(xml_text)
    out: list[dict[str, Any]] = []
    for group in ("INDIVIDUALS", "ENTITIES"):
        for entry in root.findall(f".//{group}/*"):
            try:
                ref = (entry.findtext("REFERENCE_NUMBER") or "").strip()
                first = (entry.findtext("FIRST_NAME") or "").strip()
                second = (entry.findtext("SECOND_NAME") or "").strip()
                third = (entry.findtext("THIRD_NAME") or "").strip()
                fourth = (entry.findtext("FOURTH_NAME") or "").strip()
                name = " ".join(p for p in (first, second, third, fourth) if p)
                if not name:
                    name_node = entry.findtext("NAME") or entry.findtext("FULL_NAME")
                    name = (name_node or "").strip()
                if not name:
                    _PARSE_FAILED.labels(source="un", reason="no_name").inc()
                    continue
                aliases = [
                    (a.text or "").strip()
                    for a in entry.findall(".//INDIVIDUAL_ALIAS/ALIAS_NAME")
                    if a.text
                ]
                aliases += [
                    (a.text or "").strip()
                    for a in entry.findall(".//ENTITY_ALIAS/ALIAS_NAME")
                    if a.text
                ]
                country = (entry.findtext(".//COUNTRY/VALUE") or "").strip() or None
                out.append(
                    {
                        "external_id": ref or None,
                        "category": "sanctions",
                        "name": name,
                        "aliases": [a for a in aliases if a],
                        "country": country,
                        "metadata": {"feed": "un"},
                    }
                )
                _PARSED.labels(source="un").inc()
            except Exception as exc:  # noqa: BLE001
                _PARSE_FAILED.labels(source="un", reason="exception").inc()
                _log.warning("aml_watchlist.un.parse_failed", error=str(exc))
                continue
    return out


# ---------------------------------------------------------------------------
# OFAC SDN list
# ---------------------------------------------------------------------------


# OFAC SDN.csv columns (no header — positional). The columns are
# documented at https://sdnsearch.ofac.treas.gov/.
_OFAC_COLS = [
    "ent_num", "name", "type", "program", "title", "call_sign",
    "vessel_type", "tonnage", "grt", "vessel_flag", "vessel_owner", "remarks",
]


def parse_ofac_csv(csv_text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    reader = csv.reader(io.StringIO(csv_text))
    for row in reader:
        try:
            data = dict(zip(_OFAC_COLS, row + [""] * (len(_OFAC_COLS) - len(row))))
            name = (data.get("name") or "").strip(' "')
            if not name:
                _PARSE_FAILED.labels(source="ofac", reason="no_name").inc()
                continue
            ent_type = (data.get("type") or "").strip(' "').lower()
            # Skip vessel / aircraft entries; they're not relevant to MoMo.
            if ent_type in ("vessel", "aircraft"):
                continue
            program = (data.get("program") or "").strip(' "')
            country = None
            remarks = (data.get("remarks") or "").strip(' "')
            # OFAC encodes country in remarks ("nationality is X").
            if "nationality" in remarks.lower():
                # cheap extract — best-effort.
                lower = remarks.lower()
                idx = lower.find("nationality")
                country = remarks[idx + len("nationality"):].split(";", 1)[0].strip()
            out.append(
                {
                    "external_id": (data.get("ent_num") or "").strip(' "') or None,
                    "category": "sanctions",
                    "name": name,
                    "aliases": [],
                    "country": country,
                    "metadata": {"feed": "ofac", "program": program},
                }
            )
            _PARSED.labels(source="ofac").inc()
        except Exception as exc:  # noqa: BLE001
            _PARSE_FAILED.labels(source="ofac", reason="exception").inc()
            _log.warning("aml_watchlist.ofac.parse_failed", error=str(exc))
            continue
    return out


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


async def fetch_text(url: str, *, timeout_s: float = 30.0) -> str:
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
