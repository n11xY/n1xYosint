"""Which image is a found account's cover (banner / header / background),
read from what the username check already downloaded -- no extra requests.

Every place that could hold one hands in candidates; nothing is taken just
because it came first:

  * api       -- an account API answer's own cover field (Mastodon's
                 "header", Wattpad's "backgroundUrl", Audius' "cover_photo");
  * json      -- a page's embedded state (__NEXT_DATA__, __INITIAL_STATE__,
                 Apollo, JSON-LD, any inline JSON), walked recursively. A key's
                 name alone is not enough for the vaguer names ("cover",
                 "background"): the object around it has to be the account's
                 (it carries the username) for those to count;
  * dom       -- images and CSS backgrounds inside elements whose id / class
                 names a profile cover ("profile-header", "channel-banner",
                 Steam's "profile_animated_background"), from src, srcset,
                 data-src, poster and style="background-image: url(...)";
  * meta      -- og:image / twitter:image / image_src. Audited across 170
                 found profiles, these were never the cover: they are the
                 avatar or the site's share card. They are recorded so the
                 diagnostics say they were seen, and never chosen.

Each candidate gets a score from SCORES, adjusted by PENALTIES (stock and
placeholder pictures, the site's own assets, the avatar again, a square or
tiny size named in the URL, a "URL" that is a folder rather than a file).
The best one at or above MIN_SCORE wins; the rest are counted, with the
reason, in the diagnostics the finding carries.

URLs are only normalized (entities, escaped slashes, protocol-relative and
relative paths), never rewritten: query strings stay, because image CDNs
sign them. Whether the image is really there and really an image is checked
when it is fetched -- by the web app's image proxy, which validates the
bytes -- not here.
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlsplit

log = logging.getLogger(__name__)

# -- scoring ---------------------------------------------------------------

SCORES = {
    "site_pattern": 92,     # the site entry's own banner_pattern
    "api_field": 90,        # a cover field at the top of an account API answer
    "youtube_banner": 90,   # ytInitialData's imageBannerViewModel
    "json_strong": 80,      # a key that only ever means an account's cover
    "dom_image": 70,        # <img>/<video poster>/<source> on or just inside a cover container
    "dom_image_deep": 62,   # the same, a few levels further in
    "dom_background": 68,   # CSS background on / in a cover container
    "json_vague": 40,       # "cover", "background", ...: needs the account around it
    "json_weak": 25,        # "artwork", "hero": needs the account around it
    "meta_image": 0,        # og:image and friends: recorded, never chosen
}
BONUSES = {
    "owner_object": 35,     # the JSON object (or its parent) names the username
    "owner_object_strong": 10,
    "wide_hint": 8,         # the URL names a wide size (1500x500, w1060)
    "avatar_host": 0,       # same user-content host as the avatar (kept neutral)
}
PENALTIES = {
    "placeholder": -100,    # missing.png, default, dummy, blank, ...
    "default_flag": -100,   # {"isDefault": true, "url": ...}
    "avatar_duplicate": -100,
    "not_a_file": -100,     # ".../files/cover", a bare host
    "unsupported_format": -100,  # .svg / .ico: the image proxy won't serve them
    "site_asset": -45,      # logo, sprite, favicon, the site's own UI images
    "avatar_like": -50,     # "avatar"/"flag"/"icon"/"badge" in the path or element
    "square_hint": -35,     # 400x400 in the URL: a photo, not a cover
    "tiny_hint": -60,       # under 200px either way
    "outside_account": -30,  # a JSON key outside the account's object, when the page has one
    "shared": -100,         # the same URL for two different accounts on one site (run-level)
}
MIN_SCORE = 60

MAX_JSON_NODES = 60_000
MAX_JSON_DEPTH = 40
MAX_STATE_BLOBS = 12
MAX_STATE_CHARS = 3_000_000
MAX_CANDIDATES = 80

# -- key dictionary ----------------------------------------------------------
# Keys are compared lowercased with "_" and "-" removed.

STRONG_KEYS = frozenset({
    "bannerurl", "bannerimage", "bannerimageurl", "bannerimg", "bannerexternalurl",
    "profilebanner", "profilebannerurl", "profilebannerurlhttps", "profilebannerimage",
    "userbanner", "userbannerurl", "channelbanner", "channelbannerurl", "channelart",
    "headerstatic", "headerimage", "headerimg", "headerphoto",
    "coverphoto", "coverphotourl", "coverimageurl", "profilecover", "profilecoverurl", "usercover",
    "visualurl", "profilebackgroundimage", "profilebackgroundimageurl",
})
VAGUE_KEYS = frozenset({
    "banner", "cover", "coverurl", "coverimage", "coverpicture", "header", "headerurl", "headerimageurl",
    "headermediaitem", "background", "backgroundurl", "backgroundimage", "backgroundimageurl",
    "backgroundpicture", "profilebackground", "profilebackgroundurl", "backdrop", "backdropurl",
    "wallpaper", "wallpaperurl", "offlinebannerimage",
})
WEAK_KEYS = frozenset({"heroimage", "hero", "artwork", "artworkurl", "splash", "splashurl"})
# An account API answer is about the account, so there its plain cover names
# are the account's own (the top-level object only).
API_KEYS = STRONG_KEYS | VAGUE_KEYS

# Keys whose value names the account: a match puts the object in context.
_IDENTITY_KEYS = frozenset({
    "username", "user_name", "userName", "screen_name", "screenName", "handle", "login", "slug", "acct",
    "uniqueId", "unique_id", "nickname", "permalink", "vanity", "vanityUrl", "vanity_url", "user_slug",
    "profile_url", "profileUrl", "url", "canonical_url", "uri",
})
# Keys that hold the account's picture: used to spot the avatar candidate.
_AVATAR_KEYS = frozenset({"avatar", "avatarurl", "avatarimage", "profileimage", "profileimageurl", "profilepic",
                          "profilepicurl", "picture", "photo", "photourl", "pfp", "icon", "iconurl", "image"})

# -- URL checks ------------------------------------------------------------

_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,24}|xn--[a-z0-9\-]{2,59})")
_PLACEHOLDER = re.compile(
    r"(missing|default|placeholder|blank|dummy|anonymous|noentry|no[-_]?(image|cover|banner|header|photo|avatar)|"
    r"empty[-_.]|fallback|generic[-_])", re.I)
_SITE_ASSET = re.compile(
    r"(logo|favicon|sprite|/static/|/assets/|/ui-images/|/skins?/|/themes?/|siteheader|site-header|"
    r"opengraph|og[-_]?image|og[-_]default|share[-_]?(image|card)|apple-touch|/dist/|/build/|webpack|"
    r"hamburger|spinner|loader|[-_/]icons?[-_./]|[-_/]menu[-_.]|/[^/]*arrow[^/]*\.(?:gif|png|svg)$|"
    r"/public/(?:shared/)?images/)", re.I)
_AVATAR_LIKE = re.compile(r"(avatar|/flags?/|_flags?/|tiny_flags|emoji|/icons?/|badge|profile[-_]?pic|portrait|"
                          r"profile[-_]?image|/profile\.(?:gif|png|jpe?g|webp)$)", re.I)
# An <img> drawn as a circle is the avatar, whatever box it sits in.
_ROUND_ELEMENT = re.compile(r"(rounded-full|rounded-circle|\bcircle\b|\bround\b|avatar|logo)", re.I)
# How far inside a cover container an image may sit and still be its image.
_MAX_INHERIT = 6
_BARE_SEGMENT = re.compile(r"^(cover|covers|banner|banners|header|headers|background|backgrounds|image|images|"
                           r"files|img|media|uploads|default)$", re.I)
_BAD_FORMAT = re.compile(r"\.(svg|ico)(?:$|[?#])", re.I)
# Size hints in a URL: 1500x500, w1420_h280, =w1060-..., width=..&height=..
_SIZE_WXH = re.compile(r"(?<![0-9])(\d{1,4})x(\d{1,4})(?![0-9])")
_SIZE_WH = re.compile(r"w(\d{2,4})[_-]h(\d{2,4})", re.I)
_SIZE_QUERY = re.compile(r"[?&]w(?:idth)?=(\d{2,4}).*?[?&]h(?:eight)?=(\d{2,4})", re.I)
# Path parts that only say which size of the same picture: dropped to compare
# two URLs for "the same image", never from the URL itself.
_VARIANT_BITS = re.compile(
    r"(=[swh]\d+[^/]*$|[-_](\d{2,4}x\d{2,4}|t\d{2,4}x\d{2,4}|normal|bigger|mini|full|medium|small|large|"
    r"original|thumb|thumbnail)(?=\.[a-z0-9]{3,4}$|$)|/(\d{2,4}x\d{2,4}|s\d{2,4}|w\d{2,4})/)", re.I)


def normalize(raw: Any, base: str = "") -> Optional[str]:
    """An absolute http(s) URL from what a page wrote, or None."""
    if not isinstance(raw, str):
        return None
    url = raw.strip().strip("'\"").strip()
    if not url or len(url) > 2048:
        return None
    # JSON inside a JSON string escapes its slashes twice ("https:\\/\\/").
    while "\\/" in url:
        url = url.replace("\\/", "/")
    url = (url.replace("\\u002F", "/").replace("\\u002f", "/")
           .replace("\\u0026", "&").replace("\\u003d", "=").replace("\\u003D", "="))
    url = html.unescape(url)
    if url.startswith("data:") or url.startswith("blob:"):
        return None
    if url.startswith("//"):
        url = "https:" + url
    if base and not url.lower().startswith(("http://", "https://")):
        if not base.lower().startswith(("http://", "https://")):
            return None
        url = urljoin(base, url)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password:
        return None
    host = (parts.hostname or "").lower()
    if not _HOST.fullmatch(host):
        return None
    return url


def variant_key(url: str) -> str:
    """The same picture's URLs at different sizes map to one key."""
    parts = urlsplit(url)
    path = parts.path
    for _ in range(3):
        new = _VARIANT_BITS.sub("", path)
        if new == path:
            break
        path = new
    return f"{(parts.hostname or '').lower()}{path.rstrip('/').lower()}"


