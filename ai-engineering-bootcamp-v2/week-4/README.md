# Week 4 — TRACE on the SEC Claim Auditor

Trace, Read, Analyze, Codify, Enforce, applied to the Week 3 agent. Twenty
hand-built claims, every run recorded, every run read by hand, six failure types
named, three checks written in code, one instruction rewritten, and then the
whole thing run enough times to find out whether the rewrite did anything.

**Headline: it did not, measurably.** The rewrite scores 16.3/20 against the
baseline's 16.0/20, and the baseline scores 15, 16 and 17 across three runs with
nothing changed at all. The improvement is smaller than the measurement error.
That is the result, and the part worth reading.

Week 3 is untouched. This directory has its own copy of the agent, its own
`.venv`, and `sec_tool.py` is byte-identical to the one Week 3 shipped.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # only if recreating
cp ../week-3/.env .                                                   # keys, gitignored

.venv/bin/python run_batch.py                    # 20 audits -> traces.jsonl   ~1 min, ~$0.15
.venv/bin/streamlit run open_coding.py           # read and annotate them by hand
.venv/bin/python checks.py                       # grade the recorded runs      instant, free
.venv/bin/pytest test_checks.py -q               # the same checks, as tests
.venv/bin/streamlit run evals_app.py             # the dashboard
```

To run the two instruction versions against each other:

```bash
INSTRUCTION_VERSION=baseline .venv/bin/python run_batch.py --label baseline-rep4 --out traces-reps.jsonl
INSTRUCTION_VERSION=fixed    .venv/bin/python run_batch.py --label fixed-rep4    --out traces-reps.jsonl
```

`baseline` is byte-identical to Week 3's instruction; `fixed` is the rewrite.
Both live in `instructions.py`, generated from the two `agent.py` files rather
than retyped, so the baseline cannot quietly drift from what was submitted.

## The files

| File | What it is |
|---|---|
| `claims.py` | The 20 claims, each with the weakness it was written to expose and the SEC figure established by hand |
| `agent.py` | Week 3's agent. Only the instruction moved out; the runner gained turn numbers |
| `instructions.py` | The two instruction texts, selected by `INSTRUCTION_VERSION` |
| `sec_tool.py` | Week 3's, unchanged. Verified byte-identical |
| `trace_log.py` | Writes one run per line to JSONL, reads them back. Judges nothing |
| `run_batch.py` | Runs the claim set. Decides nothing |
| `open_coding.py` | The reading bench — one run at a time, free-text notes, binary pass/fail |
| `checks.py` | The three code checks, and a CLI |
| `test_checks.py` | The checks as pytest, plus tests of the checks themselves |
| `evals_app.py` | The dashboard |
| `grounding.py` | Path B: the grounding question, the 40-run sample, TPR/TNR |
| `label_grounding.py` | The labelling bench — 40 binary decisions |
| `judge.py` | The LLM judge, two prompt versions, cached per model |
| `judge_notes.md` | The judge write-up: confusion matrices, what it still misses |
| `taxonomy.md` | The six failure types, counts, ranking, and limits |
| `traces*.jsonl` | Every recorded run, including the human annotations |

## What was found

Six failure types, from reading all twenty runs before any category existed.
Full definitions and counts in [taxonomy.md](taxonomy.md).

| | Failure | Runs |
|---|---|---|
| F1 | Tag search abandoned or misdirected | C05, C06 |
| F2 | Required answer format abandoned | C21, C22 |
| F3 | Wrong company audited, undetected | C17 |
| F4 | Verdict ruled without testing the alternative | C09 |
| F5 | Tool evidence dropped from the answer | C10 |
| F6 | Answered with no tool call at all | C19 |

Two faults were found by hand before the agent ran at all, by calling the tool
directly with no model involved:

- **`sec_tool.py` cannot return any balance-sheet figure, for any company.**
  `_annual_entries` keeps only facts spanning 350–380 days; a balance-sheet fact
  has no span (the SEC sends `start: null`). Every `Assets` and
  `StockholdersEquity` lookup fails as `no_annual_data`. The agent's own
  instruction recommends both tags.
- **Company resolution silently picks the wrong filer.**
  `resolve_company("Coca-Cola")` returns COCA-COLA EUROPACIFIC PARTNERS plc, a
  UK bottler. `"Coca Cola"` without the hyphen returns the right company.
  `"XOM"` returns ExxonMobil Holdings Corp rather than Exxon Mobil Corp.

Neither is fixed here. Both are tool faults, and the week's one change was to
the instruction — mixing the two would make the measurement unattributable.

## What the checks catch

Three deterministic checks, no model involved, ~0.1s for the whole set:

- **`answer_format`** — five fields present, verdict one of the four allowed
- **`evidence_exists`** — at least one tool call was made
- **`contradicted_was_searched`** — a CONTRADICTED verdict on a claim within 2×
  the filed figure must have tested an alternative tag afterwards

They catch four of the eight failures a human found. The other four — chose
badly from a suggestion list, dropped a restatement, audited a UK bottler —
need somebody who knows what the numbers mean. That gap is the division of
labour, not an oversight.

## Path B — a validated LLM judge

The code checks catch four of the eight failures. One of the rest — *does the
reasoning assert anything the tool results do not support?* — is automated with
an LLM judge, and the judge is then validated against 40 hand-labelled runs.
Full write-up in [judge_notes.md](judge_notes.md).

```
model         prompt    TP  FN  FP  TN     TPR     TNR   agree
gpt-4o-mini   v1         2   9   2  27    18%     93%     72%
gpt-4o-mini   v2         3   8   2  27    27%     93%     75%
gpt-4o        v1         6   5   1  28    55%     97%     85%
gpt-4o        v2         9   2   0  29    82%    100%     95%

