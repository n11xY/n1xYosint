"""YouTube Data API v3 source module -- uses the official, documented
`channels.list?forHandle=` endpoint to confirm a @handle resolves to a
real channel. Requires a free Google Cloud API key with the "YouTube Data
API v3" enabled (console.cloud.google.com -> APIs & Services -> Credentials;
free tier quota is generous for occasional lookups). Config:
sources.youtube_api.api_key.

The generic username_sites check for youtube.com/@{handle} (a plain
status-code check) was live-tested and found to work correctly in most
cases -- unlike Telegram/Threads, there's no fundamental reason it can't
work -- but this plugin exists alongside it anyway, for the same reason
twitch_api/steam_api exist alongside their own username_sites entries:
an official API result is a stronger, CONFIRMED-grade signal than a bare
HTTP status code, which can still occasionally be affected by bot
detection or A/B-tested page variants outside this project's control.

NOT live-verified by the maintainer: no Google Cloud API key was
available in this environment. `forHandle` is a real, documented
parameter (added when YouTube introduced @handles), but verify against a
real handle and a clearly nonexistent one before relying on this.

Without a key, the channel's photo, banner and subscriber count come from
the documented public APIs of the open-source YouTube front ends Piped
(`/c/@handle`) and Invidious (`/api/v1/resolveurl` + `/api/v1/channels`),
asked in turn until one answers. youtube.com itself can't be read for
them from the EU: it serves a consent page instead of the channel. These
instances are run by volunteers and come and go (on 2026-11-13 one of nine
Piped and one Invidious instance answered), so the list is configurable
(sources.youtube_api.piped_instances / invidious_instances) and a miss
just leaves the YouTube card without them; the channel's existence is the
username site check's to report. The pictures are linked at YouTube's own
image host (yt3), not through the instance's proxy.
"""
from __future__ import annotations

import re
from typing import Any, ClassVar, Optional
from urllib.parse import parse_qs, quote, urlsplit

from osintrecon.core.logging_setup import get_logger
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://www.googleapis.com/youtube/v3/channels"
DEFAULT_PIPED = ("https://api.piped.private.coffee",)
DEFAULT_INVIDIOUS = ("https://yt.chocolatemoo53.com",)
# YouTube handles: 3-30 letters, digits, dots, dashes, underscores.
HANDLE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
log = get_logger("youtube_api")


def _youtube_image(url: Any, size: Optional[str] = None) -> Optional[str]:
    """A picture link at YouTube's own image host. Piped proxies them as
    https://proxy.example/<path>?host=yt3.googleusercontent.com, Invidious as
    https://instance/ggpht/<path>; both name the same picture on yt3. `size`
    replaces the leading size code ("=s160" or "=w2560") with another."""
    if not isinstance(url, str) or not url.startswith(("https://", "http://", "//")):
        return None
    parts = urlsplit(url if not url.startswith("//") else "https:" + url)
    host = (parse_qs(parts.query).get("host") or [""])[0]
    path = parts.path
    if path.startswith("/ggpht/"):
        host, path = host or "yt3.ggpht.com", path[len("/ggpht"):]
    if not host:
        if parts.hostname and parts.hostname.endswith(("googleusercontent.com", "ggpht.com")):
            host = parts.hostname
        else:
            return None
    if not host.endswith(("googleusercontent.com", "ggpht.com")):
        return None
    if size:
        path = re.sub(r"=[sw]\d+", "=" + size, path, count=1)
    return f"https://{host}{path}"


