from osintrecon.core.engine import Engine
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus


def _finding_with_discoveries(*discovered_values_and_types):
    discovered = [Identifier(value=v, type=t) for v, t in discovered_values_and_types]
    return Finding(
        source="github",
        identifier=Identifier(value="alice", type=IdentifierType.USERNAME),
        status=MatchStatus.CONFIRMED,
        source_url="https://github.com/alice",
        discovered_identifiers=discovered,
    )


def test_collect_new_identifiers_skips_already_visited():
    seed = Identifier(value="alice", type=IdentifierType.USERNAME)
    already_found_email = Identifier(value="alice@example.com", type=IdentifierType.EMAIL)
    finding = _finding_with_discoveries(("alice@example.com", IdentifierType.EMAIL))

    visited = {seed, already_found_email}
    new_ids = Engine._collect_new_identifiers([finding], visited, max_enrichment=200)

    assert new_ids == []  # already in `visited`, must not be re-queued


def test_collect_new_identifiers_dedupes_within_round():
    finding_a = _finding_with_discoveries(("bob@example.com", IdentifierType.EMAIL))
    finding_b = _finding_with_discoveries(("bob@example.com", IdentifierType.EMAIL))

    new_ids = Engine._collect_new_identifiers([finding_a, finding_b], visited=set(), max_enrichment=200)

    assert len(new_ids) == 1
    assert new_ids[0].value == "bob@example.com"


def test_collect_new_identifiers_respects_cap():
    findings = [
        _finding_with_discoveries((f"user{i}@example.com", IdentifierType.EMAIL))
        for i in range(10)
    ]
    # 8 already "visited" (e.g. from seeds), cap of 10 total -> only 2 more allowed
    visited = {Identifier(value=f"seed{i}", type=IdentifierType.USERNAME) for i in range(8)}

    new_ids = Engine._collect_new_identifiers(findings, visited, max_enrichment=10)

    assert len(new_ids) == 2
