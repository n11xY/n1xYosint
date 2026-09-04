import asyncio

from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources.dork_links import DorkLinksPlugin


def test_no_network_client_needed():
    # Pure URL construction, no self.http call at all -- pass None to prove it.
    plugin = DorkLinksPlugin(config={}, http=None)
    identifier = Identifier(value="John Doe", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 2


def test_both_platforms_present_with_correct_urls():
    plugin = DorkLinksPlugin(config={}, http=None)
    identifier = Identifier(value="John Doe", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    platforms = {f.metadata["platform"] for f in findings}
    assert platforms == {"linkedin", "x"}

    linkedin = next(f for f in findings if f.metadata["platform"] == "linkedin")
    assert "site%3Alinkedin.com%2Fin" in linkedin.source_url
    assert "John+Doe" in linkedin.source_url

    x = next(f for f in findings if f.metadata["platform"] == "x")
    assert "site%3Ax.com" in x.source_url
    assert "John+Doe" in x.source_url


def test_findings_are_uncertain_search_lead_category():
    plugin = DorkLinksPlugin(config={}, http=None)
    identifier = Identifier(value="John Doe", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    for f in findings:
        assert f.status == MatchStatus.UNCERTAIN
        assert f.category == "search-lead"
        assert f.source == "dork_links"


def test_special_characters_in_name_are_url_encoded():
    plugin = DorkLinksPlugin(config={}, http=None)
    identifier = Identifier(value='O\'Brien "Test"', type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    for f in findings:
        assert " " not in f.source_url.split("?", 1)[1]
        assert '"' not in f.source_url
