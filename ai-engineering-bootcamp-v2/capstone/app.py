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

BADGE = {
    "SUPPORTED": ("✅", "VERIFIED", "#1a7f37"),
    "CONTRADICTED": ("❌", "CONTRADICTED", "#cf222e"),
    "DEFINITION_MISMATCH": ("⚠️", "BASIS MISMATCH", "#9a6700"),
    "NOT_CHECKABLE": ("❔", "NOT IN XBRL", "#57606a"),
}

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
# turned out to be general. Now it opens on that component — three failures, the
# thing built to catch one of them, and the measurement — and the filings work
# follows as what it is: the evidence, and the place the failures were found.
(fail_tab, built_tab, gate_tab, match_tab,
 study_tab, time_tab, filings_tab) = st.tabs(
    ["Three ways it fails", "What we built", "Is the source real?",
     "Does the number match?", "How much can be checked", "How figures change",
     "Filing report"])


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

# --- filing report ----------------------------------------------------------

with filings_tab:
    # The output of the pipeline in tab 2, one filing at a time. This replaced a
    # report built from the earlier agent runs over fifty hand-drawn claims —
    # two things were on the page describing different systems, and the older
    # one was the one a visitor saw first.
    import json as _json

    joined = [_json.loads(l) for l in (report_mod.HERE / "join.jsonl").open(encoding="utf-8")]
    hist = {m["fiscal_year"]: m for m in
            _json.loads((report_mod.HERE / "data" / "history" / "manifest.json").read_text())}
    years = sorted({r["doc_fy"] for r in joined}, reverse=True)

    year = st.selectbox("Filing", years,
                        format_func=lambda y: f"JPMorgan Chase — FY{y} Form 10-K")
    rows = [r for r in joined if r["doc_fy"] == year]
    meta = hist.get(str(year), {})

    buckets = {b: [r for r in rows if r["bucket"] == b]
               for b in ("verified", "review", "no_counterpart")}

    st.caption(
        f"{len(rows)} numeric claims from Item 7 checked against filed XBRL"
        + (f" · [source filing]({meta['source_url']})" if meta.get("source_url") else ""))

    c = st.columns(3)
    c[0].metric("Verified", len(buckets["verified"]), help="a filed figure matches")
    c[1].metric("Needs a person", len(buckets["review"]),
                help="a concept was identified, but the figure differs")
    c[2].metric("Nothing to check against", len(buckets["no_counterpart"]),
                help="no filed concept resembles the claim")

    st.divider()

    def money(v):
        return f"&#36;{v/1e9:,.2f}B" if abs(v) >= 1e9 else f"&#36;{v/1e6:,.0f}M"

    st.markdown("#### Verified — the claim matches what was filed")
    if not buckets["verified"]:
        st.caption("None this year.")
    for r in buckets["verified"]:
        with st.expander(f"**{r['figure']}** · {r.get('matched_tag','')}"):
            st.info(r["raw_sentence"][:400].replace("$", "\\$"))
            m = st.columns(3)
            m[0].metric("claimed", r["figure"].replace("$", "\\$"))
            m[1].metric("filed", money(r["filed"]).replace("&#36;", "$"))
            m[2].metric("confidence", f"{r.get('matched_cos', 0):.2f}")
            st.caption(f"Concept: `{r.get('matched_tag')}`"
                       + ("  ·  compared as a year-over-year change" if r["is_change"]
                          else "  ·  compared as a total"))

    st.divider()
    st.markdown("#### Needs a person — a concept was found, the figure differs")
    st.warning(
        "**This queue mixes two different things and the tool cannot separate "
        "them.** Some are genuine disagreements. Most are a figure that measures "
        "something narrower than the concept it resembles — one division rather "
        "than the bank, or a part of a total. A person has to look."
    )
    for r in buckets["review"][:25]:
        with st.expander(f"**{r['figure']}** · closest concept: {r.get('best_tag','—')}"):
            st.info(r["raw_sentence"][:400].replace("$", "\\$"))
            st.caption(f"Closest concept `{r.get('best_tag')}` at confidence "
                       f"{r.get('best_cos', 0):.2f}, but its filed value does not match "
                       f"this figure."
                       + (f"  ·  section: {r['section']}" if r.get("section") else ""))
    if len(buckets["review"]) > 25:
        st.caption(f"…and {len(buckets['review']) - 25} more.")

    st.divider()
    st.markdown("#### Nothing to check against")
    st.caption(
        f"{len(buckets['no_counterpart'])} claims — {len(buckets['no_counterpart'])/len(rows):.0%} "
        "of this filing. No filed concept resembles them closely enough to compare. "
        "They are changes, part-figures, ratios, or one division rather than the "
        "whole bank. **MD&A is not required to be tagged, so this is normal and "
        "legal — an unverifiable claim is unverifiable, not false.**"
    )
    with st.expander("See a sample"):
        for r in buckets["no_counterpart"][:12]:
            st.markdown(f"- **{r['figure'].replace('$', chr(92)+'$')}** — "
                        f"{r['raw_sentence'][:150].replace('$', chr(92)+'$')}…")


