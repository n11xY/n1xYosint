from osintrecon.core.entity_scoring import confidence_bucket, score_entity
from osintrecon.core.models import Entity, Finding, Identifier, IdentifierType, MatchStatus


def _identifier(value="Test Person", itype=IdentifierType.NAME):
    return Identifier(value=value, type=itype)


def _finding(source, status=MatchStatus.UNCERTAIN, metadata=None, identifier=None):
    return Finding(
        source=source,
        identifier=identifier or _identifier(),
        status=status,
        source_url="https://example.com/x",
        metadata=metadata or {},
    )


def test_single_identifier_entity_is_left_alone():
    entity = Entity(identifiers={_identifier()}, findings=[_finding("github_name_search")])

    score_entity(entity)

    assert entity.confidence == 0.0
    assert entity.reasons == []


def test_two_independent_plugin_families_are_scored_and_explained():
    entity = Entity(
        identifiers={_identifier(), _identifier("testuser", IdentifierType.USERNAME)},
        findings=[
            _finding("github_name_search", status=MatchStatus.UNCERTAIN),
            _finding("orcid", status=MatchStatus.UNCERTAIN),
        ],
    )

    score_entity(entity)

    assert entity.confidence > 0.0
    assert any("2 independent sources" in r for r in entity.reasons)


def test_confirmed_findings_score_higher_than_all_uncertain():
    identifiers = {_identifier(), _identifier("testuser", IdentifierType.USERNAME)}

    uncertain_entity = Entity(identifiers=identifiers, findings=[
        _finding("github_name_search", status=MatchStatus.UNCERTAIN),
        _finding("orcid", status=MatchStatus.UNCERTAIN),
    ])
    confirmed_entity = Entity(identifiers=identifiers, findings=[
        _finding("github_name_search", status=MatchStatus.CONFIRMED),
        _finding("orcid", status=MatchStatus.CONFIRMED),
    ])

    score_entity(uncertain_entity)
    score_entity(confirmed_entity)

    assert confirmed_entity.confidence > uncertain_entity.confidence
    assert any("Confirmed on 2 sources" in r for r in confirmed_entity.reasons)


def test_same_metadata_value_from_different_families_adds_a_reason():
    entity = Entity(
        identifiers={_identifier(), _identifier("testuser", IdentifierType.USERNAME)},
        findings=[
            _finding("github", metadata={"company": "Acme Inc."}),
            _finding("openalex", metadata={"company": "Acme Inc."}),
        ],
    )

    score_entity(entity)

    assert any("Same company" in r and "github" in r and "openalex" in r for r in entity.reasons)


def test_different_metadata_values_do_not_produce_a_false_match():
    entity = Entity(
        identifiers={_identifier(), _identifier("testuser", IdentifierType.USERNAME)},
        findings=[
            _finding("github", metadata={"company": "Acme Inc."}),
            _finding("openalex", metadata={"company": "Totally Different Corp"}),
        ],
    )

    score_entity(entity)

    assert not any("Same company" in r for r in entity.reasons)


def test_avatar_correlation_signal_is_counted():
    entity = Entity(
        identifiers={_identifier(), _identifier("testuser", IdentifierType.USERNAME)},
        findings=[
            _finding("github_name_search"),
            _finding("avatar_correlation"),
        ],
    )

    score_entity(entity)

    assert any("Profile photo" in r for r in entity.reasons)


def test_not_found_and_error_findings_are_excluded_from_scoring():
    entity = Entity(
        identifiers={_identifier(), _identifier("testuser", IdentifierType.USERNAME)},
        findings=[
            _finding("github_name_search", status=MatchStatus.NOT_FOUND),
            _finding("orcid", status=MatchStatus.ERROR),
        ],
    )

    score_entity(entity)

    assert entity.confidence == 0.0
    assert entity.reasons == []


def test_confidence_bucket_thresholds():
    assert confidence_bucket(0.95) == "very strong"
    assert confidence_bucket(0.80) == "strong"
    assert confidence_bucket(0.60) == "possible"
    assert confidence_bucket(0.40) == "weak"
    assert confidence_bucket(0.10) == "very weak"
