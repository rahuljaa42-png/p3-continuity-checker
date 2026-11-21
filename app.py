"""Streamlit UI: upload a show, get continuity flags, fact issues and a fix list.

streamlit run app.py
"""
import os
import tempfile
from pathlib import Path

import streamlit as st

try:  # on Streamlit Cloud the key lives in the app's Secrets, not in a file
    for _k in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass

from continuity.llm import PROVIDERS  # noqa: E402  (importing this also reads keys.env)
from run import run  # noqa: E402

st.set_page_config(page_title="Continuity Checker", layout="wide")
st.title("Continuity Checker: name, place and fact drift across episodes")
st.caption("Upload a .zip of episode screenplays (.txt). Every flag comes with episodes, beat IDs and "
           "exact quotes. In LLM mode, model claims whose quotes are not in the script are dropped.")

with st.sidebar:
    st.header("Settings")
    backend = st.radio("Backend", ["rules", "llm"],
                       help="rules = free and instant. llm = adjudicates flags, finds missed names and fact "
                            "contradictions; needs a key.")
    is_llm = backend == "llm"
    provider = st.selectbox("Provider", list(PROVIDERS), index=list(PROVIDERS).index("groq"), disabled=not is_llm)
    model = st.text_input("Model (blank = keys.env LLM_MODEL / provider default)", disabled=not is_llm)
    base_url = st.text_input("Custom OpenAI-compatible base URL (optional)", disabled=not is_llm)
    env_name = PROVIDERS[provider][1]
    api_key = ""
    if is_llm:
        if not env_name:
            st.info("This provider needs no key.")
        elif os.environ.get(env_name):
            st.success(f"Key loaded ({env_name})")
        else:
            st.warning(f"No {env_name} found in keys.env or app secrets.")
            api_key = st.text_input("API key (optional, used for this run only)", type="password")

up = st.file_uploader("Screenplay pack (.zip)", type=["zip"])
if up and st.button("Check continuity", type="primary"):
    with st.spinner("Checking…"), tempfile.TemporaryDirectory() as tmp:
        zp = Path(tmp) / "pack.zip"
        zp.write_bytes(up.getvalue())
        out = Path(tmp) / "out"
        try:
            meta, flags, facts, rejected = run(
                zp, out, backend, provider if is_llm else None, (model or None) if is_llm else None,
                api_key or None, (base_url or None) if is_llm else None)
        except Exception as e:  # show the real error to the user
            st.error(f"Run failed: {type(e).__name__}: {e}")
            st.stop()
        files = {n: (out / n).read_bytes() for n in
                 ("fix_list.csv", "continuity_flags.json", "report.html", "rejected_llm_claims.json")}
    st.session_state["res"] = (meta, [f.to_dict() for f in flags], facts, rejected, files)

res = st.session_state.get("res")
if res:
    meta, flags, facts, rejected, files = res
    c = st.columns(5)
    c[0].metric("Episodes", meta["episodes"])
    c[1].metric("Flags", len(flags))
    c[2].metric("Fact issues", len(facts))
    c[3].metric("LLM claims rejected", len(rejected))
    c[4].metric("Run time", f'{meta["seconds"]}s')
    if meta.get("model"):
        st.caption(f'Model: {meta["model"]} · API calls: {meta.get("api_calls", 0)}')

    d = st.columns(3)
    d[0].download_button("Download fix_list.csv", files["fix_list.csv"], "fix_list.csv", "text/csv")
    d[1].download_button("Download continuity_flags.json", files["continuity_flags.json"],
                         "continuity_flags.json", "application/json")
    d[2].download_button("Download report.html", files["report.html"], "report.html", "text/html")

    st.header("Flags")
    if not flags:
        st.info("No flags.")
    sev_icon = {"high": "🔴", "medium": "🟠", "low": "🔵"}
    for f in flags:
        with st.container(border=True):
            head = f'{sev_icon.get(f["severity"], "")} **{f["id"]}** · {f["kind"]} · severity **{f["severity"]}**'
            if f.get("verdict"):
                head += f' · LLM verdict **{f["verdict"]}**'
            st.markdown(head)
            st.markdown(f'Suggested canonical: **{f["canonical"]}**')
            st.table([{"variant": v, "count": f["variants"].get(v, 0),
                       "episodes": ", ".join(map(str, eps))}
                      for v, eps in sorted(f["episodes"].items(), key=lambda x: min(x[1]))])
            if f.get("note"):
                st.caption(f["note"])
            if f.get("llm_groups"):
                st.markdown("**LLM verdict groups**")
                for g in f["llm_groups"]:
                    st.markdown(f'- **{g.get("verdict", "")}**: {", ".join(g.get("variants", []))}'
                                f' ({g.get("entity", "")}). {g.get("why", "")}')
            with st.expander(f'Evidence ({len(f["evidence"])} quotes)'):
                for x in f["evidence"]:
                    st.markdown(f'`Ep {x["episode"]} · {x["beat"]} · {x["where"]}` …{x["quote"]}…')

    st.header("Fact issues")
    if not facts:
        st.info("No fact issues." + ("" if is_llm or meta["backend"] == "llm" else
                                     " (Fact checks run only with the llm backend.)"))
    for i in facts:
        with st.container(border=True):
            st.markdown(f'**{i.get("what", "")}**')
            st.markdown(f'Ep {i["a"]["episode"]}: “{i["a"]["quote"]}”')
            st.markdown(f'Ep {i["b"]["episode"]}: “{i["b"]["quote"]}”')
            if i.get("why"):
                st.caption(i["why"])

    if rejected:
        with st.expander(f"Rejected LLM claims ({len(rejected)})"):
            st.json(rejected)
