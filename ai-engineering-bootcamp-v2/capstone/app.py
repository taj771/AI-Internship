"""The Calibrated Claim Auditor. Phase 5 of the build plan.

    .venv/bin/streamlit run app.py

Two things, and the split between them is the architecture.

FILING REPORTS are precomputed. Auditing a whole 10-K is a hundred-odd agent
runs against a rate-limited API — three minutes of spinner and a bill on every
page load, for an answer that does not change between visits. So the batch runs
offline into traces.jsonl and this page reads it. A visitor sees the report
immediately.

CHECK A CLAIM is live, one claim at a time, because that is the case where the
input is new and there is nothing to precompute.

The abstention layer is on the front of both. Right now it auto-accepts nothing,
and the page says so plainly rather than presenting verdicts as findings. A tool
that cannot say which of its answers to trust has not earned the reader's time,
and hiding that behind a confident layout would make it worse, not better.
"""

import asyncio
import json
import os
import random
import re
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path

import streamlit as st

import report as report_mod
from diagram import gate_svg

st.set_page_config(page_title="Groundgate", page_icon="⛔", layout="wide")


def svg(markup: str) -> str:
    """Hand an SVG to Streamlit without markdown eating it.

    st.markdown treats $...$ as LaTeX, and every diagram on this page quotes
    dollar figures. A picture containing "$51.6 billion" and "$1.6 billion"
    therefore had everything between the two swallowed and re-rendered as an
    equation, collapsing the whole drawing into raw text on the deployed page
    while rendering correctly in every local check.

    Escaping at the one point where SVG reaches the page, rather than in each
    diagram, means the next drawing cannot reintroduce it.
    """
    return markup.replace("$", "&#36;")


st.title("Groundgate")
st.caption(
    "An answer is not verified because it names a source. This looks the cited "
    "source up in the system it claims to come from, and blocks the answer when "
    "it isn't there. **Try it in *Is the source real?*** &nbsp;·&nbsp; "
    "Built out of a study of 34,870 numeric claims in bank filings, which is the "
    "evidence in the last four tabs."
)

# The written record lives off-page.
#
# groundgate.onrender.com carries the measurement in full, including a claim it
# retracted, and is the thing to attach to something rather than send someone
# to press buttons on.
st.markdown(
    '<div style="font-size:13.5px;line-height:1.5;padding:9px 14px;margin:2px 0 6px;'
    'border:1px solid rgba(128,128,128,.28);border-left:3px solid #14526b;'
    'border-radius:3px">The written record — how this was measured, and one claim '
    'it retracted — is at '
    '<a href="https://groundgate.onrender.com" target="_blank"><b>groundgate.onrender.com</b></a>.'
    '</div>', unsafe_allow_html=True)

# Order is the argument: the problem, the method, the method running on one
# filing, the same method browsable one concept at a time, then how much of any
# filing can be confirmed at all, and finally how the confirmed figures move
# between filings. Coverage before temporal — the scale of the gap has to land
# before its behaviour over time means anything.
# Order is the argument, and the argument changed.
#
# It used to open on a study of bank filings and end with the one component that
# turned out to be general. Now it is only that component: the problem, the thing
# built to catch it, and the thing itself.
#
# The filings study is not deleted, it is off the page. Its four tabs — the
# per-filing report, prose against filed figures, how much of a filing is
# checkable, and how filed figures move — were the evidence for a claim this page
# no longer leads with, and every one of them made a visitor read about banks
# before reaching the point. coverage.py, consistency.py, browse.py, their data
# and their measurements are untouched in this directory, the numbers are in the
# deck, and the written record at groundgate.onrender.com carries the summary.
fail_tab, built_tab, gate_tab = st.tabs(
    ["Three ways it fails", "What we built", "Is the source real?"])


# --- where models fail ------------------------------------------------------
#
# The problem, before any solution. Rows one and two of the stage-4 grid live
# here; the third row — what fixes it — is held back for "How it works", so a
# reader meets the difficulty before the answer.
#
# A decline is presented as correct behaviour throughout, because it is. The
# failure worth showing is a confident wrong answer, and there are four of them.

