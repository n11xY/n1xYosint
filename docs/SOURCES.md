# Source inventory

What n1xYosint already checks, what was added in the 2026-11-05 expansion,
and what was tried and turned down. Check this file before adding a site
or a plugin: a platform is identified by the service behind it (its
accounts), not by its name, URL or plugin name, so a second entry for a
covered platform is a duplicate even when the URL differs. A genuinely new
*capability* of a covered platform goes into its existing integration.

Regenerate the two inventory tables by hand when sources change; the
sections after them are written, not generated.

## Summary

| | Before | After |
|---|---:|---:|
| `username_sites` entries (`config/sites.json`) | 100 | 157 (10 adult, opt-in) |
| Dedicated source plugins (besides `username_sites`) | 42 | 43 |

The README said 93 sites before this expansion; the file actually held 100.

## Already implemented: dedicated plugins

"Platform" is the service whose accounts the plugin looks at, which is
what deduplication goes by; "API host" is where requests actually go.

| Source id | Platform | API endpoint host(s) | Inputs | Key | Location |
|---|---|---|---|---|---|
| `abstractapi_phone` | abstractapi.com | phonevalidation.abstractapi.com | phone | yes | `osintrecon/plugins/sources/abstractapi_phone.py` |
| `anilist` | anilist.co | graphql.anilist.co | username | no | `osintrecon/plugins/sources/anilist.py` |
| `bluesky` | bsky.app | public.api.bsky.app | username | no | `osintrecon/plugins/sources/bluesky.py` |
| `chess_com` | chess.com | api.chess.com | username | no | `osintrecon/plugins/sources/chess_com.py` |
| `codeforces` | codeforces.com | codeforces.com | username | no | `osintrecon/plugins/sources/codeforces.py` |
| `codewars` | codewars.com | www.codewars.com | username | no | `osintrecon/plugins/sources/codewars.py` |
| `crossref` | crossref.org | api.crossref.org | name | no | `osintrecon/plugins/sources/crossref.py` |
| `devto` | dev.to | dev.to | username | no | `osintrecon/plugins/sources/devto.py` |
| `discord` | discord.com | discord.com | username | no | `osintrecon/plugins/sources/discord.py` |
| `dockerhub` | docker.com | hub.docker.com | username | no | `osintrecon/plugins/sources/dockerhub.py` |
| `dork_links` | — | no request (builds search links) | name | no | `osintrecon/plugins/sources/dork_links.py` |
| `emailrep` | emailrep.io | emailrep.io | email | no | `osintrecon/plugins/sources/emailrep.py` |
| `github` | github.com | api.github.com | username | no | `osintrecon/plugins/sources/github.py` |
| `github_commit_email` | github.com | api.github.com | username | no | `osintrecon/plugins/sources/github_commit_email.py` |
| `github_name_search` | github.com | api.github.com | name | no | `osintrecon/plugins/sources/github_name_search.py` |
| `gitlab` | gitlab.com | gitlab.com | username | no | `osintrecon/plugins/sources/gitlab.py` |
| `gravatar` | gravatar.com | www.gravatar.com | email | no | `osintrecon/plugins/sources/gravatar.py` |
| `hackernews` | ycombinator.com | hacker-news.firebaseio.com | username | no | `osintrecon/plugins/sources/hackernews.py` |
| `hibp` | haveibeenpwned.com | haveibeenpwned.com | email | yes | `osintrecon/plugins/sources/hibp.py` |
| `holehe` | — | external holehe package | email | yes | `osintrecon/plugins/sources/holehe_bridge.py` |
| `hunter_io` | hunter.io | api.hunter.io | email | yes | `osintrecon/plugins/sources/hunter_io.py` |
| `keybase` | keybase.io | keybase.io | username | no | `osintrecon/plugins/sources/keybase.py` |
| `lichess` | lichess.org | lichess.org | username | no | `osintrecon/plugins/sources/lichess.py` |
| `mastodon_social` | mastodon.social | mastodon.social | username | no | `osintrecon/plugins/sources/mastodon_social.py` |
| `minecraft` | minecraft.net | api.mojang.com | username | no | `osintrecon/plugins/sources/minecraft.py` |
| `openalex` | openalex.org | api.openalex.org | name | no | `osintrecon/plugins/sources/openalex.py` |
| `orcid` | orcid.org | pub.orcid.org | name | no | `osintrecon/plugins/sources/orcid.py` |
| `pastebin_search` | pastebin.com | psbdmp.ws | email, username | no | `osintrecon/plugins/sources/pastebin_search.py` |
| `pgp_keys` | keys.openpgp.org, keyserver.ubuntu.com | keys.openpgp.org, keyserver.ubuntu.com | email | no | `osintrecon/plugins/sources/pgp_keys.py` |
| `phone_lookup` | — | offline (libphonenumber) | phone | no | `osintrecon/plugins/sources/phone_lookup.py` |
| `roblox` | roblox.com | users.roblox.com | username | no | `osintrecon/plugins/sources/roblox.py` |
| `scratch` | scratch.mit.edu | api.scratch.mit.edu | username | no | `osintrecon/plugins/sources/scratch.py` |
| `search_api` | google.com | www.googleapis.com | email, name, phone, username | yes | `osintrecon/plugins/sources/search_api.py` |
| `speedrun_com` | speedrun.com | www.speedrun.com | username | no | `osintrecon/plugins/sources/speedrun_com.py` |
| `steam_api` | steamcommunity.com | api.steampowered.com | username | yes | `osintrecon/plugins/sources/steam_api.py` |
| `telegram_api` | t.me | api.telegram.org | username | yes | `osintrecon/plugins/sources/telegram_api.py` |
| `twitch_api` | twitch.tv | id.twitch.tv, api.twitch.tv | username | yes | `osintrecon/plugins/sources/twitch_api.py` |
| `twitter_api` | x.com | api.twitter.com | username | yes | `osintrecon/plugins/sources/twitter_api.py` |
| `twitter_email` | x.com | api.twitter.com | email | no | `osintrecon/plugins/sources/twitter_email.py` |
| `wayback` | archive.org | web.archive.org | url, username | no | `osintrecon/plugins/sources/wayback.py` |
| `wikipedia` | wikipedia.org, wikimedia.org | en.wikipedia.org, meta.wikimedia.org | name, username | no | `osintrecon/plugins/sources/wikipedia.py` |
| `xposedornot` | xposedornot.com | api.xposedornot.com | email | no | `osintrecon/plugins/sources/xposedornot.py` |
| `youtube_api` | youtube.com | www.googleapis.com | username | yes | `osintrecon/plugins/sources/youtube_api.py` |

