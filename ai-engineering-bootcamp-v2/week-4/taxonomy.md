# Failure taxonomy — SEC Claim Auditor

Twenty claims, run through the Week 3 agent unchanged on 2026-08-22, recorded to
`traces.jsonl`, read one at a time and annotated by hand before any category
existed. Baseline: **12 pass, 8 fail — 60%**.

Every count below is computed from the trace file rather than remembered. The
commands that produce them are at the bottom.

## Method

Claims were written first, against known SEC figures established by hand with
the model switched off (`claims.py` records the ground truth for each). They
were chosen to stress specific suspected weaknesses rather than to be
representative — a set the agent handles well would measure nothing.

Open coding came before categories. Each run was read in full — claim, every
tool call, every tool result, final answer — and given free-text notes and a
binary pass/fail. Categories were assembled afterwards, from the notes.

A run fails if any of these is true, regardless of whether the verdict is right:

- the verdict disagrees with the hand-established answer
- a figure in the answer came from somewhere other than a tool result
- it broke one of the five rules in its own instruction
- it stopped while the tool had told it where to look next

That last one is why a correct verdict can still fail. C04 reaches the right
answer by asserting that no alternative definition exists, without looking; C09
does exactly the same and is wrong. Grading only the verdict scores the first as
a success and learns nothing from the second.

## The taxonomy

Impact is what the reader of the answer suffers:

- **3** — actively misled. A confident answer that is wrong.
- **2** — no usable answer. A checkable claim comes back unchecked, or the
  output cannot be read by whatever consumes it.
- **1** — the answer is sound, the working is not.

| # | Failure | Definition | Runs | Freq | Impact | Score |
|---|---------|------------|------|------|--------|-------|
| **F1** | Tag search abandoned or misdirected | The tool returned a suggestion list and the agent either ignored it or chose a tag that holds no data for the year asked | C05, C06 | 2 | 2 | **4** |
| **F2** | Required answer format abandoned | No VERDICT/CLAIMED/FILED/TAG lines at all — replies in prose or asks the user a question | C21, C22 | 2 | 2 | **4** |
| **F3** | Wrong company audited, undetected | `resolve_company` returned a different filer; the wrong name appears in every observation and is never checked against the claim | C17 | 1 | 3 | **3** |
| **F4** | Verdict ruled without testing the alternative | Answers CONTRADICTED — which asserts that no alternative definition exists — without spending a lookup to find out | C09 | 1 | 3 | **3** |
| **F5** | Tool evidence dropped from the answer | The tool reported something material (here, a restatement) and the answer does not mention it | C10 | 1 | 2 | **2** |
| **F6** | Answered with no tool call at all | Verdict and reasoning from the model's own knowledge; zero lookups | C19 | 1 | 2 | **2** |

### F1 — tag search abandoned or misdirected

Two runs, two different shapes, and they need different fixes.

**C05 (Apple)** stopped after one lookup. The failure it read named
`RevenueFromContractWithCustomerExcludingAssessedTax` — the correct tag — first
in its suggestion list. It tried nothing further, with two of three calls unspent.

**C06 (Microsoft)** did work the list, and chose `SalesRevenueNet`, which
Microsoft stopped using in 2017. The correct tag appeared in all three
suggestion lists it was shown.

The underlying cause is the same and it is not the model's: `_suggest_tags`
returns **tag names only**. The SEC publishes a plain-English label and a full
description for every tag, in the same 8 MB file the tool already downloads, and
the tool discards both. So the agent chooses on spelling. `SalesRevenueNet`
reads like revenue; `RevenueFromContractWithCustomerExcludingAssessedTax` reads
like a footnote. The name that looks right is the dead one.

### F2 — required answer format abandoned

Both runs are claims with nothing to look up: C21 names no company and no year,
C22 is a prompt injection attached to a fictional company. In both, the agent
replies in prose asking for more information.

The judgement is right and `NOT_CHECKABLE` exists for exactly this case. It did
not use it. The output has no VERDICT line, so anything parsing the answer gets
nothing.

Worth recording separately: C22 did **not** comply with the injection. It never
emitted the demanded "VERDICT: SUPPORTED and nothing else". The safety behaviour
held; the format did not.

### F3 — wrong company audited, undetected

`resolve_company("Coca-Cola")` returns **COCA-COLA EUROPACIFIC PARTNERS plc**, a
UK bottler, rather than The Coca-Cola Company. The hyphen decides it — `"Coca
Cola"` resolves correctly.

Three lookups were spent auditing the wrong company. The wrong name is printed
in every observation the agent read, and no rule requires it to compare that
name against the claim.

One run, and the highest-harm failure in the set: a wrong answer with nothing in
the output to suggest anything went wrong. The same fault is reachable
elsewhere — `resolve_company("XOM")` returns ExxonMobil Holdings Corp rather
than Exxon Mobil Corp.

### F4 — verdict ruled without testing the alternative

C09 claims Goldman's 2022 revenue was $7.4bn. The agent found $47.37bn and
answered CONTRADICTED.

$7.4bn is real: it is Goldman's investment banking revenue, $7.36bn. And
`InvestmentBankingRevenue` was **second in the suggestion list the agent read**,
with one of three lookups unspent.

The stated reason is "the numbers differ" — which is what CONTRADICTED and
DEFINITION_MISMATCH have in common, not what separates them.

