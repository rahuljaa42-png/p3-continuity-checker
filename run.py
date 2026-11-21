"""Continuity check for a microdrama screenplay pack.

python run.py --input show.zip --out outputs/rules
python run.py --input show.zip --out outputs/gemini --backend llm --provider gemini
"""
import argparse
import csv
import html
import json
import time
from pathlib import Path

from continuity.checks import name_variant_flags, speaker_label_flags
from continuity.parser import load_show


def run(inp, out, backend="rules", provider=None, model=None, api_key=None, base_url=None):
    t0 = time.time()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    show, episodes = load_show(inp)
    flags = name_variant_flags(episodes) + speaker_label_flags(episodes)
    facts, rejected, meta = [], [], {"show": show, "episodes": len(episodes), "backend": backend}
    if backend == "llm":
        from continuity import llm_checks as L
        from continuity.llm import LLM
        llm = LLM(provider, model, api_key, base_url, cache_dir=out / ".cache", log_path=out / "llm_raw_log.jsonl")
        meta["model"] = llm.name()
        L.adjudicate(llm, flags, episodes, rejected)
        flags += L.discover(llm, flags, episodes, rejected)
        facts = L.facts(llm, episodes, rejected)
        meta["api_calls"] = llm.calls
        meta["cached_calls"] = llm.cached_calls
    for i, f in enumerate(flags, 1):
        f.id = f"F{i:02d}"
    meta["seconds"] = round(time.time() - t0, 1)
    fixes = fix_list(flags)
    (out / "continuity_flags.json").write_text(json.dumps(
        {"meta": meta, "flags": [f.to_dict() for f in flags], "fact_issues": facts}, indent=2, ensure_ascii=False))
    (out / "rejected_llm_claims.json").write_text(json.dumps(rejected, indent=2, ensure_ascii=False))
    with (out / "fix_list.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["flag", "episode", "find", "replace_with", "status"])
        w.writerows(fixes)
    (out / "report.html").write_text(render(meta, flags, facts, rejected), encoding="utf-8")
    return meta, flags, facts, rejected


def fix_list(flags):
    """Per-episode find/replace rows. Only LLM/editor-confirmed errors are 'ready'."""
    rows = []
    for f in flags:
        groups = f.llm_groups or [{"variants": list(f.variants), "verdict": "unreviewed", "canonical": f.canonical}]
        for g in groups:
            if g.get("verdict") not in ("error", "unreviewed") or len(g["variants"]) < 2:
                continue
            can = g.get("canonical") if g.get("canonical") in g["variants"] else max(g["variants"], key=f.variants.get)
            for v in g["variants"]:
                if v == can:
                    continue
                for ep in f.episodes.get(v, []):
                    rows.append([f.id, ep, v, can, "ready" if g["verdict"] == "error" else "needs review"])
    return sorted(rows, key=lambda r: (r[1], r[0]))


def render(meta, flags, facts, rejected):
    e = html.escape
    parts = []
    for f in flags:
        timeline = " → ".join(f"<b>{e(v)}</b> (Ep {', '.join(map(str, eps))})"
                              for v, eps in sorted(f.episodes.items(), key=lambda x: min(x[1])))
        ev = "".join(f"<li><span class=m>Ep {x['episode']} · {e(x['beat'])} · {e(x['where'])}</span> "
                     f"…{e(x['quote'])}…</li>" for x in f.evidence)
        groups = "".join(f"<li><b>{e(g.get('verdict', ''))}</b>: {e(', '.join(g['variants']))} — "
                         f"{e(str(g.get('entity', '')))}. {e(str(g.get('why', '')))}</li>" for g in f.llm_groups)
        parts.append(f"""<section class='card {f.severity}'><h2>{f.id} · {e(f.kind)} · severity {f.severity}
{(' · verdict <b>' + e(f.verdict) + '</b>') if f.verdict else ''}</h2>
<p>Suggested canonical: <b>{e(f.canonical)}</b></p><p>{timeline}</p><p class=m>{e(f.note)}</p>
{('<ul>' + groups + '</ul>') if groups else ''}<details><summary>Evidence ({len(f.evidence)})</summary><ul>{ev}</ul></details></section>""")
    fx = "".join(f"<section class=card><h2>Fact issue: {e(str(i.get('what', '')))}</h2>"
                 f"<p>Ep {i['a']['episode']}: “{e(i['a']['quote'])}”</p><p>Ep {i['b']['episode']}: “{e(i['b']['quote'])}”</p>"
                 f"<p class=m>{e(str(i.get('why', '')))}</p></section>" for i in facts)
    return f"""<!DOCTYPE html><html><head><meta charset=utf-8><title>Continuity – {e(meta['show'])}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px}}
.card{{border:1px solid #ddd;border-left:6px solid #bbb;border-radius:8px;padding:10px 16px;margin:12px 0}}
.high{{border-left-color:#d33}}.medium{{border-left-color:#e90}}.low{{border-left-color:#59a}}
h2{{font-size:16px;margin:0}}.m{{color:#777;font-size:13px}}li{{margin:4px 0;font-size:14px}}</style></head>
<body><h1>Continuity report — {e(meta['show'])}</h1><p class=m>{meta['episodes']} episodes · backend {e(meta['backend'])}
{(' · ' + e(meta['model'])) if meta.get('model') else ''} · {len(flags)} flags · {len(facts)} fact issues ·
{len(rejected)} LLM claims rejected for unverifiable quotes · {meta['seconds']}s</p>{''.join(parts)}{fx}</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", default="outputs/latest")
    ap.add_argument("--backend", choices=["rules", "llm"], default="rules")
    ap.add_argument("--provider", default=None, help="default: groq (keys.env)")
    ap.add_argument("--model")
    ap.add_argument("--api-key")
    ap.add_argument("--base-url")
    a = ap.parse_args()
    meta, flags, facts, rej = run(a.input, a.out, a.backend, a.provider, a.model, a.api_key, a.base_url)
    print(json.dumps(meta, indent=2))
    for f in flags:
        print(f"{f.id} [{f.severity}] {f.kind}: {f.canonical} <- {f.variants} {f.verdict}")
    print(f"fact issues: {len(facts)}, rejected LLM claims: {len(rej)}")


def _under_streamlit() -> bool:
    try:
        from streamlit.runtime import exists
        return exists()
    except Exception:
        return False


if __name__ == "__main__":
    if _under_streamlit():   # deployed with run.py as the main file: show the web app instead of the CLI
        import runpy
        runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
    else:
        main()
