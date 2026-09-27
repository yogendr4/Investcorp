"""Local demo/evaluation UI. Launch:  streamlit run app/app.py  (from the project root).

Two tabs:
  Chat        talks to the frozen V2.1 service; shows the answer plus a deterministic execution trace
              (entity resolution / routing / evidence / validation / timing), never the model's hidden reasoning
  Evaluation  reads the already-produced docs/evaluation/*_results.json files; runs nothing

This file only calls the existing services and renders what they already return. It contains no pipeline logic.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.evaluation import HOLDOUT_LABEL, VERSION_LABEL, load_holdout, question as eval_question, semantic_observations, validation_failures, visible_dashboard
from app.trace import build_trace

st.set_page_config(page_title="Investcorp Baseline/V1/V2/V2.1 demo", layout="wide")


@st.cache_resource(show_spinner=False)
def _service():
    from src.v2.v21 import V21Service               # the default interactive backend, as approved
    return V21Service()


def render_answer(resp: dict) -> None:
    status = resp["status"]
    if resp.get("answer"):
        st.markdown(resp["answer"])
    else:
        st.info(resp.get("message") or "(no answer)")
    badges = f"**status:** `{status}`  ·  **route:** `{resp.get('route')}`"
    if resp.get("qualified"):
        badges += "  ·  qualified"
    st.caption(badges)
    refs = []
    se, ae, me = resp.get("structured_evidence"), resp.get("meeting_aggregate_evidence"), resp.get("meeting_evidence")
    if se:
        refs += [f"[{s}]" for s in (se.get("source_tables") or [])]
    if ae:
        refs.append("[meetings]")
    if me:
        refs += [f"[meeting: {h['meeting_id']}, {h['meeting_date']}]" for h in me.get("hits", [])[:6]]
    if refs:
        st.caption("Evidence/source references: " + ", ".join(dict.fromkeys(refs)))


_STATUS_ICON = {"ok": "✅", "ambiguous": "⚠️", "not_found": "⚠️", "invalid": "❌", "error": "❌", "skipped": "⏭️", "unsupported": "⏭️"}


def render_trace(resp: dict) -> None:
    events = build_trace(resp).to_list()
    st.subheader("Execution trace")
    st.caption("Deterministic pipeline steps the frozen service already took. This is not the model's reasoning: only Claude's final answer text is ever shown.")
    for e in events:
        icon = _STATUS_ICON.get(e["status"], "•")
        with st.expander(f"{icon} {e['name']} — {e['status']}" + (f" ({e['duration_ms']:.0f} ms)" if e["duration_ms"] else ""), expanded=False):
            meta = {k: v for k, v in e["metadata"].items() if k not in ("hits", "mentions")}
            if meta:
                st.json(meta, expanded=False)
            if e["name"] == "Entity Resolution":
                for m in e["metadata"].get("mentions", []):
                    st.write(f"- `{m['text']}` → **{m['entity_type']}**, {m['status']}" + (f" (`{m['canonical']}`)" if m.get("canonical") else ""))
            if e["name"] == "Meeting Evidence":
                hits = e["metadata"].get("hits", [])
                if hits:
                    st.dataframe([{"meeting_id": h["meeting_id"], "date": h["date"], "rank": h["rank"], "source": h["source"],
                                  "semantic_score": h["semantic_score"], "snippet": h["snippet"]} for h in hits], use_container_width=True, hide_index=True)
            if e["evidence_refs"]:
                st.caption("Evidence: " + ", ".join(e["evidence_refs"]))

    total = (resp.get("timing_ms") or {}).get("total_ms")
    if total is not None:
        st.caption(f"Total latency: {total:.0f} ms")


def render_technical_details(resp: dict) -> None:
    with st.expander("Technical details (collapsed by default)", expanded=False):
        se, ae, me = resp.get("structured_evidence"), resp.get("meeting_aggregate_evidence"), resp.get("meeting_evidence")
        if se and se.get("sql"):
            st.caption("Generated SQL (structured)")
            st.code(se["sql"], language="sql")
        if ae and ae.get("sql"):
            st.caption("Generated SQL (meeting aggregate)")
            st.code(ae["sql"], language="sql")
        if me:
            st.caption(f"Retrieval ranks/scores ({me.get('rank_method')}, {len(me.get('hits', []))} hits)")
            st.dataframe([{"meeting_id": h["meeting_id"], "date": h["meeting_date"], "fused_rank": h.get("rank"), "lexical_rank": h.get("lexical_rank"),
                          "semantic_rank": h.get("semantic_rank"), "semantic_score": h.get("semantic_score")} for h in me.get("hits", [])], use_container_width=True, hide_index=True)
            sel = me.get("selection")
            if sel:
                st.caption("Evidence selection (V2.1): candidates kept as evidence vs. filtered out")
                st.json({k: v for k, v in sel.items() if k != "rule"}, expanded=False)
        val = resp.get("validation")
        if val:
            st.caption("Validation")
            st.json(val, expanded=False)


def chat_tab() -> None:
    st.header("Chat — V2.1")
    st.caption("Answers are grounded only in the returned evidence. No hidden model reasoning is shown; only the final answer text and the deterministic pipeline trace.")
    q = st.text_input("Ask a question about investments, performance or meetings", key="chat_q")
    go = st.button("Ask", type="primary")
    if go and q.strip():
        with st.spinner("Running the V2.1 pipeline…"):
            t0 = time.monotonic()
            resp = _service().answer_question(q).to_dict()
            wall_ms = round((time.monotonic() - t0) * 1000, 1)
        st.session_state["last_response"] = resp
        st.session_state["last_wall_ms"] = wall_ms
    resp = st.session_state.get("last_response")
    if resp:
        st.divider()
        render_answer(resp)
        render_trace(resp)
        render_technical_details(resp)


def evaluation_tab() -> None:
    st.header("Evaluation")
    dash = visible_dashboard()
    if not dash:
        st.warning("No evaluation result files found under docs/evaluation/.")
        return

    st.subheader("Version comparison (visible benchmark, 40 questions)")
    st.dataframe([{"Version": VERSION_LABEL[v], "Correct": d["correct"], "Incorrect": d["incorrect"], "Needs review": d["needs_review"],
                  "Mean latency": f"{d['mean_latency_ms']/1000:.1f} s" if d["mean_latency_ms"] else "-"} for v, d in dash.items()], use_container_width=True, hide_index=True)

    st.subheader("Results by category")
    cats = sorted({c for d in dash.values() for c in d["by_category"]})
    st.dataframe([{"Category": cat, **{VERSION_LABEL[v]: f"{d['by_category'].get(cat, {}).get('correct', 0)}/{d['by_category'].get(cat, {}).get('total', 0)}" for v, d in dash.items()}} for cat in cats],
                 use_container_width=True, hide_index=True)

    st.subheader("Failure-mode distribution")
    modes = sorted({m for d in dash.values() for m in d["failure_modes"]})
    st.dataframe([{"Failure mode": m, **{VERSION_LABEL[v]: d["failure_modes"].get(m, 0) for v, d in dash.items()}} for m in modes], use_container_width=True, hide_index=True)

    st.subheader("Semantic retrieval observations")
    sem = semantic_observations()
    v_pick = st.selectbox("Version", [v for v in ("v2", "v21") if v in sem], format_func=lambda v: VERSION_LABEL[v], key="sem_v")
    for s in sem.get(v_pick, []):
        with st.expander(f"{s['id']}"):
            st.json(s, expanded=False)

    st.subheader("Validation failures")
    v_pick2 = st.selectbox("Version", list(dash), format_func=lambda v: VERSION_LABEL[v], key="val_v")
    vf = validation_failures(v_pick2)
    st.dataframe(vf, use_container_width=True, hide_index=True) if vf else st.caption("None.")

    st.subheader("Question drill-down (visible benchmark only)")
    col1, col2 = st.columns(2)
    v_pick3 = col1.selectbox("Version", list(dash), format_func=lambda v: VERSION_LABEL[v], key="drill_v")
    from app.evaluation import load_visible
    all_ids = [x["id"] for x in load_visible()[v_pick3]["results"]]
    qid = col2.selectbox("Question", all_ids, key="drill_q")
    x = eval_question(v_pick3, qid)
    if x:
        st.write(f"**Question:** {x['question']}")
        st.write(f"**Version:** {VERSION_LABEL[v_pick3]}  ·  **Status:** `{x['status']}`  ·  **Route:** `{x['route']}` (expected: `{x.get('expected_route')}`)  ·  **Result:** {x['result']}")
        st.write(f"**Answer / message:** {x.get('answer') or x.get('message') or '(none)'}")
        exp = x.get("scoring") or {}
        if exp:
            st.caption("Expected result / scoring method")
            st.json(exp, expanded=False)
        with st.expander("Retrieved evidence"):
            st.json({"structured_evidence": x.get("structured_evidence"), "meeting_aggregate_evidence": x.get("meeting_aggregate_evidence"), "meeting_evidence": x.get("meeting_evidence")}, expanded=False)
        st.caption("Validation")
        st.json(x.get("validation"), expanded=False)
        st.write(f"**Failure mode:** {x['failure_mode']['primary']}" + (f" — {x['failure_mode'].get('explanation')}" if x["failure_mode"].get("explanation") else ""))

    holdout = load_holdout()
    if holdout:
        st.divider()
        st.subheader(HOLDOUT_LABEL)
        st.caption("This was NOT a blind evaluation: the holdout questions were read before this run. Aggregate metrics only — no per-question answers are shown here.")
        m = holdout["metrics"]
        st.dataframe([{"Version": VERSION_LABEL[v], "Correct": m[v]["correct"], "Incorrect": m[v]["incorrect"], "Needs review": m[v]["needs_review"],
                      "Mean latency": f"{m[v]['latency_ms']['mean']/1000:.1f} s"} for v in m], use_container_width=True, hide_index=True)


def main() -> None:
    st.title("Investcorp Baseline → V1 → V2 → V2.1 demo")
    tab_chat, tab_eval = st.tabs(["Chat", "Evaluation"])
    with tab_chat:
        chat_tab()
    with tab_eval:
        evaluation_tab()


if __name__ == "__main__":
    main()