with fail_tab:
    st.markdown("### Three ways a model gets a cited figure wrong")
    st.markdown(
        "Not one failure but three, and they are not equally fixable. Each "
        "example below is a real answer from a real run, and each figure is "
        "checkable against data.sec.gov by anyone who doubts it.")

    # --- 1 ---------------------------------------------------------------
    st.divider()
    st.markdown("#### 1 · The number is wrong")
    st.caption(
        "Run it. A real sentence from a real 10-K with **every figure stripped "
        "out**, so the model has to supply the number rather than read it back — "
        "then compare what it says against what the sentence said.")

    _cov = [json.loads(l) for l in (report_mod.HERE / "coverage.jsonl").open(encoding="utf-8")]
    _pool = [r for r in _cov if r["ticker"] == "JPM" and r["structural"] == "reachable"
             and r["has_tag"] and 60 < len(r["raw_sentence"]) < 240]

    def _blind(sentence, figure):
        import extract as _ex
        spans = sorted({(m.start(), m.end()) for m in _ex.MONEY.finditer(sentence)}
                       | {(m.start(), m.end()) for m in _ex.PCT.finditer(sentence)})
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
            out.append("[[ ? ]]" if a == target else "[...]")
            last = b
        out.append(sentence[last:])
        return "".join(out)

    if "f1_claim" not in st.session_state and _pool:
        st.session_state["f1_claim"] = random.Random(3).choice(_pool)
    claim1 = st.session_state.get("f1_claim")

    if claim1:
        masked = _blind(claim1["raw_sentence"], claim1["figure"])
        st.markdown("**The question, with the answer taken out of it**")
        st.code(f"JPMorgan Chase, Item 7, fiscal year {claim1['fiscal_year']}:\n\n"
                f"  {masked}\n\n"
                f"What figure belongs at [[ ? ]], and under which us-gaap concept "
                f"did they file it?", language="text")

        r1, r2 = st.columns([1, 3])
        go1 = r1.button("Ask the model", type="primary", key="f1_go")
        if r2.button("Another sentence", key="f1_next"):
            st.session_state["f1_claim"] = random.choice(_pool)
            st.session_state.pop("f1_answer", None)
            st.rerun()

        if go1:
            _key = os.getenv("OPENAI_API_KEY")
            if not _key:
                st.warning(
                    "No model key on this instance, so this cannot call one. The "
                    "recorded run of exactly this experiment is at "
                    "[groundgate.onrender.com](https://groundgate.onrender.com) — "
                    "40 questions, the figure right once in eleven.")
            else:
                from openai import OpenAI
                with st.spinner("One call, no tools..."):
                    try:
                        resp = OpenAI(api_key=_key).chat.completions.create(
                            model=os.getenv("OPENAI_MODEL", "gpt-4o"), temperature=0,
                            messages=[{"role": "user", "content":
                                f"Below is one sentence from JPMorgan Chase's 10-K, "
                                f"Item 7, for fiscal year {claim1['fiscal_year']}. "
                                f"Every figure has been removed.\n\n  {masked}\n\n"
                                f"The removed figure marked [[ ? ]] is the one to "
                                f"identify. Which exact XBRL tag did JPMorgan Chase "
                                f"file it under, and what value did they file?\n\n"
                                f"Answer in exactly this shape and nothing else:\n"
                                f"TAG: <the exact us-gaap tag, or UNKNOWN>\n"
                                f"VALUE: <the figure in dollars, or UNKNOWN>"}])
                        st.session_state["f1_answer"] = resp.choices[0].message.content or ""
                    except Exception as exc:                      # noqa: BLE001
                        st.error(f"The call failed: `{type(exc).__name__}: {exc}`.")

        if "f1_answer" in st.session_state:
            raw = st.session_state["f1_answer"]
            g1, g2 = st.columns(2)
            with g1:
                st.markdown("**What it answered**")
                st.code(raw[:240], language="text")
            with g2:
                st.markdown("**What the sentence actually said**")
                st.code(f"the figure at [[ ? ]] is  {claim1['figure']}", language="text")
                import prepare_evidence as _pe
                truth = _pe.parse_claimed(claim1["figure"])
                m = re.search(r"VALUE:\s*([^\n]+)", raw)
                got = None
                if m:
                    t = m.group(1).replace(",", "").strip().lstrip("$")
                    t = t.replace("(", "-").replace(")", "")
                    try:
                        got = float(t)
                    except ValueError:
                        got = _pe.parse_claimed(m.group(1))
                if got is None or truth is None:
                    st.info("It gave no figure — the correct answer, and still "
                            "useless: you have to go and look.")
                elif abs(got - truth) / abs(truth) <= 0.015:
                    st.success("Right this time. It does happen — once in eleven "
                               "across the recorded run.")
                else:
                    st.error(svg(
                        f"Off by **{abs(got - truth) / abs(truth):,.0%}**. It "
                        f"answered ${got/1e9:,.2f}B against the {claim1['figure']} "
                        f"the sentence states."))

    st.markdown(
        "**Why this one is hard to catch.** A model that answers the *firmwide* "
        "figure for a *division's* sentence has named a real concept and returned "
        "a real number — nothing about the answer looks wrong. Catching it needs "
        "the true value, which needs the right concept **and** the right scope, "
        "and the SEC's public API will not return segment figures at all.")

    st.divider()
    st.markdown("#### 2 · Two sources, two numbers, both filed")
    st.caption(
        "Fetched from data.sec.gov when you press the button — one request for "
        "one concept, and every annual value the filer has ever published for it.")

    CIKS = {"JPM": "0000019617", "BAC": "0000070858", "MS": "0000895421",
            "WFC": "0000072971", "C": "0000831001"}
    WATCHABLE = ["NetCashProvidedByUsedInOperatingActivities",
                 "NetCashProvidedByUsedInFinancingActivities",
                 "InvestmentBankingRevenue", "NoninterestExpense",
                 "InterestIncomeExpenseNet", "Assets", "Deposits"]

    q1, q2, q3 = st.columns([1, 2.2, 1])
    f2_bank = q1.selectbox("Filer", list(CIKS), key="f2_bank")
    f2_tag = q2.selectbox("Concept", WATCHABLE, key="f2_tag")
    q3.markdown("&nbsp;")
    if q3.button("Look it up", type="primary", key="f2_go"):
        import requests as _rq
        url = (f"https://data.sec.gov/api/xbrl/companyconcept/"
               f"CIK{CIKS[f2_bank]}/us-gaap/{f2_tag}.json")
        with st.spinner("data.sec.gov..."):
            try:
                resp = _rq.get(url, timeout=45, headers={
                    "User-Agent": os.getenv("SEC_USER_AGENT", "capstone reader"),
                    "Accept-Encoding": "gzip, deflate"})
                st.session_state["f2_data"] = (
                    resp.json() if resp.status_code == 200 else None,
                    resp.status_code, url, f2_bank, f2_tag)
            except Exception as exc:                              # noqa: BLE001
                st.session_state["f2_data"] = (None, type(exc).__name__, url,
                                               f2_bank, f2_tag)

    if "f2_data" in st.session_state:
        data, status, url, bank_used, tag_used = st.session_state["f2_data"]
        st.caption(f"`GET .../CIK{CIKS[bank_used]}/us-gaap/{tag_used}.json` -> {status}")
        if not data:
            st.info(f"{bank_used} does not file `{tag_used}`, so the SEC has nothing "
                    "to return. Which is failure 3 in miniature: a perfectly "
                    "plausible concept that this filer has never used.")
        else:
            years = defaultdict(set)
            for e in data.get("units", {}).get("USD", []):
                start, end = e.get("start"), e.get("end")
                if not end:
                    continue
                if start:
                    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
                    if not 350 <= days <= 380:
                        continue
                elif e.get("fp") != "FY":
                    continue
                years[int(end[:4])].add(e["val"])
            moved = {y: sorted(v) for y, v in years.items() if len(v) > 1}
            if not moved:
                st.success(svg(
                    f"**{bank_used} · {tag_used}** — one filed value in every year "
                    "the SEC returned. Nothing restated. Not every concept moves, "
                    "which is why this is worth checking rather than assuming."))
            else:
                st.warning(svg(
                    f"**{len(moved)} fiscal year(s) carry more than one filed "
                    "value.** Every annual report republishes prior years, and a "
                    "reclassification moves them."))
                st.dataframe(
                    [{"fiscal year": y,
                      "values the SEC returns": "   and   ".join(
                          f"${v/1e9:,.2f}B" for v in vals),
                      "apart by": f"{abs(vals[-1]-vals[0])/max(abs(vals[0]), 1):,.0%}"}
                     for y, vals in sorted(moved.items())],
                    hide_index=True, use_container_width=True)
                st.caption(
                    "Both values are correct — each was the filed figure on the day "
                    "it was filed. A number copied down on the first of those days "
                    "was right, and wrong within a year, with nothing announcing it.")

    st.divider()
    st.markdown("#### 3 · The source does not exist")
    e, f = st.columns([1, 1])
    with e:
        st.markdown("**Asked about** &#36;28.3 billion — JPMorgan, FY2011")
        st.code("TAG:   us-gaap:AllowanceForLoanAndLeaseLosses\n"
                "VALUE: 23,023,000,000", language="text")
    with f:
        st.markdown("**What is true**")
        st.error("That concept exists in the us-gaap taxonomy, is spelled "
                 "correctly, and **JPMorgan has never filed it** — not once, in "
                 "any year, in any of its 918 concepts.")
    st.success(
        "**This one is solvable, and cleanly.** Whether a filer has ever used a "
        "concept is a lookup in data they published — no model, no human, no "
        "opinion. It is the only one of the three with a deterministic answer, "
        "which is why it became a tool. **Try it in the last tab, "
        "*Is the source real?***")
    st.caption(
        "Measured over 40 blinded questions: 29 declined, 11 committed to an "
        "answer, and 2 of those 11 cited a concept the filer had never used.")

