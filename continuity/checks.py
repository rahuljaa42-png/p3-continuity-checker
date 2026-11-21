"""Rule-based continuity checks for AI-written microdrama scripts.

Finds names that drift across episodes ("Ashbury Manor" -> "Ashburg Manor" -> "Ashburton"),
speaker labels that drift ("JULIANA" vs "LADY JULIANA"), and gives every flag its evidence:
episode, beat ID and the exact quote. A human (or the LLM adjudicator) decides if it is a mistake.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher

from .parser import Episode

WORD_RE = re.compile(r"[A-Za-z][a-z]+(?:'s)?")
HONORIFICS = {"LADY", "LORD", "SIR", "THE", "MR", "MRS", "MS", "MISS", "MASTER", "DAME", "YOUNG", "OLD",
              "DR", "DOCTOR"}

STOP = {"the", "and", "but", "you", "she", "his", "her", "him", "they", "what", "who", "oh", "ah",
        "god", "yes", "no", "none", "miss", "duke", "duchess", "lady", "lord", "king", "queen"}


def _is_hon(w: str) -> bool:
    return w.rstrip(".") in HONORIFICS   # "DR." and "MR." carry a period in speaker labels


@dataclass
class Mention:
    word: str
    episode: int
    beat: str
    kind: str
    quote: str


@dataclass
class Flag:
    kind: str                       # name_variant | speaker_label
    variants: dict                  # variant -> count
    canonical: str
    episodes: dict                  # variant -> [episodes]
    evidence: list = field(default_factory=list)   # [{variant, episode, beat, quote}]
    severity: str = "medium"
    note: str = ""
    verdict: str = ""               # filled by LLM / editor: error | intentional | different_entity
    id: str = ""
    llm_groups: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def _texts(ep: Episode):
    yield ("logline", f"E{ep.number:02d}-LOG", "logline", ep.logline)
    yield ("notes", f"E{ep.number:02d}-NOTES", "notes", ep.continuity)
    for b in ep.beats:
        yield (b.kind, b.id, b.kind, b.text)


def proper_nouns(episodes: list[Episode]) -> dict[str, list[Mention]]:
    """Words that are always capitalised in the show and appear mid-sentence at least once."""
    cap, low, mid = Counter(), Counter(), Counter()
    mentions = defaultdict(list)
    for ep in episodes:
        for kind, bid, _, text in _texts(ep):
            for m in WORD_RE.finditer(text):
                w = m.group(0)
                base = re.sub(r"'s$", "", w)
                key = base.lower()
                if w[0].isupper():
                    cap[key] += 1
                    before = text[:m.start()].rstrip()
                    if before and before[-1] not in ".!?\"“—:" and not before.endswith("..."):
                        mid[key] += 1
                    s = max(0, m.start() - 70)
                    mentions[key].append(Mention(base, ep.number, bid, kind,
                                                 text[s:m.end() + 70].strip()))
                else:
                    low[key] += 1
    keep = {k for k in cap if low[k] == 0 and mid[k] > 0 and k not in STOP and len(k) >= 4}
    return {k: v for k, v in mentions.items() if k in keep}


def _similar(a: str, b: str) -> bool:
    if a == b or a.startswith(b + "s") or b.startswith(a + "s"):
        return False   # plural / family form ("Ashcroft" vs "Ashcrofts")
    if min(len(a), len(b)) < 5:
        return False
    r = SequenceMatcher(None, a, b).ratio()
    return r >= 0.78 or (a[:4] == b[:4] and r >= 0.7)


def cluster(words: list[str]) -> list[list[str]]:
    parent = {w: w for w in words}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    ws = sorted(words)
    for i, a in enumerate(ws):
        for b in ws[i + 1:]:
            if _similar(a, b):
                parent[find(a)] = find(b)
    groups = defaultdict(list)
    for w in ws:
        groups[find(w)].append(w)
    return [g for g in groups.values() if len(g) > 1]


def name_variant_flags(episodes: list[Episode], max_evidence: int = 3) -> list[Flag]:
    pn = proper_nouns(episodes)
    flags = []
    for group in cluster(list(pn)):
        counts = {pn[w][0].word: len(pn[w]) for w in group}
        eps = {pn[w][0].word: sorted({m.episode for m in pn[w]}) for w in group}
        canonical = max(counts, key=lambda k: (counts[k], -min(eps[k])))
        ev = []
        for w in group:
            for m in pn[w][:max_evidence]:
                ev.append({"variant": m.word, "episode": m.episode, "beat": m.beat, "where": m.kind,
                           "quote": m.quote})
        spoken = any(m.kind == "dialogue" for w in group for m in pn[w] if m.word != canonical)
        overlap = sum(1 for a in eps for b in eps if a < b and set(eps[a]) & set(eps[b]))
        sev = "high" if spoken else "medium"
        note = ("A non-canonical spelling is spoken aloud in dialogue. " if spoken else "") + \
               ("Variants appear in the same episode: may be different people/places. " if overlap else
                "Variants never share an episode: classic drift between generation batches.")
        flags.append(Flag("name_variant", counts, canonical, eps, ev, sev, note.strip()))
    flags.sort(key=lambda f: (-len(f.variants), -sum(f.variants.values())))
    return flags


def speaker_label_flags(episodes: list[Episode]) -> list[Flag]:
    labels = defaultdict(lambda: defaultdict(set))
    first = {}
    for ep in episodes:
        for b in ep.beats:
            if b.kind == "dialogue" and b.speaker:
                raw = re.sub(r"\s*\((?:V\.O\.|O\.S\.|O\.C\.|CONT'D)\)", "", b.speaker.upper()).strip()
                core = " ".join(w for w in raw.split() if not _is_hon(w))
                if core:
                    labels[core][raw].add(ep.number)
                    first.setdefault((core, raw), (ep.number, b.id, b.text))
    flags = []
    for core, raws in labels.items():
        if len(raws) < 2:
            continue
        # LADY X vs LORD X are two people; only flag "X" vs "LADY X" (one honorific set inside the other)
        hon = {r: {w.rstrip('.') for w in r.split() if _is_hon(w)} for r in raws}
        rs = list(raws)
        if not any(hon[a] < hon[b] or hon[b] < hon[a] for i, a in enumerate(rs) for b in rs[i + 1:]):
            continue
        counts = {r: len(e) for r, e in raws.items()}
        canonical = max(counts, key=counts.get)
        ev = [{"variant": r, "episode": first[(core, r)][0], "beat": first[(core, r)][1], "where": "speaker",
               "quote": f"{r}: {first[(core, r)][2][:90]}"} for r in raws]
        flags.append(Flag("speaker_label", counts, canonical, {r: sorted(e) for r, e in raws.items()}, ev,
                          "low", "Same character under different speaker labels; voice casting or "
                                 "subtitle tools that key on the label will split this character."))
    flags.sort(key=lambda f: -sum(f.variants.values()))
    return flags


def verify_quote(episodes: dict[int, Episode], episode: int, quote: str) -> bool:
    """True only if the quote really occurs in that episode (whitespace/quote-mark tolerant)."""
    ep = episodes.get(episode)
    if not ep or not quote.strip():
        return False

    def norm(s):
        return re.sub(r"[\s\"“”'‘’]+", " ", s).strip().lower()
    # The fact prompt shows beats as "[Ep 3] NAME: line" and "[Ep 3] (action)", so models copy those
    # wrappers; strip them (and leading/trailing ellipses), never the words themselves.
    q = quote.strip()
    q = re.sub(r"^\[Ep \d+\]\s*", "", q)
    q = re.sub(r"^[A-Z][A-Z0-9 .'-]*(?:\([^)]*\))?:\s+", "", q)
    q = re.sub(r"^(?:\.\.\.|…)\s*|\s*(?:\.\.\.|…)$", "", q).strip()
    if q.startswith("(") and q.endswith(")"):
        q = q[1:-1]
    q = norm(q)
    if not q:
        return False
    hay = norm(" ".join([ep.logline, ep.continuity] + [b.text for b in ep.beats]))
    return q in hay
