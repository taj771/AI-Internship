"""Week 2 — the Session 1 `/ask` service, extended with retrieval.

Starts as a copy of week-1/main.py, which was built up across five live-session
stages: a bare endpoint, then structured output, then a validation guardrail,
then a per-request model override, then cost reporting. Those five layers are
still here and still doing their jobs.

Week 2 adds retrieval. This file stays the web layer -- it turns HTTP requests
into function calls and back. Everything about storing and searching documents
lives in vector_store.py, so a failure has an obvious address: an HTTP problem
is in this file, a Pinecone problem is in that one.
"""

import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

# Week 2. Everything about storing and searching documents lives in its own
# file; this one stays the web layer. Importing it here does not connect to
# anything -- the Pinecone connection is made on first use, so a missing key
# cannot stop the service from starting.
import vector_store

# Load .env from this folder so the key is found regardless of shell working directory.
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

app = FastAPI()

# The OpenAI client comes from vector_store, which builds it on first use rather
# than at import.
#
# Week 1 constructed it here as `client = OpenAI()`, at module level. That reads
# the key immediately, so a missing key raises during import -- before FastAPI
# has registered a single route. On Render that is a service which builds fine,
# boots, dies, and retries, with the actual cause several screens into the
# deploy log and no endpoint alive to explain it. Deferring construction means
# the service starts, /health/pinecone answers, and the answer names the missing
# key.
#
# Sharing vector_store's client also means one place knows how to build it, so
# retrieval and generation cannot end up authenticated differently.
def client() -> OpenAI:
    """The shared OpenAI client, created on first use."""
    return vector_store.openai_client()

# Stage 4 default. The starter shipped "gpt-4o"; this deployment defaults to the
# mini model because the Render URL is public and unauthenticated, so anyone who
# has it can spend real credit. ~18x cheaper per call. Callers can still pass
# {"model": "gpt-4o"} explicitly, so the Stage 5 cost comparison still demos.
DEFAULT_MODEL = "gpt-4o-mini"

# Stage 5 — per-1K-token input/output USD (derived from OpenAI list prices).
MODEL_PRICES_PER_1K: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "o3-mini": (0.0011, 0.0044),
}


class Answer(BaseModel):
    """Structured model output — this is what turns a chatbot into a component."""

    answer: str
    confidence: float = Field(ge=0.0, le=1.0)
    sources_needed: bool

    # Week 2. Which documents the answer actually drew on, by document_id.
    #
    # Making this a schema field rather than asking for citations in prose is
    # the difference between a claim and a check. Prose citations cannot be
    # verified without reading the answer; a list of ids can be compared against
    # the ids that were actually retrieved, so an answer citing a document that
    # was never handed to the model is detectable in one line of code.
    #
    # Defaults to empty because a refusal cites nothing, and a refusal is a
    # correct outcome when the context does not contain the answer.
    citations: list[str] = Field(default_factory=list)


class AskRequest(BaseModel):
    """Typed request body so bad input is rejected before we spend tokens."""

    question: str
    force_bad: bool = False  # Stage 3 demo knob — first attempt breaks schema on purpose.
    model: str | None = None  # Stage 4 — optional override to swap models live.

    # Week 2.
    use_rag: bool = True
    top_k: int | None = None

    # Restrict retrieval to particular documents.
    #
    # Narrows the shelf before searching rather than searching everything and
    # discarding afterwards, which matters because top_k is applied after the
    # filter: an unfiltered search that returns four relevant chunks and one
    # stray returns five relevant ones once filtered. The filter buys back a
    # slot, it does not merely tidy the result.
    document_id: str | None = None
    source: str | None = None

    # use_rag exists to make the difference demonstrable in one request. Sending
    # the same question with use_rag false runs the Week 1 path -- straight to
    # the model, no documents -- so the before and after can be compared without
    # redeploying anything. It is also the honest way to check whether retrieval
    # is contributing at all: if both answers are identical, the model already
    # knew, and the corpus is proving nothing.