`holehe` bridges to the external holehe project, which checks email
registration on 120+ sites itself; those sites aren't enumerated here and
are covered for email input only.

## Already implemented: username site database

All rows are `username` input, implemented in `config/sites.json` and
checked by `osintrecon/plugins/sources/username_sites.py`. Rows marked
*new* were added in this expansion.

| Platform | Root domain | Endpoint checked | Check | |
|---|---|---|---|---|
| GitHub | github.com | `https://github.com/{}` | status |  |
| GitLab | gitlab.com | `https://gitlab.com/{}` | status |  |
| Docker Hub | docker.com | `https://hub.docker.com/u/{}` | status |  |
| npm | npmjs.com | `https://www.npmjs.com/~{}` | status |  |
| Reddit | reddit.com | `https://www.reddit.com/user/{}/about.json` | status |  |
| HackerNews | ycombinator.com | `https://news.ycombinator.com/user?id={}` | message |  |
| DevTo | dev.to | `https://dev.to/{}` | status |  |
| Medium | medium.com | `https://medium.com/@{}` | message |  |
| Instagram | instagram.com | `https://www.instagram.com/{}/` | message |  |
| TikTok | tiktok.com | `https://www.tiktok.com/@{}` | message |  |
| Pinterest | pinterest.com | `https://www.pinterest.com/{}/` | message |  |
| Twitch | twitch.tv | `https://www.twitch.tv/{}` | message |  |
| SoundCloud | soundcloud.com | `https://soundcloud.com/{}` | status |  |
| Vimeo | vimeo.com | `https://vimeo.com/{}` | status |  |
| Steam | steamcommunity.com | `https://steamcommunity.com/id/{}` | message |  |
| Keybase | keybase.io | `https://keybase.io/{}` | status |  |
| About.me | about.me | `https://about.me/{}` | status |  |
| Gravatar | gravatar.com | `https://en.gravatar.com/{}` | message |  |
| YouTube | youtube.com | `https://www.youtube.com/@{}` | status |  |
| Telegram | t.me | `https://t.me/{}` | message |  |
| Threads | threads.net | `https://www.threads.net/@{}` | message |  |
| Codeberg | codeberg.org | `https://codeberg.org/{}` | status |  |
| Kaggle | kaggle.com | `https://www.kaggle.com/{}` | status |  |
| Product Hunt | producthunt.com | `https://www.producthunt.com/@{}` | status |  |
| Letterboxd | letterboxd.com | `https://letterboxd.com/{}/` | status |  |
| Behance | behance.net | `https://www.behance.net/{}` | status |  |
| Dribbble | dribbble.com | `https://dribbble.com/{}` | status |  |
| Last.fm | last.fm | `https://www.last.fm/user/{}` | status |  |
| Discogs | discogs.com | `https://www.discogs.com/user/{}` | status |  |
| CodePen | codepen.io | `https://codepen.io/{}` | status |  |
| itch.io | itch.io | `https://{}.itch.io` | status |  |
| HackerOne | hackerone.com | `https://hackerone.com/{}` | status |  |
| Chess.com | chess.com | `https://www.chess.com/member/{}` | status |  |
| Bitbucket | bitbucket.org | `https://bitbucket.org/{}/` | status |  |
| SourceForge | sourceforge.net | `https://sourceforge.net/u/{}/profile/` | status |  |
| osu! | ppy.sh | `https://osu.ppy.sh/users/{}` | status |  |
| Blogspot | blogspot.com | `https://{}.blogspot.com` | status |  |
| Untappd | untappd.com | `https://untappd.com/user/{}` | status |  |
| DeviantArt | deviantart.com | `https://www.deviantart.com/{}` | status |  |
| Wattpad | wattpad.com | `https://www.wattpad.com/user/{}` | status |  |
| AO3 | archiveofourown.org | `https://archiveofourown.org/users/{}` | status |  |
| Imgur | imgur.com | `https://imgur.com/user/{}` | message |  |
| Snapchat | snapchat.com | `https://www.snapchat.com/add/{}` | status |  |
| Unsplash | unsplash.com | `https://unsplash.com/@{}` | status |  |
| ArtStation | artstation.com | `https://www.artstation.com/{}` | message |  |
| Mastodon | mastodon.social | `https://mastodon.social/@{}` | status |  |
| Newgrounds | newgrounds.com | `https://{}.newgrounds.com` | status |  |
| crates.io | crates.io | `https://crates.io/users/{}` | status |  |
| RubyGems | rubygems.org | `https://rubygems.org/profiles/{}` | status |  |
| Packagist | packagist.org | `https://packagist.org/users/{}/` | status |  |
| TryHackMe | tryhackme.com | `https://tryhackme.com/p/{}` | status |  |
| Tumblr | tumblr.com | `https://{}.tumblr.com` | status |  |
| Flickr | flickr.com | `https://www.flickr.com/people/{}/` | status |  |
| VSCO | vsco.co | `https://vsco.co/{}` | status |  |
| Linktree | linktr.ee | `https://linktr.ee/{}` | status |  |
| Carrd | carrd.co | `https://{}.carrd.co` | status |  |
| Venmo | venmo.com | `https://venmo.com/u/{}` | status |  |
| CashApp | cash.app | `https://cash.app/${}` | status |  |
| Bandcamp | bandcamp.com | `https://{}.bandcamp.com` | message |  |
| Mixcloud | mixcloud.com | `https://www.mixcloud.com/{}/` | message |  |
| Substack | substack.com | `https://{}.substack.com` | status |  |
| MyAnimeList | myanimelist.net | `https://myanimelist.net/profile/{}` | status |  |
| Hashnode | hashnode.com | `https://hashnode.com/@{}` | message |  |
| Buy Me a Coffee | buymeacoffee.com | `https://www.buymeacoffee.com/{}` | status |  |
| Goodreads | goodreads.com | `https://www.goodreads.com/{}` | status |  |
| DevPost | devpost.com | `https://devpost.com/{}` | status |  |
| Lobsters | lobste.rs | `https://lobste.rs/u/{}` | status |  |
| Rumble | rumble.com | `https://rumble.com/user/{}` | status |  |
| Speedrun.com | speedrun.com | `https://www.speedrun.com/user/{}` | status |  |
| Backloggd | backloggd.com | `https://backloggd.com/u/{}` | status |  |
| Giant Bomb | giantbomb.com | `https://www.giantbomb.com/profile/{}/` | status |  |
| Wellfound | wellfound.com | `https://wellfound.com/u/{}` | status |  |
| Peerlist | peerlist.io | `https://peerlist.io/{}` | status |  |
| BeerAdvocate | beeradvocate.com | `https://www.beeradvocate.com/community/members/{}/` | status |  |
| WordPress.org | wordpress.org | `https://profiles.wordpress.org/{}/` | status |  |
| LibraryThing | librarything.com | `https://www.librarything.com/profile/{}` | message |  |
| Kick | kick.com | `https://kick.com/{}` | message |  |
| Codewars | codewars.com | `https://www.codewars.com/users/{}` | status |  |
| Lichess | lichess.org | `https://lichess.org/@/{}` | status |  |
| OpenSea | opensea.io | `https://opensea.io/{}` | status |  |
| NuGet | nuget.org | `https://www.nuget.org/profiles/{}` | status |  |
| Fiverr | fiverr.com | `https://www.fiverr.com/{}` | status |  |
| Freesound | freesound.org | `https://freesound.org/people/{}/` | status |  |
| OpenStreetMap | openstreetmap.org | `https://www.openstreetmap.org/user/{}` | status |  |
| Setlist.fm | setlist.fm | `https://www.setlist.fm/user/{}` | status |  |
| Scratch | scratch.mit.edu | `https://scratch.mit.edu/users/{}/` | status |  |
| Slashdot | slashdot.org | `https://slashdot.org/~{}` | status |  |
| Patreon | patreon.com | `https://www.patreon.com/{}` | status |  |
| AtCoder | atcoder.jp | `https://atcoder.jp/users/{}` | status |  |
| Pastebin | pastebin.com | `https://pastebin.com/u/{}` | status |  |
| Poshmark | poshmark.com | `https://poshmark.com/closet/{}` | status |  |
| Observable | observablehq.com | `https://observablehq.com/@{}` | status |  |
| Gumroad | gumroad.com | `https://{}.gumroad.com` | status |  |
| Hugging Face | huggingface.co | `https://huggingface.co/{}` | status |  |
| Replit | replit.com | `https://replit.com/@{}` | status |  |
| Gitee | gitee.com | `https://gitee.com/{}` | status |  |
| Spotify | spotify.com | `https://open.spotify.com/user/{}` | status |  |
| Liberapay | liberapay.com | `https://liberapay.com/{}/` | status |  |
| Open Collective | opencollective.com | `https://opencollective.com/{}` | status |  |
| Modrinth | modrinth.com | `https://api.modrinth.com/v2/user/{}` | status | new |
| SourceHut | sr.ht | `https://sr.ht/~{}/` | status | new |
| Gitea | gitea.com | `https://gitea.com/api/v1/users/{}` | status | new |
| Hex | hex.pm | `https://hex.pm/api/users/{}` | status | new |
| Hackage | haskell.org | `https://hackage.haskell.org/user/{}` | status | new |
| Clojars | clojars.org | `https://clojars.org/users/{}` | status | new |
| Chocolatey | chocolatey.org | `https://community.chocolatey.org/profiles/{}` | status | new |
| HackerRank | hackerrank.com | `https://www.hackerrank.com/rest/contests/master/hackers/{}/profile` | status | new |
| DMOJ | dmoj.ca | `https://dmoj.ca/api/v2/user/{}` | status | new |
| Qiita | qiita.com | `https://qiita.com/api/v2/users/{}` | status | new |
| Zenn | zenn.dev | `https://zenn.dev/api/users/{}` | status | new |
| Habr | habr.com | `https://habr.com/en/users/{}/` | status | new |
| Hackster | hackster.io | `https://www.hackster.io/{}` | status | new |
| Instructables | instructables.com | `https://www.instructables.com/member/{}/` | status | new |
| Disqus | disqus.com | `https://disqus.com/by/{}/` | status | new |
| Lemmy | lemmy.world | `https://lemmy.world/api/v3/user?username={}` | status | new |
| Pixelfed | pixelfed.social | `https://pixelfed.social/{}` | status | new |
| VK | vk.com | `https://vk.com/{}` | status | new |
| Dailymotion | dailymotion.com | `https://api.dailymotion.com/user/{}` | status | new |
| BandLab | bandlab.com | `https://www.bandlab.com/api/v1.3/users/{}` | status | new |
| MusicBrainz | musicbrainz.org | `https://musicbrainz.org/user/{}` | status | new |
| Flipboard | flipboard.com | `https://flipboard.com/@{}` | status | new |
| Issuu | issuu.com | `https://issuu.com/{}` | status | new |
| Duolingo | duolingo.com | `https://www.duolingo.com/2017-06-30/users?username={}` | message | new |
| Kitsu | kitsu.io | `https://kitsu.io/api/edge/users?filter[slug]={}` | message | new |
| Trello | trello.com | `https://trello.com/1/members/{}` | status | new |
| Figma | figma.com | `https://www.figma.com/@{}` | status | new |
| Hatena | hatena.ne.jp | `https://profile.hatena.ne.jp/{}/` | status | new |

