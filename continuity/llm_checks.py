"""LLM steps. The model judges and discovers; code verifies every quote it gives.

1. adjudicate: for each rule flag, which variants are the SAME entity (error) vs different
   people/places vs deliberate (an alias, a disguise).
2. discover: same-entity groups that string similarity missed ("Ashwood" vs "Ashbury").
3. facts: contradictions about objects, relationships and facts across episodes.
Any claim whose quote is not found in the cited episode is dropped and logged as rejected.
"""
from __future__ import annotations

import json
import re

from .checks import Flag, proper_nouns, verify_quote
from .llm import LLM, LLMError
from .parser import Episode

SYSTEM = """You are a script continuity supervisor for AI-generated vertical microdramas.
Episodes were generated in batches, so names, places, titles and objects drift.
Be precise: an alias, a disguise or two genuinely different people is NOT an error.
Quote the script exactly when asked. Reply with JSON only."""

ADJUDICATE = """These spellings were found in one show and look similar:
{variants}

Example lines (episode, quote):
{evidence}

Episode loglines for context:
{loglines}

Split them into groups that refer to the SAME person/place/thing. For each group:
- verdict: "error" (one entity, inconsistent naming), "intentional" (alias, disguise, nickname),
  or "different_entity" (genuinely separate)
- canonical: the spelling to standardise on (for errors)
JSON: {{"groups": [{{"entity": "short description", "variants": ["..."], "verdict": "error",
  "canonical": "...", "why": "one line"}}]}}"""

DISCOVER = """Proper nouns used in this show (word | count | episodes | example):
{table}

Already grouped by spelling similarity: {known}

List OTHER groups of words that refer to the same person, place or title but are spelled
differently (e.g. an estate called two different names). Only groups you can support with
the examples above. JSON: {{"groups": [{{"entity": "...", "variants": ["..."], "why": "..."}}]}}"""

FACTS = """Episodes {first}–{last} of a microdrama (loglines, continuity notes and dialogue):

{script}

Find CONTINUITY ERRORS: the same object, relationship, title, number or fact described in
incompatible ways in different episodes (e.g. a ring that is emerald in one episode and ruby
in another; a character called someone's sister and later their cousin). Ignore normal plot
development and deliberate lies by characters.
For each, quote BOTH places exactly (copy words from the script, 5–20 words each).
JSON: {{"issues": [{{"what": "...", "a": {{"episode": 3, "quote": "..."}},
  "b": {{"episode": 9, "quote": "..."}}, "why": "..."}}]}}"""


def adjudicate(llm: LLM, flags: list[Flag], episodes: list[Episode], log: list) -> None:
    eps = {e.number: e for e in episodes}
    for f in flags:
        if f.kind != "name_variant":
            continue
        involved = sorted({e for v in f.episodes.values() for e in v})
        user = ADJUDICATE.format(
            variants="\n".join(f"- {v}: {c} mentions, episodes {f.episodes[v]}" for v, c in f.variants.items()),
            evidence="\n".join(f"- Ep {x['episode']}: {x['quote']}" for x in f.evidence),
            loglines="\n".join(f"Ep {n}: {eps[n].logline}" for n in involved if n in eps))
        try:
            data = llm.chat_json(SYSTEM, user, tag=f"adjudicate-{f.canonical}", mock_fn=_mock_adj(f))
        except LLMError as e:
            if e.fatal:
                raise
            f.note += f" (LLM adjudication failed: {e})"
            continue
        groups = []
        for g in data.get("groups") or []:
            mapped = [_to_variant(v, f.variants) for v in g.get("variants", [])]
            vs = list(dict.fromkeys(v for v in mapped if v))
            bad = [v for v, m in zip(g.get("variants", []), mapped) if not m]
            if bad:
                log.append({"step": "adjudicate", "flag": f.canonical, "reason": f"unknown variants {bad}"})
            if vs:
                groups.append({**g, "variants": vs})
        f.llm_groups = groups  # type: ignore[attr-defined]
        errs = [g for g in groups if g.get("verdict") == "error" and len(g["variants"]) > 1]
        if not groups:   # nothing usable came back: do not invent a verdict
            f.verdict = "needs_review"
            continue
        f.verdict = "error" if errs else ("intentional" if any(g.get("verdict") == "intentional" for g in groups)
                                          else "different_entity")
        if errs:
            can = errs[0].get("canonical")
            if can in f.variants:
                f.canonical = can


