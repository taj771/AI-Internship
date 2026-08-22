"""Score the RAG service against a golden set.

Three measures, reported separately because they fail separately and each one
implies a different repair:

  retrieval_hit  Did the expected document come back at all?
                 If not, nothing downstream can help -- the model never saw the
                 answer. The fix is chunking, top_k or the embedding model, and
                 no amount of prompt wording will substitute.

  correct        Does the answer state the known fact?
                 Checked by substring rather than by exact match, because the
                 model paraphrases and "within one hour" and "1 hour" are both
                 right.

  faithful       Is the answer actually supported by the retrieved passages?
                 The one that catches a right answer arrived at wrongly: the
                 model recalling a fact from training while the retrieved text
                 happens to agree. That system looks healthy and breaks silently
                 the day a document says something the internet does not.

The first two are exact and free. Faithfulness needs a second model call and is
a judgement rather than a measurement -- it is reported separately and labelled
as such, because presenting an LLM's opinion as a metric overclaims it.

Usage
-----
    .venv/bin/python evaluate.py
    .venv/bin/python evaluate.py --api https://week2-rag-api.onrender.com
    .venv/bin/python evaluate.py --top-k 3
    .venv/bin/python evaluate.py --no-faithfulness      # skip the judge calls
    .venv/bin/python evaluate.py --json results.json    # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_GOLDEN_SET = PROJECT_DIR / "golden_set.json"
DEFAULT_API = "http://127.0.0.1:8001"

# Free-tier Render sleeps and takes ~45s to wake; a short timeout would score a
# sleeping service as a failing one.
REQUEST_TIMEOUT = 180.0

# The judge model. Deliberately the same cheap model the service answers with:
# a judge is not required to be smarter than the thing it grades, only to be
# asked a narrower question ("is this supported?" rather than "what is true?").
JUDGE_MODEL = "gpt-4o-mini"

JUDGE_PROMPT = """You are checking whether an answer is supported by the passages it was given.

Answer only YES or NO.

YES if every factual claim in the answer appears in the passages.
NO if the answer states anything not present in the passages, even if that
statement is true in general.

A refusal ("I don't have enough information") counts as YES: declining to
answer asserts nothing.

Passages:
{context}

Answer:
{answer}

