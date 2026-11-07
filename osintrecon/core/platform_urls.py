"""Which account does a URL point at? "https://twitter.com/alice" ->
("twitter", "alice"). Used to turn the links a profile shows (its website
field, rel="me" links, a link hub's buttons, "ig: alice" in a bio) into
accounts, so one found account linking to another becomes evidence the two
belong together.

The patterns come from the site database's own profile URL templates, plus
a short list of large platforms that the database checks by API (or not at
all) but that profiles link to constantly. Platform keys are folded the
same way as everywhere else (timeline.platform_key), so a link to
"chess.com/member/x" meets the "chess_com" plugin's finding for "x".
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

from osintrecon.core.timeline import platform_key

SITES_PATH = Path(__file__).resolve().parents[2] / "config" / "sites.json"

_USER = r"(?P<u>[A-Za-z0-9][A-Za-z0-9_.\-]{0,62})"

# platform, host (regex, no scheme / www), path (regex, {u} = the username)
_EXTRA: tuple[tuple[str, str, str], ...] = (
    ("twitter", r"(?:twitter|x)\.com", r"/{u}/?"),
    ("instagram", r"instagram\.com", r"/{u}/?"),
    ("facebook", r"facebook\.com", r"/{u}/?"),
    ("tiktok", r"tiktok\.com", r"/@{u}/?"),
    ("youtube", r"youtube\.com", r"/(?:@|c/|user/){u}/?"),
    ("twitch", r"twitch\.tv", r"/{u}/?"),
    ("reddit", r"(?:old\.)?reddit\.com", r"/(?:u|user)/{u}/?"),
    ("telegram", r"(?:t\.me|telegram\.me)", r"/{u}/?"),
    ("bluesky", r"bsky\.app", r"/profile/{u}/?"),
    ("linkedin", r"linkedin\.com", r"/in/{u}/?"),
    ("github", r"github\.com", r"/{u}/?"),
    ("gitlab", r"gitlab\.com", r"/{u}/?"),
    ("keybase", r"keybase\.io", r"/{u}/?"),
    ("medium", r"medium\.com", r"/@{u}/?"),
    ("threads", r"threads\.(?:net|com)", r"/@{u}/?"),
    ("snapchat", r"snapchat\.com", r"/add/{u}/?"),
    ("steam", r"steamcommunity\.com", r"/id/{u}/?"),
    ("mastodon", r"mastodon\.social", r"/@{u}/?"),
    ("pinterest", r"pinterest\.[a-z.]+", r"/{u}/?"),
    ("soundcloud", r"soundcloud\.com", r"/{u}/?"),
    ("patreon", r"patreon\.com", r"/{u}/?"),
    ("kofi", r"ko-fi\.com", r"/{u}/?"),
    ("buymeacoffee", r"buymeacoffee\.com", r"/{u}/?"),
    ("linktree", r"linktr\.ee", r"/{u}/?"),
    ("tumblr", r"{u}\.tumblr\.com", r"/?"),
    ("substack", r"{u}\.substack\.com", r"/?"),
    ("chesscom", r"chess\.com", r"/member/{u}/?"),
    ("lichess", r"lichess\.org", r"/@/{u}/?"),
    ("devto", r"dev\.to", r"/{u}/?"),
    ("kick", r"kick\.com", r"/{u}/?"),
    ("spotify", r"open\.spotify\.com", r"/user/{u}/?"),
    ("vk", r"vk\.com", r"/{u}/?"),
)

# First path segments that are a site's own pages, never an account.
_RESERVED = frozenset("""
about account accounts api apps blog careers channel company contact developers download explore
features feed help hashtag home i intent jobs join legal login logout news notifications p
policies policy pricing privacy search settings share signin signup status store support terms
tos watch www embed embeds static assets search_results results groups pages events marketplace
discover trending topics tag tags categories category user users people profile profiles r s
""".split())


def _host_pattern(host: str) -> str:
    return r"(?:www\.|m\.|mobile\.)?" + host


def _template_to_pattern(template: str) -> Optional[tuple[str, str]]:
    """(host regex, path regex) from a sites.json URL template, or None for
    templates that aren't a profile page (APIs, query strings)."""
    if "{}" not in template or "?" in template or ".json" in template:
        return None
    parts = urlsplit(template.replace("{}", "usernameplaceholder"))
    host = parts.netloc.lower().removeprefix("www.")
    if re.search(r"(^|\.)api[.\-]|/api/|/v\d+/", parts.netloc + parts.path):
        return None
    host_re = re.escape(host).replace("usernameplaceholder", "{u}")
    path_re = re.escape(parts.path.rstrip("/")).replace("usernameplaceholder", "{u}") + "/?"
    if "{u}" not in host_re and "{u}" not in path_re:
        return None
    return host_re, path_re


