# How much of a bank's own story can be checked?

**Written 2026-09-03, before any of it was built.**

A companion to the auditor, using the same machinery pointed at a different
question. The auditor asks whether a particular claim is true. This asks how
much of what management writes is *checkable at all*, and how that has changed
over sixteen years.

The design is written down first for the same reason
[EXTRACTION_RULES.md](EXTRACTION_RULES.md) was: the rules below decide what the
results say, and rules written after the results are rules invented to fit them.
One of them — the value-blind pinning rule in stage 2 — would produce a
beautiful and entirely meaningless number if it were relaxed, and the temptation
to relax it will only exist once the numbers are on screen.


## Why this exists

The auditor's headline is an accuracy figure measured against an answer key, and
an answer key costs hours of a person's time. Forty-six of the fifty labels
behind the current numbers were drafted by rule rather than established by hand,
and a review found several of them wrong. Every number carries that caveat.

Nothing below needs an answer key. Stage 1 needs no model at all. Stages 2 and 4
are graded against filed XBRL, which is the ground truth rather than an estimate
of it. That is the point: these are the measurements that survive not having
labels, and they are worth having for exactly that reason.


## Scope, and the year the study starts

**JPMorgan Chase, fiscal years 2010 to 2025.** Item 7 only.

Not 2000. XBRL did not exist before 2009 — the SEC phased it in from 2009 for
large filers and 2011 for everyone. Measured on JPMorgan's own filed facts:

| period ending | facts filed |
|---|---:|
| 2006 | 1 |
| 2007 | 89 |
| 2008 | 720 |
| 2009 | 1,891 |
| 2010 | 3,351 |
| 2011–2025 | ~3,000 each |

Every claim from 2000–2008 would bin as "no tag" because the tagging regime did
not exist, which has nothing to do with disclosure practice and would wreck the
result. 2010 is the first year with full coverage.


## Stage 1 — how much is checkable

Extract every numeric claim from each year's Item 7 and bin it:

- **has a reachable tag** — a `us-gaap` concept with annual data for that year
- **no reachable tag** — and record *which* reason, because they are different
  findings: a percentage (nobody files one), a segment figure (tagged, but the
  SEC's JSON API strips dimensions — verified across 41,100 facts, zero carry a
  segment qualifier), a non-GAAP measure (not filed by definition), or a
  narrative sub-component with no counterpart at all.

No agent, no model, no labels. Roughly 8,000 claims.

The five bins carry a plain-language name in the app, because the first reader
to see the figure asked what the legend meant. A legend that needs its author
standing next to it is not a legend. The mapping, one to one:

| `structural` / `has_tag` | app label | FY2025 |
|---|---|---|
| `reachable`, tag found | Checkable | 125 · 45% |
| `derivable` | A change, not an amount | 44 · 16% |
| `tagged_unreachable` | One part of the bank | 53 · 19% |
| `reachable`, no tag found | Nothing filed by that name | 20 · 7% |
| `rarely_tagged` + `never_tagged` | Never filed by anyone | 36 · 13% |

They are ordered by how recoverable each one is — arithmetic away, a harder
parser away, possibly our own retrieval's fault, impossible — and the chart's
hues follow that order rather than a stock categorical ramp.

**This is a fact about the disclosure regime, not about any company.** MD&A is
not required to be tagged, so a low checkable share is normal and legal, not a
red flag. The finding is its size, and how it moves.


## Stage 2 — of the checkable, how consistent

Compare the figure in the prose against the figure that was filed.

### The rule that decides whether this means anything

**A tag is pinned to a sentence using the wording alone. The values are never
consulted when deciding which tag a sentence refers to.**

If a pin were accepted only when the numbers agreed, every mismatch would be
defined out of existence and the consistency rate would be 100% by construction.
That is not a hypothetical: the auditor's own calibration shipped a feature that
was true by definition, reported it as the strongest predictor of failure, and
it took an outside review to catch. The same shape of error here would invalidate
the entire study rather than one column of it.

