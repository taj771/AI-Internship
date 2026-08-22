"""Vector store for the Week 2 RAG work — configuration only, for now.

This file will grow into "talk to Pinecone", but it starts as just the settings
so that a misconfiguration announces itself here instead of surfacing later as a
confusing failure inside an API call.

Why a separate file from main.py
--------------------------------
main.py is the HTTP layer: it turns web requests into function calls and back.
Everything about *how* documents are stored and searched belongs here. Keeping
them apart means that when something breaks, "is this an HTTP problem or a
Pinecone problem?" is answered by which file the error came from.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec

# Load .env from this folder specifically, rather than letting python-dotenv
# search upward from wherever the process happened to start. main.py already
# does the same thing, for the same reason: uvicorn can be launched from any
# directory, and a config file found by accident is a config file that will be
# missed on a different machine.
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)


# --- Secrets -----------------------------------------------------------------
# Read, never hardcoded. Absent locally means "you forgot the .env"; absent on
# Render means "the dashboard variable was never filled in". Both are reported
# by health_check() rather than raised at import time, because a service that
# refuses to start cannot tell you *why* it refused.

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")


# --- Non-secret configuration ------------------------------------------------

# The Pinecone index holding our vectors. Lowercase letters, digits and hyphens
# only -- Pinecone rejects underscores and capitals.
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "week2-rag")

# The embedding model, defined in exactly one place and used for BOTH storing
# documents and searching them.
#
# This single-source rule is not tidiness, it is correctness. An embedding model
# maps text into its own coordinate system, and different models produce
# different systems -- not merely different sizes, but different meanings per
# axis. Store documents with one model and search with another and the results
# are meaningless. The cruel part is that if the two models happen to share a
# dimension count, nothing raises an error: retrieval just quietly returns
# nonsense that looks like a working system.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# How many numbers each embedding has. text-embedding-3-small produces 1536.
#
# Pinecone needs this when the index is CREATED and it is permanent -- an index
# built for 1536 rejects anything else, and changing it means deleting and
# rebuilding. So it is derived from the model name rather than configured
# separately: two settings that must agree are one setting waiting to disagree.
EMBEDDING_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

# Where Pinecone hosts the index. The free Starter plan supports exactly one
# region -- AWS us-east-1 -- so these are defaults rather than choices, but they
# are read from the environment so a paid plan can move without a code change.
PINECONE_CLOUD = os.getenv("PINECONE_CLOUD", "aws")
PINECONE_REGION = os.getenv("PINECONE_REGION", "us-east-1")

# Distance measure for comparing vectors. Cosine similarity compares the
# *direction* of two vectors while ignoring their length, which is what we want:
# a long passage and a short one about the same topic should count as similar,
# and length is exactly what would otherwise separate them.
PINECONE_METRIC = "cosine"

# --- Chunking ----------------------------------------------------------------

# How large each stored passage is, in characters, and how much of the previous
# passage is repeated at the start of the next.
#
# Overlap exists because chunk boundaries land in arbitrary places. "Net interest
# income rose 12% to $92.4 billion" split after "12%" leaves one passage claiming
# a rise of 12% from nothing in particular, and another quoting a figure with no
# subject. Neither answers a question. Repeating the tail of each passage at the
# head of the next means a sentence cut in half still survives whole somewhere.
#
# The cost of overlap is duplication: at 800/100 roughly an eighth of the corpus
# is stored twice, so embedding costs and storage rise by about that much.
#
# These are defaults. POST /ingest accepts per-request overrides, because the
# method this assignment teaches is "change one thing, measure again" -- and
# needing a server restart to try 500 instead of 800 kills that loop.
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "100"))


def embedding_dimensions() -> int:
    """Vector length for the configured model."""
    if EMBEDDING_MODEL not in EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Unknown embedding model {EMBEDDING_MODEL!r}. "
            f"Known: {', '.join(sorted(EMBEDDING_DIMENSIONS))}. "
            "Add its dimension to EMBEDDING_DIMENSIONS before using it."
        )
    return EMBEDDING_DIMENSIONS[EMBEDDING_MODEL]


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[str]:
    """Split a document into overlapping passages.

    "Recursive" in RecursiveCharacterTextSplitter refers to the separator list.
    It tries to split on paragraph breaks first; where a paragraph is still too
    long it falls back to single newlines, then sentence ends, then spaces, then
    -- as a last resort -- mid-word. So the ugly cuts happen only where there is
    no better option, and most passages end at a natural boundary.

    This is why chunk_size is an upper bound rather than a target: a paragraph
    of 300 characters becomes a 300-character chunk, because splitting it
    further to reach 800 would break the thing the splitter is trying to
    preserve.
    """
    size = chunk_size if chunk_size is not None else CHUNK_SIZE
    overlap = chunk_overlap if chunk_overlap is not None else CHUNK_OVERLAP

    # Overlap at or above chunk size makes each chunk re-contain the previous
    # one, which either loops forever or produces near-duplicate chunks that
    # crowd out real results at query time. Catch it here with a readable
    # message rather than letting the splitter behave strangely.
    if overlap >= size:
        raise ValueError(
            f"chunk_overlap ({overlap}) must be smaller than chunk_size ({size})."
        )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
        length_function=len,
    )
    return splitter.split_text(text)


def config_summary() -> dict:
    """Report the configuration with secrets reduced to present/absent.

    Deliberately returns booleans and lengths for the keys rather than the keys
    themselves. This ends up in an HTTP response, and a debug endpoint that
    prints your credentials is a debug endpoint that leaks them to anyone who
    finds the URL.
    """
    return {
        "openai_key_loaded": bool(OPENAI_API_KEY),
        "pinecone_key_loaded": bool(PINECONE_API_KEY),
        "index_name": PINECONE_INDEX_NAME,
        "embedding_model": EMBEDDING_MODEL,
        "dimensions": embedding_dimensions(),
        "cloud": PINECONE_CLOUD,
        "region": PINECONE_REGION,
        "metric": PINECONE_METRIC,
    }


# --- Embeddings --------------------------------------------------------------

# How many passages to send to OpenAI in one request.
#
# One request carrying 100 passages costs exactly the same as 100 requests
# carrying one each -- billing is per token, not per call -- but it takes a
# fraction of the time, because the network round trip happens once instead of a
# hundred times. The cap exists because a single request has a size limit; 100
# passages of ~800 characters sits comfortably inside it.
EMBED_BATCH_SIZE = 100

_openai: OpenAI | None = None


def openai_client() -> OpenAI:
    """The OpenAI client, created once and reused."""
    global _openai
    if _openai is None:
        if not OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Locally: add it to .env. "
                "On Render: fill it in under Environment."
            )
        _openai = OpenAI(api_key=OPENAI_API_KEY)
    return _openai


def embed_texts(texts: list[str]) -> tuple[list[list[float]], int]:
    """Turn passages into vectors. Returns the vectors and the tokens billed.

    Uses EMBEDDING_MODEL -- the same constant the index dimension is derived
    from, and the same one the query path will use. That is the single most
    important property of this function: if documents and questions are embedded
    by different models, every search result is meaningless, and nothing
    anywhere raises an error to tell you.

    Token count is returned rather than discarded because embedding is billed
    and currently invisible. The Week 1 service reports cost_usd on every answer;
    ingestion spending should not be the one thing that happens silently.
    """
    if not texts:
        return [], 0

    client = openai_client()
    vectors: list[list[float]] = []
    total_tokens = 0

    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)

        # The API is documented to return embeddings in request order, but it
        # also returns an explicit index on each item. Sorting by it costs
        # nothing and removes any dependence on that ordering guarantee -- a
        # silently reordered batch would attach every vector to the wrong
        # passage, which is exactly the class of bug that produces plausible
        # wrong answers rather than an error.
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors.extend(item.embedding for item in ordered)

        if response.usage:
            total_tokens += response.usage.total_tokens

    return vectors, total_tokens


# Price per 1,000 tokens, so ingestion can report what it spent. Embedding models
# bill for input only -- there is no output to charge for -- which is why this is
# a single number per model rather than the input/output pair main.py keeps for
# chat models.
EMBEDDING_PRICE_PER_1K: dict[str, float] = {
    "text-embedding-3-small": 0.00002,
    "text-embedding-3-large": 0.00013,
    "text-embedding-ada-002": 0.0001,
}


def embedding_cost_usd(tokens: int) -> float:
    """Dollars for a given number of embedding tokens."""
    price = EMBEDDING_PRICE_PER_1K.get(EMBEDDING_MODEL, 0.0)
    return tokens / 1000 * price


# --- Pinecone ----------------------------------------------------------------

# One client, created on first use rather than at import time.
#
# Import-time construction would mean that a missing key crashes the whole
# service on startup, before any route exists to explain what went wrong. On
# Render that shows up as a deploy that boots and immediately dies, with the
# cause buried in logs. Deferring it means the service starts, /health/pinecone
# answers, and the answer says exactly which key is missing.
_client: Pinecone | None = None


def pinecone_client() -> Pinecone:
    """The Pinecone client, created once and reused."""
    global _client
    if _client is None:
        if not PINECONE_API_KEY:
            raise RuntimeError(
                "PINECONE_API_KEY is not set. Locally: add it to .env. "
                "On Render: fill it in under Environment (it is declared "
                "sync: false, so a blank value still deploys successfully)."
            )
        _client = Pinecone(api_key=PINECONE_API_KEY)
    return _client


def ensure_index() -> str:
    """Create the index if it does not exist, and return its name.

    Safe to call repeatedly -- it checks first, so this is not "create" so much
    as "make sure it is there". That matters because it runs on a web server
    that may start many times.

    Two details worth knowing:

    `dimension` is fixed at creation and permanent. An index built for 1536
    rejects vectors of any other length, and there is no way to change it later
    -- switching embedding model means deleting the index and re-ingesting
    everything. It comes from embedding_dimensions() so it cannot drift from the
    model actually being used.

    Creation is not instant. Pinecone provisions the index and returns before it
    is ready to accept data, so we wait for the ready flag. Skipping the wait
    produces an intermittent failure on the very first ingest -- the kind that
    works on your laptop and fails in CI.
    """
    pc = pinecone_client()

    if not pc.has_index(PINECONE_INDEX_NAME):
        pc.create_index(
            name=PINECONE_INDEX_NAME,
            dimension=embedding_dimensions(),
            metric=PINECONE_METRIC,
            spec=ServerlessSpec(cloud=PINECONE_CLOUD, region=PINECONE_REGION),
        )
        deadline = time.time() + INDEX_READY_TIMEOUT_SEC
        while time.time() < deadline:
            if pc.describe_index(PINECONE_INDEX_NAME).status.get("ready"):
                break
            time.sleep(1)
        else:
            raise TimeoutError(
                f"Index {PINECONE_INDEX_NAME!r} was created but did not become "
                f"ready within {INDEX_READY_TIMEOUT_SEC}s."
            )

    return PINECONE_INDEX_NAME


def get_index():
    """Handle to the index, ready for reading and writing vectors."""
    return pinecone_client().Index(ensure_index())


# How long to wait for a newly created index to come online. Provisioning is
# usually a few seconds; the ceiling exists so a Pinecone outage fails with a
# clear message instead of hanging a web request forever.
INDEX_READY_TIMEOUT_SEC = 120


# How many vectors to send to Pinecone per upsert request. Pinecone caps the
# size of a single request; 100 vectors of 1536 floats plus metadata sits well
# inside it.
UPSERT_BATCH_SIZE = 100


def chunk_vector_id(document_id: str, chunk_index: int) -> str:
    """Stable id for one chunk of one document.

    Derived from the document id rather than random, so that re-ingesting a
    document overwrites its own chunks instead of adding a second copy. Two
    copies of a passage would compete for the same top-k slots at query time and
    crowd out genuinely different material -- the duplicate-chunk failure the
    live-session notebook demonstrates, arriving by a different route.
    """
    return f"{document_id}#{chunk_index}"


def delete_document(document_id: str) -> int:
    """Remove every stored chunk of a document. Returns how many were deleted.

    Needed because overwriting by id only covers chunks that still exist. Ingest
    a 60-chunk document, edit it down to 40, re-ingest: chunks 0-39 are
    overwritten and chunks 40-59 remain, still matching queries, still quoting
    text that is no longer in the document. That is a genuinely nasty bug --
    "I fixed the document and it still gives me the old answer" -- so ingestion
    clears the document first rather than trusting overwrite.

    Ids are listed by prefix rather than deleted by metadata filter, because
    filtered delete is not supported on Pinecone's serverless indexes.
    """
    index = get_index()
    prefix = f"{document_id}#"

    ids: list[str] = []
    for page in index.list(prefix=prefix):
        # What list() yields has changed across SDK versions and is not
        # something to guess at: this SDK returns ListResponse objects whose
        # .vectors hold ListItem records, while older ones yielded plain lists
        # of id strings. Handle each shape rather than pinning a version, since
        # passing the wrong one to delete() fails deep inside JSON encoding with
        # an error that names neither this function nor the real problem.
        if isinstance(page, str):
            ids.append(page)
        elif hasattr(page, "vectors"):
            ids.extend(item.id for item in page.vectors)
        else:
            ids.extend(item if isinstance(item, str) else item.id for item in page)

    for start in range(0, len(ids), UPSERT_BATCH_SIZE):
        index.delete(ids=ids[start : start + UPSERT_BATCH_SIZE])

    return len(ids)


def _validated_metadata_value(key: str, value):
    """Check one caller-supplied metadata value against what Pinecone accepts.

    Pinecone metadata values may only be strings, numbers, booleans, or lists of
    strings -- no nested objects. Passing a dict fails inside the upsert call
    with an error that names neither the field nor the rule, so the offending
    key is named here instead. Raises ValueError, which POST /ingest turns into
    a 400.
    """
    if isinstance(value, bool) or isinstance(value, (int, float, str)):
        return value
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    raise ValueError(
        f"metadata field {key!r} has type {type(value).__name__}, which the "
        "vector store cannot store. Allowed: string, number, boolean, or a "
        "list of strings."
    )


def upsert_chunks(
    document_id: str,
    chunks: list[str],
    vectors: list[list[float]],
    source: str | None = None,
    extra_metadata: dict | None = None,
) -> int:
    """Store chunk vectors with their metadata. Returns how many were stored.

    The chunk text itself is stored in metadata, which is not merely convenient.
    Pinecone stores vectors -- 1536 numbers -- and a vector cannot be turned back
    into the text it came from. Without the text alongside it, a search would
    return a list of ids and similarity scores and no way to recover the passages
    to answer from. Every RAG system keeps the text somewhere; here it rides
    along in the metadata.
    """
    if len(chunks) != len(vectors):
        raise ValueError(
            f"Got {len(chunks)} chunks but {len(vectors)} vectors -- these must "
            "correspond one-to-one."
        )

    index = get_index()
    records = []
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        metadata = {
            "document_id": document_id,
            "chunk_index": i,
            "source": source or document_id,
            "text": chunk,
        }
        if extra_metadata:
            # Caller-supplied fields cannot overwrite the four above; a caller
            # passing {"text": ...} would otherwise silently replace the passage
            # the answer is built from.
            for key, value in extra_metadata.items():
                if key not in metadata:
                    metadata[key] = _validated_metadata_value(key, value)

        records.append(
            {"id": chunk_vector_id(document_id, i), "values": vector, "metadata": metadata}
        )

    for start in range(0, len(records), UPSERT_BATCH_SIZE):
        index.upsert(vectors=records[start : start + UPSERT_BATCH_SIZE])

    return len(records)


def ingest_document(
    text: str,
    document_id: str,
    source: str | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    extra_metadata: dict | None = None,
) -> dict:
    """Chunk, embed and store one document. Returns a summary of what happened.

    The three stages are separate functions above so each can be tested on its
    own; this is the orchestration, and the only thing POST /ingest needs to
    call.
    """
    if not text or not text.strip():
        raise ValueError("text is empty — nothing to ingest.")
    if not document_id or not document_id.strip():
        raise ValueError("document_id is required.")

    chunks = chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if not chunks:
        raise ValueError("text produced no chunks — it may be whitespace only.")

    # Clear first, so a shortened document does not leave orphaned chunks behind.
    deleted = delete_document(document_id)

    vectors, tokens = embed_texts(chunks)
    stored = upsert_chunks(
        document_id=document_id,
        chunks=chunks,
        vectors=vectors,
        source=source,
        extra_metadata=extra_metadata,
    )

    return {
        "document_id": document_id,
        "chunks_indexed": stored,
        "status": "ok",
        # Beyond the required three. Chunk settings are echoed because they are
        # per-request overridable, so a response that did not state them would
        # leave you guessing which setting produced which result while comparing.
        "source": source or document_id,
        "chunk_size": chunk_size if chunk_size is not None else CHUNK_SIZE,
        "chunk_overlap": chunk_overlap if chunk_overlap is not None else CHUNK_OVERLAP,
        "replaced_existing_chunks": deleted,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_tokens": tokens,
        "cost_usd": round(embedding_cost_usd(tokens), 8),
    }


# How many chunks to retrieve by default.
#
# Too few and an answer needing two passages gets one. Too many and irrelevant
# passages ride along into the prompt, costing tokens and giving the model more
# to be distracted by. There is no correct value in the abstract -- it is
# measured against a golden set, which is why it is a parameter.
DEFAULT_TOP_K = 5


def search(query: str, top_k: int = DEFAULT_TOP_K) -> dict:
    """Find the chunks most similar in meaning to a question.

    This is retrieval on its own, with no LLM anywhere in it. Being able to run
    it in isolation is what makes a bad answer diagnosable: if the passages
    coming back here are wrong, no amount of prompt wording will fix the answer,
    and if they are right, the problem is in generation. Debugging the two
    together means debugging neither.

    The question is embedded with EMBEDDING_MODEL -- the same constant used to
    embed the documents. Both sets of coordinates must come from the same model
    or the comparison is meaningless, and nothing would report an error.
    """
    if not query or not query.strip():
        raise ValueError("query is empty — nothing to search for.")
    if top_k < 1:
        raise ValueError(f"top_k must be at least 1, got {top_k}.")

    query_vectors, tokens = embed_texts([query])

    response = get_index().query(
        vector=query_vectors[0],
        top_k=top_k,
        include_metadata=True,  # Without this, only ids and scores come back --
        include_values=False,   # and ids cannot be read or answered from.
    )

    matches = []
    for match in response.get("matches", []):
        metadata = match.get("metadata") or {}
        matches.append(
            {
                "id": match.get("id"),
                # Cosine similarity: 1.0 identical direction, 0.0 unrelated.
                # Read these relative to each other, not as a percentage.
                "score": round(float(match.get("score", 0.0)), 4),
                "document_id": metadata.get("document_id"),
                "chunk_index": metadata.get("chunk_index"),
                "source": metadata.get("source"),
                "text": metadata.get("text", ""),
            }
        )

    return {
        "query": query,
        "top_k": top_k,
        "matches_returned": len(matches),
        "embedding_model": EMBEDDING_MODEL,
        "embedding_tokens": tokens,
        "cost_usd": round(embedding_cost_usd(tokens), 8),
        "matches": matches,
    }


def health_check(create_if_missing: bool = False) -> dict:
    """Report whether Pinecone is reachable and correctly configured.

    Never raises. A health check that throws is useless precisely when it is
    needed, so every failure is caught and returned as data -- the caller gets
    an answer describing the problem rather than a stack trace.

    `create_if_missing` defaults to False so that merely *checking* health does
    not create infrastructure as a side effect. The CLI below passes True,
    because running this file directly is an explicit setup action.
    """
    result: dict = {
        "ok": False,
        "config": config_summary(),
        "pinecone": {},
        "error": None,
    }

    try:
        pc = pinecone_client()

        # Listing indexes is the cheapest call that proves two separate things:
        # the network is reachable, and the API key is accepted.
        existing = [i["name"] for i in pc.list_indexes()]
        result["pinecone"]["reachable"] = True
        result["pinecone"]["all_indexes"] = existing

        exists = PINECONE_INDEX_NAME in existing
        result["pinecone"]["index_exists"] = exists

        if not exists:
            if not create_if_missing:
                result["error"] = (
                    f"Index {PINECONE_INDEX_NAME!r} does not exist yet. "
                    "Run `python vector_store.py` to create it."
                )
                return result
            ensure_index()
            result["pinecone"]["index_created"] = True

        described = pc.describe_index(PINECONE_INDEX_NAME)
        actual_dimension = described.dimension
        expected = embedding_dimensions()

        result["pinecone"].update(
            {
                "index_name": PINECONE_INDEX_NAME,
                "dimension": actual_dimension,
                "metric": described.metric,
                "host": described.host,
                "status_ready": described.status.get("ready"),
            }
        )

        # The check that catches the worst failure mode in this whole project.
        #
        # If the index was built for a different embedding model, storing and
        # searching still "work" -- Pinecone accepts vectors of the right length
        # regardless of which model produced them, and returns nearest
        # neighbours computed in a coordinate system that means nothing. There
        # is no error anywhere; results are simply wrong. Comparing the index's
        # dimension against the configured model's is the cheapest way to catch
        # a mismatched index before any data goes in.
        if actual_dimension != expected:
            result["error"] = (
                f"Dimension mismatch: index {PINECONE_INDEX_NAME!r} was built "
                f"for {actual_dimension}-dimensional vectors, but "
                f"{EMBEDDING_MODEL!r} produces {expected}. Index dimension is "
                "fixed at creation -- delete the index and recreate it, or "
                "point EMBEDDING_MODEL at the model it was built for."
            )
            return result

        stats = get_index().describe_index_stats()
        result["pinecone"]["total_vectors"] = stats.get("total_vector_count", 0)
        result["pinecone"]["namespaces"] = list((stats.get("namespaces") or {}).keys())

        result["ok"] = True
        return result

    except Exception as exc:
        # Class name included because Pinecone's messages are often terse;
        # "Unauthorized" alone does not say whether the key is wrong or absent.
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


if __name__ == "__main__":
    # Run this file directly to check the configuration and set up the index:
    #     .venv/bin/python vector_store.py
    import json

    print(f"Reading settings from: {_ENV_PATH}")
    print(f"        .env exists:   {_ENV_PATH.exists()}\n")

    print("Checking Pinecone (creating the index if it does not exist)...\n")
    report = health_check(create_if_missing=True)
    print(json.dumps(report, indent=2, default=str))
    print("\nOK" if report["ok"] else "\nPROBLEM — see 'error' above")
    raise SystemExit(0 if report["ok"] else 1)
