# Account evidence

A username search finds accounts by name, and the same name on two sites is
often two different people. n1xYosint therefore reads every found profile
for what it says about its owner, and ties accounts together only when
there is evidence. Code: `osintrecon/core/profile_extract.py`,
`osintrecon/core/platform_urls.py`, `osintrecon/core/account_evidence.py`,
`osintrecon/core/username_rarity.py`.

## What a profile says

The username check already downloads the profile page (or API answer), so
reading it costs no extra request. From it:

- **JSON API answers**: display name, bio (`bio`, `note`, `description`,
  ...), avatar, location, website and Mastodon-style profile fields.
- **schema.org JSON-LD** (`Person`, `ProfilePage`), only when it describes
  the account, not the site's own `Organization`.
- **`rel="me"` links**: the owner's own claim that a link is theirs.
- **OpenGraph tags**, filtered: `og:description` is dropped when it reads
  like the site's boilerplate ("Check out alice's profile on ..."), and
  `og:image` when it looks like a logo or share card.
- **Link hubs** (Linktree, Carrd, About.me, lit.link, Keybase, Gravatar,
  marked `"link_hub": true` in `config/sites.json`): every outbound button.
- **Handles written in a bio**: "ig: alice", "twitter @alice".

The site's own accounts (its header and footer links) are left out. Links
that point at an account on a known platform become `linked_accounts`;
links to someone's own website become `url` leads (at most three), and
usernames on other platforms become `username` leads (at most three), which
`--depth 2` then investigates.

## Evidence kinds

| Strength | Kind | Meaning |
|---|---|---|
| strong | `keybase_proof` | a signed Keybase proof |
| strong | `profile_link` | one profile links to the other |
| strong | `gravatar_match` | both photos are the same Gravatar or Libravatar, i.e. the same email address (or the photo is the Gravatar of a searched email) |
| strong | `same_website` | both link to the same personal website (shortener and big shared services excluded; shared hosting such as `*.github.io` compared by full host) |
| medium | `avatar_match` | near-identical profile photo on two platforms (perceptual hash) |
| medium | `same_bio` | the same bio, long enough to be written by someone |
| weak | `same_name` | the same display name (not the username again, not a lone first name) |
| weak | `same_location` | the same location |

Two accounts are **tied** when one strong kind, or kinds adding up to the
same weight (strong 1, medium 0.5, weak 0.25), connect them. Tied accounts
form **linked groups**; several groups for one username are likely several
people. Everything weaker is listed as a hint.

**Contradictions**: two accounts giving different real names (both from
the platform's own data, two or more words each) or different places
("Russia" and "Russian Federation" count as the same). They never link
anything; they're a sign the shared name may be all two accounts have in
common.

## How distinctive is the username?

Following Perito et al., *How Unique and Traceable Are Usernames?* (PETS
2011): the chance that the same username on two sites is the same person
depends on how much information the name carries. `username_rarity.py`
estimates it in bits: dictionary words, common first names and surnames,
years and "xX_..._Xx" decoration are cheap, random-looking letters are
not; a name taken on a large share of the sites checked is capped as
common whatever it looks like. Shown as common (< 20 bits), moderate
(20-30) or distinctive (30+). It's a heuristic to read results by, never
used to drop one.

## Where it shows

- Terminal: an "Account evidence" panel; tied rows carry `⇄N` (group N).
- `--export json`: `account_evidence` (links, contradictions, groups) and
  `username_rarity`; `--export html`: an "Account evidence" section.
- Entities: tied accounts, contradictions and username rarity add to the
  confidence score and its reasons.
- `osintrecon.core.timeline.evidence_links()` / `account_links()` return
  only tied pairs.
