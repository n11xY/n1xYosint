"""Wikipedia source module -- uses the official, free, keyless Wikipedia
OpenSearch API to check whether a full name matches a notable public
figure's article.

A match only confirms Wikipedia has an article with that (or a very
similar) title -- not that it's the specific person being investigated;
common names can collide with an unrelated notable figure who happens to
share it, and OpenSearch does prefix/fuzzy matching, not exact identity
confirmation. Always reported as UNCERTAIN, same reasoning as
github_name_search.py and the existing precedent in search_api.py ("a
keyword hit isn't proof of identity").

Live-verified: searching "Linus Torvalds" returns his article; a random
nonexistent name returns an empty result set, not an error.

Tries the exact name first; if that returns no titles, retries once
with the ASCII-folded form (name_variants.py) -- OpenSearch's prefix
matching is not reliably diacritic-tolerant for names like Turkish ones.

For a username it asks a different question: does a Wikimedia account by
that name exist? One login covers every Wikimedia wiki (Wikipedia in every
language, Commons, Wikidata, ...), and the CentralAuth API
(meta=globaluserinfo) answers for all of them at once -- with the home
wiki, registration date, total edits and the wikis actually edited. This
replaced username_sites' "Wikipedia (user page)" entry, which only saw
English Wikipedia, missed every account without a user page, and now gets
HTTP 403 for any request with a browser-imitating User-Agent (Wikimedia's
User-Agent policy) -- this module sends the client's own honest one.
Account names are exact, so a hit is CONFIRMED.

Live-verified: "Jimbo Wales" returns home "enwiki" and the merged list;
a random name returns {"globaluserinfo": {"missing": ""}}.
"""
from __future__ import annotations

from typing import ClassVar
from urllib.parse import quote

from osintrecon.core import name_variants
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://en.wikipedia.org/w/api.php"
CENTRALAUTH_URL = "https://meta.wikimedia.org/w/api.php"
ACCOUNT_PAGE = "https://meta.wikimedia.org/wiki/Special:CentralAuth/{}"
TOP_WIKIS = 5
MAX_RESULTS = 3
MAX_RESULTS_BY_DEPTH = {"quick": 1, "normal": MAX_RESULTS, "deep": 6}


class WikipediaPlugin(SourcePlugin):
    name: ClassVar[str] = "wikipedia"
    category: ClassVar[str] = "profile-directory"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.NAME, IdentifierType.USERNAME}
    description: ClassVar[str] = (
        "Name: checks Wikipedia's official, free, keyless OpenSearch API for an article matching "
        "a full name -- always UNCERTAIN, a title match alone is never proof of identity. "
        "Username: looks the account up across every Wikimedia wiki via the CentralAuth API."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        if identifier.type is IdentifierType.USERNAME:
            return await self._global_account(identifier)
        return await self._article_search(identifier)

    async def _global_account(self, identifier: Identifier) -> list[Finding]:
        params = {
            "action": "query",
            "meta": "globaluserinfo",
            "guiuser": identifier.value,
            "guiprop": "editcount|merged",
            "format": "json",
        }
        resp = await self.http.get(self.name, CENTRALAUTH_URL, params=params)
        if resp.error is not None or resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=CENTRALAUTH_URL, title="Wikimedia account lookup failed", category=self.category,
                metadata={"error": resp.error, "http_status": resp.status},
            )]

        info = ((resp.json() or {}).get("query") or {}).get("globaluserinfo") or {}
        if not info or "missing" in info:
            return []

        merged = [w for w in info.get("merged") or [] if isinstance(w, dict)]
        edited = sorted((w for w in merged if (w.get("editcount") or 0) > 0),
                        key=lambda w: w.get("editcount") or 0, reverse=True)
        name = info.get("name") or identifier.value
        # The global account's own "registration" is when it was created,
        # which for accounts older than the 2008-2015 unification is the
        # unification date, not the sign-up. The earliest registration on
        # any attached wiki is the real one (ISO strings sort by time).
        dates = [d for d in [info.get("registration")] + [w.get("registration") for w in merged]
                 if isinstance(d, str) and d]
        created = min(dates) if dates else None
        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=ACCOUNT_PAGE.format(quote(name.replace(" ", "_"), safe="")),
            title=f"Wikimedia account: {name} (home wiki {info.get('home') or 'unknown'})",
            category=self.category,
            metadata={
                "home_wiki": info.get("home"),
                "created_at": created,
                "global_account_created_at": info.get("registration"),
                "edit_count": info.get("editcount"),
                "wikis_attached": len(merged),
                "wikis_edited": len(edited),
                "top_wikis": [{"wiki": w.get("wiki"), "url": w.get("url"), "edits": w.get("editcount")}
                              for w in edited[:TOP_WIKIS]],
                # Globally locked: banned network-wide, or locked at the owner's request.
                "locked": "locked" in info,
            },
            evidence_path=resp.evidence_path,
        )]

    async def _article_search(self, identifier: Identifier) -> list[Finding]:
        search_depth = self.config.get("search_depth", "normal")
        max_results = MAX_RESULTS_BY_DEPTH.get(search_depth, MAX_RESULTS)

        titles, descriptions, urls = [], [], []

        for candidate_name in name_variants.variants(identifier.value, deep=(search_depth == "deep")):
            params = {
                "action": "opensearch",
                "search": candidate_name,
                "limit": max_results,
                "namespace": 0,
                "format": "json",
            }
            resp = await self.http.get(self.name, API_URL, params=params)

            if resp.error is not None:
                return [Finding(
                    source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                    source_url=API_URL, title="Wikipedia API request failed", category=self.category,
                    metadata={"error": resp.error},
                )]
            if resp.status != 200:
                return [Finding(
                    source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                    source_url=API_URL, title=f"Wikipedia API returned {resp.status}", category=self.category,
                    metadata={"http_status": resp.status},
                )]

            data = resp.json()
            # OpenSearch's response is a positional array, not an object:
            # [query, [titles], [descriptions], [urls]].
            if not isinstance(data, list) or len(data) < 4:
                return []
            titles, descriptions, urls = data[1] or [], data[2] or [], data[3] or []
            if titles:
                break  # exact form matched -- no need to try the ASCII-folded variant

        return [
            Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.UNCERTAIN,
                source_url=url,
                title=f"Wikipedia article: {title}",
                category=self.category,
                metadata={"description": descriptions[i] if i < len(descriptions) else None},
                evidence_path=resp.evidence_path,
            )
            for i, (title, url) in enumerate(zip(titles, urls))
        ]
