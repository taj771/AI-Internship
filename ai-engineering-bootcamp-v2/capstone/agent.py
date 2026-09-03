"""
SEC Claim Auditor — the agent itself.

The agent holds the job, the rules and the verdicts. It knows nothing about how
the SEC website works; that lives in sec_tool.py, which was written and checked
by hand before this file was allowed to depend on it.

Run:  .venv/bin/python agent.py
"""

import asyncio
import os

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

# The real lookup, from the other file. The name is unchanged from the
# placeholder it replaces, so the tools list below did not have to change: the
# agent was always asking for "lookup_filed_figure", and that name now points
# at something that actually goes to data.sec.gov.
from sec_tool import lookup_filed_figure

# The two instruction texts, week 3's and the week 4 rewrite. See the note above
# INSTRUCTION_VERSION below for why they live in their own file.
from instructions import VERSIONS

# Week 5. Durable memory: the store, and the policy deciding what may enter it.
#
# Note what is NOT imported here — nothing that decides anything. `recall` reads,
# `propose_from_trace` reads a finished trace, and neither can change a verdict.
# Memory enters this file at exactly two points, both marked below, and the loop
# between them is byte-identical to week 4's.
from memory import MemoryStore, as_prompt_block
from memory_gate import apply_proposals, propose_from_trace

# Reads .env so GOOGLE_API_KEY never has to be typed into this file.
load_dotenv()

# Which engine drives the agent. Set LLM_PROVIDER in .env to "openai" or
# "gemini".
#
# The assignment prefers Google ADK, and this is Google ADK either way — same
# Agent, same tools, same Runner, same event stream. Only the engine behind it
# changes, through ADK's own LiteLlm wrapper.
#
# The default is openai for one practical reason: Gemini's free tier allows
# twenty requests per day *per model*, and one audit costs three or four. That
# is roughly five audits a day before the page starts returning errors, which is
# not enough to survive a cohort clicking a link. The OpenAI key is already on
# billing from Week 1 and one audit costs a fraction of a cent.
#
# Switching to gemini is a one-line change here, so a run on Google's own stack
# can be shown on request.
PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# MODEL is the plain name, used for display in the terminal and the sidebar.
# LLM is what the Agent below is actually given: a string for Gemini, which ADK
# understands natively, or a LiteLlm wrapper for anything else.
if PROVIDER == "gemini":
    MODEL = GEMINI_MODEL
    LLM = GEMINI_MODEL
else:
    from google.adk.models.lite_llm import LiteLlm

    MODEL = OPENAI_MODEL
    LLM = LiteLlm(model=f"openai/{OPENAI_MODEL}")

# The hard stop on the loop. Every "think" is one call to Gemini, so this is
# both a safety limit and a cost limit — the assignment is explicit that an
# unbounded agent burns tokens and money. Eight is generous for this job: read
# the claim, look up a figure, maybe retry under a different tag, then answer.
#
# It also keeps us inside Google's free tier, which allows only five requests
# per minute. A single run may hit that ceiling; two runs back-to-back will.
MAX_STEPS = 8


# --- Agent ---

# Everything that steers this agent's behaviour is English. There is no other
# logic in this file — no rules, no if-statements deciding verdicts. When the
# agent misbehaves, that text is what gets edited.
#
# In week 3 the text lived here. It now lives in instructions.py, holding two
# versions: BASELINE, byte-identical to what week 3 shipped, and FIXED, the
# rewrite derived from the failures recorded in taxonomy.md.
#
# It moved for one reason. Comparing the two means running both, repeatedly,
# and hand-editing the text back and forth between runs is how a baseline
# quietly stops being the baseline. Selecting it by environment variable also
# means the comparison can be rerun months later by someone who was not here.
#
# Nothing else changed alongside it. sec_tool.py is byte-identical to week 3's,
# the step cap is still eight, the model is still the one named in .env. Any
# difference between the two versions can therefore only have come from the
# instruction — which is the entire point of changing one thing at a time.
INSTRUCTION_VERSION = os.getenv("INSTRUCTION_VERSION", "fixed").strip().lower()

if INSTRUCTION_VERSION not in VERSIONS:
    raise SystemExit(
        f"INSTRUCTION_VERSION={INSTRUCTION_VERSION!r} is not one of "
        + ", ".join(sorted(VERSIONS))
    )

