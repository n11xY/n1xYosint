"""The cover-image search (osintrecon/core/banner_discovery.py), on small
local pages and API answers shaped like the real ones it was built from."""
import asyncio
import json
import time
from types import SimpleNamespace

from osintrecon.core import banner_discovery as bd
from osintrecon.core.models import Identifier, IdentifierType
from osintrecon.core.profile_extract import extract
from osintrecon.plugins.sources.username_sites import UsernameSitesPlugin


def page(body: str, head: str = "") -> str:
    return f"<html><head>{head}</head><body>{body}</body></html>"


def banner(html_or_json: str, url: str = "https://example.org/alice", user: str = "alice", **kw):
    return extract(html_or_json, url, "Example", user, **kw)


# -- layer A: an account API answer -------------------------------------------

def test_an_api_answers_own_cover_field_is_taken():
    api = json.dumps({"username": "alice", "avatar": "https://cdn.example.com/a/1.jpg",
                      "header": "https://cdn.example.com/h/1500x500/9f3.jpg"})
    got = banner(api, "https://example.org/api/users/alice", content_type="application/json")
    assert got["banner_url"] == "https://cdn.example.com/h/1500x500/9f3.jpg"
    assert got["banner_source"] == "api:header" and got["banner_confidence"] >= 90


def test_an_api_cover_given_as_sizes_or_nested_object_is_read():
    sizes = json.dumps({"handle": "alice", "cover_photo": {"640x": "https://cdn.example.com/c/640.jpg",
                                                           "2000x": "https://cdn.example.com/c/2000.jpg"}})
    assert banner(sizes, content_type="application/json")["banner_url"] == "https://cdn.example.com/c/2000.jpg"
    nested = json.dumps({"payload": {"user": {"username": "alice",
                                              "header_media_item": {"img_url": "https://m.example.com/h/77.png"}}}})
    assert banner(nested, content_type="application/json")["banner_url"] == "https://m.example.com/h/77.png"


def test_an_api_cover_flagged_as_the_default_is_not_the_accounts():
    # BandLab answers {"isDefault": true, "url": ".../userbackgrounds/default/"} for no background.
    api = json.dumps({"username": "alice", "name": "Alice",
                      "backgroundPicture": {"isDefault": True, "url": "https://img.example.com/bg/2b8/"}})
    got = banner(api, content_type="application/json")
    assert "banner_url" not in got
    assert got["banner_diagnostics"][0]["reasons"] == {"default_flag": 1}


def test_stock_headers_are_rejected_as_placeholders():
    api = json.dumps({"username": "alice", "display_name": "Alice",
                      "header": "https://pixelfed.example/storage/headers/missing.png"})
    got = banner(api, content_type="application/json")
    assert "banner_url" not in got and got["banner_diagnostics"][0]["why_none"] == "all_rejected"


# -- layer B/C: embedded JSON --------------------------------------------------

def test_a_strong_key_in_embedded_state_is_taken():
    # Misskey's profile page: "bannerUrl" in the page's state.
    body = page('<script>window.__STATE__ = {"user":{"username":"alice",'
                '"bannerUrl":"https:\\/\\/media.example.jp\\/misskey\\/65b2.jpg"}};</script>')
    got = banner(body)
    assert got["banner_url"] == "https://media.example.jp/misskey/65b2.jpg"


def test_a_vague_key_counts_only_inside_the_accounts_own_object():
    # LivePix: "backgroundImage" next to the username -- the account's.
    own = page('<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"profile":'
               '{"username":"alice","avatar":"https://cdn.example.gg/avatars/1b7.png",'
               '"backgroundImage":"https://cdn.example.gg/profile/backgrounds/211c.png"}}}}</script>')
    got = banner(own)
    assert got["banner_url"] == "https://cdn.example.gg/profile/backgrounds/211c.png"
    assert got["banner_source"] == "json:backgroundImage"
    # Flipboard: "headerImageURL" on an editorial section, nowhere near the account.
    editorial = page('<script>window.__PRELOADED_STATE__={"sections":[{"title":"Inside",'
                     '"headerImageURL":"https://cdn.example.com/editorial/09232018/TheInsight.jpg"}],'
                     '"user":{"username":"alice"}};</script>')
    assert "banner_url" not in banner(editorial)


