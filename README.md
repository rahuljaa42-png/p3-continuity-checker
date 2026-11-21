# Continuity Checker — catch name and fact drift in AI-written microdrama scripts

## API key (Groq) — one-time setup
1. Get a free key: https://console.groq.com/keys
2. Open **`keys.env`** in this folder (`open -e keys.env` on Mac) and paste it after `GROQ_API_KEY=`, save.
3. That is it: every command below reads it. `keys.env` is in `.gitignore`, so it never goes to GitHub.
4. On Streamlit Cloud: App → Settings → **Secrets**, add the line `GROQ_API_KEY = "your_key"`.

If Groq says "model not found", change `LLM_MODEL=` in `keys.env` to a model from https://console.groq.com/docs/models.

```bash
pip install -r requirements.txt
python run.py --input path/to/show.zip --out outputs/rules                 # free, ~0.1 s
python run.py --input path/to/show.zip --out outputs/llm --backend llm     # Groq key from keys.env
open outputs/rules/report.html
```

Outputs: `report.html`, `continuity_flags.json`, `fix_list.csv` (per-episode find/replace),
`rejected_llm_claims.json` (model claims dropped because their quotes were not in the script).

| file | what |
| --- | --- |
| `continuity/parser.py` | screenplay → episodes and timed beats (shared with Promo Finder) |
| `continuity/checks.py` | proper-noun mining, spelling clusters, speaker-label drift, quote verifier |
| `continuity/llm_checks.py` | LLM adjudication, discovery, fact contradictions; every quote verified |
| `continuity/llm.py` | OpenAI-compatible client (swap model with `--provider/--model/--base-url`) |

The input pack is Kuku's content: `.gitignore` keeps `*.zip`, `data/` and `outputs/` out of the repo.
See `SOLUTION.md` for the problem, evidence and results.
