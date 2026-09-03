"""The labelling bench: fifty verdicts established by hand, before any agent run.

    .venv/bin/streamlit run label.py

Phase 2c of CAPSTONE_BUILD_PLAN.md, and the same discipline as Week 4's
label_grounding.py. Every number this project reports is measured against these
fifty rows. If they are casual, everything downstream is decoration.


THE LOOKUP PANEL, AND WHY IT IS NOT CHEATING

The page can call data.sec.gov for you. That is a deterministic HTTP request to
the SEC — sec_tool.lookup_filed_figure with no model anywhere in the path. It is
the same way ground truth was established for Week 4's twenty claims.

What matters is the direction of the dependency: the truth is established from
the SEC, by you, and the agent is scored against it afterwards. If the labels
came from the agent's own answers, the evaluation would be marking its own
homework and every consistent mistake would score as correct.

So the agent has not run yet, and this page shows no verdict, no confidence and
no prediction of any kind. There is nothing here for you to agree with.


WHAT IS DELIBERATELY ABSENT

The extractor's `type` is shown, because you need to know what kind of claim you
are looking at. Its `flags` are not, and neither is the sampling weight — they
are bookkeeping, and a row marked "this is probably a balance-sheet claim" would
nudge you toward looking up a balance-sheet tag before reading the sentence.

Correcting a wrong type is part of the job. The disagreement between what the
extractor guessed and what you decide is itself a number worth reporting.
"""

import json
from pathlib import Path

import streamlit as st

import sec_tool

HERE = Path(__file__).parent
TO_LABEL = HERE / "to_label.jsonl"
LABELS = HERE / "labels.jsonl"

VERDICTS = [
    ("SUPPORTED", "the claimed figure matches what was filed"),
    ("CONTRADICTED", "it disagrees, and no other definition explains the gap"),
    ("DEFINITION_MISMATCH", "the figure is real, but measures something else"),
    ("NOT_CHECKABLE", "no filed figure exists to check it against"),
]

# Offered as starting points, not as answers. The same list the agent's own
# instruction gives it, so the labeller is not working from better hints than
# the thing being evaluated.
COMMON_TAGS = [
    "Revenues",
    "RevenuesNetOfInterestExpense",
    "NetIncomeLoss",
    "ProfitLoss",
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "Deposits",
    "InterestAndDividendIncomeOperating",
    "InterestExpense",
    "NoninterestIncome",
    "NoninterestExpense",
    "ProvisionForLoanLeaseAndOtherLosses",
    "EarningsPerShareDiluted",
]


def money_safe(text: str) -> str:
    """Escape dollar signs so Streamlit does not read them as LaTeX.

    Markdown treats $...$ as a maths formula. A sentence carrying two dollar
    amounts — which is most of this corpus — had everything between them
    swallowed and re-rendered as equations. Every figure on the page is a dollar
    amount, so this is not a cosmetic bug: it corrupts the one thing the
    labeller is reading.
    """
    return text.replace("$", "\\$")


def context_around(record: dict, before: int = 700, after: int = 250) -> str:
    """The filing text surrounding the sentence, for the labeller only.

    Some sentences lose the heading that gives them their subject. "Pre-tax
    earnings were $151 million for 2025" is a firmwide claim or a segment claim
    depending entirely on the heading above it, and the heading is not in the
    sentence.

    This is shown to the labeller and never sent to the agent, and that asymmetry
    is deliberate. Ground truth is what a careful person with the full filing in
    front of them concludes. The agent's task is to do as well as it can with the
    sentence it is given — and where the sentence is genuinely ambiguous, the
    correct answer is NOT_CHECKABLE. Giving the agent the context too would
    quietly delete that whole category of failure from the evaluation.
    """
    path = HERE / "data" / f"{record['ticker']}-2025-mdna.txt"
    if not path.exists() or record.get("char_offset") is None:
        return ""
    text = path.read_text(encoding="utf-8")
    start = max(0, record["char_offset"] - before)
    end = min(len(text), record["char_offset"] + after)
    return " ".join(text[start:end].split())