# --- live check -------------------------------------------------------------

with fail_tab:
    st.caption(
        "One claim, checked live against data.sec.gov. Slower than the report "
        "above because the agent runs now — typically ten to twenty seconds."
    )
    claim = st.text_area(
        "Claim",
        value="Goldman Sachs reported net revenues of $58.28 billion for fiscal year 2025.",
        height=90,
    )
    if st.button("Audit this claim", type="primary"):
        from agent import audit_checked
        from memory import MemoryStore

        with st.spinner("Looking it up at data.sec.gov…"):
            # The system temp directory, not a folder beside the code.
            #
            # This first pointed at .cache/, which exists locally and is
            # gitignored — so on Render the directory was simply absent and
            # sqlite3 cannot create a file inside a directory that does not
            # exist. It failed only on this tab, only in deployment, and only
            # when a visitor pressed the button, which is the worst of the
            # possible times to find out.
            #
            # Nothing here needs to survive: memory is off, the store is handed
            # in empty so that no learned fact can influence a live answer, and
            # a free instance loses its filesystem on every sleep anyway.
            store = MemoryStore(
                dsn="", sqlite_path=Path(tempfile.gettempdir()) / "capstone-live.db"
            )
            answer, trace, evidence = asyncio.run(
                audit_checked(claim, store=store, learn=False)
            )

        if not evidence.get("admissible"):
            if evidence.get("reason") == "gave_up_early":
                st.error(
                    "**It gave up early.** The agent answered NOT_CHECKABLE after "
                    f"{evidence['tool_calls']} of 3 lookups, while the tool was "
                    "still offering tags it never tried: "
                    + ", ".join(f"`{t}`" for t in evidence.get("untried_tags", [])[:3])
                    + ". Its verdict is withheld, because \"nothing to check\" "
                    "has not been established by stopping early — and a false "
                    "NOT_CHECKABLE is the worst error here: a wrong verdict gets "
                    "argued with, while \"there is nothing to check\" ends the "
                    "enquiry and a real contradiction goes unreported."
                )
            else:
                st.error(
                    "**No evidence.** The agent answered without consulting any "
                    "filed data, on every attempt. Its verdict is not shown, "
                    "because an answer with nothing behind it is not a finding."
                )
            with st.expander("What it said anyway"):
                st.code(answer or "(nothing)")
        else:
            st.code(answer)
            st.caption(f"{evidence['tool_calls']} lookup(s), {evidence['attempts']} attempt(s)")
        with st.expander("Trace — every step the agent took"):
            for step in trace:
                st.text(f"{step.get('kind', '?'):8s} {str(step)[:300]}")

