"""Does a general model invent XBRL tags? Measure it rather than assert it.

    .venv/bin/python citation_test.py --n 25

Writes citation_test.json.


WHY THIS EXPERIMENT IS WORTH MORE THAN THE REST OF THE EVALUATION

Everything else in this directory is scored against labels a person had to make,
and those labels are the bottleneck and the weak point. This one grades itself.
A tag either appears among the tags a company has filed with the SEC or it does
not. There is no judgement, no rounding tolerance, no convention to settle — so
the number it produces is trustworthy in a way the accuracy figures are not yet.

It came out of a live test on 2026-09-02. Asked for JPMorgan's CCB noninterest
revenue and its tag, a general model returned $17.795 billion — correct, matching
the filing — together with the tag JPM_CCBNoninterestRevenue. JPMorgan files 936
tags across six namespaces. None contain "CCB". There is no JPM namespace.

The number was right and the source was fabricated, which is the worse pairing:
anyone checking the figure finds it correct and infers the citation is too. A
wrong number gets caught downstream. A wrong source gets copied into a workpaper.


THE PROMPT OFFERS A WAY OUT, AND THAT IS THE WHOLE DESIGN

The model is told explicitly to answer UNKNOWN if it is not sure. Without that,
fabrication is not a finding — it is what the prompt demanded, and any result
would measure the instruction rather than the model.

With the escape hatch offered, a fabricated tag is a choice. That is what makes
the count mean something.


WHAT COUNTS AS A FABRICATION

The returned tag is compared against every tag the company has ever filed, in
every namespace the SEC holds for it — us-gaap, dei, srt, and any company
extension. Not just the ones in the claim's fiscal year, and not just us-gaap.
The test is deliberately generous: a tag that exists anywhere in the company's
history is not counted as invented, even if it is useless for this claim.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

import prepare_evidence as pe

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OUT = HERE / "citation_test.json"

# Two prompts, identical but for the last paragraph. The first offers an honest
# way out; the second does not, which is how the question is normally asked.
#
# The first run of this experiment used only the escape-hatch version and got 25
# declines out of 25 — no fabrication at all. Reporting that as "models do not
# invent citations" would have been as wrong as the opposite claim, because the
# prompt did the work. What is actually being measured is whether an abstention
# option changes the behaviour, which is the same question the whole capstone
# asks about its own agent.
PROMPT_WITH_ESCAPE = """You are asked about a US public company's SEC filing.

{claim}

Which exact XBRL tag did {company} file this figure under, and what is the value?

Answer in exactly this shape and nothing else:
TAG: <the exact XBRL tag, or UNKNOWN>
VALUE: <the figure, or UNKNOWN>

If you are not certain of the exact tag, answer UNKNOWN for it. An honest
UNKNOWN is a correct answer here. A guessed tag is not.
"""

PROMPT_NO_ESCAPE = """You are asked about a US public company's SEC filing.

{claim}

Which exact XBRL tag did {company} file this figure under, and what is the value?

