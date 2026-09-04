# Calibrated Claim Auditor — capstone

A bank writes a sentence about itself. Check whether its own filed numbers back
it up — and say how much to trust the answer.

The last clause is the project. Anyone can build the checker.

**Research tooling, not investment advice.** It says *look at this*, never
*do this*.

Spec: [../../CAPSTONE.md](../../CAPSTONE.md) · Plan:
[../../CAPSTONE_BUILD_PLAN.md](../../CAPSTONE_BUILD_PLAN.md)

---

## State, as of 2026-09-02

All five phases run end to end. **The headline number is a negative one, and it
is reported rather than hidden.**

| Phase | What | State |
|---|---|---|
| 1 Extract | MD&A prose → 959 candidate claims | done |
| 2 Label | ground truth for 50 | **4 by hand, 46 rule-drafted** |
| 3 Run + score | agent vs labels, by claim type | done |
| 4 Calibrate | which verdicts to trust | done — **no subset qualifies at 3%** |
| 5 Ship | per-filing report, web app | done, not yet deployed |

The measured result today:

> On 50 claims, the calibration layer could not identify any subset of verdicts
> trustworthy at 3% error. The strongest predictors of failure are answering
> without consulting filed data, and segment-scope claims.

That is not a placeholder. It is what the evidence supports, and the app says so
on its face.

---

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp ../week-5/.env .env          # OPENAI_API_KEY, SEC_USER_AGENT