So: decide the tag from the words, then look at the number once, and never
revisit the pin.

### Pinning, and its cost

Measured on the existing 959-claim corpus, pinning value-blind:

| | |
|---|---:|
| firmwide dollar claims (pinnable in principle) | 354 |
| one tag clearly wins on wording | **78** |
| two or more tags plausible | 236 |
| nothing matches distinctively | 40 |

Eight percent of all claims. Scaled to sixteen years, roughly **650 pinned
comparisons**, about forty per filing.

Coverage is reported alongside the consistency rate, always. "Of the claims we
could pin, X% agreed" is honest; quoting X% without the coverage is not.

### The one thing here that needs a person

**Hand-check twenty pins.** Not to label verdicts — to estimate how often the
pinning itself is right. Without it there is no answer to "how do you know you
matched the right tag", and the consistency rate has no error bar.

Twenty minutes, and it is the only human time this study requires.


## Stage 3 — one tag across sixteen years

For a single concept: the filed value each year, the value management wrote
where a claim pinned, and two things marked on the same axis.

**Restatements** — where a year's figure changed between filings. `sec_tool`
already detects these.

**Tag death** — where a concept stops being filed. Two are already known:
Goldman abandoned `PrincipalTransactionsRevenue` after 2010; JPMorgan abandoned
`FinancingReceivableAllowanceForCreditLosses` after 2021, at the CECL changeover.
Both still return HTTP 200 with historical data, so a dead tag looks exactly like
a live one.

That last point refines the principle Week 5 was built on. "Store the route, not
the answer" — but **routes expire too**, predictably, when an accounting standard
changes. A remembered tag can go as stale as a remembered figure.

Years with no pinned claim are drawn as gaps. Never interpolated.


## Stage 4 — does tool access change what a model says

The same claims, the same question, different models and tool configurations.
Graded automatically: for a pinned claim the filed value **is** the truth, so
"did the model state a figure within 1% of what was filed" needs no judgement.

| | runnable |
|---|---|
| gpt-4o, no tools | yes — 24 of 25 declined in an earlier run |
| gpt-4o + SEC lookup | yes — the current agent |
| Claude | only if `ANTHROPIC_API_KEY` is exported; not set in this environment |
| Gemini | supported in `agent.py`, but the free tier allows ~20 requests a day |
| web search | different API surface; not attempted |

Three outcomes are counted separately, and the middle one is not a failure:
**correct**, **declined**, **wrong**. A model that says "I don't know" is
behaving correctly, and an experiment that scored it as a miss would be
measuring the wrong thing.


## What this study cannot say

- **Nothing about whether a company is misleading anyone.** MD&A is not required
  to be tagged. An unverifiable claim is unverifiable, not false.
- **Nothing about the natural contradiction base rate.** That needs hand-labelled
  verdicts, which is exactly the cost this study is designed to avoid.
- **Nothing about `DEFINITION_MISMATCH`.** Segment figures are unreachable
  through the public JSON API, and that verdict is where human judgement is
  genuinely required.
- **Nothing that generalises past one bank.** One filer, one section, sixteen
  years.


## Build order, and why it is this order

1. **Stage 1.** Stands alone, needs no model, cannot fail the way the
   calibration did.
2. **Stage 4** on the existing corpus — cheapest, and the most quotable.
3. **Stage 2**, once pinning is worth the effort.
4. **Stage 3**, which is the visual and depends on the others.

Built on a branch. The live URL deploys from `main` and is the mandatory
deliverable; nothing unfinished touches it.

---

# Stage 1 results

**Run 2026-09-03.** JPMorgan Chase, FY2011–FY2025, 6,655 numeric claims across
15 filings. No model, no agent, no labels.

FY2010 is missing: its Item 7 heading matches none of the patterns in
`fetch_filings.py`, and the year was not worth chasing — it is XBRL's first year
and the noisiest.

