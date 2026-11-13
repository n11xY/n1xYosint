"""Profile extraction, URL resolution, account evidence and username rarity
-- all offline, on hand-written pages and findings."""
import hashlib
import json

from osintrecon.core import platform_urls, profile_extract
from osintrecon.core.account_evidence import analyze, gravatar_email_matches, to_dict
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.core.username_rarity import assess

ALICE = Identifier(value="alice", type=IdentifierType.USERNAME)


def found(source, ident=ALICE, status=MatchStatus.CONFIRMED, **metadata):
    return Finding(source=source, identifier=ident, status=status,
                   source_url=f"https://{source.lower()}.example/alice", metadata=metadata)


# ------------------------------------------------------------ platform_urls

def test_profile_urls_resolve_to_platform_and_username():
    assert platform_urls.resolve("https://twitter.com/alice") == ("twitter", "alice")
    assert platform_urls.resolve("https://x.com/alice/") == ("twitter", "alice")
    assert platform_urls.resolve("https://www.instagram.com/alice/") == ("instagram", "alice")
    assert platform_urls.resolve("https://github.com/alice") == ("github", "alice")
    assert platform_urls.resolve("https://news.ycombinator.com/user?id=alice") == ("hackernews", "alice")
    assert platform_urls.resolve("https://alice.tistory.com/") == ("tistory", "alice")  # from sites.json


def test_site_pages_are_not_accounts():
    assert platform_urls.resolve("https://github.com/about") is None
    assert platform_urls.resolve("https://twitter.com/intent/tweet") is None
    assert platform_urls.resolve("https://alice.example/") is None


def test_personal_sites_and_shared_hosting():
    assert platform_urls.personal_site("https://alice.example")
    assert platform_urls.personal_site("https://alice.github.io")
    assert not platform_urls.personal_site("https://gist.github.com/abc")   # a platform's other page
    assert not platform_urls.personal_site("https://bit.ly/x")              # shortener
    assert not platform_urls.personal_site("https://wordpress.com")         # the host itself
    assert platform_urls.site_key("https://alice.wordpress.com/about") == "alice.wordpress.com"
    assert platform_urls.site_key("https://blog.alice.example/x") == "alice.example"


# ------------------------------------------------------------ profile_extract

PAGE = """<html><head>
<meta property="og:title" content="Alice Liddell (@alice) | Example">
<meta property="og:description" content="Check out alice's profile on Example!">
<meta property="og:image" content="https://cdn.example/static/og-default.png">
<script type="application/ld+json">{"@type": "Organization", "name": "Example",
  "sameAs": ["https://x.com/example"]}</script>
<script type="application/ld+json">{"@type": "ProfilePage", "mainEntity": {"@type": "Person",
  "name": "Alice Liddell", "url": "https://example.com/alice", "description": "Knitting, tea and Rust.",
  "image": "https://cdn.example/u/alice.jpg", "sameAs": ["https://github.com/alice-l"]}}</script>
</head><body>
<a rel="me" href="https://alice.example">my site</a>
<a href="https://x.com/example">Follow Example</a>
</body></html>"""


def test_page_fields_come_from_the_owner_not_the_site():
    p = profile_extract.extract(PAGE, "https://example.com/alice", "Example", "alice")
    assert p["name"] == "Alice Liddell" and p["name_source"] == "structured"
    assert p["bio"] == "Knitting, tea and Rust."
    assert p["avatar_url"] == "https://cdn.example/u/alice.jpg"
    # the site's own Organization sameAs and footer link are not the owner's
    assert set(p["links"]) == {"https://github.com/alice-l", "https://alice.example"}
    assert p["linked_accounts"] == [{"platform": "github", "username": "alice-l", "evidence": "profile_link",
                                     "url": "https://github.com/alice-l"}]


def test_boilerplate_description_and_logo_are_dropped():
    page = ('<meta property="og:title" content="alice - Example">'
            '<meta property="og:description" content="Join Example today and see posts from alice">'
            '<meta property="og:image" content="https://example.com/logo.png">')
    assert profile_extract.extract(page, "https://example.com/alice", "Example", "alice") == {}


