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
import re
import tempfile
from pathlib import Path

import streamlit as st

import report as report_mod
from diagram import pipeline_svg

st.set_page_config(page_title="Calibrated Claim Auditor", page_icon="🔍", layout="wide")


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

st.title("Calibrated Claim Auditor")
st.caption(
    "Checks what a bank wrote about itself in its 10-K against what it filed "
    "with the SEC — and says how much to trust the answer. "
    "**Research tooling, not investment advice.** It says *look at this*, never *do this*."
)

# Order is the argument: the problem, the method, the method running on one
# filing, the same method browsable one concept at a time, then how much of any
# filing can be confirmed at all, and finally how the confirmed figures move
# between filings. Coverage before temporal — the scale of the gap has to land
# before its behaviour over time means anything.
fail_tab, built_tab, filings_tab, match_tab, study_tab, time_tab = st.tabs(
    ["Where models fail", "What we built", "Filing report",
     "Does the number match?", "How much can be checked", "How figures change"])


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

    data = report_mod.load_json("browse.json", {})
    if not data:
        st.info("Run `python3 browse.py` to build browse.json.")
    else:
        st.markdown("### What Item 7 says, against what was filed")
        st.caption(
            "655 numeric claims from JPMorgan's Item 7, fiscal years 2011–2025. "
            "Each was pinned to a filed concept **by its wording alone** — the "
            "figure was never consulted when choosing the concept, so a gap "
            "here is a measurement rather than a leftover."
        )

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
                st.caption(
                    "The row above is what this pipeline says. These are what a "
                    "general model says when asked the same question — nothing "
                    "precomputed, run live against `data.sec.gov` when you press "
                    "the button."
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
                    from stage4_grid import ASK, no_tools, parse, with_agent

                    base = ASK.format(sentence=r["sentence"][:600],
                                      figure=r["figure"], company="JPMorgan Chase",
                                      fy=r["fy"])
                    with st.spinner("Two model runs against data.sec.gov…"):
                        try:
                            client = OpenAI(api_key=_os.getenv("OPENAI_API_KEY"))
                            got = [("Model alone, no tools",
                                    parse(no_tools(client, base)),
                                    "It cannot look anything up. A refusal here "
                                    "is the correct answer — and still leaves "
                                    "you to go and look.")]
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
                    # Two axes, because they come apart and the interesting
                    # cases are where they do. Asked about JPMorgan's FY2012
                    # allowance, the tool-equipped model answered
                    # LoansAndLeasesReceivableAllowance rather than the pinned
                    # FinancingReceivableAllowanceForCreditLosses — and both
                    # tags hold $21.94B. Scoring the tag alone would have marked
                    # a right answer wrong.
                    same_tag = bool(tag) and tag.lower() == c["tag"].lower()
                    declined = not tag or tag.upper() == "UNKNOWN"
                    val = model_value(value)
                    same_val = (val is not None and r["filed"]
                                and abs(val - r["filed"]) / abs(r["filed"]) <= 0.015)
                    if declined:
                        mark, verdict_word = "❔", "declined"
                    elif same_tag and same_val:
                        mark, verdict_word = "✅", "same concept, same number"
                    elif same_val:
                        mark, verdict_word = "🟰", "different concept, same number"
                    else:
                        mark, verdict_word = "❌", "different number"

                    st.markdown(f"{mark} **{label}** — {verdict_word}")
                    g = st.columns(2)
                    g[0].caption("tag it answered")
                    g[0].code(tag or "— none —", language="text")
                    g[1].caption("value it answered")
                    g[1].code((value or "— none —")[:60], language="text")
                    st.caption(note)

                if cache.get(r["id"]):
                    st.caption(
                        "**🟰 is not a failure.** JPMorgan files some figures "
                        "under more than one tag, and a model naming a different "
                        "one that holds the same value has found the number by "
                        "another route. **❌ is the failure that matters** — a "
                        "confident answer with a different figure, which is real "
                        "and belongs to something else."
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
            "bugs under JPMorgan's name."
        )


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
        st.markdown(svg(chart.read_text(encoding="utf-8")), unsafe_allow_html=True)

    # The legend, spelled out.
    #
    # The five bands were labelled "Checkable / No tag filed / Comparison /
    # Segment / Ratio / non-GAAP" and the first reader to see the chart asked
    # what those meant. They are five answers to one question — can this number
    # be looked up in what the bank filed, and if not, why not — and four of
    # them are different reasons for no, with different fixes. A legend has room
    # for a phrase; this is where the sentence goes.
    #
    # Rendered from coverage_bands.json rather than typed here, so the counts
    # cannot drift from the figure above them. Routed through svg() for the same
    # reason every drawing is: the examples quote dollar figures, and st.markdown
    # reads $...$ as LaTeX.
    bands = report_mod.load_json("coverage_bands.json", [])
    if bands:
        st.markdown("#### What the five colours mean")
        st.caption(
            "One question, five answers: **can this number be looked up in "
            "what the bank filed — and if not, why not?** Only the first is a "
            "yes. The other four are different reasons for no, and they do not "
            "have the same fix."
        )
        cards = "".join(
            f'<div class="lk"><div class="sw" style="background:{b["light"]}">'
            f'<span style="background:{b["dark"]}"></span></div>'
            f'<div class="bd"><div class="hd"><span>{b["short"]}</span>'
            f'<span class="n">{b["count"]} claims · {b["share"]:.0%}</span></div>'
            f'<p>{b["plain"]}</p>'
            f'<blockquote>“…{b["example"]}”</blockquote></div></div>'
            for b in bands
        )
        st.markdown(svg(f"""<style>
.lkey{{--ln:#e3e3e0;--ink:#1a1a19;--dim:#57606a;--q:#f4f4f2;
       display:flex;flex-direction:column;gap:9px;margin:.2rem 0 .6rem}}
@media (prefers-color-scheme:dark){{.lkey{{--ln:#2f2f2d;--ink:#e8e8e4;
       --dim:#a8a8a0;--q:#242423}}}}
.lkey .lk{{display:grid;grid-template-columns:7px 1fr;
       border:1px solid var(--ln);border-radius:7px;overflow:hidden}}
.lkey .sw span{{display:none}}
@media (prefers-color-scheme:dark){{.lkey .sw{{background:none!important}}
       .lkey .sw span{{display:block;height:100%}}}}
.lkey .bd{{padding:11px 15px 12px}}
.lkey .hd{{margin:0 0 5px;font:600 14px ui-sans-serif,system-ui,sans-serif;
       color:var(--ink);display:flex;justify-content:space-between;
       align-items:baseline;gap:14px}}
.lkey .n{{font-weight:400;font-size:12.5px;color:var(--dim);white-space:nowrap;
       font-variant-numeric:tabular-nums}}
.lkey p{{margin:0;font-size:13.5px;line-height:1.55;color:var(--ink)}}
.lkey blockquote{{margin:8px 0 0;padding:7px 11px;background:var(--q);
       border-radius:5px;font-size:12.5px;line-height:1.5;color:var(--dim)}}
</style><div class="lkey">{cards}</div>"""), unsafe_allow_html=True)

    st.divider()
    c = st.columns(3)
    c[0].metric("Numeric density, FY2011", "16.9", help="claims per 10,000 characters")
    c[1].metric("Numeric density, FY2025", "7.0", "−58%", delta_color="off")
    c[2].metric("Claims about one part of the bank", "3% → 19%",
                help="29 of 896 claims in FY2011; 53 of 278 in FY2025")
    st.markdown(
        "**JPMorgan puts less than half as many numbers in its narrative as it did "
        "in 2011**, normalised for document length. Tag availability never moved "
        "(79–88% throughout). What changed is that the prose moved down to segment "
        "level — the orange band, 3% of claims in 2011 and 19% today — where "
        "the SEC's JSON API strips the dimensions and cannot follow."
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
    st.markdown(svg(pipeline_svg()), unsafe_allow_html=True)
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
