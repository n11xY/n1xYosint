# Instagram

What n1xYosint can and can't learn about an Instagram username, why, and how
to configure it. Investigated 2026-11-06.

## Short version

- Anonymous requests to instagram.com no longer carry profile data, from a
  home connection or from a datacenter server. No request pattern on our side
  causes this, and none fixes it without circumventing Instagram's access
  controls, which this project doesn't do.
- The `username_sites` check therefore reports Instagram's answer for what it
  is: rate limited, sign-in required, challenge, or Instagram's error page.
  It never reports "not found" for any of them. It pauses Instagram after
  such an answer, and the pause holds across searches and processes.
- What can still be answered comes from:
  - **Meta's official Graph API** (`instagram_graph`): professional accounts
    only, your own token.
  - **Profiles seen before** (`username_sites` result cache): shown as stale
    and uncertain.
  - **Internet Archive captures** (`wayback`): always uncertain.

## Root cause (evidence)

These requests were made by hand with curl, using a browser User-Agent and
three seconds between requests. There was one request per case, against the
real account `instagram` and a random name.

| From | Request | Real account | Random name |
|---|---|---|---|
| Home connection (Poland) | `instagram.com/<name>/` | 200, 509 KB generic page (`httpErrorPage`), title "Instagram", no profile data | identical page |
| Home connection | `api/v1/users/web_profile_info/?username=` with the web app id | **429**, empty body, on the first request | 429 |
| Production server (Hostinger, Frankfurt) | `instagram.com/<name>/` | redirected to `/accounts/login/?next=…&is_from_rle` | same |
| Production server | `web_profile_info` | **404** "Page Not Found" page | 404, the same page |

What this rules out and rules in:

- **Not caused by request volume or concurrency on our side.** Instagram
  already runs at one request at a time, five seconds apart, with no retries,
  and duplicate lookups are shared. The very first request in each
  environment was already refused.
- **The profile page is no longer a usable signal.** It is the same page for
  an existing and a non-existing account, so the old found-text check could
  only ever say "couldn't tell". It said so correctly, but without naming the
  reason.
- **`web_profile_info` is worse than useless from the server.** It answers 404
  for an account that exists. Treating that 404 as "not found", as some tools
  do, would report real accounts as nonexistent.
- **This is an Instagram-side restriction on anonymous automated clients.**
  The ways around it are logging in with an account, rotating proxies,
  emulating the mobile app, or imitating a browser more closely to get past
  the error page. The last of these was declined by this project's own
  safeguards when tried. All of them are circumvention, and out of scope.

## How other tools handle it (researched 2026-11-06)