## Platforms with more than one capability

The same service reached by more than one integration. These are separate
capabilities (a profile check, an API with richer data, a search by name or
email), not duplicates.

- **chess.com**: sites.json: Chess.com; plugin: chess_com
- **codewars.com**: sites.json: Codewars; plugin: codewars
- **dev.to**: sites.json: DevTo; plugin: devto
- **docker.com**: sites.json: Docker Hub; plugin: dockerhub
- **github.com**: sites.json: GitHub; plugin: github; plugin: github_commit_email; plugin: github_name_search
- **gitlab.com**: sites.json: GitLab; plugin: gitlab
- **gravatar.com**: sites.json: Gravatar; plugin: gravatar
- **keybase.io**: sites.json: Keybase; plugin: keybase
- **lichess.org**: sites.json: Lichess; plugin: lichess
- **mastodon.social**: sites.json: Mastodon; plugin: mastodon_social
- **pastebin.com**: sites.json: Pastebin; plugin: pastebin_search
- **scratch.mit.edu**: sites.json: Scratch; plugin: scratch
- **speedrun.com**: sites.json: Speedrun.com; plugin: speedrun_com
- **steamcommunity.com**: sites.json: Steam; plugin: steam_api
- **t.me**: sites.json: Telegram; plugin: telegram_api
- **twitch.tv**: sites.json: Twitch; plugin: twitch_api
- **x.com**: plugin: twitter_api; plugin: twitter_email
- **ycombinator.com**: sites.json: HackerNews; plugin: hackernews
- **youtube.com**: sites.json: YouTube; plugin: youtube_api