INSTRUCTION = VERSIONS[INSTRUCTION_VERSION]

# Week 5 changes how this is built, not what it is.
#
# In weeks 3 and 4 the Agent was constructed once, here, because its instruction
# never varied. It now varies per claim: recalled facts are appended to the
# instruction before the run. So construction moves into a function called once
# per audit. That costs nothing — it assembles an object, it does not open a
# socket — and it keeps the alternative at bay, which would have been mutating a
# shared agent's instruction between runs. A shared object rewritten per request
# is a race condition waiting for the first two people to click at once.
def build_agent(instruction: str) -> Agent:
    return Agent(
        name="sec_claim_auditor",
        model=LLM,
        description="Audits numeric claims about public companies against SEC filings.",
        instruction=instruction,
        tools=[lookup_filed_figure],
    )


# Kept because `adk web` and `adk run` discover an agent by this name, and
# because it is the agent with no memory attached — which is the right thing for
# those tools to find. Every audit below builds its own.
root_agent = build_agent(INSTRUCTION)


# Who the facts belong to. Preferences and company aliases are per-person; tag
# facts are shared (see the note on GLOBAL_SCOPE in memory.py). There is no login
# on this page, so everyone hitting the public URL is the same "user" unless a
# caller says otherwise — which is honest for a demo and is stated in the README
# rather than hidden.
DEFAULT_USER = os.getenv("MEMORY_USER_ID", "demo-user").strip() or "demo-user"

# One store per process, opened on first use rather than at import.
#
# Deferred because importing this module must not require a working database.
# `python -c "import agent"` during a build, or a Streamlit page that only wants
# MODEL for its sidebar, should not fail because Supabase is asleep.
_store: MemoryStore | None = None


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store


# --- Runner ---


