"""What a found profile says about its owner: display name, bio, location,
photo and the links it shows, read from the page (or API answer) the
username check already downloaded -- no extra requests.

Sources, most reliable first:
  * JSON API answers: the usual field names (display_name, bio/note,
    avatar, location, website, Mastodon-style profile fields);
  * schema.org JSON-LD (Person, ProfilePage), which many sites embed;
  * rel="me" links -- the owner's own claim that a link is theirs (the
    IndieWeb convention Mastodon verification is built on);
  * OpenGraph / Twitter card tags, filtered: on many sites og:description
    is the site's own boilerplate and og:image its logo, so those are only
    kept when they don't look like it;
  * link hubs (Linktree, Carrd, ...): every outbound button is the owner's;
  * "ig: alice" / "twitter @alice" written in a bio.

The result goes into the finding's metadata under the keys the dedicated
plugins already use (name, bio, location, avatar_url, links), plus
linked_accounts for links that resolve to an account on a known platform.
"""
from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

from osintrecon.core import platform_urls

MAX_BODY = 600_000
MAX_BIO = 400
MAX_LINKS = 15
MAX_HANDLES = 8

# Gravatar and Libravatar both name a photo by the hash of the email address.
_GRAVATAR = re.compile(r"(?:^|//)(?:[a-z0-9]+\.)?(?:gravatar\.com|libravatar\.org)/avatar/([0-9a-f]{64}|[0-9a-f]{32})", re.I)
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_TAGS = re.compile(r"<[^>]+>")
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,24}|xn--[a-z0-9\-]{2,59})")
_EMBEDDED_URL = re.compile(r'"url"\s*:\s*"(https?:\\?/\\?/[^"\s]{4,300})"')

# "ig: alice", "twitter @alice", "tiktok - alice"
_HANDLE_PLATFORMS = {
    "ig": "instagram", "insta": "instagram", "instagram": "instagram",
    "tw": "twitter", "twitter": "twitter", "x": "twitter",
    "tt": "tiktok", "tiktok": "tiktok",
    "yt": "youtube", "youtube": "youtube",
    "twitch": "twitch", "ttv": "twitch",
    "gh": "github", "github": "github",
    "snap": "snapchat", "sc": "snapchat", "snapchat": "snapchat",
    "telegram": "telegram", "tg": "telegram",
    "reddit": "reddit", "steam": "steam", "threads": "threads", "bsky": "bluesky", "bluesky": "bluesky",
}
_HANDLE = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(sorted(map(re.escape, _HANDLE_PLATFORMS), key=len, reverse=True)) +
    r")\s*(?::|-|–|=|>|\|)?\s*@([A-Za-z0-9_.]{2,30})|(?<![A-Za-z0-9])(" +
    "|".join(sorted(map(re.escape, _HANDLE_PLATFORMS), key=len, reverse=True)) + r")\s*[:\-–=>|]\s*([A-Za-z0-9_.]{2,30})",
    re.I)

# Site boilerplate in og:description ("Check out alice's profile on X").
_BOILERPLATE = re.compile(
    r"\b(sign ?up|log ?in|join (us|now|today|the)|create (an|your) account|check out|profile on|"
    r"is on [A-Z]|see (photos|posts|the latest)|followers|following|subscribers|watch (the )?(latest|videos)|"
    r"listen to|stream (live|on)|discover|explore|download|the (world'?s|best|largest|biggest)|"
    r"official (site|website)|connect with|on (the )?\w+ (app|platform|community)|member since|"
    r"view (the )?profile|has \d+ \w+|\d[\d,.]*\s*(k|m)?\s*(followers|likes|posts|videos|fans))\b",
    re.I)

# og:image values that are a site's logo or share card, not a photo.
_NOT_A_PHOTO = re.compile(
    r"(logo|og[-_]?image|og[-_]default|default|share|social|brand|banner|card|favicon|placeholder|"
    r"missing|blank|anonymous|no[-_]?avatar|site[-_]?image|opengraph|/og/|/static/|/assets/images/)",
    re.I)

