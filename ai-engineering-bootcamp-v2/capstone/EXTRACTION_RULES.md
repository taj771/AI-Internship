# What counts as a claim

**Written 2026-08-31, before any extraction or labelling.**

Deciding what counts as a checkable claim is a judgement call, and it is the
judgement the whole project is about. If this file is written after the labels,
it records a rule invented to fit them. So it is written first, and where the
corpus later disagrees with it the disagreement is recorded at the bottom rather
than edited out of the top.

Scope for this pass: Goldman Sachs and JPMorgan Chase, FY2025 Form 10-K, Item 7.
Target 50 labelled claims. Both files in `data/`, provenance in `data/manifest.json`.


## The unit

One claim = one sentence from the MD&A that asserts one number about the filer.

A sentence carrying three figures produces three candidate claims, not one. The
agent answers one verdict per claim, so a sentence bundling revenue, expenses and
headcount cannot receive a single verdict that means anything.


## The rewrite, and why extraction is not slicing

MD&A prose is written from inside the company: *"We generated net earnings of
$17.18 billion for 2025."* The subject is "we" and the filer is never named.

`agent.audit()` takes a free-text claim, and rule 6 of its instruction forces
NOT_CHECKABLE when a claim names no company, no item or no year. Feeding raw MD&A
sentences to it would return NOT_CHECKABLE on nearly everything — and would look
exactly like the agent failing, when in fact the extractor never gave it a
company.

So every candidate is rewritten to be self-contained:

    raw    We generated net earnings of $17.18 billion for 2025.
    claim  The Goldman Sachs Group reported net earnings of $17.18 billion
           for fiscal year 2025.

The company comes from the manifest, not from the model. The fiscal year comes
from the sentence where it says one and from the manifest where it does not.
The figure is copied verbatim — never re-rounded, never unit-converted. A
rewrite that changes the number is not a rewrite, it is a different claim.

The raw sentence is kept alongside the rewrite in every record, because at
labelling time the question "is this claim faithful to the filing" can only be
answered against the original.


## The eight types

Every candidate gets exactly one. The type is assigned by the extractor and
corrected by hand during labelling; the disagreement between the two is itself a
number worth reporting.

| Type | What it is | Checkable against XBRL? |
|---|---|---|
| `STATED` | one firm-wide figure, one period — *"Net revenues were $58.28 billion for 2025"* | **yes**, one lookup |
| `DERIVED` | a change or comparison between periods — *"9% higher than 2024"* | **only with arithmetic** over two lookups. The engine does not do this today |
| `BALANCE` | a balance-sheet position — total assets, deposits, loans, equity | **no, for an engine reason** — see below |
| `SEGMENT` | a figure attributed to a named business — *"net revenues in Equities"* | rarely; segment tags exist but are inconsistently filed |
| `RATIO` | ROE, efficiency ratio, CET1, EPS, book value per share | **mixed** — EPS is tagged, most ratios are not |
| `NON_GAAP` | explicitly management-defined — "managed basis", "tangible", "excluding" | **no, by definition.** Not filed |
| `FORWARD` | a target, an expectation, a plan | **no ground truth exists.** Rejected |
| `NOT_A_CLAIM` | the figure sits in a definition, a cross-reference, a hypothetical, or a risk-disclosure range | rejected |

The first three are where the interesting result lives.

### `BALANCE` is a separate type because the failure is ours, not the filing's

`_annual_entries` in `sec_tool.py` keeps only facts whose period spans 350–380
days. A balance-sheet fact has no span at all — the SEC returns `start: null`,
because total assets is a position on a date, not a flow across a year. Every
such fact is dropped before the year filter ever runs.

So the tool returns nothing for total assets, and the agent, following its
instruction correctly, answers NOT_CHECKABLE. The claim is checkable. We cannot
check it.

Typing these separately keeps that distinct from claims that are genuinely not
in the filed data. Reporting them together would let an engine bug appear in the
results table as a property of MD&A, which is the kind of mistake this project
exists to catch. Whether to fix `_annual_entries` is decided after extraction,
on the measured cost — how many candidates the bug puts out of reach.

### `DERIVED` is separate because it is the majority and the engine skips it

A comparison is checkable — *"9% higher than 2024"* follows from two filed
figures and one division — but the engine does today what every claim checker
does: one lookup, one comparison. The arithmetic check is stretch goal 1 in the
build plan.

Typing derived claims now means the Phase 3 results can be reported **by type**
rather than in aggregate, which is the whole point: a single accuracy number
would hide that derived claims are harder than stated ones.


## Recall over precision

A junk candidate costs about five seconds to reject during labelling. A missed
claim is invisible forever — it cannot appear in the results, and nothing in the
pipeline downstream will ever reveal that it was dropped.

So the extractor is deliberately permissive and the rejection happens by hand.
`NOT_A_CLAIM` and `FORWARD` are expected to be a large share of the candidates.
That is the design working, not the extractor failing.

The corresponding obligation: hand-check a sample of what the extractor **drops**,
not only what it keeps. A bad extractor produces a labelled set that measures the
extractor rather than the auditor, and nothing downstream can detect that.


## What is out of scope, and stays out

- **Tables.** MD&A is roughly half tables, and the fetched text flattens them
  into unlabelled number runs. A figure without its row and column header is not
  a claim, it is a number. Prose only.