# --- verification over time ---------------------------------------------------
#
# Stage 3. The filed side is complete, so it is drawn as a line; MD&A appears
# only where a claim verified, which is 61 times in 3,915. Those points are not
# joined, because four dots spread over fifteen years are not a series and
# drawing them as one would be inventing data.

with time_tab:
    import json as _json
    st.markdown("### The same concept, filing after filing")
    st.caption(
        "What a company filed each year is complete and continuous. What it "
        "wrote about in prose, and that we could verify, is sparse — so the "
        "filed value is a line and verified claims are individual points."
    )
    panel = report_mod.HERE / "stage3_panel.svg"
    if panel.exists():
        st.markdown(svg(panel.read_text(encoding="utf-8")), unsafe_allow_html=True)

    st.divider()
    left, right = st.columns(2)
    with left:
        st.markdown("**Figures move after they are published**")
        st.markdown(
            "JPMorgan's filed cash from operating activities changed between "
            "filings in **four consecutive years** — 2016, 2017, 2018 and 2019. "
            "Every annual report republishes the prior years, and the values can "
            "move when something is reclassified.\n\n"
            "A figure copied down in 2016 was correct that day and wrong within "
            "a year, with nothing to announce it."
        )
    with right:
        st.markdown("**And concepts stop being filed**")
        st.markdown(
            "`TierOneRiskBasedCapital` ends in **2013**, at the Basel III "
            "transition that replaced the capital definitions. "
            "`StockRepurchaseProgramAuthorizedAmount1` ends in **2022**.\n\n"
            "Both still answer a request with their historical data, so **a dead "
            "concept looks exactly like a live one** until you check which years "
            "it covers."
        )
    st.info(
        "Together these correct the rule the memory layer was built on. *Store "
        "where to look, not what was found* was right about figures and "
        "incomplete about routes — **routes expire too**, and unlike a stale "
        "figure a dead one fails silently."
    )

    st.divider()
    st.markdown("### Every verified claim, by concept")
    joined = [_json.loads(l) for l in (report_mod.HERE / "join.jsonl").open(encoding="utf-8")]
    ver = [r for r in joined if r["bucket"] == "verified"]
    concepts = sorted({r["matched_tag"] for r in ver})
    pick = st.selectbox(
        f"{len(ver)} verified claims across {len(concepts)} concepts",
        concepts,
        format_func=lambda t: f"{t}  ({sum(1 for r in ver if r['matched_tag']==t)})")
    hits = sorted((r for r in ver if r["matched_tag"] == pick),
                  key=lambda r: r["fiscal_year"])
    st.caption(
        f"Verified in {len(hits)} claim(s), across fiscal years "
        f"{', '.join(str(r['fiscal_year']) for r in hits)}."
    )
    for r in hits:
        with st.expander(f"FY{r['fiscal_year']} · **{r['figure']}**"):
            st.info(r["raw_sentence"][:400].replace("$", chr(92) + "$"))
            m = st.columns(3)
            m[0].metric("claimed", r["figure"])
            m[1].metric("filed", f"${r['filed']/1e9:,.2f}B")
            m[2].metric("confidence", f"{r.get('matched_cos', 0):.2f}")
            st.caption("compared as a year-over-year change" if r["is_change"]
                       else "compared as a total")


# --- claim against filing, one concept at a time ----------------------------
#
# The concrete artifact: what Item 7 said, what was filed, and how far apart
# they are — browsable by the concept the sentence was pinned to, because XBRL
# is a fixed vocabulary and the concept is the only grouping a reader can act on.
#
# Two things here are deliberate and both cost the page some confidence.
#
# There is no red cross. A large gap in this corpus has never once turned out to
# be a bank misreporting; every one inspected was a wrong pin, a near-miss pin,
# or a sentence the splitter merged. Rendering those as contradictions would
# publish our own bugs under JPMorgan's name. browse.py's header carries the
# three examples.
#
# And the picker shows each pin's own record, because a concept that collected
# 40 claims and landed on none of them is one bad pin applied 40 times. A reader
# who clicks into that finds the tool wrong, not the filer — so it is labelled
# before they click, not after.

