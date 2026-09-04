<h1 align="center">n1xYosint</h1>

<p align="center">
  Async, plugin-based OSINT reconnaissance for usernames, email addresses, and phone numbers.
</p>

<p align="center">
  <a href="https://github.com/n11xY/n1xYosint/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/n11xY/n1xYosint/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="License" src="https://img.shields.io/github/license/n11xY/n1xYosint">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Kali%20Linux-557C94">
  <a href="https://github.com/n11xY/n1xYosint/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/n11xY/n1xYosint?style=social"></a>
</p>

Point it at a username, email, or phone number and it fans out across
dozens of platforms and APIs concurrently, correlates whatever it finds
back into linked identities, scores each result by how confident it
actually is, and hands you a report — terminal, JSON, CSV, or TXT.

Every result is either **confirmed** (an official API said so) or
**probable** (a page-content heuristic said so) — the two are never mixed
together silently, so you always know how much to trust a hit.

**Contents:** [Features](#features) · [Scope](#scope) · [Install](#install)
· [Usage](#usage) · [Source modules](#source-modules) ·
[Contributing](#contributing) · [Security](#security) · [License](#license)

## Features

- 93-site curated username enumeration database (each site individually live-verified against both a real and a nonexistent account at curation time, *and* cross-checked against a decoy at query time -- see below) plus dedicated API modules for GitHub, GitLab, Roblox, Minecraft, Bluesky, AniList, Twitch, and Steam
- Email intelligence: breach exposure (XposedOrNot, free — HaveIBeenPwned, paid), paste exposure, deliverability verification (Hunter.io), reputation signal (EmailRep), Gravatar, and optional registration checks across 120+ sites via holehe
- Phone number intelligence: offline validity/country/carrier/line-type parsing (no API key, no rate limit), plus reverse web search when `search_api` is configured
- Cross-identifier correlation — links usernames, emails, and discovered profile URLs back into one entity
- Optional avatar cross-correlation (`pip install -e ".[imagehash]"`) — flags a near-identical profile photo shared across otherwise-unlinked accounts (perceptual hash, always reported as probable, never merges identities automatically)
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
n1xyosint -u johndoe -e john@example.com -p +15551234567 -n "John Doe"

# from a file, one identifier per line
n1xyosint -f targets.txt --export json:report.json --export csv:report.csv

# interactive prompt
n1xyosint --interactive

# custom config, save raw evidence, verbose, route through Tor
n1xyosint -u johndoe -c config/config.yaml --save-evidence -vv \
  --proxy socks5h://127.0.0.1:9050

# also investigate identifiers discovered along the way -- an email found
# in a bio, or a full name pulled from a Gravatar profile -- two rounds deep
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
| `username_sites` | social/various | username | 93-site database (`config/sites.json`); every apparent match is cross-checked against a random nonexistent decoy username on the same site before being reported, downgrading to `uncertain` if the decoy also "matches" (site's signal isn't reliable right now) |
| `github` / `gitlab` | code-hosting | username | official public APIs |
| `github_commit_email` | code-hosting | username | free, no key — GitHub's commit search API; finds a real commit-author email even when it's hidden from the profile (optional GitHub PAT raises the search API's tighter 10/min rate limit) |
| `wayback` | archive | url | free, no key — Internet Archive's CDX API; archive history for a discovered URL (e.g. a GitHub profile's linked blog) |
| `roblox` / `minecraft` / `bluesky` / `anilist` | social | username | official public APIs |
| `hackernews` | forum | username | free, no key — official Firebase-backed HN API, upgrades the heuristic site check to a confirmed API result with real karma/account-age data |
| `devto` | forum | username | free, no key — official DEV.to API; discovers linked GitHub/Twitter usernames for `--depth` enrichment |
| `chess_com` | social | username | free, no key — official Chess.com Published-Data API; discovers a linked Twitch channel |
| `lichess` | social | username | free, no key — official Lichess API; discovers a linked Twitch channel |
| `codewars` | code-hosting | username | free, no key — official Codewars API |
| `speedrun_com` | social | username | free, no key — official speedrun.com API; discovers linked Twitch/YouTube usernames |
| `dockerhub` | code-hosting | username | free, no key — official Docker Hub API |
| `keybase` | profile-directory | username | free, no key — official Keybase lookup API |
| `mastodon_social` | social | username | free, no key — official API, scoped to the mastodon.social instance specifically (Mastodon is federated, so this can't generalize to every instance) |
| `scratch` | social | username | free, no key — official Scratch (MIT) API |
| `github_name_search` | code-hosting | name | free, no key — GitHub's user-search API (`in:name`); finds accounts whose display name matches, always `uncertain` (a name match alone is never proof of identity), discovers each matched username for `--depth` enrichment |
| `wikipedia` | profile-directory | name | free, no key — official OpenSearch API; flags a matching Wikipedia article for a notable public figure, always `uncertain` for the same reason |
| `discord` | social | username | username-availability check (Discord has no public profile pages) |
| `twitter_email` | social | email | checks X/Twitter's own signup-flow endpoint for email registration, no key needed |
| `gravatar` | profile-directory | email | |
| `pastebin_search` | paste | both | |
| `xposedornot` | breach | email | free, no key |
| `emailrep` | breach | email | free, optional key for higher rate limit |
| `hibp` | breach + paste | email | key required, paid (~$4.39/mo) |
| `twitch_api` / `steam_api` | social | username | key required (free), upgrades the heuristic site check to a confirmed API result |
| `telegram_api` | social | username | key required (free bot token via @BotFather) — official Bot API `getChat`; the generic `username_sites` entry for Telegram can't distinguish real from nonexistent (live-verified: byte-identical HTML either way, see [CONTRIBUTING.md](CONTRIBUTING.md)), so this is the only reliable Telegram signal — **not live-verified**, see the module's docstring |
| `youtube_api` | social | username | key required (free tier), official Data API v3 `channels.list?forHandle=` — a stronger, CONFIRMED-grade alternative to the heuristic `username_sites` entry — **not live-verified**, see the module's docstring |
| `hunter_io` | profile-directory | email | key required (free tier), deliverability check |
| `phone_lookup` | phone | phone | free, no key, no API call — offline validity/country/carrier/line-type via libphonenumber |
| `abstractapi_phone` | phone | phone | key required (free tier), live carrier/line-type/location lookup — **not live-verified**, see the module's docstring |
| `search_api` | search-engine | username, email, phone, name | key required (free tier), disabled by default — Google Custom Search API, supports dork operators (`site:`, `intitle:`, ...); **not live-verified**, see the module's docstring |
| `twitter_api` | social | username | key required, disabled by default (paid API tier) |
| `holehe` | social | email | optional: `pip install -e ".[holehe]"` — bridges to the [holehe](https://github.com/megadose/holehe) project for registration checks across 120+ sites |

Key-gated modules are skipped silently when unconfigured.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## Contributing

Bug reports, feature requests, and PRs are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, the plugin API, and the
verification bar a new `username_sites` entry needs to clear before it's
merged (every existing one is individually curl-tested against a real
and a nonexistent account — see [Features](#features)).

## Security

Found a vulnerability? Please don't open a public issue — see
[SECURITY.md](SECURITY.md) for how to report it privately.

## Credits

Built by [n1xY](https://github.com/n11xY), with development assistance
from Claude (Anthropic).

## License

GPLv3 — see [LICENSE](LICENSE). Use it, modify it, ship it, just keep it
open under the same terms.
