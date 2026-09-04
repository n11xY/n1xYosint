"""Telegram Bot API source module -- uses the official, documented Bot API
(`getChat`) to check whether a username resolves to a real chat (user,
channel, bot, or group). Requires a free bot token from @BotFather
(message @BotFather on Telegram, /newbot) -- config:
sources.telegram_api.api_key (the bot token itself, no "bot" prefix).

Added because the generic `username_sites` check for t.me/{username} was
found to be fundamentally unfixable: live-testing (diffing the full page
with the username normalized out) showed Telegram's public web preview
page renders byte-identical boilerplate for a real and a nonexistent
username -- literally nothing distinguishes them in the unauthenticated
HTML, only client-side JS the tool's HTTP client never executes. That
username_sites entry is kept (see config/sites.json) but deliberately
configured so it never reports a false match; this plugin is the actual
reliable signal for Telegram, when a bot token is configured.

NOT live-verified by the maintainer: no bot token was available in this
environment. In particular, Telegram's own documentation isn't fully
explicit about whether getChat resolves a PRIVATE user account's public
@username the same way it resolves a channel/group/bot username, or
whether it requires the user to have started a conversation with the bot
first. Verify against a real, known personal-account username and a
clearly nonexistent one before relying on this for individual accounts
specifically; channel/bot username resolution is expected to work either
way, since those are meant to be publicly discoverable by design.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://api.telegram.org/bot{token}/getChat"


class TelegramAPIPlugin(SourcePlugin):
    name: ClassVar[str] = "telegram_api"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    requires_api_key: ClassVar[bool] = True
    description: ClassVar[str] = (
        "Checks whether a username resolves via Telegram's official Bot API (getChat) -- "
        "requires a free bot token from @BotFather. NOT live-verified, see the module's docstring."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        token = self.config["api_key"]
        url = API_URL.format(token=token)
        params = {"chat_id": f"@{identifier.value}"}
        resp = await self.http.get(self.name, url, params=params, expected_statuses={400, 403, 404})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=f"https://t.me/{identifier.value}", title="Telegram Bot API request failed",
                category=self.category, metadata={"error": resp.error},
            )]

        data = resp.json() or {}
        if resp.status != 200 or not data.get("ok"):
            # getChat responds with ok:false (typically HTTP 400, "Bad
            # Request: chat not found") for a username that doesn't
            # resolve to anything -- not an error worth surfacing.
            return []

        chat = data.get("result") or {}
        chat_type = chat.get("type", "unknown")
        display_name = chat.get("title") or " ".join(
            filter(None, [chat.get("first_name"), chat.get("last_name")])
        ) or identifier.value

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://t.me/{identifier.value}",
            title=f"Telegram {chat_type}: {display_name}",
            category=self.category,
            metadata={
                "chat_type": chat_type,
                "title": chat.get("title"),
                "first_name": chat.get("first_name"),
                "last_name": chat.get("last_name"),
                "bio": chat.get("bio"),
                "description": chat.get("description"),
            },
            evidence_path=resp.evidence_path,
        )]
