"""How likely is it that the same username on two sites is the same person?

The answer depends on the name itself (Perito et al., "How Unique and
Traceable Are Usernames?", PETS 2011): "john" is registered by thousands
of different people, "x7kq_lena" by very few. This module gives a rough
estimate in bits of how much a username says, from two signals:

  * the string: dictionary words and common first names are cheap, digits
    that look like a year or a short counter add little, random-looking
    letters and mixed character classes add a lot;
  * this run's hit rate: a name taken on a large share of the sites
    checked is a popular name, whoever holds it.

It's a heuristic, not a measurement -- shown so a reader can weigh "found
on 40 sites" correctly, never used to drop a result.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Common English words, first names and username filler. Lowercase.
_COMMON = frozenset("""
a an the and or of to in on at by for with from is it me my we you your our us he she they them his her
i am are be was were do did done go get got make made take took see saw new old big small good bad best
real true official the1 its im iam mr mrs ms miss dr sir lady lord king queen prince princess boss chief
pro noob gamer gaming games game play player plays live tv news music art arts artist design designer dev
code coder hacker tech admin user test demo info mail web site blog online shop store love lover life
lives world city man men woman women boy boys girl girls kid kids baby babe guy guys dude bro sis team
crew club fan fans star stars sun moon sky night day dark light black white red blue green gold silver
pink purple grey gray orange yellow fire ice water wind storm rain snow shadow ghost demon angel devil
god dragon wolf fox cat cats dog dogs bear lion tiger eagle hawk shark snake panda bunny monkey ninja
samurai warrior knight hunter killer master legend hero zero one two three four five six seven eight
nine ten max mini super ultra mega hyper alpha beta omega prime x xx xxx xo lol omg wtf cool hot sexy
cute sweet happy sad crazy mad wild free rich lucky magic dream dreams soul spirit mind heart rock metal
punk jazz pop rap hip hop beat beats dj mc sound audio video photo photos film films movie movies book
books read write writer poet story stories food cook chef coffee tea pizza cake candy sugar honey
travel trip road car cars bike fly sea ocean beach island mountain forest garden flower rose lily
apple banana cherry lemon mango peach berry kiwi
john james robert michael william david richard joseph thomas charles chris christopher daniel matthew
anthony mark donald steven paul andrew joshua kevin brian george edward ronald timothy jason jeffrey ryan
jacob gary nicholas eric jonathan stephen larry justin scott brandon benjamin samuel frank gregory
raymond alexander patrick jack dennis jerry tyler aaron jose adam henry nathan douglas zachary peter
kyle walter ethan jeremy harold keith christian roger noah gerald carl terry sean austin arthur
lawrence jesse dylan bryan joe jordan billy bruce albert willie gabriel logan alan juan wayne roy
ralph randy eugene vincent russell elijah louis bobby philip johnny mary patricia jennifer linda
elizabeth barbara susan jessica sarah karen nancy lisa betty margaret sandra ashley kimberly emily
donna michelle dorothy carol amanda melissa deborah stephanie rebecca sharon laura cynthia kathleen
amy shirley angela helen anna brenda pamela nicole emma samantha katherine christine debra rachel
catherine carolyn janet ruth maria heather diane virginia julie joyce victoria olivia kelly christina
lauren joan evelyn judith megan cheryl andrea hannah martha jacqueline frances gloria ann teresa
kathryn sara janice jean alice madison doris abigail julia judy grace denise amber marilyn beverly
danielle theresa sophia marie diana brittany natalie isabella charlotte rose alexis kayla lena leo
luca lucas liam mia ava ella amelia harper evelyn aria chloe zoe nora lily layla riley sam alex
ahmet mehmet mustafa ali ayse fatma emre can burak murat elif zeynep ivan olga anna maria jan piotr
smith johnson williams brown jones garcia miller davis rodriguez martinez hernandez lopez gonzalez wilson
anderson taylor moore jackson martin lee thompson harris clark lewis walker hall young allen king wright
scott green baker adams nelson hill campbell mitchell roberts carter phillips evans turner torres parker
kim nguyen mohammed muhammad ahmed singh kumar khan wang li zhang liu chen yang huang zhao wu zhou
yilmaz kaya demir sahin celik nowak kowalski muller schmidt schneider fischer weber rossi russo silva
santos ferreira dad daddy mom mommy mama papa bob bobby johnny jimmy tommy danny mike dave steve nick
tony matt josh ben joe ken sam tim tom ray bill jim ron don ed fred greg ted jen jess kate liz meg sue
""".split())

# "xX_name_Xx": decoration everyone uses.
_DECORATION = frozenset({"x", "xx", "xxx", "xo", "xd", "the", "its", "im", "iam", "real", "official", "mr", "ms"})
_YEAR = re.compile(r"(19[5-9]\d|20[0-3]\d)")


@dataclass(frozen=True)
class Rarity:
    username: str
    bits: float
    label: str                 # common | moderate | rare
    hits: int = 0
    checked: int = 0

    @property
    def hit_rate(self) -> float:
        return self.hits / self.checked if self.checked else 0.0

    def to_dict(self) -> dict:
        return {"username": self.username, "bits": round(self.bits, 1), "label": self.label,
                "hits": self.hits, "checked": self.checked}


def _segment_bits(segment: str) -> float:
    if not segment:
        return 0.0
    if segment.isdigit():
        if _YEAR.fullmatch(segment):
            return 6.0
        if len(segment) <= 2:
            return 4.0 + len(segment)
        return 3.3 * len(segment)
    word = segment.lower()
    vowels = sum(c in "aeiouy" for c in word)
    # Letters that aren't a known word: about 4 bits each for pronounceable
    # strings, more when vowels are scarce (random-looking).
    per_char = 4.0 if vowels and len(word) / max(vowels, 1) <= 3.5 else 4.7

    def piece(chunk: str) -> float:
        if chunk in _DECORATION:
            return 1.5
        if chunk in _COMMON and (len(chunk) >= 2 or chunk in ("a", "i", "x")):
            return 9.0
        return per_char * len(chunk)

    # Cheapest split into known words and leftover letters
    # ("therealjohn" -> the + real + john).
    best = [0.0] + [math.inf] * len(word)
    for i in range(1, len(word) + 1):
        for j in range(max(0, i - 12), i):
            best[i] = min(best[i], best[j] + piece(word[j:i]) + (1.0 if j else 0.0))
    return best[-1]


def string_bits(username: str) -> float:
    """Rough information content of the name itself, in bits."""
    name = username.strip()
    if not name:
        return 0.0
    parts = re.findall(r"[A-Za-z]+|\d+|[^A-Za-z\d]+", name)
    bits = 0.0
    # camelCase splits into words too ("DarkWolf")
    for part in parts:
        if part.isalpha():
            for word in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", part) or [part]:
                bits += _segment_bits(word)
        elif part.isdigit():
            bits += _segment_bits(part)
        else:
            bits += 1.5  # separator: ".", "_", "-"
    if len(parts) > 1:
        bits += math.log2(len(parts))  # which pieces, in which order
    return bits


def assess(username: str, hits: int = 0, checked: int = 0) -> Rarity:
    bits = string_bits(username)
    # Taken on a large share of sites: popular regardless of how it looks.
    if checked >= 30:
        rate = hits / checked
        if rate >= 0.5:
            bits = min(bits, 16.0)
        elif rate >= 0.35:
            bits = min(bits, 22.0)
    label = "common" if bits < 20 else "moderate" if bits < 30 else "rare"
    return Rarity(username, bits, label, hits, checked)