@lru_cache(maxsize=1)
def _patterns() -> tuple[tuple[str, re.Pattern], ...]:
    rules: list[tuple[str, str, str]] = list(_EXTRA)
    try:
        sites = json.loads(SITES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sites = []
    for site in sites:
        template = site.get("profile_url") or site.get("url") or ""
        found = _template_to_pattern(template)
        if found:
            rules.append((site["name"], found[0], found[1]))
    compiled = []
    seen: set[tuple[str, str]] = set()
    for platform, host, path in rules:
        if (host, path) in seen:
            continue
        seen.add((host, path))
        host_re = host.replace("{u}", _USER)
        path_re = path.replace("{u}", _USER if "{u}" not in host else r"[^/]*")
        compiled.append((platform_key(platform),
                         re.compile(_host_pattern(host_re) + "$", re.IGNORECASE),
                         re.compile(path_re + "$", re.IGNORECASE)))
    return tuple((p, h, pa) for p, h, pa in compiled)  # type: ignore[misc]


def resolve(url: str) -> Optional[tuple[str, str]]:
    """(platform key, username) for a profile URL, or None."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    host = parts.netloc.lower().split("@")[-1].split(":")[0]
    path = unquote(parts.path) or "/"
    if host.removeprefix("www.") == "news.ycombinator.com" and path.rstrip("/") == "/user":
        user = dict(p.split("=", 1) for p in parts.query.split("&") if "=" in p).get("id", "")
        return ("hackernews", user) if re.fullmatch(r"[A-Za-z0-9_\-]{2,15}", user) else None
    for platform, host_re, path_re in _patterns():  # type: ignore[misc]
        host_match = host_re.match(host)
        if not host_match:
            continue
        path_match = path_re.match(path)
        if not path_match:
            continue
        username = (host_match.groupdict().get("u") or path_match.groupdict().get("u") or "").strip(".")
        if not username or username.lower() in _RESERVED:
            continue
        return platform, username
    return None


@lru_cache(maxsize=1)
def _platform_roots() -> frozenset[str]:
    roots = set()
    for _, host_re, _ in _patterns():  # type: ignore[misc]
        literal = re.sub(r"\(\?P<u>[^)]*\)(\\.)?", "", host_re.pattern)
        literal = literal.replace(r"(?:www\.|m\.|mobile\.)?", "").rstrip("$").replace("\\", "")
        if re.fullmatch(r"[a-z0-9.\-]+", literal):
            roots.add(registrable(literal))
    return frozenset(roots)


def registrable(host: str) -> str:
    """The domain a person registers: last two labels (three for the
    common two-part country suffixes such as co.uk)."""
    labels = host.lower().strip(".").removeprefix("www.").split(".")
    if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "net", "ac", "gov", "edu") and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def known_host(host: str) -> bool:
    """Whether a host belongs to a platform this module knows, subdomains
    included (gist.github.com): a link to it is not someone's own website."""
    host = host.lower().removeprefix("www.")
    for _, host_re, _ in _patterns():  # type: ignore[misc]
        if host_re.match(host) or host_re.match("www." + host):
            return True
    return registrable(host) in _platform_roots()


# Link shorteners, smart links and big shared services: two profiles both
# linking to one of these share nothing personal.
SHARED_DOMAINS = frozenset("""
lnk.to bit.ly t.co goo.gl tinyurl.com smarturl.it found.ee ffm.to hyperfollow.com distrokid.com
spotify.com apple.com amzn.to amazon.com youtu.be google.com goo.su ow.ly buff.ly rebrand.ly
cutt.ly linkin.bio beacons.ai bio.link taplink.cc solo.to campsite.bio lnk.bio allmylinks.com
paypal.me paypal.com cash.app venmo.com discord.gg discord.com wa.me whatsapp.com
onlyfans.com fansly.com throne.com streamlabs.com streamelements.com tipeeestream.com
wikipedia.org imdb.com amazon.de amazon.co.uk etsy.com shopify.com myshopify.com gumroad.com
bandsintown.com songkick.com deezer.com tidal.com soundcloud.app napster.com
""".split())


# Hosts where every subdomain is a different person's site: compared by
# the full host name (alice.github.io), never by the shared domain.
SHARED_HOSTING = frozenset("""
wordpress.com blogspot.com github.io gitlab.io netlify.app vercel.app pages.dev neocities.org tumblr.com
substack.com carrd.co wixsite.com weebly.com squarespace.com livejournal.com herokuapp.com firebaseapp.com
web.app glitch.me notion.site super.site framer.website framer.ai webflow.io mystrikingly.com jimdosite.com
typepad.com over-blog.com hatenablog.com hatenablog.jp tistory.com bearblog.dev mataroa.blog write.as
micro.blog ghost.io beehiiv.com wordpress.org blogger.com sites.google.com start.page mozello.com
""".split())


def site_key(url: str) -> str:
    """What two links must share to be "the same website": the domain a
    person registers, or the full host on shared hosting."""
    host = urlsplit(url).netloc.lower().split(":")[0].removeprefix("www.")
    root = registrable(host)
    return host if root in SHARED_HOSTING else root


def personal_site(url: str) -> bool:
    """A link to someone's own website: not an account on a known
    platform, not a platform's other pages, not a shortener or a big
    shared service."""
    try:
        host = urlsplit(url).netloc.lower().split(":")[0]
    except ValueError:
        return False
    if not host or resolve(url) or known_host(host):
        return False
    root = registrable(host)
    if root in SHARED_HOSTING:
        return host.removeprefix("www.") != root  # alice.wordpress.com, not wordpress.com itself
    return root not in SHARED_DOMAINS