always-GROUNDED                          0%    100%     72%
```

The last line is the point. A judge that answers GROUNDED to everything — one
return statement, no model — scores 72% agreement on this set. gpt-4o-mini with
the obvious prompt scored **exactly that**, while missing 9 of 11 real failures.
Reported as agreement, it would have shipped.

The prompt refinement helped and the model mattered more: mini with the better
prompt still scores below gpt-4o with the worse one. gpt-4o with v2 is usable as
a pre-filter — 82% of unsupported assertions caught, and not one false alarm in
29 clean runs.

```bash
.venv/bin/streamlit run label_grounding.py                    # the labelling bench
.venv/bin/python judge.py --compare                           # both prompts
JUDGE_MODEL=gpt-4o .venv/bin/python judge.py --compare
```

## Honest limits

**The fix cannot be shown to work.** Three repeats each: baseline 15/16/17,
rewrite 16/17/16. The ranges overlap. The only claim that improved consistently
is C22, where the format rule took. Everything else is inside the noise.

**Fifteen of twenty claims are stable; all the movement lives in five.** C03,
C09, C13, C19 and C22 flip between repeats of an *unchanged* instruction. Those
five are where the agent is genuinely undecided and where a real fix would have
to be measured — with more repeats, or more claims, than there are here.

**The first draft of the fix caused a regression the checks could not see.**
Wording it as "no test needed above the filed figure" licensed the agent to rule
CONTRADICTED on JPMorgan's managed-basis claim without looking — turning a
passing run into a failing one. `contradicted_was_searched` passed it, because
the check carried the same misconception as the rule. The suite got greener
while the agent got worse on its hardest claim. Both were fixed; the tests in
`test_checks.py` pin the corrected behaviour.

**Twenty runs is a small number and the claim set is not representative.**
Claims were chosen to break specific things, not to reflect what arrives in
practice. F3, F4, F5 and F6 each rest on a single run. The 60% human pass rate
is a property of this set.

**Two expected verdicts are contestable.** C09 is graded DEFINITION_MISMATCH and
CONTRADICTED is defensible. C17's ground truth was never established by hand,
because every tag tried failed against the wrong company.

**The judge's labels are not independent human ground truth.** They were
drafted by Claude from the traces and spot-checked by the author, so the rates
measure whether a smaller model reproduces that analysis rather than whether it
matches an independent human. Stated again in judge_notes.md, because it caps
what those numbers can support.

**Eleven positives.** Every TPR is a fraction over 11, so one reclassified run
moves it 9 points. One label (C03 baseline, "such as a managed basis figure")
is genuinely borderline and carries that much on its own.

**The pass rates measure two different things and neither is "the" score.**
The checks say 16/20; a human reading the same runs said 12/20. A suite that
matched the human exactly would mean the checks had been written to fit the
answers.
