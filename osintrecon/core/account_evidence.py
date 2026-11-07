"""Evidence that two found accounts belong to the same person.

A username search finds accounts by name, and the same name on two sites is
often two people. This module looks for what actually ties accounts
together, each kind with a strength:

  strong  keybase_proof   a signed Keybase proof
          profile_link    one profile links to the other (website field,
                          rel="me", a link hub button, "ig: alice" in a bio)
          gravatar_match  both photos are the same Gravatar, i.e. the same
                          email address behind both accounts
          same_website    both link to the same personal website
  medium  avatar_match    near-identical profile photo (avatar_correlation)
          same_bio        the same bio text, long enough to be written by
                          someone rather than a template
  weak    same_name       the same display name
          same_location   the same location

Two accounts count as tied when one strong kind, or kinds adding up to the
same weight (two medium ones, or a medium one and two weak ones), connect
them; tied accounts form clusters. Several clusters for one username are
likely several people.

Contradictions are reported too: two accounts giving different real names
or different places. They never link anything; they're a warning that the
shared name may be all the two have in common.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional

from osintrecon.core.models import Finding, IdentifierType, MatchStatus
from osintrecon.core.source_names import source_name
from osintrecon.core.timeline import platform_key

COUNTED = (MatchStatus.CONFIRMED, MatchStatus.PROBABLE)

STRENGTH = {
    "keybase_proof": "strong", "profile_link": "strong", "gravatar_match": "strong", "same_website": "strong",
    "avatar_match": "medium", "same_bio": "medium",
    "same_name": "weak", "same_location": "weak",
}
WEIGHT = {"strong": 1.0, "medium": 0.5, "weak": 0.25}
KINDS = tuple(STRENGTH)  # strongest first
TIED = 1.0

Node = tuple[str, str]  # (platform key, lowercased identifier)


@dataclass(frozen=True)
class Link:
    a: Node
    b: Node
    kind: str
    detail: str = ""
    # What they share (the website, name, place, email) -- for views that
    # build their own sentence, e.g. in another language.
    value: str = ""
    mutual: bool = False

    @property
    def strength(self) -> str:
        return STRENGTH.get(self.kind, "weak")


@dataclass(frozen=True)
class Conflict:
    a: Node
    b: Node
    kind: str          # name_conflict | location_conflict
    detail: str = ""
    value_a: str = ""
    value_b: str = ""


@dataclass
class Verdict:
    kinds: tuple[str, ...] = ()        # evidence tying this account to a tied neighbour
    hints: tuple[str, ...] = ()        # weaker evidence that doesn't reach "tied"
    cluster: Optional[int] = None      # index into clusters(), None when alone
    conflicts: tuple[str, ...] = ()

    @property
    def tied(self) -> bool:
        return self.cluster is not None


@dataclass
class Analysis:
    links: list[Link] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    clusters: list[list[Node]] = field(default_factory=list)
    verdicts: dict[Node, Verdict] = field(default_factory=dict)
    labels: dict[Node, str] = field(default_factory=dict)

    def tied_links(self) -> list[Link]:
        return [link for link in self.links if _pair(link.a, link.b) in self._tied_pairs]

    def cluster_links(self, index: int) -> list[Link]:
        """Every piece of evidence between members of one linked group,
        including weaker kinds that back up the tie."""
        members = set(self.clusters[index])
        return [link for link in self.links if link.a in members and link.b in members]

    def loose_links(self) -> list[Link]:
        """Evidence that doesn't fall inside one linked group: hints."""
        inside = {link for i in range(len(self.clusters)) for link in self.cluster_links(i)}
        return [link for link in self.links if link not in inside]

    _tied_pairs: set = field(default_factory=set)


def _pair(a: Node, b: Node) -> tuple[Node, Node]:
    return (a, b) if a <= b else (b, a)


def node_of(f: Finding) -> Node:
    return (platform_key(f.source), f.identifier.value.lower())


def label_of(f: Finding) -> str:
    return source_name(f.source)


# ---------------------------------------------------------------- text helpers

def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


_GENERIC_PLACES = frozenset("earth internet worldwide everywhere online world here home somewhere nowhere "
                            "global space moon mars universe planet cyberspace web the".split())