def _shorten(value, limit: int = 420) -> str:
    """Trim a value for display without hiding that it was trimmed."""
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def audit(
    claim: str,
    user_id: str = DEFAULT_USER,
    store: MemoryStore | None = None,
    learn: bool = True,
) -> tuple[str, list[dict]]:
    """Audit one claim. Returns the final answer and the full step trace.

    WEEK 5 — the two places memory touches this function

    RECALL, before the model runs. Facts naming this claim's company are read
    from the store and appended to the instruction. Nothing else about the run
    changes.

    LEARN, after it finishes. The trace is read back and any tag the run had to
    recover to find is written. `learn=False` turns this off, which the
    before/after demo needs: showing that a second session is faster requires a
    first session whose own writes cannot contaminate the comparison.

    Both appear in the returned trace as steps, alongside THINK, ACT and
    OBSERVE. That is deliberate. The assignment asks the UI to *show* recall, and
    a RECALL step in the trace shows up everywhere the trace already does — the
    terminal, the Streamlit page, a saved JSONL — without any of them needing to
    know memory exists. Week 4's harness filters the trace by kind, so the extra
    kinds are ignored there and its checks still run against week 5 runs.

    `store` is injectable so tests can hand in a throwaway SQLite file. Left
    None, it uses the process-wide store, which is Postgres when DATABASE_URL is
    set and a local file otherwise.

    The trace is the point of this function. ADK reports every step as it
    happens — the model reasoning, the tool being requested, the result coming
    back — and Demo 1 discards all of it, keeping only the last line. That is
    why running Demo 1 showed a tidy answer about an invoice with no visible
    sign of the lookup that produced it.

    Here each event is instead labelled with the three words the assignment asks
    to see, so that a run can be shown rather than asserted:

        THINK    the model is reasoning, or explaining what it will do
        ACT      the model has asked for a tool, with these exact arguments
        OBSERVE  the tool ran and this is what came back

    The same trace feeds the terminal and the Streamlit page, so what a grader
    sees in the browser is the run itself and not a retelling of it.
    """
    memory = store if store is not None else get_store()

    trace: list[dict] = []
    answer = "(no response)"

    # --- ① RECALL ------------------------------------------------------------
    #
    # The only read. `recall` returns facts whose company is named in this claim,
    # plus this user's preferences, and it drops anything quarantined before we
    # ever see it — so a refused fact cannot reach the prompt even by mistake.
    recalled = memory.recall(user_id, claim)
    instruction = INSTRUCTION + as_prompt_block(recalled)

    if recalled:
        memory.note_hits(recalled)
        trace.append(
            {
                "kind": "RECALL",
                "detail": "\n".join(f"{fact.one_line()}" for fact in recalled),
                "facts": [
                    {
                        "kind": fact.kind,
                        "key": fact.key,
                        "value": fact.value,
                        "source": fact.source,
                        "observed_at": fact.observed_at,
                    }
                    for fact in recalled
                ],
                "turn": 0,
            }
        )

    # InMemorySessionService is still here, and still built per audit, and that
    # is now a statement rather than an oversight. It holds this turn's context —
    # the conversation, thrown away when the function returns. The store above
    # holds memory. Weeks 3 and 4 had only the first and called it neither.
    service = InMemorySessionService()
    runner = Runner(
        agent=build_agent(instruction),
        app_name="sec_auditor",
        session_service=service,
    )
    session = await service.create_session(app_name="sec_auditor", user_id="user1")

    message = types.Content(role="user", parts=[types.Part(text=claim)])

    # Week 4 addition. Counts the events ADK hands back, so every recorded step
    # can say which turn it belonged to.
    #
    # This exists because of something the first recorded run showed. Auditing
    # the Goldman claim produced ACT, ACT, OBSERVE, OBSERVE — both lookups
    # requested before either result had come back. The model did not read a
    # failure and retry; it asked for `Revenues` and `RevenuesNetOfInterest\
    # Expense` at the same time, because the instruction names both. That is a
    # hedge, not a decision, and week 3's write-up describes it as a decision.
    #
    # Without a turn number the two are indistinguishable in the record: both
    # appear as two ACTs and two OBSERVEs, in an order that a reader will
    # naturally assume is causal. With one, "same turn" means parallel guessing
    # and "later turn" means the model actually reacted to what came back — and
    # step 7 can check for it rather than argue about it.
    turn = 0

    async for event in runner.run_async(
        user_id="user1",
        session_id=session.id,
        new_message=message,
        run_config=RunConfig(max_llm_calls=MAX_STEPS),
    ):
        if not (event.content and event.content.parts):
            continue

        turn += 1
        is_final = event.is_final_response()

        for part in event.content.parts:
            # A tool request. ADK has not run anything yet at this point — the
            # model has only written down what it wants, and these are its exact
            # arguments. This is where a bad guess becomes visible: a wrong
            # company, a wrong year, or a tag that does not exist.
            if getattr(part, "function_call", None):
                call = part.function_call
                args = ", ".join(f"{k}={v!r}" for k, v in (call.args or {}).items())
                trace.append(
                    {
                        "kind": "ACT",
                        "detail": f"{call.name}({args})",
                        # Week 4 addition. `detail` is a sentence for a human to
                        # read; `tool` and `args` are the same fact in a shape a
                        # check can test. An assertion that wants to know whether
                        # the model invented a tag needs args["xbrl_tag"], not a
                        # string it has to pull apart with a regex.
                        "tool": call.name,
                        "args": dict(call.args or {}),
                        "turn": turn,
                    }
                )

            # The tool's return value, handed back to the model. When this says
            # tag_not_filed, the next THINK is the model deciding what to do
            # about it — which is the whole reason this is an agent.
            elif getattr(part, "function_response", None):
                response = part.function_response.response
                trace.append(
                    {
                        "kind": "OBSERVE",
                        "detail": _shorten(response),
                        # Week 4 addition, and the one that matters most. The
                        # displayed version is cut at 420 characters, which is
                        # right for a screen and wrong for evidence: a
                        # tag_not_filed result carries twelve suggested tags and
                        # the truncation lands in the middle of them, so the
                        # saved trace could easily omit the exact tag the model
                        # went on to choose. Keeping the untruncated result means
                        # a later check can ask "was that tag ever offered to the
                        # model, or did it invent one?" and get a real answer.
                        "response": response,
                        "turn": turn,
                    }
                )

            elif getattr(part, "text", None) and part.text.strip():
                if is_final:
                    answer = part.text
                else:
                    # Reasoning on the way to an answer, rather than the answer
                    # itself. Gemini does not always narrate, so a run with two
                    # tool calls and no THINK lines is normal, not broken.
                    #
                    # `text` is the Week 4 addition: the untruncated reasoning.
                    # Rule 4 of the instruction says the verdict must agree with
                    # the reasoning, and that is only checkable against the whole
                    # of what was said, not its first 420 characters.
                    trace.append(
                        {
                            "kind": "THINK",
                            "detail": _shorten(part.text),
                            "text": part.text,
                            "turn": turn,
                        }
                    )

    # --- ② LEARN -------------------------------------------------------------
    #
    # The only write, and it happens after the answer is final. Nothing the gate
    # decides here can change the verdict that was just produced — the run is
    # over. That ordering is what makes memory unable to corrupt an audit: it can
    # only ever affect the *next* one, where it arrives as a hint that still has
    # to survive a live lookup.
    if learn:
        written = apply_proposals(memory, propose_from_trace(trace))
        if written:
            trace.append(
                {
                    "kind": "LEARN",
                    "detail": "\n".join(
                        f"{fact.one_line()}  [{fact.source}]" for fact in written
                    ),
                    "facts": [
                        {"kind": f.kind, "key": f.key, "value": f.value} for f in written
                    ],
                    "turn": turn + 1,
                }
            )

    return answer, trace


