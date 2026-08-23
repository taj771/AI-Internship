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

# Reads .env so GOOGLE_API_KEY never has to be typed into this file.
load_dotenv()

# Settable from .env, because the free tier's daily quota is counted *per
# model*: twenty requests a day for gemini-3.6-flash, and a separate twenty for
# gemini-3.5-flash. One audit costs three or four of them, so a working
# afternoon exhausts a model quickly and the only remedy is to use a different
# one. Making that a one-line change in .env rather than an edit to this file
# means it can be done mid-demo without touching code.
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

# The hard stop on the loop. Every "think" is one call to Gemini, so this is
# both a safety limit and a cost limit — the assignment is explicit that an
# unbounded agent burns tokens and money. Eight is generous for this job: read
# the claim, look up a figure, maybe retry under a different tag, then answer.
#
# It also keeps us inside Google's free tier, which allows only five requests
# per minute. A single run may hit that ceiling; two runs back-to-back will.
MAX_STEPS = 8


# --- Agent ---

# Everything that steers this agent's behaviour is the English below. There is
# no other logic in this file — no rules, no if-statements deciding verdicts.
# When the agent misbehaves, this text is what gets edited.
INSTRUCTION = """
You audit numeric claims about public companies against what those companies
actually filed with the US Securities and Exchange Commission.

You will be given one claim. Work out which company it is about, which financial
item it names, and which fiscal year. Then use lookup_filed_figure to find out
what was actually filed, and compare.

HOW TO USE THE TOOL

The tool needs an exact XBRL tag, not a plain word like "revenue". There is no
single tag that works everywhere — companies file the same concept under
different names. Start with a common guess:

    revenue      -> Revenues, or RevenuesNetOfInterestExpense
    net income   -> NetIncomeLoss, or ProfitLoss
    total assets -> Assets
    equity       -> StockholdersEquity

Only ever pass a tag from the list above, or one the tool has suggested to you.
Never invent a tag name. In particular, do not call the tool with a made-up tag
in order to read the suggestions that come back with the failure — that spends a
lookup and returns nothing, because the suggestions are matched against the name
you asked for.

WHEN A LOOKUP COMES BACK EMPTY

Do not give up, and do not fall back on your own knowledge. A failed lookup
returns a field called tags_this_company_does_file. Read it, pick the tag that
best matches what the claim is actually about, and call the tool again.

Prefer a tag that names the whole figure over one that names a part of it. For a
claim about total revenue, RevenuesNetOfInterestExpense is the firm-wide total,
while InvestmentBankingRevenue is one segment inside it.

If the failure instead says no_annual_data, the tag may be right and the year
wrong. Check annual_years_available_for_this_tag before trying anything else.

BUDGET

Use at most three tool calls for one claim. If you still have no figure after
three, answer NOT_CHECKABLE and say which tags you tried.

WHEN THE NUMBERS DIFFER

Claims are usually rounded, so treat figures within about 1% of each other as
matching.

If they differ by more than that, consider whether the claim might be true under
a different definition before calling it wrong. "Revenue" can mean the total,
the total net of interest expense, or a company's own non-GAAP figure such as
JPMorgan's "managed basis", and these differ by billions. If you suspect that,
you may spend one of your three tool calls testing an alternative tag. If the
alternative matches the claim, the verdict is DEFINITION_MISMATCH, and you must
name both tags.

THE FOUR VERDICTS

- SUPPORTED — the claimed figure matches the filed figure.
- CONTRADICTED — the claimed figure disagrees with the filed figure, and no
  plausible alternative definition accounts for the gap.
- DEFINITION_MISMATCH — the claimed figure is real for that company and year,
  but measures something other than what was filed under the tag you checked.
- NOT_CHECKABLE — no filed figure could be found, or the claim is too vague to
  pin to a company, an item and a year.

RULES YOU MUST NOT BREAK

1. Never state a filed figure from your own knowledge. You do not know what a
   company filed. The only acceptable source is a tool result.
2. Always report the tag you used. A number without its definition is the exact
   problem this tool exists to catch.
3. Always show both figures — what was claimed, and what was filed.
4. Your verdict must agree with your own reasoning. If the reasoning names an
   alternative definition that explains the gap — a non-GAAP or managed-basis
   figure, a different tag, a segment rather than the total — then the verdict
   is DEFINITION_MISMATCH, not CONTRADICTED. Never label a claim contradicted
   in the same breath as explaining why its number is real. CONTRADICTED is for
   figures you cannot account for at all.

ANSWER IN THIS SHAPE

VERDICT: <one of the four>
CLAIMED: <the figure in the claim>
FILED: <the figure the tool returned, or "none found">
TAG: <the XBRL tag the filed figure came from, and any tags you tried first>
REASONING: <two sentences at most>
"""

root_agent = Agent(
    name="sec_claim_auditor",
    model=MODEL,
    description="Audits numeric claims about public companies against SEC filings.",
    instruction=INSTRUCTION,
    tools=[lookup_filed_figure],
)


# --- Runner ---


def _shorten(value, limit: int = 220) -> str:
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

    async for event in runner.run_async(
        user_id="user1",
        session_id=session.id,
        new_message=message,
        run_config=RunConfig(max_llm_calls=MAX_STEPS),
    ):
        if not (event.content and event.content.parts):
            continue

        is_final = event.is_final_response()

        for part in event.content.parts:
            # A tool request. ADK has not run anything yet at this point — the
            # model has only written down what it wants, and these are its exact
            # arguments. This is where a bad guess becomes visible: a wrong
            # company, a wrong year, or a tag that does not exist.
            if getattr(part, "function_call", None):
                call = part.function_call
                args = ", ".join(f"{k}={v!r}" for k, v in (call.args or {}).items())
                trace.append({"kind": "ACT", "detail": f"{call.name}({args})"})

            # The tool's return value, handed back to the model. When this says
            # tag_not_filed, the next THINK is the model deciding what to do
            # about it — which is the whole reason this is an agent.
            elif getattr(part, "function_response", None):
                trace.append(
                    {
                        "kind": "OBSERVE",
                        "detail": _shorten(part.function_response.response),
                    }
                )

            elif getattr(part, "text", None) and part.text.strip():
                if is_final:
                    answer = part.text
                else:
                    # Reasoning on the way to an answer, rather than the answer
                    # itself. Gemini does not always narrate, so a run with two
                    # tool calls and no THINK lines is normal, not broken.
                    trace.append({"kind": "THINK", "detail": _shorten(part.text)})

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
