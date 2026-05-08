"""Feed parsers — UN XML, OFAC CSV. Robust against malformed entries."""

from __future__ import annotations

from aml_watchlist.feeds import parse_ofac_csv, parse_un_xml


_UN_XML = """<?xml version="1.0"?>
<CONSOLIDATED_LIST>
  <INDIVIDUALS>
    <INDIVIDUAL>
      <REFERENCE_NUMBER>QDi.001</REFERENCE_NUMBER>
      <FIRST_NAME>Osama</FIRST_NAME>
      <SECOND_NAME>Bin</SECOND_NAME>
      <THIRD_NAME>Laden</THIRD_NAME>
      <COUNTRY><VALUE>SA</VALUE></COUNTRY>
      <INDIVIDUAL_ALIAS>
        <ALIAS_NAME>Usama</ALIAS_NAME>
      </INDIVIDUAL_ALIAS>
    </INDIVIDUAL>
    <INDIVIDUAL>
      <REFERENCE_NUMBER>QDi.002</REFERENCE_NUMBER>
      <!-- no name — should be skipped -->
    </INDIVIDUAL>
  </INDIVIDUALS>
  <ENTITIES>
    <ENTITY>
      <REFERENCE_NUMBER>QDe.001</REFERENCE_NUMBER>
      <FIRST_NAME>Al-Qaida</FIRST_NAME>
    </ENTITY>
  </ENTITIES>
</CONSOLIDATED_LIST>
"""


def test_parse_un_xml_extracts_individuals_and_entities() -> None:
    rows = parse_un_xml(_UN_XML)
    names = {r["name"] for r in rows}
    assert "Osama Bin Laden" in names
    assert "Al-Qaida" in names
    # Empty-name row was skipped.
    assert len(rows) == 2


def test_parse_un_xml_carries_aliases_and_country() -> None:
    rows = parse_un_xml(_UN_XML)
    obl = next(r for r in rows if r["name"] == "Osama Bin Laden")
    assert "Usama" in obl["aliases"]
    assert obl["country"] == "SA"


_OFAC_CSV = """1,"SMITH, John","individual","SDGT","","","","","","","","Other AKA: Johnny Smith"
2,"BAD CORP","entity","UKRAINE-EO13660","","","","","","","",""
3,"USS Vessel","vessel","CUBA","","","","","","","",""
"""


def test_parse_ofac_csv_skips_vessels() -> None:
    rows = parse_ofac_csv(_OFAC_CSV)
    names = {r["name"] for r in rows}
    assert "SMITH, John" in names
    assert "BAD CORP" in names
    assert "USS Vessel" not in names


def test_parse_ofac_csv_carries_external_id_and_program() -> None:
    rows = parse_ofac_csv(_OFAC_CSV)
    smith = next(r for r in rows if r["name"] == "SMITH, John")
    assert smith["external_id"] == "1"
    assert smith["metadata"]["program"] == "SDGT"