# The question the model is asked, with every figure taken out of it.
#
# The version in stage4_grid.py opens "The figure to identify is $1.6 billion"
# and then pastes in a sentence that also contains it. Nothing was being asked:
# a model repeating $1.6 billion had read its own prompt, and scoring that as a
# hit made a fabricated tag beside a correct number look like a success.
#
# So both leaks are closed. Every money and percent figure in the sentence is
# masked, the one under test is marked, and the model has to supply the number
# from somewhere — its own memory, or the tool. That is the whole comparison.
#
# stage4_grid.ASK is deliberately left alone. stage4_grid.json was measured with
# it and the numbers on tab 1 are reported against that wording; silently
# changing the prompt under a published result would make the two disagree with
# no record of why.
BLIND_ASK = """Below is one sentence from {company}'s 10-K, Item 7, for
fiscal year {fy}. Every figure in it has been removed.

    {masked}

The removed figure marked [[ ? ]] is the one to identify.

Which exact XBRL tag did {company} file it under, and what value did they
file for fiscal year {fy}?

Answer in exactly this shape and nothing else:
TAG: <the exact us-gaap tag, or UNKNOWN>
VALUE: <the figure in dollars, or UNKNOWN>"""


@st.cache_data(show_spinner=False)
def redact(sentence: str, figure: str) -> str:
    """Mask every figure in a sentence, marking the one under test.

    extract.py's own patterns, imported rather than copied: the corpus was built
    with them, and a second private notion of what counts as a figure would let
    one leak through the mask that the extractor had already called a claim.
    """
    import extract as _ex

    spans = sorted({(m.start(), m.end()) for m in _ex.MONEY.finditer(sentence)}
                   | {(m.start(), m.end()) for m in _ex.PCT.finditer(sentence)})
    # Drop any span wholly inside another, so "$1.6 billion" is masked once.
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


@st.cache_resource(show_spinner=False)
def filer_facts(ticker: str = "JPM") -> dict:
    """Every us-gaap concept this filer has ever filed, parsed once per process.

    Cached as a resource rather than data: it is 8 MB of JSON and re-parsing it
    on every rerun would cost more than the model call it is checking.
    """
    import prepare_evidence as _pe
    return _pe.company_facts(ticker)["facts"]["us-gaap"]


def check_tag(tag: str | None, fy: int, ticker: str = "JPM") -> tuple[str, float | None, str | None]:
    """Did this filer actually file that tag, and what value for that year?

    This is the citation check, and it is the part of the comparison that could
    not be done by eye. A model can name a concept that exists in the us-gaap
    taxonomy, is spelled correctly, and reads plausibly — and that this filer
    has never once used. Asked about JPMorgan's FY2021 preferred dividends, the
    no-tool model answered PreferredStockDividendsAndOtherAdjustments, which is
    a real taxonomy concept and appears nowhere in JPMorgan's filings.

    That is the failure tab 1 opens with, and it is worse than a wrong number:
    the figure beside it can be right, so a reader who checks the figure finds
    it correct and assumes the source is too.
    """
    if not tag or tag.upper() == "UNKNOWN":
        return "declined", None, None
    # "us-gaap_InvestmentBankingRevenue" is the right concept with the namespace
    # glued on. Reporting that as a tag the filer never used would be the same
    # unfairness as scoring a synonym wrong — a formatting difference dressed up
    # as a fabrication.
    tag = re.sub(r"^(us[-_]?gaap|jpm)[:_]", "", tag.strip(), flags=re.I)
    part = filer_facts(ticker).get(tag)
    if part is None:
        return "not_filed", None, tag
    import prepare_evidence as _pe
    usd = part.get("units", {}).get("USD") or []
    v = _pe.annual_value(usd, fy)
    return ("filed", v["value"], tag) if v else ("filed_other_years", None, tag)


def model_value(text: str | None) -> float | None:
    """Turn a model's VALUE line into dollars.

    prepare_evidence.parse_claimed reads prose ("$21.94 billion") because that
    is what 10-K prose looks like. Models write "$21.94B", and parse_claimed
    returns 21.94 for that — a number a thousand million times too small, which
    would score a correct answer as wrong. Expand the suffix first, then hand
    the rest to the same parser the corpus uses.
    """
    if not text:
        return None
    t = text.replace(",", "").strip()
    m = re.search(r"\$?\s*(-?[\d.]+)\s*(bn|b|mm|m|t)\b", t, re.I)
    if m:
        scale = {"t": 1e12, "bn": 1e9, "b": 1e9, "mm": 1e6, "m": 1e6}[m.group(2).lower()]
        try:
            return float(m.group(1)) * scale
        except ValueError:
            return None
    import prepare_evidence as _pe
    return _pe.parse_claimed(t)


