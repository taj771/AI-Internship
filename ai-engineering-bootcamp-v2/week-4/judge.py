"""
The LLM judge for grounding, and its validation.

    .venv/bin/python judge.py --version v1        # run and score one version
    .venv/bin/python judge.py --compare           # every version side by side

Path B of the assignment: one binary judge for a semantic failure, validated
with true and false positive rates against hand labels rather than raw
agreement.

The judge exists because three of the four failures the code checks cannot see
are semantic. It is validated because a judge nobody has measured is not an
instrument, it is a second opinion with a confident tone.


WHY NOT AGREEMENT

11 of the 40 labelled runs are ungrounded. A judge that answers GROUNDED to
everything — no model, no prompt, a single return statement — scores 72%
agreement on this set and catches nothing. Any number that a constant function
can beat is not measuring the thing it claims to.

So both rates are reported:

    TPR   of the runs a human called ungrounded, how many did the judge catch
          low = the judge sleeps through failures

    TNR   of the runs a human called grounded, how many did it leave alone
          low = the judge cries wolf, and gets switched off within a week

Neither alone is sufficient, and the always-GROUNDED score is printed beside
them so the comparison stays visible.


ONE MODEL, TWO PROMPTS

Every version below runs on the same model at the same temperature. Only the
prompt text changes between them, so a difference in the rates is attributable
to the wording rather than to a model swap — the same discipline as changing
one thing at a time in step 8, applied to the judge.

Temperature 0, because a judge that returns different answers to the same run
on different days cannot be used to detect a change in anything else.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from grounding import DEFINITION, evidence, key, load_labels, rates, sample

load_dotenv()

MODEL = os.getenv("JUDGE_MODEL", "gpt-4o-mini")
CACHE_PATH = Path(__file__).parent / "judgements_grounding.jsonl"


# --- the prompts ------------------------------------------------------------

# v1 is the version anyone would write first: state the question, show the
# evidence, ask for a verdict. It is kept unchanged after v2 was written so the
# comparison is honest — improving v1 in passing would erase the finding.
V1 = """\
You are checking one line of reasoning written by an SEC claim-auditing agent.

{definition}

THE CLAIM THE AGENT WAS AUDITING
{claim}

WHAT THE TOOL RETURNED TO THE AGENT
{evidence}

THE REASONING TO JUDGE
{reasoning}

Answer in exactly two lines:
VERDICT: GROUNDED or UNGROUNDED
WHY: one sentence, naming the specific phrase that decided it
"""

# v2 is the single refinement, written after reading every run v1 got wrong.
# What it adds and why is recorded in judge_notes.md rather than guessed at.
V2 = """\
You are checking one line of reasoning written by an SEC claim-auditing agent.

{definition}

HOW TO DECIDE, IN ORDER

1. Split the reasoning into its separate factual statements.
2. For each one, find the exact field in the tool results that supports it. If
   you cannot point at a field, that statement is unsupported.
3. Statements about what does NOT exist need particular care. "No alternative
   definition explains this", "no relevant tags could be found", "under any of
   the common tags", "it is not a public company" are claims about everything
   that was not checked. They are supported only if the tool was actually asked
   and answered. Two lookups returning the same number do not establish that a
   third would have differed.
4. A statement scoped to what was tried — "under these tags", "under the tag I
   checked" — is supported. The same statement widened to "any tag" is not.
5. Comparing two figures the agent holds is not an assertion about the world:
   that they match, that a gap is too large for rounding, that a claim is too
   vague to pin down. Those are grounded.
6. Naming a company differently from every tool result is unsupported. If the
   results all say COCA-COLA EUROPACIFIC PARTNERS plc, reasoning that reports a
   finding about "Coca-Cola" is asserting something not in evidence.

Being correct about the real world does not make a statement grounded. A true
fact the tool did not supply is exactly what this check is for.

THE CLAIM THE AGENT WAS AUDITING
{claim}

WHAT THE TOOL RETURNED TO THE AGENT
{evidence}

THE REASONING TO JUDGE
{reasoning}