class YouTubeAPIPlugin(SourcePlugin):
    name: ClassVar[str] = "youtube_api"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = (
        "A @handle's channel via the official YouTube Data API v3 with a free Google Cloud key; "
        "without one, its photo, banner and subscribers from public Piped/Invidious instances."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        if not self.config.get("api_key"):
            return await self._run_keyless(identifier)
        params = {
            # brandingSettings carries the channel banner; same quota cost
            "part": "snippet,statistics,brandingSettings",
            "forHandle": identifier.value,
            "key": self.config["api_key"],
        }
        resp = await self.http.get(self.name, API_URL, params=params, expected_statuses={400, 403})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=f"https://www.youtube.com/@{identifier.value}",
                title="YouTube Data API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 403:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=f"https://www.youtube.com/@{identifier.value}",
                title="YouTube Data API quota exceeded or key invalid", category=self.category,
                metadata={"http_status": 403},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=f"https://www.youtube.com/@{identifier.value}",
                title=f"YouTube Data API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        items = (resp.json() or {}).get("items") or []
        if not items:
            return []

        channel = items[0]
        snippet = channel.get("snippet") or {}
        stats = channel.get("statistics") or {}
        banner = ((channel.get("brandingSettings") or {}).get("image") or {}).get("bannerExternalUrl")
        thumbs = snippet.get("thumbnails") or {}
        avatar = next((thumbs[k].get("url") for k in ("high", "medium", "default") if isinstance(thumbs.get(k), dict)), None)

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://www.youtube.com/@{identifier.value}",
            title=f"YouTube channel: {snippet.get('title', identifier.value)}",
            category=self.category,
            metadata={
                "channel_id": channel.get("id"),
                "display_name": snippet.get("title"),
                "avatar_url": avatar,
                # the banner URL comes without a size; =w1060 asks for a card-sized one
                "banner_url": f"{banner}=w1060" if isinstance(banner, str) and banner.startswith("https://") else None,
                "description": snippet.get("description"),
                "published_at": snippet.get("publishedAt"),
                "country": snippet.get("country"),
                "subscriber_count": stats.get("subscriberCount"),
                "video_count": stats.get("videoCount"),
                "view_count": stats.get("viewCount"),
            },
            evidence_path=resp.evidence_path,
        )]

    # -- without a key: Piped, then Invidious ----------------------------------

    async def _run_keyless(self, identifier: Identifier) -> list[Finding]:
        handle = identifier.value.lstrip("@")
        if not HANDLE.match(handle):
            return []
        # Every instance in turn until one returns the channel: an instance's
        # "not found" isn't final (Piped answers it for some real channels),
        # and whether the channel exists is the site check's to say anyway.
        lookups = [(self._piped, base) for base in self.config.get("piped_instances") or DEFAULT_PIPED]
        lookups += [(self._invidious, base) for base in self.config.get("invidious_instances") or DEFAULT_INVIDIOUS]
        for lookup, base in lookups:
            got = await lookup(base.rstrip("/"), handle)
            if got:
                return self._keyless_finding(identifier, handle, got)
        log.debug("no Piped/Invidious instance returned %s", handle)
        return []

    @staticmethod
    def _missing(resp) -> bool:
        """The instance answered that YouTube has no such channel."""
        text = (resp.text or "")[:600]
        return resp.status in (400, 404, 500) and bool(re.search(r"404|not found|does not exist", text, re.I))

    async def _piped(self, base: str, handle: str) -> Optional[dict]:
        """{} for "no such channel", None for "this instance didn't answer"."""
        resp = await self.http.get(self.name, f"{base}/c/@{quote(handle, safe='')}", expected_statuses={400, 404, 500})
        if resp.error is not None:
            return None
        if resp.status == 200:
            data = resp.json() or {}
            if not data.get("id"):
                return None
            return {"via": base, "channel_id": data.get("id"), "display_name": data.get("name"),
                    "avatar": data.get("avatarUrl"), "banner": data.get("bannerUrl"),
                    "subscribers": data.get("subscriberCount"), "description": data.get("description")}
        return {} if self._missing(resp) else None

    async def _invidious(self, base: str, handle: str) -> Optional[dict]:
        page = quote(f"https://www.youtube.com/@{handle}", safe="")
        resp = await self.http.get(self.name, f"{base}/api/v1/resolveurl?url={page}", expected_statuses={400, 404, 500})
        if resp.error is not None:
            return None
        if resp.status != 200:
            return {} if self._missing(resp) else None
        ucid = (resp.json() or {}).get("ucid")
        if not ucid:
            return None
        resp = await self.http.get(self.name, f"{base}/api/v1/channels/{quote(ucid, safe='')}"
                                   "?fields=author,authorId,authorThumbnails,authorBanners,subCount,description",
                                   expected_statuses={400, 404, 500})
        if resp.error is not None or resp.status != 200:
            return None
        data = resp.json() or {}
        thumbs = sorted(data.get("authorThumbnails") or [], key=lambda t: t.get("width") or 0)
        banners = sorted(data.get("authorBanners") or [], key=lambda t: t.get("width") or 0)
        return {"via": base, "channel_id": data.get("authorId"), "display_name": data.get("author"),
                "avatar": (thumbs[-1] if thumbs else {}).get("url"),
                "banner": (banners[-1] if banners else {}).get("url"),
                "subscribers": data.get("subCount"), "description": data.get("description")}

    def _keyless_finding(self, identifier: Identifier, handle: str, got: dict) -> list[Finding]:
        subscribers = got.get("subscribers")
        if isinstance(subscribers, str) and subscribers.isdigit():
            subscribers = int(subscribers)
        return [Finding(
            source=self.name, identifier=identifier, status=MatchStatus.CONFIRMED,
            source_url=f"https://www.youtube.com/@{handle}",
            title=f"YouTube channel: {got.get('display_name') or handle}", category=self.category,
            metadata={
                "channel_id": got.get("channel_id"),
                "display_name": got.get("display_name"),
                "avatar_url": _youtube_image(got.get("avatar"), "s240"),
                # a card-sized banner rather than the 2560px original
                "banner_url": _youtube_image(got.get("banner"), "w1060"),
                "subscriber_count": subscribers if isinstance(subscribers, int) else None,
                "description": (got.get("description") or "")[:500] or None,
                "data_origin": f"third_party ({urlsplit(got['via']).hostname})",
            },
        )]