def size_hint(url: str) -> Optional[tuple[int, int]]:
    for rx in (_SIZE_WH, _SIZE_QUERY, _SIZE_WXH):
        m = rx.search(url)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            if w and h:
                return w, h
    m = re.search(r"=w(\d{3,4})(?:-|$)", url)  # Google's =w1060: wide by convention
    if m:
        return int(m.group(1)), max(1, int(m.group(1)) // 3)
    return None


# -- candidates --------------------------------------------------------------

@dataclass
class Candidate:
    url: str
    layer: str                # api / json / dom / meta / site_pattern / youtube
    signal: str               # the key, class or tag that pointed at it
    base: int
    notes: list[tuple[str, int]] = field(default_factory=list)

    @property
    def score(self) -> int:
        return self.base + sum(delta for _reason, delta in self.notes)

    def note(self, reason: str, delta: int) -> None:
        if not any(r == reason for r, _d in self.notes):
            self.notes.append((reason, delta))


@dataclass
class Discovery:
    """What the search for a cover image found and why it chose what it did."""
    banner_url: Optional[str] = None
    source: Optional[str] = None
    confidence: Optional[int] = None
    layers: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    ms: float = 0.0

    def diagnostics(self) -> dict[str, Any]:
        rejected = [c for c in self.candidates if c.url != self.banner_url]
        reasons: dict[str, int] = {}
        for c in rejected:
            neg = [n for n in c.notes if n[1] < 0]
            worst = min(neg, key=lambda n: n[1])[0] if neg else ("never_chosen" if c.layer == "meta" else "low_score")
            reasons[worst] = reasons.get(worst, 0) + 1
        out: dict[str, Any] = {
            "layers": list(self.layers),
            "candidates": len(self.candidates),
            "rejected": len(rejected),
            "ms": round(self.ms, 1),
        }
        if reasons:
            out["reasons"] = reasons
        if self.banner_url:
            out["selected"] = {"source": self.source, "score": self.confidence}
        else:
            out["why_none"] = "all_rejected" if self.candidates else "no_candidates"
        return out

    def fields(self, stage: str = "page") -> dict[str, Any]:
        """The keys a finding's metadata carries. banner_diagnostics is a list,
        one entry per stage that looked (the page, a profile API, the site's
        pattern), so a later stage's reading doesn't hide an earlier one's."""
        out: dict[str, Any] = {"banner_diagnostics": [{"stage": stage, **self.diagnostics()}]}
        if self.banner_url:
            out.update(banner_url=self.banner_url, banner_source=self.source, banner_confidence=self.confidence)
        return out


BANNER_FIELDS = ("banner_url", "banner_source", "banner_confidence")


def merge(profile: dict[str, Any], later: dict[str, Any]) -> None:
    """Fold a later stage's banner reading into a profile, in place: a banner
    it found replaces the earlier one (later stages are the site's own
    sources: its profile API, then its pattern); the diagnostics of every
    stage are kept."""
    stages = list(profile.get("banner_diagnostics") or []) + list(later.get("banner_diagnostics") or [])
    if later.get("banner_url"):
        for key in BANNER_FIELDS:
            if later.get(key) is not None:
                profile[key] = later[key]
    if stages:
        profile["banner_diagnostics"] = stages


def _key(name: str) -> str:
    return name.replace("_", "").replace("-", "").lower()


def _url_in(value: Any) -> tuple[Optional[str], bool]:
    """(the picture URL a value holds, whether it says it is a default)."""
    if isinstance(value, list):
        urls = [_url_in(v) for v in value[:8]]
        urls = [u for u in urls if u[0]]
        return urls[-1] if urls else (None, False)
    if isinstance(value, dict):
        flagged = any(value.get(k) is True for k in ("isDefault", "is_default", "default", "isPlaceholder"))
        for k in ("url", "src", "href", "contentUrl", "img_url", "imgUrl", "original", "source", "uri",
                  "mediaurl", "media_url", "full", "large"):
            v = value.get(k)
            if isinstance(v, str) and v.strip():
                return v, flagged
            if isinstance(v, dict):
                inner, inner_flag = _url_in(v)
                if inner:
                    return inner, flagged or inner_flag
        # Sizes as keys ({"640x": ..., "2000x": ...}): the largest.
        sized = []
        for k, v in value.items():
            if isinstance(v, str) and v.strip().lower().startswith(("http://", "https://", "//")):
                m = re.search(r"\d+", str(k))
                sized.append((int(m.group()) if m else 0, v))
        if sized:
            return max(sized)[1], flagged
        return None, flagged
    if isinstance(value, str) and value.strip():
        return value, False
    return None, False


def _names_account(obj: dict, username: str) -> bool:
    if not username:
        return False
    u = username.lower().lstrip("@")
    for k, v in obj.items():
        if k not in _IDENTITY_KEYS or not isinstance(v, str):
            continue
        s = v.strip().lower()
        if s.lstrip("@") == u:
            return True
        if k in ("url", "profile_url", "profileUrl", "canonical_url", "uri", "permalink") and \
                re.search(r"/@?" + re.escape(u) + r"/?(?:$|[?#])", s):
            return True
    return False


class _Collector:
    def __init__(self, page_url: str, username: str) -> None:
        self.page_url = page_url
        self.username = username
        self.items: list[Candidate] = []
        self._seen: dict[str, Candidate] = {}
        # Whether the JSON read so far had an object that is the account's
        # own: then a cover key outside it belongs to something else on the
        # page (Misskey's server banner next to the user's "bannerUrl": null).
        self.owner_seen = False

    def add(self, raw: Any, layer: str, signal: str, base: int, *, flagged_default: bool = False,
            notes: Iterable[tuple[str, int]] = ()) -> Optional[Candidate]:
        url = normalize(raw, self.page_url)
        if not url or len(self.items) >= MAX_CANDIDATES:
            return None
        key = url
        cand = self._seen.get(key)
        if cand is not None:
            # The same URL from two places is one candidate: keep the better
            # reading (an API field over the same URL in the page's JSON).
            if base > cand.base:
                cand.base, cand.signal, cand.layer = base, signal, layer
            for r, d in notes:
                cand.note(r, d)
            return cand
        cand = Candidate(url=url, layer=layer, signal=signal, base=base)
        if flagged_default:
            cand.note("default_flag", PENALTIES["default_flag"])
        for r, d in notes:
            cand.note(r, d)
        self._seen[key] = cand
        self.items.append(cand)
        return cand


# -- layer: account API answer --------------------------------------------

def from_api_object(col: _Collector, obj: dict) -> None:
    """The top-level cover fields of an account API answer."""
    for k, v in obj.items():
        nk = _key(k)
        if nk in API_KEYS:
            url, flagged = _url_in(v)
            if url:
                col.add(url, "api", k, SCORES["api_field"], flagged_default=flagged)


# -- layer: embedded JSON ----------------------------------------------------

def walk_json(col: _Collector, data: Any, *, layer: str = "json", owner_obj: Optional[dict] = None) -> int:
    """Every cover-ish key anywhere in a JSON document, judged by the object
    around it. Returns how many nodes were visited.

    An object is the account's when it names the username (or is the API
    answer's account object). That context reaches its sub-objects
    ("profile": {...}, "images": {...}) two levels down, but never into a
    list: a list holds other things -- the account's posts, projects,
    followers -- whose "cover" is theirs, not the account's."""
    visited = 0
    stack: list[tuple[Any, int, int]] = [(data, 0, 0)]
    while stack and visited < MAX_JSON_NODES:
        node, depth, owner = stack.pop()
        visited += 1
        if depth > MAX_JSON_DEPTH:
            continue
        if isinstance(node, dict):
            here = 3 if (node is owner_obj or _names_account(node, col.username)) else owner
            if here == 3:
                col.owner_seen = True
            for k, v in node.items():
                if not isinstance(k, str):
                    continue
                nk = _key(k)
                tier = ("json_strong" if nk in STRONG_KEYS else "json_vague" if nk in VAGUE_KEYS
                        else "json_weak" if nk in WEAK_KEYS else None)
                if tier:
                    url, flagged = _url_in(v)
                    if url and (isinstance(v, (str, dict, list))):
                        notes = []
                        if here > 0:
                            notes.append(("owner_object", BONUSES["owner_object"] if tier != "json_strong"
                                          else BONUSES["owner_object_strong"]))
                        col.add(url, layer, k, SCORES[tier], flagged_default=flagged, notes=notes)
                if isinstance(v, dict):
                    stack.append((v, depth + 1, max(here - 1, 0)))
                elif isinstance(v, list):
                    stack.append((v, depth + 1, 0))
        elif isinstance(node, list):
            for item in node[:500]:
                if isinstance(item, (dict, list)):
                    stack.append((item, depth + 1, 0))
    return visited


_STATE_START = re.compile(
    r"(?:window\.)?(__NEXT_DATA__|__NUXT__|__INITIAL_STATE__|__INITIAL_DATA__|__APOLLO_STATE__|__PRELOADED_STATE__|"
    r"__REDUX_STATE__|__APP_STATE__|__STATE__|__DATA__|__INITIAL_PROPS__|__PINIA_STATE__)\s*=\s*", re.S)
_JSON_SCRIPT = re.compile(r"<script\b[^>]*type=[\"']application/(?:ld\+)?json[\"'][^>]*>(.*?)</script>", re.S | re.I)


def _balanced(text: str, start: int) -> Optional[str]:
    """The JSON object/array starting at text[start], braces in strings skipped."""
    if start >= len(text) or text[start] not in "{[":
        return None
    depth, in_str, esc = 0, False, False
    end = min(len(text), start + MAX_STATE_CHARS)
    for i in range(start, end):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def embedded_json(body: str) -> list[Any]:
    """Parsed JSON blobs a page embeds: <script type=application/json> (Next's
    __NEXT_DATA__, JSON-LD, ...) and window.__X__ = {...} assignments."""
    blobs: list[Any] = []
    for m in _JSON_SCRIPT.finditer(body):
        if len(blobs) >= MAX_STATE_BLOBS:
            break
        raw = html.unescape(m.group(1).strip()) if "&quot;" in m.group(1)[:200] else m.group(1).strip()
        try:
            blobs.append(json.loads(raw))
        except ValueError:
            continue
    for m in _STATE_START.finditer(body):
        if len(blobs) >= MAX_STATE_BLOBS:
            break
        raw = _balanced(body, m.end())
        if not raw:
            continue
        try:
            blobs.append(json.loads(raw))
        except ValueError:
            # JS, not JSON (undefined, functions): the raw-text scan below
            # still sees its strong keys.
            continue
    return blobs


# The strong keys straight from the text, for state that isn't valid JSON
# (escaped JSON inside a string, a Nuxt 2 function, JSON.parse("...")).
_RAW_STRONG = re.compile(
    r'\\?"([A-Za-z_]*?(?:banner|Banner|cover|Cover|header|Header|visual|Visual)[A-Za-z_]*)\\?"\s*:\s*\\?"'
    r'((?:https?:)?(?:\\{0,2}/){2}(?:[^"\\\s]|\\{1,2}/|\\u0026){4,600})')
_YOUTUBE_BANNER = re.compile(r'"imageBannerViewModel":\{"image":\{"sources":\[(.*?)\]', re.S)
_YOUTUBE_SOURCE = re.compile(r'"url":"(https://yt3\.googleusercontent\.com/[^"]+)"')


def from_raw_text(col: _Collector, body: str) -> None:
    yt = _YOUTUBE_BANNER.search(body)
    if yt:
        sources = _YOUTUBE_SOURCE.findall(yt.group(1))
        if sources:
            col.add(sources[-1], "youtube", "imageBannerViewModel", SCORES["youtube_banner"])
    for m in _RAW_STRONG.finditer(body):
        if _key(m.group(1)) in STRONG_KEYS:
            col.add(m.group(2), "json", m.group(1), SCORES["json_strong"])


# -- layer: DOM / CSS ----------------------------------------------------------

_CONTAINER_TOKEN = re.compile(
    r"(banner|cover|backdrop|channel[-_]?art|hero|profile[-_a-z]*?(background|bg|header|cover|wall)|"
    r"header[-_a-z]*?(image|img|photo|bg|background|media|picture|cover)|"
    r"user[-_]?(header|cover|background))", re.I)
# Utility classes that only set object-fit / background-size, and names of
# the page's own furniture.
_NOT_CONTAINER = re.compile(
    r"^(object|bg|background|img|image)[-_](fit[-_])?(cover|contain)$|^object-fit-cover$|^cover-fit$|"
    r"site|global|nav|menu|logo|^ads?$|[-_]ads?(?:[-_]|$)|advert|promo|sponsor|cookie|footer|flag|icon|badge|"
    r"avatar|emoji|btn|button|product|thumb|card-image|post[-_]|article|game[-_]?cover|album|track|playlist|"
    r"discover|search|category-cover|cover[-_]?art|artwork", re.I)
_BG_URL = re.compile(r"background(?:-image)?\s*:[^;]*?url\(\s*(?:&quot;|&#x27;|['\"])?([^)'\"&]+?)(?:&quot;|&#x27;|['\"])?\s*\)", re.I)
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
                   "track", "wbr"})


