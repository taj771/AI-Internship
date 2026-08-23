# Week 5 — Durable memory for the SEC Claim Auditor

The Week 3 agent, unchanged in what it does, given a memory that outlives the
process running it.

**Live:** https://week5-memory-auditor.onrender.com
**Previous weeks:** [week-3](../week-3) (the agent) · [week-4](../week-4) (the evals that found the bugs this memory fixes)

---

## The distinction the week is about

Week 3's agent creates `InMemorySessionService()` *inside* `audit()`. Every audit
therefore begins knowing nothing — not across days, and not across two clicks of
the same button.

The tempting fix is to hoist that line out of the function so the session service
lives for the whole program. That is not memory. It is a longer prompt, held in
RAM, discarded when the process ends — and on Render's free plan the process ends
roughly fifteen minutes after anybody stops watching.

> **Context** is what the model can see this turn.
> **Memory** is what a *different process* can read back after this one has died.

Everything below is built to the second definition. `InMemorySessionService` is
still there, still per-audit, and is now labelled as what it is.

---

## The five questions

### What do I keep?

Three kinds of fact, and each one exists because Week 4 recorded it failing.

| Kind | Example | Why |
|---|---|---|
| `xbrl_tag` | Goldman Sachs files revenue under `RevenuesNetOfInterestExpense` | rediscovering this costs a failed lookup and an ~8 MB tag listing, every run |
| `company_alias` | when this user says "Coca-Cola" they mean CIK 0000021344 | `resolve_company("Coca-Cola")` returns a UK bottler, silently |
| `preference` | state figures in billions to two decimals | how one person wants answers written |

**And what is never kept: a figure.**

This is the rule the whole design rests on. `sec_tool.py::_annual_entries`
documents that every annual report republishes prior years, so one fact appears
in three filings and those values can disagree after a restatement. A remembered
figure would be correct on the day it was written and silently wrong afterwards —
and the agent would repeat it with the confidence of something it had checked.
Caching answers turns a claim auditor into a stale-answer generator, which is the
exact failure it exists to catch.

A company's *choice of tag* is a reporting convention, not a number. Goldman
filed revenue under `RevenuesNetOfInterestExpense` last year and will next year.
So memory stores the route and the figure is fetched live on every single audit.

> Remember where to look. Never what was found.

### When do I write it?

A tag fact is written only when a lookup **succeeded after an earlier lookup for
the same company and metric had failed**.

That condition is the gate's real work. A run that guessed `Revenues` for Bank of
America and got it first time has taught us nothing — `Revenues` is already the
model's opening guess, so remembering it would add a row that never changes a
future run. A run that tried `Revenues` for Goldman, was told it is not filed,
paid for the tag listing and then succeeded with `RevenuesNetOfInterestExpense`
has learned something a later run cannot derive.

> Remember only what could not have been guessed.

Two details that matter:

- **Pairing is by position in the trace, not by turn number.** Week 4 established
  that this agent often fires both tags in the *same* turn rather than reading
  the first result and reacting. Grouping by turn would see no failure preceding
  the success and would learn nothing from exactly the runs worth learning from.
- **At most three writes per run**, enforced in code. A cap the model is asked to
  respect holds until the run that does not respect it.

Facts a human supplies follow the same principle from the other direction — see
*Refusing what it cannot verify* below.

### Where does it live?

Postgres on Supabase in production; SQLite on disk locally. One code path, one
set of SQL, selected by whether `DATABASE_URL` is set.

**Not a JSON file, and not SQLite in production**, even though the assignment
offers both. Render's free plan has no persistent disk: a free service sleeps
after ~15 minutes idle and returns with an empty filesystem. A file-backed store
there passes every local test and then forgets everything between the demo and a
grader opening the link. This repo already records that hazard — it is why Week
4's annotation bench is deliberately not deployed (see `render.yaml`).

SQLite stays because it makes the store runnable with no account, no connection
string and no network, which is what keeps `test_memory.py` a test that actually
gets run.

```sql
CREATE TABLE memory_facts (
    scope, kind, key,        -- PRIMARY KEY (scope, kind, key)
    value,
    detail,                  -- JSON: cik, ticker, match terms, refusal reason
    source, trust,           -- provenance, read on the retrieval path
    observed_at,
    hits                     -- times recalled into a prompt
)
```

The primary key is the natural one. Learning the same fact twice is an UPDATE,
not a second row — which is the consolidation Path B offers as a stretch goal,
obtained by choosing the key well rather than by writing a merge pass. It is also
why the store cannot grow with use: auditing 300 Goldman revenue claims produces
**one** row, not 300.