## Newly added unique platforms

Each was checked against everything above, then live-verified on
2026-11-05 through `username_sites` itself (a known existing account
found and decoy-checked; a random nonexistent name not found). Where a
platform matches usernames case-sensitively but only allows lowercase
names, the entry sets `"lowercase": true`.

| Platform | What it is | Endpoint | Notes |
|---|---|---|---|
| Modrinth | Minecraft mod hosting | `api.modrinth.com/v2/user/{}` | official API, case-insensitive |
| SourceHut | code hosting | `sr.ht/~{}/` | lowercase names |
| Gitea | code hosting (gitea.com) | `gitea.com/api/v1/users/{}` | official API; distinct service from Codeberg (Forgejo) |
| Hex | Elixir/Erlang packages | `hex.pm/api/users/{}` | official API |
| Hackage | Haskell packages | `hackage.haskell.org/user/{}` | case-sensitive, mixed-case names exist |
| Clojars | Clojure packages | `clojars.org/users/{}` | case-sensitive |
| Chocolatey | Windows packages | `community.chocolatey.org/profiles/{}` | |
| HackerRank | competitive programming | `hackerrank.com/rest/.../hackers/{}/profile` | |
| DMOJ | competitive programming | `dmoj.ca/api/v2/user/{}` | official API |
| Qiita | developer blogging (JP) | `qiita.com/api/v2/users/{}` | official API, 60 anonymous requests/hour |
| Zenn | developer blogging (JP) | `zenn.dev/api/users/{}` | lowercase names |
| Habr | tech community (RU) | `habr.com/en/users/{}/` | |
| Hackster | hardware projects | `hackster.io/{}` | |
| Instructables | DIY projects | `instructables.com/member/{}/` | |
| Disqus | comment platform | `disqus.com/by/{}/` | |
| Lemmy | federated forum (lemmy.world) | `lemmy.world/api/v3/user?username={}` | one instance, like `mastodon_social` |
| Pixelfed | federated photos (pixelfed.social) | `pixelfed.social/{}` | one instance |
| VK | social network | `vk.com/{}` | |
| Dailymotion | video | `api.dailymotion.com/user/{}` | official API |
| BandLab | music creation | `bandlab.com/api/v1.3/users/{}` | |
| MusicBrainz | music database | `musicbrainz.org/user/{}` | also the account for ListenBrainz (see rejected) |
| Flipboard | news curation | `flipboard.com/@{}` | |
| Issuu | publishing | `issuu.com/{}` | |
| Duolingo | language learning | `duolingo.com/2017-06-30/users?username={}` | message check: `{"users":[]}` when absent, so `probable` |
| Kitsu | anime tracking | `kitsu.io/api/edge/users?filter[slug]={}` | official JSON:API, message check (`"data":[]`), so `probable`; distinct from MyAnimeList and AniList |
| Trello | boards (Atlassian) | `trello.com/1/members/{}` | public member API |
| Figma | design community | `figma.com/@{}` | |
| Hatena | blogging (JP) | `profile.hatena.ne.jp/{}/` | occasional 503, reported as unavailable, not as absent |

