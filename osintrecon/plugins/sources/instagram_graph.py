"""Instagram through Meta's official Graph API (Business Discovery).

The only officially supported way to look up another Instagram account by
username. It answers for professional accounts (Business and Creator) only:
for those it returns the username, name, biography, website and counts,
straight from Instagram. For a personal account, an age-gated one or a name
nobody has, the API answers the same way ("cannot find user", error 110 /
subcode 2207013), so this module reports nothing then -- never "not found".

Why it exists: anonymous requests to instagram.com no longer carry profile
data. Checked 2026-11-06 with one request per case: from a home connection
the profile URL served the same generic error page for a real account and a
random name, and the web_profile_info JSON endpoint answered 429 at once;
from the production server the profile URL redirected to the login page and
web_profile_info answered 404 even for the real "instagram" account. The
username_sites check reports those as restrictions (see docs/INSTAGRAM.md);
this module is the supported route for what can still be answered.

Setup (all yours; nothing is shared or bundled):
  1. A Meta developer app with the "Instagram API with Facebook Login" product.
  2. An Instagram professional account of your own, linked to a Facebook Page.
  3. A user access token with instagram_basic, instagram_manage_insights and
     pages_read_engagement (long-lived tokens last about 60 days).
  4. Config: sources.instagram_graph.api_key = the token (or the environment
     variable OSINTRECON_INSTAGRAM_GRAPH_API_KEY) and
     sources.instagram_graph.ig_user_id = your own professional account's id.

The token goes in the Authorization header, never in the URL, so it can't
end up in logs, cache keys or saved evidence. Responses are not cached.

NOT live-verified by the maintainer: no Graph API token was available in
this environment. The request and error shapes follow Meta's reference for
the business_discovery field and its documented error codes.
"""
from __future__ import annotations

from typing import Any, ClassVar, Optional
from urllib.parse import quote

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

GRAPH_HOST = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v27.0"
PROFILE_FIELDS = "username,name,biography,website,followers_count,follows_count,media_count,profile_picture_url"

# Meta's documented throttling codes: app (4), user (17), page (32), custom
# rate (613) and Instagram business-use-case (80002) limits.
_RATE_LIMIT_CODES = {4, 17, 32, 613, 80002}
_EXPIRED_SUBCODES = {463, 467}       # token expired / session invalidated
_PERMISSION_CODES = {10, 200, 3}     # missing permission or capability


