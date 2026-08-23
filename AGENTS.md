# AGENTS.md — rules for any coding agent working in this repo

Read this before editing anything. These are not style preferences. Each one is
here because breaking it has already cost something, or would silently produce a
result that looks correct and is not.

This file exists because chat does not survive. A long session gets compacted:
earlier turns are summarised and the detail is dropped, so a rule stated only in
conversation is a rule with a half-life. A rule that must hold on turn 200 has to
live in a file that is reloaded every session. That is the same distinction the
week-5 project is about — context is what fits in the window, memory is what is
written down outside it — applied to the tools building it.

---

## What this repo is

Coursework for the Maven "Build and Ship Production AI Agents" bootcamp. Not one
application: independent folders per week, each self-contained.

```
ai-engineering-bootcamp-v2/
  week-1/   /ask API                      deployed  week1-ask-api.onrender.com
  week-2/   RAG over a document corpus    deployed  week2-rag-api / -ui
  week-3/   SEC Claim Auditor (ADK)       deployed  week3-sec-auditor
  week-4/   TRACE evals + LLM judge       deployed  week4-evals
  week-5/   durable memory                ← current work
ai-engineering-bootcamp/    course-provided lab material — reference, do not edit
multi-agent-systems/        a different course
render.yaml                 every deployment, one service per week
```

The through-line is the capstone: a **Calibrated Claim Auditor**. An LLM makes a
judgment; the project estimates when to trust it. Full spec lives in
`../CAPSTONE.md` in the parent workspace.

---

## Rules that must not be broken

### 1. A submitted week is frozen. Copy it forward; do not edit it in place.

`week-5/` began as a copy of `week-4/`'s runtime files. `week-2/README.md`
explains why: a graded artifact must stay exactly as graded, and a reader must be
able to diff two folders to see precisely what a week added.

If a bug in `week-3/` needs fixing for `week-5/` to work, fix it in `week-5/` and
note the divergence. Do not reach backwards.

### 2. Never claim an improvement from a single run.

The agent is non-deterministic. Week 4 measured a baseline three times and got
15, 16, 17 out of 20 — a spread two wide with *nothing changed between runs*. The
"improvement" being written up at the time was 0.3, entirely inside that spread.

So: run the baseline at least three times before and after any change, report the
range and not just the median, and say plainly when the groups overlap.

This applies to prompts, models, instructions and memory alike. A before/after
screenshot of a non-deterministic system is not evidence.

### 3. Test your checks against runs you have already graded by hand.

Two of week 4's automated checks flagged correct runs on their first draft. One
demanded the filed figure verbatim, and a correct run had written
`$94,950 million` where the tool said `$94.95B`.

A check that has never been run against known-good output is an untested check.

### 4. Never store a computed figure in durable memory. Store where to look.

`sec_tool.py::_annual_entries` documents this: every annual report republishes
prior years, so one fact appears in three filings and the values can disagree
after a restatement. A remembered figure is correct when written and silently
wrong later — and the agent will repeat it with the confidence of something it
checked.

Memory stores the XBRL *tag* — a reporting convention that does not move — and
the figure is fetched live on every audit. See `week-5/memory_gate.py`.

### 5. Nothing enters trusted memory that data.sec.gov has not confirmed.

Not "nothing the model invented", which is unenforceable. The enforceable
version: a fact becomes trusted only when a tool result fetched from the SEC in
this process says so. A human asserting something does not raise its trust; it
only decides which check runs.

Facts that fail the check are stored **quarantined** — visible, never injected.
Deleting them instead would make a refusal indistinguishable from a bug.

### 6. The deployed disk is ephemeral. Never persist to a local file in a service.

Render's free plan has no persistent disk. A free service sleeps after ~15
minutes idle and returns with an empty filesystem. Anything written to disk there
is gone by the time a grader opens the link.

Durable state goes to Postgres via `DATABASE_URL`. This is also why week 4's
annotation bench (`open_coding.py`) is deliberately not deployed — see the note
in `render.yaml`.

### 7. Secrets are `sync: false` in `render.yaml` and never in git.

`OPENAI_API_KEY`, `GOOGLE_API_KEY`, `DATABASE_URL`. Configuration that is not a
credential — model names, chunk sizes, `SEC_USER_AGENT` — is written in full,
where it is reviewable and version-controlled.

A `sync: false` variable left blank in the dashboard still deploys successfully
and then fails on the first request. Give every such service a health check that
says which variable is missing.

### 8. Use the project-local interpreter.

`week-N/.venv/bin/python`, never a global `python3`. Each week has its own venv
and its own pinned `requirements.txt`. `PYTHON_VERSION` is pinned to `3.12.3` in
`render.yaml` so a platform default bump cannot shift under a working deploy —
keep local venvs on the same version.

### 9. `data.sec.gov` requires a `User-Agent` naming a real person.

Set `SEC_USER_AGENT`. Anonymous requests are refused, and the failure surfaces as
an unhelpful error far from its cause. Their fair-access limit is 10 requests per
second; stay well under it.

Be aware that a failed tag lookup triggers `_suggest_tags`, which downloads
roughly 8 MB. That is affordable once and is not affordable in a loop.

### 10. State limits in the artifact, not only in conversation.

Every README here carries a "Known limits" or "Honest limits" section naming what
the numbers cannot support: truncated job-ad descriptions, endogenous effort, a
25 km weather grid, detection ≠ presence, a UI button that restarts a session and
not a process.

A result without its caveat is not finished. When adding to a README, match that.

---

## Conventions

- Comments explain **why**, at length, where a choice is non-obvious. See the
  header of `week-5/memory_gate.py` or `week-4/claims.py`. Follow the density of
  the surrounding file rather than trimming it down.
- Dates in notes are absolute, never relative.
- One folder per week, each with a `README.md` covering what it does and how to
  run it.
- Ground truth is established by hand, before the agent sees it. Week 4's
  expected verdicts came from calling `sec_tool.lookup_filed_figure` directly
  with no model involved — otherwise the evaluation marks its own homework.

## Before you commit

- [ ] Tests pass: `cd week-5 && .venv/bin/pytest -q`
- [ ] No `.env`, no connection string, no API key in the diff
- [ ] Any performance claim rests on ≥3 runs per side, with the range reported
- [ ] New limitations written into the README, not just mentioned in chat