| FY | claims / 10k chars | segment share | tag rate | checkable |
|---|---:|---:|---:|---:|
| 2011 | 16.9 | 3% | 87% | 53% |
| 2013 | 16.0 | 6% | 86% | 50% |
| 2015 | 10.9 | 7% | 79% | 44% |
| 2017 | 7.9 | 5% | 86% | 50% |
| 2019 | 6.0 | 15% | 87% | 44% |
| 2021 | 6.3 | 14% | 85% | 48% |
| 2023 | 7.4 | 20% | 81% | 44% |
| 2025 | 7.0 | 19% | 86% | 45% |

**Numeric density more than halved**, 16.9 claims per 10,000 characters to 7.0,
normalised for document length. The fall happens between FY2013 and FY2019 and
then flattens.

**Tag availability never moved.** Among claims a single lookup could reach, a
matching tag with data for that year existed 79–88% of the time throughout, with
no trend.

**Segment-level claims rose from 3% to about 20%**, and that is the mechanism
behind the decline in checkable share. Less of the narrative is verifiable than
in FY2011 — not because the tagging regime weakened, but because the prose moved
down to segment level, where the public JSON API cannot follow.

## A finding that was retracted before it was reported

The first run showed segment claims rising from **0%** in FY2011 to 20% in
FY2025, which looked like a clean story about disclosure moving to segment level.

It was an artifact. JPMorgan reorganised: in FY2011 the segments were Investment
Bank, Retail Financial Services, Card Services, Commercial Banking, Treasury &
Securities Services and Asset Management. CCB, CIB and AWM did not exist. The
detector knew only the modern names, so it found no segment claims in 2011 —
not because there were none, but because it was looking for words that had not
been coined.

With every name the filer has used in the period, FY2011 is 3%, not 0%. The rise
is real and smaller.

The general form of this is worth stating, because it will recur in any long
time series: **a detector tuned on recent documents will report the past as
empty.** The trend it produces is its own blind spot with a slope on it.

## Honest limits

- **Nothing here says JPMorgan is misleading anyone.** MD&A is not required to be
  tagged. An unverifiable claim is unverifiable, not false.
- **`has_tag` means a plausible tag exists**, not the correct one. The pinning is
  keyword and rarity matching; its precision is unmeasured until stage 2's
  twenty hand-checked pins.
- **The segment detector is still a keyword heuristic.** It is now fair across
  years, but residual asymmetry in how densely each era names its segments cannot
  be ruled out.
- **Why the density fell is not established.** It could be regulatory disclosure
  changes, a house-style shift, or content moving from prose into tables — which
  this pipeline discards. Measured that it happened, not why.
- **One filer, one section, fifteen years.** Nothing generalises past that.

---

# Stage 2 results

**Run 2026-09-03.** The question was: of the claims that can be checked, how many
agree with what was filed. The answer is that the prior question — which filed
concept does this sentence mean — is not solved, and stage 2 spent itself
establishing that rather than measuring agreement.

## Five ways of joining a sentence to a concept, and what each scored

Every method pins value-blind, and is scored by whether the pinned tag's filed
value matches the figure within tolerance.

| method | rate |
|---|---:|
| lexical, IDF-weighted, over tag labels | 4% |
| lexical, over labels plus official definitions | 2% |
| dense embeddings of the sentence, nearest of ~800 | 6% |
| dense embeddings of the phrase naming the figure | 6% |
| value matching alone | see below |

Peak cosine similarity anywhere in the corpus was **0.79**; the median 0.50. No
sentence in fifteen years lands close to any tag definition, and the reason is
that they are written for different readers. A standards body defines
`CommercialPaper` as "carrying value as of the balance sheet date of short-term
borrowings using unsecured obligations issued by banks"; management writes
"total commercial paper liabilities were $51.6 billion". Same concept, almost no
shared language.

