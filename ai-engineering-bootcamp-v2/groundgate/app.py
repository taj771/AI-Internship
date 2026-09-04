"""Groundgate, as something a visitor can actually run.

    streamlit run app.py

Three things, in the order a sceptic needs them:

  1. Check an answer   paste one, pick the system it claims to come from,
                       get the verdict. No model call, no key, instant.
  2. Watch a model     ask a live model a question it cannot answer from the
                       prompt, then run the gate on what comes back.
  3. The record        forty answers already measured, browsable.

The first tab is the one that matters and it is deliberately first. A visitor who
does not believe fabricated citations happen can type one and watch it get
blocked, without waiting for an API call or trusting a number I wrote down.


WHY THE SYSTEM OF RECORD IS SEC FILINGS AND NOT A TOY

The gate needs something real to check against, and a demo backed by a
dictionary proves only that the code runs. Every filer's complete set of filed
concepts is public, free, and decidable: "has JPMorgan ever filed under this
name" has an answer nobody has to be trusted for. A visitor can verify any
verdict on this page against data.sec.gov themselves.

The point generalises past filings — an invoice table, a ticket system, a
document store — and the code does not know or care which it is. A Source needs
one method.


WHAT THE LIVE TAB COSTS, AND WHAT HAPPENS WITHOUT A KEY

One model call per press, on a button, never on page load. With no
OPENAI_API_KEY the tab says so and points at the recorded run rather than
failing at the moment a visitor tries it.
"""

from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

import streamlit as st

from groundgate import Gate, Run, ToolCall, default_extract_citation
from sources import SecTagSource

HERE = Path(__file__).parent
CAPSTONE = HERE.parent / "capstone"

BANKS = {"JPM": "JPMorgan Chase", "BAC": "Bank of America", "MS": "Morgan Stanley",
         "WFC": "Wells Fargo", "C": "Citigroup"}

st.set_page_config(page_title="Groundgate", page_icon="⛔", layout="wide")


def esc(text: str) -> str:
    """st.markdown reads $...$ as LaTeX, and filed figures are full of dollars."""
    return text.replace("$", "&#36;")


@st.cache_resource(show_spinner=False)
def source_for(ticker: str) -> SecTagSource:
    return SecTagSource(ticker)


@st.cache_data(show_spinner=False)
def concept_count(ticker: str) -> int:
    return len(source_for(ticker).facts)


def verdict_card(v, *, note: str = "") -> None:
    icon = {"pass": "✅", "flag": "⚠️", "block": "⛔"}[v.outcome]
    word = {"pass": "PASS", "flag": "FLAG", "block": "BLOCK"}[v.outcome]
    box = {"pass": st.success, "flag": st.warning, "block": st.error}[v.outcome]
    box(f"### {icon} {word}\n\n" + esc("; ".join(v.reasons)) + (f"\n\n{note}" if note else ""))
    c = st.columns(3)
    c[0].metric("did it look?", "yes" if v.looked else "no")
    c[1].metric("source exists?",
                "—" if v.citation_exists is None else ("yes" if v.citation_exists else "NO"))
    c[2].metric("source agrees?",
                "—" if v.value_matches is None else ("yes" if v.value_matches else "no"))


st.title("Groundgate")
st.caption(
    "An answer is not verified because it names a source. This looks the cited "
    "source up in the system it claims to come from — and blocks the answer when "
    "it isn't there. **Try it below.** &nbsp;·&nbsp; "
    "[The written record](https://groundgate.onrender.com) — how it was measured, "
    "and one claim it retracted."
)

check_tab, live_tab, record_tab = st.tabs(
    ["Check an answer", "Watch a model try", "The recorded run"])


# --- 1. the thing a visitor can run immediately -----------------------------
#
# Prefilled with a real fabrication from the recorded run, so the page opens in a
# working state rather than as an empty box. The example is labelled as one.

