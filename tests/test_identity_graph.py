from osintrecon.core.engine import RunResult
from osintrecon.core.identity_graph import build_graph
from osintrecon.core.models import Entity, Finding, Identifier, IdentifierType, MatchStatus


def _identifier(value, itype):
    return Identifier(value=value, type=itype)


def _finding(source, identifier, status=MatchStatus.UNCERTAIN, source_url="https://example.com/x",
             title="", metadata=None, discovered_identifiers=None):
    return Finding(
        source=source,
        identifier=identifier,
        status=status,
        source_url=source_url,
        title=title,
        metadata=metadata or {},
        discovered_identifiers=discovered_identifiers or [],
    )


def _entity(*identifiers, findings):
    e = Entity(identifiers=set(identifiers), findings=findings)
    return e


def test_single_identifier_entity_produces_no_person_node():
    name_id = _identifier("Solo Person", IdentifierType.NAME)
    entity = _entity(name_id, findings=[_finding("wikipedia", name_id)])
    result = RunResult(entities=[entity])

    graph = build_graph(result)

    assert graph["nodes"] == []
    assert graph["edges"] == []


def test_multi_identifier_entity_produces_person_node_with_sameas_and_mentionedby():
    name_id = _identifier("Jane Doe", IdentifierType.NAME)
    user_id = _identifier("janedoe", IdentifierType.USERNAME)
    findings = [
        _finding("github_name_search", name_id, source_url="https://github.com/janedoe"),
        _finding("orcid", name_id, source_url="https://orcid.org/0000-0000-0000-0001"),
    ]
    entity = _entity(name_id, user_id, findings=findings)
    result = RunResult(entities=[entity])

    graph = build_graph(result)

    person_nodes = [n for n in graph["nodes"] if n["type"] == "Person"]
    assert len(person_nodes) == 1
    assert person_nodes[0]["label"] == "Jane Doe"
    assert person_nodes[0]["properties"]["confidence"] == entity.confidence

    same_as_edges = [e for e in graph["edges"] if e["type"] == "sameAs"]
    assert {e["target"] for e in same_as_edges} == {
        "https://github.com/janedoe", "https://orcid.org/0000-0000-0000-0001",
    }

    mentioned_by_edges = [e for e in graph["edges"] if e["type"] == "mentionedBy"]
    assert {e["target"] for e in mentioned_by_edges} == {"github_name_search", "orcid"}


def test_organization_and_university_nodes_dedup_by_normalized_name():
    name_id = _identifier("Jane Doe", IdentifierType.NAME)
    user_id = _identifier("janedoe", IdentifierType.USERNAME)
    findings = [
        _finding("github", name_id, metadata={"company": "Acme Inc."}),
        _finding("dockerhub", name_id, metadata={"company": "acme inc."}),
        _finding("openalex", name_id, metadata={"institution": "MIT"}),
    ]
    entity = _entity(name_id, user_id, findings=findings)
    result = RunResult(entities=[entity])

    graph = build_graph(result)

    org_nodes = [n for n in graph["nodes"] if n["type"] == "Organization"]
    uni_nodes = [n for n in graph["nodes"] if n["type"] == "University"]
    assert len(org_nodes) == 1
    assert org_nodes[0]["label"] == "Acme Inc."
    assert len(uni_nodes) == 1
    assert uni_nodes[0]["label"] == "MIT"

    works_at_edges = [e for e in graph["edges"] if e["type"] == "worksAt"]
    member_of_edges = [e for e in graph["edges"] if e["type"] == "memberOf"]
    assert len(works_at_edges) == 2  # both findings link to the same deduped org node
    assert len(member_of_edges) == 1


def test_crossref_finding_produces_publication_node_with_doi_id():
    name_id = _identifier("Jane Doe", IdentifierType.NAME)
    user_id = _identifier("janedoe", IdentifierType.USERNAME)
    findings = [
        _finding(
            "crossref", name_id,
            title="Crossref publication: A Real Paper",
            metadata={"doi": "10.1000/xyz", "venue": "Some Journal", "year": 2020},
        ),
    ]
    entity = _entity(name_id, user_id, findings=findings)
    result = RunResult(entities=[entity])

    graph = build_graph(result)

    pub_nodes = [n for n in graph["nodes"] if n["type"] == "Publication"]
    assert len(pub_nodes) == 1
    assert pub_nodes[0]["id"] == "publication:10.1000/xyz"
    assert pub_nodes[0]["label"] == "A Real Paper"

    author_of_edges = [e for e in graph["edges"] if e["type"] == "authorOf"]
    assert len(author_of_edges) == 1
    assert author_of_edges[0]["target"] == "publication:10.1000/xyz"


def test_crossref_finding_without_doi_falls_back_to_slug_id():
    name_id = _identifier("Jane Doe", IdentifierType.NAME)
    user_id = _identifier("janedoe", IdentifierType.USERNAME)
    findings = [
        _finding("crossref", name_id, title="Crossref publication: A Paper Without a DOI!", metadata={}),
    ]
    entity = _entity(name_id, user_id, findings=findings)
    result = RunResult(entities=[entity])

    graph = build_graph(result)

    pub_nodes = [n for n in graph["nodes"] if n["type"] == "Publication"]
    assert len(pub_nodes) == 1
    assert pub_nodes[0]["id"] == "publication:a-paper-without-a-doi"


def test_discovered_url_identifier_produces_linksto_edge():
    name_id = _identifier("Jane Doe", IdentifierType.NAME)
    user_id = _identifier("janedoe", IdentifierType.USERNAME)
    discovered_url = _identifier("https://orcid.org/0000-0000-0000-0002", IdentifierType.URL)
    findings = [
        _finding("openalex", name_id, discovered_identifiers=[discovered_url]),
    ]
    entity = _entity(name_id, user_id, findings=findings)
    result = RunResult(entities=[entity])

    graph = build_graph(result)

    links_to_edges = [e for e in graph["edges"] if e["type"] == "linksTo"]
    assert len(links_to_edges) == 1
    assert links_to_edges[0]["target"] == "https://orcid.org/0000-0000-0000-0002"


def test_not_found_and_error_findings_are_skipped():
    name_id = _identifier("Jane Doe", IdentifierType.NAME)
    user_id = _identifier("janedoe", IdentifierType.USERNAME)
    findings = [
        _finding("orcid", name_id, status=MatchStatus.NOT_FOUND, source_url="https://orcid.org/x"),
        _finding("crossref", name_id, status=MatchStatus.ERROR, source_url="https://api.crossref.org/works"),
    ]
    entity = _entity(name_id, user_id, findings=findings)
    result = RunResult(entities=[entity])

    graph = build_graph(result)

    assert graph["edges"] == []