| Project | Approach | Usable here? |
|---|---|---|
| [instaloader](https://github.com/instaloader/instaloader) | Anonymous: loads the profile page and parses the JSON the page embeds (`__bbox` data). Its own code comment says `web_profile_info` "is refused with HTTP 400 for some professional profiles", and with 401/429 for some sessions. Client-side rate controller: 75–200 requests per 11 minutes by query type, plus jitter. Login redirects raise `LoginRequiredException`; `checkpoint/challenge/feedback_required` abort. ([context code](https://github.com/instaloader/instaloader/blob/master/instaloader/instaloadercontext.py), [structures](https://github.com/instaloader/instaloader/blob/master/instaloader/structures.py)) | The embedded data isn't in the page Instagram served us (see above). The rest is login-based. Its error classification informed ours. |
| [instagrapi](https://github.com/subzeroid/instagrapi) | Private mobile and web APIs. Its README recommends authenticated sessions for production, calls anonymous paths "opportunistic", and points to official APIs or a paid hosted provider. | No: needs an Instagram account. Account, proxy and challenge handling would be circumvention. |
| [Osintgram](https://github.com/Datalux/Osintgram) | A HikerAPI (paid, third-party) key, or an instagrapi login with your own account. | No: a third-party paid scraper or a logged-in account. |
| [maigret](https://github.com/soxoj/maigret) | Probes `web_profile_info` with `x-ig-app-id` and a referer, and looks for `"biography"`. | No: 429 from home, and a misleading 404 from the server (see the table above). |
| [sherlock](https://github.com/sherlock-project/sherlock) | Status code check, probing `imginn.com/<name>` (a third-party Instagram viewer) instead of instagram.com. | Not adopted: a third-party mirror that itself scrapes Instagram, whose answer is neither Instagram's nor verifiable. |
| [Meta Graph API: Business Discovery](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/business-discovery) | Official. Looks up professional accounts (business/creator) by username with your own token. | **Yes**: implemented as `instagram_graph`. |

## What n1xYosint does now

**`username_sites` → Instagram** (`config/sites.json`, `osintrecon/plugins/sources/username_sites.py`):

- **Strict classification.** Only the profile markers (`Instagram photos and
  videos` and `@<name>`) count as found, and nothing counts as not found.
- **Restrictions** recognised from the response:
  - `"Please wait a few minutes"` and `feedback_required` → **RATE_LIMITED**.
  - `"require_login":true` and the login redirect → **AUTH_REQUIRED**.
  - `challenge_required` and `checkpoint_required` → **CHALLENGE_REQUIRED**.
  - Instagram's `httpErrorPage` shell → **TEMPORARILY_UNAVAILABLE** (error page).
  - An HTTP 429 is always **RATE_LIMITED**.

  Each of these pauses Instagram.
- **Pause (cooldown).** Retry-After is honoured. Without it the pause is
  15 min, doubling per consecutive restriction up to 6 h, with jitter. It is
  stored in the cache database, so it holds across searches, processes and
  web users. While paused, nothing is sent and the finding says
  "not checked -- paused until …".
- **One request at a time**, at least 5 s apart across runs, with no retries
  within a lookup. Identical lookups in flight share one request.
- **Result cache.** "Found" is reused for 24 h and "not found" for 1 h.
  Restrictions are never cached as results. A found profile past its 24 h may
  be shown for up to 7 more days as **PARTIAL** (stale, uncertain) while
  Instagram can't be asked.
- **Uniform status** on every finding:
  - `source_status`: SUCCESS, PARTIAL, RATE_LIMITED, AUTH_REQUIRED,
    CHALLENGE_REQUIRED, TEMPORARILY_UNAVAILABLE or ERROR.
  - `data_origin`: direct, cache or stale_cache.
  - `verification`: verified or unverified.
  - `retry_eligible`, plus `cooldown_until` when a pause is known.

  "Not found" produces no finding, so a restriction can never appear as one.
  Every other username site gets the same fields.

**`instagram_graph`** (`osintrecon/plugins/sources/instagram_graph.py`), Meta's Business Discovery:

- **What a match gives you.** A CONFIRMED finding with username, name,
  biography, website and counts, with `data_origin: official_api`.
- **"Cannot find user"** (110/2207013) means a personal account, an age-gated
  one or no account at all. The API can't tell these apart, so the module
  reports nothing.
- **Error mapping:**
  - Codes 4, 17, 32, 613, 80002 and HTTP 429 → RATE_LIMITED.
  - Code 190 → SESSION_EXPIRED (subcode 463/467) or AUTH_REQUIRED.
  - Codes 10, 200, 3 → AUTH_REQUIRED (missing permission).
- **Token handling.** The token goes in the `Authorization` header only, so
  it never appears in URLs, logs, cache keys or evidence. Responses aren't
  cached.

**`wayback`** (unchanged): Internet Archive captures of `instagram.com/<name>/`.
These are third-party historical observations, always reported as uncertain.

## Configuring

The `username_sites` Instagram defaults are built in (`osintrecon/core/config.py`).
To override any of them, set the key under
`sources.username_sites.site_settings.Instagram` in `config/config.yaml`.
The keys are listed in `config/config.example.yaml`. To turn Instagram off:

```yaml
sources:
  username_sites:
    site_settings:
      Instagram:
        enabled: false
```

To clear a pause by hand (for example after moving to another network):

```python
from osintrecon.core.cache import ResponseCache
cache = ResponseCache("path/to/cache.sqlite3")
cache.reset_source_state("username_sites:Instagram")
cache.close()
```

To set up the official API (`instagram_graph`):

1. Create a Meta developer app and add "Instagram API with Facebook Login".
2. Use an Instagram professional (business or creator) account of your own,
   linked to a Facebook Page.
3. Generate a user access token with `instagram_basic`,
   `instagram_manage_insights` and `pages_read_engagement`. Exchange it for a
   long-lived token, which lasts about 60 days.
4. Find your account's Instagram user id: `GET /me/accounts`, then
   `GET /<page-id>?fields=instagram_business_account`.
5. Configure it. Keep the token out of version control: `config/config.yaml`
   is git-ignored, or use the environment variable.

```yaml
sources:
  instagram_graph:
    api_key: null          # or set OSINTRECON_INSTAGRAM_GRAPH_API_KEY
    ig_user_id: "17841400000000000"
```

`n1xyosint --doctor` checks that `graph.facebook.com` resolves.

## Remaining limitations

- Anonymous Instagram checks are expected to stay restricted from both
  networks tested. Most Instagram lookups will read as AUTH_REQUIRED,
  TEMPORARILY_UNAVAILABLE or RATE_LIMITED, which is the truth.
- `instagram_graph` covers professional accounts only, and only with your own
  token. It has not been tested against the live API (no token was
  available); the shapes follow Meta's reference.
- Wayback captures say an account existed when it was archived, not that it
  exists now.
- Tests use a local server and fakes only. No test contacts Instagram or Meta.