- **Multi-year claims.** *"...for 2025, 2024 and 2023 respectively"* — three
  claims wearing one sentence, and splitting them reliably needs the table the
  sentence is standing in for. Typed `NOT_A_CLAIM`.
- **Anything not about the filer.** Market-wide statements, Federal Reserve rate
  moves, index levels. There is no XBRL fact for the S&P 500.


## Known limits of this rule set

- The type is assigned from surface language. A sentence saying "compared with"
  is typed `DERIVED` whether or not the comparison is really the assertion.
- `NON_GAAP` is detected by keyword. Management can define a measure without
  using any of the flag words, and that claim will be typed `STATED` and then
  fail as a definition mismatch. That is a genuine ceiling and it belongs in the
  results, not in a footnote.
- Two banks, one fiscal year, one document section. Any base rate reported from
  this set is a base rate for large-cap US bank MD&A in FY2025 and nothing wider.


## Measurements taken before writing the extractor

Sentence counts over the two files, splitting on sentence-final punctuation:

| | GS | JPM |
|---|---|---|
| sentences | 1,485 | 1,506 |
| carrying a `$` figure | 163 | 197 |
| carrying only a `%` | 32 | 41 |
| numeric, either | 195 | 238 |
| ...using comparison language | 88 (45%) | 86 (36%) |

433 numeric sentences across the two banks, against a target of 50 labels. Not a
supply problem.

**One number disagrees with the build plan.** `CAPSTONE_BUILD_PLAN.md` reports
61% of Goldman's dollar-figure sentences as derived; this measurement puts
comparison language at 45% of numeric sentences. Different denominators and a
different sentence splitter, so the two are not directly comparable, and neither
has been hand-checked. Recorded rather than reconciled — the labelled set will
settle it, and that is a better arbiter than a second regex.


---

# Results of the first extraction pass

**Run 2026-08-31.** `extract.py` → `claims.jsonl`, 909 candidates.

| Type | Count | Share |
|---|---:|---:|
| `STATED` | 206 | 22.7% |
| `SEGMENT` | 103 | 11.3% |
| `BALANCE` | 106 | 11.7% |
| `DERIVED` | 106 | 11.7% |
| `RATIO` | 54 | 5.9% |
| `NON_GAAP` | 12 | 1.3% |
| `FORWARD` | 55 | 6.1% |
| `NOT_A_CLAIM` | 138 | 15.2% |
| `TABLE` | 129 | 14.2% |

**312 candidates are checkable in principle** (`STATED` + `DERIVED`) against a
target of 50 labels. Supply is not the constraint; labelling time is.

`TABLE` rows are one per flattened table, not one per figure. Those 129 rows
absorb roughly 2,100 figures that the first pass emitted as individual
candidates. They are written to `claims.jsonl` rather than discarded so that the
question "what did extraction throw away" has an answer on disk.

## Four defects found by hand-checking the output, and fixed

The first run's numbers were wrong in ways no downstream stage would have
revealed. All four came from reading twenty sampled candidates.

1. **The wrapper misstated the filing year.** `build_claim` interpolated the
   *figure's* year into "its fiscal year N Form 10-K", so a sentence about 2024
   was presented as coming from a 2024 filing. Every document here is the FY2025
   10-K. The two years are now separate parameters, and the distinction matters
   beyond tidiness: a claim about FY2024 made inside the FY2025 filing is the
   restatement case, which is the case the calibration layer most needs.

2. **STATED vs DERIVED was decided per sentence, not per figure.** *"Other
   principal transactions revenues were $1.59 billion for 2025, 66% lower than
   2024"* holds one of each — $1.59B is one lookup, 66% is two lookups and a
   division. Typed together, the by-type results table would have measured
   nothing. Now decided from the words immediately preceding each figure.

3. **Regulatory minimums were typed as claims.** *"Certain banking organizations
   are required to hold a capital conservation buffer of 2.5%"* is a fact about
   the rule, not about JPMorgan, and no XBRL fact corresponds to it.

4. **Dollar amounts were typed `RATIO`.** A ratio is a percent or a per-share
   amount. In *"average interest-earning assets were $3.8 trillion... the yield
   was 5.05%"*, the dollar total is the balance the ratio was computed on.

## Known residue, accepted rather than fixed

- **Tables leak into `NOT_A_CLAIM` and `FORWARD`.** Where the splitter merged a
  table with adjacent prose, the sentence is rejected under the wrong reason.
  It is still rejected, so no candidate is lost; only the reject-reason counts
  are slightly off. Not worth a second splitter.
- **Sentence-level types still outrank the per-figure call** for `SEGMENT`,
  `BALANCE`, `NON_GAAP` and `RATIO`. A sentence carrying both a segment figure
  and a firmwide one types both as `SEGMENT`. `flags` records every match, so
  correcting this is a re-sort of `claims.jsonl`, not a re-run.
- **The 61% figure is still unsettled.** The build plan's "61% of dollar-figure
  sentences are derived" does not reproduce here: per-figure, `DERIVED` is 11.7%
  of all candidates and 34% of the checkable ones. The denominators differ
  (figures vs sentences, all types vs dollar-figure-bearing only). The hand
  labels will settle it and neither number should be quoted until they do.