class RetrievedChunk(BaseModel):
    """One retrieved chunk, as reported back to the caller.

    The chunk text is deliberately not included. It is already reflected in the
    answer, it can be several thousand characters per request, and /debug/retrieve
    exists for when the full passages are what you want to see.
    """

    id: str
    score: float
    document_id: str | None = None
    chunk_index: int | None = None
    source: str | None = None


class AskResponse(BaseModel):
    """Typed response so callers always get the same shape back.

    The Week 1 fields keep their Week 1 meanings, which matters because anything
    already calling this endpoint should not break: tokens_used and latency_ms
    still describe the generation call. Retrieval's cost is reported separately
    rather than folded into them silently.

    cost_usd is the one exception -- it is now the true total for the request,
    generation plus the embedding of the question, with cost_breakdown showing
    the split. Reporting only part of what a request cost would make the number
    quietly wrong, which is worse than changing what it counts.
    """

    answer: Answer
    tokens_used: int
    model: str
    latency_ms: int
    cost_usd: float

    # Week 2.
    rag: bool
    top_k: int | None = None
    # Echoed so a caller comparing a filtered run against an unfiltered one can
    # tell which is which. A result that does not state its own filter cannot be
    # attributed.
    retrieval_filter: dict | None = None
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    retrieved: list[RetrievedChunk] = Field(default_factory=list)
    retrieval_ms: int = 0
    embedding_tokens: int = 0
    cost_breakdown: dict[str, float] = Field(default_factory=dict)

    # Whether every id the model cited was actually among the retrieved chunks.
    #
    # A citation the model invented is worse than no citation: it makes an
    # ungrounded claim look sourced, and a reader who does not check will
    # believe it. Since the retrieved ids are known exactly, this is a real
    # check rather than a guess -- and it is the reason citations is a schema
    # field of ids rather than a sentence of prose.
    citations_verified: bool = True
    unknown_citations: list[str] = Field(default_factory=list)

    # True when the model gave the fixed refusal sentence. Surfaced as a flag so
    # a caller -- or an evaluation script -- can count refusals without parsing
    # prose. Refusing is a correct outcome, not an error, so this is reported
    # rather than raised.
    refused: bool = False


class IngestRequest(BaseModel):
    """A document to store, plus optional overrides.

    chunk_size and chunk_overlap are per-request rather than server-wide because
    the method this assignment teaches is "change one thing, measure again".
    Needing a server restart to compare 500 against 800 would make that loop
    slow enough that nobody does it.
    """

    text: str
    document_id: str
    source: str | None = None  # e.g. the original filename, for citations.
    metadata: dict | None = None  # Extra fields stored alongside every chunk.
    chunk_size: int | None = None
    chunk_overlap: int | None = None


class IngestResponse(BaseModel):
    """What ingestion did.

    The first three fields are the ones the endpoint is specified to return; the
    rest exist because they answer questions you would otherwise have to guess
    at. Echoing the chunk settings matters most: when you are comparing runs,
    a response that does not state which settings produced it is a result you
    cannot attribute.
    """

    document_id: str
    chunks_indexed: int
    status: str

    source: str
    chunk_size: int
    chunk_overlap: int
    replaced_existing_chunks: int
    embedding_model: str
    embedding_tokens: int
    cost_usd: float


# --- Grounding ---------------------------------------------------------------

# The prompt that turns a general-purpose model into one that answers from your
# documents. Every line of it is load-bearing:
#
# "ONLY the context" -- without it the model blends what it read in the context
#   with what it remembers from training, and the two become indistinguishable
#   in the output. A blended answer is the worst outcome available here: it
#   looks sourced, and part of it is not.
#
# "quote or closely paraphrase" -- discourages the model from generalising a
#   specific figure into a vaguer claim that is harder to check.
#
# "say so plainly" -- a model asked to answer will nearly always produce
#   something. Refusal has to be named as an acceptable outcome or it will not
#   happen; "I don't know" is a correct answer to a question the documents do
#   not address, and a system that cannot say it will confabulate instead.
#
# "confidence at or below 0.3" -- pins refusal to a numeric range so it can be
#   detected programmatically rather than by reading prose.
#
# "cite the document_id" -- and the context blocks below are labelled with those
#   ids, so the model has something exact to copy rather than a title to invent.
# The exact sentence the model is told to use when the context is insufficient.
#
# Fixing the wording is what makes refusal countable. "Say so plainly" produces a
# different sentence every time, so measuring how often the system declines means
# reading every answer by hand; an exact string can be matched in one line. That
# matters as soon as you are comparing configurations -- a chunk size that halves
# the refusal rate is a result, but only if refusals can be counted.
REFUSAL_TEXT = "I don't have enough information to answer that."