def test_a_bare_banner_key_that_is_the_sites_own_is_not_taken():
    body = '<script>{"banner":"https://example.org/static/site-banner.png"}</script>'
    assert "banner_url" not in banner(body)


def test_escaped_json_inside_a_string_is_still_read():
    body = page('<script>JSON.parse("{\\"profile_banner_url\\":\\"https:\\\\/\\\\/pbs.example.com\\\\/b\\\\/42\\\\/1500x500\\"}")</script>')
    assert banner(body)["banner_url"] == "https://pbs.example.com/b/42/1500x500"


def test_youtubes_banner_view_model_gives_the_largest_source():
    yt = ('<script>var ytInitialData = {"imageBannerViewModel":{"image":{"sources":['
          '{"url":"https://yt3.googleusercontent.com/AB1=w1060-fcrop64"},'
          '{"url":"https://yt3.googleusercontent.com/AB1=w2560-fcrop64"}]}}};</script>')
    got = extract(yt, "https://www.youtube.com/@alice", "YouTube", "alice")
    assert got["banner_url"].endswith("=w2560-fcrop64") and got["banner_source"] == "youtube:imageBannerViewModel"


def test_state_that_is_javascript_not_json_does_not_break_the_page():
    body = page('<script>window.__NUXT__=(function(a){return {user:a}}("x"));</script>'
                '<div class="profile-cover" style="background-image:url(https://cdn.example.com/c/88.jpg)"></div>')
    assert banner(body)["banner_url"] == "https://cdn.example.com/c/88.jpg"


def test_a_page_link_under_a_banner_key_is_not_a_picture():
    # A forum's "banner_url" pointing at its help page; Kwork's "coverUrl" folder.
    body = page('<script>window.__DATA__={"user":{"username":"alice","banner_url":"https://cursor.example/help",'
                '"coverUrl":"https://cdn-edge.example.ru/files/cover"}};</script>')
    got = banner(body)
    assert "banner_url" not in got and got["banner_diagnostics"][0]["reasons"]["not_a_file"] == 2


def test_a_servers_own_banner_next_to_the_accounts_empty_one_is_not_taken():
    # Misskey: the instance metadata has "bannerUrl"; the user object says null.
    body = page('<script>window.__STATE__={"meta":{"name":"misskey.example","bannerUrl":'
                '"https://media.example.jp/misskey/65b25d3c.jpg"},"user":{"username":"alice","bannerUrl":null,'
                '"avatarUrl":"https://media.example.jp/a/1.webp"}};</script>')
    got = banner(body)
    assert "banner_url" not in got
    assert got["banner_diagnostics"][0]["reasons"] == {"outside_account": 1}


def test_covers_of_the_accounts_posts_are_not_its_cover():
    # Instructables: the member object lists their projects, each with a coverImage.
    body = page('<script>window.__INITIAL_STATE__={"member":{"screenName":"alice","instructables":['
                '{"title":"Toy Boat","coverImage":{"downloadUrl":"https://content.example.com/FBI/LA12/boat.jpg"}},'
                '{"title":"Slingshot","coverImage":{"downloadUrl":"https://content.example.com/EB/OC8/sling.jpg"}}]}};</script>')
    assert "banner_url" not in banner(body)


def test_shared_banners_across_accounts_are_dropped_after_the_run():
    from osintrecon.core.models import Finding, MatchStatus
    def finding(user, url):
        return Finding(source="username_sites:Misskey", identifier=Identifier(value=user, type=IdentifierType.USERNAME),
                       status=MatchStatus.CONFIRMED, title="t", category="social", source_url="https://m.example/@" + user,
                       metadata={"banner_url": url, "banner_source": "json:bannerUrl", "banner_confidence": 80,
                                 "banner_diagnostics": [{"stage": "page"}]})
    a, b = finding("alice", "https://m.example/server.jpg"), finding("bob", "https://m.example/server.jpg")
    c = finding("carol", "https://m.example/carol.jpg")
    assert bd.drop_shared_banners([a, b, c]) == 2
    assert "banner_url" not in a.metadata and "banner_url" not in b.metadata
    assert a.metadata["banner_diagnostics"][-1]["dropped"] == "shared_with_other_accounts"
    assert c.metadata["banner_url"] == "https://m.example/carol.jpg"