### How do I get it back?

Before each turn, `recall(user_id, claim)` returns facts whose company is named
in the claim, plus this user's preferences, and drops anything quarantined. Those
are appended to the instruction — not to the user's message, so the claim
recorded in a trace stays exactly what was typed and Week 4's harness still
matches runs by claim text.

Retrieval is word-boundary string matching, not embeddings. Week 2's corpus was
prose, where a question and its answer share no words; that is what embeddings
are for. This corpus is a lookup table of a dozen facts keyed by company — a
claim either names the company or it does not. Boundaries rather than plain
substring matching because Citigroup's ticker is `C`, and `"C" in claim` is true
of every claim ever written.

Recall and the write both appear in the returned trace as steps, alongside
`THINK` / `ACT` / `OBSERVE`:

```
1. RECALL   GOLDMAN SACHS GROUP INC files revenue under "RevenuesNetOfInterestExpense"
2. ACT      lookup_filed_figure(company='Goldman Sachs', xbrl_tag='RevenuesNetOfInterestExpense', fiscal_year=2023)
3. OBSERVE  {'status': 'found', ...}
```

That is why the terminal, the Streamlit page and the saved JSONL all show memory
without any of them containing code that knows memory exists.

### When do I forget?

Honestly: **almost never, and not automatically.**

- Relearning a fact overwrites it, so a company that changes its tag corrects
  itself on the next run that has to recover.
- `remember.py forget <kind> <key>` and a per-fact button in the UI delete one.
- `remember.py clear` wipes everything.

There is **no decay pass and no consolidation pass**, and that is a decision
rather than an omission. The key shape caps the store at roughly
`companies × metrics-where-the-obvious-tag-is-wrong` — a dozen rows after
labelling every claim in four banks' MD&A. Decay is machinery for a store that
grows, and this one does not. `hits` and `observed_at` are recorded so that if it
ever does, the evidence for a decay rule is already there.

The one thing that *should* eventually expire is a `company_alias`, since a
merger can make one wrong. Nothing detects that today. Stated here rather than
discovered later.

---

## Refusing what it cannot verify — Path B

Provenance on every write (`source`, `trust`, `observed_at`), and a refusal path
for untrusted ingest.

The rule is enforceable rather than aspirational:

> **Nothing enters trusted memory that data.sec.gov has not confirmed.**

Not "nothing the model invented" — unenforceable, since an invented tag appears
in the same font as a real one. A fact becomes trusted only when a tool result,
fetched from the SEC in this process, says so. A human asserting something does
not raise its trust; it only decides which check runs.

```
$ .venv/bin/python remember.py poison

Attempting to plant a fabricated tag, as an untrusted user would:

  "Goldman Sachs files revenue under TotallyRealTag"

  → Refused and quarantined. data.sec.gov says: GOLDMAN SACHS GROUP INC files
    nothing under us-gaap/TotallyRealTag. The agent will never see this.

  trust:  quarantined
  source: user_stated_unverified

  recalled into the next prompt? no
  visible in `remember.py list`?  yes (as quarantined)
```

Refused facts are **stored, not dropped** — a refusal nobody can see is
indistinguishable from a write that never happened. What makes the provenance
load-bearing rather than decorative is one line on the read path:

```python
if not fact.is_usable:      # trust == "trusted"
    continue
```

Why each kind is safe under this rule:

- **Tag facts are global** because they cannot be asserted into existence. The
  only way to write one is for the SEC to have returned it, so a stranger on the
  public URL can only ever add true ones.
- **Aliases are per-user** despite being verified. Verification proves the target
  is a real filer; it does not prove it is the one the speaker meant. "Coca-Cola
  means KO" is a statement about intent, and one visitor should not decide it for
  everybody.
- **Preferences** are taken on a person's word alone, because they change wording
  and never a verdict or a figure. The worst a poisoned preference achieves is an
  ugly answer.

> A human can teach this agent things. A human cannot teach it things that are false.

---

## Does it actually work? — 40 recorded runs

`remember.py demo --reps 20`, recorded in
[`memory-demo.jsonl`](memory-demo.jsonl).

Twenty runs with memory off, one learning run, then twenty runs of a **different
claim about a different fiscal year** — so memory can only have supplied the
route, never the answer.

```
                           BEFORE (no memory)      AFTER (memory)
                           FY2022 claim            FY2023 claim
wasted lookups             19 of 20 wasted one     0 of 20 wasted one
total lookups              2 × 19,  1 × 1          1 × 20
verdict                    SUPPORTED × 20          SUPPORTED × 20
duration (median, range)   10.7s  (4.1–13.9)       8.6s  (3.5–16.5)
```