# Outbound links on a link hub that are the hub's own (app stores, legal).
_HUB_JUNK_HOSTS = re.compile(r"(^|\.)(apple\.com|google\.com|play\.google\.com|onelink\.me|cookiebot\.com|"
                             r"cloudflare\.com|gstatic\.com|googleapis\.com|w3\.org|schema\.org)$", re.I)
_HUB_JUNK_PATH = re.compile(r"(privacy|terms|cookie|legal|signup|sign-up|login|register|report|help)", re.I)

_WRAPPER_KEYS = ("data", "user", "result", "account", "profile", "person", "channel", "author", "entry",
                 "uinf", "response", "payload", "graphql", "person_view", "them", "users", "results",
                 "attributes")
_NAME_KEYS = ("display_name", "displayName", "displayname", "display-name", "full_name", "fullName",
              "fullname", "nickname", "nick", "name", "realname", "real_name")
_BIO_KEYS = ("bio", "note", "description", "about", "about_me", "aboutMe", "summary", "intro", "tagline",
             "headline", "signature", "status_text", "dsc")
_AVATAR_KEYS = ("avatar_url", "avatarUrl", "avatar", "profile_image_url_https", "profile_image_url",
                "profile_image", "profile_pic_url", "profilePicUrl", "picture", "photo", "image", "ico",
                "avatar_static", "icon_url", "img_avatar", "avatar_720_url", "gravatar_url", "photo_url",
                "pfp", "profile_picture", "images")
_LOCATION_KEYS = ("location", "city", "country", "hometown", "loc")
# The profile's cover image. Only names that mean "this account's banner":
# a bare "banner" or "cover" key on a page is as often the site's own.
_BANNER_KEYS = ("banner_url", "bannerUrl", "bannerImage", "banner_image", "banner_img", "profile_banner_url",
                "profileBanner", "header_static", "header_image", "headerImage", "cover_photo", "coverPhoto",
                "cover_image_url", "coverImageUrl", "profile_cover", "visual_url")
# An API answer is about the account, so there plain names for its cover
# image are safe too (Mastodon's "header", Wattpad's "backgroundUrl").
_JSON_BANNER_KEYS = _BANNER_KEYS + ("header", "banner", "cover", "coverImage", "cover_image", "cover_url",
                                    "coverUrl", "cover_photo_url", "backgroundUrl", "background_url",
                                    "background_image", "header_media_item", "cover_photo")
# What an API says about the account besides who it is: when it was made
# and how many follow it. Stored under one name each, whatever the source
# called them.
_CREATED_KEYS = ("created_at", "createdAt", "created", "creationDate", "date_joined", "joined", "joined_at",
                 "memberSince", "created_on", "createdOn", "createDate", "created_time", "time_created",
                 "published", "registered")
_COUNT_KEYS = {
    "followers_count": ("followers_count", "followersCount", "follower_count", "followerCount", "numFollowers",
                        "followers_total", "subscribers_count", "followers"),
    "following_count": ("following_count", "followingCount", "follows_count", "numFollowing", "following"),
    "posts_count": ("statuses_count", "posts_count", "postsCount", "numStoriesPublished"),
}
# A stock picture standing in for "no photo" (Mastodon's missing.png,
# Gitee's no_portrait, GameJolt's avatar frames). Matched on host and path
# only: a Gravatar URL names its fallback in the query.
_PLACEHOLDER_PICTURE = re.compile(
    r"(missing|default|placeholder|blank|anonymous|no[-_]?avatar|no[-_]?portrait|avatar-frame|/gravatar\.jpg|"
    r"favicon|logo|/static/|/assets/images/|opengraph|og[-_]?image)", re.I)
# In a page's embedded JSON (escaped slashes and all): one of those keys
# and an image URL.
_EMBEDDED_BANNER = re.compile(
    r'"(?:' + "|".join(_BANNER_KEYS) + r')"\s*:\s*"(https?:(?:\\?/){2}[^"\s]{8,600})"')
# YouTube keeps the channel banner in ytInitialData: the sources run from
# small to large, so the last one is the full size.
_YOUTUBE_BANNER = re.compile(r'"imageBannerViewModel":\{"image":\{"sources":\[(.*?)\]', re.S)
_YOUTUBE_SOURCE = re.compile(r'"url":"(https://yt3\.googleusercontent\.com/[^"]+)"')
_LINK_KEYS = ("website", "url", "blog", "homepage", "home_page", "site", "web", "links", "website_url",
              "external_url", "fields", "social", "socials", "social_links", "twitter_username")