New plugin:

| Source id | Platform | Inputs | Notes |
|---|---|---|---|
| `pgp_keys` | keys.openpgp.org, keyserver.ubuntu.com | email | keys.openpgp.org serves only owner-confirmed addresses, so a hit is `confirmed` and its name and other addresses feed `--depth`; keyserver.ubuntu.com accepts anything (famous addresses have 100 forged keys), so it is one `uncertain` summary that never feeds enrichment. Both keyless and documented for this lookup (VKS API, HKP machine-readable index). |

## New capabilities added to existing platforms

- **Wikimedia** (`wikipedia` plugin) now takes usernames: the CentralAuth
  API (`meta.wikimedia.org`, `meta=globaluserinfo`) finds the single
  account behind every Wikimedia wiki, with home wiki, registration date,
  total edits and the most-edited wikis. It replaces the sites.json entry
  *Wikipedia (user page)*, which only saw English Wikipedia user pages
  (missing every account without one) and now gets HTTP 403 for every
  request (see the broken list).
- **username_sites**: entries can name a `profile_url`, so an entry that
  checks a JSON API still links to the page a person would open.
- **Default User-Agent** now carries the project URL; Wikimedia refuses
  API requests whose User-Agent has no contact information.
- **`"browser_headers": false`** (2026-11-05, later): Codeberg, Liberapay,
  DevPost, osu!, Slashdot and Mixcloud answered every lookup with a bot
  challenge (HTTP 403) when sent the browser imitation, and answer
  correctly to the client's honest User-Agent, checked from both a home
  connection and the production server. Last.fm likewise, from a home
  connection; from the server's datacenter IP it challenges both, so its
  entry also requires a profile marker (the canonical link, which echoes
  the username) and a challenge page reads as "couldn't tell" instead of
  as an account for every username.