VERDICT = {
    "agrees":       ("✅", "agrees",              "#1a7f37", "#3fb950"),
    "basis":        ("⚠️", "different basis",     "#9a6700", "#d29922"),
    "incomparable": ("❔", "too far to compare",  "#57606a", "#8b949e"),
}

with match_tab:
    import html as _html

    index = report_mod.load_json("browse_index.json", [])
    if not index:
        st.info("Run `python3 browse.py` to build the browse files.")
    else:
        st.markdown("### What Item 7 says, against what was filed")
        st.caption(
            f"{sum(f['claims'] for f in index):,} claims across five banks, "
            "fiscal years 2011–2025. Each was pinned to a filed concept **by "
            "its wording alone** — the figure was never consulted when choosing "
            "the concept, so a gap here is a measurement rather than a leftover."
        )

        # A bank picker, and the pin's record for that bank beside it. The
        # wording-only pin lands on 7-13% of a filer's claims and agrees on a
        # few percent of those, everywhere — which is worth a reader seeing
        # before they browse, because otherwise the first empty concept looks
        # like a bug rather than the normal case.
        by_ticker = {f["ticker"]: f for f in index}
        order = [t for t in ("JPM", "BAC", "MS", "WFC", "C") if t in by_ticker]
        bank = st.selectbox("Bank", order, key="mt_bank",
                            format_func=lambda t: by_ticker[t]["name"])
        f = by_ticker[bank]
        b = st.columns(4)
        b[0].metric("claims pinned", f"{f['claims']:,}")
        b[1].metric("agree within 1.5%", f["agrees"])
        b[2].metric("different basis", f["basis"])
        b[3].metric("concepts", f"{f['works']} / {f['partial']} / {f['broken']}",
                    help="pin works / partial / never lands")

        data = report_mod.load_json(f"browse_{bank}.json", {})
        if not data:
            st.info(f"No browse file for {bank}.")
            st.stop()
        concepts = data["concepts"]
        hide = st.checkbox(
            f"Hide the {sum(1 for c in concepts if c['tier'] == 'broken')} concepts "
            "whose pin never lands within 10%", value=True,
            help="These are our retrieval failures, not the filer's. They are "
                 "kept visible rather than deleted so a refusal cannot be "
                 "mistaken for a clean result.")
        shown = [c for c in concepts if not (hide and c["tier"] == "broken")]

        TIER_MARK = {"works": "●●●", "partial": "●○○", "broken": "○○○"}
        pick = st.selectbox(
            f"{len(shown)} concepts · sorted by how often the pin lands",
            range(len(shown)),
            format_func=lambda i: (
                f"{TIER_MARK[shown[i]['tier']]}  {shown[i]['label'][:52]}  — "
                f"{shown[i]['n']} claims, {shown[i]['plausible']} comparable"))
        c = shown[pick]
        rows = data["claims"][c["tag"]]

        st.code(f"us-gaap:{c['tag']}", language="text")

        if c["tier"] == "broken":
            st.error(
                f"**This pin never lands.** {c['n']} claims were pinned to this "
                "concept and not one came within 10% of the filed value. That is "
                "one bad pin applied "
                f"{c['n']} times, not {c['n']} disagreements — read the rows as "
                "our retrieval failing, not as anything about JPMorgan."
            )
        elif c["tier"] == "partial":
            st.warning(
                f"**Over-applied.** The pin landed on {c['plausible']} of "
                f"{c['n']} claims ({c['plausible']/c['n']:.0%}). The comparable "
                "rows below are worth reading; the rest are sentences this "
                "concept attracted and does not describe."
            )
        else:
            st.success(
                f"**This pin has a record.** It landed on {c['plausible']} of "
                f"{c['n']} claims ({c['plausible']/c['n']:.0%}), so a gap on this "
                "concept is worth taking seriously."
            )

        m = st.columns(3)
        m[0].metric("agrees", c["agrees"], help="within 1.5% — prose rounds")
        m[1].metric("different basis", c["basis"], help="1.5% to 10% apart")
        m[2].metric("too far to compare", c["incomparable"], help="over 10% apart")

        if c["systematic"]:
            sy = c["systematic"]
            st.info(
                f"**The prose sits consistently {sy['direction']} the filed "
                f"figure** — all {sy['n']} comparable claims lean the same way, "
                f"median {sy['median']:+.1%}. A gap that repeats with one sign is "
                "the signature of a scope difference: the sentence means "
                "something slightly wider or narrower than the tag. A gap that "
                "flips sign year to year is noise."
            )

        comparable = [r for r in rows if r["verdict"] != "incomparable"]
        st.markdown(f"#### {len(comparable)} comparable, closest first")
        if not comparable:
            st.caption("None. Every claim on this concept is more than 10% away.")

        show_all = st.checkbox(f"Also show the {len(rows)-len(comparable)} "
                               "claims too far apart to compare", value=False)
        listing = rows if show_all else comparable

        # The stylesheet is emitted once, above the loop. Every claim renders
        # its own card so that a Streamlit expander can sit under it — one blob
        # of HTML cannot carry sixty buttons, and the comparison only means
        # anything directly beneath the row it is comparing against.
        st.markdown(svg("""<style>
.cls{--ln:#e3e3e0;--ink:#1a1a19;--dim:#57606a;--q:#f6f6f4;margin:.3rem 0 -.4rem}
@media (prefers-color-scheme:dark){.cls{--ln:#2f2f2d;--ink:#e8e8e4;
      --dim:#a8a8a0;--q:#242423}}
.cls .cl{border:1px solid var(--ln);border-left-width:4px;border-radius:7px;
      padding:11px 15px 12px}
.cls .agrees{border-left-color:#1a7f37} .cls .basis{border-left-color:#9a6700}
.cls .incomparable{border-left-color:#8b949e}
.cls .hd{display:flex;justify-content:space-between;gap:12px;
      font:600 13px ui-sans-serif,system-ui,sans-serif;color:var(--ink)}
.cls .g{font-variant-numeric:tabular-nums;color:var(--dim);font-weight:400}
.cls .pair{display:grid;grid-template-columns:auto 1fr;gap:2px 14px;
      margin:8px 0 0;font-size:13px;color:var(--ink)}
.cls .k{color:var(--dim);font-size:11.5px;text-transform:uppercase;
      letter-spacing:.04em;align-self:center}
.cls .a,.cls .b{font-variant-numeric:tabular-nums;font-weight:600}
.cls blockquote{margin:9px 0 0;padding:7px 11px;background:var(--q);
      border-radius:5px;font-size:12.5px;line-height:1.55;color:var(--dim)}
.cls mark{background:#ffe08a;color:#1a1a19;padding:0 2px;border-radius:2px}
@media (prefers-color-scheme:dark){.cls mark{background:#6b5600;color:#fff}}
</style>"""), unsafe_allow_html=True)

        cache = st.session_state.setdefault("mc_results", {})

        for r in listing[:60]:
            icon, word, _lt, _dk = VERDICT[r["verdict"]]
            sent = _html.escape(r["sentence"])
            fig = _html.escape(r["figure"])
            if fig in sent:
                sent = sent.replace(fig, f"<mark>{fig}</mark>", 1)
            gap = ("exact" if r["gap"] is not None and abs(r["gap"]) < 0.0005
                   else f"{r['gap']:+.1%}" if r["gap"] is not None else "—")
            filed = f"${r['filed']/1e9:,.2f}B" if r["filed"] else "—"

            st.markdown(svg(
                f'<div class="cls"><div class="cl {r["verdict"]}">'
                f'<div class="hd"><span class="v">{icon} {word}</span>'
                f'<span class="g">{gap}</span></div>'
                f'<div class="pair"><span class="k">Item 7 says</span>'
                f'<span class="a">{fig}</span>'
                f'<span class="k">XBRL filed</span><span class="b">{filed}</span>'
                f'<span class="k">fiscal year</span><span>FY{r["fy"]}, in the '
                f'FY{r["doc_fy"]} filing</span></div>'
                f'<blockquote>{sent}</blockquote></div></div>'), unsafe_allow_html=True)

            # --- the same claim, without the pipeline -----------------------
            #
            # Two conditions, not three. An earlier draft ran a third that
            # handed the model the concept this page pinned and asked it to
            # confirm — which is exactly what the card above already shows, so
            # it was answering a question the reader could already see answered.
            # What is left is the comparison that is actually like-for-like:
            # here is our answer, here is what a general model says when asked
            # the same thing.
            #
            # Behind a button, per claim, cached. Two calls against a
            # rate-limited API is ten to thirty seconds and a real bill; running
            # them on page load would spend both on every visitor who scrolled.
            with st.expander("Could a model have found this without the pipeline?"):
                masked = redact(r["sentence"], r["figure"])
                st.caption(
                    "The row above is what this pipeline says. Below is the "
                    "**exact question** put to a general model — with every "
                    "figure stripped out, so the answer is not sitting in the "
                    "prompt. It has to produce the number, from memory or from "
                    "the tool."
                )
                st.code(BLIND_ASK.format(fy=r["fy"], masked=masked,
                                         company=f["name"]), language="text")
                # The caveat sits above the button, not under the result, so a
                # reader has it whichever way the run goes. It was missing until
                # someone read a run where the tool-equipped model matched this
                # pipeline exactly and asked the obvious question.
                st.info(
                    "**A fair test, on the easy claims.** Every claim you can "
                    "run this on is one where our own pin landed *and* the "
                    "filed figure corroborated it — that is what makes it "
                    "scoreable. On the 593 claims where the pin did not land we "
                    "have no answer to test a model against, so this comparison "
                    "cannot show either side failing there. **Expect the "
                    "tool-equipped model to do well here**: over 40 such claims "
                    "it named the right concept 70% of the time. What it cannot "
                    "do is tell you which 70% — 4 of those 40 were confident and "
                    "wrong, and nothing in the answer says which."
                )
                if st.button("Run both conditions", key=f"mc_{r['id']}"):
                    import asyncio as _asyncio
                    import os as _os
                    import tempfile as _tempfile
                    import time as _time

                    # Imported here, not at the top of the module: a missing or
                    # broken model dependency then costs this one button rather
                    # than the whole page. The tabs that need no model at all
                    # must stay readable on a box that cannot reach OpenAI.
                    from openai import OpenAI

                    from memory import MemoryStore
                    from stage4_grid import no_tools, parse, with_agent

                    base = BLIND_ASK.format(fy=r["fy"], masked=masked,
                                            company=f["name"])
                    with st.spinner("Two model runs against data.sec.gov…"):
                        try:
                            client = OpenAI(api_key=_os.getenv("OPENAI_API_KEY"))
                            got = [("Model alone, no tools",
                                    parse(no_tools(client, base)),
                                    "No tool, no filing, and the figure is "
                                    "not in the question. Whatever it says is "
                                    "recall or invention.")]
                            _time.sleep(2)
                            store = MemoryStore(dsn="", sqlite_path=Path(
                                _tempfile.gettempdir()) / "mc.db")
                            got.append(("Model + SEC lookup tool",
                                        parse(_asyncio.run(with_agent(base, store))),
                                        "Full tool access, free to search as it "
                                        "likes. This is the comparison that "
                                        "matters."))
                            cache[r["id"]] = got
                        except Exception as exc:                  # noqa: BLE001
                            st.error(
                                f"The run failed: `{type(exc).__name__}: {exc}`. "
                                "Everything else on this page is precomputed and "
                                "unaffected — only this button needs a model."
                            )

                for label, (tag, value), note in cache.get(r["id"], []):
                    # The tag is scored against what this filer actually filed,
                    # not against our pin. Three things had to be separated and
                    # an earlier version ran them together:
                    #
                    #   a synonym      LoansAndLeasesReceivableAllowance instead
                    #                  of the pinned FinancingReceivable... —
                    #                  both hold $21.94B for FY2012, so naming
                    #                  either one is right
                    #   a wrong tag    a concept the filer does file, holding a
                    #                  different number
                    #   a fabrication  a concept this filer has never filed
                    #
                    # And the value is not scored at all for the no-tool
                    # condition, because ASK hands the figure to the model in
                    # the question. Repeating it is not recall, and marking it
                    # a hit made a fabricated citation look like a success.
                    state, actual, clean = check_tag(tag, r["fy"], bank)
                    said = model_value(value)
                    value_ok = (said is not None and r["filed"]
                                and abs(said - r["filed"]) / abs(r["filed"]) <= 0.015)
                    same_tag = bool(clean) and clean.lower() == c["tag"].lower()
                    tag_holds_it = (actual is not None and r["filed"]
                                    and abs(actual - r["filed"]) / abs(r["filed"]) <= 0.015)

                    # Both axes matter now that neither is handed over. The
                    # source is judged first, because a fabricated citation is
                    # the worse failure even when the number beside it is right
                    # — and in practice that is exactly the pair that shows up.
                    if state == "declined":
                        mark, word = "❔", "declined"
                    elif state == "not_filed":
                        mark, word = "⛔", f"cited a tag {f['name']} has never filed"
                    elif state == "filed_other_years":
                        mark, word = "⚠️", f"real tag, nothing filed for FY{r['fy']}"
                    elif tag_holds_it and value_ok:
                        mark, word = (("✅", "right concept, right number") if same_tag
                                      else ("🟰", "a different tag holding the same figure"))
                    elif tag_holds_it:
                        mark, word = "❌", "right concept, wrong number"
                    else:
                        mark, word = "❌", "a real concept, holding a different figure"

                    st.markdown(f"{mark} **{label}** — {word}")
                    g = st.columns(2)
                    g[0].caption("tag it answered")
                    g[0].code(tag or "— none —", language="text")
                    g[0].caption(
                        f"{f['name']} has never filed this tag" if state == "not_filed"
                        else f"{f['name']} filed ${actual/1e9:,.2f}B under it for FY{r['fy']}"
                        if actual is not None
                        else "" if state == "declined"
                        else f"filed in other years, not FY{r['fy']}")
                    g[1].caption("value it answered")
                    g[1].code((value or "— none —")[:60], language="text")
                    g[1].caption(
                        f"✔ matches the filing (${r['filed']/1e9:,.2f}B)" if value_ok
                        else "— nothing offered —" if said is None
                        else f"✘ the filing says ${r['filed']/1e9:,.2f}B, "
                             f"off by {abs(said - r['filed']) / abs(r['filed']):.0%}")
                    st.caption(note)

                if cache.get(r["id"]):
                    st.caption(
                        "**Nothing here was given to the model.** The figure was "
                        "stripped from the question, and every tag it named was "
                        f"checked against {f['name']}'s own filings afterwards. "
                        "✅ and 🟰 both found it — banks file some numbers "
                        "under more than one concept, so naming either is right. "
                        "❌ named a concept the firm does file, holding a "
                        "different number. **⛔ is the worst**: a concept the "
                        "firm has never filed — and it can appear beside a "
                        "figure that is correct, so anyone checking the number "
                        "finds it right and assumes the source is too."
                    )

        if len(listing) > 60:
            st.caption(f"Showing 60 of {len(listing)}.")

        st.divider()
        st.caption(
            "**Why there is no red cross.** Every large gap inspected by hand "
            "was a wrong pin, a near-miss pin (*“$2.7 billion of authorized "
            "repurchase capacity **remained**”* pinned to the authorised amount "
            "rather than the remaining amount), or a sentence the splitter "
            "merged with a cross-reference line. None was a bank misreporting. "
            "A red cross would need a pin confirmed by hand, on a concept with a "
            "record, with a gap too large to be rounding or scope — there are "
            "currently none, and inventing the category would publish our own "
            "bugs under the filer's name."
        )