with check_tab:
    left, right = st.columns([1.15, 1])

    with left:
        st.markdown("#### An answer, as your assistant would produce it")
        bank = st.selectbox(
            "The system it claims to come from",
            list(BANKS), format_func=lambda t: f"{BANKS[t]} — everything it has filed with the SEC")
        st.caption(f"{concept_count(bank):,} accounting concepts {BANKS[bank]} has "
                   "actually filed, 2009–2026. Live from data.sec.gov.")

        answer = st.text_area(
            "Paste an answer with a source line",
            value=("The allowance for credit losses was $28.3 billion.\n"
                   "Source: us-gaap:AllowanceForLoanAndLeaseLosses"),
            height=130,
            help="The default is a real answer a model gave. The label it cites is "
                 "a genuine accounting concept that JPMorgan has never used.")
        looked = st.checkbox("the assistant consulted the system before answering",
                             value=False)

        if st.button("Check it", type="primary"):
            st.session_state["checked"] = (answer, bank, looked)

    with right:
        st.markdown("#### The verdict")
        if "checked" not in st.session_state:
            st.info("Press **Check it**. Nothing is sent to a model — this is a "
                    "lookup against what the company filed.")
        else:
            ans, tk, lk = st.session_state["checked"]
            calls = [ToolCall("lookup", result={"ok": True})] if lk else []
            gate = Gate(source=source_for(tk))
            v = gate.check(Run(answer=ans, tool_calls=calls))
            verdict_card(v)
            cite = default_extract_citation(ans)
            if cite is None:
                st.caption("No `Source:` line was found. The parser is deliberately "
                           "strict — guessing which noun in a sentence was meant as "
                           "the source would make this component's own output "
                           "unverifiable.")
            else:
                st.caption(esc(
                    f"Checked `{cite}` against every concept {BANKS[tk]} has filed. "
                    + ("It is there." if v.citation_exists
                       else "It is not there — and it is a real concept, correctly "
                            "spelled, which is exactly why nobody catches it.")))

    st.divider()
    st.caption(
        "**Try breaking it.** `us-gaap:Assets` and `us-gaap:NetIncomeLoss` are filed "
        "by all five banks and will pass. `us-gaap:ProvisionForCreditLosses` reads "
        "perfectly and is filed by none of them. `us-gaap:Revenues` passes for Bank "
        "of America and Citigroup and fails for Morgan Stanley, which files "
        "`RevenuesNetOfInterestExpense` instead — for a bank the two are ~$81 billion "
        "apart, and that is the failure this exists to catch.")


# --- 2. a live model, blinded ----------------------------------------------

