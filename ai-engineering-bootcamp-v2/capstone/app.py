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
import tempfile
from pathlib import Path

import streamlit as st

import report as report_mod
from diagram import pipeline_svg

st.set_page_config(page_title="Calibrated Claim Auditor", page_icon="🔍", layout="wide")

BADGE = {
    "SUPPORTED": ("✅", "VERIFIED", "#1a7f37"),
    "CONTRADICTED": ("❌", "CONTRADICTED", "#cf222e"),
    "DEFINITION_MISMATCH": ("⚠️", "BASIS MISMATCH", "#9a6700"),
    "NOT_CHECKABLE": ("❔", "NOT IN XBRL", "#57606a"),
}

st.title("Calibrated Claim Auditor")
st.caption(
    "Checks what a bank wrote about itself in its 10-K against what it filed "
    "with the SEC — and says how much to trust the answer. "
    "**Research tooling, not investment advice.** It says *look at this*, never *do this*."
)

fail_tab, built_tab, filings_tab, live_tab, study_tab = st.tabs(
    ["Where models fail", "What we built", "Filing report", "Check a claim", "The study"])


# --- where models fail ------------------------------------------------------
#
# The problem, before any solution. Rows one and two of the stage-4 grid live
# here; the third row — what fixes it — is held back for "How it works", so a
# reader meets the difficulty before the answer.
#
# A decline is presented as correct behaviour throughout, because it is. The
# failure worth showing is a confident wrong answer, and there are four of them.

with fail_tab:
    st.markdown("### Ask a model which figure a company filed. Watch what happens.")
    st.caption(
        "40 claims where the correct XBRL concept is known independently. "
        "One question each: which tag did the company file this under?"
    )

    grid = report_mod.load_json("stage4_grid.json", {})
    rs = grid.get("results", {})

    def tally(key):
        g = rs.get(key, [])
        ok = sum(1 for x in g if x["correct"])
        dec = sum(1 for x in g if not x["got"] or x["got"] == "UNKNOWN")
        return g, ok, dec, len(g) - ok - dec

    if rs:
        g1, ok1, dec1, wrong1 = tally("no_tools")
        g2, ok2, dec2, wrong2 = tally("tools")

        left, right = st.columns(2)
        with left:
            st.markdown("**A model on its own**")
            st.metric("correct", f"{ok1}/{len(g1)}", f"{ok1/len(g1):.0%}", delta_color="off")
            st.caption(f"{dec1} declined · {wrong1} confidently wrong")
            st.info(
                "**It almost always refuses.** It has no way to know what a "
                "company filed, and it says so. That is the correct answer, and "
                "it is also useless — you still have to go and look."
            )
        with right:
            st.markdown("**The same model, with a live SEC lookup tool**")
            st.metric("correct", f"{ok2}/{len(g2)}", f"{ok2/len(g2):.0%}", delta_color="off")
            st.caption(f"{dec2} declined · {wrong2} confidently wrong")
            st.warning(
                f"**Much better — and still wrong or silent on {len(g2)-ok2} of "
                f"{len(g2)}.** Giving it the data is not the same as it finding "
                "the right number."
            )

        st.divider()
        st.markdown("#### The failures that matter are the confident ones")
        wrong = [x for x in g2 if not x["correct"] and x["got"] and x["got"] != "UNKNOWN"]
        if wrong:
            st.dataframe(
                [{"the concept the sentence meant": w["expected"],
                  "what the model answered": w["got"]} for w in wrong],
                hide_index=True, use_container_width=True)
        st.markdown(
            "Look at the last row. `StockholdersEquity` and "
            "`StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` "
            "are one qualifying phrase apart and hold **different numbers**. "
            "Accounting distinctions live in exactly those words, and a model "
            "reaching for the shorter, more familiar name gets a real figure for "
            "the wrong thing."
        )

    st.divider()
    st.markdown("#### And sometimes the source itself is invented")
    st.markdown(
        "Asked for JPMorgan's CCB noninterest revenue **and the tag it was filed "
        "under**, a general model returned:"
    )
    st.code("VALUE: $17.795 billion      ← correct\n"
            "TAG:   JPM_CCBNoninterestRevenue   ← does not exist", language="text")
    st.error(
        "JPMorgan files **933 tags** across six namespaces. None contains \"CCB\", "
        "and there is no `JPM` namespace. **The number was right and the citation "
        "was fabricated** — which is worse, not better: anyone checking the figure "
        "finds it correct and assumes the source is too. A wrong number gets "
        "caught downstream. A wrong source gets copied into a workpaper."
    )
    st.caption(
        "Measured over 25 claims, a model with no tools invented a tag once and "
        "declined 24 times. Fabrication is rare and not the main failure — being "
        "confidently wrong about *which* concept is."
    )

