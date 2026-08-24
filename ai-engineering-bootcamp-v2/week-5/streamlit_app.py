"""
Browser UI for the SEC Claim Auditor, with durable memory.

Run:  .venv/bin/streamlit run streamlit_app.py

This page deliberately shows the agent's working, not just its answer. A page
that printed only a verdict would be indistinguishable from Week 2's one-shot
RAG service — the assignment asks to see Think, Act and Observe, and the whole
argument that this is an agent rather than a fixed workflow rests on the steps
being visible.

Unlike the Week 2 UI, this page calls the agent directly rather than over HTTP.
The agent runs inside this same program, which is why this service holds an API
key and week 2's UI did not.

WEEK 5 — what this page has to demonstrate, and what it honestly cannot

The Memory tab shows every stored fact with where it came from and whether it is
trusted, lets a preference be saved, and lets a false fact be planted so the
refusal can be watched rather than described.

What a button on this page cannot do is restart the process. "Start a new
session" clears Streamlit's session state, which proves the page is not caching
anything — a real and necessary check, since a page that simply kept the last
answer in a variable would look exactly like memory. But the process keeps
running, so it does not by itself prove the fact reached a disk.

That proof lives in two places that do restart: `remember.py`, where every
command is a separate process, and `test_memory.py`, which spawns a subprocess,
lets it die, and reads the fact back. The page says so rather than letting the
weaker demonstration stand in for the stronger one.
"""

import asyncio

import streamlit as st

import memory_gate
from agent import DEFAULT_USER, MAX_STEPS, MODEL, PROVIDER, audit, get_store
from memory import GLOBAL_SCOPE, KIND_PREFERENCE, KIND_TAG

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
    # Week 5. RECALL and LEARN are trace steps like any other, which is why they
    # appear here and nowhere else in this file — the existing loop that renders
    # a trace renders them without knowing what they are.
    "RECALL": ("🧷", "#7b3fa0"),
    "LEARN": ("📌", "#7b3fa0"),
}

SOURCE_LABEL = {
    "tool_observation": "the SEC returned this during an audit",
    "user_stated_verified": "a person said it, data.sec.gov confirmed it",
    "user_stated_unverified": "a person said it and nothing confirmed it",
    "user_preference": "a person's presentation preference",
}


# --- This run, drawn ---------------------------------------------------------
#
# The generic flow diagram in the "How it works" tab shows the decision
# structure. This one shows a single run through it, with the real arguments and
# the real results in the boxes — the trace, laid out rather than listed.
#
# The list and the graph are built from the same `trace`, so they cannot
# disagree. That matters more than it sounds: a hand-drawn diagram of a system
# drifts from the system, and a diagram that drifts is worse than none, because
# it is believed.


def _dot(text: str, width: int = 34) -> str:
    """Escape a string for a DOT label and wrap it to a readable width.

    Graphviz has no line wrapping of its own — a long label produces one very
    wide box that pushes the whole graph off the page. Wrapping has to happen
    here, before the label is written.
    """
    text = str(text).replace("\\", "\\\\").replace('"', '\\"')
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width and current:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return "\\n".join(lines) or " "


