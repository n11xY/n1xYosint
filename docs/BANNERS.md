# Cover images (banners)

A found account's cover image -- banner, header, channel art, profile
background -- is chosen by `osintrecon/core/banner_discovery.py` from what
the username check already downloaded. It adds no requests of its own; a
site that keeps the cover somewhere else says so in `config/sites.json`
(`profile_api`, `banner_pattern`).

## How a cover is chosen

Every place that could hold one hands in candidates:

| Layer | Where | Base score |
|---|---|---|
| `site_pattern` | the site entry's `banner_pattern` | 92 |
| `api` | a cover field at the top of an account API answer (`header`, `cover_photo`, `backgroundPicture`, ...) and the dedicated plugins' API fields | 90 |
| `youtube` | `imageBannerViewModel` in YouTube's page data | 90 |
| `json` (strong key) | a key that only ever means an account's cover (`profile_banner_url`, `bannerUrl`, `coverPhoto`, `headerImage`, ...), anywhere in the page's embedded state | 80 |
| `dom` | `<img>`, `<video poster>`, `<source srcset>` on or just inside an element whose id/class names a profile cover (`profile-header`, `channel-banner`, `profile_animated_background`, ...) | 70 (62 a few levels deeper) |
| `dom` (CSS) | `background-image: url(...)` / `data-background` on such an element | 68 |
| `json` (vague key) | `cover`, `background`, `backgroundImage`, `header`, ... | 40, **+35** when the object around it names the username (or is the API answer's account object; the context reaches its sub-objects, never into lists -- those are the account's posts and projects) |
| `json` (weak key) | `heroImage`, `artwork`, `splash` | 25, +35 the same way |
| `meta` | `og:image`, `twitter:image`, `image_src` | 0 -- recorded, never chosen |

Embedded state means `<script type="application/json">` (Next's
`__NEXT_DATA__`, JSON-LD, ...) and `window.__X__ = {...}` assignments
(`__INITIAL_STATE__`, `__PRELOADED_STATE__`, `__APOLLO_STATE__`, Redux, Pinia),
walked recursively (at most 60,000 nodes). State that is JavaScript rather
than JSON is still scanned as text for the strong keys, escaped slashes and
all.

Then each candidate is judged:

| Adjustment | When |
|---|---|
| -100 `placeholder` | `missing`, `default`, `dummy`, `blank`, `noentry`, `no-cover`, ... in host or path |
| -100 `default_flag` | the value says so: `{"isDefault": true, "url": ...}` |
| -100 `avatar_duplicate` | the avatar again, at any size (`_normal` / `_400x400` / `=s240` are one picture) |
| -100 `not_a_file` | a bare host, a folder (`/files/cover`), or a page (`/help`: one plain word, no digit, no query) |
| -100 `unsupported_format` | `.svg` / `.ico`: the web app's image proxy won't serve them |
| -60 `tiny_hint` | a size under 200px named in the URL |
| -50 `avatar_like` | `avatar`, `profile_image`, `portrait`, flags, icons, badges in the URL |
| -45 `site_asset` | logos, sprites, favicons, `/static/`, `/assets/`, the site's own UI images |
| -35 `square_hint` | a square size named in the URL (`400x400`) |
| -30 `outside_account` | a JSON key outside the account's own object, on a page that has one |
| +8 `wide_hint` | a wide size named in the URL (`1500x500`, `w1420_h280`, `=w1060`) |

The best candidate at or above **60** wins. All the numbers live in
`SCORES`, `BONUSES`, `PENALTIES` and `MIN_SCORE` at the top of the module.

URLs are normalized (HTML entities, `\/` and doubly escaped `\\/`,
`&`, protocol-relative and relative paths) but never rewritten: query
strings stay, because image CDNs sign them. Whether the URL really answers
with an image is checked when it is fetched -- by the web app's image proxy,
which validates the bytes -- not during the scan.

### Why og:image is never the cover