# -- layer D: DOM and CSS -------------------------------------------------------

def test_a_background_image_on_a_profile_header_is_taken():
    body = page('<div class="closet__header__container--background" '
                'style="background-image: url(&quot;https://cdn.example.net/users/2018/05/m_5af7.jpg&quot;)"></div>')
    got = banner(body)
    assert got["banner_url"] == "https://cdn.example.net/users/2018/05/m_5af7.jpg"
    assert got["banner_source"].startswith("dom:")


def test_a_video_poster_in_an_animated_profile_background_is_taken():
    # Steam's animated profile background: the <video>'s poster is the still.
    body = page('<div class="profile_animated_background"><video playsinline autoplay muted loop '
                'poster="https://shared.example.com/community_assets/images/items/2598440/83b8.jpg">'
                '<source src="https://shared.example.com/items/2598440/421f.webm" type="video/webm"></video></div>')
    assert banner(body, "https://steamcommunity.example/id/alice")["banner_url"].endswith("/83b8.jpg")


def test_lazy_and_srcset_images_in_a_cover_container_are_read():
    lazy = page('<section id="channel-banner"><img data-src="//img.example.com/b/1/wide.jpg"></section>')
    assert banner(lazy)["banner_url"] == "https://img.example.com/b/1/wide.jpg"
    srcset = page('<div class="user-cover"><img srcset="/c/7-640.jpg 640w, /c/7-1600.jpg 1600w"></div>')
    assert banner(srcset)["banner_url"] == "https://example.org/c/7-1600.jpg"


def test_a_page_wide_wrapper_doesnt_make_every_image_a_cover():
    # Steam puts "has_profile_background" on <body>: its own background
    # counts, the menu icon deep inside does not.
    body = ('<html><body class="profile_page has_profile_background"><div><div><div>'
            '<img src="https://static.example.com/public/shared/images/responsive/header_menu_hamburger.png">'
            '</div></div></div></body></html>')
    assert "banner_url" not in banner(body)


def test_the_avatar_inside_the_header_is_not_the_cover():
    # Linktree / Hatena: the round photo sits inside the profile header.
    body = page('<div class="profile-header"><img class="h-24 w-24 rounded-full object-cover" '
                'src="https://ugc.example.ee/USl9Uq9?io=true&amp;size=avatar-v3_0"></div>')
    assert "banner_url" not in banner(body)


def test_object_fit_utility_classes_are_not_cover_containers():
    body = page('<img class="object-cover w-full" src="https://cdn.example.com/products/55.jpg">'
                '<div class="bg-cover bg-center" style="background-image:url(https://cdn.example.com/x/9.jpg)"></div>')
    assert "banner_url" not in banner(body)


def test_the_sites_own_header_images_are_not_the_accounts():
    goodreads = page('<div class="siteHeader__topFullImage"><img src="https://i.example.com/siteheaderbannerimages/795.jpg"></div>')
    assert "banner_url" not in banner(goodreads)
    # Geocaching: the stock profile background, a relative path to the site's UI images.
    geocaching = page('<div id="ProfileHeader_uxBannerPhoto"><img src="/account/app/ui-images/components/profile/p_bgimage-large.png"></div>')
    assert "banner_url" not in banner(geocaching, "https://www.geocaching.example/p/?u=alice")
    # hearthis.at: a "dummy" banner for everyone without one.
    hearthis = page('<div class="profile-banner"><img src="https://img.example.at/uploads/dummy/user/w1420_h280_cropped_user_00967.png"></div>')
    assert "banner_url" not in banner(hearthis)
    logo = page('<div class="m-header__image"><img src="https://cdn.example.com/images/cc-logo.svg"></div>')
    assert "banner_url" not in banner(logo)


def test_album_art_and_avatar_hosts_are_not_covers():
    lastfm = page('<div class="cover-art"><img src="https://lastfm.example.net/i/u/64s/064e010e.jpg"></div>')
    assert "banner_url" not in banner(lastfm)
    steam = page('<div class="profile_header_size"><div><div><img src="https://avatars.example.com/54653e93_full.jpg"></div></div></div>')
    assert "banner_url" not in banner(steam)


