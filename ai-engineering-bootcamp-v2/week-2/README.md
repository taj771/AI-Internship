# Week 2 — RAG over documents, extending the Session 1 `/ask` API

Retrieval-Augmented Generation added to the Week 1 service: instead of answering
from what the model happens to remember, `/ask` retrieves relevant passages from
a vector database first and answers from those.

## Relationship to `week-1/`

This folder **starts from a copy** of the Session 1 project rather than editing
it in place. `week-1/` is a submitted, deployed artifact; freezing it means the
graded thing stays exactly as graded, and a reader can diff the two folders to
see precisely what RAG added.

Copied across: `main.py` (the Session 1 service) and the config. Left behind:
`serve_stage1-5.py`, `test_all_stages.py` and `demo_page.py`, which are Week 1
teaching artifacts — the stage files are superseded by `main.py`, and the demo
page drove those stage servers rather than this one. `streamlit_app.py` replaces
it.

## What Week 1 already gave us

The Session 1 service is not a throwaway. It contributes four things RAG needs
and most tutorials bolt on later:

| From Week 1 | Why it matters here |
|---|---|
| Structured output (`Answer` schema) | answers stay machine-readable once grounded |
| Validation + one retry | a malformed answer still cannot reach a caller |
| `tokens_used` | RAG inflates prompts; this makes the inflation visible |
| `cost_usd` | so the price of a chunking decision is measurable, not guessed |

Retrieval slots in *before* the model call. Everything after it — timing, token
accounting, the guardrail, the response schema — is untouched.

## Files