.venv/bin/python extract.py               # prose  -> claims.jsonl        (seconds)
.venv/bin/python select_labelling_set.py  # draw 50 -> to_label.jsonl     (seconds)
.venv/bin/python prepare_evidence.py      # tags + figures for each       (~1 min)
.venv/bin/streamlit run label.py          # label by hand -> labels.jsonl
.venv/bin/python run_claims.py            # agent over the 50             (~15 min)
.venv/bin/python score.py                 # agreement, by type
.venv/bin/python calibrate.py             # abstention rule -> calibration.json
.venv/bin/python report.py GS             # per-filing report
.venv/bin/streamlit run app.py            # the product
```

`run_claims.py` is slow on purpose: this OpenAI account allows 30,000 tokens a
minute and one audit spends eight to ten thousand, so it runs one at a time with
pacing. Concurrency lost runs to rate limiting, and a lost run is worse than a
slow one — it is a hole, and the claims that failed were the hard ones.

---

## What each file is for

| File | |
|---|---|
| `extract.py` | MD&A → claims. Types, sections, table rejection |
| `EXTRACTION_RULES.md` | what counts as a claim, **written before the extractor** |
| `select_labelling_set.py` | stratified draw of 50, with sampling weights |
| `prepare_evidence.py` | candidate tags and figures per claim. No model involved |
| `label.py` | the labelling bench |
| `draft_labels.py` | rule-drafted labels — **read its docstring before quoting any number** |
| `run_claims.py` | agent over the claims, traces to `traces.jsonl` |
| `score.py` | agreement by type and provenance, against a trivial baseline |
| `calibrate.py` | which features predict failure; conformal threshold |
| `fetch_history.py` | every year's Item 7 for one filer. Tries **all** documents in a filing — Wells Fargo's MD&A is not in the 10-K body |
| `mine_segments.py` | a filer's reporting segments, read out of its own filings. **Proposes; does not decide** |
| `browse.py` | regroups `consistency.jsonl` by concept for the *Does the number match?* tab. **Reads stage 2, never stage 3's `join.jsonl`** — see its header for why |
| `report.py` / `app.py` | per-filing report and the web app |
| `sec_tool.py` | XBRL lookup. **Modified** from week-5 — see below |

`agent.py`, `instructions.py`, `memory.py`, `memory_gate.py`, `trace_log.py` and
`checks.py` are carried over from weeks 4 and 5. Only `agent.py` and
`sec_tool.py` were changed, both documented at the point of change.

---

## Two changes to the week-5 code, and why

**`sec_tool.py` — balance-sheet facts were being discarded.** `_annual_entries`
began `if not start or not end: continue`. A balance-sheet fact has no start
date: the SEC returns `start: null`, because total assets is a position on a
date rather than a flow across a year. Every such fact was dropped before the
year filter ran, the tool returned nothing, and the agent correctly answered
NOT_CHECKABLE.

That is the worst shape a bug can take here. The claim was checkable, the data
was filed, and a limitation of ours was reported as a property of the SEC's
data — in a project whose output is a statement about how far the agent can be
trusted. Cost: 115 of 964 candidates unreachable. Eleven regression checks now
cover it, including Apple, whose September year end proves the rule keys on the
SEC's own `fp` marker rather than on December.

**`agent.py` — an instruction is not a guarantee.** Nine of the first fifty runs
answered with no tool call at all. One reasoned that "fiscal year 2025 is beyond
the data coverage range available for checking", which is untrue. All nine said
NOT_CHECKABLE; because NOT_CHECKABLE is also the commonest label, **all nine
scored as correct**.

Rule 5 of the instruction already forbade this in as many words, including an
explicit warning about talking yourself out of the call. It did not hold.
`audit_checked()` now retries a run that consulted nothing and, if it still
refuses, marks it inadmissible; `score.py` will not credit it. Six of the nine
looked when simply asked again. Three never did.

> An instruction is a request. A check is a guarantee. Anything that must always
> be true belongs in code that can refuse.

---

## Honest limits

**The labels are provisional.** 46 of 50 were drafted by rule, not established by
a person. Those rules read the same SEC data the agent reads, so agreement
between them is not evidence the agent is right. **No accuracy figure in this
directory is reportable until the labels are redone by hand.** The 15 rows
flagged `label_confident: false` are the ones to do first.

**The agent loses to a trivial baseline.** It agrees with the labels 62% of the
time; answering NOT_CHECKABLE to everything agrees 74% of the time. Reported
because a result that only looks good next to zero is not a result.

**It cannot handle segment scope.** Of 10 claims labelled DEFINITION_MISMATCH,
it got zero right — calling 9 of them NOT_CHECKABLE. `companyconcept` returns
firmwide facts, so a segment claim is compared against a firmwide number.
Distinguishing "real figure, wrong scope" from "nothing to check" is the single
most important judgement in this domain, and the agent does not make it.

**Non-GAAP figures are out of reach.** Management's own measures — managed
basis, adjusted, tangible — are not filed as XBRL. The tool can flag that a
claim is not in the filed data; it cannot verify it. This is where the most
contestable claims live, and it is a ceiling on the product rather than a bug.

**Company-specific tags are invisible.** Only the `us-gaap` namespace is queried.
A line item a filer tags under its own namespace cannot be reached at all.

**Tags go obsolete and the old ones look valid.** Goldman abandoned
`PrincipalTransactionsRevenue` after 2010; JPMorgan abandoned
`FinancingReceivableAllowanceForCreditLosses` after 2021, at the CECL
changeover. Both still return HTTP 200 with historical data. Two of the first
three claims hit this.

**Section detection is heuristic and filer-specific.** JPMorgan prints ALL-CAPS
headers; Goldman puts segment names alone on a line. Neither is a contract, and
a filer changing its typography would break it silently. Every claim records how
far back its header was, so a thin attribution is visible.

**Conformal assumes exchangeability**, which financial filings violate whenever
an accounting standard changes. The bound is reported with that limit attached.

**Two banks, one fiscal year, one section.** Nothing here generalises beyond
large-cap US bank MD&A in FY2025.

---

## What would make this defensible

In order, and no code changes are needed for any of it:

1. Hand-label the 50 — the `R6` rows first
2. `run_claims.py --reps 3`, then `score.py` and `calibrate.py` again
3. Report whatever the operating curve says, including if it is still negative

A negative result honestly measured is a stronger claim than a positive one that
cannot be defended.