## Value matching looked like the answer and was mostly coincidence

29.0% of claims match exactly one filed tag within 1%. That looks decisive until
it is compared against a null: multiply every figure by a random off-one factor,
destroying true matches while preserving magnitudes, and **25.3% still match
exactly one tag**. The excess over chance is 3.7 points.

**About 87% of unique value matches are numeric coincidence.** With roughly 800
filed values clustered in the billions and a 1% window, one unrelated tag lands
in the window most of the time. Sampled examples: "net income increased by $1.6
billion" uniquely matched `IncomeTaxExaminationPenaltiesAndInterestAccrued`;
"net interest income was $14.2 billion" uniquely matched `SecuritiesLoaned`.

The perturbation null is the tool that settled this, and it needs no labels.

## The metric was wrong, and that was the largest error

Scoring a join by whether the values agree conflates two failures: pinning the
wrong tag, and pinning the right tag for a figure that legitimately differs — a
segment, a subtotal, a change. The 4-6% figures above are therefore a lower
bound on retrieval accuracy, not a measurement of it.

Tested directly: on 22 claims where the correct tag could be established
independently, dense retrieval placed it in the **top 5 every time** and top 1
in 64%. Retrieval was working far better than any of the numbers above could
see. That gold set was itself selected by embedding confidence, so it is
optimistic — but the direction of the error is established.

## What the rebuilt stage 2 does

`join.py`. Retrieve every tag above a null-calibrated cosine threshold — usually
none, sometimes one or two — then compare. Retrieval never sees the figure, so
the comparison is a test rather than a selection.

The threshold, 0.55, comes from the null: at that bar a value match is about
five times more likely to be real than coincidental. At 0.30 the null matches
almost as often as the real data.

Levels and changes are compared differently, which the first version did not do.
"Net income was $17.4 billion" is a level and a tag holds it. "Net income
increased by $1.6 billion" is a change and no tag holds it — it is this year
minus last. Comparing a change against a level can never match, so every
change-shaped claim was either a false alarm or written off as unverifiable.
FY2011 alone has 225 of them in 896 claims.

## Results, and they are not yet a product

Over 900 claims:

| | | |
|---|---:|---|
| verified | 12 (1%) | figure matches a retrieved concept |
| review | 153 (17%) | concept found, figure differs |
| no counterpart | 735 (82%) | nothing filed resembles it |

**A 17% review queue that is almost all false alarms is worse than no tool.**
An analyst abandons that in ten minutes.

And the threshold discards correct joins: `CommercialPaper` at cosine 0.508 and
`StockholdersEquity` at 0.527 are both right and both below the bar. The
value-first method found them because the number narrowed the field first.

So the two directions fail differently, and the finished design almost certainly
uses both with different standards of evidence:

- **value-first** — high yield, 87% coincidence, and structurally blind to
  disagreement, since a misstated figure never enters a value shortlist
- **text-first** — low coincidence, misses correct joins, and can detect
  disagreement

That combination is the next piece of work, not a threshold tweak at the end of
a long night.

## What stage 2 established

The join is the bottleneck for automated MD&A verification. Not the comparison,
which is arithmetic. Five retrieval methods, a perturbation null, and a direct
recall test all point at the same place, and none of it needed a hand-labelled
verdict.

## Honest limits

- **No agreement rate is reported, and none can be** from these joins. Where
  the value selects the candidate, agreement is the selection criterion.
- **The 82% "no counterpart" is an upper bound**, not a measurement. It mixes
  genuinely unfiled figures with retrieval misses, and stage 1's looser matcher
  put the same quantity at about 50%. The truth is between two measurement
  choices, and neither pins it.
- **Recall@5 of 100% rests on 22 claims** whose gold set was selected by
  embedding confidence. Optimistic by construction.
- **Nothing here detects a misstatement.** The verified bucket verifies; the
  review bucket is a queue for a person, and it mixes real disagreements with
  scope differences that are not errors at all.