def load_records() -> list[dict]:
    if not TO_LABEL.exists():
        return []
    return [json.loads(line) for line in TO_LABEL.open(encoding="utf-8")]


def load_labels() -> dict[str, dict]:
    if not LABELS.exists():
        return {}
    return {row["id"]: row for row in map(json.loads, LABELS.open(encoding="utf-8"))}


def save_label(row: dict) -> None:
    """Rewrite the whole file on every save.

    Fifty rows, so the cost is nothing, and appending would leave two entries
    for a claim whose verdict was revised — with the stale one first. Losing an
    afternoon of labelling to a duplicate-key bug is not a risk worth taking to
    save a few bytes.
    """
    labels = load_labels()
    labels[row["id"]] = row
    with LABELS.open("w", encoding="utf-8") as fh:
        for key in sorted(labels):
            fh.write(json.dumps(labels[key], ensure_ascii=False) + "\n")


st.set_page_config(page_title="Label claims", page_icon="🏷️", layout="wide")

records = load_records()
if not records:
    st.error("No claims to label. Run `python select_labelling_set.py` first.")
    st.stop()

labels = load_labels()

if "i" not in st.session_state:
    st.session_state.i = 0


def first_unlabelled(start: int = 0) -> int:
    for offset in range(len(records)):
        index = (start + offset) % len(records)
        if records[index]["id"] not in labels:
            return index
    return start


# --- sidebar ----------------------------------------------------------------

with st.sidebar:
    st.title("Ground truth")
    done = sum(1 for r in records if r["id"] in labels)
    st.progress(done / len(records))
    st.caption(f"{done} of {len(records)} labelled")

    if st.button("Next unlabelled", use_container_width=True, type="primary"):
        st.session_state.i = first_unlabelled(st.session_state.i)
        st.rerun()

    left, right = st.columns(2)
    if left.button("← Prev", use_container_width=True):
        st.session_state.i = (st.session_state.i - 1) % len(records)
        st.rerun()
    if right.button("Next →", use_container_width=True):
        st.session_state.i = (st.session_state.i + 1) % len(records)
        st.rerun()

    st.divider()
    st.caption("**Progress by stratum**")
    for name in ("STATED", "DERIVED", "BALANCE", "UNCHECKABLE"):
        pool = [r for r in records if r["stratum"] == name]
        n = sum(1 for r in pool if r["id"] in labels)
        st.caption(f"{name.title():13s} {n}/{len(pool)}")

    st.divider()
    st.caption(
        "Verdicts are established here **before** the agent runs. "
        "The lookup panel calls data.sec.gov directly — no model is involved."
    )

record = records[st.session_state.i]
existing = labels.get(record["id"], {})

# --- the claim --------------------------------------------------------------

st.subheader(f"{record['label_seq']} of {len(records)} — {record['id']}")
section = record.get("section")
st.caption(
    f"{record['company']} · fiscal year {record['fiscal_year']} · "
    f"section: **{section or 'firmwide'}** · "
    f"extractor called this **{record['type']}**  (correct it below if it is wrong)"
)
if section:
    st.caption(
        f"From the nearest preceding section header, {record.get('section_distance', -1):,} "
        "characters back. Included in what the agent is sent. If the surrounding "
        "text below says otherwise, say so in the note."
    )

st.markdown("**The figure to check**")
st.header(money_safe(record["figure"]))

st.markdown("**The sentence, as filed**")
st.info(money_safe(record["raw_sentence"]))

context = context_around(record)
if context:
    with st.expander("Surrounding text in the filing — read this to find the subject", expanded=True):
        st.caption(
            "Shown to you, never sent to the agent. Use it to work out whose "
            "figure this is — firmwide, or one segment. If the sentence alone "
            "cannot be pinned to a company, an item and a year, that ambiguity "
            "is itself the finding."
        )
        st.text(context)