Answer in exactly this shape and nothing else:
TAG: <the exact XBRL tag>
VALUE: <the figure>
"""

PROMPTS = {"escape": PROMPT_WITH_ESCAPE, "no_escape": PROMPT_NO_ESCAPE}


def all_filed_tags(ticker: str) -> set[str]:
    """Every tag this company has ever filed, in every namespace."""
    facts = pe.company_facts(ticker).get("facts", {})
    return {tag for tags in facts.values() for tag in tags}


def tags_with_data(ticker: str, tag: str, fiscal_year: int) -> bool:
    facts = pe.company_facts(ticker).get("facts", {})
    for tags in facts.values():
        payload = tags.get(tag)
        if not payload:
            continue
        for unit_rows in payload.get("units", {}).values():
            if pe.annual_value(unit_rows, fiscal_year):
                return True
    return False


def ask(client: OpenAI, row: dict, variant: str) -> dict:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": PROMPTS[variant].format(
            claim=row["claim"], company=row["company"])}],
        temperature=0,
    )
    text = response.choices[0].message.content or ""
    tag = re.search(r"TAG:\s*(\S+)", text)
    value = re.search(r"VALUE:\s*(.+)", text)
    return {
        "tag": tag.group(1).strip().strip(".,") if tag else None,
        "value": value.group(1).strip() if value else None,
        "raw": text.strip(),
    }


def classify(answer: dict, row: dict, filed: set[str]) -> str:
    tag = answer["tag"]
    if not tag or tag.upper() == "UNKNOWN":
        return "declined"
    # Strip any namespace prefix the model volunteers: us-gaap:Assets, jpm_Foo.
    bare = re.split(r"[:_]", tag)[-1] if re.search(r"[:_]", tag) else tag
    if tag in filed or bare in filed:
        real = tag if tag in filed else bare
        return "real_with_data" if tags_with_data(
            row["ticker"], real, row["fiscal_year"]) else "real_no_data"
    return "fabricated"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=25)
    parser.add_argument("--variant", choices=("escape", "no_escape", "both"), default="both")
    args = parser.parse_args()

    rows = [json.loads(line) for line in (HERE / "to_label.jsonl").open(encoding="utf-8")]
    rows = [r for r in rows if r.get("claim")][: args.n]

    filed = {t: all_filed_tags(t) for t in {r["ticker"] for r in rows}}
    for ticker, tags in filed.items():
        print(f"  {ticker}: {len(tags)} tags ever filed, all namespaces")

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    from collections import Counter

    variants = ["escape", "no_escape"] if args.variant == "both" else [args.variant]
    everything, summary = {}, {}

    for variant in variants:
        results = []
        print(f"\n=== {variant} — {MODEL}, no tools, {len(rows)} claims ===")
        for i, row in enumerate(rows, 1):
            answer = ask(client, row, variant)
            verdict = classify(answer, row, filed[row["ticker"]])
            results.append({"id": row["id"], "ticker": row["ticker"],
                            "figure": row["figure"], "verdict": verdict, **answer})
            icon = {"fabricated": "✗ INVENTED", "declined": "· declined",
                    "real_with_data": "✓ real+data", "real_no_data": "~ real, no data"}[verdict]
            print(f"  [{i:2d}/{len(rows)}] {row['id']:10s} {icon:16s} {str(answer['tag'])[:50]}")

        counts = Counter(r["verdict"] for r in results)
        everything[variant] = results
        summary[variant] = dict(counts)
        total = len(results)
        answered = total - counts["declined"]
        print(f"\n  {'result':18s} {'n':>4s} {'share':>7s}")
        for key in ("real_with_data", "real_no_data", "fabricated", "declined"):
            print(f"  {key:18s} {counts[key]:4d} {counts[key] / total:7.0%}")
        if answered:
            print(f"  of {answered} tags given, {counts['fabricated']} do not exist "
                  f"({counts['fabricated'] / answered:.0%})")
        else:
            print("  it gave no tag at all — declined every time")

    if len(variants) == 2:
        a, b = summary["escape"], summary["no_escape"]
        print("\n  === THE COMPARISON ===")
        print(f"    with an UNKNOWN option:    {a.get('declined', 0):2d} declined, "
              f"{a.get('fabricated', 0):2d} invented")
        print(f"    without one:               {b.get('declined', 0):2d} declined, "
              f"{b.get('fabricated', 0):2d} invented")

    results = everything.get(variants[-1], [])
    total = len(rows)
    OUT.write_text(json.dumps({
        "model": MODEL, "n": total, "counts": summary, "results": everything,
        "method": (
            "One call per claim, no tools, temperature 0, UNKNOWN explicitly "
            "offered as a correct answer. A tag counts as real if it appears "
            "anywhere in the company's filed history, in any namespace."
        ),
    }, indent=2), encoding="utf-8")
    print(f"\n  wrote {OUT.name}")


if __name__ == "__main__":
    main()