def discover(llm: LLM, flags: list[Flag], episodes: list[Episode], log: list) -> list[Flag]:
    pn = proper_nouns(episodes)
    known = sorted({v.lower() for f in flags for v in f.variants})
    rows = []
    for k, ms in sorted(pn.items(), key=lambda x: -len(x[1])):
        rows.append(f"{ms[0].word} | {len(ms)} | {sorted({m.episode for m in ms})} | {ms[0].quote[:110]}")
    user = DISCOVER.format(table="\n".join(rows), known=", ".join(known) or "(none)")
    try:
        data = llm.chat_json(SYSTEM, user, tag="discover", mock_fn=lambda u: {"groups": []})
    except LLMError as e:
        if e.fatal:
            raise
        log.append({"step": "discover", "reason": str(e)})
        return []
    out = []
    for g in data.get("groups") or []:
        vs = [v for v in g.get("variants", []) if v.lower() in pn]
        missing = [v for v in g.get("variants", []) if v.lower() not in pn]
        if missing or len(vs) < 2:
            log.append({"step": "discover", "reason": f"variants not in script: {missing}", "raw": g})
            continue
        counts = {pn[v.lower()][0].word: len(pn[v.lower()]) for v in vs}
        epsd = {pn[v.lower()][0].word: sorted({m.episode for m in pn[v.lower()]}) for v in vs}
        ev = [{"variant": m.word, "episode": m.episode, "beat": m.beat, "where": m.kind, "quote": m.quote}
              for v in vs for m in pn[v.lower()][:2]]
        out.append(Flag("name_variant_llm", counts, max(counts, key=counts.get), epsd, ev, "medium",
                        "Found by the LLM, not by spelling similarity: " + str(g.get("why", ""))[:200],
                        "needs_review"))
    return out


def facts(llm: LLM, episodes: list[Episode], log: list, window: int = 3) -> list[dict]:
    eps = {e.number: e for e in episodes}
    issues = []
    # 1-episode overlap so boundaries are compared
    queue = [episodes[max(0, i - 1): i + window] for i in range(0, len(episodes), window)]
    while queue:
        win = queue.pop(0)
        parts = []
        for e in win:
            lines = [f"[Ep {e.number}] Logline: {e.logline}", f"[Ep {e.number}] Notes: {e.continuity}"]
            lines += [f"[Ep {e.number}] {b.speaker}: {b.text}" if b.kind == "dialogue" else f"[Ep {e.number}] ({b.text})"
                      for b in e.beats if b.kind in ("dialogue", "action")]
            parts.append("\n".join(lines))
        user = FACTS.format(first=win[0].number, last=win[-1].number, script="\n\n".join(parts))
        print(f"  facts: episodes {win[0].number}-{win[-1].number} (~{len(user) // 4} tokens)", flush=True)
        try:
            data = llm.chat_json(SYSTEM, user, tag=f"facts-{win[0].number}", mock_fn=lambda u: {"issues": []})
        except LLMError as e:
            if e.fatal:
                raise
            if "HTTP 413" in str(e) and len(win) > 2:
                # Request bigger than the provider's per-request token limit (Groq free tier: 8k/min).
                # Split the window in two (sharing one episode) instead of silently skipping it.
                mid = len(win) // 2
                queue[:0] = [win[:mid + 1], win[mid:]]
                log.append({"step": "facts", "window": win[0].number,
                            "reason": f"too large for the provider, split into Ep {win[0].number}-"
                                      f"{win[mid].number} and Ep {win[mid].number}-{win[-1].number}"})
                continue
            log.append({"step": "facts", "window": win[0].number, "reason": str(e)})
            continue
        for it in data.get("issues") or []:
            a, b = it.get("a") or {}, it.get("b") or {}
            ok_a = verify_quote(eps, _int(a.get("episode")), str(a.get("quote", "")))
            ok_b = verify_quote(eps, _int(b.get("episode")), str(b.get("quote", "")))
            if ok_a and ok_b and _int(a.get("episode")) != _int(b.get("episode")):
                issues.append(it)
            else:
                log.append({"step": "facts", "reason": "quote not found in script (dropped)",
                            "quote_a_found": ok_a, "quote_b_found": ok_b, "raw": it})
    return issues


def _to_variant(name: str, variants) -> str | None:
    """Map the model's 'Elias Varga' back to the flagged spelling 'Elias' (exact word match only)."""
    if name in variants:
        return name
    hits = [w for w in re.findall(r"[A-Za-z]+", name) if w in variants]
    return hits[0] if len(hits) == 1 else None


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return -1


def _mock_adj(f: Flag):
    return lambda u: {"groups": [{"entity": "mock", "variants": list(f.variants), "verdict": "error",
                                  "canonical": f.canonical, "why": "mock"}]}