def run_graph_dot(claim: str, trace: list[dict], verdict: str) -> str:
    """Draw one audit as a graph, using what actually happened in it."""
    acts = [e for e in trace if e["kind"] == "ACT"]
    observes = [e for e in trace if e["kind"] == "OBSERVE"]
    recalls = [e for e in trace if e["kind"] == "RECALL"]
    learns = [e for e in trace if e["kind"] == "LEARN"]

    ok = 'style=filled fillcolor="#e6f4ea" color="#1a7f37" fontcolor="#12492a"'
    warn = 'style=filled fillcolor="#fdecea" color="#c62828" fontcolor="#7d1d1d"'
    mem = 'style=filled fillcolor="#f3e8fb" color="#7b3fa0" fontcolor="#43205c"'
    tool = 'style=filled fillcolor="#fff4e0" color="#b26a00" fontcolor="#5c3600"'
    plain = 'style=filled fillcolor="#eef1f8" color="#9aa4bf" fontcolor="#20242e"'

    _, colour = VERDICT_STYLE.get(verdict, ("•", "#5f6368"))
    verdict_style = (
        f'style=filled fillcolor="#ffffff" color="{colour}" '
        f'fontcolor="{colour}" penwidth=2'
    )

    lines = [
        "digraph run {",
        "  rankdir=TB; bgcolor=transparent;",
        '  node [shape=box fontname="Helvetica" fontsize=10 margin="0.16,0.10"];',
        '  edge [fontname="Helvetica" fontsize=8 color="#9aa4bf"];',
        f'  claim [label="CLAIM\\n{_dot(claim, 40)}" {plain}];',
    ]

    previous = "claim"

    # RECALL — present only when memory had something to say about this claim.
    if recalls:
        facts = recalls[0].get("facts", [])
        detail = _dot(recalls[0]["detail"], 40)
        lines.append(
            f'  recall [label="RECALL — from an earlier session\\n{detail}\\n'
            f'({len(facts)} fact(s), read from the store)" {mem}];'
        )
        lines.append(f"  {previous} -> recall;")
        previous = "recall"
    else:
        lines.append(
            f'  norecall [label="RECALL\\nnothing stored about this company\\n'
            f'instruction unchanged" {plain}];'
        )
        lines.append(f"  {previous} -> norecall;")
        previous = "norecall"

    # One ACT/OBSERVE pair per lookup, in trace order. Pairing by position
    # rather than by turn, for the reason set out in memory_gate: this agent
    # often fires two tags in the same turn.
    for index, (act, observe) in enumerate(zip(acts, observes), start=1):
        args = act.get("args") or {}
        response = observe.get("response") if isinstance(observe.get("response"), dict) else {}
        status = response.get("status", "?")
        found = status == "found"

        result = (
            f"{status} — {response.get('value_readable', '')}"
            if found
            else f"{status}\\n{_dot(response.get('detail', ''), 34)}"
        )

        lines += [
            f'  act{index} [label="ACT {index}\\n{_dot(args.get("xbrl_tag", "?"))}\\n'
            f'FY{args.get("fiscal_year", "?")}  ·  {_dot(args.get("company", "?"))}" {tool}];',
            f'  obs{index} [label="OBSERVE {index}\\n{result}" '
            f'{ok if found else warn}];',
            f"  {previous} -> act{index};",
            f"  act{index} -> obs{index};",
        ]
        previous = f"obs{index}"

    lines.append(
        f'  verdict [label="VERDICT\\n{verdict or "—"}\\n'
        f'figure fetched live, never remembered" {verdict_style}];'
    )
    lines.append(f"  {previous} -> verdict;")

    # LEARN — the write gate's answer for this run.
    if learns:
        lines += [
            f'  learn [label="LEARN — written to the store\\n'
            f'{_dot(learns[0]["detail"], 40)}" {mem}];',
            "  verdict -> learn;",
            '  store [label="memory_facts (Postgres)" shape=cylinder '
            f"{mem}];",
            "  learn -> store;",
            '  store -> next [label="  read by the NEXT session" style=dashed];',
            '  next [label="a later audit\\nnaming this company" '
            f"{plain}];",
        ]
    else:
        wasted = sum(
            1
            for e in observes
            if isinstance(e.get("response"), dict)
            and e["response"].get("status") != "found"
        )
        # Stated as what the gate did, not as a guess about why. The common
        # case is the first one; the second can happen when the failure and the
        # success were for different metrics, so no single tag was established.
        why = (
            "no lookup had to recover —\\nthe first guess worked"
            if wasted == 0
            else "a lookup failed, but the gate\\nestablished no tag to keep"
        )
        lines += [
            f'  nolearn [label="WRITE GATE: nothing stored\\n{why}" {plain}];',
            "  verdict -> nolearn;",
        ]

    lines.append("}")
    return "\n".join(lines)


# --- Sidebar ---

st.sidebar.title("Run settings")
st.sidebar.write("**Framework** Google ADK")
st.sidebar.write(f"**Engine** `{PROVIDER}` · `{MODEL}`")
st.sidebar.write(f"**Step cap** {MAX_STEPS}")
st.sidebar.write("**Tool** `data.sec.gov` — free, public, no key")