Supported (YES/NO)?"""


def load_golden_set(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["questions"]


def ask(api: str, question: str, top_k: int | None) -> dict:
    payload: dict = {"question": question}
    if top_k is not None:
        payload["top_k"] = top_k
    response = httpx.post(f"{api}/ask", json=payload, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def judge_faithfulness(api: str, answer_text: str, retrieved_ids: list[str], contexts: str) -> bool | None:
    """Ask the model whether the answer is supported by the passages.

    Routed through the service's own /ask with retrieval disabled, so this needs
    no separate OpenAI key and no second client -- the credential stays where it
    already is. Returns None when the judge cannot be reached, which is reported
    as unknown rather than silently counted as a pass.
    """
    prompt = JUDGE_PROMPT.format(context=contexts, answer=answer_text)
    try:
        response = httpx.post(
            f"{api}/ask",
            json={"question": prompt, "use_rag": False, "model": JUDGE_MODEL},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        verdict = response.json()["answer"]["answer"].strip().upper()
    except Exception:  # noqa: BLE001 - reported as unknown, not swallowed
        return None
    if verdict.startswith("YES"):
        return True
    if verdict.startswith("NO"):
        return False
    return None


def fetch_contexts(api: str, question: str, top_k: int | None) -> str:
    """The passages retrieval returned, as text, for the judge to check against."""
    params: dict = {"q": question}
    if top_k is not None:
        params["top_k"] = top_k
    try:
        response = httpx.get(f"{api}/debug/retrieve", params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return "\n\n".join(m["text"] for m in response.json()["matches"])
    except Exception:  # noqa: BLE001
        return ""


def evaluate(api: str, questions: list[dict], top_k: int | None, check_faithfulness: bool) -> list[dict]:
    results = []

    for item in questions:
        row: dict = {
            "id": item["id"],
            "question": item["question"],
            "expect_refusal": bool(item.get("expect_refusal")),
            "expect_document": item.get("expect_document"),
        }

        try:
            response = ask(api, item["question"], top_k)
        except Exception as exc:  # noqa: BLE001
            row.update({"error": f"{type(exc).__name__}: {exc}", "passed": False})
            results.append(row)
            continue

        answer_text = response["answer"]["answer"]
        retrieved_ids = response.get("retrieved_chunk_ids", [])
        retrieved_documents = {
            chunk["document_id"] for chunk in response.get("retrieved", []) if chunk.get("document_id")
        }

        row["answer"] = answer_text
        row["refused"] = response.get("refused", False)
        row["citations"] = response["answer"].get("citations", [])
        row["retrieved_documents"] = sorted(retrieved_documents)
        row["top_score"] = response["retrieved"][0]["score"] if response.get("retrieved") else None
        row["cost_usd"] = response.get("cost_usd", 0.0)

        # Did any retrieved chunk actually carry the fact?
        #
        # Document-level retrieval_hit is too coarse to act on. A question can
        # retrieve five chunks from the right document and none of them contain
        # the answer -- which happened here: the WB-9 speed qualifier sits in
        # SPEC-WB9#2, and a question about narrow aisles retrieved the header,
        # the intro, the dimensions section and the limitations section instead.
        # retrieval_hit said PASS. Nothing was wrong with the document; the chunk
        # was simply absent.
        #
        # This check is what separates the two failures. Evidence missing means
        # fix retrieval -- chunking, top_k, the embedding model. Evidence present
        # and the answer still wrong or refused means fix generation -- the
        # prompt. The repairs are opposite, so conflating them wastes the effort.
        contexts = fetch_contexts(api, item["question"], top_k)
        marker = item.get("expect_evidence")
        row["evidence_in_context"] = (
            (marker.lower() in contexts.lower()) if marker else None
        )

        if row["expect_refusal"]:
            # For a question the corpus does not answer there is no correct
            # document to retrieve, so retrieval_hit is not applicable. The only
            # thing being tested is whether the system declines.
            row["retrieval_hit"] = None
            row["correct"] = row["refused"]
            row["passed"] = row["refused"]
        else:
            row["retrieval_hit"] = item["expect_document"] in retrieved_documents
            needles = item.get("must_contain", [])
            lowered = answer_text.lower()
            row["correct"] = any(n.lower() in lowered for n in needles) if needles else None
            # Citing the right document is required as well as answering
            # correctly: an answer that is right but attributed to the wrong
            # source is not a grounded answer, it is a lucky one.
            row["cited_expected"] = item["expect_document"] in row["citations"]
            row["passed"] = bool(row["retrieval_hit"] and row["correct"] and not row["refused"])

        if check_faithfulness:
            row["faithful"] = judge_faithfulness(api, answer_text, retrieved_ids, contexts)
        else:
            row["faithful"] = None

        results.append(row)

    return results


def mark(value: bool | None) -> str:
    if value is None:
        return "  - "
    return " PASS" if value else " FAIL"


def report(results: list[dict], check_faithfulness: bool) -> bool:
    print()
    header = f"{'ID':4} {'retrieval':>9} {'evidence':>9} {'correct':>8} {'cited':>6}"
    if check_faithfulness:
        header += f" {'faithful':>9}"
    header += "  question"
    print(header)
    print("-" * (len(header) + 18))

    for row in results:
        if "error" in row:
            print(f"{row['id']:4}   ERROR   {row['error'][:60]}")
            continue
        line = (
            f"{row['id']:4} {mark(row.get('retrieval_hit')):>9} "
            f"{mark(row.get('evidence_in_context')):>9} "
            f"{mark(row.get('correct')):>8} {mark(row.get('cited_expected')):>6}"
        )
        if check_faithfulness:
            line += f" {mark(row.get('faithful')):>9}"
        line += f"  {row['question'][:46]}"
        print(line)

    print()
    answerable = [r for r in results if not r["expect_refusal"] and "error" not in r]
    refusals = [r for r in results if r["expect_refusal"] and "error" not in r]

    hits = sum(1 for r in answerable if r.get("retrieval_hit"))
    correct = sum(1 for r in answerable if r.get("correct"))
    cited = sum(1 for r in answerable if r.get("cited_expected"))
    passed = sum(1 for r in results if r.get("passed"))

    print(f"Retrieval hit    : {hits}/{len(answerable)}   expected document was retrieved")
    with_evidence = [r for r in answerable if r.get("evidence_in_context") is not None]
    evidenced = sum(1 for r in with_evidence if r["evidence_in_context"])
    print(f"Evidence present : {evidenced}/{len(with_evidence)}   a retrieved chunk carried the fact")
    print(f"Correct          : {correct}/{len(answerable)}   answer states the known fact")
    print(f"Cited correctly  : {cited}/{len(answerable)}   answer attributes it to that document")
    if refusals:
        got = sum(1 for r in refusals if r.get("refused"))
        print(f"Refused as needed: {got}/{len(refusals)}   declined where the corpus has no answer")

    if check_faithfulness:
        judged = [r for r in results if r.get("faithful") is not None]
        faithful = sum(1 for r in judged if r["faithful"])
        print(
            f"Faithful         : {faithful}/{len(judged)}   "
            "second opinion from an LLM judge, not a measurement"
        )

    # Split the failures by cause, because they need opposite repairs.
    retrieval_failures = [r for r in answerable if r.get("evidence_in_context") is False]
    generation_failures = [
        r for r in answerable
        if r.get("evidence_in_context") and not r.get("correct")
    ]
    if retrieval_failures or generation_failures:
        print()
        if retrieval_failures:
            ids = ", ".join(r["id"] for r in retrieval_failures)
            print(f"  RETRIEVAL problem ({ids}): the fact was never retrieved.")
            print("    Fix chunking, top_k or the embedding model. The prompt cannot help.")
        if generation_failures:
            ids = ", ".join(r["id"] for r in generation_failures)
            print(f"  GENERATION problem ({ids}): the fact was retrieved and the answer is still wrong.")
            print("    Fix the prompt. Retrieval is doing its job.")

    total_cost = sum(r.get("cost_usd", 0.0) for r in results)
    print(f"\nOverall          : {passed}/{len(results)} passed   (${total_cost:.6f})")
    return passed == len(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument(
        "--no-faithfulness",
        action="store_true",
        help="Skip the LLM judge (halves the cost, drops the softest measure)",
    )
    parser.add_argument("--json", type=Path, help="Also write full results here")
    args = parser.parse_args()

    api = args.api.rstrip("/")
    if not args.golden_set.exists():
        sys.exit(f"No golden set at {args.golden_set}")

    questions = load_golden_set(args.golden_set)

    try:
        health = httpx.get(f"{api}/health/pinecone", timeout=REQUEST_TIMEOUT).json()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Cannot reach {api} — is the service running?  ({exc})")
    if not health.get("ok"):
        sys.exit(f"Vector store is not healthy: {health.get('error')}")

    print(f"API      : {api}")
    print(f"Corpus   : {health['pinecone']['total_vectors']} chunks in "
          f"{health['pinecone']['index_name']}")
    print(f"Questions: {len(questions)}")
    if args.top_k:
        print(f"top_k    : {args.top_k}")

    results = evaluate(api, questions, args.top_k, not args.no_faithfulness)
    all_passed = report(results, not args.no_faithfulness)

    if args.json:
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nFull results written to {args.json}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