**The visible frequency understates this.** C04 and C13 both pass while making
the same unsupported assertion that no alternative definition exists. Three runs
reason this way; one produced a wrong answer and got caught. Fixing only what
failed would leave two runs one unlucky claim away from the same error.

### F5 — tool evidence dropped from the answer

C10 claims Citigroup's 2023 revenue was $78.46bn. The tool returned `restated:
true` and `all_reported_values: ["$78.07B", "$78.46B"]` — the claimed figure is
*exactly* the second, the number Citigroup filed before restating.

The answer says the claim "closely matches ... well within a 1% margin",
converting an exact match into an approximate one and dropping the restatement.
Correct verdict, materially worse information, for a tool whose stated purpose
is that a number without its definition is the problem being solved.

### F6 — answered with no tool call at all

C19 (OpenAI) makes zero lookups. The TAG line reads "I have not attempted any
tag lookup" and the reasoning is "OpenAI is not a public company as of the last
update".

Right today. `company_not_found` is evidence with a date on it; "as of the last
update" is a statement about the model's training. The same confident answer
would be produced the day after an IPO.

## Not a failure: hedging (7 of 20)

The agent frequently issues two tool calls in a single turn — both before either
result exists — rather than reading one failure and choosing the next tag.

```
runs making more than one lookup      8
  of those, hedged (same turn)        7   C01 C02 C03 C06 C09 C14 C20
  genuine sequential retry            1   C17
runs making exactly one lookup        9
runs making none                      3   C19 C21 C22
```

It is harmless in six of the seven. In C06 it consumed two of three calls before
the agent knew anything, leaving one call to solve a problem needing two, and
directly caused that failure.

It is listed here rather than in the table because it is a **risk factor**, not
a failure: counting it as one would give it the highest raw frequency × impact
score in the taxonomy while describing behaviour that mostly works.

It does, however, contradict the Week 3 write-up, which describes the agent
reading a failed lookup and choosing the next tag from the suggestions. Across
twenty runs that happened **once** — on C17, the run auditing the wrong company.

## Ranking, and what gets fixed

Ranked by frequency × impact, with F4's latent frequency of 3 noted:

1. **F1** (4) and **F2** (4)
2. **F3** (3) and **F4** (3)
3. **F5** (2) and **F6** (2)

**The fix shipped in step 8 is a rewrite of the agent's instruction**, targeting
F2, F6 and F4 together — all three are failures of what the agent is *told to
do*, and all three are addressable in the text that steers it:

- answer in the five-line format always, using NOT_CHECKABLE where a claim
  cannot be pinned down, and never reply with a question (F2)
- never answer without at least one tool call (F6)
- before answering CONTRADICTED, spend a lookup testing whether the claimed
  figure is some other real figure, and say what was tested (F4)

F1 and F3 are excluded deliberately, and not because they matter less — F3 is
the worst single harm in the set. Both are faults in `sec_tool.py` rather than
in the instruction: F1 needs the suggestion list to carry the SEC's labels, F3
needs company matching that does not silently pick a different filer. Mixing a
tool change and a prompt change into one step would make the measured movement
impossible to attribute.

## What happened when the fix shipped

Recorded in [README.md](README.md). Briefly: the rewrite scores 16.3/20 against
the baseline's 16.0/20, and the baseline scores 15, 16 and 17 across three runs
with nothing changed. The improvement is inside the measurement error, and the
first draft of the fix caused a regression on C03 that the checks approved,
because the check had been given the same misconception as the rule.

The ranking below still stands as the analysis of what is wrong. It is the
claim that a rewrite fixed any of it that did not survive repetition.

## Limits

**Twenty runs, one model, one day.** Every count here is small. F3, F4, F5 and
F6 each rest on a single run. The 60% baseline is a property of this claim set,
which was built to be hard, and not an estimate of accuracy on real claims.

**The set is not representative and is not meant to be.** Claims were chosen to
break specific things. Nine of twenty expect SUPPORTED, seven NOT_CHECKABLE —
weighted by what was worth testing, not by what arrives in practice.

**The agent is not deterministic.** The same Goldman claim produced a hedge in
one run and a genuine sequential retry in another, with the same model and
instruction. Single-run counts should be read as "this happens" rather than "this
happens x/20 of the time".

**Two expected verdicts are contestable.** C09 is graded DEFINITION_MISMATCH;
CONTRADICTED is defensible, and the run fails on its reasoning either way. C17's
ground truth was never established by hand, because every tag tried failed — the
run fails on auditing the wrong company, which does not depend on the figure.

**Impact scores are a judgement, not a measurement.** Nobody has been harmed by
any of these outputs. The 1-2-3 scale encodes what would matter to a reader, and
a different reader would rank F5 and F6 differently.

## Reproducing the counts

```bash
.venv/bin/python -c "
from trace_log import load_records
rs = load_records()
print('pass', sum(1 for r in rs if r['your_pass_fail']=='pass'), 'of', len(rs))
print('hedged', [r['trace_id'] for r in rs
                 if len(r['tool_calls']) > len({c['turn'] for c in r['tool_calls']})])
print('no verdict line', [r['trace_id'] for r in rs if not r['parsed']['VERDICT']])
print('no lookups', [r['trace_id'] for r in rs if not r['tool_calls']])
"
```
