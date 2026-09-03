"""Of the claims that can be checked, how many agree with what was filed. Stage 2.

    .venv/bin/python consistency.py

Reads coverage.jsonl, writes consistency.jsonl and pins_to_check.md.


THE RULE THIS FILE IS BUILT AROUND

A tag is pinned to a sentence **using the wording alone**. The values are never
consulted when deciding which tag a sentence refers to.

If a pin were accepted only when the numbers matched, every disagreement would
be defined out of existence and the consistency rate would be 100% by
construction — a number that looks like a finding and is an identity. That is
not hypothetical here: the auditor's own calibration shipped a feature that was
true by definition, reported it as its strongest predictor of failure, and an
outside review had to catch it.

So the order is fixed and one-way: choose the tag from the words, fetch the
value once, record the comparison, never revisit the pin. `pin()` below cannot
see a figure even if it wanted to — it is handed the sentence and the year, and
nothing else.


WHY A MARGIN RULE RATHER THAN A TOP-1

Taking the best-scoring candidate always produces a pin, and a pin that was a
coin-flip between two plausible tags produces a comparison that means nothing.
Requiring the winner to beat the runner-up by a clear margin trades coverage for
precision, which is the right trade when the output is a rate: a low rate
honestly reported is usable, and a high rate over bad pins is not.

Coverage is therefore reported alongside the rate, always. "Of the claims we
could pin, X% agreed" is a finding. X% on its own is not.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import prepare_evidence as pe

HERE = Path(__file__).parent
MARGIN = 1.35      # the winner must beat the runner-up by this much
TOLERANCE = 0.01   # prose rounds; 1% is the agent's own instruction


def pin(facts: dict, idf: dict, sentence: str, fiscal_year: int) -> dict | None:
    """The one tag this sentence is about, decided on words only.

    Deliberately takes no figure. The signature is the enforcement.
    """
    cands, _ = pe.candidates(facts, idf, sentence, fiscal_year, limit=3)
    if not cands:
        return None
    if len(cands) > 1 and cands[0]["score"] < MARGIN * cands[1]["score"]:
        return None          # two plausible tags: no pin, and no comparison
    return cands[0]


def main() -> None:
    rows = [json.loads(l) for l in (HERE / "coverage.jsonl").open(encoding="utf-8")]
    ticker = rows[0]["ticker"]
    facts, idf = pe.company_facts(ticker), pe.build_idf(pe.company_facts(ticker))

    checked = []
    for r in rows:
        if r["structural"] != "reachable" or not r["has_tag"]:
            continue
        claimed = pe.parse_claimed(r["figure"])
        if claimed is None or claimed == 0:
            continue

        tag = pin(facts, idf, r["raw_sentence"], r["fiscal_year"])   # words only
        if tag is None:
            continue

        gap = (tag["value"] - claimed) / claimed
        checked.append({**r,
                        "pinned_tag": tag["tag"], "pinned_label": tag["label"],
                        "filed": tag["value"], "claimed": claimed,
                        "gap": gap, "agrees": abs(gap) <= TOLERANCE,
                        "restated": tag["restated"]})

    with (HERE / "consistency.jsonl").open("w", encoding="utf-8") as fh:
        for c in checked:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    by_year = defaultdict(list)
    for c in checked:
        by_year[str(c["doc_fy"])].append(c)

    print(f"{'FY':>5} {'pinned':>7} {'agree':>6} {'rate':>6} {'coverage':>10}")
    print("  " + "-" * 52)
    per_year_total = Counter(str(r["doc_fy"]) for r in rows)
    for fy in sorted(by_year):
        g = by_year[fy]
        ok = sum(c["agrees"] for c in g)
        print(f"  {fy} {len(g):>7} {ok:>6} {ok/len(g):>5.0%}   {len(g)/per_year_total[fy]:>20.0%}")

    ok = sum(c["agrees"] for c in checked)
    print(f"\n  {len(checked):,} pinned of {len(rows):,} claims "
          f"({len(checked)/len(rows):.0%} coverage)")
    print(f"  {ok:,} agree within {TOLERANCE:.0%}  ({ok/len(checked):.0%})")
    print(f"  {sum(c['restated'] for c in checked)} involved a restated figure")

    # The twenty pins a person has to check, so the rate above has an error bar.
    sample = checked[:: max(1, len(checked) // 20)][:20]
    lines = ["# Twenty pins to check by hand", "",
             "The consistency rate above assumes these pins are right. Nobody has",
             "established that. Mark each ✓ or ✗ — the score is the precision of the",
             "pinning, and it is the slack that belongs on the headline number.", "",
             "Twenty minutes. It is the only human time this study needs.", ""]
    for i, c in enumerate(sample, 1):
        lines += [f"## {i}. {c['id']}  (FY{c['fiscal_year']})", "",
                  f"> {c['raw_sentence'][:260]}", "",
                  f"- figure in prose: **{c['figure']}**",
                  f"- pinned tag: `{c['pinned_tag']}` — {c['pinned_label']}",
                  f"- filed: ${c['filed']/1e9:,.2f}B  ·  gap {c['gap']*100:+,.1f}%",
                  "- is this the tag the sentence is about?  [ ] yes  [ ] no", ""]
    (HERE / "pins_to_check.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"  wrote consistency.jsonl and pins_to_check.md ({len(sample)} pins)")


if __name__ == "__main__":
    main()