# -- og:image -----------------------------------------------------------------

def test_og_image_is_recorded_but_never_chosen():
    head = ('<meta property="og:image" content="https://cdn.example.com/u/42/photo_1200x630.jpg">'
            '<meta property="og:title" content="Alice Example">')
    got = banner(page("<p>hi</p>", head))
    assert "banner_url" not in got
    diag = got["banner_diagnostics"][0]
    # Here it is also the avatar (og:image fills that in), which is the usual case.
    assert "meta" in diag["layers"] and diag["candidates"] == diag["rejected"] == 1
    other = banner(page("<p>hi</p>", '<meta property="og:image" content="https://cdn.example.com/u/42/share.png">'
                                     '<meta name="twitter:image" content="https://cdn.example.com/u/42/card-wide.jpg">'))
    assert "banner_url" not in other
    assert other["banner_diagnostics"][0]["reasons"] == {"never_chosen": 2}


# -- scoring and normalization ---------------------------------------------------

def test_the_avatar_at_another_size_is_not_taken_as_the_cover():
    api = json.dumps({"username": "alice", "avatar_url": "https://pbs.example.com/p/123/abc_normal.jpg",
                      "profile_banner_url": "https://pbs.example.com/p/123/abc_400x400.jpg"})
    got = banner(api, content_type="application/json")
    assert "banner_url" not in got and got["banner_diagnostics"][0]["reasons"] == {"avatar_duplicate": 1}


def test_a_square_size_loses_to_a_wide_one():
    body = page('<div class="profile-banner"><img src="https://cdn.example.com/b/400x400/1.jpg">'
                '<img src="https://cdn.example.com/b/1500x500/1.jpg"></div>')
    assert banner(body)["banner_url"] == "https://cdn.example.com/b/1500x500/1.jpg"


def test_signed_urls_keep_their_query_and_entities_are_decoded():
    signed = "https://cdn.example.com/c/9.jpg?Expires=1800000000&Signature=a~b_c&Key-Pair-Id=K2"
    body = page(f'<div class="profile-cover"><img src="{signed.replace("&", "&amp;")}"></div>')
    assert banner(body)["banner_url"] == signed


def test_normalize_resolves_relative_and_protocol_relative_urls():
    assert bd.normalize("//cdn.example.com/a.jpg") == "https://cdn.example.com/a.jpg"
    assert bd.normalize("../img/b.png", "https://example.org/u/alice/") == "https://example.org/u/img/b.png"
    assert bd.normalize("https:\\/\\/x.example.com\\/c.jpg?a=1\\u0026b=2") == "https://x.example.com/c.jpg?a=1&b=2"
    for bad in ("javascript:alert(1)", "data:image/png;base64,AAA", "https://user:pw@example.com/a.jpg",
                "https://localhost/a.jpg", "ftp://example.com/a.jpg", "https:///a.jpg"):
        assert bd.normalize(bad) is None, bad


def test_variant_keys_match_sizes_of_the_same_picture():
    assert bd.variant_key("https://i.example.com/a/xyz_400x400.jpg") == bd.variant_key("https://i.example.com/a/xyz_normal.jpg")
    assert bd.variant_key("https://yt3.example.com/AB1=s240-c-k") == bd.variant_key("https://yt3.example.com/AB1=w1060")
    assert bd.variant_key("https://i.example.com/a/1.jpg") != bd.variant_key("https://i.example.com/a/2.jpg")


def test_scores_live_in_one_table():
    assert bd.SCORES["meta_image"] < bd.MIN_SCORE <= bd.SCORES["dom_image_deep"] < bd.SCORES["json_strong"]
    assert bd.SCORES["json_vague"] < bd.MIN_SCORE <= bd.SCORES["json_vague"] + bd.BONUSES["owner_object"]


# -- diagnostics, merging, plugins ---------------------------------------------------

def test_diagnostics_say_what_was_tried_and_why_nothing_was_taken():
    got = banner(page('<p>Alice</p>', '<meta property="og:description" content="Painter in Lisbon">'))
    [diag] = got["banner_diagnostics"]
    assert diag["stage"] == "page" and diag["why_none"] == "no_candidates" and diag["candidates"] == 0
    assert set(diag["layers"]) >= {"embedded_json", "dom", "meta"} and isinstance(diag["ms"], float)


