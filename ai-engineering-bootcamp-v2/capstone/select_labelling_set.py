"""Draw the 50 claims to label by hand. Phase 2a of CAPSTONE_BUILD_PLAN.md.

Reads claims.jsonl, writes to_label.jsonl. Deterministic: same seed, same draw.


WHY STRATIFIED RATHER THAN RANDOM

A random draw of 50 from the 964 candidates would follow the population, which
is roughly 30% STATED and 10% DERIVED. That gives about 5 derived claims — too
few to say anything about the harder half of the corpus, and the stated-versus-
derived gap is the finding Phase 3 exists to produce.

So each type gets a quota, and each drawn row carries the sampling weight that
undoes the quota afterwards:

    weight = (rows of this type in the corpus) / (rows of this type drawn)

A base rate computed as sum(weight * indicator) / sum(weight) is then an
estimate for the corpus, not for the sample. Both numbers come out of one
labelling session: precision by type, and an unbiased overall base rate.


WHY UNCHECKABLE CLAIMS ARE IN THE SAMPLE

Twelve of the fifty are types the tool cannot verify — segment figures, ratios,
management's own non-GAAP measures. They are not there to be got right. They are
there to test whether the agent says NOT_CHECKABLE or invents a verdict.

An auditor that quietly answers when it has no evidence is more dangerous than
one that is merely inaccurate, because nothing downstream can tell the two
apart. A sample containing only checkable claims cannot measure that at all, and
it is the failure mode the calibration layer most needs to see.


WHY ONE FIGURE PER SENTENCE

The extractor emits one candidate per figure, so a single sentence can yield
three rows. Drawing two of them would mean labelling the same sentence twice and
counting it as two independent observations — which inflates the effective
sample size and makes any error bar narrower than it has earned.
"""

import json
import random
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
CLAIMS = HERE / "claims.jsonl"
OUT = HERE / "to_label.jsonl"

SEED = 20260902  # the date of the draw; recorded so the sample is reproducible

# Quotas. STATED is the largest because it is the case the engine handles today
# and therefore the baseline everything else is compared against.
QUOTAS = {
    "STATED": 15,
    "DERIVED": 12,
    "BALANCE": 11,
    "UNCHECKABLE": 12,
}

# The uncheckable stratum is three extractor types pooled, because what is being
# measured is one behaviour — does the agent abstain — not three.
UNCHECKABLE_TYPES = {"SEGMENT", "RATIO", "NON_GAAP"}


def stratum_of(row: dict) -> str | None:
    if row["type"] in ("STATED", "DERIVED", "BALANCE"):
        return row["type"]
    if row["type"] in UNCHECKABLE_TYPES:
        return "UNCHECKABLE"
    return None


def main() -> None:
    rows = [json.loads(line) for line in CLAIMS.open(encoding="utf-8")]

    strata: dict[str, list[dict]] = {name: [] for name in QUOTAS}
    for row in rows:
        name = stratum_of(row)
        if name:
            strata[name].append(row)

    rng = random.Random(SEED)
    drawn: list[dict] = []

    for name, quota in QUOTAS.items():
        pool = strata[name]
        population = len(pool)

        # Shuffle first, then take the first row of each sentence encountered.
        # Selecting from a shuffled list rather than sampling and de-duplicating
        # afterwards keeps the draw uniform: dropping duplicates after the fact
        # would quietly favour sentences that carry only one figure.
        rng.shuffle(pool)
        seen_sentences: set[str] = set()
        picked: list[dict] = []
        for row in pool:
            if row["raw_sentence"] in seen_sentences:
                continue
            seen_sentences.add(row["raw_sentence"])
            picked.append(row)
            if len(picked) == quota:
                break

        weight = population / len(picked) if picked else 0.0
        for row in picked:
            row["stratum"] = name
            row["sampling_weight"] = round(weight, 3)
            row["stratum_population"] = population
            # Empty fields for the human to fill in. Written now so the file has
            # one shape whether or not it has been labelled yet.
            row["label_verdict"] = None
            row["label_true_figure"] = None
            row["label_xbrl_tag"] = None
            row["label_source_url"] = None
            row["label_note"] = None
        drawn.extend(picked)

    # Interleave the strata so a labelling session does not spend its first hour
    # on one type. Fatigue is real and it is not evenly distributed across a
    # sitting; a labeller who is sharpest at the start should not spend that on
    # whichever type happened to sort first.
    rng.shuffle(drawn)
    for index, row in enumerate(drawn, start=1):
        row["label_seq"] = index

    with OUT.open("w", encoding="utf-8") as fh:
        for row in drawn:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"{len(drawn)} claims to label -> {OUT.name}   (seed {SEED})\n")
    print(f"  {'stratum':14s} {'drawn':>5s} {'of':>5s} {'weight':>8s}")
    for name in QUOTAS:
        picked = [r for r in drawn if r["stratum"] == name]
        if picked:
            print(
                f"  {name:14s} {len(picked):5d} {picked[0]['stratum_population']:5d} "
                f"{picked[0]['sampling_weight']:8.2f}"
            )

    print()
    by_ticker = Counter(r["ticker"] for r in drawn)
    print("  by bank: " + ", ".join(f"{k} {v}" for k, v in sorted(by_ticker.items())))
    by_type = Counter(r["type"] for r in drawn)
    print("  by extractor type: " + ", ".join(f"{k} {v}" for k, v in sorted(by_type.items())))
    by_year = Counter(r["fiscal_year"] for r in drawn)
    print("  by fiscal year: " + ", ".join(f"{k} {v}" for k, v in sorted(by_year.items())))


if __name__ == "__main__":
    main()