- **Still blocked by bot protection** with either User-Agent (reported as
  "couldn't tell", never as absent): npm, Medium, Letterboxd, Product Hunt,
  ArtStation, CodePen, Speedrun.com (the `speedrun_com` API plugin still
  works), Lichess (`lichess` plugin still works), Discogs, VSCO,
  LibraryThing, SourceForge, Unsplash, BeerAdvocate, Rumble, Peerlist,
  Giant Bomb, Backloggd, Reddit, Tumblr, Newgrounds. Not circumvented.

- **Site health check** (2026-11-06): `python -m osintrecon.core.site_health`
  asks every site about one random name. Six answered "found":
  - *YouTube*: from EU addresses every profile URL redirects to
    `consent.youtube.com`. The entry now sends the consent-declined cookie
    (`SOCS=CAI`), and a consent redirect still reads as "requires sign-in"
    rather than as an account.
  - *crates.io*: the profile page is a client-side shell, identical for
    every name. The entry now checks `crates.io/api/v1/users/{}` (404 for
    a free name) and links to the profile page.
  - *Bandcamp*: a free subdomain redirects to `bandcamp.com/signup?new_domain=`,
    which the production server gets as a bot challenge, and the old
    not-found text never appeared there. Now: `not_found_url` for that
    redirect, a profile marker (`"url":"https://{}.bandcamp.com`) for a
    real page, and the honest User-Agent (the browser one is challenged).
  - *Threads* and *Spotify*: the anonymous page is the same client-side
    shell for every name (a few bytes apart: the name itself). Removed.
  - *Backloggd*: a bot challenge for every request, from home and server.
    Removed.

## Third batch (2026-11-06)

