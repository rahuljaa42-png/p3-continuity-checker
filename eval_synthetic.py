"""Score the checker against the synthetic show with planted errors.

python3 eval_synthetic.py                      # rules  -> outputs/synthetic_rules/
python3 eval_synthetic.py --backend llm        # llm    -> outputs/synthetic_llm/ (skipped if no key)

Per planted item: found / missed for real errors; false alarm / correct for the two traps
(a deliberate alias and two different people with similar names). Any flag or fact issue that
matches no planted item is an unplanned false alarm; a fact issue that only restates a planted
name/place error is a duplicate (not counted either way).
recall = found / errors, precision = found / (found + false alarms).
"""
import argparse
import json
import os
import sys
from pathlib import Path

from run import run

SHOW = Path(__file__).parent / "tests" / "synthetic_show"


def _names(item):
    return set(item.get("names") or [item.get("canonical"), item.get("variant")]) - {None}


def _flag_judgement(flag, names):
    """For a flag that contains all `names`: did the run call them one entity?"""
    groups = flag.get("llm_groups") or []
    if not groups:                       # rules only, or LLM step failed: the flag itself is the claim
        return "error", flag.get("verdict") or "unreviewed"
    for g in groups:
        if names <= set(g.get("variants", [])):
            return ("error" if g.get("verdict") == "error" else "cleared"), g.get("verdict", "")
    return "cleared", "split into separate groups"


def score(flags, facts, items, backend):
    rows, used_flags, used_facts = [], set(), set()
    for it in items:
        names = _names(it)
        if it["type"] == "prop_contradiction":
            hits = [i for i, f in enumerate(facts)
                    if any(k in json.dumps(f).lower() for k in it["keywords"])
                    and set(it["episodes"]) <= {_ep(f.get("a")), _ep(f.get("b"))}]
            used_facts.update(hits)
            status = "found" if hits else "missed"
            why = f"fact issue #{hits[0] + 1}" if hits else (
                "fact checks run only in llm mode" if backend == "rules" else "no matching fact issue")
        else:
            kinds = ("speaker_label",) if it["type"] == "speaker_label" else ("name_variant", "name_variant_llm")
            cand = [f for f in flags if f["kind"] in kinds and names <= set(f["variants"])]
            used_flags.update(f["id"] for f in cand)
            calls = [(f, *_flag_judgement(f, names)) for f in cand]
            said_error = [c for c in calls if c[1] == "error"]
            if it["is_error"]:
                status = "found" if said_error else "missed"
                why = (f"{said_error[0][0]['id']} ({said_error[0][2]})" if said_error else
                       f"{cand[0]['id']} cleared as {calls[0][2]}" if cand else "no flag")
            else:
                status = "false alarm" if said_error else "correct"
                why = (f"{said_error[0][0]['id']} treats them as one entity ({said_error[0][2]})" if said_error else
                       f"{cand[0]['id']} flagged, cleared as {calls[0][2]}" if cand else "not flagged")
        rows.append({"id": it["id"], "type": it["type"], "is_error": it["is_error"],
                     "planted": it.get("detail") or f"{it.get('variant')} for {it.get('canonical')} "
                                                     f"(Ep {', '.join(map(str, it['episodes']))})",
                     "status": status, "evidence": why})
    for f in flags:   # unplanned flags: anything not covering a whole planted item
        if f["id"] not in used_flags:
            j, v = _flag_judgement(f, set(f["variants"]))
            if j == "error":
                rows.append({"id": f["id"], "type": "unplanned_flag", "is_error": False,
                             "planted": "-", "status": "false alarm",
                             "evidence": f"{f['kind']}: {', '.join(f['variants'])} ({v})"})
    variants = {i["variant"]: i["id"] for i in items if i["is_error"] and i.get("variant")}
    for i, f in enumerate(facts):
        if i not in used_facts:
            dup = [pid for v, pid in variants.items() if v in json.dumps(f, ensure_ascii=False)]
            if dup:   # restates a planted name/place error as a "fact": neither a new find nor wrong
                rows.append({"id": f"fact#{i + 1}", "type": "duplicate_fact", "is_error": True, "planted": "-",
                             "status": "duplicate", "evidence": f"same as {', '.join(dup)}: {f.get('what', '')}"})
                continue
            rows.append({"id": f"fact#{i + 1}", "type": "unplanned_fact", "is_error": False, "planted": "-",
                         "status": "false alarm", "evidence": str(f.get("what", ""))[:120]})
    found = sum(r["status"] == "found" for r in rows)
    errors = sum(1 for i in items if i["is_error"])
    fa = sum(r["status"] == "false alarm" for r in rows)
    return rows, {"errors_planted": errors, "found": found, "missed": errors - found, "false_alarms": fa,
                  "recall": round(found / errors, 3) if errors else None,
                  "precision": round(found / (found + fa), 3) if found + fa else None}


def _ep(x):
    try:
        return int((x or {}).get("episode"))
    except (TypeError, ValueError):
        return -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["rules", "llm"], default="rules")
    ap.add_argument("--out")
    ap.add_argument("--provider")
    ap.add_argument("--model")
    a = ap.parse_args()
    out = Path(a.out or f"outputs/synthetic_{a.backend}")
    if a.backend == "llm":
        from continuity.llm import DEFAULT_PROVIDER, PROVIDERS
        env = PROVIDERS.get(a.provider or DEFAULT_PROVIDER, ("", "", ""))[1]
        if env and not os.environ.get(env):
            print(f"Skipped: no {env} in keys.env or the environment, so the llm run was not started.")
            return 0
    meta, flags, facts, rejected = run(SHOW, out, a.backend, a.provider, a.model)
    if a.backend == "llm" and not (meta.get("api_calls", 0) + meta.get("cached_calls", 0)):
        reasons = sorted({str(r.get("reason", ""))[:200] for r in rejected})
        print("LLM run FAILED: no LLM call succeeded, so these would just be rules results. Not scoring.")
        print("\n".join(reasons))
        return 1
    items = json.loads((SHOW / "planted_errors.json").read_text())["items"]
    rows, summary = score([f.to_dict() for f in flags], facts, items, a.backend)
    summary = {"backend": a.backend, "model": meta.get("model"), "flags": len(flags),
               "fact_issues": len(facts), "rejected_llm_claims": len(rejected), **summary}
    (out / "eval_results.json").write_text(json.dumps({"summary": summary, "items": rows}, indent=2))
    w = max(len(r["planted"]) for r in rows)
    print(f"{'id':8} {'type':20} {'planted':{w}}  status       evidence")
    for r in rows:
        print(f"{r['id']:8} {r['type']:20} {r['planted']:{w}}  {r['status']:12} {r['evidence']}")
    print(json.dumps(summary, indent=2))
    print(f"raw outputs: {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
