from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.core.scoring import process


def _finding(source, status, ident_value="alice", url="https://example.com/alice"):
    return Finding(
        source=source,
        identifier=Identifier(value=ident_value, type=IdentifierType.USERNAME),
        status=status,
        source_url=url,
    )


def test_dedup_removes_exact_repeats():
    findings = [
        _finding("github", MatchStatus.CONFIRMED),
        _finding("github", MatchStatus.CONFIRMED),  # exact duplicate: same source/identifier/url
    ]
    scored, removed = process(findings)
    assert len(scored) == 1
    assert removed == 1


def test_corroboration_boosts_confidence():
    findings = [
        _finding("github", MatchStatus.CONFIRMED, url="https://github.com/alice"),
        _finding("gitlab", MatchStatus.CONFIRMED, url="https://gitlab.com/alice"),
    ]
    scored, _ = process(findings)
    # Both findings are for the same identifier from two independent sources,
    # so each should get a small corroboration boost above the base CONFIRMED score.
    for f in scored:
        assert f.confidence > 0.95


def test_not_found_gets_zero_confidence():
    findings = [_finding("github", MatchStatus.NOT_FOUND)]
    scored, _ = process(findings)
    assert scored[0].confidence == 0.0
