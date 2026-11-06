from datetime import datetime, timezone

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.core.timeline import account_dates, parse_date

NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)
ALICE = Identifier(value="alice", type=IdentifierType.USERNAME)


def finding(source, status=MatchStatus.CONFIRMED, identifier=ALICE, **metadata):
    return Finding(source=source, identifier=identifier, status=status,
                   source_url=f"https://{source}.example/alice", metadata=metadata)


def test_parse_date_reads_every_shape_the_sources_use():
    day = datetime(2011, 9, 3, tzinfo=timezone.utc)
    assert parse_date("2011-09-03T00:00:00Z", NOW) == day                 # GitHub, Wikimedia
    assert parse_date("2011-09-03T00:00:00.000000Z", NOW) == day          # Docker Hub
    assert parse_date("2011-09-03T02:00:00+02:00", NOW) == day            # an offset
    assert parse_date(int(day.timestamp()), NOW) == day                   # Unix seconds
    assert parse_date(int(day.timestamp()) * 1000, NOW) == day            # Lichess milliseconds
    assert parse_date(str(int(day.timestamp())), NOW) == day              # seconds as text
    assert parse_date("Sep 3, 2011", NOW) == day                          # dev.to
    assert parse_date("2011-09-03", NOW) == day                           # date only


def test_parse_date_rejects_placeholders_and_garbage():
    assert parse_date(0, NOW) is None                 # 1970: a placeholder, not a sign-up
    assert parse_date("1985-01-01", NOW) is None
    assert parse_date("2031-01-01", NOW) is None      # in the future
    assert parse_date("yesterday", NOW) is None
    assert parse_date(None, NOW) is None
    assert parse_date(True, NOW) is None
    assert parse_date("", NOW) is None


def test_account_dates_keeps_only_trusted_dated_accounts_oldest_first():
    findings = [
        finding("github", created_at="2011-09-03T00:00:00Z"),
        finding("lichess", MatchStatus.PROBABLE, created_at=1546300800000),   # 2019-01-01
        finding("chess_com", MatchStatus.UNCERTAIN, joined=1262304000),        # uncertain: skipped
        finding("hackernews", MatchStatus.ERROR, created=1262304000),          # error: skipped
        finding("username_sites:Reddit", created_at="2010-01-01T00:00:00Z"),   # no known date field
        finding("pgp_keys", identifier=Identifier(value="a@x.org", type=IdentifierType.EMAIL),
                created_at="2015-05-05T00:00:00Z"),
        finding("bluesky", created_at="not a date"),
    ]

    dated = account_dates(findings, NOW)

    assert [(d.source, d.created.year, d.kind, d.status) for d in dated] == [
        ("github", 2011, "account", "confirmed"),
        ("pgp_keys", 2015, "key", "confirmed"),
        ("lichess", 2019, "account", "probable"),
    ]
    assert dated[0].url == "https://github.example/alice"


def test_account_dates_keeps_the_earliest_per_account():
    dated = account_dates([
        finding("github", created_at="2014-01-01T00:00:00Z"),
        finding("github", created_at="2011-09-03T00:00:00Z"),
    ], NOW)
    assert len(dated) == 1 and dated[0].created.year == 2011


def test_accounts_are_linked_only_by_evidence_never_by_the_name_alone():
    findings = [
        finding("github", created_at="2008-03-10T00:00:00Z", avatar_url="https://a/1.png"),
        finding("keybase", joined=1478736000,  # 2016-11-10, proves the GitHub account
                linked_accounts=[{"platform": "github", "username": "ALICE", "evidence": "keybase_proof"}]),
        finding("lichess", created_at=1457136000000,  # 2016-03-05, links a Twitch channel that wasn't found
                linked_accounts=[{"platform": "twitch", "username": "alice", "evidence": "profile_link"}]),
        finding("chess_com", joined=1324166400),   # 2011-12-18: same name, nothing else
        finding("devto", joined_at="Jul 7, 2024", avatar_url="https://a/2.png"),
        finding("avatar_correlation", status=MatchStatus.PROBABLE,
                avatar_url_a="https://a/1.png", avatar_url_b="https://a/2.png"),
    ]

    by_source = {d.source: d for d in account_dates(findings, NOW)}

    assert by_source["github"].evidence == ("keybase_proof", "avatar_match")
    assert by_source["keybase"].evidence == ("keybase_proof",)
    assert by_source["devto"].evidence == ("avatar_match",)
    assert by_source["chess_com"].linked is False
    assert by_source["lichess"].linked is False  # its link points at an account the search didn't find


def test_a_site_check_and_a_plugin_for_the_same_platform_are_the_same_account():
    from osintrecon.core.timeline import account_links, platform_key

    assert platform_key("username_sites:Chess.com") == platform_key("chess_com") == "chesscom"
    assert platform_key("twitch_api") == platform_key("username_sites:Twitch") == "twitch"
    links = account_links([
        finding("username_sites:GitHub"),
        finding("devto", linked_accounts=[{"platform": "github", "username": "alice", "evidence": "profile_link"}]),
    ])
    assert links[("github", "alice")] == ("profile_link",)


def test_evidence_links_lists_each_pair_once_with_its_kind():
    from osintrecon.core.timeline import evidence_links

    pairs = evidence_links([
        finding("github"),
        finding("keybase", linked_accounts=[{"platform": "github", "username": "alice", "evidence": "keybase_proof"},
                                            {"platform": "github", "username": "alice", "evidence": "keybase_proof"}]),
        finding("devto", linked_accounts=[{"platform": "github", "username": "alice", "evidence": "profile_link"},
                                          {"platform": "twitter", "username": "alice", "evidence": "profile_link"}]),
    ])
    assert sorted(pairs) == [
        (("devto", "alice"), ("github", "alice"), "profile_link"),
        (("github", "alice"), ("keybase", "alice"), "keybase_proof"),
    ]


def test_a_photo_match_within_one_platform_is_not_evidence():
    from osintrecon.core.timeline import evidence_links

    bob = Identifier(value="bob", type=IdentifierType.USERNAME)
    pairs = evidence_links([
        finding("scratch", avatar_url="https://s/1.png"),
        finding("scratch", identifier=bob, avatar_url="https://s/2.png"),
        finding("avatar_correlation", status=MatchStatus.PROBABLE,
                avatar_url_a="https://s/1.png", avatar_url_b="https://s/2.png"),
    ])
    assert pairs == []