# --- method -----------------------------------------------------------------

with built_tab:
    st.markdown("### Three checks between an answer and the person reading it")
    st.markdown(svg(gate_svg()), unsafe_allow_html=True)
    st.caption(
        "The order matters because they fail in that order, and only the middle "
        "one leaves the run. That is the whole design: **a confidence score is "
        "the model marking its own work; a lookup is not.**")

    st.divider()
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("##### 1 · Did it look?")
        st.markdown(
            "Any tool call that came back with something. A call that errored or "
            "returned nothing does not count — otherwise the check is satisfied "
            "by calling a tool and ignoring the failure.")
        st.caption("Flags, never blocks. An answer that consulted nothing may "
                   "still be right; it must not be **credited**.")
    with c2:
        st.markdown("##### 2 · Does the source exist?")
        st.markdown(
            "The cited identifier, looked up in the system of record. **This is "
            "the one nobody does**, and the only one with a deterministic "
            "answer — it is in the system or it is not.")
        st.caption("Blocks. Nothing else in the answer can rescue a source that "
                   "is not there.")
    with c3:
        st.markdown("##### 3 · Does the source say that?")
        st.markdown(
            "The identifier is real, and holds something else. Numbers compare "
            "within 1.5%, because prose rounds — &#36;1.6 billion against a "
            "filed 1,595,000,000 is agreement, not a discrepancy.")
        st.caption("Flags. A gap is usually a difference of scope, not an error.")

    st.divider()
    st.markdown("### What a caller has to provide")
    a, b = st.columns([1.15, 1])
    with a:
        st.code(
            "from groundgate import Gate, Run, ToolCall\n\n"
            "gate = Gate(source=my_invoice_system)\n"
            "v = gate.check(Run(answer=reply, tool_calls=calls))\n\n"
            "v.outcome          # 'pass' | 'flag' | 'block'\n"
            "v.citation_exists  # is the source it named real?\n"
            "print(v)           # BLOCK: cited `INV-00000`, which is\n"
            "                   #        not in the source",
            language="python")
    with b:
        st.markdown("**A `Source` needs one method.**")
        st.code("def exists(self, citation: str) -> bool: ...", language="python")
        st.markdown(
            "That is the whole interface. An invoice table, a ticket system, a "
            "document store, a filer's XBRL facts — the gate does not know or "
            "care which.\n\n"
            "`value()` is optional, and the SEC source deliberately declines to "
            "implement it: a concept's value depends on which fiscal year and "
            "period type is meant, and guessing would compare against the wrong "
            "figure.")

    st.divider()
    st.markdown("### What it will not claim")
    st.warning(
        "**A verified source is not a verified answer.** All three checks can "
        "pass on an answer that cites a real record, quotes it correctly, and "
        "answers a question you did not ask. This narrows how an answer can be "
        "unfounded. It does not establish that it is founded.")
    st.markdown(
        "Measured over 40 questions put to gpt-4o with every figure stripped out, "
        "so it had to recall rather than repeat: **29 declined, 11 committed to "
        "an answer, and 2 of those cited a concept the filer had never used.** "
        "Both were real concepts in the us-gaap taxonomy, correctly spelled.\n\n"
        "An earlier eight-question pilot suggested the fabricated citation "
        "usually sits beside a *correct* figure — the pairing that makes it "
        "dangerous, because whoever checks the number finds it right. **At forty "
        "that was 0 of 11** and the claim was retracted. The full record is at "
        "[groundgate.onrender.com](https://groundgate.onrender.com).")
    st.caption(
        "The library, its twelve tests and the run that produced these numbers "
        "are in `ai-engineering-bootcamp-v2/groundgate/`.")


