"""How often does a model invent a source when it cannot decline safely?

    python3 blind_probe.py --n 40

Writes blind_probe.json.


WHY THIS RUN AND NOT THE ONES ALREADY RECORDED

The capstone has two saved experiments that asked a model for the concept behind
a figure, and both report almost no fabrication: 1 in 25, and 1 in 40. Read the
transcripts and the reason is plain — the question handed the model the figure,
so it could answer "I can't access SEC filings" and be entirely right. It
declined 24 times out of 25 and the fabrication rate is a measure of how often it
felt cornered, not of what it does when it commits.

Strip the figure out and the question changes. Now the model must supply the
number as well as the source, and declining costs it the whole answer. That is
the position an assistant is in when a user asks it something and expects a
reply — which is the case the gate exists for.

An eight-claim version of this run produced five fabricated concepts and four
correct figures. Five out of eight is not a number to put in front of anyone, so
this runs a proper sample and records it.

The prompt is deliberately the same one the capstone's app uses live, so the
figure a reader sees on the deployed page and the figure here come from asking
the same question.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

HERE = Path(__file__).parent
CAPSTONE = HERE.parent / "capstone"
load_dotenv(CAPSTONE / ".env")

ASK = """Below is one sentence from {company}'s 10-K, Item 7, for
fiscal year {fy}. Every figure in it has been removed.

    {masked}

The removed figure marked [[ ? ]] is the one to identify.

Which exact XBRL tag did {company} file it under, and what value did they
file for fiscal year {fy}?

Answer in exactly this shape and nothing else:
TAG: <the exact us-gaap tag, or UNKNOWN>
VALUE: <the figure in dollars, or UNKNOWN>"""


def parse(text: str) -> tuple[str | None, str | None]:
    t = re.search(r"TAG:\s*(\S+)", text or "")
    v = re.search(r"VALUE:\s*(.+)", text or "")
    tag = t.group(1).strip().strip(".,`") if t else None
    return tag, (v.group(1).strip() if v else None)


def main() -> int:
    import sys
    sys.path.insert(0, str(CAPSTONE))
    import extract as ex

    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--ticker", default="JPM")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    def redact(sentence: str, figure: str) -> str:
        spans = sorted({(m.start(), m.end()) for m in ex.MONEY.finditer(sentence)}
                       | {(m.start(), m.end()) for m in ex.PCT.finditer(sentence)})
        spans = [(x, y) for x, y in spans
                 if not any(c <= x and y <= d and (c, d) != (x, y) for c, d in spans)]
        if not spans:
            return sentence
        target = sentence.find(figure)
        if target < 0:
            target = spans[0][0]
        out, last = [], 0
        for x, y in spans:
            out.append(sentence[last:x])
            out.append("[[ ? ]]" if x == target else "[…]")
            last = y
        out.append(sentence[last:])
        return "".join(out)

    rows = [json.loads(l) for l in (CAPSTONE / "coverage.jsonl").open(encoding="utf-8")]
    pool = [r for r in rows
            if r["ticker"] == a.ticker and r["structural"] == "reachable" and r["has_tag"]]
    random.Random(a.seed).shuffle(pool)
    pool = pool[: a.n]

    company = {"JPM": "JPMorgan Chase", "BAC": "Bank of America", "MS": "Morgan Stanley",
               "WFC": "Wells Fargo", "C": "Citigroup"}[a.ticker]
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-4o")

    out = []
    for i, r in enumerate(pool, 1):
        prompt = ASK.format(company=company, fy=r["fiscal_year"],
                            masked=redact(r["raw_sentence"][:600], r["figure"]))
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0,
                messages=[{"role": "user", "content": prompt}])
            raw = resp.choices[0].message.content or ""
        except Exception as exc:                       # noqa: BLE001
            print(f"  [{i:>2}] failed: {type(exc).__name__}")
            continue
        tag, value = parse(raw)
        out.append({"id": r["id"], "ticker": a.ticker, "fy": r["fiscal_year"],
                    "figure": r["figure"], "tag": tag, "value": value, "raw": raw[:300]})
        print(f"  [{i:>2}/{len(pool)}] {str(tag)[:46]:48} {str(value)[:20]}")
        time.sleep(0.6)

    path = HERE / "blind_probe.json"
    path.write_text(json.dumps(
        {"model": model, "ticker": a.ticker, "n": len(out), "seed": a.seed,
         "method": ("One call per claim, no tools, temperature 0. Every figure in "
                    "the sentence is masked and the one under test marked [[ ? ]], "
                    "so the model must supply the number rather than repeat it. "
                    "UNKNOWN is explicitly offered as an acceptable answer."),
         "results": out}, indent=2), encoding="utf-8")
    print(f"\n  wrote {path.name} ({len(out)} answers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