def test_json_api_answers_and_gravatar_hash():
    body = json.dumps({"result": {"data": {
        "display_name": "Alice L", "note": "<p>Hello, I make <b>things</b></p>",
        "avatar": "https://www.gravatar.com/avatar/" + "a" * 32 + "?s=200",
        "location": "Oxford", "website": "alice.example",
        "fields": [{"name": "GitHub", "value": '<a href="https://github.com/alice">github</a>'}]}}})
    p = profile_extract.extract(body, "https://api.example.com/users/alice", "Example", "alice")
    assert p["name"] == "Alice L" and p["bio"] == "Hello, I make things" and p["location"] == "Oxford"
    assert p["gravatar_hash"] == "a" * 32
    assert "https://alice.example" in p["links"] and "https://github.com/alice" in p["links"]


def test_handles_written_in_a_bio():
    assert profile_extract.handles_in("art | ig: @alice.draws · tiktok - alicedraws") == [
        ("instagram", "alice.draws"), ("tiktok", "alicedraws")]


def test_link_hub_buttons_are_links():
    page = ('<script id="__NEXT_DATA__">{"links":[{"url":"https:\\/\\/twitter.com\\/alice"},'
            '{"url":"https://alice.example/shop"}]}</script><a href="https://linktr.ee/privacy">Privacy</a>')
    p = profile_extract.extract(page, "https://linktr.ee/alice", "Linktree", "alice", link_hub=True)
    assert p["links"] == ["https://twitter.com/alice", "https://alice.example/shop"]


def test_broken_links_are_ignored():
    p = profile_extract.extract('{"name": "A B", "website": "https://www."}', "https://e.com/a", "E", "a")
    assert "links" not in p


# ------------------------------------------------------------ account_evidence

def test_a_profile_link_ties_two_accounts():
    findings = [
        found("github", linked_accounts=[{"platform": "twitter", "username": "Alice", "evidence": "profile_link"}]),
        found("username_sites:Twitter"),
        found("username_sites:Pinterest"),
    ]
    a = analyze(findings)
    assert a.clusters == [[("github", "alice"), ("twitter", "alice")]]
    assert a.verdicts[("github", "alice")].kinds == ("profile_link",)
    assert not a.verdicts[("pinterest", "alice")].tied
    assert a.links[0].detail == "GitHub links to Twitter"


def test_mutual_links_are_described_as_such():
    findings = [
        found("github", linked_accounts=[{"platform": "twitter", "username": "alice"}]),
        found("username_sites:Twitter", linked_accounts=[{"platform": "github", "username": "alice"}]),
    ]
    assert analyze(findings).links[0].detail == "GitHub and Twitter link to each other"


def test_same_gravatar_and_same_website_are_strong():
    h = "b" * 32
    findings = [
        found("username_sites:WordPress.org", gravatar_hash=h),
        found("username_sites:Disqus", gravatar_hash=h),
        found("username_sites:Codeberg", links=["https://alice.example/blog"]),
        found("username_sites:Keybase", links=["https://www.alice.example"]),
    ]
    a = analyze(findings)
    kinds = {lk.kind for lk in a.tied_links()}
    assert kinds == {"gravatar_match", "same_website"}
    assert len(a.clusters) == 2


def test_a_photo_alone_is_a_hint_but_photo_and_bio_tie():
    bio = "Knitting, tea and the Rust programming language since 2015"
    match = Finding(source="avatar_correlation", identifier=ALICE, status=MatchStatus.PROBABLE,
                    source_url="https://a/1.png",
                    metadata={"account_a": ["flickr", "alice"], "account_b": ["vimeo", "alice"]})
    only_photo = analyze([found("username_sites:Flickr"), found("username_sites:Vimeo"), match])
    assert only_photo.clusters == [] and only_photo.verdicts[("flickr", "alice")].hints == ("avatar_match",)

    both = analyze([found("username_sites:Flickr", bio=bio), found("username_sites:Vimeo", bio=bio), match])
    assert both.clusters == [[("flickr", "alice"), ("vimeo", "alice")]]


