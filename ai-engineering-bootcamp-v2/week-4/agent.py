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

root_agent = Agent(
    name="sec_claim_auditor",
    model=LLM,
    description="Audits numeric claims about public companies against SEC filings.",
    instruction=INSTRUCTION,
    tools=[lookup_filed_figure],
)


# --- Runner ---


def _shorten(value, limit: int = 420) -> str:
    """Trim a value for display without hiding that it was trimmed."""
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def audit(claim: str) -> tuple[str, list[dict]]:
    """Audit one claim. Returns the final answer and the full step trace.

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
    service = InMemorySessionService()
    runner = Runner(agent=root_agent, app_name="sec_auditor", session_service=service)
    session = await service.create_session(app_name="sec_auditor", user_id="user1")

    message = types.Content(role="user", parts=[types.Part(text=claim)])

    trace: list[dict] = []
    answer = "(no response)"

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

    return answer, trace


def print_trace(trace: list[dict]) -> None:
    colour = {"THINK": "\033[36m", "ACT": "\033[33m", "OBSERVE": "\033[32m"}
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