113 sites, each checked end to end through the engine's own client and
classifier (one or two clearly public accounts found, two random names not
found) and with curl from the production server (real account 200, random
name 404 or the entry's not-found signal). Test accounts were platform,
company or project accounts, or well-known public figures.

- **Software and developer communities** (14): Launchpad, asciinema, Exercism, WakaTime, Puppet Forge, Open VSX, HackMD, Gradle Plugins, freeCodeCamp, Coderwall, Rust Users Forum, Arduino Forum, Julia Discourse, Elixir Forum.
- **Games, art, music and video** (11): hearthis.at, Tapas, Kongregate, ReverbNation, GOG, Lospec, Shikimori, Fandom, TETR.IO, Pokemon Showdown, Audius.
- **Regional social networks** (12): Plurk, V2EX, Minds, TabNews, Cnblogs, omg.lol, Boosty, Misskey.io, Virgool, Wykop, Postype, Tipeee.
- **Sports, travel and hobbies** (13): Lidraughts, Lishogi, PlayStrategy, PyChess, Hevy, Travellerspoint, D&D Beyond, Mixlr, TwitCasting, Geocaching, Brickset, ListenBrainz, Couchsurfing.
- **Asia, Middle East, Latin America and the fediverse** (15): note, Aparat, Kompasiana, Vocus, lit.link, Cafecito, Blockdit, Catarse, LivePix, CodeChef, Meneame, mstdn.jp, Fosstodon, Lemmy.ml, Framatube.
- **Education, science, finance and publishing** (12): Tatoeba, Zooniverse, TradingView, Farcaster, Treehouse, Zotero, Rosalind, WIP, Buttondown, Steemit, Contra, Vocal.
- **Freelance, marketplaces and portfolios** (13): Codementor, Paragraph, Big Cartel, Carbonmade, Kwork, CGTrader, Speaker Deck, ThemeForest, Tellonym, Habr Career, HowLongToBeat, Ghost, Freelancer.
- **Project forums (Discourse) and wikis (MediaWiki API)** (23): Godot Forum, Blender Developer Forum, Blender Artists, Ubuntu Discourse, NixOS Discourse, Obsidian Forum, Kubernetes Discuss, OpenAI Developer Forum, Discourse Meta, PyTorch Forums, Hugging Face Forums, freeCodeCamp Forum, Unreal Engine Forums, KDE Discuss, Cursor Forum, GNOME Discourse, LLVM Discourse, three.js Forum, Ghost Forum, Gentoo Wiki, NixOS Wiki, Factorio Wiki, Guild Wars 2 Wiki.

Also in this batch:
- **401/403 never reads as "not found".** A status- or text-checked entry
  answered with a bot challenge (Cloudflare serves 403) used to read as
  "no account", silently hiding real accounts on every site behind such a
  wall. It now reads as access restricted, unless the entry itself declares
  that status as its not-found status.
- **Removed: blocked for every request**, from a home connection and from
  the production server (Cloudflare or similar): ArtStation, BeerAdvocate,
  CodePen, Discogs, Giant Bomb, GitLab, Letterboxd, LibraryThing, Lichess,
  Medium, Newgrounds, npm, Product Hunt, Reddit, Rumble, SourceForge,
  Speedrun.com, Tumblr, Unsplash, VSCO. GitLab, Lichess and Speedrun.com
  keep their dedicated API plugins.
- **Turned off: Velog** (`site_settings.Velog.enabled: false`). Since 2026-11-06 every
  request to velog.io gets a response that never finishes, for profile, RSS and
  about pages alike, from home and from the server. Set it back to true once it
  answers again.
- **Removed: TryHackMe**, whose profile URL now serves the same app shell
  for every name. **Observable** moved to `api.observablehq.com/user/@{}`
  for the same reason.

## Second batch (2026-11-05)

Checked like the first batch (end to end locally, real account found and
decoy-checked, random name not found), and also with curl from the
production server.

| Platform | What it is | Endpoint | Notes |
|---|---|---|---|
| Picarto | art streaming | `api.picarto.tv/api/v1/channel/name/{}` | official API |
| GameJolt | indie games | `gamejolt.com/site-api/web/profile/@{}/` | |
| Imgflip | memes | `imgflip.com/user/{}` | |
| Onedio | social media (TR) | `onedio.com/profil/{}` | |
| Bionluk | freelancing (TR) | `bionluk.com/{}` | |
| Pikabu | community (RU) | `pikabu.ru/@{}` | |
| OK.ru | social network (RU) | `ok.ru/{}` | separate accounts from VK |
| Tildes | forum | `tildes.net/user/{}` | |
| Velog | developer blogging (KR) | `velog.io/@{}` | lowercase names |
| Naver Blog | blogging (KR) | `blog.naver.com/{}` | lowercase IDs |
| Tistory | blogging (KR) | `{}.tistory.com` | |
| Ameba | blogging (JP) | `ameblo.jp/{}/` | lowercase IDs |
| Write.as | blogging | `write.as/{}` | |
| Micro.blog | microblogging | `micro.blog/{}` | |
| Bear Blog | blogging | `{}.bearblog.dev` | |
| Mataroa | blogging | `{}.mataroa.blog` | |
| Neocities | web hosting | `{}.neocities.org` | |
| Ollama | model hosting | `ollama.com/{}` | distinct from Hugging Face |
| Replicate | model hosting | `replicate.com/{}` | |
| iNaturalist | nature observations | `api.inaturalist.org/v1/users/{}` | official API |

Turned down in this batch: FurAffinity and solo.to (work from a home
connection, HTTP 403 from the server), Brunch (redirect loop from the
server, which would read as "no account"), Coub, Giphy, YouPorn (real and
nonexistent names look the same), Codecademy, Anaconda, Snapcraft,
Monzo, MyFreeCams, TwitCasting, Inkbunny, WordPress.com, Uludağ Sözlük,
DLive, Revolut, Taplink, Cam4, freeCodeCamp, FanCentro (same status for
both; not-found only visible in the page or redirect), PSNProfiles,
Civitai, 9GAG, Weasyl, 1000Kitap, StoryGraph, Ekşi Sözlük, SpankBang,
Lnk.bio, RetroAchievements (bot protection), GNOME and KDE GitLab, Salsa,
freedesktop GitLab, AUR, Hentai Foundry (API refused, sign-in or bot
challenge), LiveJournal, Dreamwidth (timeouts).

## Adult sites (opt-in)

Marked `"nsfw": true`, category `adult`, and skipped unless the run opts in
(`--include-adult`, or `sources.username_sites.include_adult: true`), so no
search checks them by accident. Verified 2026-11-05 from the production
server (and from a home connection where the site is reachable).

| Platform | Endpoint | Notes |
|---|---|---|
| Pornhub | `pornhub.com/users/{}` | strict: requires the profile's canonical link; blocked (HTTP 410) in several countries, which reads as "couldn't tell" |
| Fansly | `apiv3.fansly.com/api/v1/account?usernames={}` | JSON; `"response":[]` when absent, so `probable` |
| xHamster | `xhamster.com/users/{}` | |
| XVideos | `xvideos.com/profiles/{}` | case-sensitive, lowercase names |
| Eporner | `eporner.com/profile/{}/` | |
| RedTube | `redtube.com/users/{}` | redirects a real name to its numeric profile |
| Fanvue | `fanvue.com/{}` | |
| NiteFlirt | `niteflirt.com/{}` | |
| Literotica | `literotica.com/authors/{}` | |
| e621 | `e621.net/users/{}.json` | JSON API; profile link `e621.net/users/{}` |

Rejected: OnlyFans, ManyVids, LoyalFans (same client-rendered page for real
and nonexistent names), JustFor.Fans (not-found is only a redirect URL
parameter), Chaturbate (same page from the server), Stripchat, CamSoda,
BongaCams, Fetlife (bot protection or sign-in), Motherless (does not
resolve), Erome, RedGifs, Clips4Sale, XNXX (real accounts not
distinguishable from nonexistent ones).

## Rejected duplicates

| Candidate | Why |
|---|---|
| ListenBrainz | Same MetaBrainz account as MusicBrainz (single sign-on); a ListenBrainz 404 only means the account never used it. Covered by the MusicBrainz entry. |
| Libravatar | Falls back to Gravatar for every address it doesn't host (verified: 302 to `/gravatarproxy/`), so most answers are Gravatar's. Covered by `gravatar`. |
| Fosstodon and other Mastodon instances | Same platform as the Mastodon entry and `mastodon_social`; adding instances one by one isn't new capability. |
| GitHub Gist, GitHub Sponsors | Same GitHub account as `github`. |
| Archive.org user pages | Same service as `wayback`; returned the same page for real and nonexistent accounts anyway. |
| Semantic Scholar | Author search duplicates `openalex`, which already aggregates the same scholarly graph. |

## Rejected: broken, blocked or unreliable

| Candidate | Result on 2026-11-05 |
|---|---|
| Wikipedia (user page), existing entry | HTTP 403 for every request with a browser-imitating User-Agent (Wikimedia policy); replaced by the CentralAuth lookup above. |
| DBLP | Author search sits behind an Anubis proof-of-work bot check (HTTP 202 challenge page); not circumvented. |
| Launchpad | Correct answers at first, then every lookup for a nonexistent name timed out after a short burst; would produce constant error rows. |
| Exercism | Profiles are opt-in public; a real account returned 404. |
| Genius | `genius.com/{}` resolves artist names as well as users, so a famous name would read as an account. |
| PyPI, SlideShare, Drupal.org, Audius | Same 3,036-byte client-challenge page for real and nonexistent names. |
| LeetCode, Ko-fi, bio.link, Beacons, BoardGameGeek, SPOJ, CodeSandbox, Depop, Diigo, Printables, OpenHub, Smule | HTTP 403 bot protection for both real and nonexistent names; not circumvented. |
| Kattis, JSFiddle, TopCoder, note.com, Quay | Real accounts returned the same 404/400/402 as nonexistent ones. |
| MetaCPAN API | HTTP 402 for every request. |
| Root-Me, Polarsteps, Pinboard, Bugcrowd, Audiomack, Freelancer, CodeChef | Same page (or a redirect to sign-up/home) for real and nonexistent names. |
| Reverbnation | Real profile redirected to the legacy home page. |
| Trakt | Same client-rendered app page (HTTP 200) for real and nonexistent names; already removed once for this in commit 74bee0e. |
| Fedora Accounts | Requires sign-in; nonexistent names redirect to login. |
| Discourse Meta | Works, but one forum instance of a self-hosted product has little lookup value. |
| Domain sources (crt.sh, RDAP) | Work, but the engine takes no domain input yet (no `--domain` option); worth adding together with that input. |

