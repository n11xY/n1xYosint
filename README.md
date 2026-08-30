<h1 align="center">n1xYosint</h1>

<p align="center">
  Async, plugin-based OSINT reconnaissance for usernames and email addresses.
</p>

<p align="center">
  <img alt="License" src="https://img.shields.io/github/license/n11xY/n1xYosint">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Kali%20Linux-557C94">
</p>

Point it at a username or email and it fans out across dozens of platforms
and APIs concurrently, correlates whatever it finds back into linked
identities, scores each result by how confident it actually is, and hands
you a report — terminal, JSON, CSV, or TXT.

Every result is either **confirmed** (an official API said so) or
**probable** (a page-content heuristic said so) — the two are never mixed
together silently, so you always know how much to trust a hit.

## Features

- 80-site username enumeration database (GitHub, Reddit, Instagram, Steam, YouTube, Twitch, ...) plus dedicated API modules for GitHub, GitLab, Roblox, Minecraft, Bluesky, Twitch, and Steam
- Email intelligence: breach exposure (XposedOrNot, free — HaveIBeenPwned, paid), paste exposure, deliverability verification (Hunter.io), reputation signal (EmailRep), Gravatar, and optional registration checks across 120+ sites via holehe
- Cross-identifier correlation — links usernames, emails, and discovered profile URLs back into one entity
- Multi-hop enrichment (`--depth`) — automatically investigates identifiers discovered along the way (an email pulled from a bio, say), with cycle protection and a configurable cap
- Confidence scoring with deduplication, tuned to not let a pile of unrelated hits fake out corroboration
- Async engine with configurable concurrency, retries, per-source rate limiting, SQLite response caching, and proxy support (HTTP or SOCKS5/Tor)
- `--doctor` diagnostics — checks config, filesystem paths, and DNS reachability for every enabled source before you run a real scan
- Plugin architecture — drop a file in `plugins/sources/`, no core changes needed
- JSON / CSV / TXT export, evidence capture, full source attribution on every finding

## Scope

Everything here reads publicly accessible pages or calls a legitimate,
documented third-party API. No auth bypass, no exploiting access controls,
no touching private data. Use it only against targets you're authorized to
investigate — your own accounts, an engagement you have written
authorization for, a CTF — and respect each service's terms of use.

## Install

```bash
git clone https://github.com/n11xY/n1xYosint.git
cd n1xYosint
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Usage

```bash
n1xyosint -u johndoe -e john@example.com

# from a file, one identifier per line
n1xyosint -f targets.txt --export json:report.json --export csv:report.csv

# interactive prompt
n1xyosint --interactive

# custom config, save raw evidence, verbose, route through Tor
n1xyosint -u johndoe -c config/config.yaml --save-evidence -vv \
  --proxy socks5h://127.0.0.1:9050

# also investigate identifiers discovered along the way (e.g. an email
# found in a GitHub bio gets checked too), two rounds deep
n1xyosint -u johndoe --depth 2

# check setup before running a real scan: config, filesystem paths, and
# DNS reachability for every enabled source's domain(s)
n1xyosint --doctor
```

Copy [`config/config.example.yaml`](config/config.example.yaml) to
`config/config.yaml` to enable the API-key-gated modules below (or set keys
via `OSINTRECON_<SOURCE>_API_KEY` env vars). Everything else works with zero
configuration.

## Source modules

| Module | Category | Identifier | Notes |
|---|---|---|---|
| `username_sites` | social/various | username | 80-site database, `config/sites.json` |
| `github` / `gitlab` | code-hosting | username | official public APIs |
| `roblox` / `minecraft` / `bluesky` / `anilist` | social | username | official public APIs |
| `gravatar` | profile-directory | email | |
| `pastebin_search` | paste | both | |
| `xposedornot` | breach | email | free, no key |
| `emailrep` | breach | email | free, optional key for higher rate limit |
| `hibp` | breach + paste | email | key required, paid (~$4.39/mo) |
| `twitch_api` / `steam_api` | social | username | key required (free), upgrades the heuristic site check to a confirmed API result |
| `hunter_io` | profile-directory | email | key required (free tier), deliverability check |
| `search_api` | search-engine | both | key required, disabled by default |
| `twitter_api` | social | username | key required, disabled by default (paid API tier) |
| `holehe` | social | email | optional: `pip install -e ".[holehe]"` — bridges to the [holehe](https://github.com/megadose/holehe) project for registration checks across 120+ sites |

Key-gated modules are skipped silently when unconfigured.

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

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## License

GPLv3 — see [LICENSE](LICENSE). Use it, modify it, ship it, just keep it
open under the same terms.