**19 of 20 baseline runs wasted a lookup. None of the 20 memory runs did.** Each
wasted lookup triggers `_suggest_tags`, which downloads roughly 8 MB — so across
those runs, ~152 MB not fetched from data.sec.gov.

### What these numbers do not support

**Not "memory always turns two lookups into one."** One baseline run — rep 15 —
led with `RevenuesNetOfInterestExpense` unprompted and used a single lookup. The
model occasionally guesses right without help, so the ranges touch at zero and
the harness prints `OVERLAPPING` rather than letting the medians imply a clean
separation.

**No timing claim at all.** The medians moved 10.7s → 8.6s, and the ranges are
4.1–13.9 against 3.5–16.5. That is near-total overlap. This is Week 4's finding
repeated: a median that moved inside a spread that did not.

Both of these are the reason the demo runs twenty times per side rather than
once. At three reps the lucky baseline run would probably not have appeared, and
this README would confidently say something untrue.

**Also unmeasured:** one claim, one company, one model, one day. The effect is
mechanical rather than statistical — an explicit tag in the prompt is hard for
the model to ignore — but it is demonstrated on a single company that happens to
file unusually.

---

## Proving it survives a restart

Three levels, weakest to strongest. They are listed in this order because the
weakest is the one a screenshot shows, and it should not be mistaken for the
others.

**1. The UI's "new session" button.** Clears Streamlit's session state. Proves
the page is not caching answers in a variable — worth proving, since that would
look exactly like memory. Does **not** restart the process, so on its own it
proves nothing about disk. The page says so on screen.

**2. The CLI.** Every `remember.py` invocation is a separate process. Anything
one command sees that another wrote is memory by definition.

```bash
.venv/bin/python remember.py audit "Goldman Sachs had revenue of \$47.37 billion in 2022."
.venv/bin/python remember.py list      # ← different process, fact is there
```

**3. The test.** `test_fact_survives_a_real_process_restart` spawns a subprocess,
has it write a fact, waits for it to exit, and reads the fact back from the
parent. Closing and reopening a connection would prove nothing — the interpreter
and its caches are still alive. Only an exit proves the fact reached a disk.

```bash
.venv/bin/pytest test_memory.py -v      # 14 passed
```

---

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp ../week-4/.env .env          # OPENAI_API_KEY, SEC_USER_AGENT

.venv/bin/pytest test_memory.py -v            # no key, no network, no database
.venv/bin/streamlit run streamlit_app.py      # the page
.venv/bin/python remember.py --help           # the CLI
```

Set `DATABASE_URL` to a Postgres URI to use Postgres instead of a local file.
The table creates itself on first use — `CREATE TABLE IF NOT EXISTS` — so an
empty database is a working database and there is no migration step to forget.

| Command | What it does |
|---|---|
| `remember.py audit "<claim>"` | one audit, full trace |
| `remember.py list` | every fact, with provenance and hit counts |
| `remember.py alias "Coca-Cola" KO` | the alias fix, verified against the SEC |
| `remember.py prefer units "billions"` | store a presentation preference |
| `remember.py poison` | plant a false tag, watch it be refused |
| `remember.py forget <kind> <key>` | delete one fact |
| `remember.py demo --reps 20` | the before/after above |

---

## Known limits

- **One shared identity on the public URL.** There is no login, so every visitor
  shares `MEMORY_USER_ID=public-demo` and therefore shares preferences and
  aliases. Tag facts are shared by design regardless; preferences being shared is
  a consequence of not building auth, not a decision.
- **Anyone can write to the deployed store.** The gate bounds what they can write
  — only facts the SEC confirms — but a visitor can add rows and delete them
  through the UI. Acceptable for a demo, not for anything real.
- **Retrieval is string matching.** A claim that names a company in a way not in
  its `match` list will not recall its fact. "The bank formerly known as Goldman"
  retrieves nothing. It fails by missing, never by matching the wrong company,
  which is the right direction to fail in.
- **Aliases never expire.** A merger can make one wrong and nothing detects it.
- **Local venv is Python 3.13; the deploy pins 3.12.3.** Same drift as week 4.
  The pin is deliberate — it stops a Render default bump moving under a working
  deploy — but it means local and deployed runtimes are not identical.
- **The measurement is one claim, one company, one model, one day.** See *What
  these numbers do not support* above.
- **`memory-demo.jsonl` is committed evidence, but it is my run.** Rerunning it
  will not reproduce those numbers exactly; the agent is non-deterministic and
  that is the entire point of running it twenty times.