Answer in exactly two lines:
VERDICT: GROUNDED or UNGROUNDED
WHY: one sentence, naming the specific phrase that decided it
"""

PROMPTS = {"v1": V1, "v2": V2}


# --- running it -------------------------------------------------------------


def load_cache() -> dict[str, dict]:
    if not CACHE_PATH.exists():
        return {}
    cache = {}
    for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            cache[f"{entry.get('model', MODEL)}/{entry['version']}/{entry['key']}"] = entry
    return cache


def save_cache(cache: dict[str, dict]) -> None:
    with open(CACHE_PATH, "w", encoding="utf-8") as handle:
        for entry_key in sorted(cache):
            handle.write(json.dumps(cache[entry_key], ensure_ascii=False) + "\n")


def judge_one(client: OpenAI, record: dict, version: str) -> tuple[str, str]:
    """Ask the judge about one run. Returns (verdict, reason)."""
    reasoning = (record.get("parsed") or {}).get("REASONING")
    if not reasoning:
        # No reasoning line means nothing was asserted, so nothing can be
        # unsupported. Decided here rather than sent to the model: spending a
        # call to have it agree that an empty string contains no false claims
        # would add cost and a chance of a wrong answer for no information. The
        # human labelling page states the same rule, so both sides match.
        return "GROUNDED", "no REASONING line — nothing asserted"

    prompt = PROMPTS[version].format(
        definition=DEFINITION,
        claim=record["claim"],
        evidence=evidence(record),
        reasoning=reasoning,
    )

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content or ""

    verdict, why = "", ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("VERDICT:"):
            verdict = stripped.split(":", 1)[1].strip().upper()
        elif stripped.upper().startswith("WHY:"):
            why = stripped.split(":", 1)[1].strip()

    # An unparseable answer is recorded as such rather than coerced to one side.
    # Defaulting it to GROUNDED would quietly inflate the true negative rate.
    if verdict not in ("GROUNDED", "UNGROUNDED"):
        verdict = "UNPARSEABLE"
        why = text[:160]

    return verdict, why


def run(version: str, refresh: bool = False) -> list[dict]:
    """Judge every labelled run. Cached, so reruns cost nothing."""
    labels = load_labels()
    cache = load_cache()
    client = OpenAI()

    results = []
    for record in sample():
        entry_key = key(record)
        if entry_key not in labels:
            continue

        # Keyed on the model as well as the prompt version. Without it, a
        # comparison across models silently returns the first model's
        # answers and reports them as the second's.
        cache_key = f"{MODEL}/{version}/{entry_key}"
        if refresh or cache_key not in cache:
            verdict, why = judge_one(client, record, version)
            cache[cache_key] = {
                "version": version,
                "key": entry_key,
                "model": MODEL,
                "verdict": verdict,
                "why": why,
            }
            save_cache(cache)

        entry = cache[cache_key]
        results.append(
            {
                "key": entry_key,
                "human": labels[entry_key]["label"],
                "human_note": labels[entry_key].get("note", ""),
                "judge": entry["verdict"],
                "why": entry["why"],
            }
        )
    return results


def report(version: str, results: list[dict]) -> dict:
    pairs = [(r["human"], r["judge"]) for r in results]
    stats = rates(pairs)

    print(f"\n=== {version} · {MODEL} · {stats['n']} labelled runs ===")
    print(f"  {'':22}judge says UNGROUNDED   judge says GROUNDED")
    print(f"  human UNGROUNDED{stats['tp']:>16}{stats['fn']:>22}")
    print(f"  human GROUNDED  {stats['fp']:>16}{stats['tn']:>22}")
    print()
    print(f"  TPR  {stats['tpr']:.0%}   caught {stats['tp']} of {stats['positives']} ungrounded runs")
    print(f"  TNR  {stats['tnr']:.0%}   left alone {stats['tn']} of {stats['negatives']} grounded runs")
    print(f"  agreement {stats['agreement']:.0%}  "
          f"(an always-GROUNDED judge scores {stats['always_grounded_agreement']:.0%} "
          f"with TPR 0%)")

    misses = [r for r in results if r["human"] == "UNGROUNDED" and r["judge"] != "UNGROUNDED"]
    false_alarms = [r for r in results if r["human"] == "GROUNDED" and r["judge"] == "UNGROUNDED"]

    if misses:
        print("\n  MISSED (human said ungrounded, judge did not):")
        for r in misses:
            print(f"    {r['key']:<16} human: {r['human_note'][:66]}")
            print(f"    {'':16} judge: {r['why'][:66]}")
    if false_alarms:
        print("\n  FALSE ALARMS (human said grounded, judge flagged it):")
        for r in false_alarms:
            print(f"    {r['key']:<16} judge: {r['why'][:66]}")

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and validate the grounding judge.")
    parser.add_argument("--version", default="v1", choices=sorted(PROMPTS))
    parser.add_argument("--compare", action="store_true", help="run every version")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache")
    args = parser.parse_args()

    if not load_labels():
        parser.error("no labels yet — run `streamlit run label_grounding.py` first")

    versions = sorted(PROMPTS) if args.compare else [args.version]
    summary = {}
    for version in versions:
        summary[version] = report(version, run(version, refresh=args.refresh))

    if len(summary) > 1:
        print("\n" + "=" * 58)
        print(f"  {'':6}{'TPR':>8}{'TNR':>8}{'agreement':>12}")
        for version, stats in summary.items():
            print(f"  {version:<6}{stats['tpr']:>7.0%}{stats['tnr']:>8.0%}"
                  f"{stats['agreement']:>11.0%}")


if __name__ == "__main__":
    main()