# Which store this run is actually using, named on screen. A page demonstrating
# durable memory that cannot show *which* store it wrote to is asking to be taken
# on trust — and on Render's free plan the difference between Postgres and a
# local file is the difference between remembering and not.
store = get_store()
st.sidebar.write(f"**Memory** `{store.backend}`")
if not store.is_postgres:
    st.sidebar.warning(
        "SQLite on local disk. Fine here; on Render's free plan the disk is "
        "wiped when the service sleeps, so a deployed copy needs DATABASE_URL.",
        icon="⚠️",
    )

st.sidebar.caption(
    "All read from .env. No API key or connection string appears on this screen, "
    "which is why it is safe to screenshot."
)
st.sidebar.divider()
st.sidebar.caption(
    "The framework is Google ADK either way — same Agent, same tools, same "
    "trace. The engine is switchable because Gemini's free tier allows twenty "
    "requests a day per model, roughly five audits, which a public page "
    "exhausts quickly. Set LLM_PROVIDER=gemini in .env to run it on Google's "
    "own stack."
)


# --- Page ---

st.title("🔎 SEC Claim Auditor")
st.caption(
    "Paste a claim containing a number about a public company. The agent decides "
    "what to look up, fetches what the company actually filed with the US SEC, "
    "and returns a verdict. Every step it takes is shown below — including what "
    "it remembered from earlier sessions, and what it learned in this one."
)

audit_tab, memory_tab, flow_tab = st.tabs(
    ["Audit a claim", "🧠 Memory", "🔀 How it works"]
)

with audit_tab:
    choice = st.selectbox(
        "Start from an example, or write your own below", list(EXAMPLES)
    )

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
                # Kept so the "How it works" tab can light up the path this run
                # actually took. In session state rather than in the store,
                # because it is this page's record of the last thing it did —
                # context, not memory — and "Start a new session" should clear
                # it along with everything else the page is holding.
                st.session_state["last_trace"] = trace
                st.session_state["last_claim"] = claim
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
                st.code(answer, language="text", wrap_lines=True)

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
                    # wrap_lines matters more than it looks. Without it the ACT
                    # lines scroll off to the right at exactly the wrong point:
                    # xbrl_tag='Reve… — hiding whether the model asked for Revenues
                    # or RevenuesNetOfInterestExpense, which is the one detail the
                    # whole trace exists to show.
                    st.code(entry["detail"], language="text", wrap_lines=True)

                tool_calls = sum(1 for e in trace if e["kind"] == "ACT")
                if tool_calls > 1:
                    st.success(
                        f"**{tool_calls} separate lookups.** The second was chosen "
                        "after reading the result of the first — which is the "
                        "difference between an agent and a fixed workflow."
                    )

            # The same run, drawn. Full width rather than inside the trace
            # column, because a graph squeezed into half a screen is a graph
            # nobody reads. Expanded by default: this is the thing worth
            # screenshotting, and a collapsed panel does not appear in a
            # screenshot at all.
            st.divider()
            with st.expander("🔀 This run, drawn as a graph", expanded=True):
                st.caption(
                    "Generated from the trace above, not drawn by hand — so the "
                    "boxes carry the tags this run actually asked for and the "
                    "results that actually came back. The two cannot disagree."
                )
                st.graphviz_chart(
                    run_graph_dot(claim, trace, verdict), use_container_width=True
                )

    elif run:
        st.warning("Type a claim first.")


# --- Memory tab --------------------------------------------------------------
#
# Everything below reads and writes the same store the agent uses. Nothing here
# is a display copy: the table is the table, and a fact deleted here is gone from
# the next audit.

