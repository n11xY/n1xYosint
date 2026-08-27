from osintrecon.core.correlation import CorrelationEngine
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus


def test_correlates_seed_with_discovered_identifier():
    seed = Identifier(value="alice", type=IdentifierType.USERNAME)
    discovered_email = Identifier(value="alice@example.com", type=IdentifierType.EMAIL)

    finding = Finding(
        source="github",
        identifier=seed,
        status=MatchStatus.CONFIRMED,
        source_url="https://github.com/alice",
        discovered_identifiers=[discovered_email],
    )

    entities = CorrelationEngine().correlate([finding])
    assert len(entities) == 1
    assert seed in entities[0].identifiers
    assert discovered_email in entities[0].identifiers


def test_unrelated_identifiers_stay_separate():
    f1 = Finding(
        source="github",
        identifier=Identifier(value="alice", type=IdentifierType.USERNAME),
        status=MatchStatus.CONFIRMED,
        source_url="https://github.com/alice",
    )
    f2 = Finding(
        source="github",
        identifier=Identifier(value="bob", type=IdentifierType.USERNAME),
        status=MatchStatus.CONFIRMED,
        source_url="https://github.com/bob",
    )

    entities = CorrelationEngine().correlate([f1, f2])
    assert len(entities) == 2