# "in the citations field, not in your answer text" is an addition to the
# assignment's wording, and it is there because the original produced a real
# defect. Asked only to "cite the document_id", the model sometimes wrote the id
# into the answer prose -- "(source: customer-support-faq)" -- and left the
# citations list empty. That is worse than not citing at all: the verification
# check compares the citations list against the retrieved ids, so an empty list
# passes trivially while the actual citation goes unchecked. Naming the field
# removes the choice, which is the same fix that the block numbering and chunk
# ids needed.
GROUNDING_PROMPT_TEMPLATE = """Answer using ONLY the context below.
If the context does not contain the answer, say:
"{refusal}"
Cite the document_id of each chunk you used, in the `citations` field. Do not put
citations in your answer text.

Context:
{context}

Question: {question}"""


# How one retrieved chunk is presented to the model. The document_id is repeated
# on every block because that is the string the model must copy into citations,
# and a label it can see next to the text is far more reliably copied than one
# it has to remember from a list elsewhere in the prompt.
#
# This block has been wrong twice, the same way both times, and the pattern is
# worth stating: whenever it offered more than one identifier, the model cited
# the wrong one.
#
#   "[1] document_id: handbook | ..."     -> cited "1"
#   "document_id: handbook
#    source: ... (chunk handbook#0)"      -> cited "handbook#0"
#
# Neither was disobedience; the instruction said document_id and two plausible
# ids were present. The fix is not a firmer instruction, it is removing the
# alternatives. The chunk id was only ever here for human debugging, and it is
# already returned to the caller in retrieved_chunk_ids, so the model never
# needed to see it.
CONTEXT_BLOCK_TEMPLATE = """document_id: {document_id}
source: {source}
{text}"""

# Text used when retrieval returned nothing at all -- an empty index, or a
# question so unrelated that no chunk cleared the search. Stating it explicitly
# beats sending an empty Context section, which reads as a formatting bug and
# invites the model to answer from training data anyway.
NO_CONTEXT_PLACEHOLDER = "(No relevant context was found in the document store.)"


def build_grounding_prompt(question: str, matches: list[dict]) -> str:
    """Assemble the prompt sent to the model: instructions, context, question."""

    if not matches:
        context = NO_CONTEXT_PLACEHOLDER
    else:
        context = "\n\n".join(
            CONTEXT_BLOCK_TEMPLATE.format(
                document_id=match.get("document_id", "unknown"),
                source=match.get("source", "unknown"),
                text=match.get("text", "").strip(),
            )
            for match in matches
        )

    return GROUNDING_PROMPT_TEMPLATE.format(
        refusal=REFUSAL_TEXT, context=context, question=question
    )


def is_refusal(answer_text: str) -> bool:
    """Did the model decline for lack of context?

    Matched against the fixed refusal sentence rather than inferred from
    confidence or from phrases like "I cannot", because those also appear in
    genuine answers ("the policy cannot be waived"). The model does not always
    reproduce the sentence with identical punctuation or capitalisation, so the
    comparison is loosened just enough to survive that without matching anything
    else.
    """
    normalise = lambda s: "".join(c for c in s.lower() if c.isalnum() or c.isspace()).strip()
    return normalise(REFUSAL_TEXT) in normalise(answer_text)


