"""
The labelling bench: forty binary decisions, one question, no scores.

    .venv/bin/streamlit run label_grounding.py

Every judge needs something to be judged against, and that something has to be
made by a person before the judge is built. These labels are the ground truth
the true and false positive rates in judge.py are computed against; if they are
casual, every number downstream is decoration.

The page is therefore stripped to exactly what the question needs: the claim,
everything the tool returned, and the reasoning under scrutiny. The verdict, the
expected verdict, whether the run passed the code checks, and the open-coding
notes are all deliberately absent. None of them bear on whether a sentence is
supported by a tool result, and all of them would colour the judgement — knowing
a run was already marked a failure makes its reasoning read worse.

Keyboard-light on purpose: two buttons, and the next unlabelled run loads
straight away. Forty decisions should take twenty minutes, not an afternoon.
"""

import streamlit as st

from grounding import DEFINITION, evidence, key, load_labels, sample, save_labels

st.set_page_config(page_title="Label grounding", page_icon="🏷️", layout="wide")

records = sample()
labels = load_labels()

if not records:
    st.error("No recorded runs found. Run `.venv/bin/python run_batch.py` first.")
    st.stop()


# --- position ---------------------------------------------------------------

if "i" not in st.session_state:
    st.session_state.i = 0


def first_unlabelled(start: int = 0) -> int:
    for offset in range(len(records)):
        index = (start + offset) % len(records)
        if key(records[index]) not in labels:
            return index
    return start


with st.sidebar:
    st.title("Grounding labels")
    done = sum(1 for r in records if key(r) in labels)
    st.progress(done / len(records))
    st.caption(f"{done} of {len(records)} labelled")

    if st.button("Jump to next unlabelled", use_container_width=True):
        st.session_state.i = first_unlabelled(st.session_state.i)
        st.rerun()

    st.divider()
    choice = st.radio(
        "Runs",
        range(len(records)),
        index=st.session_state.i,
        format_func=lambda i: (
            f"{'✓' if key(records[i]) in labels else '·'} "
            f"{records[i]['trace_id']} · {records[i]['source']}"
        ),
        label_visibility="collapsed",
    )
    if choice != st.session_state.i:
        st.session_state.i = choice
        st.rerun()

    st.divider()
    counts = {"GROUNDED": 0, "UNGROUNDED": 0}
    for entry in labels.values():
        counts[entry["label"]] = counts.get(entry["label"], 0) + 1
    st.caption(f"grounded {counts['GROUNDED']} · ungrounded {counts['UNGROUNDED']}")
    st.caption(
        "A judge is only measurable if both classes appear. If one of these "
        "stays near zero, the rates on the other are computed from a handful "
        "of runs and mean very little."
    )


record = records[st.session_state.i]
existing = labels.get(key(record))


# --- the question -----------------------------------------------------------

st.title("🏷️ Is the reasoning grounded?")

with st.expander("The question, in full — worth rereading every few runs", expanded=not existing):
    st.text(DEFINITION)

st.caption(
    f"Run {st.session_state.i + 1} of {len(records)} · claim {record['trace_id']} · "
    f"instruction: {record['source']}"
)

left, right = st.columns([1, 1], gap="large")

with left:
    st.markdown(f"**The claim being audited**")
    st.info(record["claim"])

    st.markdown("**What the tool returned — all of it**")
    st.code(evidence(record), language="json", wrap_lines=True)

with right:
    st.markdown("**The reasoning under scrutiny**")
    reasoning = (record.get("parsed") or {}).get("REASONING")
    if reasoning:
        st.warning(reasoning)
    else:
        st.error(
            "This run produced no REASONING line at all.\n\n"
            "There is nothing to judge for grounding — label it GROUNDED, since "
            "no unsupported assertion was made. The missing line is already "
            "caught by the answer_format check and should not be counted twice."
        )

    st.markdown("**Your call**")

    note = st.text_input(
        "Which phrase decided it (optional, but useful when the judge disagrees later)",
        value=existing["note"] if existing else "",
        key=f"note_{key(record)}",
    )

    grounded, ungrounded = st.columns(2)

    def record_label(value: str) -> None:
        labels[key(record)] = {
            "key": key(record),
            "trace_id": record["trace_id"],
            "source": record["source"],
            "label": value,
            "note": note,
        }
        save_labels(labels)
        st.session_state.i = first_unlabelled(st.session_state.i + 1)
        st.rerun()

    if grounded.button("✅ GROUNDED", use_container_width=True, type="primary"):
        record_label("GROUNDED")
    if ungrounded.button("⚠️ UNGROUNDED", use_container_width=True):
        record_label("UNGROUNDED")

    if existing:
        st.success(f"Currently labelled **{existing['label']}** — press either button to change it.")

    st.divider()
    st.caption(
        "Deliberately not shown on this page: the verdict, the expected "
        "verdict, whether the run passed the code checks, and the open-coding "
        "notes. None of them bear on whether a sentence is supported by a tool "
        "result, and knowing a run already failed makes its reasoning read "
        "worse than it is."
    )
