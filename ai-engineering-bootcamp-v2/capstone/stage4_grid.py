"""Does structure beat tool access? Stage 4.

    .venv/bin/python stage4_grid.py --limit 40

Writes stage4_grid.json.


THE HYPOTHESIS

A single agent with full tool access cannot reproduce what a staged pipeline
gets right. If that holds, the structure is doing the work rather than the
model, and the case for building a pipeline instead of prompting an agent is a
measurement rather than an opinion.

Three conditions, one question, same claims:

  1  model alone, no tools            pure recall
  2  model + the SEC lookup tool      full tool access, free to search as it likes
  3  model + lookup + the retrieved   the same tools, plus one stage of structure
     candidate shortlist

The only difference between 2 and 3 is that 3 is handed the candidates dense
retrieval found. Same model, same tool, same budget. Any gap between them is
attributable to the structure and to nothing else.


THE TEST SET, AND WHY IT IS NOT CIRCULAR

The 61 claims where join.py verified: dense retrieval proposed a concept and the
filed value independently matched the figure in the prose. The correct tag is
therefore known without a human having labelled anything, and known by agreement
between two signals rather than by assertion.

Condition 3 is handed retrieval's candidates, and retrieval helped construct the
test set — so condition 3 is advantaged by construction and its score is an
upper bound, not a measurement of skill. That is stated wherever the number is.
Conditions 1 and 2 are unaffected: neither sees retrieval output, so their
scores are clean.

What the comparison can support: whether tool access alone gets an agent to the
answer. What it cannot support: that condition 3's margin is entirely real.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from agent import audit_checked
from memory import MemoryStore

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

ASK = """{sentence}

The figure to identify is {figure}, for {company}'s fiscal year {fy}.

Which exact XBRL tag did the company file this figure under, and what value did
they file?

Answer in exactly this shape and nothing else:
TAG: <the exact us-gaap tag, or UNKNOWN>
VALUE: <the figure in dollars, or UNKNOWN>"""

SHORTLIST = """

Dense retrieval over the company's filed tag definitions returned these
candidates for this sentence, each with a value filed for that year:
{cands}

One of them may be correct. Verify with the tool before answering."""


def parse(text: str) -> tuple[str | None, str | None]:
    t = re.search(r"TAG:\s*(\S+)", text or "")
    v = re.search(r"VALUE:\s*(.+)", text or "")
    tag = t.group(1).strip().strip(".,`") if t else None
    if tag and ":" in tag:
        tag = tag.split(":")[-1]
    return tag, (v.group(1).strip() if v else None)


def no_tools(client: OpenAI, prompt: str) -> str:
    r = client.chat.completions.create(model=MODEL, temperature=0,
                                       messages=[{"role": "user", "content": prompt}])
    return r.choices[0].message.content or ""


async def with_agent(prompt: str, store) -> str:
    answer, _, _ = await audit_checked(prompt, store=store, learn=False)
    return answer or ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()

    rows = [json.loads(l) for l in (HERE / "join.jsonl").open(encoding="utf-8")]
    gold = [r for r in rows if r["bucket"] == "verified"][: a.limit]
    print(f"  {len(gold)} verified claims as the test set · {MODEL}\n")

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    import tempfile
    store = MemoryStore(dsn="", sqlite_path=Path(tempfile.gettempdir()) / "s4.db")

    results = {"no_tools": [], "tools": [], "tools_plus_structure": []}
    for i, r in enumerate(gold, 1):
        base = ASK.format(sentence=r["raw_sentence"][:600], figure=r["figure"],
                          company="JPMorgan Chase", fy=r["fiscal_year"])
        cands = f"  - {r['best_tag']}" if r.get("best_tag") else "  (none)"
        structured = base + SHORTLIST.format(cands=cands)

        got = {}
        got["no_tools"] = parse(no_tools(client, base))
        time.sleep(2)
        got["tools"] = parse(asyncio.run(with_agent(base, store)))
        time.sleep(4)
        got["tools_plus_structure"] = parse(asyncio.run(with_agent(structured, store)))
        time.sleep(4)

        line = f"  [{i:>2}/{len(gold)}] {r['matched_tag'][:34]:36s}"
        for k in ("no_tools", "tools", "tools_plus_structure"):
            tag, _ = got[k]
            ok = bool(tag) and tag.lower() == r["matched_tag"].lower()
            results[k].append({"id": r["id"], "expected": r["matched_tag"],
                               "got": tag, "correct": ok})
            line += f"  {'Y' if ok else ('-' if not tag or tag == 'UNKNOWN' else 'n')}"
        print(line)

    print(f"\n  {'condition':26s} {'exact tag':>10} {'declined':>9}")
    print("  " + "-" * 48)
    for k, label in (("no_tools", "model alone, no tools"),
                     ("tools", "model + SEC lookup"),
                     ("tools_plus_structure", "+ retrieved shortlist")):
        rs = results[k]
        ok = sum(x["correct"] for x in rs)
        dec = sum(1 for x in rs if not x["got"] or x["got"] == "UNKNOWN")
        print(f"  {label:26s} {ok:>4}/{len(rs)} {ok/len(rs):>4.0%} {dec:>8}")

    (HERE / "stage4_grid.json").write_text(json.dumps(
        {"model": MODEL, "n": len(gold), "results": results,
         "caveat": ("Condition 3 is handed retrieval's candidates and retrieval "
                    "helped construct the test set, so its score is an upper "
                    "bound. Conditions 1 and 2 are clean.")}, indent=2), encoding="utf-8")
    print("\n  wrote stage4_grid.json")


if __name__ == "__main__":
    main()
