"""Ingest a folder of documents through POST /ingest, one file at a time.

Deliberately goes over HTTP rather than importing vector_store and calling it
directly. Calling the library would test the library; calling the endpoint tests
the thing that is actually deployed -- request validation, error mapping, the
JSON contract, and the running server. A corpus that loads through the library
but fails through the API is a corpus that fails in production.

Usage
-----
    .venv/bin/python ingest_corpus.py corpus/
    .venv/bin/python ingest_corpus.py corpus/ --api http://127.0.0.1:8001
    .venv/bin/python ingest_corpus.py corpus/ --chunk-size 500 --chunk-overlap 150
    .venv/bin/python ingest_corpus.py corpus/ --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

DEFAULT_API = "http://127.0.0.1:8001"

# Which files count as documents. Deliberately narrow: a folder scanned for
# "everything" picks up .DS_Store, editor swap files and stray CSVs, all of
# which embed successfully and quietly pollute retrieval with nonsense.
TEXT_SUFFIXES = {".txt", ".md", ".markdown"}

# Ingest is one HTTP call that chunks, embeds and upserts a whole document, so a
# large file can legitimately take a while. Longer than the default because the
# failure mode of too short a timeout is a half-ingested corpus.
REQUEST_TIMEOUT_SEC = 300.0


def document_id_for(path: Path, root: Path) -> str:
    """A stable, readable id derived from the file's path.

    Stable matters more than pretty. Ingestion overwrites by document_id, so an
    id derived from the path means re-running this script updates the corpus in
    place; an id containing a timestamp or a random number would add a fresh
    copy of everything on every run, and retrieval would slowly fill with
    duplicates of the same passages.

    The relative path is used rather than just the filename so that
    reports/2025.md and notes/2025.md do not collide and silently overwrite one
    another.
    """
    relative = path.relative_to(root).with_suffix("")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(relative)).strip("-").lower()
    return slug or path.stem.lower()


def find_documents(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES and not p.name.startswith(".")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Folder of .txt/.md documents")
    parser.add_argument("--api", default=DEFAULT_API, help=f"API base URL (default {DEFAULT_API})")
    parser.add_argument("--chunk-size", type=int, default=None)
    parser.add_argument("--chunk-overlap", type=int, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be ingested, without calling the API",
    )
    args = parser.parse_args()

    root = args.folder.resolve()
    if not root.is_dir():
        sys.exit(f"Not a folder: {root}")

    documents = find_documents(root)
    if not documents:
        sys.exit(
            f"No {'/'.join(sorted(TEXT_SUFFIXES))} files found under {root}."
        )

    api = args.api.rstrip("/")
    print(f"Corpus : {root}")
    print(f"Files  : {len(documents)}")
    print(f"API    : {api}\n")

    if args.dry_run:
        for path in documents:
            print(f"  {document_id_for(path, root):40s} {path.relative_to(root)}")
        print("\n--dry-run: nothing was sent.")
        return 0

    # Fail early and clearly if the server is not up. Otherwise every file in
    # the loop fails identically and the real message is buried under N copies.
    try:
        health = httpx.get(f"{api}/health/pinecone", timeout=30.0).json()
        if not health.get("ok"):
            sys.exit(f"Vector store is not healthy: {health.get('error')}")
    except httpx.HTTPError as exc:
        sys.exit(f"Cannot reach {api} — is the server running?  ({exc})")

    total_chunks = 0
    total_tokens = 0
    total_cost = 0.0
    failures: list[tuple[str, str]] = []

    for path in documents:
        document_id = document_id_for(path, root)
        text = path.read_text(encoding="utf-8", errors="replace")

        payload = {
            "text": text,
            "document_id": document_id,
            "source": path.name,
        }
        if args.chunk_size is not None:
            payload["chunk_size"] = args.chunk_size
        if args.chunk_overlap is not None:
            payload["chunk_overlap"] = args.chunk_overlap

        print(f"  {document_id:40s} {len(text):>8,} chars ... ", end="", flush=True)
        try:
            response = httpx.post(
                f"{api}/ingest", json=payload, timeout=REQUEST_TIMEOUT_SEC
            )
        except httpx.HTTPError as exc:
            print("FAILED (network)")
            failures.append((document_id, str(exc)))
            continue

        if response.status_code != 200:
            detail = response.json().get("detail", response.text)
            print(f"FAILED ({response.status_code})")
            failures.append((document_id, str(detail)[:200]))
            continue

        result = response.json()
        total_chunks += result["chunks_indexed"]
        total_tokens += result["embedding_tokens"]
        total_cost += result["cost_usd"]
        replaced = result["replaced_existing_chunks"]
        note = f"  (replaced {replaced})" if replaced else ""
        print(f"{result['chunks_indexed']:>3} chunks{note}")

    print()
    print(f"Documents ingested : {len(documents) - len(failures)} of {len(documents)}")
    print(f"Chunks created     : {total_chunks:,}")
    print(f"Embedding tokens   : {total_tokens:,}")
    print(f"Cost               : ${total_cost:.6f}")

    if failures:
        print(f"\nFailures ({len(failures)}):")
        for document_id, reason in failures:
            print(f"  {document_id}: {reason}")

    # Pinecone indexes asynchronously, so a count read immediately after the
    # last upsert can be short of what was just written -- not an error, just a
    # view that has not caught up.
    print("\nWaiting for the index to settle...")
    time.sleep(5)
    health = httpx.get(f"{api}/health/pinecone", timeout=30.0).json()
    stored = health["pinecone"]["total_vectors"]
    print(f"Total vectors in store: {stored:,}")

    if stored != total_chunks:
        print(
            f"  note: {total_chunks:,} chunks were written this run but the store "
            f"holds {stored:,}. Expected if documents were already present from "
            "an earlier run, or if indexing has not finished settling."
        )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