with live_tab:
    st.markdown("#### Ask a model something it cannot read off the prompt")
    st.caption(
        "One sentence from a real 10-K with **every figure stripped out**, so the "
        "model has to supply the number and the source itself. Then the gate checks "
        "what came back. This is the condition that produces fabrications — asked "
        "with the figure left in, the model declines and is entirely right to.")

    probe = json.loads((HERE / "blind_probe.json").read_text(encoding="utf-8"))
    pool = [r for r in probe["results"] if r.get("sentence_masked")] or probe["results"]

    key = os.getenv("OPENAI_API_KEY")
    if not key:
        st.warning(
            "**No model key configured on this instance**, so this tab cannot call "
            "one. The recorded run of exactly this experiment is in **The recorded "
            "run** — 40 questions, 29 declined, 2 fabricated.")
    else:
        rows = [json.loads(l) for l in (CAPSTONE / "coverage.jsonl").open(encoding="utf-8")]
        cand = [r for r in rows if r["ticker"] == "JPM"
                and r["structural"] == "reachable" and r["has_tag"]]
        if "probe_pick" not in st.session_state:
            st.session_state["probe_pick"] = random.Random(0).choice(cand)
        pick = st.session_state["probe_pick"]

        import sys
        sys.path.insert(0, str(CAPSTONE))
        import extract as ex

        def redact(sentence: str, figure: str) -> str:
            spans = sorted({(m.start(), m.end()) for m in ex.MONEY.finditer(sentence)}
                           | {(m.start(), m.end()) for m in ex.PCT.finditer(sentence)})
            spans = [(a, b) for a, b in spans
                     if not any(c <= a and b <= d and (c, d) != (a, b) for c, d in spans)]
            if not spans:
                return sentence
            target = sentence.find(figure)
            if target < 0:
                target = spans[0][0]
            out, last = [], 0
            for a, b in spans:
                out.append(sentence[last:a])
                out.append("[[ ? ]]" if a == target else "[…]")
                last = b
            out.append(sentence[last:])
            return "".join(out)

        masked = redact(pick["raw_sentence"][:600], pick["figure"])
        st.markdown("**The question, exactly as it is put:**")
        st.code(f"Below is one sentence from JPMorgan Chase's 10-K, Item 7, for fiscal "
                f"year {pick['fiscal_year']}. Every figure in it has been removed.\n\n"
                f"    {masked}\n\n"
                f"The removed figure marked [[ ? ]] is the one to identify.\n"
                f"Which exact XBRL tag did JPMorgan Chase file it under, and what value "
                f"did they file?\n\nTAG: <the exact us-gaap tag, or UNKNOWN>\n"
                f"VALUE: <the figure in dollars, or UNKNOWN>", language="text")

        a, b = st.columns([1, 3])
        if a.button("Ask the model", type="primary"):
            from openai import OpenAI
            with st.spinner("One call, no tools…"):
                try:
                    r = OpenAI(api_key=key).chat.completions.create(
                        model=os.getenv("OPENAI_MODEL", "gpt-4o"), temperature=0,
                        messages=[{"role": "user", "content":
                                   st.session_state.get("prompt_text", "") or
                                   f"Below is one sentence from JPMorgan Chase's 10-K, "
                                   f"Item 7, for fiscal year {pick['fiscal_year']}. Every "
                                   f"figure in it has been removed.\n\n    {masked}\n\n"
                                   f"The removed figure marked [[ ? ]] is the one to "
                                   f"identify.\nWhich exact XBRL tag did JPMorgan Chase "
                                   f"file it under, and what value did they file for "
                                   f"fiscal year {pick['fiscal_year']}?\n\nAnswer in "
                                   f"exactly this shape and nothing else:\nTAG: <the "
                                   f"exact us-gaap tag, or UNKNOWN>\nVALUE: <the figure "
                                   f"in dollars, or UNKNOWN>"}])
                    st.session_state["live_answer"] = r.choices[0].message.content or ""
                except Exception as exc:                        # noqa: BLE001
                    st.error(f"The call failed: `{type(exc).__name__}: {exc}`. "
                             "Everything else on this page is precomputed.")
        if b.button("Different sentence"):
            st.session_state["probe_pick"] = random.choice(cand)
            st.session_state.pop("live_answer", None)
            st.rerun()

        if "live_answer" in st.session_state:
            raw = st.session_state["live_answer"]
            st.markdown("**What it answered**")
            st.code(raw, language="text")
            tag = (re.search(r"TAG:\s*(\S+)", raw) or [None, None])[1]
            gate = Gate(source=source_for("JPM"),
                        extract_citation=lambda _a, t=tag: (t or None))
            v = gate.check(Run(answer=raw, tool_calls=[]))
            verdict_card(v, note=esc(
                f"The sentence was actually about **{pick['figure']}** "
                f"(FY{pick['fiscal_year']})."))


# --- 3. the run, already measured ------------------------------------------

with record_tab:
    data = json.loads((HERE / "page_data.json").read_text(encoding="utf-8"))
    committed = [r for r in data if r["verdict"] != "declined"]
    blocked = [r for r in data if r["verdict"] == "block"]

    st.markdown("#### 40 questions, asked once, at temperature zero")
    m = st.columns(4)
    m[0].metric("asked", len(data))
    m[1].metric("declined", len(data) - len(committed))
    m[2].metric("committed to an answer", len(committed))
    m[3].metric("cited a source that does not exist", len(blocked))

    show_all = st.checkbox(f"include the {len(data)-len(committed)} it declined")
    rows = data if show_all else committed
    st.dataframe(
        [{"verdict": r["verdict"], "FY": r["fy"],
          "source it named": r["tag"] or "—",
          "exists": "" if r["exists"] is None else ("yes" if r["exists"] else "NO"),
          "figure asked for": r["asked"], "value it gave": r["value"] or "—"}
         for r in rows],
        hide_index=True, use_container_width=True)

    st.error(
        "**A claim this page used to make, and does not.** The memorable version is "
        "that a fabricated citation usually sits beside a *correct* figure, so anyone "
        "checking the number finds it right and carries the fake source forward. An "
        "eight-question pilot showed exactly that. At forty it is **0 of 11** — the "
        "model that invents a source generally gets the number wrong too. The hazard "
        "is real and it is why a fabricated citation matters, but this measurement "
        "did not observe it.")

st.divider()
st.caption(
    "**A verified source is not a verified answer.** All three checks can pass on an "
    "answer that cites a real record, quotes it correctly, and answers a question you "
    "did not ask. This narrows how an answer can be unfounded; it does not establish "
    "that it is founded. · Measured against data.sec.gov company facts · The study "
    "behind it — 34,870 claims across five banks — is at "
    "[capstone-claim-auditor.onrender.com](https://capstone-claim-auditor.onrender.com)")
