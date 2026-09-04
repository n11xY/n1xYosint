"""Derives a typed identity graph (nodes + edges) from a single run's
already-correlated entities and findings.

This is deliberately NOT a persistent, cross-run graph database -- no new
storage layer, no schema migrations. It's a structured *view* over data
`engine.py` already collected in this one run, the same posture as
entity_scoring.py: derive and annotate, never invent data that isn't
actually there.

Node types: Person, Organization, University, Publication.
Edge types: sameAs, mentionedBy, worksAt, memberOf, authorOf, linksTo.

`sameAs` and `linksTo` point at a literal URL string rather than another
typed node -- a raw profile/link URL isn't one of the four node types
above, and schema.org itself uses `sameAs` this way (Person.sameAs -> a
list of URLs that identify the same person), so this mirrors an existing
convention rather than inventing a fifth node type just to hold URLs.
`mentionedBy` points at a literal plugin-family name for the same reason.

Only entities with 2+ identifiers become Person nodes -- the same scope
renderer.py's entity panel and exporters.py's JSON `entities` list already
use (a single-seed entity has nothing correlated to graph).
"""
from __future__ import annotations

import re
from typing import Any

from osintrecon.core.engine import RunResult
from osintrecon.core.models import Finding, IdentifierType, MatchStatus
from osintrecon.core.scoring import plugin_family

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str, max_len: int = 60) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:max_len] or "untitled"


def _get_or_create(nodes: dict[str, dict], node_id: str, node_type: str, label: str, properties: dict[str, Any]) -> str:
    if node_id not in nodes:
        nodes[node_id] = {"id": node_id, "type": node_type, "label": label, "properties": properties}
    return node_id


def build_graph(result: RunResult) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    for entity in result.entities:
        if len(entity.identifiers) < 2:
            continue

        person_id = f"person:{entity.entity_id}"
        name_identifier = next((i.value for i in entity.identifiers if i.type == IdentifierType.NAME), None)
        person_label = name_identifier or entity.entity_id
        _get_or_create(nodes, person_id, "Person", person_label, {
            "identifiers": [
                {"type": i.type.value, "value": i.value}
                for i in sorted(entity.identifiers, key=lambda i: (i.type.value, i.value))
            ],
            "confidence": entity.confidence,
            "reasons": entity.reasons,
        })

        seen_families: set[str] = set()

        for f in entity.findings:
            if f.status in (MatchStatus.NOT_FOUND, MatchStatus.ERROR):
                continue

            if f.source_url:
                edges.append({"source": person_id, "target": f.source_url, "type": "sameAs", "properties": {"via": f.source}})

            family = plugin_family(f.source)
            if family not in seen_families:
                seen_families.add(family)
                edges.append({"source": person_id, "target": family, "type": "mentionedBy", "properties": {}})

            company = f.metadata.get("company")
            if company and isinstance(company, str) and company.strip():
                org_id = f"org:{company.strip().lower()}"
                _get_or_create(nodes, org_id, "Organization", company.strip(), {})
                edges.append({"source": person_id, "target": org_id, "type": "worksAt", "properties": {"via": f.source}})

            institution = f.metadata.get("institution")
            if institution and isinstance(institution, str) and institution.strip():
                uni_id = f"university:{institution.strip().lower()}"
                _get_or_create(nodes, uni_id, "University", institution.strip(), {})
                edges.append({"source": person_id, "target": uni_id, "type": "memberOf", "properties": {"via": f.source}})

            if f.source == "crossref":
                _add_publication_edge(nodes, edges, person_id, f)

            for discovered in f.discovered_identifiers:
                if discovered.type == IdentifierType.URL:
                    edges.append({"source": person_id, "target": discovered.value, "type": "linksTo", "properties": {"via": f.source}})

    return {"nodes": list(nodes.values()), "edges": edges}


def _add_publication_edge(nodes: dict[str, dict], edges: list[dict], person_id: str, f: Finding) -> None:
    title = f.title.removeprefix("Crossref publication: ") if f.title else ""
    if not title:
        return
    doi = f.metadata.get("doi")
    pub_id = f"publication:{doi}" if doi else f"publication:{_slug(title)}"
    _get_or_create(nodes, pub_id, "Publication", title, {
        "doi": doi,
        "venue": f.metadata.get("venue"),
        "year": f.metadata.get("year"),
    })
    edges.append({"source": person_id, "target": pub_id, "type": "authorOf", "properties": {}})