class _Page(HTMLParser):
    def __init__(self, collect_anchors: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.collect_anchors = collect_anchors
        self.meta: dict[str, str] = {}
        self.rel_me: list[str] = []
        self.anchors: list[str] = []
        self.jsonld: list[str] = []
        self._buf: Optional[list[str]] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag in ("a", "link"):
            href = a.get("href", "").strip()
            if not href:
                return
            if "me" in a.get("rel", "").lower().split():
                self.rel_me.append(href)
            elif tag == "a" and self.collect_anchors and len(self.anchors) < 200:
                self.anchors.append(href)
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._buf is not None:
            self.jsonld.append("".join(self._buf))
            self._buf = None

    def handle_data(self, data: str) -> None:
        if self._buf is not None:
            self._buf.append(data)


def _text(value: Any, limit: int = MAX_BIO) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = html.unescape(_TAGS.sub(" ", value))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] or None


def _first(obj: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = obj.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _first_image(obj: dict, keys: tuple[str, ...]) -> Optional[str]:
    """The first of these keys that holds a picture: a key holding no URL
    (an empty size map, a colour) doesn't hide a later one that does."""
    for key in keys:
        url = _image_url(obj.get(key))
        if url:
            return url
    return None


def _image_url(value: Any) -> Optional[str]:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        named = value.get("url") or value.get("contentUrl") or value.get("src") or value.get("img_url")
        if not named:
            # Sizes as keys ({"90x90": ..., "32x32": ...}, {"150x150": ...}):
            # the largest.
            words = {"master": 9000, "original": 8000, "full": 7000, "xlarge": 6000, "large": 5000,
                     "medium": 400, "small": 100, "tiny": 30, "thumb": 50}
            sized = []
            for k, v in value.items():
                if not (isinstance(v, str) and v.startswith(("http://", "https://", "//"))):
                    continue
                m = re.search(r"\d+", str(k))
                size = int(m.group()) if m else words.get(str(k).lower())
                if size is not None:
                    sized.append((size, v))
            named = max(sized)[1] if sized else None
        value = named
    if isinstance(value, str) and value.strip().lower().startswith(("http://", "https://", "//")):
        return value.strip()
    return None


def _location(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        parts = [value.get(k) for k in ("name", "addressLocality", "addressRegion", "addressCountry")]
        value = ", ".join(p for p in parts if isinstance(p, str) and p.strip())
    return _text(value, 100)


def _links_from(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.extend(_URL_IN_TEXT.findall(value))
        if "<a" in value:
            out.extend(re.findall(r"href=[\"']([^\"']+)", value))
        stripped = value.strip()
        if not out and re.fullmatch(r"[a-z0-9.\-]+\.[a-z]{2,}(/\S*)?", stripped, re.I):
            out.append("https://" + stripped)
    elif isinstance(value, list):
        for item in value:
            out.extend(_links_from(item))
    elif isinstance(value, dict):
        for key in ("url", "value", "href", "link"):
            if key in value:
                out.extend(_links_from(value[key]))
    return out


def _unwrap(obj: Any) -> Any:
    """Descend through API envelopes ({"result": {"data": {...}}})."""
    for _ in range(4):
        if isinstance(obj, list) and len(obj) == 1:
            obj = obj[0]
            continue
        if not isinstance(obj, dict):
            return obj
        # Already the account (it has a name or a photo): its own "profile"
        # or "data" field is a part of it, not a further envelope.
        if any(obj.get(k) for k in _NAME_KEYS + _AVATAR_KEYS + ("username", "handle")):
            return obj
        nested = next((obj[k] for k in _WRAPPER_KEYS if isinstance(obj.get(k), (dict, list)) and obj.get(k)), None)
        if nested is None:
            return obj
        obj = nested
    return obj


def _placeholder(url: str) -> bool:
    parts = urlsplit(url if "//" in url else "//" + url)
    return bool(_PLACEHOLDER_PICTURE.search(f"{parts.netloc}{parts.path}"))


def picture_from(body: str, pattern: str, username: str, fmt: Optional[str] = None) -> Optional[str]:
    """A picture URL a site keeps somewhere the general reading doesn't look
    (a size code to rebuild, XML, a nested key): its entry's regex, whose
    first group is the URL, "{username}" standing for the username. None when the
    pattern finds nothing or the result is a stock picture."""
    try:
        m = re.search(pattern.replace("{username}", re.escape(username)), body or "", re.S | re.I)
    except re.error:
        return None
    if not m:
        return None
    url = (m.group(1).replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
           .replace("\\u0026", "&").replace("&amp;", "&").strip())
    if fmt:
        url = fmt.format(url)
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("https://", "http://")) or _placeholder(url):
        return None
    return url


def _banner_in_page(body: str) -> Optional[str]:
    """The account's cover image from a page's embedded JSON, if it names
    one: YouTube's channel banner, or a key that only means a banner."""
    yt = _YOUTUBE_BANNER.search(body)
    if yt:
        sources = _YOUTUBE_SOURCE.findall(yt.group(1))
        if sources:
            return sources[-1].replace("\\u0026", "&")
    m = _EMBEDDED_BANNER.search(body)
    if m:
        return m.group(1).replace("\\/", "/").replace("\\u0026", "&")
    return None


def _from_json(data: Any) -> dict[str, Any]:
    obj = _unwrap(data)
    if not isinstance(obj, dict):
        return {}
    out: dict[str, Any] = {}
    name = _first(obj, _NAME_KEYS)
    if isinstance(name, str):
        out["name"], out["name_source"] = _text(name, 80), "api"
    out["bio"] = _text(_first(obj, _BIO_KEYS))
    out["avatar_url"] = _first_image(obj, _AVATAR_KEYS)
    out["banner_url"] = _first_image(obj, _JSON_BANNER_KEYS)
    created = _first(obj, _CREATED_KEYS)
    if isinstance(created, (str, int, float)) and not isinstance(created, bool):
        out["created_at"] = created
    for name, keys in _COUNT_KEYS.items():
        value = _first(obj, keys)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            out[name] = int(value)
    out["location"] = _location(_first(obj, _LOCATION_KEYS))
    links: list[str] = []
    for key in _LINK_KEYS:
        if key == "twitter_username" and isinstance(obj.get(key), str) and obj[key].strip():
            links.append(f"https://twitter.com/{obj[key].strip()}")
        elif key in obj:
            links.extend(_links_from(obj[key]))
    out["claimed_links"] = links
    return out


def _jsonld_people(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    found: list[dict] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
        elif isinstance(node, dict):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "Person" in kinds or "Organization" in kinds:
                found.append(node)
            for key in ("mainEntity", "@graph", "author", "about"):
                if key in node:
                    walk(node[key], depth + 1)

    walk(data)
    return found


def _is_owner(node: dict, site_name: str, username: str) -> bool:
    """A JSON-LD Person/Organization describing the account, not the site
    itself (many sites embed their own Organization on every page)."""
    name = str(node.get("name") or "")
    url = str(node.get("url") or "")
    alternate = str(node.get("alternateName") or node.get("identifier") or "")
    if username.lower() in (url + " " + alternate).lower():
        return True
    if node.get("@type") == "Organization" or (isinstance(node.get("@type"), list) and "Organization" in node["@type"]):
        return False
    site_bits = re.sub(r"[^a-z0-9]", "", site_name.lower())
    return bool(name) and re.sub(r"[^a-z0-9]", "", name.lower()) != site_bits


def _clean_title(title: str, site_name: str, username: str) -> Optional[str]:
    text = html.unescape(title or "").strip()
    if not text:
        return None
    site_bits = {site_name.lower(), re.sub(r"[^a-z0-9]", "", site_name.lower())}
    parts = re.split(r"\s+[|·•–—-]\s+|\s+on\s+|\s+at\s+", text)
    kept = [p for p in parts if p.strip() and not any(b and b in re.sub(r"[^a-z0-9 ]", "", p.lower()).replace(" ", "")
                                                      for b in site_bits)]
    if not kept:
        return None
    name = kept[0]
    wrapped = re.fullmatch(r"\s*@?" + re.escape(username) + r"\s*\((.+)\)\s*", name, flags=re.I)
    if wrapped:
        name = wrapped.group(1)
    name = re.sub(r"\(\s*@?" + re.escape(username) + r"\s*\)", "", name, flags=re.I)
    name = re.sub(r"@" + re.escape(username) + r"\b", "", name, flags=re.I)
    name = re.sub(r"[’']s (profile|page|channel|blog|account).*$", "", name, flags=re.I)
    name = re.sub(r"\b(profile|user|account|member|channel)\b\s*$", "", name, flags=re.I)
    name = name.strip(" -|·:")
    if name.count("(") != name.count(")"):
        name = name.replace("(", "").replace(")", "").strip()
    if not name or len(name) > 60 or name.lower() == username.lower() or not re.search(r"[^\W\d_]", name):
        return None
    if re.search(r"\b(404|not found|error|sign in|log in|welcome|home)\b", name, re.I):
        return None
    return name


def _absolute(href: str, base: str) -> Optional[str]:
    href = html.unescape(href.strip())
    if href.startswith("//"):
        href = "https:" + href
    url = urljoin(base, href)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if not _HOST.fullmatch(parts.netloc.lower().split("@")[-1].split(":")[0]):
        return None  # "https://www." and other fragments
    return url


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower().split(":")[0].removeprefix("www.")


def _same_site(host: str, site_host: str) -> bool:
    if not site_host:
        return False
    root = ".".join(site_host.split(".")[-2:])
    return host == site_host or host.endswith("." + root) or host == root


def handles_in(text: Optional[str]) -> list[tuple[str, str]]:
    if not text:
        return []
    out: list[tuple[str, str]] = []
    for m in _HANDLE.finditer(text):
        label, user = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        platform = _HANDLE_PLATFORMS[label.lower()]
        user = user.strip(".")
        if len(user) >= 2 and (platform, user.lower()) not in {(p, u.lower()) for p, u in out}:
            out.append((platform, user))
    return out[:MAX_HANDLES]


def extract(body: str, page_url: str, site_name: str, username: str, *, link_hub: bool = False,
            content_type: str = "") -> dict[str, Any]:
    """Profile fields from a found account's page or API answer. Missing
    fields are left out; an empty dict means nothing usable was there."""
    if not body:
        return {}
    body = body[:MAX_BODY]
    site_host = _host(page_url)
    profile: dict[str, Any] = {}
    claimed: list[str] = []
    stripped = body.lstrip()

    if "json" in content_type.lower() or stripped[:1] in ("{", "["):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if data is not None:
            got = _from_json(data)
            claimed.extend(got.pop("claimed_links", []))
            # A count of 0 is a fact too (nobody follows yet), not a gap.
            profile.update({k: v for k, v in got.items() if v or (type(v) is int)})
    else:
        page = _Page(collect_anchors=link_hub)
        try:
            page.feed(body)
            page.close()
        except Exception:  # noqa: BLE001 -- broken markup still leaves what was parsed
            pass
        for raw in page.jsonld:
            for person in _jsonld_people(raw):
                if not _is_owner(person, site_name, username):
                    continue
                if not profile.get("name") and isinstance(person.get("name"), str):
                    profile["name"], profile["name_source"] = _text(person["name"], 80), "structured"
                profile.setdefault("bio", _text(person.get("description")))
                profile.setdefault("avatar_url", _image_url(person.get("image")))
                profile.setdefault("location", _location(person.get("homeLocation") or person.get("address")))
                claimed.extend(_links_from(person.get("sameAs")))
        claimed.extend(page.rel_me)
        if link_hub:
            claimed.extend(page.anchors)
            # Hubs built as client-side apps (Linktree) carry their buttons
            # in embedded JSON rather than in <a> tags.
            claimed.extend(u.replace("\\/", "/") for u in _EMBEDDED_URL.findall(body)[:100])
        meta = page.meta
        if not profile.get("name"):
            name = _clean_title(meta.get("og:title") or meta.get("twitter:title") or "", site_name, username)
            if name:
                profile["name"], profile["name_source"] = name, "page_title"
        if not profile.get("bio"):
            desc = _text(meta.get("og:description") or meta.get("description") or meta.get("twitter:description"))
            if desc and not _BOILERPLATE.search(desc) and site_name.lower() not in desc.lower():
                profile["bio"] = desc
        if not profile.get("avatar_url"):
            image = meta.get("og:image") or meta.get("twitter:image") or meta.get("twitter:image:src")
            if image and not _NOT_A_PHOTO.search(image):
                profile["avatar_url"] = image
        banner = _banner_in_page(body)
        if banner:
            profile["banner_url"] = banner
        profile = {k: v for k, v in profile.items() if v}
    if profile.get("name") and profile["name"].lstrip("@").strip().lower() == username.lower():
        # The username again, not a display name.
        profile.pop("name")
        profile.pop("name_source", None)

    links: list[str] = []
    seen: set[str] = set()
    for href in claimed + _links_from(profile.get("bio") or ""):
        url = _absolute(href, page_url)
        if not url:
            continue
        host = _host(url)
        if _same_site(host, site_host) or _HUB_JUNK_HOSTS.search(host):
            continue
        if link_hub and _HUB_JUNK_PATH.search(urlsplit(url).path):
            continue
        key = url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        links.append(url)
        if len(links) >= MAX_LINKS:
            break
    if links:
        profile["links"] = links

    for key in ("avatar_url", "banner_url"):
        if profile.get(key) and _placeholder(profile[key]):
            profile.pop(key)
    if profile.get("banner_url"):
        banner = _absolute(profile["banner_url"], page_url)
        # An image has a path to it; a bare host ("https://mango.plurk.com/")
        # under a banner-ish key is a link, not a picture.
        path = urlsplit(banner).path if banner else ""
        if (banner and len(path.strip("/")) >= 3 and not _placeholder(banner)
                and banner != profile.get("avatar_url")):
            profile["banner_url"] = banner
        else:
            profile.pop("banner_url")
    if profile.get("avatar_url"):
        profile["avatar_url"] = _absolute(profile["avatar_url"], page_url) or profile["avatar_url"]
        m = _GRAVATAR.search(profile["avatar_url"])
        if m:
            profile["gravatar_hash"] = m.group(1).lower()

    # The site's own accounts, linked from every page's header or footer
    # ("x.com/opencollect" on Open Collective): not the profile owner's.
    site_fold = re.sub(r"[^a-z0-9]", "", site_name.lower())

    def site_own(url: str, account_name: str = "") -> bool:
        if len(site_fold) < 5:
            return False
        name_fold = re.sub(r"[^a-z0-9]", "", account_name.lower())
        if name_fold == re.sub(r"[^a-z0-9]", "", username.lower()):
            return False
        segments = [re.sub(r"[^a-z0-9]", "", seg.lower()) for seg in urlsplit(url).path.split("/")]
        for candidate in [name_fold, *segments]:
            if len(candidate) >= 5 and (site_fold.startswith(candidate) or candidate.startswith(site_fold)):
                return True
        return site_fold in re.sub(r"[^a-z0-9]", "", urlsplit(url).path.lower())

    if links:
        links = [url for url in links if not site_own(url, (platform_urls.resolve(url) or ("", ""))[1])]
        if links:
            profile["links"] = links
        else:
            profile.pop("links", None)

    accounts: list[dict[str, str]] = []
    keys: set[tuple[str, str]] = set()
    for url in links:
        resolved = platform_urls.resolve(url)
        if resolved and (resolved[0], resolved[1].lower()) not in keys:
            keys.add((resolved[0], resolved[1].lower()))
            accounts.append({"platform": resolved[0], "username": resolved[1], "evidence": "profile_link", "url": url})
    for platform, user in handles_in(profile.get("bio")):
        if (platform, user.lower()) not in keys:
            keys.add((platform, user.lower()))
            accounts.append({"platform": platform, "username": user, "evidence": "profile_link"})
    if accounts:
        profile["linked_accounts"] = accounts
    return profile
