"""Every pinned claim, grouped by the concept it was pinned to, ready to browse.

    python3 browse.py

Reads consistency.jsonl, writes one browse_<TICKER>.json per filer plus a
browse_index.json. No network, no model — this is a regrouping of stage 2, not a
new measurement.


WHY THIS READS consistency.jsonl AND NOT join.jsonl

Both files pair a sentence with a filed figure and both look like they would
answer "does the prose match the data". Only one of them can.

join.py chooses the tag *because* its value matches the claim — it walks the
candidate list and stops at the first one inside tolerance. That is the right
design for its own question (can a retrieved shortlist reach a known answer) and
the wrong input for this one: a claim whose number disagrees with its true
concept never becomes a match there, it falls into `review` next to every
retrieval miss, and the two are indistinguishable afterwards.

consistency.py pins on the wording alone and is not allowed to see a figure. So
a gap in this file is a measurement. A gap in join.jsonl is a leftover.


THE THREE VERDICTS, AND THE ONE THAT IS DELIBERATELY MISSING

    agrees        |gap| <= 1.5%    prose rounds to two or three significant
                                   figures, so this is as close as agreement
                                   can be observed
    basis         1.5% < |gap| <= 10%   both the concept and the number are
                                   real and they are a few percent apart —
                                   almost always a scope difference
    incomparable  |gap| > 10%      the pin is not near enough to compare

There is no "contradicted". It was asked for and the data will not carry it.
Every large gap inspected by hand turned out to be one of three things and none
of them was a bank misreporting:

  a wrong pin      "net charge-offs were $12.2 billion" pinned to
                   OtherIntangibleAssetsNet ($4.04B). That concept collected 24
                   claims and agreed with none of them.

  a near-miss pin  "$2.7 billion of authorized repurchase capacity REMAINED"
                   pinned to StockRepurchaseProgramAuthorizedAmount1 ($6.40B).
                   The right tag is the ...RemainingAuthorizedRepurchaseAmount1
                   one. Same family, wrong member — the failure mode the model
                   grid on tab 1 is about, reproduced by our own retrieval.

  an extraction    a cross-reference line ("For further discussion please see
  artefact         ... pages 79-80. 2014 compared with 2013 ...") that the
                   splitter merged with the paragraph after it, so figures from
                   an unrelated passage arrived attached to the wrong sentence.

Rendering those as a red cross would publish our own bugs as JPMorgan's. What
would earn one: a pin confirmed by hand, on a concept with a track record, with
a gap too large to be a rounding or scope difference. There are currently none,
and the honest thing is to say so rather than to manufacture the category.


THE PICKER CARRIES THE PIN'S RECORD, WHICH IS THE POINT

A concept where 40 claims produced 0 plausible comparisons is not 40
disagreements — it is one bad pin applied 40 times, and a reader who clicks the
first one finds the tool wrong rather than the filer. So each concept ships with
how often its pin lands, and the tiers are:

    works    >= 3 plausible claims and >= 20% of its claims plausible
    partial  at least one plausible claim
    broken   never once landed within 10%

For JPMorgan that split 3 / 11 / 40, which is the shape to expect: most concepts
a wording-only pin proposes are ones it should not have. The counts are written
per filer into browse_index.json rather than quoted here, because a number in a
docstring is a number nobody re-runs.

"Plausible" is |gap| <= 10%: close enough that the pin is probably on the right
concept. It is not a claim about the filer, only about our retrieval.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent

AGREE = 0.015      # prose rounds; below this, agreement is unobservable
COMPARABLE = 0.10  # above this the pin is too far away to be compared at all


def verdict(gap: float | None) -> str:
    if gap is None:
        return "incomparable"
    g = abs(gap)
    return "agrees" if g <= AGREE else ("basis" if g <= COMPARABLE else "incomparable")


def tier(rows: list[dict]) -> str:
    plausible = sum(1 for r in rows if verdict(r["gap"]) != "incomparable")
    if plausible >= 3 and plausible / len(rows) >= 0.20:
        return "works"
    return "partial" if plausible else "broken"


def systematic(rows: list[dict]) -> dict | None:
    """Does this concept's prose sit consistently to one side of its filed value?

    A gap that repeats with the same sign year after year is the signature of a
    scope difference — the sentence means something slightly wider or narrower
    than the tag. A gap that flips sign is noise. Three is the fewest that can
    tell those apart, and unanimity is required because a 4-1 split at n=5 is
    not evidence of anything.
    """
    band = [r for r in rows if verdict(r["gap"]) == "basis"]
    if len(band) < 3:
        return None
    pos = sum(1 for r in band if r["gap"] > 0)
    if pos not in (0, len(band)):
        return None
    return {"n": len(band),
            "median": round(statistics.median(r["gap"] for r in band), 4),
            "direction": "above" if pos else "below"}


NAMES = {"JPM": "JPMorgan", "BAC": "Bank of America", "MS": "Morgan Stanley",
         "WFC": "Wells Fargo", "C": "Citigroup", "GS": "Goldman Sachs"}


def build(rows: list[dict]) -> dict:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["pinned_tag"]].append(r)

    concepts, claims = [], {}
    for tag, rs in by.items():
        counts = defaultdict(int)
        for r in rs:
            counts[verdict(r["gap"])] += 1
        concepts.append({
            "tag": tag,
            "label": rs[0]["pinned_label"],
            "n": len(rs),
            "agrees": counts["agrees"],
            "basis": counts["basis"],
            "incomparable": counts["incomparable"],
            "plausible": counts["agrees"] + counts["basis"],
            "tier": tier(rs),
            "systematic": systematic(rs),
        })
        claims[tag] = [{
            "id": r["id"], "doc_fy": r["doc_fy"], "fy": r["fiscal_year"],
            "figure": r["figure"], "claimed": r["claimed"], "filed": r["filed"],
            "gap": r["gap"], "verdict": verdict(r["gap"]),
            "restated": r["restated"], "section": r["section"],
            "sentence": r["raw_sentence"][:600],
        } for r in sorted(rs, key=lambda r: (abs(r["gap"]) if r["gap"] is not None else 9e9,
                                             r["fiscal_year"]))]

    # Works first, then partial, then broken; within a tier, most landings first.
    rank = {"works": 0, "partial": 1, "broken": 2}
    concepts.sort(key=lambda c: (rank[c["tier"]], -c["plausible"], -c["n"]))

    return {"agree_tolerance": AGREE, "comparable_tolerance": COMPARABLE,
            "concepts": concepts, "claims": claims}


def main() -> int:
    rows = [json.loads(l) for l in (HERE / "consistency.jsonl").open(encoding="utf-8")]
    # One file per filer rather than one keyed by filer. The app loads exactly
    # the bank a visitor picked; a single 1.5 MB blob would be parsed in full to
    # render one of five.
    index = []
    for ticker in sorted({r["ticker"] for r in rows}):
        mine = [r for r in rows if r["ticker"] == ticker]
        out = build(mine)
        path = HERE / f"browse_{ticker}.json"
        path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

        cs = out["concepts"]
        tot = {k: sum(c[k] for c in cs) for k in ("n", "agrees", "basis", "incomparable")}
        tiers = Counter(c["tier"] for c in cs)
        index.append({"ticker": ticker, "name": NAMES.get(ticker, ticker),
                      "claims": tot["n"], "concepts": len(cs),
                      "agrees": tot["agrees"], "basis": tot["basis"],
                      "incomparable": tot["incomparable"],
                      "works": tiers["works"], "partial": tiers["partial"],
                      "broken": tiers["broken"]})
        print(f"  {ticker:4} {tot['n']:>5} claims  {len(cs):>3} concepts   "
              f"agree {tot['agrees']:>3}  basis {tot['basis']:>3}  "
              f"far {tot['incomparable']:>4}   "
              f"tiers {tiers['works']}/{tiers['partial']}/{tiers['broken']}   "
              f"-> {path.name} ({path.stat().st_size / 1024:,.0f} KB)")

    (HERE / "browse_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"  wrote browse_index.json ({len(index)} filers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
