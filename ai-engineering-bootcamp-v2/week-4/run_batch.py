"""
Run every claim in claims.py through the agent and record what happened.

    .venv/bin/python run_batch.py                       # -> traces.jsonl, label "baseline"
    .venv/bin/python run_batch.py --label after-fix     # -> traces.jsonl, label "after-fix"
    .venv/bin/python run_batch.py --only C02 C09        # rerun two claims while iterating

This is the "Trace" step of TRACE carried out at scale, and it is deliberately
the dullest file in the folder. It decides nothing. It runs each claim, hands
the result to trace_log, and moves on.


WHY THE CLAIMS RUN ONE AT A TIME

Twenty audits back to back take about four minutes, and running them
concurrently would take under one. They run sequentially anyway, for three
reasons that all outrank the three minutes.

The SEC asks for no more than ten requests a second from one client and blocks
clients that ignore it. A single audit already makes several requests, one of
which downloads about eight megabytes of company facts when a tag lookup fails,
so twenty concurrent audits would be both rude and likely to be throttled —
and a throttled lookup returns an error the model would then reason about,
quietly turning an infrastructure problem into what looks like an agent failure.

Rate limits aside, concurrency makes durations meaningless. duration_s is
recorded per run and twenty runs competing for the same network turn it into a
measure of contention rather than of the agent.

And a sequential batch fails legibly. If claim eleven crashes, the file holds
ten complete records and the error is attached to the eleventh.


WHY A CRASH DOES NOT STOP THE BATCH

Each claim is wrapped so that a failure is written down and the loop continues.
An exception escaping here would abandon the remaining claims, and the
temptation on rerunning would be to quietly drop the claim that broke — which
is exactly the claim most worth keeping. A crashed run is recorded with its
error, no answer, and whatever steps it managed, and it counts against the
totals like any other.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from agent import INSTRUCTION_VERSION, MAX_STEPS, MODEL, PROVIDER, audit
from claims import CLAIMS
from trace_log import DEFAULT_PATH, append_record, build_record


async def run_one(claim: dict, run_label: str) -> dict:
    """Audit one claim and return the record to be written."""
    started = time.time()
    try:
        answer, trace = await audit(claim["claim"])
        error = None
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        # type and message rather than the traceback: the traceback is about
        # this file, and what matters in the record is what went wrong for the
        # agent. Quota exhaustion and a network timeout look completely
        # different here and need to be told apart when reading later.
        answer, trace = None, []
        error = f"{type(exc).__name__}: {exc}"

    return build_record(
        trace_id=claim["id"],
        claim=claim["claim"],
        answer=answer,
        trace=trace,
        provider=PROVIDER,
        model=MODEL,
        duration_s=round(time.time() - started, 1),
        error=error,
        expected_verdict=claim["expected_verdict"],
        why_this_claim=claim["stresses"],
        run_label=run_label,
        instruction_version=INSTRUCTION_VERSION,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label",
        default="baseline",
        help="names this pass over the claim set; step 8 uses --label after-fix",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_PATH),
        help="file to append to (default traces.jsonl)",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        metavar="ID",
        help="run just these claim ids, e.g. --only C02 C09",
    )
    args = parser.parse_args()

    selected = CLAIMS
    if args.only:
        wanted = set(args.only)
        selected = [c for c in CLAIMS if c["id"] in wanted]
        missing = wanted - {c["id"] for c in selected}
        if missing:
            parser.error(f"no such claim id: {', '.join(sorted(missing))}")

    out = Path(args.out)

    print(f"{len(selected)} claims · {PROVIDER}/{MODEL} · step cap {MAX_STEPS} · instruction {INSTRUCTION_VERSION}")
    print(f"appending to {out.name} under label {args.label!r}\n")

    for position, claim in enumerate(selected, start=1):
        print(f"  [{position:>2}/{len(selected)}] {claim['id']}  {claim['claim'][:56]}…")

        record = await run_one(claim, args.label)
        append_record(record, out)

        if record["error"]:
            print(f"          ERROR  {record['error'][:100]}")
        else:
            verdict = record["parsed"]["VERDICT"] or "(no VERDICT line)"
            expected = record["expected_verdict"]
            # A marker, not a grade. It compares two strings and knows nothing
            # about whether the tag was right, whether the evidence was real, or
            # whether the reasoning contradicted the verdict. Step 5 is where a
            # human decides what a run was worth; this is here so a batch that
            # has gone badly wrong is obvious while it is still running.
            agrees = "  " if verdict == expected else " ≠"
            print(
                f"        {agrees} {verdict:<20} expected {expected:<20}"
                f" {record['n_tool_calls']} lookups  {record['duration_s']}s"
            )

    print(f"\nwrote {len(selected)} records to {out}")
    print("Verdict agreement above is a running indicator only — open coding in")
    print("step 5 is what decides whether a run passed.")


if __name__ == "__main__":
    asyncio.run(main())
