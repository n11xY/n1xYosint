# Contributing

## Dev setup

```bash
git clone https://github.com/n11xY/n1xYosint.git
cd n1xYosint
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

No enforced formatter or linter yet — match the style already in the file
you're touching.

## Writing a plugin

```python
from osintrecon.plugins.base import SourcePlugin
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus

class MyPlugin(SourcePlugin):
    name = "my_source"
    category = "social"
    accepts = {IdentifierType.USERNAME}

    async def run(self, identifier: Identifier) -> list[Finding]:
        resp = await self.http.get(self.name, f"https://example.com/{identifier.value}")
        if resp.status != 200:
            return []
        return [Finding(
            source=self.name, identifier=identifier, status=MatchStatus.CONFIRMED,
            source_url=resp.url, title="Found", category=self.category,
        )]
```

Drop the file in `osintrecon/plugins/sources/` (or point `plugins_dir` in
config at an external directory) — the registry picks it up automatically.
`MatchStatus.CONFIRMED` is for results an official API vouches for;
`MatchStatus.PROBABLE` is for anything derived from a page-content
heuristic. Never blur the two — that distinction is the whole reason a
report is trustworthy.

## Adding a site to `username_sites`

Most new platforms don't need a plugin at all — add an entry to
[`config/sites.json`](config/sites.json):

```json
{"name": "SiteName", "category": "social", "url": "https://example.com/{}", "check": "status", "found_status": 200}
```

For sites where a taken/free username both return HTTP 200 (no reliable
status split), use a content check instead:

```json
{"name": "SiteName", "category": "social", "url": "https://example.com/{}", "check": "content", "not_found_text": "This page doesn't exist"}
```

**Before opening a PR, verify the entry against both a real, known-existing
account and a username you're confident doesn't exist** (e.g. a long
random string) — curl both URLs and confirm the check actually
distinguishes them. This project has been burned before by sites that
looked reliable but silently changed behavior (rate limiting, bot
challenges, or a redirect instead of a 404), producing false positives
against real people. A `status` check that isn't independently verified
against a real account is worse than not having the site at all — say so
explicitly in the PR description, or don't submit it. `content`-check
entries are inherently probabilistic (markup changes break them silently)
and are reported as `PROBABLE`, never `CONFIRMED`, for exactly this
reason. `username_sites` also cross-checks every apparent match against
a random decoy username on the same site before reporting it (see
`osintrecon/plugins/sources/username_sites.py`) — if your new entry gets
auto-downgraded to `UNCERTAIN` in your own testing, that's the mechanism
working, not a bug to route around.

**Some platforms genuinely have no reliable unauthenticated signal at
all.** Diff the *entire* response (not just the one marker you expect)
for a real account against a decoy, with the target string normalized
out — some sites now render everything client-side and serve
byte-identical HTML regardless of whether the account exists (Telegram's
`t.me/{username}` and Threads' `threads.net/@{username}` both do this;
Twitch looked the same at first glance but turned out to have a real,
different `og:description` for real vs. nonexistent channels once
checked properly). If you can't find any distinguishing signal:
1. Check whether the platform has an official API instead (see
   `telegram_api.py`/`youtube_api.py` for the pattern: key-gated, clearly
   marked `MatchStatus.CONFIRMED`, and honestly labeled **not
   live-verified** in the docstring if you don't have a test key).
2. If no API exists either, don't remove the `username_sites` entry
   outright if the project wants to keep a placeholder for it — instead
   set `not_found_text` to something that's *always* present in the
   response (e.g. Threads' login-wall boilerplate), so the check safely
   resolves to "not found" every time instead of lying. Say clearly in
   the PR why the entry can never produce a real match.

## Reporting issues

Use the bug report / feature request templates. For anything that looks
like a security vulnerability, see [SECURITY.md](SECURITY.md) instead of
opening a public issue.