# --- is the source real? ----------------------------------------------------
#
# The one component of this project general enough to leave the subject. Every
# other tab is about bank filings; this one is about a check that applies to any
# assistant that cites anything — an invoice number, a ticket, a document id.
#
# It lives here rather than as its own service because it already has what it
# needs: this app is deployed, has a key, and redeploys on push. A second free
# instance would add a URL to maintain and a cold start to apologise for, in
# exchange for nothing a tab does not give.
#
# It calls no model and needs no key. "Has this filer ever used this concept" is
# a lookup in data they published, so a visitor can check any verdict here
# against data.sec.gov themselves — which is the entire reason the check is
# worth anything. A confidence score from a second model would not be.

with gate_tab:
    import re as _re
    import sys as _sys

    _sys.path.insert(0, str(report_mod.HERE.parent / "groundgate"))
    try:
        from groundgate import Gate, Run, ToolCall, default_extract_citation
        from sources import DictSource, SecTagSource
    except ImportError as exc:                                # noqa: BLE001
        st.error(f"groundgate is not importable here: {exc}")
        st.stop()

    BANKS = {"JPM": "JPMorgan Chase", "BAC": "Bank of America",
             "MS": "Morgan Stanley", "WFC": "Wells Fargo", "C": "Citigroup"}

    # A system of record small enough to print.
    #
    # The SEC version checks against 918 concepts a bank really filed, which is
    # the honest demonstration and completely opaque: a visitor types an
    # identifier into a box and something invisible says yes or no. Nothing is
    # learned unless they already know what an XBRL concept is.
    #
    # Six invoices fit on screen. The reader sees both sides of the check at
    # once — the claim, and the table it is checked against — and needs no
    # domain knowledge at all. It also makes the third check demonstrable, which
    # the SEC source cannot do: a tag's value depends on which fiscal year and
    # period type is meant, so sources.py deliberately declines to guess.
    INVOICES = {
        "INV-88421": 1_200_000, "INV-88422": 84_000, "INV-88510": 15_400,
        "INV-90001": 840_000, "INV-90114": 226_000, "INV-91002": 47_500,
    }
    VENDORS = {"INV-88421": "Amazon Web Services", "INV-88422": "Datadog",
               "INV-88510": "Figma", "INV-90001": "Snowflake",
               "INV-90114": "Databricks", "INV-91002": "PagerDuty"}

    st.markdown("### An answer is not verified because it names a source")
    st.markdown(
        "An assistant tells you the company spent **&#36;1.2 million on AWS last "
        "quarter, source: invoice INV-88421**. The figure is right. There is no "
        "invoice INV-88421.\n\n"
        "A wrong number is caught by the next person who looks. **A wrong source "
        "is caught by nobody** — nobody has the reflex to check that a cited "
        "identifier exists. So it travels into a report and acquires the "
        "authority of something verified.")

    st.divider()
    mode = st.radio(
        "Check an answer against:",
        ["An invoice system — six rows, printed below",
         "A bank's SEC filings — every concept it has ever filed"],
        key="gate_mode", horizontal=False)
    invoices = mode.startswith("An invoice")

    if invoices:
        st.caption("**This is the entire system of record.** Anything an answer "
                   "cites is either in this table or invented.")
        st.dataframe(
            [{"invoice": k, "vendor": VENDORS[k], "amount": f"${v:,}"}
             for k, v in INVOICES.items()],
            hide_index=True, use_container_width=True)
        source = DictSource(INVOICES)
        system = "the invoice system"
        samples = {
            "a real source, checked": "We spent $840,000 with Snowflake.\nSource: INV-90001",
            "a real source, wrong amount": "We spent $1.2 million with Snowflake.\nSource: INV-90001",
            "an invented source": "We spent $1.2 million on AWS.\nSource: INV-00042",
            "no source at all": "We spent about $1.2 million on AWS last quarter.",
        }
    else:
        bank = st.selectbox(
            "Which filer", list(BANKS),
            format_func=lambda t: f"{BANKS[t]} — everything it has filed with the SEC",
            key="gate_bank")
        source = SecTagSource(bank)
        st.caption(f"{len(source.facts):,} concepts {BANKS[bank]} has actually filed, "
                   "2009–2026, live from data.sec.gov. You cannot see them all, which "
                   "is exactly why a person cannot do this check by eye.")
        system = BANKS[bank] + "'s filings"
        samples = {
            "a real source": "Total assets were $4.4 trillion.\nSource: us-gaap:Assets",
            "an invented source":
                "The allowance for credit losses was $28.3 billion.\n"
                "Source: us-gaap:AllowanceForLoanAndLeaseLosses",
            "one that splits the banks":
                "Net revenue was $46.5 billion.\nSource: us-gaap:RevenuesNetOfInterestExpense",
            "no source at all": "Total assets were $4.4 trillion.",
        }

    st.markdown("#### Try to fool it")
    st.caption(
        f"Edit the answer below — **invent a source that sounds real** and see what "
        f"happens. You will know you made it up, which is the point: nobody has to "
        f"take my word for a rigged example. Or load one of these:")
    # Seed the box through session_state and never pass value= alongside a key.
    # Streamlit ignores value when the key already exists and logs a warning for
    # it, so the two together are a widget that silently stops honouring its own
    # default. Switching the system of record reseeds, because an invoice number
    # in the SEC box would check a question nobody asked.
    if st.session_state.get("gate_seeded") != mode:
        st.session_state["gate_answer"] = list(samples.values())[1]
        st.session_state["gate_seeded"] = mode
        st.session_state.pop("gate_done", None)

    cols = st.columns(len(samples))
    for col, (label, text) in zip(cols, samples.items()):
        if col.button(label, key=f"gs_{label}", use_container_width=True):
            st.session_state["gate_answer"] = text
            st.session_state.pop("gate_done", None)
            st.rerun()

    left, right = st.columns([1.05, 1])
    with left:
        answer = st.text_area(
            "An answer, as your assistant would produce it",
            height=118, key="gate_answer")
        looked = st.checkbox("the assistant consulted the system before answering",
                             value=True, key="gate_looked")
        if st.button("Check the source", type="primary", key="gate_run"):
            st.session_state["gate_done"] = True

    with right:
        if "gate_done" not in st.session_state:
            st.info(f"Press **Check the source**. No model is called — this is a "
                    f"lookup in {system}.")
        else:
            money = _re.search(r"\$\s?([\d,]+(?:\.\d+)?)\s*(million|billion|trillion)?",
                               answer, _re.I)
            claimed = None
            if money and invoices:
                claimed = float(money.group(1).replace(",", ""))
                claimed *= {"million": 1e6, "billion": 1e9, "trillion": 1e12}.get(
                    (money.group(2) or "").lower(), 1)
            v = Gate(source=source).check(Run(
                answer=answer, claimed_value=claimed,
                tool_calls=[ToolCall("lookup", result={"ok": True})] if looked else []))
            icon = {"pass": "✅", "flag": "⚠️", "block": "⛔"}[v.outcome]
            box = {"pass": st.success, "flag": st.warning, "block": st.error}[v.outcome]
            box(f"### {icon} {v.outcome.upper()}\n\n" + svg("; ".join(v.reasons)))
            m = st.columns(3)
            m[0].metric("did it look?", "yes" if v.looked else "no")
            m[1].metric("source exists?", "—" if v.citation_exists is None
                        else ("yes" if v.citation_exists else "NO"))
            m[2].metric("source agrees?", "—" if v.value_matches is None
                        else ("yes" if v.value_matches else "NO"))
            cite = default_extract_citation(answer)
            st.caption(svg(
                f"Looked `{cite}` up in {system}. "
                + ("It is there." if v.citation_exists else
                   "It is not there — and it reads perfectly, which is exactly "
                   "why nobody catches it.")) if cite else
                "No `Source:` line found. The parser is strict on purpose: guessing "
                "which noun was meant as the source would make this component's own "
                "output unverifiable.")

    st.divider()
    if not invoices:
        st.markdown(
            "**The pair worth trying.** `us-gaap:RevenuesNetOfInterestExpense` "
            "**passes** for JPMorgan, Morgan Stanley and Wells Fargo and is "
            "**blocked** for Bank of America and Citigroup, which file "
            "`us-gaap:Revenues` instead. Same plausible label, and for a bank the "
            "two concepts are tens of billions apart. Switch the filer and watch "
            "the verdict flip.\n\n"
            "`us-gaap:ProvisionForCreditLosses` and "
            "`us-gaap:AllowanceForLoanAndLeaseLosses` are real concepts in the "
            "taxonomy that **not one of the five has ever filed**.")
    st.caption(
        "**A verified source is not a verified answer.** All three checks can pass "
        "on an answer that cites a real record, quotes it correctly, and answers a "
        "question you did not ask. This narrows how an answer can be unfounded; it "
        "does not establish that it is founded. Measured over 40 blinded questions: "
        "29 declined, 11 committed, 2 cited a concept the filer had never used. "
        "The full record, including a claim it retracted, is at "
        "[groundgate.onrender.com](https://groundgate.onrender.com)."
    )
