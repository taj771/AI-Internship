"""Join a claim to the concept it refers to, then compare. Stage 2, rebuilt.

    .venv/bin/python join.py --limit 900

Reads coverage.jsonl, writes join.jsonl.


WHAT CHANGED, AND WHY THE FIRST VERSION COULD NOT WORK

consistency.py pinned exactly one tag per claim using word overlap, then
compared. The pin was wrong about ninety-five times in a hundred, so the
"consistency rate" it produced measured the matcher and nothing else.

Two things fix that.

**Retrieve a set, not a pin.** Dense retrieval over the tag definitions returns
everything above a threshold — usually zero, sometimes one, occasionally a
handful. Retrieval never sees the figure, so the comparison that follows is a
real test rather than a selection effect. Where nothing clears the bar the
answer is "I cannot check this", which is true for most of the corpus and is
the honest thing to say.

**Compare like with like.** A sentence states either a level or a change, and
they are different quantities:

    "Net income was $17.4 billion"            a level. A tag holds it.
    "Net income increased by $1.6 billion"    a change. No tag holds it —
                                              it is this year minus last year.

The first version compared both against levels, so every change-shaped claim
came back as a mismatch that could never have matched. Those are not
discrepancies; they are the wrong subtraction. FY2011 alone has 225 of them out
of 896 claims, and each one was either a false alarm in the review queue or
written off as unverifiable.

The extractor has typed these DERIVED since Phase 1. This is the first thing to
use that label at comparison time.


THE THRESHOLD IS CALIBRATED, NOT CHOSEN

tau = 0.55 comes from a perturbation null: figures multiplied by a random
off-one factor, destroying real matches while preserving magnitudes. At 0.55 a
value match is about five times more likely to be real than coincidental. Lower
thresholds admit enough candidates that something lands by luck — at 0.30 the
null matches almost as often as the real data.


WHAT THIS CANNOT DO

The review bucket mixes real disagreements with legitimate scope differences —
segment against firmwide, a subtotal against a total. Separating those is human
judgement, and the tool says so rather than counting them as contradictions.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import numpy as np

import prepare_evidence as pe
from embed_tags import embed

HERE = Path(__file__).parent
TAU = 0.55          # null-calibrated: matches ~5x more likely real than coincidental
TOL = 0.015         # prose rounds to two or three significant figures

# A figure introduced by one of these is a change, not a level. Deliberately
# checked against the words immediately before the number rather than the whole
# sentence: "Net income was $17.4 billion, up $1.6 billion" contains both kinds,
# and only the second figure is a delta.
DELTA_LEAD = re.compile(
    r"\b(increased|decreased|declined|grew|rose|fell|up|down|"
    r"an?\s+(?:increase|decrease|decline|reduction|gain)\s+of|"
    r"change[sd]?\s+of|higher\s+by|lower\s+by)\s*(?:by\s*)?$", re.I)


def is_change(sentence: str, figure: str) -> bool:
    i = sentence.find(figure)
    return bool(DELTA_LEAD.search(sentence[max(0, i - 40):i])) if i > 0 else False


def annual_values(gaap: dict, tag: str, fy: int) -> float | None:
    usd = gaap.get(tag, {}).get("units", {}).get("USD")
    if not usd:
        return None
    v = pe.annual_value(usd, fy)
    return v["value"] if v else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=900)
    ap.add_argument("--ticker", default="JPM")
    a = ap.parse_args()
    t = a.ticker.upper()

    tags = json.loads((HERE / ".cache" / f"{t}-tagnames.json").read_text())
    vecs = np.load(HERE / ".cache" / f"{t}-tagvecs.npy")
    pos = {tag: i for i, tag in enumerate(tags)}
    gaap = pe.company_facts(t)["facts"]["us-gaap"]

    rows = [json.loads(l) for l in (HERE / "coverage.jsonl").open(encoding="utf-8")]
    pool = [r for r in rows
            if r["structural"] in ("reachable", "derivable")
            and pe.parse_claimed(r["figure"])][: a.limit]

    # tags with an annual value for each year, once
    byyear: dict[int, list[tuple[str, float]]] = {}
    for tag, p in gaap.items():
        usd = p.get("units", {}).get("USD")
        if not usd or tag not in pos:
            continue
        for fy in {int(e["end"][:4]) for e in usd if e.get("end")}:
            v = pe.annual_value(usd, fy)
            if v:
                byyear.setdefault(fy, []).append((tag, v["value"]))

    sv = embed([r["raw_sentence"][:2000] for r in pool])
    out, buckets = [], Counter()

    for i, r in enumerate(pool):
        fy = r["fiscal_year"]
        avail = byyear.get(fy, [])
        sims = vecs[[pos[tg] for tg, _ in avail]] @ sv[i] if avail else np.array([])
        cands = [(avail[k][0], avail[k][1], float(sims[k]))
                 for k in range(len(avail)) if sims[k] >= TAU]

        claimed = pe.parse_claimed(r["figure"])
        change = is_change(r["raw_sentence"], r["figure"])
        rec = {**r, "claimed": claimed, "is_change": change,
               "n_candidates": len(cands),
               "best_tag": max(cands, key=lambda c: c[2])[0] if cands else None,
               "best_cos": round(max(c[2] for c in cands), 3) if cands else None}

        if not cands:
            rec["bucket"] = "no_counterpart"
        else:
            hit = None
            for tag, level, cos in cands:
                if change:
                    # a change is this year minus last year, so fetch both and
                    # subtract rather than comparing a delta against a level
                    prev = annual_values(gaap, tag, fy - 1)
                    target = None if prev is None else level - prev
                else:
                    target = level
                if target is not None and target != 0 and \
                        abs((abs(target) - abs(claimed)) / abs(claimed)) <= TOL:
                    hit = (tag, target, cos)
                    break
            if hit:
                rec.update(bucket="verified", matched_tag=hit[0], filed=hit[1],
                           matched_cos=hit[2])
            else:
                rec["bucket"] = "review"
        buckets[rec["bucket"]] += 1
        out.append(rec)

    with (HERE / "join.jsonl").open("w", encoding="utf-8") as fh:
        for rec in out:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    n = len(out)
    ch = sum(1 for r in out if r["is_change"])
    print(f"\n  {n} claims  ({ch} of them changes, {n-ch} levels)\n")
    for b, label in (("verified", "figure matches a retrieved concept"),
                     ("review", "concept found, figure differs"),
                     ("no_counterpart", "nothing filed resembles it")):
        print(f"    {b:16s} {buckets[b]:>4}  ({buckets[b]/n:>3.0%})  {label}")
    v = [r for r in out if r["bucket"] == "verified"]
    print(f"\n    of the verified: {sum(1 for r in v if r['is_change'])} were changes, "
          f"{sum(1 for r in v if not r['is_change'])} levels")
    print(f"    wrote join.jsonl")


if __name__ == "__main__":
    main()