# --- filing report ----------------------------------------------------------

with filings_tab:
    ticker = st.selectbox("Filing", ["GS", "JPM"], format_func=lambda t:
                          {"GS": "Goldman Sachs — FY2025", "JPM": "JPMorgan Chase — FY2025"}[t])
    data = report_mod.build(ticker)

    if not data["rows"]:
        st.warning(f"No runs recorded for {ticker}. Run `python run_claims.py` first.")
        st.stop()

    st.subheader(f"{data['company']} — FY{data['fiscal_year']} Form 10-K, Item 7")
    st.caption(f"{len(data['rows'])} numeric claims checked against filed XBRL · "
               f"[source filing]({data['source_url']})")

    # Labelled "the tool said", not "VERIFIED", because they are two different
    # questions and the page kept being read as though they were one. A green
    # tick beside a row that also says "needs review" looks like a contradiction
    # until you know the tick is a claim and the review flag is about whether the
    # claim can be trusted. Saying so in the label is cheaper than expecting the
    # reader to hold the distinction.
    st.caption("**What the tool said** — its verdicts, not confirmed results:")
    cols = st.columns(4)
    for col, key in zip(cols, ["SUPPORTED", "DEFINITION_MISMATCH", "CONTRADICTED", "NOT_CHECKABLE"]):
        icon, label, _ = BADGE[key]
        col.metric(f"{icon} said {label}", data["counts"].get(key, 0))

    alpha = data["calibration"].get("alpha", 0.03)
    needs_human = len(data["rows"]) - data["n_auto"]
    if data["n_auto"]:
        st.success(
            f"**Auto-accepted {data['n_auto']} of {len(data['rows'])} at ≤{alpha:.0%} error. "
            f"{needs_human} need a human.**"
        )
    else:
        st.error(
            f"**Auto-accepted 0 of {len(data['rows'])}.** No subset of these verdicts is "
            f"trustworthy at ≤{alpha:.0%} error on the current evidence, so every row below "
            "needs review. This is what the calibration measured, not a placeholder — see "
            "**How it works** for why."
        )

    st.divider()
    for row in data["rows"]:
        icon, label, colour = BADGE.get(row["verdict"], ("•", "UNKNOWN", "#57606a"))
        flag = "🟢 trusted" if row["auto"] else "🟡 unverified"
        with st.expander(
            f"{flag}  ·  **{row['figure']}**  ·  tool said {icon} {label}  ·  {row['id']}"
        ):
            st.markdown("**The sentence, as filed**")
            st.info(row["sentence"].replace("$", "\\$"))
            meta = st.columns(4)
            meta[0].metric("claimed", (row["claimed"] or "—").replace("$", "\\$"))
            meta[1].metric("filed", (row["filed"] or "—").replace("$", "\\$"))
            meta[2].metric("lookups", row["n_lookups"])
            meta[3].metric("section", row["section"] or "firmwide")
            if row["tag"]:
                st.caption(f"XBRL tag: `{row['tag']}`")
            if row["reasoning"]:
                st.write(row["reasoning"].replace("$", "\\$"))
            if not row["auto"]:
                st.warning(
                    f"**The tool's verdict above is unverified.** {row['why']}. "
                    f"It said {label} — that is its opinion, not an established "
                    "result, so read the sentence and the filed figure yourself."
                )
            st.caption(f"[Source filing]({row['source_url']})")


# --- live check -------------------------------------------------------------

with live_tab:
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


# --- the study --------------------------------------------------------------
#
# Fifteen years of JPMorgan filings, measured without a single hand-labelled
# verdict. It is here rather than in a paper because the app is the only thing a
# stranger will open, and a finding that lives only in a repository is a finding
# nobody reads.

