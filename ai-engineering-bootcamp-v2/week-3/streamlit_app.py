"""
Browser UI for the SEC Claim Auditor.

Run:  .venv/bin/streamlit run streamlit_app.py

This page deliberately shows the agent's working, not just its answer. A page
that printed only a verdict would be indistinguishable from Week 2's one-shot
RAG service — the assignment asks to see Think, Act and Observe, and the whole
argument that this is an agent rather than a fixed workflow rests on the steps
being visible.

Unlike the Week 2 UI, this page calls the agent directly rather than over HTTP.
There is no API and nothing deployed: the agent runs inside this same program.
That is what the assignment allows when wiring into FastAPI is not worth the
week ("a localhost ADK demo with a clear Loom is enough").
"""

import asyncio

import streamlit as st

from agent import MAX_STEPS, MODEL, audit

st.set_page_config(page_title="SEC Claim Auditor", page_icon="🔎", layout="wide")


# Claims chosen so that a demo can show more than one outcome without the
# audience taking it on trust. The first two are cases we verified by hand
# against data.sec.gov before building anything.
EXAMPLES = {
    "Definition mismatch — real number, different basis": (
        "JPMorgan Chase reported total revenue of $132.3 billion in 2022."
    ),
    "Supported — should match the filing": (
        "Bank of America's total revenue in 2022 was $94.95 billion."
    ),
    "Forces a retry — Goldman does not file under 'Revenues'": (
        "Goldman Sachs had revenue of $47.4 billion in 2022."
    ),
    "Contradicted — invented figure": (
        "JPMorgan Chase earned net income of $200 billion in 2023."
    ),
}

VERDICT_STYLE = {
    "SUPPORTED": ("✅", "#1a7f37"),
    "CONTRADICTED": ("❌", "#c62828"),
    "DEFINITION_MISMATCH": ("⚠️", "#b26a00"),
    "NOT_CHECKABLE": ("❔", "#5f6368"),
}

STEP_STYLE = {
    "THINK": ("🧠", "#5b6abf"),
    "ACT": ("🔧", "#b26a00"),
    "OBSERVE": ("👁", "#1a7f37"),
}


# --- Sidebar ---

st.sidebar.title("Run settings")
st.sidebar.write(f"**Model** `{MODEL}`")
st.sidebar.write(f"**Step cap** {MAX_STEPS}")
st.sidebar.caption(
    "Read from .env. No API key is shown here or held by this page — the key is "
    "read from .env by the agent, which is why this screen is safe to screenshot."
)
st.sidebar.divider()
st.sidebar.caption(
    "Google's free tier allows 20 requests per day **per model**, and one audit "
    "costs three or four. If a run fails on quota, change GEMINI_MODEL in .env "
    "to another model — each has its own separate allowance."
)


# --- Page ---

st.title("🔎 SEC Claim Auditor")
st.caption(
    "Paste a claim containing a number about a public company. The agent decides "
    "what to look up, fetches what the company actually filed with the US SEC, "
    "and returns a verdict. Every step it takes is shown below."
)

choice = st.selectbox("Start from an example, or write your own below", list(EXAMPLES))

claim = st.text_area("Claim to audit", value=EXAMPLES[choice], height=90)

run = st.button("Audit this claim", type="primary")

if run and claim.strip():
    with st.spinner("The agent is working — this takes 10 to 20 seconds…"):
        try:
            # audit() is asynchronous because ADK waits on the network. Streamlit
            # runs this script top to bottom with no event loop of its own, so
            # asyncio.run starts one, runs the audit, and closes it again.
            answer, trace = asyncio.run(audit(claim))
            failure = None
        except Exception as exc:  # noqa: BLE001 - shown to the user, not swallowed
            answer, trace, failure = None, [], exc

    if failure is not None:
        # Quota is by far the most likely failure and has a specific remedy, so
        # it gets its own message rather than a raw traceback the user has to
        # decode mid-demo.
        if "RESOURCE_EXHAUSTED" in str(failure) or "429" in str(failure):
            st.error(
                f"**Out of free quota for `{MODEL}`.** Google allows 20 requests "
                "per day per model. Open `.env`, set `GEMINI_MODEL` to another "
                "model — `gemini-3-flash-preview` or `gemini-flash-lite-latest` "
                "— and rerun."
            )
        else:
            st.error(f"**{type(failure).__name__}**")
        st.code(str(failure)[:1500])

    else:
        verdict = next(
            (
                line.split(":", 1)[1].strip()
                for line in answer.splitlines()
                if line.upper().startswith("VERDICT:")
            ),
            "",
        )
        icon, colour = VERDICT_STYLE.get(verdict, ("•", "#5f6368"))

        left, right = st.columns([1, 1])

        with left:
            st.subheader("Verdict")
            if verdict:
                st.markdown(
                    f"<div style='font-size:1.5rem;font-weight:600;color:{colour}'>"
                    f"{icon} {verdict}</div>",
                    unsafe_allow_html=True,
                )
            st.code(answer, language="text")

        with right:
            st.subheader(f"How it got there — {len(trace)} steps, cap {MAX_STEPS}")
            st.caption(
                "ACT is the model asking for the tool, with the exact arguments it "
                "chose. OBSERVE is what came back from data.sec.gov. Nothing in "
                "the code decides which tag to try; the model does, after reading "
                "the previous result."
            )

            for number, entry in enumerate(trace, start=1):
                mark, tint = STEP_STYLE.get(entry["kind"], ("•", "#5f6368"))
                st.markdown(
                    f"<span style='color:{tint};font-weight:600'>"
                    f"{number}. {mark} {entry['kind']}</span>",
                    unsafe_allow_html=True,
                )
                st.code(entry["detail"], language="text")

            tool_calls = sum(1 for e in trace if e["kind"] == "ACT")
            if tool_calls > 1:
                st.success(
                    f"**{tool_calls} separate lookups.** The second was chosen "
                    "after reading the result of the first — which is the "
                    "difference between an agent and a fixed workflow."
                )

elif run:
    st.warning("Type a claim first.")