def print_trace(trace: list[dict]) -> None:
    colour = {
        "THINK": "\033[36m",
        "ACT": "\033[33m",
        "OBSERVE": "\033[32m",
        "RECALL": "\033[35m",
        "LEARN": "\033[35m",
    }
    for step, entry in enumerate(trace, start=1):
        tint = colour.get(entry["kind"], "")
        print(f"  {step}. {tint}{entry['kind']:<8}\033[0m {entry['detail']}")


async def main() -> None:
    # One claim, not several. Free tier allows five requests per minute and a
    # single audit uses three or four, so a list of test claims would fail on
    # quota rather than on anything interesting.
    claim = "JPMorgan Chase reported total revenue of $132.3 billion in 2022."

    print(f"CLAIM: {claim}\n")

    answer, trace = await audit(claim)

    print(f"--- TRACE ({len(trace)} steps, cap {MAX_STEPS}) ---")
    print_trace(trace)
    print("\n--- ANSWER ---")
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())


# --- Evidence enforcement ---------------------------------------------------


def evidence_count(trace: list[dict]) -> int:
    """How many tool calls a run actually made."""
    return sum(1 for step in trace if step.get("kind") == "ACT")


async def audit_checked(
    claim: str,
    user_id: str = DEFAULT_USER,
    store=None,
    learn: bool = True,
    attempts: int = 3,
) -> tuple[str, list[dict], dict]:
    """audit(), but a run that consulted no evidence is not accepted.

    THE FAILURE THIS EXISTS TO STOP

    Measured on 2026-09-02 over the fifty capstone claims: nine of fifty runs
    answered without making a single tool call. One reasoned that "fiscal year
    2025 is beyond the data coverage range available for checking", which is
    simply untrue — that year is filed and the tool returns it.

    All nine answered NOT_CHECKABLE. Because NOT_CHECKABLE is also the most
    common label, all nine were counted as correct. An agent that gives up
    without looking scored identically to one that looked and found nothing, and
    no aggregate number could tell them apart.

    Rule 5 of the instruction already forbids this, in as many words, including
    an explicit warning about talking yourself out of the call. It did not hold,
    and that is the general lesson rather than a fact about this model: an
    instruction is a request, and a request is not a guarantee. Anything that
    must always be true belongs in code that can refuse.

    So: retry a run that produced no ACT step. If every attempt refuses to look,
    return the last answer with `evidence.admissible` false, and let scoring
    treat it as a failure rather than as an opinion that happened to agree.

    Retries reuse the claim unchanged. Appending "you must call the tool" would
    make the measured claim different from the one in claims.jsonl, and then
    what was evaluated is no longer what was labelled.
    """
    answer, trace, calls = "", [], 0

    for attempt in range(1, attempts + 1):
        answer, trace = await audit(claim, user_id=user_id, store=store, learn=learn)
        calls = evidence_count(trace)
        if calls:
            return answer, trace, {
                "admissible": True,
                "tool_calls": calls,
                "attempts": attempt,
            }

    return answer, trace, {
        "admissible": False,
        "tool_calls": 0,
        "attempts": attempts,
        "detail": (
            f"Answered with no tool call on all {attempts} attempts. The run has "
            "no admissible evidence, whatever its verdict says."
        ),
    }