---

# Stage 3 results — one concept across fifteen years

**Run 2026-09-03.** `stage3_panel.py` → `stage3_panel.svg`.

The plan was two lines per concept, the filed figure against the figure
management wrote, diverging where they disagree. Stage 2 produced 61 verified
joins across 3,915 claims, so for any single concept there are a handful of MD&A
points and not a series. A line through four dots spread over fifteen years
would be invented data, so the filed value is drawn as a line — it is complete —
and MD&A appears as individual points where a claim actually verified. Gaps are
drawn as gaps.

| concept | filed | MD&A points | restated |
|---|---|---:|---|
| Cash from operating activities | 2011–2025 | 4 | **2016, 2017, 2018, 2019** |
| Cash from financing activities | 2011–2025 | 4 | — |
| Investment banking revenue | 2011–2025 | 4 | 2016, 2017 |
| Preferred stock dividends | 2011–2025 | 5 | — |
| Buyback authorised | **2015–2022** | 6 | — |
| Tier 1 capital | **2011–2013** | 1 | — |

Two things the panel is for.

**Figures move after they are published.** JPMorgan's filed operating cash flow
changed between filings in four consecutive years. A figure cached in 2016 was
correct that day and wrong within a year, silently.

**Concepts stop being filed.** `TierOneRiskBasedCapital` ends in 2013, at the
Basel III transition that replaced the capital definitions;
`StockRepurchaseProgramAuthorizedAmount1` ends in 2022. Both still return HTTP
200 with their historical data, so a dead tag is indistinguishable from a live
one until you look at which years it covers.

That is a correction to the principle week 5 was built on. "Store the route, not
the answer" was right about figures and incomplete about routes: **routes expire
too**, predictably, at accounting-standard changes, and unlike a stale figure a
dead tag fails silently rather than loudly.

One bug caught by looking at the rendering: verified *changes* were being plotted
against a level axis, putting a correct match visibly off the line where it read
as a disagreement that was not there. Levels only now.

---

# Stage 4 results — does structure beat tool access

**Run 2026-09-03.** `stage4_grid.py` → `stage4_grid.json`. Forty of the 61
verified joins, gpt-4o, three conditions, one question: which tag did the
company file this figure under.

| condition | exact tag | declined |
|---|---:|---:|
| model alone, no tools | 5/40 — **12%** | 35 |
| model + the SEC lookup tool | 28/40 — **70%** | 8 |
| + the retrieved candidate shortlist | 36/40 — **90%** | 4 |

**The hypothesis was that an agent with full tool access could not reach the
pipeline's answer. It half held.**

Tool access is the largest single effect in this study: 12% to 70%. An agent
that can look things up is dramatically better than one reasoning from memory,
which is the case for having built the Week 3 agent at all.

But one stage of structure adds twenty points on top of full tool access. Same
model, same tool, same budget — the only difference is being handed the
candidates retrieval found. So the pipeline is not redundant with the agent; it
does something the agent cannot do for itself.

The thirty-five declines without tools are their own result, and consistent with
the earlier citation test: asked for a filed figure it cannot know, the model
mostly refuses rather than inventing.

## The caveat that travels with the 90%

**Condition 3 is advantaged by construction.** It is handed retrieval's
candidates, and retrieval helped build the test set, so 90% is an upper bound
rather than a measurement of skill. Conditions 1 and 2 are clean — neither sees
retrieval output — so the 12% and the 70% stand on their own.

## Honest limits

- Forty claims, one model, one filer. The 20-point structure margin is inflated
  and its true size is not established here.
- The test set is claims where the pipeline already succeeded, so it asks
  whether an agent can reach an answer known to be reachable — not how either
  performs on the 80% of claims where no counterpart exists at all.
- Claude and Gemini were not run: no Anthropic key in this environment, and the
  Gemini free tier allows about twenty requests a day.
