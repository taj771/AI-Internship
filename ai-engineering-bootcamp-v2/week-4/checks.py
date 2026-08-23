"""
The codified checks: grade a recorded run without a human and without a model.

    .venv/bin/python checks.py                          # grade traces.jsonl
    .venv/bin/python checks.py --file traces-after.jsonl
    .venv/bin/pytest test_checks.py -q                  # same checks, as tests

Three checks, each tied to a failure actually observed in the twenty baseline
runs and named in taxonomy.md. Each returns a binary pass/fail and one line of
reason. There is no score, no partial credit and no 1-5 scale: a scale invites a
3, and a 3 is a way of not deciding.


WHY NONE OF THESE ASK A MODEL

An LLM judge would be slower, cost money per run, and — the part that matters —
return slightly different answers on different days. A pass rate that moves
because the grader was in a different mood cannot be used to show that a fix
worked. Worse, the judge would share the weaknesses of the thing it is judging:
the agent's characteristic failure this week is asserting things it has not
checked, and asking the same model family to check that is circular.

So these are narrow and dull on purpose. Dull and repeatable beats clever and
unpredictable when the whole job is measuring a change.


WHAT THESE CANNOT SEE

Four of the eight failures found by hand are invisible here. C05 and C06 chose
badly from a suggestion list, C10 dropped a restatement from its answer, C17
audited a company nobody asked about. Deciding those needs somebody who knows
what the numbers mean.

That is not a gap to apologise for, it is the division of labour: code catches
the mechanical failures every time and for free, and a human keeps reading
traces for the rest. A suite that claimed to catch everything would be lying
about the four it cannot.


ON FALSE POSITIVES

Every check here was run against all twenty baseline runs and adjusted until it
stopped flagging runs a human had passed. Two earlier drafts did:

    "CONTRADICTED with only one lookup is a failure"
        flagged C04, a correct run — $200bn against $49.55bn needs no search.

    "the FILED figure must appear verbatim in a tool result"
        flagged C14, a correct run — the tool said "$94.95B" and the agent
        wrote "$94,950 million", the same money in the claim's own units.

A check that cries wolf on good runs gets ignored, and an ignored check is worse
than no check because it looks like coverage. Both were fixed rather than
dropped: check C gained the size condition, and money is compared numerically
rather than as text.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from trace_log import ALLOWED_VERDICTS, ANSWER_FIELDS, DEFAULT_PATH, load_records

# Written as (name, function) so the suite has one definition of what it
# contains — the CLI, the pytest wrapper and the Streamlit page all read this
# list rather than each keeping their own.
CHECKS: list[tuple[str, callable]] = []


def check(name: str):
    """Register a check under a short stable name."""

    def register(fn):
        CHECKS.append((name, fn))
        return fn

    return register


# --- shared parsing ---------------------------------------------------------

_MULTIPLIER = {
    "trillion": 1e12,
    "t": 1e12,
    "billion": 1e9,
    "b": 1e9,
    "bn": 1e9,
    "million": 1e6,
    "m": 1e6,
    "thousand": 1e3,
    "k": 1e3,
}

# Above this multiple of the filed figure, a claim is beyond rescue by any
# definition and no alternative need be tested. See the long note in
# check_contradicted_was_searched for why the bound applies only upwards.
EXPLAINABLE_RATIO = 2.0

_MONEY = re.compile(
    r"\$?\s*([\d,]+(?:\.\d+)?)\s*(trillion|billion|million|thousand|bn|[tbmk])?\b",
    re.IGNORECASE,
)


def parse_money(text: str | None) -> float | None:
    """Turn '$94,950 million' or '$94.95B' into a number of dollars.

    Returns None when there is no figure to read, which includes the agent's
    own "none found" — that is an absence of a figure rather than a broken
    answer, and the checks that care handle it as such.

    Comparing money as text is what broke an earlier version of check C. The
    tool returns "$94.95B" and the agent may restate it as "$94,950 million" to
    match the claim's units; both are the same amount and a string comparison
    calls them different.
    """
    if not text:
        return None

    match = _MONEY.search(text)
    if not match:
        return None

    amount = float(match.group(1).replace(",", ""))
    unit = (match.group(2) or "").lower()
    return amount * _MULTIPLIER.get(unit, 1.0)


def first_found_turn(record: dict) -> int | None:
    """The turn on which the first successful lookup came back, if any."""
    for step in record.get("steps", []):
        if step.get("kind") != "OBSERVE":
            continue
        response = step.get("response")
        if isinstance(response, dict) and response.get("status") == "found":
            return step.get("turn")
    return None


# --- check A: the answer has the shape it promised --------------------------


@check("answer_format")
def check_answer_format(record: dict) -> tuple[bool, str]:
    """All five fields present, and VERDICT is one of the four allowed words.

    Catches F2 in the taxonomy — C21 and C22, both of which replied in prose
    asking the user for more information instead of answering. Their judgement
    was right in both cases and NOT_CHECKABLE exists for exactly that, but an
    answer with no VERDICT line cannot be read by anything downstream.

    A crashed run fails here too, and should: no answer is not a format the
    caller can work with either.
    """
    answer = record.get("answer")
    if not answer:
        return False, "no answer at all" + (f" ({record['error']})" if record.get("error") else "")

    parsed = record.get("parsed") or {}
    missing = [field for field in ANSWER_FIELDS if not parsed.get(field)]
    if missing:
        return False, f"missing {', '.join(missing)}"

    verdict = parsed["VERDICT"].strip().upper()
    if verdict not in ALLOWED_VERDICTS:
        return False, f"verdict {verdict!r} is not one of the four allowed"

    return True, "five fields present, verdict allowed"


# --- check B: it looked something up ----------------------------------------


@check("evidence_exists")
def check_evidence_exists(record: dict) -> tuple[bool, str]:
    """At least one tool call was made.

    Catches F6 — C19, which answered "OpenAI is not a public company as of the
    last update" with zero lookups, and whose TAG line reads "I have not
    attempted any tag lookup".

    The standard is deliberate and worth defending, because it also flags C21,
    where the claim names no company at all and there is arguably nothing to
    look up. There is: a lookup on whatever the claim does name returns
    company_not_found, and that is evidence with a date on it. "As of the last
    update" is a statement about the model's training, and it would produce the
    same confident answer the day after an IPO.

    Rule 1 of the agent's own instruction is that the only acceptable source is
    a tool result. A run with no tool result has no acceptable source.
    """
    calls = record.get("tool_calls") or []
    if not calls:
        return False, "answered with no tool call — no evidence for any claim in the answer"

    tags = [c.get("args", {}).get("xbrl_tag") for c in calls]
    return True, f"{len(calls)} lookup(s): {', '.join(t for t in tags if t)}"


# --- check C: CONTRADICTED is a claim about absence --------------------------


@check("contradicted_was_searched")
def check_contradicted_was_searched(record: dict) -> tuple[bool, str]:
    """A smaller-than-filed claim must be tested before it is contradicted.

    Catches F4 — C09, where "Goldman Sachs' revenue in 2022 was $7.4 billion"
    was ruled CONTRADICTED against a filed $47.37bn. $7.4bn is real: it is
    Goldman's investment banking revenue, and InvestmentBankingRevenue was
    second in the suggestion list the agent had already read, with a lookup to
    spare.

    CONTRADICTED asserts that no alternative definition accounts for the gap.
    That is a claim about absence, and absence has to be looked for.

    THE SIZE CONDITION, AND WHY IT IS ASYMMETRIC

    Requiring a search before every CONTRADICTED flags C04 — $200bn claimed
    against $49.55bn filed — which a human passed and which is correct. Nothing
    is four times a company's net income, so demanding a search there is
    make-work. Hence a threshold.

    The first version of this check set that threshold at "claimed is below
    filed", on the reasoning that a smaller figure can be a segment and a larger
    one cannot be part of the total. That is true and it was not enough. The
    matching instruction rule caused a regression: on C03, JPMorgan's $132.3bn
    claim against $128.69bn filed, the agent had passed at baseline with
    DEFINITION_MISMATCH and afterwards ruled CONTRADICTED without looking —
    reading "no test required above the filed figure" as permission. This check
    did not catch it, because the check carried the same misconception as the
    rule. The suite got greener while the agent got worse on its hardest claim.

    So the threshold is now a ratio, and it is asymmetric for a reason:

        claimed below filed          always test. A segment can be any
                                     fraction — Goldman's investment banking
                                     revenue is a sixth of the firm's total.

        claimed up to 2x filed       test. A broader or non-GAAP definition
                                     runs a few percent to a few tens of
                                     percent above the filed figure, which is
                                     precisely the C03 case.

        claimed above 2x filed       no test. No definition of revenue is twice
                                     another.

    This is domain reasoning rather than a general rule, which is why the check
    lives in a file that knows what an SEC filing is.

    Consequence worth stating plainly: tightening this makes C13 (Tesla, $110bn
    claimed against $96.77bn filed, ruled CONTRADICTED off one lookup) fail at
    baseline where it previously passed. The baseline number goes down. That is
    the correct direction — the run always had this fault and the earlier check
    could not see it.
    """
    parsed = record.get("parsed") or {}
    verdict = (parsed.get("VERDICT") or "").strip().upper()

    if verdict != "CONTRADICTED":
        return True, "not a CONTRADICTED verdict — check does not apply"

    claimed = parse_money(parsed.get("CLAIMED"))
    filed = parse_money(parsed.get("FILED"))

    # Missing figures are check A's problem, not this one. Failing here as well
    # would count one fault twice and make the totals overstate the damage.
    if claimed is None or filed is None:
        return True, "no comparable figures — check does not apply"

    if claimed > EXPLAINABLE_RATIO * filed:
        return True, (
            f"claimed {claimed / 1e9:.2f}B is more than {EXPLAINABLE_RATIO:g}x filed "
            f"{filed / 1e9:.2f}B — beyond any definition, no search required"
        )

    found_turn = first_found_turn(record)
    if found_turn is None:
        return True, "no successful lookup to search beyond — check does not apply"

    later = [c for c in (record.get("tool_calls") or []) if (c.get("turn") or 0) > found_turn]
    if later:
        tags = ", ".join(c.get("args", {}).get("xbrl_tag", "?") for c in later)
        return True, f"tested {tags} after finding the figure"

    return False, (
        f"claimed {claimed / 1e9:.2f}B is below filed {filed / 1e9:.2f}B "
        f"({claimed / filed:.0%} of it) and could be a segment, but no tag was "
        "tried after the figure was found"
    )


# --- running the suite ------------------------------------------------------


def grade(record: dict) -> dict:
    """Run every check against one record."""
    results = {}
    for name, fn in CHECKS:
        passed, reason = fn(record)
        results[name] = {"passed": passed, "reason": reason}
    return results


def grade_all(records: list[dict]) -> list[dict]:
    """Attach results to each record without modifying the stored trace."""
    graded = []
    for record in records:
        results = grade(record)
        graded.append(
            {
                "trace_id": record["trace_id"],
                "run_label": record.get("run_label", ""),
                "claim": record["claim"],
                "human_pass_fail": record.get("your_pass_fail", ""),
                "checks": results,
                "passed_all": all(r["passed"] for r in results.values()),
            }
        )
    return graded


def summarise(graded: list[dict]) -> dict:
    """Totals overall and per check."""
    total = len(graded)
    per_check = {
        name: sum(1 for g in graded if g["checks"][name]["passed"]) for name, _ in CHECKS
    }
    return {
        "total": total,
        "passed_all": sum(1 for g in graded if g["passed_all"]),
        "per_check": per_check,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade recorded runs.")
    parser.add_argument("--file", default=str(DEFAULT_PATH))
    parser.add_argument("--label", help="only grade runs with this run_label")
    args = parser.parse_args()

    records = load_records(Path(args.file))
    if args.label:
        records = [r for r in records if r.get("run_label") == args.label]

    if not records:
        parser.error(f"no records in {args.file}" + (f" with label {args.label!r}" if args.label else ""))

    graded = grade_all(records)
    names = [name for name, _ in CHECKS]

    header = "     " + "".join(f"{n[:14]:<16}" for n in names)
    print(header)
    for g in graded:
        marks = "".join(
            ("  pass" if g["checks"][n]["passed"] else "  FAIL").ljust(16) for n in names
        )
        print(f"{g['trace_id']:<5}{marks}")

    for g in graded:
        for name in names:
            if not g["checks"][name]["passed"]:
                print(f"\n{g['trace_id']}  {name}\n    {g['checks'][name]['reason']}")

    stats = summarise(graded)
    print("\n" + "-" * 60)
    for name in names:
        print(f"  {name:<28} {stats['per_check'][name]:>2} / {stats['total']}")
    print(f"  {'ALL CHECKS':<28} {stats['passed_all']:>2} / {stats['total']}"
          f"  ({stats['passed_all'] / stats['total']:.0%})")

    # The human grade is shown alongside, never reconciled automatically. They
    # measure different things: the suite catches what code can see, and a
    # human failed four runs for reasons no test here can reach. A gap between
    # the two is expected, and a suite that matched the human exactly would
    # mean the checks had been written to fit the answers.
    human_pass = sum(1 for g in graded if g["human_pass_fail"] == "pass")
    if any(g["human_pass_fail"] for g in graded):
        print(f"  {'(human open coding)':<28} {human_pass:>2} / {stats['total']}")


if __name__ == "__main__":
    main()