with study_tab:
    st.markdown("### How much of a bank's own story can be checked?")
    st.caption(
        "JPMorgan Chase, Item 7, fiscal years 2011–2025 · 6,655 numeric claims · "
        "no model, no agent, no hand-labelled answer key."
    )
    chart = report_mod.HERE / "coverage_chart.svg"
    if chart.exists():
        st.markdown(chart.read_text(encoding="utf-8"), unsafe_allow_html=True)

    c = st.columns(3)
    c[0].metric("Numeric density, FY2011", "16.9", help="claims per 10,000 characters")
    c[1].metric("Numeric density, FY2025", "7.0", "−58%", delta_color="off")
    c[2].metric("Segment-level claims", "3% → 20%", help="FY2011 to FY2025")
    st.markdown(
        "**JPMorgan puts less than half as many numbers in its narrative as it did "
        "in 2011**, normalised for document length. Tag availability never moved "
        "(79–88% throughout). What changed is that the prose moved down to segment "
        "level, where the SEC's JSON API strips the dimensions and cannot follow."
    )

    st.divider()
    st.markdown("### Figures move, and concepts expire")
    panel = report_mod.HERE / "stage3_panel.svg"
    if panel.exists():
        st.markdown(panel.read_text(encoding="utf-8"), unsafe_allow_html=True)
    st.markdown(
        "JPMorgan's filed operating cash flow **changed between filings in four "
        "consecutive years**. And two concepts stop being filed mid-series — "
        "`TierOneRiskBasedCapital` ends at the Basel III transition in 2013. Both "
        "still return HTTP 200 with historical data, so **a dead tag looks exactly "
        "like a live one.**"
    )
    st.info(
        "This corrects the principle the memory layer was built on. *Store the "
        "route, not the answer* was right about figures and incomplete about "
        "routes — **routes expire too**, and unlike a stale figure a dead tag "
        "fails silently."
    )

    st.divider()
    st.caption(
        "**Limits.** One filer, one section, fifteen years. MD&A is not required "
        "to be tagged, so an unverifiable claim is unverifiable — not false. "
        "Nothing here measures how often a company is wrong. Full method, "
        "retractions and limits in COVERAGE_STUDY.md."
    )


# --- method -----------------------------------------------------------------

