# Path B — a validated LLM judge for grounding

The three code checks in `checks.py` catch four of the eight failures found by
reading traces. The other four need someone who understands what the numbers
mean. This is one attempt at automating one of them, and — the part that matters
more — at finding out whether the automation can be trusted.

## What is judged

Whether the REASONING line asserts anything the tool results do not support.

It is the right target for a judge precisely because code cannot see it. Every
example below parses cleanly, uses an allowed verdict, and passes all three
existing checks:

| Run | The reasoning | The problem |
|---|---|---|
| C06 baseline | "Microsoft does not file data for fiscal 2022 under **any** of the common revenue tags" | Three specific tags failed. The generalisation is the agent's, and it is false |
| C04 both | "**no alternative definition** explaining this large discrepancy" | One tag was tried. Absence was never tested |
| C18 baseline | "alternative revenue-related tags are **available only for previous years**" | No alternative was ever tried |
| C19 both | "OpenAI is not a public company **as of the last update**" | No tool call at all. A statement about training data |

Grounding matters more here than in most products. The whole claim of this
capstone is that a figure without its source is the problem being solved. An
auditor that decorates a correct verdict with unsupported assertions is doing
the thing it exists to catch.

## The labelled set

Forty runs: every one of the twenty claims, once under the baseline instruction
and once under the rewrite. Deterministic, so labels stay attached when anything
downstream is rerun. Selection and reasoning in `grounding.py`.

**11 ungrounded, 29 grounded — 28% prevalence.**

The human and the judge are given the same question. `DEFINITION` in
`grounding.py` is one string, rendered on the labelling page and interpolated
into the judge's prompt. If the two sides answered subtly different questions, a
disagreement between them would be uninterpretable: a low true positive rate
could mean the judge is bad, or that it was asked something else.

**Provenance of the labels, stated plainly.** They were drafted by Claude from
the traces and spot-checked by the author. They are not independent human
ground truth, and every rate below inherits that. A judge validated against
labels written with the same reasoning that wrote its prompt is being marked by
a sympathetic examiner. The right reading is "does a smaller model, given this
prompt, reproduce this analysis" — a real question, but a narrower one than the
assignment intends.

## Why not agreement

A judge that answers GROUNDED to everything — no model, no prompt, one return
statement — scores **72% agreement** on this set and catches nothing.

That is not a hypothetical. It is exactly what the first judge scored:

```
model         prompt    TP  FN  FP  TN     TPR     TNR   agree
gpt-4o-mini   v1         2   9   2  27    18%     93%     72%
always-GROUNDED                          0%    100%     72%
```

**gpt-4o-mini with the obvious prompt is indistinguishable from a constant
function by agreement, and misses 9 of 11 real failures.** Reported as
"72% agreement with human labels", it would have shipped.

## Results

```
model         prompt    TP  FN  FP  TN     TPR     TNR   agree
gpt-4o-mini   v1         2   9   2  27    18%     93%     72%
gpt-4o-mini   v2         3   8   2  27    27%     93%     75%
gpt-4o        v1         6   5   1  28    55%     97%     85%
gpt-4o        v2         9   2   0  29    82%    100%     95%

always-GROUNDED                          0%    100%     72%
```

**The prompt refinement helped, and the model mattered more.** v1 → v2 gains one
catch on mini and three on gpt-4o. But mini with the better prompt (27%) is
still far worse than gpt-4o with the worse one (55%). No wording rescued the
small model on this task.

That echoes Week 3, where gpt-4o-mini ruled JPMorgan's managed-basis claim
CONTRADICTED on one lookup and both larger models reached DEFINITION_MISMATCH.
The same capability gap shows up in judging the reasoning as in producing it —
which is a warning about judging a large model's output with a small one to save
money.

**gpt-4o with v2 is usable**: TPR 82%, TNR 100%. It catches 9 of 11 unsupported
assertions and has never once flagged a run the human called clean. As a
pre-filter for human review — flag these, read those — that is a genuine saving.

## What changed between v1 and v2

v1 states the question, shows the evidence, asks for a verdict. It is what
anyone writes first.

v2 adds an ordered procedure, and its rules come from the failure taxonomy
rather than from v1's mistakes:

1. split the reasoning into separate factual statements
2. for each, point at the exact field that supports it
3. treat claims about what does *not* exist as needing a lookup that was
   actually made
4. accept a statement scoped to what was tried; reject the same statement
   widened to "any tag"
5. accept comparisons between figures the agent holds
6. reject reasoning that names a company differently from every tool result

**Honest note on sequencing.** v2 was written before v1 was run, from the
analysis in `taxonomy.md`, not from watching v1 fail. The assignment's
instruction is to refine after reading the errors, and that is the stronger
method. What can be said is that v1's errors turned out to be concentrated in
exactly the places v2 addresses — eight of its nine misses are assertions of
absence or scope, covered by rules 3 and 4.

## The two the best judge still misses

**C03 baseline** — *"suggesting the use of a different revenue definition, such
as a 'managed basis' figure."* The judge calls it grounded because the
surrounding statements about the two tags are supported. The human labelled it
ungrounded because "managed basis" is specific knowledge about JPMorgan's
reporting that no tool result contains.

This one is genuinely borderline and was flagged as such when labelled. The
hedge covers whether managed basis explains the gap; it does not cover whether
managed basis exists. A different labeller could reasonably call it grounded,
and if they did the TPR would be 90%.

**C17 baseline** — *"No relevant revenue tags for Coca-Cola's 2023 fiscal year
could be found."* Every tool result names COCA-COLA EUROPACIFIC PARTNERS plc, a
UK bottler. The reasoning reports the finding as being about Coca-Cola.

v2 has an explicit rule for this — rule 6 names this exact case — and the judge
still misses it. Worth knowing: writing a rule into a prompt does not mean it
executes, which is the same lesson Step 8 taught about the agent's own
instruction.

## Limits

**The labels are not independent.** Drafted by Claude, spot-checked by the
author. Stated above, and it caps what any of these numbers can support.

**Eleven positives.** Every TPR here is a fraction with 11 in the denominator,
so one reclassified run moves it by 9 points. The v1 → v2 gain on mini is a
single run and should not be read as a real improvement.

**One borderline label carries 9 points of TPR.** If C03 baseline were labelled
grounded, gpt-4o/v2 would read 90%/100% instead of 82%/100%.

**Judged at temperature 0, so there is no run-to-run noise here** — unlike the
agent measurements in `README.md`. The instability found there does not apply to
these numbers. What does apply is the small denominator.

**Only one failure type is automated.** F1 (chose badly from a suggestion list),
F5 (dropped a restatement) and F3 (audited the wrong company) remain
human-only — and C17 shows the judge cannot yet be trusted with F3 even when
told to look for it.

## Reproducing

```bash
.venv/bin/streamlit run label_grounding.py       # the labelling bench
.venv/bin/python judge.py --compare              # gpt-4o-mini, both prompts
JUDGE_MODEL=gpt-4o .venv/bin/python judge.py --compare
```

Judgements are cached per model and prompt version in
`judgements_grounding.jsonl`, so reruns cost nothing. `--refresh` ignores the
cache.