class InstagramGraphPlugin(SourcePlugin):
    name: ClassVar[str] = "instagram_graph"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    requires_api_key: ClassVar[bool] = True
    description: ClassVar[str] = (
        "Looks a username up through Meta's official Instagram Graph API (Business Discovery) -- "
        "professional accounts only; needs your own access token and Instagram account id. "
        "NOT live-verified, see the module's docstring."
    )

    def is_configured(self) -> bool:
        return bool(self.config.get("api_key")) and bool(str(self.config.get("ig_user_id") or "").strip())

    def _profile_url(self, username: str) -> str:
        return f"https://www.instagram.com/{quote(username, safe='')}/"

    async def run(self, identifier: Identifier) -> list[Finding]:
        username = identifier.value.lower()
        if not username.replace(".", "").replace("_", "").isalnum() or len(username) > 30:
            return []  # not a name Instagram allows; nothing to ask
        version = str(self.config.get("api_version") or DEFAULT_API_VERSION)
        ig_user_id = quote(str(self.config["ig_user_id"]).strip(), safe="")
        url = f"{GRAPH_HOST}/{version}/{ig_user_id}"
        params = {"fields": f"business_discovery.username({username}){{{PROFILE_FIELDS}}}"}
        headers = {"Authorization": f"OAuth {self.config['api_key']}"}
        resp = await self.http.get(self.name, url, params=params, headers=headers,
                                   expected_statuses={400, 403}, allow_cache=False)

        if resp.error is not None:
            return [self._problem(identifier, "TEMPORARILY_UNAVAILABLE", "Instagram Graph API request failed",
                                  {"error": resp.error})]
        body = _json(resp)
        error = body.get("error") if isinstance(body, dict) else None
        if resp.status == 200 and isinstance(body, dict) and isinstance(body.get("business_discovery"), dict):
            return [self._found(identifier, body["business_discovery"])]
        if resp.status == 429 or (error and error.get("code") in _RATE_LIMIT_CODES):
            return [self._problem(identifier, "RATE_LIMITED", "Instagram Graph API: rate limit reached",
                                  {"http_status": resp.status, **_error_meta(error)},
                                  cooldown_until=getattr(resp, "cooldown_until", None))]
        if error and error.get("code") == 110 and error.get("error_subcode") == 2207013:
            # A personal or age-gated account, or no account at all: the API
            # can't tell them apart, so neither can this module.
            return []
        if error and error.get("code") == 190:
            expired = error.get("error_subcode") in _EXPIRED_SUBCODES
            return [self._problem(
                identifier, "SESSION_EXPIRED" if expired else "AUTH_REQUIRED",
                "Instagram Graph API: access token expired -- create a new one"
                if expired else "Instagram Graph API: access token rejected",
                {"http_status": resp.status, **_error_meta(error)}, retry=False)]
        if error and error.get("code") in _PERMISSION_CODES:
            return [self._problem(identifier, "AUTH_REQUIRED",
                                  "Instagram Graph API: the token lacks a required permission",
                                  {"http_status": resp.status, **_error_meta(error)}, retry=False)]
        if resp.status == 200:
            return [self._problem(identifier, "PARTIAL" if body else "ERROR",
                                  "Instagram Graph API: unexpected answer", {"http_status": 200})]
        return [self._problem(identifier, "ERROR", f"Instagram Graph API returned HTTP {resp.status}",
                              {"http_status": resp.status, **_error_meta(error)})]

    def _found(self, identifier: Identifier, user: dict[str, Any]) -> Finding:
        username = user.get("username") or identifier.value.lower()
        return Finding(
            source=self.name, identifier=identifier, status=MatchStatus.CONFIRMED,
            source_url=self._profile_url(username),
            title=f"Instagram professional account: @{username}" + (f" ({user['name']})" if user.get("name") else ""),
            category=self.category,
            metadata={
                "platform": "Instagram",
                "username": username,
                "name": user.get("name"),
                "biography": user.get("biography"),
                "website": user.get("website"),
                "followers_count": user.get("followers_count"),
                "follows_count": user.get("follows_count"),
                "media_count": user.get("media_count"),
                "profile_picture_url": user.get("profile_picture_url"),
                "account_type": "professional",
                "source_status": "SUCCESS",
                "data_origin": "official_api",
                "verification": "verified",
                "retry_eligible": False,
            },
        )

    def _problem(self, identifier: Identifier, status: str, title: str, extra: dict[str, Any],
                 retry: bool = True, cooldown_until: Optional[float] = None) -> Finding:
        metadata: dict[str, Any] = {"source_status": status, "data_origin": "official_api",
                                    "verification": "unverified", "retry_eligible": retry, **extra}
        if cooldown_until:
            from datetime import datetime, timezone
            metadata["cooldown_until"] = datetime.fromtimestamp(cooldown_until, tz=timezone.utc).isoformat(
                timespec="seconds")
        return Finding(source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                       source_url=self._profile_url(identifier.value.lower()), title=title,
                       category=self.category, metadata=metadata)


def _json(resp) -> Any:
    try:
        return resp.json()
    except (ValueError, TypeError):
        return None


def _error_meta(error: Optional[dict]) -> dict[str, Any]:
    if not error:
        return {}
    # Meta's message and codes only; never anything from the request.
    return {k: error.get(k) for k in ("code", "error_subcode", "type", "message") if error.get(k) is not None}
