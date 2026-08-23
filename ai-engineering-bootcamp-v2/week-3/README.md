# Week 3 — SEC Claim Auditor (Google ADK agent)

## The job

> **When** someone makes a claim containing a number about a public company,
> **the agent should** return a verdict — *supported*, *contradicted*,
> *definition mismatch*, or *not checkable* —
> **using** a live lookup of what that company actually filed with the SEC.

This is the one sentence the assignment asks for before any code is written:
*"When [trigger], the agent should [outcome] using [tool(s)]."*

## Why the four verdicts, not two

A first attempt had three — supported, contradicted, not checkable. Testing
showed that is not enough.

Asked for JPMorgan's FY2022 revenue, gpt-4o-mini answered **$132.3B**, four
times out of four, with no hedging. The SEC's `Revenues` figure is **$128.69B**.
The model was not hallucinating: $132.3B appears to be JPMorgan's *managed
basis* revenue, which JPMorgan's own MD&A states is a non-GAAP measure. Two real
numbers, same company, same year, different definition.

Goldman Sachs made the same point harder. Three different XBRL tags each
returned a real FY2022 figure — $47.37B, $29.02B, $39.69B — and all three are
correct answers to slightly different questions.

So `definition mismatch` is a verdict in its own right, and probably the most
common interesting one.

## Why this is an agent, not a workflow

The next step depends on the previous step's result in a way that cannot be
hard-coded. "Revenue" is filed under `Revenues` at JPMorgan and Bank of America,
but that tag returns *nothing* for Goldman Sachs, which files under
`RevenuesNetOfInterestExpense`. Which tag a company uses is not knowable in
advance, so when a lookup comes back empty the model — not the code — decides
what to try next.

That retry was observed three separate times while scoping this project. It is
forced by the data, not staged for the demo.

## Files

| File | Runs | What it is |
|---|---|---|
| `sec_tool.py` | yes | the lookup. Knows the SEC, knows nothing about agents |
| `agent.py` | yes | the agent. Knows the job, knows nothing about the SEC |
| `README.md` | no | this |

Run the lookup on its own — no model, no key, no cost:

```bash
.venv/bin/python sec_tool.py
```

Run the agent on one claim, with the full trace:

```bash
.venv/bin/python agent.py
```

## What the quota allows

Google's free tier permits **twenty requests per day, per model**. One audit
costs three or four. When a model runs dry, change `GEMINI_MODEL` in `.env` —
each model has its own separate allowance. Verified working 2026-08-22:
`gemini-3.5-flash`, `gemini-3-flash-preview`, `gemini-flash-lite-latest`.

`gemini-2.5-flash` is retired for new accounts and returns 404. The course demos
hardcode it, which is why they fail out of the box.

## Two failures the trace caught

Both were fixed by editing the instruction text in `agent.py`. Neither needed a
code change, which is the point: the instruction is the only steering there is.

**It invented a tag.** The tool returns a list of suggestions when a tag is not
filed, so the model called it with a tag named `dummy_tag` to farm that list.
A reasonable trick, but it spent a lookup and returned nothing, because
suggestions are matched against the name asked for.

**Its verdict disagreed with its own reasoning.** It stamped CONTRADICTED while
explaining, in the same answer, that the claimed figure was JPMorgan's real
managed-basis revenue — which is the definition of DEFINITION_MISMATCH. Nothing
errored. Anyone reading only the verdict would have been told a true claim was
false. This is the failure mode the capstone exists to measure, appearing
unprompted in week 3.

## Status

- [x] Step 0 — Google API key working
- [x] Step 1 — job sentence (above)
- [x] Step 2 — ADK installed, Demo 1 runs
- [x] Step 3 — Demo 1 read and understood
- [x] Step 4 — minimal agent with a placeholder tool, step cap of 8
- [x] Step 5 — `sec_tool.py`, checked by hand on six cases before any model saw it
- [x] Step 6 — real tool attached; first genuine verdict
- [x] Step 7 — Think → Act → Observe printed
- [ ] Step 8 — Streamlit UI
- [ ] Step 9 — proof captured, submitted
