# groundgate — refuse to let an answer out when its source is fiction

An assistant is asked how much the company spent on AWS last quarter. It answers
**"$1.2 million, source: invoice INV-88421"**. The figure is right. There is no
invoice INV-88421.

That pairing is worse than a wrong number, and for a specific reason: **a wrong
number is caught by the next person who looks; a wrong source is caught by
nobody.** Whoever checks the figure finds it correct and carries the citation
into a report, where it acquires the authority of something that was verified.

```python
from groundgate import Gate, Run, ToolCall

gate = Gate(source=my_invoice_system)
v = gate.check(Run(answer=assistant_reply, tool_calls=recorded_calls))

v.outcome          # "pass" | "flag" | "block"
v.looked           # did it consult anything at all?
v.citation_exists  # is the source it named real?
print(v)           # BLOCK: cited `INV-00000`, which is not in the source
```

## The three checks, in the order they fail

| | |
|---|---|
| **Did it look?** | An answer produced with no tool call — or with calls that all came back empty. |
| **Does the source exist?** | The cited identifier is absent from the system of record. **This is the one nobody does.** |
| **Does the source say that?** | The identifier is real and holds something else. |

Only the second needs a system of record. Where there is none — an assistant
summarising free text with no identifiers — the gate degrades to the first check
and says so, rather than returning a confident pass.

## Running it

```bash
../capstone/.venv/bin/python -m pytest test_groundgate.py -q   # 12 tests
../capstone/.venv/bin/python demo_sec.py                       # the measurement
../capstone/.venv/bin/python blind_probe.py --n 40             # re-measure it yourself
```

`demo_sec.py` needs no key — it reads a recorded run. `blind_probe.py` calls a
model and needs `OPENAI_API_KEY` in `../capstone/.env`.

## The measurement

Asked which accounting concept a figure was filed under, **with the figure
stripped out of the question** so it had to be recalled rather than repeated:

```
gpt-4o · 40 claims · JPMorgan · no tools

  declined to answer                 29
  named a concept the filer files     9
  FABRICATED a concept                2     18% of the answers it committed to
```

The two, verbatim, each beside a value it also supplied:

```
us-gaap:AllowanceForLoanAndLeaseLosses
us-gaap_RevenueFromContractWithCustomerExcludingAssessedTaxIncreaseDecrease
```

Both exist in the us-gaap taxonomy. Both are spelled correctly. **JPMorgan has
never filed either one.** The gate blocks both without a human reading anything,
because "is this identifier in the filer's own fact set" is decidable.

## Honest limits

**It does not decide whether an answer is true.** All three checks can pass on an
answer that is wrong — a real invoice, quoted correctly, answering the wrong
question. This narrows the ways an answer can be *unfounded*. It does not
establish that it is founded.

**Fabrication depends heavily on how the question is put.** Two experiments in
`../capstone` asked the same thing with the figure left in and found 1 fabrication
in 25 and 1 in 40, because the model could decline and be entirely right —
it declined 24 times out of 25. The 18% here comes from a question where
declining costs it the whole answer. Quote the condition with the number, or the
number means nothing.

**18% is one run of forty at temperature 0.** Treat the shape as the finding and
the figure as indicative. `blind_probe.py --seed N` will draw a different sample.

**"Did it look?" defaults to flag, not block.** An answer that consulted nothing
may still be right, and blocking it would stop correct answers. It must not be
*credited*, though: in the capstone's evaluation nine of fifty runs made no tool
call, all nine answered with the commonest correct label, and all nine scored as
hits. The honest score fell from 68% to 62% when they stopped being credited.

**The default citation parser is deliberately strict.** It reads `Source: X` on
its own line and nothing else. A looser parser would return None on answers that
did cite something, turning a blocked fabrication into a quiet pass — so callers
are expected to pass their own `extract_citation`.

## Files

| | |
|---|---|
| `groundgate.py` | the gate, the three checks, and why each default is what it is |
| `sources.py` | `DictSource` for tests, `SecTagSource` for the real one |
| `blind_probe.py` | runs the blinded question and records the answers |
| `demo_sec.py` | the gate over those answers — the proof, not a mock |
| `test_groundgate.py` | 12 tests, two of them for bugs the capstone made first |
