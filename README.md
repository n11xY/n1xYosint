# n1xYosint

A modular, CLI-first OSINT reconnaissance framework for **username and email
intelligence gathering**, built for Kali Linux. Python 3 + asyncio/aiohttp.

> **Scope & ethics.** This framework only collects information that is
> publicly accessible, or accessible through a legitimate, licensed
> third-party API (e.g. HaveIBeenPwned, GitHub/GitLab public APIs). It does
> not bypass authentication, defeat access controls, exploit vulnerabilities,
> or access private/non-public accounts or data. Use it only against targets
> you are authorized to investigate (your own identities, engagements you
> have written authorization for, CTFs, etc.), and always comply with each
> queried service's terms of service and applicable law.

## Architecture

```
CLI (cli.py)
  -> input normalization/validation (core/normalize.py)
  -> Engine (core/engine.py)
       -> PluginRegistry (plugins/registry.py)     discovers + instantiates source modules
       -> AsyncHttpClient (core/http_client.py)    async requests, retries, rate limits, proxy, cache
       -> ResponseCache (core/cache.py)            sqlite response cache
       -> N source plugins (plugins/sources/*.py)  one module per OSINT source, common interface
       -> dedup + confidence scoring (core/scoring.py)
       -> entity correlation (core/correlation.py)
  -> terminal renderer (output/renderer.py)
  -> exporters: JSON / CSV / TXT (output/exporters.py)
```

Every finding keeps its `source_url` (the exact endpoint/page it came from),
a `MatchStatus` (`confirmed` / `probable` / `uncertain` / `not_found` /
`error`), and a computed `confidence` score, so results stay auditable.

## Install (Kali Linux)

```bash
git clone <this-repo> n1xYosint && cd n1xYosint
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Usage

```bash
# Ad-hoc targets
n1xyosint -u johndoe -u j.doe -e john@example.com

# From a file (one identifier per line, '#' comments allowed)
n1xyosint -f targets.txt --export json:report.json --export csv:report.csv

# Interactive mode
n1xyosint --interactive

# Custom config, evidence capture, verbose logging, Tor proxy
n1xyosint -u johndoe -c config/config.yaml --save-evidence -vv \
  --proxy socks5h://127.0.0.1:9050
```

Copy [`config/config.example.yaml`](config/config.example.yaml) to
`config/config.yaml` and fill in API keys for the sources that need one
(HaveIBeenPwned is required for the `hibp` module; a Bing Web Search key is
required for the optional `search_api` module). Keys can also be supplied
via environment variables: `OSINTRECON_<SOURCE>_API_KEY` (e.g.
`OSINTRECON_HIBP_API_KEY`).

## Built-in source modules

| Module             | Category          | Identifier | Auth required |
|---------------------|-------------------|------------|----------------|
| `username_sites`    | social/various (41-site database) | username   | no (`config/sites.json`) |
| `github`            | code-hosting       | username   | no (optional PAT raises rate limit) |
| `gitlab`            | code-hosting       | username   | no |
| `roblox`            | social             | username   | no |
| `minecraft`         | social             | username   | no |
| `gravatar`          | profile-directory  | email      | no |
| `pastebin_search`   | paste              | both       | no |
| `xposedornot`       | breach             | email      | no -- genuinely free HIBP alternative (HIBP's API is paid-only) |
| `emailrep`          | breach             | email      | no (optional key raises rate limit) |
| `hibp`              | breach + paste     | email      | **yes** (paid, ~$4.39/mo) -- CONFIRMED, most complete breach database |
| `twitch_api`        | social             | username   | **yes** (free Twitch client id/secret) -- CONFIRMED, upgrades the heuristic `username_sites:Twitch` check |
| `steam_api`         | social             | username   | **yes** (free Steam Web API key) -- CONFIRMED, upgrades the heuristic `username_sites:Steam` check |
| `hunter_io`         | profile-directory  | email      | **yes** (free-tier Hunter.io key) -- verifies deliverability |
| `search_api`        | search-engine      | both       | **yes** (licensed web search API key; disabled by default) |
| `twitter_api`       | social             | username   | **yes** (X API v2 developer token; disabled by default -- paid tier) |

Credential-gated modules are skipped silently when unconfigured -- no need
to disable them by hand. Adding a key upgrades matching from a best-effort
HTML heuristic (`probable`, ~0.70 confidence) to an official-API result
(`confirmed`, ~0.95 confidence).

## Writing a new plugin

Drop a module into `osintrecon/plugins/sources/` (or point `plugins_dir` in
config at an external directory) that subclasses `SourcePlugin`:

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

No changes to the core engine are needed -- the registry auto-discovers it.

## Tests

```bash
pip install -e ".[dev]"
pytest
```
