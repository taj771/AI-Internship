# Week 2 — RAG over documents, extending the Session 1 `/ask` API

Retrieval-Augmented Generation added to the Week 1 service: instead of answering
from what the model happens to remember, `/ask` retrieves relevant passages from
a vector database first and answers from those.

## Relationship to `week-1/`

This folder **starts from a copy** of the Session 1 project rather than editing
it in place. `week-1/` is a submitted, deployed artifact; freezing it means the
graded thing stays exactly as graded, and a reader can diff the two folders to
see precisely what RAG added.

Copied across: `main.py` (the Session 1 service), `demo_page.py`, and the
config. Left behind: `serve_stage1-5.py` and `test_all_stages.py`, which are
Week 1 teaching snapshots superseded by `main.py`.

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
| `vector_store.py` | Config, Pinecone client, embeddings, health check. |
| `demo_page.py` | Streamlit UI for calling the API. |
| `requirements.txt` | Dependencies, including `pinecone`. |
| `.env` | Secrets. Gitignored — never committed. |
| `.env.example` | The same keys with values removed, as a template. |
| `rag-vector-databases/` | The instructor's live-session notebook. Reference. |

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

## Known limits

- The Pinecone index dimension is fixed at creation (1536, for
  `text-embedding-3-small`). Changing embedding model means deleting and
  rebuilding the index, not editing a setting.
- Free Starter plan: one region (AWS `us-east-1`), 5 indexes, ~300k vectors.
- Free Render instances sleep after ~15 minutes idle, so a first request after
  a quiet period can take ~45 seconds to wake.