# --- the study --------------------------------------------------------------
#
# Fifteen years of JPMorgan filings, measured without a single hand-labelled
# verdict. It is here rather than in a paper because the app is the only thing a
# stranger will open, and a finding that lives only in a repository is a finding
# nobody reads.

with study_tab:
    summary = {f["ticker"]: f for f in report_mod.load_json("coverage_summary.json", [])}
    st.markdown("### How much of a bank's own story can be checked?")
    st.caption(
        f"Five banks · 73 filings · "
        f"{sum(f['claims'] for f in summary.values()):,} numeric claims · "
        "fiscal years 2011–2025 · no model, no agent, no hand-labelled answer key."
    )

    comp = report_mod.HERE / "coverage_comparison.svg"
    if comp.exists():
        st.markdown(svg(comp.read_text(encoding="utf-8")), unsafe_allow_html=True)

    # The comparison used to be a single number, and the number was JPMorgan's.
    #
    # The study was built on one filer and reported 45% of FY2025 claims as
    # checkable. Four more banks put that figure between 14% and 47% — a spread
    # wider than the fifteen-year movement inside any one of them. So the page
    # leads with the spread, and the caveat sits with the figure rather than in
    # a document beside it, because a bank we read badly and a bank that
    # genuinely tags less produce the same line.
    st.warning(
        "**The headline number did not survive a second bank.** Built on "
        "JPMorgan alone, this study reported 45% of claims as checkable. Across "
        "five banks it runs **14% to 47%** — a wider spread than fifteen years "
        "of movement inside any single one. Read the slope within a panel, not "
        "the height between panels: our retrieval was tuned on JPMorgan, so a "
        "bank whose wording it reads badly is indistinguishable from a bank "
        "that tags less of its narrative."
    )
    st.markdown(
        "**What does replicate is the volume.** Numeric prose thinned in every "
        "bank, measured per 10,000 characters so a shorter document does not "
        "count as a finding: "
        + ", ".join(f"{f['name']} {f['first']['density']:.1f}→{f['last']['density']:.1f}"
                    for f in sorted(summary.values(), key=lambda f: f["name"]))
        + "."
    )

    st.divider()
    st.markdown("#### One bank at a time")

    order = ["JPM", "BAC", "MS", "WFC", "C"]
    avail = [t for t in order if t in summary]
    pick = st.selectbox("Bank", avail, format_func=lambda t: summary[t]["name"])
    f = summary[pick]

    chart = report_mod.HERE / f"coverage_chart_{pick}.svg"
    if chart.exists():
        st.markdown(svg(chart.read_text(encoding="utf-8")), unsafe_allow_html=True)

    # The legend, spelled out. Five bands, five answers to one question — can
    # this number be looked up in what the bank filed, and if not, why not.
    # The first reader to see the chart asked what the colours meant, which is
    # what a legend that needs its author standing next to it looks like.
    bands = report_mod.load_json(f"coverage_bands_{pick}.json", [])
    if bands:
        st.markdown("##### What the five colours mean")
        st.caption(
            f"One question, five answers, for {f['name']}'s FY{f['last']['fy']} "
            "filing: **can this number be looked up in what the bank filed — "
            "and if not, why not?** Only the first is a yes."
        )
        cards = "".join(
            f'<div class="lk"><div class="sw" style="background:{b["light"]}">'
            f'<span style="background:{b["dark"]}"></span></div>'
            f'<div class="bd"><div class="hd"><span>{b["short"]}</span>'
            f'<span class="n">{b["count"]} claims · {b["share"]:.0%}</span></div>'
            f'<p>{b["plain"]}</p></div></div>'
            for b in bands
        )
        st.markdown(svg(f"""<style>
.lkey{{--ln:#e3e3e0;--ink:#1a1a19;--dim:#57606a;
       display:flex;flex-direction:column;gap:8px;margin:.2rem 0 .6rem}}
@media (prefers-color-scheme:dark){{.lkey{{--ln:#2f2f2d;--ink:#e8e8e4;--dim:#a8a8a0}}}}
.lkey .lk{{display:grid;grid-template-columns:7px 1fr;
       border:1px solid var(--ln);border-radius:7px;overflow:hidden}}
.lkey .sw span{{display:none}}
@media (prefers-color-scheme:dark){{.lkey .sw{{background:none!important}}
       .lkey .sw span{{display:block;height:100%}}}}
.lkey .bd{{padding:10px 15px 11px}}
.lkey .hd{{margin:0 0 4px;font:600 13.5px ui-sans-serif,system-ui,sans-serif;
       color:var(--ink);display:flex;justify-content:space-between;
       align-items:baseline;gap:14px}}
.lkey .n{{font-weight:400;font-size:12.5px;color:var(--dim);white-space:nowrap;
       font-variant-numeric:tabular-nums}}
.lkey p{{margin:0;font-size:13px;line-height:1.5;color:var(--ink)}}
</style><div class="lkey">{cards}</div>"""), unsafe_allow_html=True)

    a, b = f["first"], f["last"]
    m = st.columns(3)
    m[0].metric(f"Numeric density, FY{a['fy']}", f"{a['density']:.1f}",
                help="claims per 10,000 characters of Item 7")
    m[1].metric(f"Numeric density, FY{b['fy']}", f"{b['density']:.1f}",
                f"{b['density'] / a['density'] - 1:+.0%}", delta_color="off")
    m[2].metric("Claims about one part of the bank",
                f"{a['segment']:.0%} → {b['segment']:.0%}",
                help=f"FY{a['fy']} to FY{b['fy']}")

    st.divider()
    st.caption(
        "**Limits.** Five filers, one section, fifteen years. MD&A is not "
        "required to be tagged, so an unverifiable claim is unverifiable — not "
        "false. Nothing here measures how often a company is wrong. Three "
        "filings could not be extracted (Citigroup FY2011–12, Wells Fargo "
        "FY2013), and Citigroup's segment claims are undercounted from FY2023, "
        "when it renamed its segments to Services, Markets, Banking and Wealth "
        "— four ordinary words that would drag firmwide sentences into the "
        "band if matched. Full method, retractions and limits in "
        "COVERAGE_STUDY.md."
    )




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
