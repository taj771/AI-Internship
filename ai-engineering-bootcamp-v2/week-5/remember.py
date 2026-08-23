"""
Command line for the durable memory store.

Run:  .venv/bin/python remember.py --help

Two jobs. The first is the one the assignment names — "show a CLI or endpoint
that writes in one session and reads in another after restart" — and every
command here does that implicitly, because each invocation is a new process that
shares nothing with the last but the store.

The second is the reason this file is longer than it needs to be. Week 4's whole
finding was that a single run of a non-deterministic agent is not evidence: the
baseline moved 15, 16, 17 out of 20 with nothing changed between runs, and a
before-and-after screenshot would have reported an improvement that was noise.
`demo` therefore does not run the claim once with memory and once without. It
runs both sides N times and prints the spread, so the write-up can say how often
rather than whether.

    .venv/bin/python remember.py demo --reps 20

WHAT THE BASELINE COSTS, and why memory is worth having at all

Every baseline run guesses `Revenues` for Goldman, is told it is not filed, and
triggers _suggest_tags — which downloads roughly 8 MB listing every tag the
company does file. The model call is the cheap part. Twenty baseline runs pull
about 160 MB from data.sec.gov to rediscover one string that has not changed
since the company started filing. That is the number memory removes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Loading .env before importing agent matters: agent.py reads LLM_PROVIDER and
# builds the model wrapper at import time.
from dotenv import load_dotenv

load_dotenv()

from memory import (  # noqa: E402  - after load_dotenv by necessity
    GLOBAL_SCOPE,
    KINDS,
    MemoryStore,
)
import memory_gate  # noqa: E402

# Both figures verified against data.sec.gov on 2026-08-23 before being written
# here, the same way week 4's ground truth was established: by calling the tool
# directly, with no model involved.
#
#   FY2022  RevenuesNetOfInterestExpense  $47.37B
#   FY2023  RevenuesNetOfInterestExpense  $46.25B
#
# Both claims are therefore SUPPORTED, and that is not cosmetic. The first draft
# of this demo used $53.5 billion for 2023 — which is real, but is Goldman's
# *2024* figure — so the claim was false and the verdict was CONTRADICTED. The
# week 4 instruction requires testing an alternative tag before returning
# CONTRADICTED, so every run in the "after" group made a second lookup *after*
# already having its answer, and the comparison showed two lookups against two.
#
# Memory had worked perfectly and the measurement said otherwise. The two groups
# must differ in the fiscal year and nothing else; a different verdict changes
# how many lookups the instruction demands, which has nothing to do with memory.
GOLDMAN_2022 = "Goldman Sachs had revenue of $47.37 billion in 2022."
GOLDMAN_2023 = "Goldman Sachs reported revenue of $46.25 billion in 2023."


def _store() -> MemoryStore:
    return MemoryStore()


def _fmt(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 1] + "…"


# --- commands ----------------------------------------------------------------


def cmd_list(args) -> int:
    """Everything in memory, including what was refused.

    Quarantined rows are shown deliberately. They are the evidence that the gate
    ran — a refusal nobody can see is indistinguishable from a write that never
    happened.
    """
    store = _store()
    facts = store.facts_for(args.user)

    print(f"store: {store.backend}")
    print(f"user:  {args.user}   (plus global scope '{GLOBAL_SCOPE}')\n")

    if not facts:
        print("  (empty)")
        return 0

    header = f"  {'KIND':<12} {'KEY':<26} {'VALUE':<30} {'SOURCE':<24} {'TRUST':<12} HITS"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for fact in facts:
        print(
            f"  {fact.kind:<12} {_fmt(fact.key, 26):<26} {_fmt(fact.value, 30):<30} "
            f"{fact.source:<24} {fact.trust:<12} {fact.hits}"
        )
        why = fact.detail.get("refused_because")
        if why:
            print(f"       └─ refused: {why}")
    print()
    usable = sum(1 for f in facts if f.is_usable)
    print(f"  {usable} usable, {len(facts) - usable} quarantined (never shown to the agent)")
    return 0


def cmd_audit(args) -> int:
    """One audit, with the trace printed. A new process every time it is run."""
    import agent

    print(f"store:  {agent.get_store().backend}")
    print(f"engine: {agent.PROVIDER} · {agent.MODEL}")
    print(f"claim:  {args.claim}\n")

    started = time.time()
    answer, trace = asyncio.run(
        agent.audit(args.claim, user_id=args.user, learn=not args.no_learn)
    )
    elapsed = time.time() - started

    agent.print_trace(trace)
    print(f"\n--- ANSWER ({elapsed:.1f}s) ---")
    print(answer)
    return 0


def cmd_alias(args) -> int:
    """Teach it what a company name means — the Coca-Cola fix.

    resolve_company("Coca-Cola") returns COCA-COLA EUROPACIFIC PARTNERS plc, a
    UK bottler, with no error of any kind. Week 4 recorded a whole run that
    audited the wrong company and said nothing about it. Nothing in the tool can
    detect that, because nothing went wrong: a name matched a company with that
    name. Only a person knows which of two real companies was meant.
    """
    store = _store()
    fact, explanation = memory_gate.remember_alias(store, args.user, args.alias, args.target)
    print(explanation)
    print(f"\n  trust:  {fact.trust}")
    print(f"  source: {fact.source}")
    if fact.detail.get("verified_against"):
        print(f"  proof:  {fact.detail['verified_against']}")
    return 0


def cmd_prefer(args) -> int:
    store = _store()
    _, explanation = memory_gate.remember_preference(store, args.user, args.name, args.value)
    print(explanation)
    return 0


def cmd_poison(args) -> int:
    """Try to plant a false fact, and watch the gate refuse it.

    This is the Path B demonstration, and the reason it is convincing is that
    the refusal is not a keyword filter or a guess about plausibility.
    "TotallyRealTag" is refused for exactly the same reason a real tag would be
    accepted: the assertion is run through the same tool the agent uses, against
    the live SEC endpoint, and the SEC does not have it.

    The consequence is the sentence worth putting in the write-up: a human can
    teach this agent things, but cannot teach it things that are false.
    """
    store = _store()

    print("Attempting to plant a fabricated tag, as an untrusted user would:\n")
    print(f'  "{args.company} files revenue under {args.tag}"\n')

    fact, explanation = memory_gate.remember_tag_assertion(
        store, args.user, args.company, args.tag, args.year
    )

    print(f"  → {explanation}\n")
    print(f"  trust:  {fact.trust}")
    print(f"  source: {fact.source}")

    recalled = store.recall(args.user, f"{args.company} revenue in {args.year}")
    planted = [f for f in recalled if f.value == args.tag]
    print(f"\n  recalled into the next prompt? {'YES — GATE FAILED' if planted else 'no'}")
    print(f"  visible in `remember.py list`?  yes (as {fact.trust})")
    return 0 if not planted else 1


def cmd_forget(args) -> int:
    store = _store()
    scope = GLOBAL_SCOPE if args.kind == "xbrl_tag" else args.user
    store.forget(scope, args.kind, args.key.lower())
    print(f"forgot {args.kind} {args.key!r} from scope {scope!r}")
    return 0


def cmd_clear(args) -> int:
    store = _store()
    store.clear(None if args.all else args.user)
    print("cleared " + ("everything" if args.all else f"scope {args.user!r}"))
    return 0


def cmd_demo(args) -> int:
    """The before/after, run enough times to report a spread rather than a point.

    Structure, and each step is here for a reason:

      1. Wipe the store, so a previous demo cannot supply the answer.
      2. Run the claim N times with learn=False. This is the baseline, and
         learn=False is what keeps it one — otherwise run 1 would teach run 2 and
         the "before" group would improve halfway through, which is precisely the
         contamination that makes most before/after comparisons meaningless.
      3. One learning run, to populate memory the way a real session would.
      4. Run a DIFFERENT claim, about a DIFFERENT fiscal year, N times.

    Step 4 is the part that makes this evidence rather than a cache demo. If the
    second group used the same claim, a sceptic could say the agent was replaying
    a stored answer. A different year forces a live lookup for a figure that was
    never stored — memory can only have supplied the route.
    """
    import agent

    store = agent.get_store()
    print(f"store:  {store.backend}")
    print(f"engine: {agent.PROVIDER} · {agent.MODEL}")
    print(f"reps:   {args.reps} per side\n")

    if not args.keep:
        store.clear()
        print("memory cleared — starting from nothing\n")

    records: list[dict] = []

    def run_group(label: str, claim: str, reps: int, learn: bool) -> dict[str, list[int]]:
        """Run one side N times. Returns both metrics, per run.

        WASTED lookups is the primary number, not total lookups.

        A wasted lookup is one that came back with anything other than `found` —
        a tag the company does not file. That is exactly what memory removes, and
        it is the expensive one: each failure triggers _suggest_tags, which
        downloads roughly 8 MB listing every tag the company files.

        Total lookups is reported too, but it is a noisy proxy. The instruction
        requires a second lookup before returning CONTRADICTED, so a run can make
        two calls for reasons that have nothing to do with what it remembered.
        Counting failures measures the thing itself rather than a correlate.
        """
        wasted: list[int] = []
        lookups: list[int] = []
        print(f"{label}: {claim}")
        for index in range(1, reps + 1):
            started = time.time()
            try:
                answer, trace = asyncio.run(
                    agent.audit(claim, user_id=args.user, learn=learn)
                )
                error = None
            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                answer, trace, error = "", [], f"{type(exc).__name__}: {exc}"

            elapsed = time.time() - started
            n_acts = sum(1 for e in trace if e["kind"] == "ACT")
            n_wasted = sum(
                1
                for e in trace
                if e["kind"] == "OBSERVE"
                and isinstance(e.get("response"), dict)
                and e["response"].get("status") != "found"
            )
            recalled = sum(1 for e in trace if e["kind"] == "RECALL")
            verdict = next(
                (
                    line.split(":", 1)[1].strip()
                    for line in answer.splitlines()
                    if line.upper().startswith("VERDICT:")
                ),
                "",
            )
            if error is None:
                lookups.append(n_acts)
                wasted.append(n_wasted)

            records.append(
                {
                    "group": label,
                    "rep": index,
                    "claim": claim,
                    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "memory_used": bool(recalled),
                    "n_tool_calls": n_acts,
                    "n_wasted_lookups": n_wasted,
                    "verdict": verdict,
                    "duration_s": round(elapsed, 2),
                    "error": error,
                    "steps": trace,
                    "answer": answer,
                }
            )
            mark = "!" if error else ("M" if recalled else "·")
            print(
                f"  {index:>3}/{reps}  {mark}  wasted={n_wasted}  lookups={n_acts}  "
                f"{elapsed:5.1f}s  {verdict or error or ''}"
            )
        print()
        return {"wasted": wasted, "lookups": lookups}

    before = run_group("BEFORE", GOLDMAN_2022, args.reps, learn=False)

    print("--- one learning run (memory on) ---")
    _, trace = asyncio.run(agent.audit(GOLDMAN_2022, user_id=args.user, learn=True))
    learned = [e for e in trace if e["kind"] == "LEARN"]
    print(("learned: " + learned[0]["detail"]) if learned else "learned nothing")
    print()

    after = run_group("AFTER", GOLDMAN_2023, args.reps, learn=False)

    out = Path(args.out)
    with out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, default=str) + "\n")

    def describe(values: list[int]) -> str:
        if not values:
            return "no successful runs"
        spread = f"{min(values)}–{max(values)}" if min(values) != max(values) else str(min(values))
        return (
            f"median {statistics.median(values):.1f}   range {spread}   "
            f"n={len(values)}"
        )

    def verdict_on(name: str, low: list[int], high: list[int]) -> None:
        """State whether the groups actually separate, rather than implying it.

        Week 4's lesson in one function. A median that moved is not a result if
        the ranges overlap — the baseline there spanned 15 to 17 on its own,
        which was wider than the effect being claimed. So this prints the
        overlap, and prints it as the headline when it exists.
        """
        if not (low and high):
            print(f"  {name}: not enough successful runs to say anything.")
            return
        if max(high) < min(low):
            print(
                f"  {name}: SEPARATED — every memory run ({min(high)}–{max(high)}) "
                f"beat every baseline run ({min(low)}–{max(low)})."
            )
        else:
            print(
                f"  {name}: OVERLAPPING — baseline {min(low)}–{max(low)}, "
                f"memory {min(high)}–{max(high)}. Report the spread, not the medians."
            )

    print("=" * 72)
    print("WASTED LOOKUPS PER AUDIT   (calls returning no data — what memory removes)")
    print(f"  BEFORE (no memory, FY2022)   {describe(before['wasted'])}")
    print(f"  AFTER  (memory,    FY2023)   {describe(after['wasted'])}")
    print()
    verdict_on("wasted lookups", before["wasted"], after["wasted"])

    print()
    print("TOTAL LOOKUPS PER AUDIT   (noisier — the CONTRADICTED rule can add one)")
    print(f"  BEFORE                       {describe(before['lookups'])}")
    print(f"  AFTER                        {describe(after['lookups'])}")
    print()
    verdict_on("total lookups", before["lookups"], after["lookups"])

    # Each wasted lookup costs a _suggest_tags call: roughly 8 MB listing every
    # tag the company files. That download, not the model call, is what memory
    # is actually buying back.
    saved = sum(before["wasted"]) - sum(after["wasted"])
    if saved > 0:
        print(
            f"\n  {saved} fewer failed lookups across {len(after['wasted'])} runs "
            f"≈ {saved * 8} MB not downloaded from data.sec.gov."
        )

    print(f"\n  {len(records)} runs written to {out}")
    print("=" * 72)
    return 0


# --- wiring ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="remember.py",
        description="Durable memory for the SEC Claim Auditor.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Every invocation is a new process. Anything one command can see "
            "that another wrote is, by definition, memory rather than context."
        ),
    )
    parser.add_argument(
        "--user",
        default=os.getenv("MEMORY_USER_ID", "demo-user"),
        help="whose preferences and aliases to use (default: demo-user)",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    p = subs.add_parser("list", help="show everything in memory, refusals included")
    p.set_defaults(func=cmd_list)

    p = subs.add_parser("audit", help="audit one claim and print the trace")
    p.add_argument("claim")
    p.add_argument("--no-learn", action="store_true", help="recall but do not write")
    p.set_defaults(func=cmd_audit)

    p = subs.add_parser("alias", help="teach it what a company name means")
    p.add_argument("alias", help='what gets typed, e.g. "Coca-Cola"')
    p.add_argument("target", help='the real filer, e.g. "KO"')
    p.set_defaults(func=cmd_alias)

    p = subs.add_parser("prefer", help="store a presentation preference")
    p.add_argument("name")
    p.add_argument("value")
    p.set_defaults(func=cmd_prefer)

    p = subs.add_parser("poison", help="try to plant a false tag and watch it be refused")
    p.add_argument("--company", default="Goldman Sachs")
    p.add_argument("--tag", default="TotallyRealTag")
    p.add_argument("--year", type=int, default=2022)
    p.set_defaults(func=cmd_poison)

    p = subs.add_parser("forget", help="delete one fact")
    p.add_argument("kind", choices=sorted(KINDS))
    p.add_argument("key")
    p.set_defaults(func=cmd_forget)

    p = subs.add_parser("clear", help="wipe memory")
    p.add_argument("--all", action="store_true", help="every scope, not just this user")
    p.set_defaults(func=cmd_clear)

    p = subs.add_parser("demo", help="the before/after comparison, repeated")
    p.add_argument("--reps", type=int, default=20, help="runs per side (default 20)")
    p.add_argument("--out", default="memory-demo.jsonl")
    p.add_argument("--keep", action="store_true", help="do not clear memory first")
    p.set_defaults(func=cmd_demo)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