with memory_tab:
    st.subheader("What this agent remembers between sessions")
    st.caption(
        "Memory is not a longer chat history. The conversation above is thrown "
        "away when the audit returns — that is context. These rows live in a "
        f"store outside the program (`{store.backend}`) and are read back by "
        "runs that share nothing with the run that wrote them."
    )

    facts = store.facts_for(DEFAULT_USER)
    usable = [f for f in facts if f.is_usable]
    refused = [f for f in facts if not f.is_usable]

    counts = st.columns(3)
    counts[0].metric("Facts in use", len(usable))
    counts[1].metric("Refused (quarantined)", len(refused))
    counts[2].metric("Times recalled", sum(f.hits for f in facts))

    if not facts:
        st.info(
            "Memory is empty. Audit the Goldman Sachs example on the other tab — "
            "it has to try `Revenues`, be told it is not filed, and recover. That "
            "recovery is the only thing worth remembering, so it is the only "
            "thing that gets written."
        )

    for fact in facts:
        border = "#1a7f37" if fact.is_usable else "#c62828"
        badge = "trusted" if fact.is_usable else "QUARANTINED — never shown to the agent"

        with st.container(border=True):
            head, actions = st.columns([6, 1])
            with head:
                st.markdown(
                    f"<span style='color:{border};font-weight:600'>{badge}</span> "
                    f"&nbsp;·&nbsp; <code>{fact.kind}</code> "
                    f"&nbsp;·&nbsp; recalled {fact.hits}×",
                    unsafe_allow_html=True,
                )
                st.markdown(f"**{fact.one_line()}**")
                st.caption(
                    f"{SOURCE_LABEL.get(fact.source, fact.source)} · "
                    f"observed {fact.observed_at} · scope "
                    + ("shared" if fact.scope == GLOBAL_SCOPE else "this user")
                )
                if fact.detail.get("refused_because"):
                    st.error(f"Refused because: {fact.detail['refused_because']}")
                if fact.detail.get("verified_against"):
                    st.caption(f"Verified against: {fact.detail['verified_against']}")
                if fact.detail.get("learned_after_failing"):
                    st.caption(
                        "Learned after failing on: "
                        + ", ".join(fact.detail["learned_after_failing"])
                    )
            with actions:
                if st.button("Forget", key=f"forget-{fact.scope}-{fact.kind}-{fact.key}"):
                    store.forget(fact.scope, fact.kind, fact.key)
                    st.rerun()

    st.divider()

    # --- The two buttons the lab asks for, plus the one that makes them mean
    # something.

    left, right = st.columns(2)

    with left:
        st.markdown("#### Save a preference")
        st.caption(
            "The only thing stored on a person's word alone. That is not a hole "
            "in the rule below: a preference changes wording, never a verdict or "
            "a figure, so the worst a poisoned one achieves is an ugly answer."
        )
        with st.form("preference"):
            name = st.text_input("Name", value="units")
            value = st.text_input(
                "Value", value="state every figure in billions to two decimals"
            )
            if st.form_submit_button("Save preference", type="primary"):
                _, said = memory_gate.remember_preference(store, DEFAULT_USER, name, value)
                st.success(said)
                st.rerun()

        st.markdown("#### Teach it a company name")
        st.caption(
            "resolve_company(\"Coca-Cola\") returns COCA-COLA EUROPACIFIC "
            "PARTNERS plc — a UK bottler — with no error at all, because a name "
            "did match a company with that name. Only a person knows which of "
            "two real companies was meant."
        )
        with st.form("alias"):
            alias = st.text_input("When I say", value="Coca-Cola")
            target = st.text_input("I mean this filer or ticker", value="KO")
            if st.form_submit_button("Verify and remember"):
                fact, said = memory_gate.remember_alias(store, DEFAULT_USER, alias, target)
                (st.success if fact.is_usable else st.error)(said)
                st.rerun()

    with right:
        st.markdown("#### Try to poison it")
        st.caption(
            "Assert something false and watch what happens. The assertion is run "
            "through the same tool the agent uses, against the live SEC endpoint. "
            "A tag the company does not file is quarantined — recorded and "
            "visible, never injected into a prompt."
        )
        with st.form("poison"):
            p_company = st.text_input("Company", value="Goldman Sachs")
            p_tag = st.text_input("Claim it files revenue under", value="TotallyRealTag")
            p_year = st.number_input("Fiscal year", value=2022, min_value=1995, max_value=2030)
            if st.form_submit_button("Assert it", type="secondary"):
                fact, said = memory_gate.remember_tag_assertion(
                    store, DEFAULT_USER, p_company, p_tag, int(p_year)
                )
                if fact.is_usable:
                    st.success(said)
                else:
                    st.error(said)
                    st.caption(
                        "A human can teach this agent things. A human cannot "
                        "teach it things that are false."
                    )
                st.rerun()

        st.markdown("#### Start a new session")
        st.caption(
            "Clears everything this page is holding — the last answer, the last "
            "trace, every widget's state. What survives came from the store, not "
            "from the page."
        )
        if st.button("Clear this page's state"):
            st.session_state.clear()
            st.rerun()

        st.warning(
            "**Honest limit.** This button restarts the *session*, not the "
            "*process*. It proves the page is not caching answers, which is worth "
            "proving — but the process keeps running, so on its own it does not "
            "prove anything reached a disk.\n\n"
            "For that: `remember.py` runs every command in a separate process, "
            "and `pytest test_memory.py` spawns a subprocess, lets it die, and "
            "reads the fact back.",
            icon="⚖️",
        )

    st.divider()
    st.caption(
        "**Never stored: a figure.** Every annual report republishes prior years, "
        "and restatements mean those values can disagree — see the note in "
        "`sec_tool.py`. A remembered figure would be right when written and "
        "silently wrong later, with the agent repeating it as confidently as "
        "something it had checked. So memory keeps the *tag* — where to look — "
        "and the number is fetched live every time."
    )