In the audit below, og:image / twitter:image was the cover on none of 170
found profiles: it was the profile photo (SoundCloud, Steam, Vimeo,
Tellonym) or the site's share card (Hugging Face, Gettr, Buy Me a Coffee,
Habbo). Taking it as a low-confidence fallback would put the avatar or a
logo behind every card that has no cover.

### Why there is no headless browser

Pages that are only a JavaScript shell (Audius, GameJolt, BandLab, Picarto,
Farcaster) are already checked through the public APIs behind them, and
those APIs carry the cover where the platform has one. A browser would cost
a ~300 MB dependency and seconds per profile on the server, and most of the
remaining shells sit behind bot protection that isn't to be worked around.

## Diagnostics

Every profile carries `banner_diagnostics`, one entry per stage that looked
(`page`, `profile_api`, `site_pattern`, or `api` for a dedicated plugin):

```json
[{"stage": "page", "layers": ["embedded_json", "dom", "meta"], "candidates": 4, "rejected": 3,
  "ms": 2.1, "reasons": {"avatar_duplicate": 1, "site_asset": 1, "never_chosen": 1},
  "selected": {"source": "json:backgroundImage", "score": 75}}]
```

or, with nothing chosen, `"why_none": "no_candidates"` / `"all_rejected"`.
The chosen cover also gets `banner_source` (`layer:key-or-class`) and
`banner_confidence` (0-100). The same summary is logged at DEBUG level
(`-v`). The web app shows these only to admins.

## Coverage

Measured 2026-11-24 on three public accounts (mkbhd, ninja, pewdiepie):
about 400 accounts found; **10 had a cover before this engine, 19 after**,
with no wrong picture among them (each checked by hand). "No cover set"
means the platform supports one and these accounts left it empty -- the
right answer is no image.

