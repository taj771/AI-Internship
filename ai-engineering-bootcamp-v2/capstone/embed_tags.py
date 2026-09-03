"""Match sentences to XBRL concepts by meaning rather than by shared words.

    .venv/bin/python embed_tags.py --build     # embed the tag definitions once
    .venv/bin/python embed_tags.py --evaluate  # lexical vs embedding, head to head

Stage 2b. The lexical matcher pins the right tag about 4% of the time, and
adding the official definitions to it made that worse — more text, more
coincidental overlap. This asks whether similarity in meaning does better than
similarity in wording.


WHY THIS IS THE HONEST PLACE FOR EMBEDDINGS IN THIS PROJECT

There is no retrieval problem over the filing text. The auditor does not need to
find a relevant passage; it is handed one sentence and must decide which of
about nine hundred filed concepts that sentence refers to. Putting a vector
store over the prose would tick a box and help nothing.

The join is where the project actually fails, measured three ways, so it is
where a retrieval method has to earn its place.


NO VECTOR DATABASE, AND THAT IS THE ENGINEERING ANSWER

Nine hundred and thirty-three vectors is one matrix. Nearest neighbour over it
is a single dot product, exact, in under a millisecond. A vector database would
add a dependency, a service and an approximate index in order to be slower and
less accurate than numpy at this size.

The embedding step is the substance; the storage is not. Saying so is more
useful than importing Chroma to look the part.


WHAT IS COMPARED

Both methods pin value-blind — the figure is never consulted when choosing a
tag — and both are scored the same way, against whether the pinned tag's filed
value matches the figure in the sentence. Same claims, same tolerance, same
denominator.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

import prepare_evidence as pe

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")
CACHE = HERE / ".cache"
MODEL = "text-embedding-3-small"
TOLERANCE = 0.01


def client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def embed(texts: list[str], batch: int = 256) -> np.ndarray:
    out = []
    c = client()
    for i in range(0, len(texts), batch):
        chunk = [t[:7000] for t in texts[i:i + batch]]
        resp = c.embeddings.create(model=MODEL, input=chunk)
        out.extend(d.embedding for d in resp.data)
        print(f"    embedded {min(i+batch, len(texts))}/{len(texts)}")
    a = np.asarray(out, dtype=np.float32)
    return a / np.linalg.norm(a, axis=1, keepdims=True)   # cosine == dot product


def build(ticker: str) -> None:
    """One vector per tag, from its name and its official definition.

    The label alone is two or three words and carries little; the definition is
    where the concept is actually described. They are concatenated so a tag is
    represented by what it is called AND what it means.
    """
    gaap = pe.company_facts(ticker).get("facts", {}).get("us-gaap", {})
    tags, texts = [], []
    for tag, payload in sorted(gaap.items()):
        if not payload.get("units", {}).get("USD"):
            continue
        label = payload.get("label") or tag
        descr = payload.get("description") or ""
        tags.append(tag)
        texts.append(f"{label}. {descr}".strip())

    print(f"  {ticker}: embedding {len(tags)} tag definitions")
    vecs = embed(texts)
    CACHE.mkdir(exist_ok=True)
    np.save(CACHE / f"{ticker}-tagvecs.npy", vecs)
    (CACHE / f"{ticker}-tagnames.json").write_text(json.dumps(tags), encoding="utf-8")
    print(f"  saved {vecs.shape} -> .cache/{ticker}-tagvecs.npy")


def evaluate(ticker: str, limit: int) -> None:
    tags = json.loads((CACHE / f"{ticker}-tagnames.json").read_text())
    vecs = np.load(CACHE / f"{ticker}-tagvecs.npy")
    facts = pe.company_facts(ticker)
    gaap = facts["facts"]["us-gaap"]
    idf = pe.build_idf(facts)

    rows = [json.loads(l) for l in (HERE / "coverage.jsonl").open(encoding="utf-8")]
    pool = [r for r in rows
            if r["structural"] == "reachable" and pe.parse_claimed(r["figure"])][:limit]
    print(f"  scoring {len(pool)} claims\n")

    sent_vecs = embed([r["raw_sentence"][:2000] for r in pool])
    sims = sent_vecs @ vecs.T                      # cosine, every claim x every tag

    lex_n = lex_ok = emb_n = emb_ok = both = 0
    for i, r in enumerate(pool):
        claimed = pe.parse_claimed(r["figure"])
        fy = r["fiscal_year"]

        # --- lexical, unchanged
        cands, _ = pe.candidates(facts, idf, r["raw_sentence"], fy, limit=3)
        lex_tag = None
        if cands and (len(cands) == 1 or cands[0]["score"] >= 1.35 * cands[1]["score"]):
            lex_tag, lex_val = cands[0]["tag"], cands[0]["value"]
            lex_n += 1
            if abs((lex_val - claimed) / claimed) <= TOLERANCE:
                lex_ok += 1

        # --- embedding: nearest tag that actually has a figure for this year
        order = np.argsort(-sims[i])[:40]
        emb_tag = None
        for j in order:
            v = pe.annual_value(gaap[tags[j]].get("units", {}).get("USD", []), fy)
            if v:
                emb_tag, emb_val, emb_sim = tags[j], v["value"], float(sims[i][j])
                break
        if emb_tag:
            emb_n += 1
            if abs((emb_val - claimed) / claimed) <= TOLERANCE:
                emb_ok += 1
                if lex_tag == emb_tag:
                    both += 1

    print(f"  {'method':22s} {'pinned':>7} {'agree':>6} {'rate':>6}")
    print("  " + "-" * 44)
    print(f"  {'lexical (labels)':22s} {lex_n:>7} {lex_ok:>6} {lex_ok/max(lex_n,1):>5.0%}")
    print(f"  {'embedding (defs)':22s} {emb_n:>7} {emb_ok:>6} {emb_ok/max(emb_n,1):>5.0%}")
    print(f"\n  both methods agreed on the same correct tag: {both}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="JPM")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--limit", type=int, default=400)
    a = ap.parse_args()
    if a.build:
        build(a.ticker.upper())
    if a.evaluate:
        evaluate(a.ticker.upper(), a.limit)


if __name__ == "__main__":
    main()