# --- How it works ------------------------------------------------------------
#
# A diagram of a system is usually a drawing of what someone intended. This one
# is generated from the trace of the last run, so the highlighted path is the
# path that actually executed — the same reason the audit tab shows ACT and
# OBSERVE rather than a summary of them.

# Node styling. LIVE marks a branch the last run took; DIM is the road not
# taken. Both are readable on Streamlit's white graph canvas in either theme,
# which is why the colours are set explicitly rather than left to the default.
LIVE = 'style="filled,bold" fillcolor="#7b3fa0" fontcolor="white" color="#4a1f66" penwidth=2'
DIM = 'style="filled" fillcolor="#f2f2f4" fontcolor="#8a8a92" color="#d7d7dc"'
NEUTRAL = 'style="filled" fillcolor="#eef1f8" fontcolor="#20242e" color="#9aa4bf"'
DECISION = 'shape=diamond style="filled" fillcolor="#fff4e0" fontcolor="#20242e" color="#d9a441"'


def audit_flow_dot(trace: list[dict] | None) -> str:
    """The audit-time path, with the branches the last run took highlighted."""
    recalled = bool(trace) and any(e["kind"] == "RECALL" for e in trace)
    learned = bool(trace) and any(e["kind"] == "LEARN" for e in trace)
    ran = bool(trace)

    n_acts = sum(1 for e in (trace or []) if e["kind"] == "ACT")
    n_wasted = sum(
        1
        for e in (trace or [])
        if e["kind"] == "OBSERVE"
        and isinstance(e.get("response"), dict)
        and e["response"].get("status") != "found"
    )

    def style(is_live: bool, base: str = NEUTRAL) -> str:
        if not ran:
            return base
        return LIVE if is_live else DIM

    def edge(is_live: bool) -> str:
        if not ran:
            return 'color="#9aa4bf"'
        return 'color="#7b3fa0" penwidth=2.5' if is_live else 'color="#d7d7dc"'

    return f"""
digraph audit {{
  rankdir=TB;
  bgcolor="transparent";
  node [shape=box style=filled fontname="Helvetica" fontsize=11 margin="0.18,0.12"];
  edge [fontname="Helvetica" fontsize=9];

  claim   [label="Claim arrives" {style(ran)}];

  recall  [label="RECALL\\ndoes any stored fact\\nname this company?" {DECISION}];
  drop    [label="quarantined facts\\ndropped here\\n(never reach the model)" {DIM}];
  inject  [label="append facts to the\\ninstruction\\n(not to the claim)" {style(recalled)}];
  plain   [label="instruction unchanged" {style(ran and not recalled)}];

  agent   [label="Agent loop\\nTHINK / ACT / OBSERVE\\n{n_acts} lookup(s), {n_wasted} wasted" {style(ran)}];
  answer  [label="VERDICT\\nfigure always fetched live" {style(ran)}];

  gate    [label="WRITE GATE\\ndid a lookup succeed AFTER\\nan earlier one failed?" {DECISION}];
  nostore [label="store nothing\\n(it was guessable)" {style(ran and not learned)}];
  store   [label="store the TAG\\nnever the figure\\nmax 3 per run" {style(learned)}];
  db      [label="memory_facts\\n(Postgres)" shape=cylinder {style(ran)}];

  claim  -> recall  [{edge(ran)}];
  recall -> inject  [label="  yes" {edge(recalled)}];
  recall -> plain   [label="  no" {edge(ran and not recalled)}];
  recall -> drop    [label="  untrusted" {edge(False)}];
  inject -> agent   [{edge(recalled)}];
  plain  -> agent   [{edge(ran and not recalled)}];
  agent  -> answer  [{edge(ran)}];
  answer -> gate    [{edge(ran)}];
  gate   -> store   [label="  yes" {edge(learned)}];
  gate   -> nostore [label="  no" {edge(ran and not learned)}];
  store  -> db      [{edge(learned)}];
  db     -> recall  [label="  read by the NEXT session\\n  (different process)" style=dashed {edge(recalled)}];

  {{rank=same; inject; plain; drop;}}
  {{rank=same; store; nostore;}}
}}
"""


