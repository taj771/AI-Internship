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
from diagram import comparison_svg

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

filings_tab, live_tab, method_tab = st.tabs(["Filing report", "Check a claim", "How it works"])


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

    cols = st.columns(4)
    for col, key in zip(cols, ["SUPPORTED", "DEFINITION_MISMATCH", "CONTRADICTED", "NOT_CHECKABLE"]):
        icon, label, _ = BADGE[key]
        col.metric(f"{icon} {label}", data["counts"].get(key, 0))

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
        flag = "🟢 auto" if row["auto"] else "🟡 review"
        with st.expander(f"{icon}  **{row['figure']}** — {label}  ·  {flag}  ·  {row['id']}"):
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
                st.warning(f"**Needs review** — {row['why']}")
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
            st.error(
                "**No evidence.** The agent answered without consulting any filed "
                "data, on every attempt. Its verdict is not shown, because an "
                "answer with nothing behind it is not a finding."
            )
        else:
            st.code(answer)
            st.caption(f"{evidence['tool_calls']} lookup(s), {evidence['attempts']} attempt(s)")
        with st.expander("Trace — every step the agent took"):
            for step in trace:
                st.text(f"{step.get('kind', '?'):8s} {str(step)[:300]}")


# --- method -----------------------------------------------------------------

with method_tab:
    calibration = report_mod.load_json("calibration.json", {})

    st.markdown("### The difference, in one picture")
    st.markdown(comparison_svg(), unsafe_allow_html=True)
    st.caption(
        "A general model reads the filing as one text, so it can tell you what "
        "the filing **says** — but if the written story and the filed figures "
        "disagree, it has no way to notice, because it never held them apart. "
        "This tool keeps them separate and reports where they diverge."
    )

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

Step 4 is the project. Anyone can build steps 1 to 3.
        """
    )
