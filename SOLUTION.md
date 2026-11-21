# Continuity Checker for AI-written microdramas (Problem 3)

## The problem, and how I know it is real
**Who:** the script / continuity editor who signs off AI-generated episodes before they go to
video generation, voice and dubbing.
**Evidence, from Kuku's own input pack (50 episodes):**
- The hero's ducal estate has **at least 8 names across 53 mentions**: Ashbury Manor (Ep 20–23), Ashburg Manor
  (Ep 19, 25–26), Ashburgh (Ep 40–41), Ashburn (Ep 42–43), Ashburton (Ep 22, 45–46), Ashbourne (Ep 27, 36, 47),
  Ashbrook (Ep 29) and Ashwood (Ep 35–36). He is crowned "Duke of Ashburn" in Ep 42 and "Duke of Ashburton" three
  episodes later.
- The villainous sister is "Clarissa" 74 times and "Clarisse" once, spoken aloud in Ep 31.
- Juliana's speaker label switches between JULIANA (31 episodes) and LADY JULIANA (8). A voice or subtitle tool
  that keys on the label would split her into two characters.
- The late mother's book appears as a ledger, grimoire, herbarium, remedy journal and botanical book in different
  episodes (whether these are one object needs an editor's call).

These errors look like generation batches drifting apart: the variants rarely share an episode.
**Why it matters:** binge viewers watch 50 episodes in a sitting, and a name said wrong in the voice track is
heard. One script error is copied into every dub, subtitle file and promo cut, and fixing it after video
generation means re-rendering. **Why above Problems 1 and 2:** it is upstream. A fix in the script fixes every
downstream asset, including the promos from Problem 1. **What I could not prove:** I found no viewer reviews
complaining about name drift specifically. Reviews mostly attack paywalls and recycled, machine-translated plots.
The evidence is the pack itself.

## What I built
One person (the continuity editor), one step (script QA before video), one checkable output: a list of flags,
each with episodes, beat IDs and exact quotes, plus a find/replace fix list.
- **Rules (free, ~0.1 s per show).** Mine proper nouns (always capitalised, used mid-sentence). Cluster similar
  spellings. Flag speaker labels that differ only by an honorific (LADY X vs X, but not LADY X vs LORD X). Severity
  is high when a wrong spelling is spoken in dialogue.
- **LLM (optional).** (1) Adjudicates each cluster: same entity, a different entity or a deliberate alias.
  (2) Discovers groups that spelling misses (Ashwood). (3) Finds fact contradictions about objects and relations.
  **Every quote the model gives is checked against the script by code; unverifiable claims are dropped and logged**
  (`rejected_llm_claims.json`).
- Output: `report.html` (timeline of variants, evidence), `continuity_flags.json`, `fix_list.csv` (27 per-episode
  find/replace rows, "needs review" until an editor or the LLM confirms).

## Results on the pack and failures
6 flags. F01 (estate cluster, 8 spellings, 54 mentions incl. Ashbridge) and F02 (Clarisse) are true errors; F03–F05 are real label drift;
F06 (THE PRIEST / PRIEST) is trivial. Where it fails:
- F01 wrongly merges **Ashbridge**, which is Catherine's surname.
- It misses **Ashwood** and **Ashwin** (the hero's surname in Ep 39), whose spelling is too different.
- "Julian" (Juliana's deliberate disguise name in Ep 22) is not flagged only because it opens a sentence. That is
  luck, not judgement.
- Rules cannot see prop drift (the book).

Re-run after the DR./MR. fix below: the same 6 flags.

**LLM mode on the pack (Groq gpt-oss-120b; partial: the free tier's 200k tokens/day ran out, so fact checks covered
only Ep 6–12, 36–42 and 48–50).** It confirmed Clarisse as an error, and a fact check caught Ashwin vs Ashburn
(Ep 39 vs 42), which rules miss. But it got the estate wrong: adjudication put each of the 8 spellings in its own
"different entity" group (so Ashbridge was separated only along with everything else), while discovery merged all
8 plus Ashcrofts into one estate. It did not find Ashwood. On this pack the LLM verdicts still need an editor.

**My own synthetic show** (`tests/synthetic_show/`, original 8-episode story, same format): 6 planted errors
(3 spellings of the town, a name misspelled once in dialogue, MAYA vs DR. MAYA, a red scarf that turns blue) and
2 traps (Elias posing as "Elian"; Marta and Martha, two different women).

| backend | found | missed | false alarms | recall | precision |
| --- | --- | --- | --- | --- | --- |
| rules | 5 | scarf (LLM only) | Elias/Elian, Marta/Martha | 0.83 | 0.71 |
| llm (Groq gpt-oss-120b) | 6 | none | 3 (below) | 1.00 | 0.67 |

The LLM cleared both traps (alias → *intentional*, Marta/Martha → *different people*) and found the scarf. Its 3
false alarms: two "discovered" groups that are first name + surname of one person (Elian/Ross, Marta/Lind), and a
fact claim whose quotes are real but whose contradiction is not. Quote checking stops invented quotes, not wrong
reasoning over real ones. The test exposed 3 bugs, now fixed: `DR.`/`MR.` labels were not seen as honorifics; the
verifier dropped the true scarf finding because the model copied the prompt's `(action)` / `NAME:` wrappers; and
adjudication lost the correct *intentional* verdict because the model answered with full names ("Elias Varga").
Before the fixes: rules 0.67 recall / 0.67 precision, LLM 0.83 / 0.63 (same cached model answers).

## How I'd know it worked with a real team
- Editor acceptance rate of flags (target above 70%).
- Distinct spellings per entity per show trending to 1.
- Re-renders caused by continuity fixes after video generation trending to 0.
- Fewer viewer comments naming the wrong character.

**Cost:** rules are free. LLM mode is about 15–20 calls (one per flag, one discovery, about 9 six-episode windows),
roughly 100k input tokens: about $0.03 a show on GPT-OSS-120B via Groq ($0.15 / $0.75 per 1M tokens). Next step: write the confirmed names back
into a series bible that is fed into the generation prompt, so drift is prevented rather than caught.