def compute_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Turn real usage into dollars — same prompt, different model, different cost."""

    prices = MODEL_PRICES_PER_1K.get(model, MODEL_PRICES_PER_1K[DEFAULT_MODEL])
    input_per_1k, output_per_1k = prices
    return (prompt_tokens / 1000 * input_per_1k) + (completion_tokens / 1000 * output_per_1k)


def call_model_structured(question: str, model: str) -> tuple[Answer, int, int, int]:
    """
    Stage 2 center: OpenAI structured output forces exactly the Answer schema.
    Returns parsed answer plus token counts from billing metadata.
    """

    completion = client().chat.completions.parse(
        model=model,
        messages=[{"role": "user", "content": question}],
        response_format=Answer,
    )

    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError("Model returned no parseable structured output")

    usage = completion.usage
    total = usage.total_tokens if usage else 0
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    return parsed, total, prompt_tokens, completion_tokens


def call_model_unsafe(question: str, model: str) -> tuple[Answer, int, int, int]:
    """
    Stage 3 demo path: free-form JSON call, then validate locally.
    The bad instruction makes confidence a string so Pydantic rejects it reliably.
    """

    completion = client().chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": (
                    f"{question}\n\n"
                    "Reply with ONLY a JSON object using keys answer, confidence, sources_needed. "
                    "Set confidence to the string 'very high' (not a number)."
                ),
            }
        ],
    )

    raw = completion.choices[0].message.content or ""
    # Guardrail: refuse malformed output instead of passing it through to clients.
    answer = Answer.model_validate_json(raw)

    usage = completion.usage
    total = usage.total_tokens if usage else 0
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    return answer, total, prompt_tokens, completion_tokens


@app.get("/")
def root():
    """Say what this service is. A deployed API that 404s at its own root reads
    as broken to anyone who pastes the bare URL into a browser."""

    return {
        "service": "week2-rag-api",
        "endpoints": {
            "POST /ingest": "store a document so questions can retrieve from it",
            "POST /ask": "ask a question",
            "GET /health/pinecone": "is the vector store reachable?",
        },
        "interactive_docs": "/docs",
        "returns": ["answer", "tokens_used", "model", "latency_ms", "cost_usd"],
    }


@app.get("/health/pinecone")
def pinecone_health():
    """Report whether the vector store is reachable and correctly configured.

    Exists because the alternative diagnosis is miserable. A deploy where the
    Pinecone key was never filled in on the dashboard starts up perfectly and
    then fails on the first real request, with an error that says nothing about
    which of half a dozen things went wrong. Opening this URL answers that in
    one step, and answers it identically on a laptop and in production.

    Returns 503 rather than 200 when unhealthy, because "unhealthy" is a real
    failure and monitoring tools read the status code, not the body. A broken
    service that returns 200 with sad news inside is a service that looks fine
    on every dashboard.

    Read-only on purpose: create_if_missing stays False so that visiting a
    health URL cannot create infrastructure as a side effect. Creating the index
    is an explicit setup action -- `python vector_store.py`.
    """

    report = vector_store.health_check()
    return JSONResponse(status_code=200 if report["ok"] else 503, content=report)


@app.get("/documents")
def documents() -> dict:
    """List the documents currently in the index, with chunk counts.

    Exists so a caller can offer a filter without hardcoding the corpus. A
    dropdown built from a fixed list goes stale the moment a document is added
    or renamed, and then filters to a document that is no longer there --
    returning nothing, with no error to explain why.
    """

    try:
        found = vector_store.list_documents()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Could not list documents: {type(exc).__name__}: {exc}",
        ) from exc
    return {"documents": found, "total_chunks": sum(d["chunks"] for d in found)}


@app.get("/debug/retrieve")
def debug_retrieve(
    q: str,
    top_k: int = vector_store.DEFAULT_TOP_K,
    document_id: str | None = None,
    source: str | None = None,
) -> dict:
    """Run retrieval alone and show what comes back. No LLM is called.

    The point is to make retrieval falsifiable on its own. A RAG system that
    answers badly has two possible causes -- it retrieved the wrong passages, or
    it retrieved the right ones and wrote a poor answer from them -- and the
    finished pipeline cannot tell you which. This endpoint answers that in one
    request, before generation exists to confuse the question.

    A GET with a query string rather than a POST body, so it can be driven from
    a browser address bar while you are exploring.

    Example:

        curl -s "http://127.0.0.1:8001/debug/retrieve?q=how%20many%20days%20can%20I%20work%20from%20home"

        curl -s "http://127.0.0.1:8001/debug/retrieve?q=parental+leave&top_k=3"

    Or simply open it in a browser:

        http://127.0.0.1:8001/debug/retrieve?q=parental leave

    Scores are cosine similarity: 1.0 means the same direction in meaning-space,
    0.0 unrelated. Read them against each other rather than as percentages --
    what matters is whether the right passage is at the top and how far clear of
    the rest it is.

    Note that this always returns top_k results, however poor. Ask about
    something absent from the corpus and you still get five passages, all with
    low scores. That is the failure mode worth watching for: retrieval never
    says "I found nothing", it just returns its least-bad guesses.
    """

    try:
        return vector_store.search(
            q,
            top_k=top_k,
            metadata_filter=vector_store.build_filter(
                document_id=document_id, source=source
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Retrieval failed: {type(exc).__name__}: {exc}",
        ) from exc


@app.get("/ingest")
def ingest_usage():
    """Same courtesy as GET /ask: explain, rather than return a bare 405."""

    return {
        "detail": "This endpoint takes POST, not GET.",
        "send": {"text": "...document text...", "document_id": "my-doc-1"},
        "optional": ["source", "metadata", "chunk_size", "chunk_overlap"],
        "try_it_in_a_browser": "/docs",
    }


@app.post("/ingest")
def ingest(body: IngestRequest) -> IngestResponse:
    """Store a document so that /ask can retrieve from it.

    Chunks the text, embeds each chunk with the configured embedding model, and
    upserts the vectors into Pinecone with document_id, chunk_index and source
    attached to each one.

    Re-ingesting the same document_id replaces that document rather than adding
    a second copy: its existing chunks are deleted first, so a shortened
    document cannot leave orphaned chunks behind still quoting text that was
    removed.

    Example:

        curl -s -X POST http://127.0.0.1:8001/ingest \\
          -H "Content-Type: application/json" \\
          -d '{
                "text": "Northwind Bank reported net interest income of $1.84 billion in 2025, up 12% from the prior year.",
                "document_id": "northwind-2025",
                "source": "northwind_ar_2025.pdf"
              }'

    With chunk settings overridden for a comparison run:

        curl -s -X POST http://127.0.0.1:8001/ingest \\
          -H "Content-Type: application/json" \\
          -d '{"text": "...", "document_id": "doc-1", "chunk_size": 500, "chunk_overlap": 150}'

    Returns 400 for input this service cannot act on -- empty text, missing
    document_id, an overlap that is not smaller than the chunk size, or metadata
    Pinecone will not store. 400 rather than 500 because the caller sent
    something wrong and can fix it; 500 would claim the fault was ours and give
    them nothing to act on.
    """

    try:
        result = vector_store.ingest_document(
            text=body.text,
            document_id=body.document_id,
            source=body.source,
            chunk_size=body.chunk_size,
            chunk_overlap=body.chunk_overlap,
            extra_metadata=body.metadata,
        )
    except ValueError as exc:
        # Every ValueError raised in vector_store is a statement about the
        # caller's input, so they map cleanly onto 400.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Anything else is OpenAI or Pinecone failing on us. 502 says "an
        # upstream service let me down", which is true and is not the caller's
        # fault to fix.
        raise HTTPException(
            status_code=502,
            detail=f"Ingestion failed: {type(exc).__name__}: {exc}",
        ) from exc

    return IngestResponse(**result)


@app.get("/ask")
def ask_usage():
    """A browser address bar can only issue GET, so visiting the endpoint yields
    405 — technically correct and completely unhelpful. Explain instead."""

    return {
        "detail": "This endpoint takes POST, not GET. A browser can only send GET from the address bar.",
        "send": {"question": "What is RAG in one sentence?", "model": "gpt-4o-mini (optional)"},
        "try_it_in_a_browser": "/docs",
    }


@app.post("/ask")
def ask(body: AskRequest) -> AskResponse:
    """Answer one question from the stored documents, with citations.

    Retrieval is inserted between "a question arrived" and "call the model" --
    the only place the Week 1 pipeline had a gap. Everything downstream of that
    call is unchanged: the same structured-output path, the same validation
    guardrail, the same retry, the same token and cost accounting. The model is
    handed a longer prompt; it is not handed a different pipeline.

    Send use_rag false to run the Week 1 behaviour instead, for comparison.
    """

    model = body.model or DEFAULT_MODEL
    top_k = body.top_k if body.top_k is not None else vector_store.DEFAULT_TOP_K

    matches: list[dict] = []
    embedding_tokens = 0
    retrieval_ms = 0
    metadata_filter: dict | None = None
    prompt = body.question

    if body.use_rag:
        metadata_filter = vector_store.build_filter(
            document_id=body.document_id, source=body.source
        )
        retrieval_start = time.perf_counter()
        try:
            found = vector_store.search(
                body.question, top_k=top_k, metadata_filter=metadata_filter
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            # Retrieval failing is not the same as the model failing, and it is
            # reported as its own thing. Falling back to an ungrounded answer
            # here would be the wrong kindness: the caller would get a fluent
            # response with no indication that it came from nothing.
            raise HTTPException(
                status_code=502,
                detail=f"Retrieval failed: {type(exc).__name__}: {exc}",
            ) from exc

        retrieval_ms = int((time.perf_counter() - retrieval_start) * 1000)
        matches = found["matches"]
        embedding_tokens = found["embedding_tokens"]
        prompt = build_grounding_prompt(body.question, matches)

    last_error: str | None = None

    # Stage 3: one retry keeps the logic legible while still protecting callers.
    for attempt in range(2):
        try:
            start = time.perf_counter()

            # First attempt with force_bad uses the unsafe path; retry uses structured output.
            use_bad_path = body.force_bad and attempt == 0
            if use_bad_path:
                answer, tokens_used, prompt_tokens, completion_tokens = call_model_unsafe(
                    prompt, model
                )
            else:
                answer, tokens_used, prompt_tokens, completion_tokens = call_model_structured(
                    prompt, model
                )

            latency_ms = int((time.perf_counter() - start) * 1000)
            generation_cost = compute_cost_usd(model, prompt_tokens, completion_tokens)
            embedding_cost = vector_store.embedding_cost_usd(embedding_tokens)

            # Compare what the model claims it used against what it was given.
            #
            # Chunk ids are accepted as well as document ids. A citation of
            # "handbook#0" points at a chunk that genuinely was retrieved, so
            # treating it as fabricated would be wrong -- the flag needs to mean
            # "this document was never shown to the model", or it stops being
            # worth reading.
            retrieved_document_ids = {
                m.get("document_id") for m in matches if m.get("document_id")
            }
            retrieved_chunk_ids = {m["id"] for m in matches}
            valid_citations = retrieved_document_ids | retrieved_chunk_ids
            unknown_citations = [
                cited for cited in answer.citations if cited not in valid_citations
            ]

            return AskResponse(
                answer=answer,
                tokens_used=tokens_used,
                model=model,
                latency_ms=latency_ms,
                cost_usd=round(generation_cost + embedding_cost, 8),
                rag=body.use_rag,
                top_k=top_k if body.use_rag else None,
                retrieval_filter=metadata_filter if body.use_rag else None,
                retrieved_chunk_ids=[m["id"] for m in matches],
                retrieved=[
                    RetrievedChunk(
                        id=m["id"],
                        score=m["score"],
                        document_id=m.get("document_id"),
                        chunk_index=m.get("chunk_index"),
                        source=m.get("source"),
                    )
                    for m in matches
                ],
                retrieval_ms=retrieval_ms,
                embedding_tokens=embedding_tokens,
                cost_breakdown={
                    "generation_usd": round(generation_cost, 8),
                    "embedding_usd": round(embedding_cost, 8),
                },
                citations_verified=not unknown_citations,
                unknown_citations=unknown_citations,
                refused=is_refusal(answer.answer),
            )
        except (ValidationError, ValueError) as exc:
            last_error = str(exc)
            continue

    # Clean failure — never leak a half-parsed response to the client.
    raise HTTPException(
        status_code=502,
        detail=f"Model response failed schema validation after retry: {last_error}",
    )
