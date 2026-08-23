"""
The open-coding bench: read one recorded run at a time and write down what you
think of it.

    .venv/bin/streamlit run open_coding.py

"Open coding" is a term from qualitative research, and the method here is the
one Hamel Husain describes for LLM products: read the raw output, write free
text about what you see, and only afterwards group those notes into categories.
The order is the whole discipline. Deciding the categories first and then
sorting runs into them finds the failures you already suspected and hides the
ones you did not — and the failures you did not suspect are the reason for
looking at all.

So this page offers a notes box and nothing that resembles a score. There is no
suggested label, no list of failure types to pick from, no highlighting of runs
that disagree with expectation. All of those would be answers, and they would be
answers arrived at without reading anything.


WHAT IS SHOWN, AND WHAT IS DELIBERATELY HIDDEN

Shown: the claim, the expected verdict, every step the agent took, and its final
answer. The expected verdict is shown because it was established by hand against
data.sec.gov before any run, so it is evidence rather than opinion — judging
whether an audit was any good without knowing the true figure is not possible.

Hidden behind a click: why each claim was chosen. Every claim in claims.py
carries a note naming the weakness it was written to expose, and reading "this
one tests whether a segment figure is mistaken for the firm total" before
reading the trace tells you what to find. It is available after you have written
your note, which is when it stops being a hint and becomes a check on whether
the claim did what it was meant to.

Not shown at all: whether the verdict matches the expectation. A verdict can
match and the run still be bad — right answer, wrong tag, no evidence, four
wasted lookups — and a green tick against those runs would stop most people
reading them.


NOTES SAVE INTO THE TRACE FILE ITSELF

Not into a separate spreadsheet joined up by row number later. traces.jsonl is
rewritten whole on save, which is safe because saving happens on a button press
and one person is doing this at a time.
"""

import json

import streamlit as st

from claims import CLAIMS
from trace_log import DEFAULT_PATH, load_records, save_records

st.set_page_config(page_title="Open coding — SEC auditor", page_icon="📝", layout="wide")

STEP_STYLE = {
    "THINK": ("🧠", "#5b6abf"),
    "ACT": ("🔧", "#b26a00"),
    "OBSERVE": ("👁", "#1a7f37"),
}

WHY = {c["id"]: c["stresses"] for c in CLAIMS}


records = load_records()

if not records:
    st.error(
        f"No runs recorded yet — {DEFAULT_PATH.name} is empty or missing.\n\n"
        "Run `.venv/bin/python run_batch.py` first."
    )
    st.stop()


# --- Which run is on screen -------------------------------------------------

if "index" not in st.session_state:
    st.session_state.index = 0


def clamp(i: int) -> int:
    return max(0, min(len(records) - 1, i))


annotated = [bool(r.get("your_notes", "").strip()) for r in records]

with st.sidebar:
    st.title("Open coding")
    st.progress(sum(annotated) / len(records))
    st.caption(f"{sum(annotated)} of {len(records)} runs have notes")

    st.divider()
    # A jump list rather than only next/back, because reading is not linear:
    # the fourth run reminds you of something in the first, and having to click
    # back through three screens to check is enough friction to stop people
    # checking.
    labels = [
        f"{'✓' if annotated[i] else '·'} {r['trace_id']}  {r['claim'][:34]}"
        for i, r in enumerate(records)
    ]
    picked = st.radio("Runs", range(len(records)), format_func=lambda i: labels[i],
                      index=st.session_state.index, label_visibility="collapsed")
    if picked != st.session_state.index:
        st.session_state.index = picked
        st.rerun()

    st.divider()
    st.caption(
        "Write what you notice in plain sentences. Categories come later, from "
        "the notes — not the other way round."
    )


record = records[st.session_state.index]


# --- The run ----------------------------------------------------------------

left, right = st.columns([3, 2], gap="large")

with left:
    st.subheader(f"{record['trace_id']} · run {st.session_state.index + 1} of {len(records)}")
    st.markdown(f"### {record['claim']}")

    meta = st.columns(4)
    meta[0].metric("Expected", record["expected_verdict"])
    meta[1].metric("Lookups", record["n_tool_calls"])
    meta[2].metric("Seconds", record["duration_s"])
    meta[3].metric("Model", record["model"])

    if record["error"]:
        st.error(f"This run crashed: {record['error']}")

    st.markdown("**The agent's answer**")
    st.code(record["answer"] or "(no answer — the run produced nothing)",
            language="text", wrap_lines=True)

    st.markdown("**Every step it took**")
    if not record["steps"]:
        st.info("No steps recorded — the agent answered without calling anything.")
    for number, step in enumerate(record["steps"], start=1):
        mark, tint = STEP_STYLE.get(step["kind"], ("•", "#5f6368"))
        st.markdown(
            f"<span style='color:{tint};font-weight:600'>"
            f"{number}. {mark} {step['kind']}</span> "
            f"<span style='color:#888'>· turn {step.get('turn', '?')}</span>",
            unsafe_allow_html=True,
        )
        # The full recorded value, not the shortened display one. On this page
        # the whole point is to see what the agent actually saw, including the
        # tail of a suggestion list that the screen version cuts off.
        full = step.get("response") or step.get("text")
        shown = json.dumps(full, indent=1) if isinstance(full, dict) else (full or step["detail"])
        st.code(shown, language="text", wrap_lines=True)


# --- Your notes -------------------------------------------------------------

with right:
    st.subheader("What do you think of this run?")

    notes = st.text_area(
        "Free text. What is good, what failed, what surprised you.",
        value=record.get("your_notes", ""),
        height=260,
        key=f"notes_{record['trace_id']}",
        placeholder=(
            "e.g. Right verdict, but it only reported the tag that worked and "
            "not the one it tried first. Rule 2 says both."
        ),
    )

    # Binary, and blank until chosen. The assignment is explicit that judgements
    # stay pass/fail rather than becoming a 1-5 scale: a five-point scale invites
    # a 3, and a 3 is a way of not deciding.
    verdict_options = ["", "pass", "fail"]
    pass_fail = st.radio(
        "Overall, did this run do its job?",
        verdict_options,
        index=verdict_options.index(record.get("your_pass_fail", "") or ""),
        format_func=lambda v: {"": "not decided yet", "pass": "pass", "fail": "fail"}[v],
        horizontal=True,
        key=f"pf_{record['trace_id']}",
    )

    label = st.text_input(
        "A short name for the failure, if it failed",
        value=record.get("your_failure_label", ""),
        key=f"label_{record['trace_id']}",
        placeholder="e.g. gave up after one lookup",
        help=(
            "Rough is fine. These get tidied into a taxonomy afterwards, and "
            "two runs you name differently now may turn out to be the same "
            "thing."
        ),
    )

    save, nxt = st.columns(2)

    if save.button("Save", type="primary", use_container_width=True):
        record["your_notes"] = notes
        record["your_pass_fail"] = pass_fail
        record["your_failure_label"] = label
        save_records(records)
        st.success("Saved to traces.jsonl")
        st.rerun()

    if nxt.button("Save and next →", use_container_width=True):
        record["your_notes"] = notes
        record["your_pass_fail"] = pass_fail
        record["your_failure_label"] = label
        save_records(records)
        st.session_state.index = clamp(st.session_state.index + 1)
        st.rerun()

    st.divider()

    with st.expander("Why this claim is in the set — open after writing your note"):
        st.caption(
            "This names the weakness the claim was written to expose. Reading it "
            "first tells you what to look for, which is the opposite of open "
            "coding."
        )
        st.write(WHY.get(record["trace_id"], "—"))