def test_an_empty_page_still_gives_no_profile_at_all():
    assert extract(page("<p>nothing</p>"), "https://example.org/a", "Example", "a") == {}


def test_merge_keeps_every_stage_and_the_later_banner():
    profile = {"banner_url": "https://a.example/1.jpg", "banner_source": "dom:x", "banner_confidence": 70,
               "banner_diagnostics": [{"stage": "page", "candidates": 1}]}
    bd.merge(profile, bd.from_pattern("https://b.example/2.jpg").fields("site_pattern"))
    assert profile["banner_url"] == "https://b.example/2.jpg" and profile["banner_source"] == "site_pattern:banner_pattern"
    assert [d["stage"] for d in profile["banner_diagnostics"]] == ["page", "site_pattern"]
    # A stage that found nothing doesn't erase what an earlier one found.
    bd.merge(profile, bd.from_pattern(None).fields("site_pattern"))
    assert profile["banner_url"] == "https://b.example/2.jpg" and len(profile["banner_diagnostics"]) == 3


def test_a_site_pattern_is_still_checked_for_placeholders():
    assert bd.from_pattern("https://img.example.com/default/banner_default.png").banner_url is None


def test_api_banner_for_the_dedicated_plugins():
    got = bd.api_banner("https://files.example.social/headers/original/missing.png", signal="header")
    assert "banner_url" not in got and got["banner_diagnostics"][0]["stage"] == "api"
    ok = bd.api_banner({"url": "https://files.example.com/images/channel/212083/banner_image/e289"}, signal="banner_image")
    assert ok["banner_url"].endswith("/e289") and ok["banner_confidence"] >= 90


class _MisskeyLikeHttp:
    """Found through webfinger (no banner there); the profile page, asked as the
    site's profile_api, carries bannerUrl in its state."""

    def __init__(self):
        self.urls = []

    async def get(self, source, url, headers=None, expected_statuses=None, **kwargs):
        self.urls.append(url)
        if "webfinger" in url:
            text = '{"subject":"acct:alice@misskey.example","links":[]}'
            hdrs = {"Content-Type": "application/jrd+json"}
        else:
            text = page('<script>window.__STATE__={"user":{"username":"alice","avatarUrl":'
                        '"https://media.example.jp/a/1.webp","bannerUrl":"https://media.example.jp/b/65b2.jpg"}};</script>')
            hdrs = {"Content-Type": "text/html"}
        return SimpleNamespace(status=200, text=text, error=None, cached=False, evidence_path=None,
                               url=url, headers=hdrs, json=lambda: None)


def test_a_profile_page_asked_as_profile_api_gives_the_banner_a_check_endpoint_lacks():
    http = _MisskeyLikeHttp()
    plugin = UsernameSitesPlugin(config={}, http=http)
    plugin.sites = [{"name": "Misskey.example", "check": "status", "confirmed": True,
                     "url": "https://misskey.example/.well-known/webfinger?resource=acct:{}@misskey.example",
                     "profile_url": "https://misskey.example/@{}", "profile_api": "https://misskey.example/@{}"}]
    [finding] = asyncio.run(plugin.run(Identifier(value="alice", type=IdentifierType.USERNAME)))
    meta = finding.metadata
    assert meta["banner_url"] == "https://media.example.jp/b/65b2.jpg"
    stages = [d["stage"] for d in meta["banner_diagnostics"]]
    assert stages[-1] == "profile_api"


# -- performance -------------------------------------------------------------------

def test_a_huge_state_blob_is_walked_within_bounds():
    blob = {"items": [{"id": i, "title": f"post {i}", "cover": f"https://cdn.example.com/posts/{i}.jpg"}
                      for i in range(5_000)], "user": {"username": "alice"}}
    # (pages are read up to profile_extract.MAX_BODY; this one is just under)
    body = page(f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(blob)}</script>')
    started = time.perf_counter()
    got = banner(body)
    assert time.perf_counter() - started < 5
    # Post covers are not the account's: no username in their objects.
    assert "banner_url" not in got
    assert got["banner_diagnostics"][0]["candidates"] <= bd.MAX_CANDIDATES
