# Continuity Checker — catch name and fact drift in AI-written microdrama scripts

## API key (Groq) — one-time setup
1. Get a free key: https://console.groq.com/keys
2. Open **`keys.env`** in this folder (`open -e keys.env` on Mac) and paste it after `GROQ_API_KEY=`, save.
   A `GROQ_API_KEY` already exported in your shell takes priority over the file.
3. That is it: the CLI, the app and the synthetic test all read it. `keys.env` is in `.gitignore`,
   so it never goes to GitHub.
4. On Streamlit Cloud: App → Settings → **Secrets**, add the line `GROQ_API_KEY = "your_key"`.

Default model: `openai/gpt-oss-120b` (Groq retired `llama-3.3-70b-versatile`). To change it, set
`LLM_MODEL=` in `keys.env` (or pass `--model`) to a model from https://console.groq.com/docs/models.

## Run
```bash
pip install -r requirements.txt

# command line
python3 run.py --input path/to/show.zip --out outputs/rules                 # free, ~0.1 s
python3 run.py --input path/to/show.zip --out outputs/llm --backend llm     # Groq key from keys.env
open outputs/rules/report.html

# web app: upload a .zip, pick rules or llm, download the fix list / flags / report
streamlit run app.py
```
`--input` takes a `.zip` or a folder of episode `.txt` files.

Outputs: `report.html`, `continuity_flags.json`, `fix_list.csv` (per-episode find/replace),
`rejected_llm_claims.json` (model claims dropped because their quotes were not in the script).

## Synthetic test (own inputs, planted errors)
`tests/synthetic_show/` is an original 8-episode story in Kuku's screenplay format (no Kuku text) with
6 planted errors and 2 traps, listed in `tests/synthetic_show/planted_errors.json`.
```bash
python3 eval_synthetic.py                  # rules -> outputs/synthetic_rules/
python3 eval_synthetic.py --backend llm    # llm   -> outputs/synthetic_llm/ (skipped if no key is set)
```
It prints found / missed / false alarm per planted item plus recall and precision, and writes
`eval_results.json` next to the raw outputs.

| file | what |
| --- | --- |
| `app.py` | Streamlit UI |
| `continuity/parser.py` | screenplay → episodes and timed beats (shared with Promo Finder) |
| `continuity/checks.py` | proper-noun mining, spelling clusters, speaker-label drift, quote verifier |
| `continuity/llm_checks.py` | LLM adjudication, discovery, fact contradictions; every quote verified |
| `continuity/llm.py` | OpenAI-compatible client (swap model with `--provider/--model/--base-url`) |
| `eval_synthetic.py` | scores a run against `planted_errors.json` |

The input pack is Kuku's content: `.gitignore` keeps `*.zip`, `data/` and `outputs/` out of the repo.
See `SOLUTION.md` for the problem, evidence and results.