def _norm_bio(bio: str, drop: Iterable[str]) -> str:
    text = re.sub(r"https?://\S+", " ", bio)
    text = _fold(text)
    for word in drop:
        word = _fold(word)
        if word:
            text = re.sub(r"\b" + re.escape(word) + r"\b", " ", text)
    text = re.sub(r"\b\d+\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Country names and their adjectives fold to one form, so "Russia" and
# "Russian Federation" don't read as a contradiction.
_PLACE_SYNONYMS = {
    "usa": "unitedstates", "us": "unitedstates", "america": "unitedstates", "uk": "unitedkingdom",
    "britain": "unitedkingdom", "england": "unitedkingdom", "deutschland": "germany", "turkiye": "turkey",
    "polska": "poland", "espana": "spain", "italia": "italy", "nederland": "netherlands", "holland": "netherlands",
    "federation": "", "republic": "", "of": "", "city": "", "state": "", "province": "",
}


def _place_tokens(text: str) -> set[str]:
    out = set()
    for word in _fold(text).split():
        word = _PLACE_SYNONYMS.get(word, word)
        if len(word) >= 2 and word not in _GENERIC_PLACES:
            out.add(word[:5])  # "russia" / "russian" -> "russi"
    return out


def _name_says_nothing(folded: str, username: str) -> bool:
    """A display name that's just the username, or one common first name."""
    from osintrecon.core.username_rarity import _COMMON
    words = folded.split()
    return folded.replace(" ", "") == _fold(username).replace(" ", "") or (len(words) == 1 and words[0] in _COMMON)


def _person_name(name: str) -> Optional[set[str]]:
    """Name tokens when the name looks like a person's real name (two or
    more words of letters), else None."""
    words = [w for w in _fold(name).split() if len(w) >= 2]
    if len(words) < 2 or any(not w.isalpha() for w in words):
        return None
    return set(words)


def _structured_name(f: Finding) -> bool:
    """A name the platform states as the account's name (API field,
    JSON-LD), not one guessed from a page title."""
    source = (f.metadata or {}).get("name_source")
    if f.source.startswith("username_sites:"):
        return source in ("api", "structured")
    return True


def _personal_domains(meta: dict) -> set[str]:
    from osintrecon.core import platform_urls
    urls = list(meta.get("links") or [])
    for key in ("blog", "website", "url_website"):
        if isinstance(meta.get(key), str) and meta[key].strip():
            value = meta[key].strip()
            urls.append(value if "://" in value else "https://" + value)
    domains = set()
    for url in urls:
        if isinstance(url, str) and platform_urls.personal_site(url):
            domains.add(platform_urls.site_key(url))
    return domains


def _email_hashes(email: str) -> set[str]:
    e = email.strip().lower().encode()
    return {hashlib.md5(e).hexdigest(), hashlib.sha256(e).hexdigest()}  # noqa: S324 -- Gravatar's own scheme


# ---------------------------------------------------------------- analysis

def analyze(findings: Iterable[Finding]) -> Analysis:
    findings = list(findings)
    found: dict[Node, list[Finding]] = {}
    for f in findings:
        if f.status in COUNTED and f.source != "avatar_correlation":
            found.setdefault(node_of(f), []).append(f)
    result = Analysis()
    for node, items in found.items():
        result.labels[node] = label_of(items[0])

    links: dict[tuple, Link] = {}

    def link(a: Node, b: Node, kind: str, detail: str = "", value: str = "", mutual: bool = False) -> None:
        if a == b or a not in found or b not in found:
            return
        key = (*_pair(a, b), kind)
        if key not in links:
            # Keep the claimant first for a one-way link ("a links to b").
            first, second = (a, b) if kind in ("profile_link", "keybase_proof") and not mutual else _pair(a, b)
            links[key] = Link(first, second, kind, detail, value, mutual)

    def merged(node: Node, key: str):
        for f in found[node]:
            value = (f.metadata or {}).get(key)
            if value:
                return value
        return None

    # Claimed links between profiles (and signed proofs).
    claims: dict[tuple[Node, Node], str] = {}
    for node, items in found.items():
        for f in items:
            for target in (f.metadata or {}).get("linked_accounts") or []:
                if not (isinstance(target, dict) and target.get("platform") and target.get("username")):
                    continue
                other = (platform_key(str(target["platform"])), str(target["username"]).lower())
                kind = target.get("evidence") or "profile_link"
                if other in found and other != node:
                    claims[(node, other)] = kind
    for (a, b), kind in claims.items():
        la, lb = result.labels[a], result.labels[b]
        if kind == "profile_link":
            detail = f"{la} and {lb} link to each other" if (b, a) in claims else f"{la} links to {lb}"
        else:
            detail = f"{la} carries a signed proof for {lb}"
        link(a, b, kind, detail, mutual=(b, a) in claims)

    # Same photo: avatar_correlation's findings, and identical Gravatar hashes.
    by_avatar: dict[str, list[Node]] = {}
    gravatar: dict[str, list[Node]] = {}
    for node in found:
        url = merged(node, "avatar_url")
        if url:
            by_avatar.setdefault(url, []).append(node)
        h = merged(node, "gravatar_hash")
        if h:
            gravatar.setdefault(h, []).append(node)
    for f in findings:
        if f.source != "avatar_correlation":
            continue
        meta = f.metadata or {}
        a = tuple(meta["account_a"]) if meta.get("account_a") else None
        b = tuple(meta["account_b"]) if meta.get("account_b") else None
        pairs = [(a, b)] if a and b else [(x, y) for x in by_avatar.get(meta.get("avatar_url_a"), [])
                                          for y in by_avatar.get(meta.get("avatar_url_b"), [])]
        for x, y in pairs:
            # Same platform on both sides is a shared default picture.
            if x and y and x[0] != y[0]:
                link(x, y, "avatar_match", f"{result.labels.get(x, x[0])} and {result.labels.get(y, y[0])} "
                                           "use a near-identical profile photo")
    for h, nodes in gravatar.items():
        for i, x in enumerate(nodes):
            for y in nodes[i + 1:]:
                if x[0] != y[0]:
                    link(x, y, "gravatar_match",
                         f"{result.labels[x]} and {result.labels[y]} show the same Gravatar (same email address)")

    # Gravatar hash = a searched or discovered email.
    emails = {f.identifier.value.lower() for f in findings if f.identifier.type is IdentifierType.EMAIL}
    for f in findings:
        emails.update(i.value.lower() for i in f.discovered_identifiers if i.type is IdentifierType.EMAIL)
    email_nodes = {e: [n for n in found if n[1] == e] for e in emails}
    for e in emails:
        hashes = _email_hashes(e)
        for node in found:
            h = merged(node, "gravatar_hash")
            if h and h in hashes:
                for other in email_nodes.get(e, []):
                    if other != node:
                        link(node, other, "gravatar_match", f"{result.labels[node]}'s photo is the Gravatar of {e}",
                             value=e)

    # Same personal website.
    sites: dict[str, list[Node]] = {}
    for node, items in found.items():
        domains: set[str] = set()
        for f in items:
            domains |= _personal_domains(f.metadata or {})
        for d in domains:
            sites.setdefault(d, []).append(node)
    for domain, nodes in sites.items():
        for i, x in enumerate(nodes):
            for y in nodes[i + 1:]:
                link(x, y, "same_website", f"{result.labels[x]} and {result.labels[y]} both link to {domain}",
                     value=domain)

    # Same bio, display name, location -- and contradictions.
    conflicts: dict[tuple, Conflict] = {}
    nodes = list(found)
    info = {}
    for node in nodes:
        drop = [node[1], result.labels[node], node[0]]
        bio = merged(node, "bio")
        name = None
        structured = False
        for f in found[node]:
            if (f.metadata or {}).get("name"):
                name = str(f.metadata["name"])
                structured = _structured_name(f)
                break
        location = merged(node, "location")
        info[node] = {
            "bio": _norm_bio(str(bio), drop) if isinstance(bio, str) else "",
            "name": _fold(name) if name else "",
            "name_raw": name,
            "person": _person_name(name) if name and structured else None,
            "loc": _fold(str(location)) if isinstance(location, str) else "",
            "loc_raw": location,
        }
    for i, x in enumerate(nodes):
        for y in nodes[i + 1:]:
            if x[0] == y[0]:
                continue
            ix, iy = info[x], info[y]
            lx, ly = result.labels[x], result.labels[y]
            bx, by = ix["bio"], iy["bio"]
            if bx and by and len(bx) >= 25 and len(by) >= 25 and len(bx.split()) >= 4:
                wx, wy = set(bx.split()), set(by.split())
                if bx == by or (min(len(wx), len(wy)) >= 6 and len(wx & wy) / len(wx | wy) >= 0.8):
                    link(x, y, "same_bio", f"{lx} and {ly} have the same bio")
            nx, ny = ix["name"], iy["name"]
            if nx and ny and len(nx) >= 3:
                if nx == ny and not _name_says_nothing(nx, x[1]):
                    link(x, y, "same_name", f"{lx} and {ly} both use the name \"{ix['name_raw']}\"",
                         value=ix["name_raw"])
                elif ix["person"] and iy["person"] and not (ix["person"] & iy["person"]):
                    conflicts[(x, y, "name_conflict")] = Conflict(
                        x, y, "name_conflict", f"{lx} says \"{ix['name_raw']}\", {ly} says \"{iy['name_raw']}\"",
                        ix["name_raw"], iy["name_raw"])
            px, py = ix["loc"], iy["loc"]
            if px and py and len(px) >= 3 and px not in _GENERIC_PLACES and py not in _GENERIC_PLACES:
                tx, ty = _place_tokens(px), _place_tokens(py)
                if px == py:
                    link(x, y, "same_location", f"{lx} and {ly} both give the location \"{ix['loc_raw']}\"",
                         value=ix["loc_raw"])
                elif tx and ty and not (tx & ty):
                    conflicts[(x, y, "location_conflict")] = Conflict(
                        x, y, "location_conflict", f"{lx} says \"{ix['loc_raw']}\", {ly} says \"{iy['loc_raw']}\"",
                        str(ix["loc_raw"]), str(iy["loc_raw"]))

    result.links = sorted(links.values(), key=lambda lk: (KINDS.index(lk.kind), lk.a, lk.b))
    result.conflicts = list(conflicts.values())

    # Tied pairs and clusters.
    weight: dict[tuple[Node, Node], float] = {}
    kinds_of_pair: dict[tuple[Node, Node], set[str]] = {}
    for lk in result.links:
        pair = _pair(lk.a, lk.b)
        if lk.kind not in kinds_of_pair.setdefault(pair, set()):
            kinds_of_pair[pair].add(lk.kind)
            weight[pair] = weight.get(pair, 0.0) + WEIGHT[lk.strength]
    tied = {pair for pair, w in weight.items() if w >= TIED - 1e-9}
    result._tied_pairs = tied

    parent: dict[Node, Node] = {}

    def find(n: Node) -> Node:
        parent.setdefault(n, n)
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for a, b in tied:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    groups: dict[Node, list[Node]] = {}
    for a, b in tied:
        for n in (a, b):
            members = groups.setdefault(find(n), [])
            if n not in members:
                members.append(n)
    result.clusters = sorted((sorted(m) for m in groups.values()), key=lambda m: (-len(m), m))
    cluster_of = {n: i for i, members in enumerate(result.clusters) for n in members}

    for node in found:
        tied_kinds: set[str] = set()
        hint_kinds: set[str] = set()
        for pair, kinds in kinds_of_pair.items():
            if node not in pair:
                continue
            (tied_kinds if pair in tied else hint_kinds).update(kinds)
        node_conflicts = {c.kind for c in result.conflicts if node in (c.a, c.b)}
        result.verdicts[node] = Verdict(
            kinds=tuple(k for k in KINDS if k in tied_kinds),
            hints=tuple(k for k in KINDS if k in hint_kinds - tied_kinds),
            cluster=cluster_of.get(node),
            conflicts=tuple(sorted(node_conflicts)),
        )
    return result


def gravatar_email_matches(findings: Iterable[Finding]) -> list[tuple[Finding, str]]:
    """(finding, email) where a found account's photo is the Gravatar of a
    searched or discovered email -- the engine records the email on that
    finding so correlation joins the two."""
    findings = list(findings)
    emails = {f.identifier.value.lower() for f in findings if f.identifier.type is IdentifierType.EMAIL}
    for f in findings:
        emails.update(i.value.lower() for i in f.discovered_identifiers if i.type is IdentifierType.EMAIL)
    if not emails:
        return []
    by_hash = {h: e for e in emails for h in _email_hashes(e)}
    out = []
    for f in findings:
        h = (f.metadata or {}).get("gravatar_hash")
        if f.status in COUNTED and h in by_hash and f.identifier.value.lower() != by_hash[h]:
            out.append((f, by_hash[h]))
    return out


def to_dict(analysis: Analysis) -> dict:
    """JSON-ready view: links (with strength and whether they tie the
    pair), contradictions and clusters, accounts as "platform:identifier"."""
    def key(node: Node) -> str:
        return f"{node[0]}:{node[1]}"

    tied = analysis._tied_pairs
    return {
        "links": [{"a": key(lk.a), "b": key(lk.b), "kind": lk.kind, "strength": lk.strength,
                   "ties": _pair(lk.a, lk.b) in tied, "detail": lk.detail, "value": lk.value, "mutual": lk.mutual}
                  for lk in analysis.links],
        "conflicts": [{"a": key(c.a), "b": key(c.b), "kind": c.kind, "detail": c.detail} for c in analysis.conflicts],
        "clusters": [[{"account": key(n), "label": analysis.labels.get(n, n[0])} for n in members]
                     for members in analysis.clusters],
    }