| File | What it does |
|---|---|
| `main.py` | FastAPI service. HTTP layer only. |
| `vector_store.py` | Config, Pinecone client, embeddings, retrieval, health check. |
| `ingest_corpus.py` | Loads a folder of documents through `POST /ingest`. |
| `streamlit_app.py` | UI for ingest and ask. Holds no keys; calls the API. |
| `corpus/` | The six provided Northwind Robotics documents. |
| `NORTHWIND-CORPUS-KEY.md` | The corpus's own answer key. Kept **outside** `corpus/` so it cannot be ingested and answered from. |
| `requirements.txt` | Pinned dependencies. |
| `.env` | Secrets. Gitignored — never committed. |
| `.env.example` | The same keys with values removed, as a template. |
| `rag-vector-databases/` | The instructor's live-session notebook. Reference. |

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /ingest` | Chunk, embed and store a document. |
| `POST /ask` | Retrieve, then answer from the retrieved text with citations. |
| `GET /debug/retrieve?q=...` | Retrieval alone, with scores. No LLM call. |
| `GET /documents` | Documents in the index, with chunk counts. |
| `GET /health/pinecone` | Reachability, index dimension, vector count, chunk settings. |

`POST /ask` and `GET /debug/retrieve` both accept `document_id` to restrict
retrieval to one document.

`POST /ask` accepts `use_rag: false` to run the Week 1 path unchanged, which is
the cheapest way to show what retrieval actually contributes.

## Running it

```bash
.venv/bin/uvicorn main:app --port 8001          # API
.venv/bin/python ingest_corpus.py corpus/       # load the corpus
.venv/bin/streamlit run streamlit_app.py        # UI
```

Port 8001 rather than the usual 8000 only because 8000 was occupied locally;
nothing depends on it.

## Live

| | URL |
|---|---|
| Week 2 (this) | https://week2-rag-api.onrender.com |
| Week 1 (frozen) | https://week1-ask-api.onrender.com |

## Setup

```bash
cd ai-engineering-bootcamp-v2/week-2
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill in the two keys
```

Check the configuration loads before anything else:

```bash
.venv/bin/python vector_store.py
```

## Environment variables

Two are secret and two are not, and they are handled differently on Render.

| Variable | Secret | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | yes | chat completions **and** embeddings — one key covers both |
| `PINECONE_API_KEY` | yes | free Starter key from https://app.pinecone.io |
| `PINECONE_INDEX_NAME` | no | which index to use (`week2-rag`) |
| `EMBEDDING_MODEL` | no | `text-embedding-3-small`, used at ingest **and** query |

On Render the two secrets are declared `sync: false` — Render prompts for them
in the dashboard and never stores them in git. The two non-secrets are written
into `render.yaml` with their values so the deploy is reproducible.

**The failure this causes:** a `sync: false` variable left blank in the dashboard
still deploys successfully, and then fails on the first request. The health
endpoint below exists to make that a ten-second diagnosis.

## Why Pinecone and not Chroma

Chroma is simpler — a library writing to a local folder, no account needed. It
is also the wrong choice *here*, for one specific reason:

> Render's free tier has an ephemeral filesystem. Files written at runtime do
> not survive a restart, and free services sleep after ~15 minutes idle.

A local Chroma directory therefore works perfectly in development and silently
empties in production. The service wakes, the index is gone, and `/ask` returns
confident answers grounded in nothing — with no error anywhere. Pinecone holds
the data outside Render, so a restart cannot lose it.

Chroma would be the better choice for a laptop-only project.

## Why one embedding model, named once

`EMBEDDING_MODEL` is read in a single place and used for both storing documents
and searching them. An embedding model maps text into its own coordinate system;
two models produce different systems, so storing with one and searching with
another yields meaningless results.

The trap is that when both models share a dimension count, **nothing raises an
error** — retrieval just returns nonsense that looks like a working system.

The vector length is derived from the model name rather than configured
separately, because two settings that must agree are one setting waiting to
disagree.

## Add-ons

| Add-on | Status | Where |
|---|---|---|
| 1 — Golden-set eval | done | `golden_set.json`, `evaluate.py`, Evaluate tab |
| 2 — Chunk size comparison | done | three sizes measured; default changed to 400 |
| 4 — Metadata filtering | done | `document_id` filter on `/ask` and `/debug/retrieve` |
| 5 — Batch ingest | done | `ingest_corpus.py corpus/` — whole folder, one command |
| 3 — Hybrid search | not attempted | see below |
| 6 — Reranking | not attempted | see below |

### Metadata filtering

Retrieval can be restricted to one document before the search runs, rather than
searching everything and discarding afterwards. The distinction matters because
`top_k` is applied *after* filtering, so the filter frees slots rather than just
tidying results.

Measured on *"What are the password requirements?"*:

| | chunks from POL-207 (security) |
|---|---|
| no filter | **4 of 5** — a handbook chunk scored 0.505 and displaced one |
| `document_id=POL-207` | **5 of 5** |

Filtering to the *wrong* document is the better demonstration: the same question
restricted to POL-118 (facilities) returns five facilities chunks and the service
**refuses**, because facilities genuinely contains no password rules. The filter
restricts what is searched; it does not merely relabel the output.

The dropdown in the UI is populated from `GET /documents` rather than hardcoded,
so it cannot go stale when a document is added or renamed.

### Hybrid search and reranking — considered, not attempted

**Hybrid search** (keyword + vector) would suit this corpus, which is full of
exact identifiers — `POL-207`, `SPEC-WB9` — that embeddings handle poorly, since
an identifier carries almost no semantic content. The obstacle is structural
rather than conceptual: Pinecone serverless stores dense vectors, so keyword
search needs a second index and a fusion step. That is a rebuild of a working
retrieval path, not an addition to it.

**Reranking** would likely improve quality most of the three. Fetching 20 chunks
and reordering them with a model that reads question and passage *together* is
strictly more accurate than comparing two independently-computed embeddings, and
it targets exactly the near-miss that H5 exposed. It needs a third-party rerank
API and adds latency to every question.

Both are the right next steps. Neither is a sensible thing to start immediately
before a deadline, and metadata filtering delivered a measurable improvement
using metadata already stored.

## Golden-set evaluation

`evaluate.py` scores the service against questions whose answers are known.
Five come from the corpus's own README; five more (H1-H5) were added because the
original five passed at every chunk size tried, which meant the set could not
discriminate rather than that the settings were equivalent.

```bash
.venv/bin/python evaluate.py --api https://week2-rag-api.onrender.com
```

| # | Question | Expected answer | Source | Retrieval hit | Faithful | Correct |
|---|---|---|---|---|---|---|
| Q1 | How many remote days are allowed? | Up to 3 days per week | `POL-101` | ✅ | ✅ | ✅ |
| Q2 | What is the mileage rate? | 45p/mile, journeys over 50 miles | `POL-114` | ✅ | ✅ | ✅ |
| Q3 | How quickly must a lost laptop be reported? | Within 1 hour, to the security desk | `POL-207` | ✅ | ✅ | ✅ |
| Q4 | What is the WB-9 payload limit? | 25 kg, including the tote | `SPEC-WB9` | ✅ | ✅ | ✅ |
| Q5 | What is the parental leave policy? | *(not in the corpus — must refuse)* | **refusal** | — | ✅ | ✅ |
| H1 | I drove 20 miles to a client meeting. How much can I claim? | Nothing — under the 50-mile threshold | `POL-114` | ✅ | ✅ | ❌ |
| H2 | Does the 25 kg payload include the tote? | Includes the tote | `SPEC-WB9` | ✅ | ✅ | ✅ |
| H3 | I am fully remote. Can I claim mileage from home? | Yes — measured from home when fully remote | `POL-114` | ✅ | ✅ | ✅ |
| H4 | Can I claim a receipt from 45 days ago? | Yes, with director approval (30–90 days) | `POL-114` | ✅ | ✅ | ✅ |
| H5 | Can the WB-9 run at 1.5 m/s in a narrow aisle near a person? | No — adaptive limits apply | `SPEC-WB9` | ✅ | ✅ | ✅ |

**9/10.** Retrieval hit 9/9, refusal 1/1, faithfulness 10/10, $0.001 per run.

Four measures rather than one, because they fail separately and imply opposite
repairs:

- **retrieval hit** — did a *retrieved chunk* carry the fact? Checked at chunk
  level, not document level. An earlier version checked only whether the right
  document came back, and passed on a question whose answer was in none of the
  five retrieved chunks.
- **faithful** — is the answer supported by the passages? An LLM judge, so a
  second opinion rather than a measurement, and reported as such.
- **correct** — does the answer state the known fact?

H1 shows why they cannot be collapsed: it is **faithful but incorrect**. The
service refused, and a refusal asserts nothing, so it is perfectly grounded and
still the wrong answer.

## H1 — a limitation left in deliberately

H1 asks about a 20-mile journey. POL-114 says *"travel over 50 miles may be
claimed at 45 pence per mile"*, and that chunk **is retrieved, top of the list**.
The service refuses anyway.

It is not hallucinating; it is over-refusing. The context states a rule and
answering requires one step of arithmetic, which the grounding prompt's *"if the
context does not contain the answer"* is read as excluding. H4 makes the same
kind of inference successfully (45 days > 30), so this is a judgement rather than
a rule.

The strictness that produces the clean parental-leave refusal is the same
strictness that refuses this. Loosening it would risk the behaviour the rest of
the assignment demonstrates, so it is documented rather than tuned away.

## Measured: chunk_overlap often does nothing

`chunk_overlap` is not "repeat the last N characters". RecursiveCharacterTextSplitter
splits into whole sentences and carries **whole sentences** into the next chunk,
as many as fit the overlap budget. It never cuts a sentence to create overlap.

So when every sentence is longer than the budget, the overlap is **zero** — and
nothing warns you. Measured on prose whose sentences run 117–170 characters:

| Setting | Actual overlap observed |
|---|---|
| overlap = 100 | **0, 0** — no sentence fits |
| overlap = 200 | 117, 170 |
| overlap = 300 | 117, 170 (no room for a second sentence) |

The configured default stays at 100 to match the assignment, but on ordinary
business prose that produces no overlap at all. Anything relying on overlap for
protection at chunk boundaries should verify it is actually happening rather
than assume the setting took effect.

## Known limits

- The Pinecone index dimension is fixed at creation (1536, for
  `text-embedding-3-small`). Changing embedding model means deleting and
  rebuilding the index, not editing a setting.
- Free Starter plan: one region (AWS `us-east-1`), 5 indexes, ~300k vectors.
- Free Render instances sleep after ~15 minutes idle, so a first request after
  a quiet period can take ~45 seconds to wake.
- Pinecone indexes asynchronously. A document ingested and queried within a
  second or two may not be found yet — the store returns a view that has not
  caught up. This is not an error and needs no retry logic, but it does mean a
  demo that ingests and immediately asks can look broken.
- Retrieval always returns `top_k` chunks, however poor. It cannot report "no
  relevant match", so a question outside the corpus still produces five passages
  and the refusal has to come from the model reading them.
- Both endpoints are public and unauthenticated. `/ingest` is a write endpoint,
  so anyone with the URL can add documents to the index and spend embedding
  credit. Acceptable for coursework; not for anything real.