WRITE_GATE_DOT = f"""
digraph gate {{
  rankdir=LR;
  bgcolor="transparent";
  node [shape=box style=filled fontname="Helvetica" fontsize=11 margin="0.18,0.12"];
  edge [fontname="Helvetica" fontsize=9];

  human [label="A person asserts\\n\\"X files revenue\\nunder TAG\\"" {NEUTRAL}];
  check [label="run it through the SAME tool\\nthe agent uses, against\\nthe live SEC endpoint" {DECISION}];
  ok    [label="trusted\\nstored and injected" style=filled fillcolor="#e6f4ea" fontcolor="#1a7f37" color="#1a7f37"];
  bad   [label="quarantined\\nstored, visible,\\nNEVER injected" style=filled fillcolor="#fdecea" fontcolor="#c62828" color="#c62828"];

  human -> check;
  check -> ok  [label="  SEC: found"];
  check -> bad [label="  SEC: not filed"];
}}
"""


with flow_tab:
    trace = st.session_state.get("last_trace")

    st.subheader("What happens on one audit")
    if trace:
        st.caption(
            f"Highlighted in purple: the path the last run actually took, read "
            f"from its trace. Claim was — *{st.session_state.get('last_claim', '')}*"
        )
    else:
        st.info(
            "Run an audit on the first tab and this diagram will light up the "
            "branches that run actually took, rather than the ones it might have."
        )

    st.graphviz_chart(audit_flow_dot(trace), use_container_width=True)

    st.markdown(
        """
**Two things this diagram is making a point about.**

The dashed arrow at the bottom is the only one that crosses a process boundary.
Everything else happens inside one `audit()` call and dies with it — that is
context. The dashed arrow is memory: written by a run that has already ended,
read by one that shares no variables with it.

The write gate has a *no* branch, and most runs take it. A run that guessed the
right tag first time has taught us nothing, because that guess is already in the
instruction. Only a run that had to recover knows something a later run cannot
work out for itself.
"""
    )

    st.divider()

    st.subheader("What happens when a person asserts a fact")
    st.caption(
        "The other way in. A human can teach this agent things — but the "
        "assertion is checked against the SEC before it is believed, so a human "
        "cannot teach it things that are false."
    )
    st.graphviz_chart(WRITE_GATE_DOT, use_container_width=True)

    st.markdown(
        """
Nothing here inspects the *wording* of the assertion, and that is the point. A
filter that tried to spot implausible tag names would be guessing, and would be
wrong about exactly the unusual tags worth remembering — `RevenuesNetOfInterest\
Expense` looks stranger than `TotallyRealTag`. The check is not "does this look
real" but "does data.sec.gov return it".
"""
    )
