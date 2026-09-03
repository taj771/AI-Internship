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
