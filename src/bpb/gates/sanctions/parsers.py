"""Parsers for the four government sanctions list formats.

Caveat (documented deliberately, since this was built without a live network call
against any of these endpoints — see the "don't spend credits/quota" constraint on
this build): column positions and XML element names below follow each publisher's
long-documented schema as of this writing, but these are the one piece of this
codebase most likely to need reconciling against an actual downloaded sample before
the first live `bpb refresh-sanctions` run. Parsing is defensive (skip malformed
rows/elements rather than crash) precisely because of that uncertainty.

Each parser returns list[SanctionsEntry] (see matcher.py) — primary names only,
no alias expansion in this build (OFAC's alias file (ALT.CSV/*_ADVANCED.XML) and
the other lists' alias fields are a documented future enhancement, not fetched
here — see docs/compliance.md).
"""

from __future__ import annotations

import csv
import io
import logging

from lxml import etree

from bpb.gates.sanctions.matcher import SanctionsEntry

logger = logging.getLogger(__name__)


def parse_ofac_sdn_csv(content: bytes) -> list[SanctionsEntry]:
    """SDN.CSV: no header row. Columns (0-indexed): ent_num, SDN_Name, SDN_Type,
    Program, Title, Call_Sign, Vess_type, Tonnage, GRT, Vess_flag, Vess_owner, Remarks.
    """
    return _parse_ofac_style_csv(content, list_source="ofac_sdn")


def parse_ofac_consolidated_csv(content: bytes) -> list[SanctionsEntry]:
    """CONSOLIDATED.CSV: same column layout as SDN.CSV."""
    return _parse_ofac_style_csv(content, list_source="ofac_consolidated")


def _parse_ofac_style_csv(content: bytes, *, list_source: str) -> list[SanctionsEntry]:
    text = content.decode("utf-8-sig", errors="replace")
    entries: list[SanctionsEntry] = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 4:
            continue
        name = row[1].strip().strip('"')
        entity_type = row[2].strip().strip('"') or None
        program = row[3].strip().strip('"') or None
        if not name or name == "-0-":  # OFAC uses "-0-" as a null placeholder
            continue
        entries.append(
            SanctionsEntry(
                list_source=list_source, name=name, entity_type=entity_type, program=program
            )
        )
    return entries


def parse_uksl_xml(content: bytes) -> list[SanctionsEntry]:
    """UK Sanctions List (FCDO). Each designation is a <Designation> with one or
    more <Name> children carrying <Name6> (primary full name) or individual
    <Name1>.."Name6" parts we join when a single full-name field isn't present.
    """
    entries: list[SanctionsEntry] = []
    try:
        root = etree.fromstring(content)
    except etree.XMLSyntaxError:
        logger.warning("UKSL XML failed to parse", exc_info=True)
        return entries

    for designation in root.iter():
        if not _local_name(designation).lower().endswith("designation"):
            continue
        regime = _first_text(designation, "UNReferenceNumber", "RegimeName", "UKStatementOfReasons")
        for name_el in designation.iter():
            if _local_name(name_el) != "Name":
                continue
            full = _first_text(name_el, "Name6")
            if not full:
                parts = [_first_text(name_el, f"Name{i}") for i in range(1, 6)]
                full = " ".join(p for p in parts if p)
            if full:
                entries.append(
                    SanctionsEntry(list_source="uksl", name=full.strip(), program=regime)
                )
    return entries


def parse_canada_sema_xml(content: bytes) -> list[SanctionsEntry]:
    """Canada's Consolidated Autonomous Sanctions List. Each record is a
    <Row>/<Entity> with a <LastName>/<GivenName> pair (individuals) or an
    <Entity>/<Name> (entities)."""
    entries: list[SanctionsEntry] = []
    try:
        root = etree.fromstring(content)
    except etree.XMLSyntaxError:
        logger.warning("Canada SEMA XML failed to parse", exc_info=True)
        return entries

    for record in root.iter():
        tag = _local_name(record).lower()
        if tag not in ("record", "individual", "entity"):
            continue
        last = _first_text(record, "LastName", "Last_Name")
        given = _first_text(record, "GivenName", "Given_Name", "FirstName")
        entity_name = _first_text(record, "Entity", "EntityName", "Name")
        program = _first_text(record, "Schedule", "Item")
        if last or given:
            full = " ".join(p for p in (given, last) if p)
            entries.append(SanctionsEntry(list_source="canada_sema", name=full, program=program))
        elif entity_name:
            entries.append(
                SanctionsEntry(
                    list_source="canada_sema",
                    name=entity_name,
                    entity_type="entity",
                    program=program,
                )
            )
    return entries


def parse_un_consolidated_xml(content: bytes) -> list[SanctionsEntry]:
    """UN Security Council Consolidated List. <INDIVIDUALS><INDIVIDUAL> has
    FIRST_NAME/SECOND_NAME/THIRD_NAME/FOURTH_NAME; <ENTITIES><ENTITY> has FIRST_NAME
    (used as the entity's full name)."""
    entries: list[SanctionsEntry] = []
    try:
        root = etree.fromstring(content)
    except etree.XMLSyntaxError:
        logger.warning("UN consolidated XML failed to parse", exc_info=True)
        return entries

    for individual in root.iter():
        if _local_name(individual) != "INDIVIDUAL":
            continue
        name_parts = [
            _first_text(individual, f"{n}_NAME")
            for n in ("FIRST", "SECOND", "THIRD", "FOURTH")
        ]
        full = " ".join(p for p in name_parts if p)
        list_type = _first_text(individual, "UN_LIST_TYPE")
        if full:
            entries.append(
                SanctionsEntry(list_source="un_consolidated", name=full, program=list_type)
            )

    for entity in root.iter():
        if _local_name(entity) != "ENTITY":
            continue
        name = _first_text(entity, "FIRST_NAME", "NAME")
        list_type = _first_text(entity, "UN_LIST_TYPE")
        if name:
            entries.append(
                SanctionsEntry(
                    list_source="un_consolidated",
                    name=name,
                    entity_type="entity",
                    program=list_type,
                )
            )
    return entries


def _local_name(element) -> str:
    tag = element.tag
    return tag.split("}", 1)[-1] if isinstance(tag, str) else ""


def _first_text(element, *tag_names: str) -> str | None:
    for tag in tag_names:
        for child in element.iter():
            if _local_name(child) == tag and child.text and child.text.strip():
                return child.text.strip()
    return None


PARSERS = {
    "ofac_sdn": parse_ofac_sdn_csv,
    "ofac_consolidated": parse_ofac_consolidated_csv,
    "uksl": parse_uksl_xml,
    "canada_sema": parse_canada_sema_xml,
    "un_consolidated": parse_un_consolidated_xml,
}