with st.expander("What the agent will be sent"):
    st.write(money_safe(record["claim"]))
st.caption(f"Source: [{record['source_document']}]({record['source_url']})")

st.divider()

# --- prepared evidence ------------------------------------------------------
#
# Assembled by prepare_evidence.py: keyword matching against XBRL tag labels and
# arithmetic against data.sec.gov, with no model at any point. It narrows nine
# thousand tags to a shortlist and does the subtraction. It does not, and must
# not, suggest a verdict — that judgement is the one thing this whole exercise
# exists to obtain from a person.

evidence = record.get("evidence")
if evidence:
    st.markdown("### Evidence, prepared for you")
    st.caption(
        "Candidate tags with annual data for this year, ranked by how "
        "distinctively their label matches the sentence. Values are live from "
        "data.sec.gov. **A shortlist, not an answer** — the top row is often "
        "wrong, and the right tag may not be here at all."
    )

    if evidence.get("claimed_usd") is None:
        st.warning(
            "**Percentage.** No company files a percentage. To check this you "
            "need the figure for both years and to do the arithmetic yourself."
        )
    elif evidence.get("near_match"):
        st.success(
            "A candidate is within 1% of the claim: "
            + ", ".join(f"`{t}`" for t in evidence["near_match"])
            + " — confirm it measures the right thing before accepting it."
        )

    cands = evidence.get("candidates") or []
    if cands:
        st.dataframe(
            [
                {
                    "tag": c["tag"],
                    "value": f"${c['value'] / 1e9:,.2f}B",
                    "period": c["period"],
                    "vs claim": (
                        f"{(c['value'] - evidence['claimed_usd']) / evidence['claimed_usd'] * 100:+,.1f}%"
                        if evidence.get("claimed_usd")
                        else "—"
                    ),
                    "restated": "yes" if c["restated"] else "",
                    "matched on": ", ".join(c["matched"]),
                }
                for c in cands
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info(
            "No us-gaap tag with annual data for this year matches the sentence "
            "distinctively. That usually means a company-specific line item, a "
            "ratio, or a narrative breakdown — none of which are tagged."
        )

    unfiled = evidence.get("matched_but_not_filed") or []
    if unfiled:
        with st.expander(f"Right concept, no data for {record['fiscal_year']} ({len(unfiled)})"):
            st.caption(
                "Tags whose label matches this sentence well, that this company "
                "has filed annually at some point — but not for this year. "
                "Useful because “the concept is not filed for this year” is a "
                "different finding from “no tag covers this at all.”"
            )
            for u in unfiled:
                st.write(f"`{u['tag']}` — filed for {', '.join(u['years_filed'])}")

    st.caption("Copy a tag into the lookup box below to see its full record.")

st.divider()

# --- the lookup -------------------------------------------------------------

st.markdown("### Look it up")
st.caption(
    "Direct call to data.sec.gov. A failed tag returns the tags this company "
    "does file — read that list rather than guessing again."
)

col_tag, col_year, col_go = st.columns([3, 1, 1])
tag = col_tag.text_input(
    "XBRL tag",
    key=f"tag_{record['id']}",
    placeholder="e.g. RevenuesNetOfInterestExpense",
)
year = col_year.number_input(
    "Year", min_value=2015, max_value=2026, value=int(record["fiscal_year"]), step=1
)
col_go.markdown("<br>", unsafe_allow_html=True)
go = col_go.button("Look up", use_container_width=True)

st.caption("Common starting points: " + " · ".join(f"`{t}`" for t in COMMON_TAGS[:8]))

if go and tag.strip():
    with st.spinner(f"data.sec.gov — {record['ticker']} / {tag} / {year}"):
        result = sec_tool.lookup_filed_figure(record["ticker"], tag.strip(), int(year))

    if result["status"] == "found":
        st.success(f"**{result['value_readable']}**  ·  {result['period']}")
        cols = st.columns(4)
        cols[0].metric("exact", f"{result['value_usd']:,}")
        cols[1].metric("kind", result.get("fact_kind", "-"))
        cols[2].metric("form", result.get("form", "-"))
        cols[3].metric("filed", result.get("filed", "-"))
        if result.get("restated"):
            st.warning(
                "**Restated.** This figure was reported differently in different "
                f"filings: {', '.join(result['all_reported_values'])}. The value "
                "above is from the most recent. Note this in the comment — a "
                "prior-year comparison against a restated figure is exactly where "
                "management's prose and the current filing legitimately diverge."
            )
        st.caption(f"[verify on data.sec.gov]({result['source']})")
    else:
        st.error(f"{result['status']} — {result.get('detail', '')}")
        if result.get("tags_this_company_does_file"):
            st.write("**Tags this company does file:**")
            st.code("\n".join(result["tags_this_company_does_file"]))
        if result.get("annual_years_available_for_this_tag"):
            st.write("Years available for this tag:")
            st.code(str(result["annual_years_available_for_this_tag"]))

st.divider()

# --- the verdict ------------------------------------------------------------

st.markdown("### Your verdict")

with st.form(key=f"verdict_{record['id']}"):
    labels_list = [v for v, _ in VERDICTS]
    current = existing.get("label_verdict")
    verdict = st.radio(
        "Verdict",
        labels_list,
        index=labels_list.index(current) if current in labels_list else None,
        format_func=lambda v: f"{v} — {dict(VERDICTS)[v]}",
    )

    true_type = st.selectbox(
        "Claim type — correct the extractor if it got this wrong",
        ["STATED", "DERIVED", "BALANCE", "SEGMENT", "RATIO", "NON_GAAP", "NOT_A_CLAIM"],
        index=(
            ["STATED", "DERIVED", "BALANCE", "SEGMENT", "RATIO", "NON_GAAP", "NOT_A_CLAIM"].index(
                existing.get("label_type") or record["type"]
            )
            if (existing.get("label_type") or record["type"])
            in ["STATED", "DERIVED", "BALANCE", "SEGMENT", "RATIO", "NON_GAAP", "NOT_A_CLAIM"]
            else 0
        ),
    )

    c1, c2 = st.columns(2)
    true_figure = c1.text_input(
        "The filed figure", value=existing.get("label_true_figure") or "",
        placeholder="e.g. $58.28B, or leave blank if none exists",
    )
    true_tag = c2.text_input(
        "The XBRL tag you found it under", value=existing.get("label_xbrl_tag") or "",
        placeholder="e.g. RevenuesNetOfInterestExpense",
    )

    note = st.text_area(
        "Note — why, especially for DEFINITION_MISMATCH and NOT_CHECKABLE",
        value=existing.get("label_note") or "",
        placeholder="What made this hard? What did you try? Anything the agent will plausibly get wrong here?",
        height=90,
    )

    saved = st.form_submit_button("Save and go to next unlabelled", type="primary")

if saved:
    if verdict is None:
        st.error("Pick a verdict before saving.")
    else:
        row = dict(record)
        row["label_verdict"] = verdict
        row["label_type"] = true_type
        row["label_true_figure"] = true_figure.strip() or None
        row["label_xbrl_tag"] = true_tag.strip() or None
        row["label_note"] = note.strip() or None
        row["label_source_url"] = (
            f"https://data.sec.gov/api/xbrl/companyconcept/CIK{record.get('cik', '')}"
            f"/us-gaap/{true_tag.strip()}.json"
            if true_tag.strip()
            else None
        )
        save_label(row)
        st.session_state.i = first_unlabelled(st.session_state.i + 1)
        st.rerun()

if existing:
    st.caption(
        f"Already labelled **{existing['label_verdict']}**. Saving again overwrites it."
    )