with built_tab:
    calibration = report_mod.load_json("calibration.json", {})

    st.markdown("### The pipeline")
    st.markdown(pipeline_svg(), unsafe_allow_html=True)
    st.caption(
        "The animated flow shows the **order**, and the order is the point: "
        "retrieval happens before the figure is consulted. Reverse it — let the "
        "number choose the concept — and the agreement you measure afterwards is "
        "the criterion you selected on, not a finding."
    )

    st.divider()
    # --- the live demonstration ------------------------------------------
    #
    # The left column of the diagram claims a general model answers without a
    # verifiable source. This lets a visitor test that claim rather than take
    # it, and the test grades itself: a tag either appears among the ones the
    # company has filed with the SEC or it does not.
    #
    # Two honesties are built into the wording below.
    #
    # First, the bare model refuses most of the time. Measured over 25 claims on
    # 2026-09-02: 24 declined, 1 invented a tag, 0 correct. A demo implying it
    # fails constantly would be a better show and a worse claim, so a refusal is
    # presented as the correct behaviour it is.
    #
    # Second, the tag list is precomputed into data/filed_tags.json rather than
    # fetched. Pulling every fact a company has filed is about eight megabytes
    # per company, which is a slow first click for a visitor and a dependency on
    # a rate-limited API in the one place the page cannot afford to hang.
    st.divider()
    st.markdown("### See it for yourself")
    st.caption(
        "Ask a general model — no tools, no search — for a figure **and the XBRL "
        "tag it was filed under**. The tag is then checked against every tag "
        "that company has actually filed with the SEC. That check is exact: a "
        "tag either exists or it does not."
    )

    EXAMPLES = {
        "JPMorgan — CCB noninterest revenue (2025)": (
            "JPM",
            "JPMorgan Chase reported noninterest revenue of $17.8 billion in its "
            "Consumer & Community Banking segment for fiscal year 2025.",
        ),
        "Goldman — net revenues (2025)": (
            "GS",
            "Goldman Sachs reported net revenues of $58.28 billion for fiscal "
            "year 2025.",
        ),
        "Goldman — Platform Solutions pre-tax loss (2024)": (
            "GS",
            "Goldman Sachs' Platform Solutions segment reported a pre-tax loss "
            "of $997 million for 2024.",
        ),
    }
    choice = st.selectbox("Pick a claim", list(EXAMPLES))
    ticker, example_claim = EXAMPLES[choice]

    if st.button("Ask a general model for the source"):
        import json as _json
        import os as _os
        import re as _re

        from openai import OpenAI

        filed = set(_json.loads(
            (report_mod.HERE / "data" / "filed_tags.json").read_text(encoding="utf-8")
        )[ticker])

        with st.spinner("Asking, with no tools available…"):
            client = OpenAI(api_key=_os.getenv("OPENAI_API_KEY"))
            reply = client.chat.completions.create(
                model=_os.getenv("OPENAI_MODEL", "gpt-4o"),
                temperature=0,
                messages=[{"role": "user", "content": (
                    f"{example_claim}\n\nWhich exact XBRL tag was this filed "
                    "under, and what is the value?\n\nAnswer in exactly this "
                    "shape:\nTAG: <the exact XBRL tag>\nVALUE: <the figure>"
                )}],
            ).choices[0].message.content or ""

        match = _re.search(r"TAG:\s*(\S+)", reply)
        tag = match.group(1).strip().strip(".,") if match else None
        bare = _re.split(r"[:_]", tag)[-1] if tag and _re.search(r"[:_]", tag) else tag

        left, right = st.columns(2)
        with left:
            st.markdown("**A general model**")
            st.code(reply.strip()[:400] or "(no answer)")
            if not tag or tag.upper() == "UNKNOWN":
                st.info(
                    "**It declined.** That is the correct answer — it has no way "
                    "to know, and it said so. Measured over 25 claims, this is "
                    "what it does 24 times out of 25."
                )
            elif tag in filed or bare in filed:
                st.success(f"`{tag}` — this tag is real. {ticker} does file it.")
            else:
                st.error(
                    f"**`{tag}` does not exist.** {ticker} files {len(filed)} tags "
                    "with the SEC and this is not one of them. The figure beside "
                    "it may well be right — which is worse, not better, because a "
                    "correct number lends credibility to a fabricated source."
                )
        with right:
            st.markdown("**This tool**")
            st.code(
                "TAG:  only ever a tag data.sec.gov returned\n"
                "      for this company, this year\n"
                "\n"
                "      If no lookup succeeds, the answer is\n"
                "      NOT_CHECKABLE and it says which tags\n"
                "      it tried.",
            )
            st.success(
                "It cannot invent a citation, because it never composes one. "
                "Every tag it reports came back from the SEC."
            )

    st.divider()
    st.markdown(
        """
### What it does

1. Pulls Item 7 — Management's Discussion and Analysis — from a bank's 10-K
2. Extracts every numeric claim in the prose, one per figure
3. Looks each one up against the company's filed XBRL data at data.sec.gov
4. Decides which verdicts are trustworthy enough to show without review
        """
    )

    st.divider()
    st.markdown("### What the structure is worth")
    st.caption(
        "The same 40 claims from **Where models fail** — same model, same SEC "
        "tool, same budget. The only thing added is step 3: the shortlist of "
        "candidate concepts retrieved for the sentence."
    )
    grid = report_mod.load_json("stage4_grid.json", {})
    rs = grid.get("results", {})
    if rs:
        rows = []
        for key, label in (("no_tools", "model alone, no tools"),
                           ("tools", "model + SEC lookup tool"),
                           ("tools_plus_structure", "+ the retrieved shortlist")):
            g = rs.get(key, [])
            if not g:
                continue
            ok = sum(1 for x in g if x["correct"])
            rows.append({"condition": label, "exact tag": f"{ok}/{len(g)}",
                         "rate": f"{ok/len(g):.0%}",
                         "declined": sum(1 for x in g
                                         if not x["got"] or x["got"] == "UNKNOWN")})
        st.dataframe(rows, hide_index=True, use_container_width=True)
    st.markdown(
        "**Tool access is the largest single effect — 12% to 70%.** A model that "
        "can look things up beats one reasoning from memory by a mile. But one "
        "stage of structure adds twenty points *on top of* full tool access. "
        "Narrowing the field before asking the model to judge is doing work the "
        "model cannot do for itself."
    )
    st.warning(
        "**The 90% is an upper bound.** That condition is handed the retrieved "
        "candidates, and retrieval helped construct the test set, so it is "
        "advantaged by construction. The 12% and the 70% see no retrieval output "
        "and stand on their own."
    )
