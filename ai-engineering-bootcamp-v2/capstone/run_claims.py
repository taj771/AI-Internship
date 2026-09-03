"""Run the agent over the fifty labelled claims. Phase 3 of the build plan.

    .venv/bin/python run_claims.py --limit 3      # smoke test, cheap
    .venv/bin/python run_claims.py                # all fifty, one rep
    .venv/bin/python run_claims.py --reps 3       # what a real comparison needs

Reads labels.jsonl, writes traces to traces.jsonl. The agent, its instruction
and its tools are week-5's, unchanged — only the claims are new.


MEMORY IS OFF BY DEFAULT, AND THAT IS A MEASUREMENT DECISION

audit() recalls learned XBRL tags before each run and writes new ones after. Run
that way over fifty claims and claim 40 is answered by an agent that claim 3
taught, so the runs are not independent and a per-claim accuracy figure has no
clean meaning.

So the default hands audit() a throwaway empty SQLite store and learn=False.
Every claim faces the same agent. `--memory on` uses the real store instead, and
the difference between the two runs is a result worth having later — it is Week
5's experiment repeated on real claims rather than on one synthetic pair.

Note this is not the same as memory being useless here. A per-filing report in
Phase 5 audits one company many times, which is exactly the case memory was
built for. It is turned off to measure, not because it does not help.


THE EXPECTED VERDICT IS RECORDED, NEVER SENT

Each record carries the label alongside the answer so scoring needs no second
file. The agent is handed `claim` and nothing else; the label is attached after
it has finished. Anything else would be the agent marking its own homework.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from agent import INSTRUCTION_VERSION, MODEL, PROVIDER, audit_checked
from memory import MemoryStore
from trace_log import append_record, build_record

HERE = Path(__file__).parent
LABELS = HERE / "labels.jsonl"
TRACES = HERE / "traces.jsonl"

# Concurrency is one, and the binding constraint is not the SEC.
#
# This OpenAI account allows 30,000 tokens a minute on gpt-4o. One audit spends
# roughly eight to ten thousand of them, because a failed lookup returns a list
# of every similar tag the company files and that list goes back into the
# context. Three at once overran the limit within seconds and lost two of five
# runs to rate-limit errors.
#
# A lost run is worse than a slow one. It is not a wrong answer that scoring can
# count; it is a hole, and holes bias whatever is computed from what remains,
# because the claims that fail are the ones with the longest tag lists — the
# hard ones.
CONCURRENCY = 1

# Retry only on rate limiting. A genuine failure — a bad tag, a network error —
# must be recorded as a failure, not smoothed away by a retry loop.
MAX_RETRIES = 5
BACKOFF_BASE = 8.0
PACE_SECONDS = 6.0


def load_claims(limit: int | None) -> list[dict]:
    rows = [json.loads(line) for line in LABELS.open(encoding="utf-8")]
    rows.sort(key=lambda r: r.get("label_seq", 0))
    return rows[:limit] if limit else rows


async def run_one(row: dict, rep: int, store: MemoryStore | None, learn: bool) -> dict:
    started = time.monotonic()
    answer, trace, error, evidence = None, [], None, {}

    for attempt in range(MAX_RETRIES):
        try:
            answer, trace, evidence = await audit_checked(
                row["claim"], store=store, learn=learn
            )
            error = None
            break
        except Exception as exc:  # noqa: BLE001 - one bad claim must not kill the batch
            error = f"{type(exc).__name__}: {exc}"
            if "RateLimit" not in type(exc).__name__ and "rate limit" not in str(exc).lower():
                break
            if attempt == MAX_RETRIES - 1:
                break
            wait = BACKOFF_BASE * (attempt + 1)
            print(f"      rate limited, waiting {wait:.0f}s "
                  f"(attempt {attempt + 2}/{MAX_RETRIES})")
            await asyncio.sleep(wait)

    record = build_record(
        trace_id=f"{row['id']}-r{rep}",
        claim=row["claim"],
        answer=answer,
        trace=trace,
        provider=PROVIDER,
        model=MODEL,
        duration_s=round(time.monotonic() - started, 2),
        error=error,
        expected_verdict=row.get("label_verdict"),
        why_this_claim=(
            f"{row['id']} · {row['stratum']} · {row.get('section') or 'firmwide'} · "
            f"label provenance {row.get('label_provenance')}"
            + (f" (rule {row['label_rule']})" if row.get("label_rule") else "")
        ),
        run_label=f"capstone-rep{rep}",
        instruction_version=INSTRUCTION_VERSION,
    )
    # Carried on the record so scoring can refuse to credit an answer that
    # consulted nothing. Kept out of build_record itself, which is week-4's and
    # is shared with runs that predate this check.
    record["evidence"] = evidence
    return record


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="first N claims only")
    parser.add_argument("--only", default=None, help="comma-separated claim ids")
    parser.add_argument("--reps", type=int, default=1, help="repetitions per claim")
    parser.add_argument("--memory", choices=("off", "on"), default="off")
    args = parser.parse_args()

    rows = load_claims(args.limit)
    if args.only:
        wanted = {i.strip() for i in args.only.split(",")}
        rows = [r for r in rows if r["id"] in wanted]
    learn = args.memory == "on"
    # An explicit path that is not the deployed store. Left to its default, this
    # would pick up DATABASE_URL and quietly run against the live Postgres the
    # Week 5 demo writes to.
    store = None if learn else MemoryStore(dsn="", sqlite_path=HERE / ".cache" / "empty-run.db")

    total = len(rows) * args.reps
    print(f"{total} runs · {PROVIDER}/{MODEL} · instruction '{INSTRUCTION_VERSION}' "
          f"· memory {args.memory}\n")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async def guarded(row: dict, rep: int) -> None:
        nonlocal done
        async with semaphore:
            record = await run_one(row, rep, store, learn)
            # Paced to stay under the per-minute token allowance rather than
            # discovering the ceiling by hitting it.
            await asyncio.sleep(PACE_SECONDS)
        append_record(record, TRACES)
        done += 1
        verdict = (record.get("parsed") or {}).get("VERDICT") or "—"
        expected = record.get("expected_verdict") or "—"
        admissible = record.get("evidence", {}).get("admissible", True)
        mark = ("✓" if verdict == expected else "✗") if admissible else "∅"
        print(f"  [{done:3d}/{total}] {record['trace_id']:16s} {mark} "
              f"said {verdict:20s} label {expected:20s} {record['duration_s']:>5.1f}s"
              + ("  NO EVIDENCE" if not admissible else "")
              + (f"  ERROR {record['error']}" if record.get("error") else ""))

    for rep in range(1, args.reps + 1):
        await asyncio.gather(*(guarded(row, rep) for row in rows))

    print(f"\nwrote {total} records to {TRACES.name}")


if __name__ == "__main__":
    asyncio.run(main())