| Platform | Profile | Cover on the platform | Where it's read | Status |
|---|---|---|---|---|
| YouTube | `youtube.com/@{}` | yes | Data API `brandingSettings` (key) / Piped, Invidious (keyless); page `imageBannerViewModel` | found; the page itself shows a consent wall from the EU |
| Bluesky | `bsky.app/profile/{}` | yes | public AppView API `banner` | read; no cover set on the audited accounts |
| Mastodon, mstdn.jp | `/@{}` | yes | account lookup API `header` (stock `missing.png` rejected) | found (pewdiepie) |
| Pixelfed | `pixelfed.social/{}` | yes | account lookup API `header` (stock `missing.png` rejected) | no cover set |
| Misskey.io | `misskey.io/@{}` | yes | **new:** the profile page, asked as `profile_api` (the check is webfinger, which has no cover); the user object's `bannerUrl` | read; `null` on the audited accounts (the page's other `bannerUrl` is the server's, rejected) |
| Lemmy | `/u/{}` | yes | API `banner` of the person (the instance's own banner in the same answer is rejected) | found (pewdiepie) |
| Kitsu | `kitsu.app/users/{}` | yes | API `coverImage` | found (ninja, pewdiepie) |
| Hackster | `hackster.io/{}` | yes | page state `cover_image_url` | found (ninja) |
| Mixcloud | `mixcloud.com/{}` | yes | **new:** DOM `profile-hero` (`profile_cover`, 1600x350) | found (mkbhd) |
| Linktree | `linktr.ee/{}` | page background | **new:** `background` in the account's own object in `__NEXT_DATA__` | found (ninja) |
| Snapchat | `snapchat.com/add/{}` | Bitmoji scene | **new:** `backgroundImage` in the account's own object | found (ninja, pewdiepie) |
| Blogger | `{}.blogspot.com` | blog header | **new:** DOM `Header1_headerimg` | found (pewdiepie) |
| AniList | `anilist.co/user/{}` | yes | GraphQL `bannerImage` | read; no cover set |
| Kick | `kick.com/{}` | yes | channel API `banner_image` | found (ninja) |
| Twitch | `twitch.tv/{}` | yes | Helix has no profile banner; the offline screen image stands in (needs API keys) | keys not configured |
| Steam | `steamcommunity.com/id/{}` | profile background | **new:** DOM -- the animated background's `<video poster>`, or the static background on the profile container | found (mkbhd, ninja) |
| LivePix | `livepix.gg/{}` | yes | **new:** `backgroundImage` in the account's own object in `__NEXT_DATA__` | found (ninja) |
| Tistory | `{}.tistory.com` | blog header | **new:** DOM `category-banner` | found (ninja) |
| Pinterest | `pinterest.com/{}` | yes | `banner_pattern` (`profile_cover`, 736px) | no cover set |
| SoundCloud | `soundcloud.com/{}` | yes | `banner_pattern` (`visual_url`) | `visuals: null` on the audited accounts |
| Patreon | `patreon.com/{}` | campaign cover | `banner_pattern` (`cover_photo_url`) | the 2026-11-24 page no longer embeds it for the audited creator |
| Dailymotion | `dailymotion.com/{}` | yes | API `cover_url` | `null` on the audited accounts |
| Wattpad | `wattpad.com/user/{}` | yes | API `backgroundUrl` | -- |
| Audius | `audius.co/{}` | yes | API `cover_photo` (size map, largest taken) | `null` on the audited accounts |
| GameJolt | `gamejolt.com/@{}` | yes | site API `header_media_item` | `null` on the audited accounts |
| BandLab | `bandlab.com/{}` | yes | API `backgroundPicture` (`isDefault: true` rejected) | default on the audited accounts |
| Substack | `{}.substack.com` | yes | page `cover_photo_url` | `null` on the audited accounts |
| Open Collective | `opencollective.com/{}` | yes | Apollo state `backgroundImage` | `null` on the audited account |
| Poshmark | `poshmark.com/closet/{}` | closet header | DOM header background | the audited closet uses the avatar there (rejected as a duplicate) |
| Myspace | `myspace.com/{}` | profile background | DOM `background` | the audited accounts use the photo there (rejected as a duplicate) |
| lit.link | `lit.link/{}` | page background | only a utility-class `bg-cover` box, no profile naming | not detected |
| Farcaster | `farcaster.xyz/{}` | no field in the public API | -- | unavailable |
| Picarto | `picarto.tv/{}` | no field in the public API | -- | unavailable |
| itch.io, GitHub, GitLab | -- | no profile cover | -- | unavailable |
| Instagram, TikTok, X | -- | behind a login or app | -- | unavailable |

Pages that look like they have a cover but don't (kept as tests in
`tests/test_banner_discovery.py`): hearthis.at's "dummy" banner, Geocaching's
stock profile background, Goodreads' own site header, Flipboard's editorial
`headerImageURL`, Kwork's `coverUrl` folder, Steemit's ad slots, CodeChef's
header logo, a forum's `banner_url` pointing at its help page, the round
avatar inside Linktree's and Hatena's profile header, and Steam's menu icon
inside a page-wide `has_profile_background` wrapper, Misskey's server banner
next to the user's `"bannerUrl": null`, Lemmy's instance banner, the covers
of an Instructables member's projects, and Last.fm's album art.

Last, across one run: a cover two different accounts on the same site both
got is dropped as the site's own (`drop_shared_banners`, noted as a `run`
stage in the diagnostics).

## Adding a platform

- The cover is in the page or API answer the check already reads: nothing to
  do if it's under a key or container the module knows; otherwise add the
  key to `STRONG_KEYS` / `VAGUE_KEYS` or the class word to `_CONTAINER_TOKEN`.
- The check reads something without the cover (an API, webfinger): set
  `profile_api` to the public, keyless URL that has it (it is asked only once
  the account is found, and may be the profile page itself).
- The cover needs rebuilding or sits where no general rule should look: a
  `banner_pattern` (with `banner_format`), the site's most specific source.
- A dedicated plugin passes its API field through
  `banner_discovery.api_banner(...)` so placeholders and avatar copies are
  rejected the same way.