def _container_signal(attrs: dict[str, str]) -> Optional[str]:
    names = " ".join(attrs.get(k, "") for k in ("id", "class", "data-testid", "data-test", "data-role"))
    for token in re.split(r"\s+", names.strip()):
        if token and _CONTAINER_TOKEN.search(token) and not _NOT_CONTAINER.search(token):
            return token[:60]
    return None


def _largest_srcset(srcset: str) -> Optional[str]:
    best, best_w = None, -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        w = 0
        if len(bits) > 1:
            m = re.match(r"(\d+)(w|x)", bits[1])
            if m:
                w = int(m.group(1)) * (1000 if m.group(2) == "x" else 1)
        if w >= best_w:
            best, best_w = bits[0], w
    return best


class DomCollector:
    """Fed tag by tag by the page parser (profile_extract._Page), so the page
    is parsed once."""

    def __init__(self) -> None:
        self.found: list[tuple[str, str, str]] = []   # (raw url, kind, signal)
        self._stack: list[tuple[str, Optional[str]]] = []

    def _inside(self) -> tuple[Optional[str], int]:
        """The nearest cover container around this point and how many levels
        up it is. A page-wide wrapper (<body class="has_profile_background">)
        names only its own background, not every image on the page."""
        for distance, (tag, signal) in enumerate(reversed(self._stack), start=1):
            if signal:
                if tag in ("body", "html", "main") or distance > _MAX_INHERIT:
                    return None, 0
                return signal, distance
        return None, 0

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        if len(self.found) >= MAX_CANDIDATES:
            return
        own = _container_signal(attrs)
        inherited, distance = (None, 0) if own else self._inside()
        signal = own or inherited
        if signal:
            style = attrs.get("style", "")
            for m in _BG_URL.finditer(style):
                self.found.append((m.group(1), "dom_background", signal))
            for k in ("data-background", "data-bg", "data-background-image", "data-bg-src"):
                if attrs.get(k):
                    self.found.append((attrs[k], "dom_background", signal))
            if tag in ("img", "source", "video", "image"):
                element_names = " ".join(attrs.get(k, "") for k in ("class", "id", "alt"))
                kind = "dom_image" if own or distance <= 2 else "dom_image_deep"
                if not _AVATAR_LIKE.search(element_names) and not _ROUND_ELEMENT.search(element_names):
                    for k in ("poster", "data-src", "data-original", "data-lazy-src", "src", "href", "xlink:href"):
                        if attrs.get(k):
                            self.found.append((attrs[k], kind, signal))
                            break
                    else:
                        if attrs.get("srcset") or attrs.get("data-srcset"):
                            best = _largest_srcset(attrs.get("srcset") or attrs.get("data-srcset", ""))
                            if best:
                                self.found.append((best, kind, signal))
        if tag not in _VOID:
            self._stack.append((tag, own))
            if len(self._stack) > 400:
                self._stack.pop(0)

    def end(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return


# -- judging -------------------------------------------------------------------

def _judge(cand: Candidate, avatar_key: Optional[str], page_host: str) -> None:
    parts = urlsplit(cand.url)
    host = (parts.hostname or "").lower()
    path = parts.path
    where = f"{host}{path}"
    last = path.rstrip("/").rsplit("/", 1)[-1]
    # A picture has a file to it: a bare host, a folder ("/files/cover") or a
    # page ("cursor.com/help": one plain word, no digit, no query) isn't one.
    # An image service named by its query ("/figure?f=...") is.
    if (len(path.strip("/")) < 3 or _BARE_SEGMENT.match(last or "")
            or (re.fullmatch(r"[A-Za-z-]{1,24}", last or "") and not re.search(r"\d", path)
                and not parts.query)):
        cand.note("not_a_file", PENALTIES["not_a_file"])
    if _BAD_FORMAT.search(path):
        cand.note("unsupported_format", PENALTIES["unsupported_format"])
    if _PLACEHOLDER.search(where):
        cand.note("placeholder", PENALTIES["placeholder"])
    if _SITE_ASSET.search(path) or (host.removeprefix("www.") == page_host and
                                    re.search(r"/(images?|img|ui|app)/", path, re.I) and
                                    cand.layer in ("dom", "meta")):
        cand.note("site_asset", PENALTIES["site_asset"])
    if _AVATAR_LIKE.search(path) or _AVATAR_LIKE.search(parts.query) or "avatar" in host:
        cand.note("avatar_like", PENALTIES["avatar_like"])
    if avatar_key and variant_key(cand.url) == avatar_key:
        cand.note("avatar_duplicate", PENALTIES["avatar_duplicate"])
    size = size_hint(cand.url)
    if not size and re.search(r"/(\d{2,3})s/", path):   # Last.fm's /i/u/64s/
        size = (int(re.search(r"/(\d{2,3})s/", path).group(1)),) * 2
    if size:
        w, h = size
        if max(w, h) < 200:
            cand.note("tiny_hint", PENALTIES["tiny_hint"])
        elif 0.8 <= w / h <= 1.25:
            cand.note("square_hint", PENALTIES["square_hint"])
        elif w / h >= 1.8:
            cand.note("wide_hint", BONUSES["wide_hint"])


_LAYER_ORDER = {"site_pattern": 0, "api": 1, "youtube": 2, "json": 3, "dom": 4, "meta": 5}


def choose(col: _Collector, avatar_url: Optional[str], layers: list[str], started: float) -> Discovery:
    avatar = normalize(avatar_url or "", col.page_url) if avatar_url else None
    avatar_key = variant_key(avatar) if avatar else None
    page_host = (urlsplit(col.page_url).hostname or "").lower().removeprefix("www.")
    for cand in col.items:
        _judge(cand, avatar_key, page_host)
        if (col.owner_seen and cand.layer == "json"
                and not any(reason == "owner_object" for reason, _d in cand.notes)):
            cand.note("outside_account", PENALTIES["outside_account"])
    result = Discovery(layers=layers, candidates=col.items)
    ranked = sorted((c for c in col.items if c.score >= MIN_SCORE),
                    key=lambda c: (-c.score, _LAYER_ORDER.get(c.layer, 9)))
    if ranked:
        best = ranked[0]
        result.banner_url = best.url
        result.source = f"{best.layer}:{best.signal}"
        result.confidence = max(0, min(100, best.score))
    result.ms = (time.perf_counter() - started) * 1000
    return result


def discover(*, page_url: str, username: str, avatar_url: Optional[str] = None,
             api_object: Optional[dict] = None, json_data: Any = None, body: str = "",
             dom: Optional[DomCollector] = None, meta: Optional[dict[str, str]] = None) -> Discovery:
    """Collect every candidate the inputs hold and pick the account's cover.

    api_object  -- the account object of an API answer (top-level fields count
                   as the account's own);
    json_data   -- the whole parsed API answer, walked for nested covers;
    body        -- a page's HTML: its embedded JSON and raw text are read;
    dom         -- the DomCollector the page parser fed;
    meta        -- the page's <meta> tags, for the record.
    """
    started = time.perf_counter()
    col = _Collector(page_url, username)
    layers: list[str] = []
    try:
        if api_object:
            layers.append("api")
            from_api_object(col, api_object)
        if json_data is not None:
            layers.append("json")
            walk_json(col, json_data, owner_obj=api_object)
        if body:
            layers.append("embedded_json")
            for blob in embedded_json(body):
                walk_json(col, blob)
            from_raw_text(col, body)
        if dom is not None:
            layers.append("dom")
            for raw, kind, signal in dom.found:
                col.add(raw, "dom", signal, SCORES[kind])
        if meta:
            layers.append("meta")
            for k in ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src", "image_src", "image"):
                if meta.get(k):
                    col.add(meta[k], "meta", k, SCORES["meta_image"])
    except Exception as exc:  # noqa: BLE001 -- a broken page still leaves what was collected
        log.debug("banner discovery stopped early on %s: %s", page_url, exc)
    result = choose(col, avatar_url, layers, started)
    log.debug("banner discovery %s", json.dumps({"page": urlsplit(page_url).netloc, **result.diagnostics()}))
    return result


def from_pattern(url: Optional[str], *, page_url: str = "", avatar_url: Optional[str] = None) -> Discovery:
    """A site entry's banner_pattern match, judged like any other candidate
    (it still can't be a placeholder or the avatar)."""
    started = time.perf_counter()
    col = _Collector(page_url, "")
    if url:
        col.add(url, "site_pattern", "banner_pattern", SCORES["site_pattern"])
    return choose(col, avatar_url, ["site_pattern"], started)


def api_banner(url: Any, *, avatar_url: Optional[str] = None, page_url: str = "",
               signal: str = "banner") -> dict[str, Any]:
    """For the dedicated plugins: their API's cover field, checked the same way
    (placeholder, avatar duplicate, format) -- the metadata keys to merge."""
    started = time.perf_counter()
    col = _Collector(page_url, "")
    raw, flagged = _url_in(url)
    if raw:
        col.add(raw, "api", signal, SCORES["api_field"], flagged_default=flagged)
    return choose(col, avatar_url, ["api"], started).fields("api")


def drop_shared_banners(findings: list) -> int:
    """Across one run: a cover URL that two different accounts on the same
    source both got is the site's own picture (an instance banner, a stock
    background), not either account's. Removes it from those findings, notes
    why in their diagnostics, and returns how many were dropped."""
    owners: dict[tuple[str, str], set[str]] = {}
    for f in findings:
        url = (f.metadata or {}).get("banner_url")
        if url:
            owners.setdefault((f.source, url), set()).add(f.identifier.value.lower())
    shared = {key for key, users in owners.items() if len(users) > 1}
    dropped = 0
    for f in findings:
        meta = f.metadata or {}
        if (f.source, meta.get("banner_url")) in shared:
            for key in BANNER_FIELDS:
                meta.pop(key, None)
            meta["banner_diagnostics"] = list(meta.get("banner_diagnostics") or []) + [
                {"stage": "run", "dropped": "shared_with_other_accounts", "reasons": {"shared": 1}}]
            dropped += 1
    return dropped