def test_names_and_places_are_weak_and_can_contradict():
    findings = [
        found("github", name="Alice Liddell", location="Oxford, UK"),
        found("gitlab", name="Alice Liddell", location="Oxford, United Kingdom"),
        found("devto", name="Bob Stone", location="Lima, Peru"),
        found("username_sites:Chess.com", name="Alice", name_source="page_title"),
    ]
    a = analyze(findings)
    assert a.clusters == []  # a shared name is not enough
    hint_kinds = {lk.kind for lk in a.loose_links()}
    assert hint_kinds == {"same_name"}       # "Alice" alone (a common first name) says nothing
    conflict_kinds = sorted({c.kind for c in a.conflicts})
    assert conflict_kinds == ["location_conflict", "name_conflict"]
    # "Russia" and "Russian Federation" don't contradict each other
    russia = analyze([found("codeforces", location="Saratov, Russia"),
                      found("username_sites:Behance", location="Russian Federation")])
    assert russia.conflicts == []


def test_same_platform_twice_is_never_evidence():
    findings = [found("github", bio="x" * 40 + " words words words words"),
                found("username_sites:GitHub", bio="x" * 40 + " words words words words")]
    assert analyze(findings).links == []


def test_gravatar_of_a_searched_email():
    email = "alice@example.com"
    h = hashlib.md5(email.encode()).hexdigest()
    mail = Identifier(value=email, type=IdentifierType.EMAIL)
    findings = [found("gravatar", ident=mail), found("username_sites:WordPress.org", gravatar_hash=h)]
    assert [(f.source, e) for f, e in gravatar_email_matches(findings)] == [("username_sites:WordPress.org", email)]
    a = analyze(findings)
    assert a.tied_links()[0].kind == "gravatar_match"
    data = to_dict(a)
    assert data["links"][0]["ties"] is True and data["clusters"][0][0]["account"] in (
        "gravatar:alice@example.com", "wordpressorg:alice")


# ------------------------------------------------------------ username_rarity

def test_username_rarity():
    assert assess("john").label == "common"
    assert assess("johnsmith").label == "common"
    assert assess("therealjohn").label == "common"
    assert assess("torvalds").label == "rare"
    assert assess("xX_ShadowKiller_Xx").label == "moderate"
    # taken on most sites: popular, whatever it looks like
    assert assess("qzxv", hits=150, checked=250).label == "common"


def test_banners_come_only_from_keys_that_mean_this_accounts_cover():
    from osintrecon.core.profile_extract import extract
    api = extract('{"name": "A", "bannerImage": "https://cdn.example.com/b/1.jpg"}', "https://api.example.com/u/a",
                  "Example", "a", content_type="application/json")
    assert api["banner_url"] == "https://cdn.example.com/b/1.jpg"
    page = ('<html><head><title>a</title></head><body><script>window.__DATA__={"user":{"coverPhoto":'
            '"https:\/\/img.example.org\/covers\/a.jpg?w=1\u0026h=2"}}</script></body></html>')
    assert extract(page, "https://example.org/a", "Example", "a")["banner_url"] == "https://img.example.org/covers/a.jpg?w=1&h=2"
    yt = ('<script>var ytInitialData={"imageBannerViewModel":{"image":{"sources":['
          '{"url":"https://yt3.googleusercontent.com/abc=w1060-fcrop64","width":1060},'
          '{"url":"https://yt3.googleusercontent.com/abc=w2560-fcrop64","width":2560}]}}}</script>')
    assert extract(yt, "https://www.youtube.com/@a", "YouTube", "a")["banner_url"].endswith("=w2560-fcrop64")
    # A bare "banner" key is as often the site's own header: not taken.
    site_own = '<script>{"banner":"https://example.org/static/site-banner.png"}</script>'
    assert "banner_url" not in extract(site_own, "https://example.org/a", "Example", "a")


def test_a_banner_key_holding_a_bare_link_is_not_a_picture():
    from osintrecon.core.profile_extract import extract
    page = '<script>{"profile_cover":"https:\/\/mango.plurk.com\/"}</script>'
    assert "banner_url" not in extract(page, "https://www.plurk.com/a", "Plurk", "a")
