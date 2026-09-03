"""Score the agent against the labels. Phase 3 of the build plan.

    .venv/bin/python score.py

Reads traces.jsonl and labels.jsonl. Reports by claim type and by label
provenance, never as a single number.


WHY THERE IS NO HEADLINE ACCURACY FIGURE

Because one would be read, quoted, and wrong twice over.

Wrong the first time because 46 of the 50 labels were drafted by rules rather
than established by a person, and those rules read the same SEC data the agent
reads. Agreement between them is not evidence the agent is right. Every table
below therefore splits on `label_provenance`, and only the human rows support a
claim about accuracy.

Wrong the second time because the corpus is unbalanced. If most claims turn out
to be NOT_CHECKABLE, an agent that answered NOT_CHECKABLE to everything would
score well while being useless — so the trivial baseline is computed and printed
next to the real score. A number that cannot beat "always say the commonest
answer" is not a result.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
VERDICTS = ["SUPPORTED", "CONTRADICTED", "DEFINITION_MISMATCH", "NOT_CHECKABLE"]


def load() -> list[dict]:
    labels = {r["id"]: r for r in map(json.loads, (HERE / "labels.jsonl").open(encoding="utf-8"))}
    rows = []
    for record in map(json.loads, (HERE / "traces.jsonl").open(encoding="utf-8")):
        claim_id = record["trace_id"].rsplit("-r", 1)[0]
        label = labels.get(claim_id)
        if not label:
            continue
        rows.append(
            {
                "id": claim_id,
                "said": (record.get("parsed") or {}).get("VERDICT"),
                "label": label["label_verdict"],
                "stratum": label["stratum"],
                "type": label.get("label_type") or label["type"],
                "section": label.get("section"),
                "provenance": label.get("label_provenance", "unknown"),
                "rule": label.get("label_rule"),
                "n_lookups": record.get("n_tool_calls", 0),
                "duration_s": record.get("duration_s"),
                "error": record.get("error"),
                "admissible": (record.get("evidence") or {}).get("admissible", True),
            }
        )
    return rows


def agrees(row: dict) -> bool:
    """Agreement, but only when the run actually consulted evidence.

    A run that made no tool call is not scored as correct even when its verdict
    matches. Nine of the first fifty runs answered NOT_CHECKABLE without looking
    anything up, and because NOT_CHECKABLE is the most common label, all nine
    counted as hits. Crediting them measures how often the commonest answer is
    right, not how often the agent is.
    """
    return bool(row["admissible"]) and row["said"] == row["label"]


def table(rows: list[dict], key: str, title: str) -> None:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key))].append(row)
    print(f"\n  {title}")
    print(f"    {'group':32s} {'n':>4s} {'agree':>6s} {'rate':>7s}")
    for name, group in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        hits = sum(1 for r in group if agrees(r))
        print(f"    {name:32s} {len(group):4d} {hits:6d} {hits / len(group):6.0%}")


def main() -> None:
    rows = load()
    errors = [r for r in rows if r["error"] or not r["said"]]
    scored = [r for r in rows if r["said"]]

    inadmissible = [r for r in scored if not r["admissible"]]
    print(f"{len(rows)} runs scored · {len(errors)} produced no verdict · "
          f"{len(inadmissible)} answered with no evidence and are not credited")

    # The trivial baseline first, so every number after it is read against
    # something rather than against zero.
    commonest, count = Counter(r["label"] for r in scored).most_common(1)[0]
    print(f"\n  TRIVIAL BASELINE — always answer {commonest}: "
          f"{count}/{len(scored)} = {count / len(scored):.0%}")
    hits = sum(1 for r in scored if agrees(r))
    print(f"  AGENT                                  "
          f"{hits}/{len(scored)} = {hits / len(scored):.0%}")
    if hits <= count:
        print("  -> the agent does not beat answering the commonest label every time.")

    table(scored, "provenance", "BY LABEL PROVENANCE — only 'human' supports a claim about accuracy")
    table(scored, "stratum", "BY STRATUM")
    table(scored, "type", "BY CLAIM TYPE")

    print("\n  CONFUSION — rows are the label, columns what the agent said")
    header = "".join(f"{v[:10]:>12s}" for v in VERDICTS)
    print(f"    {'label \\\\ said':22s}{header}")
    for label in VERDICTS:
        group = [r for r in scored if r["label"] == label]
        if not group:
            continue
        cells = "".join(
            f"{sum(1 for r in group if r['said'] == said):12d}" for said in VERDICTS
        )
        print(f"    {label:22s}{cells}")

    print("\n  LOOKUPS SPENT vs AGREEMENT — a candidate calibration signal")
    for n in sorted({r["n_lookups"] for r in scored}):
        group = [r for r in scored if r["n_lookups"] == n]
        hits = sum(1 for r in group if agrees(r))
        print(f"    {n} lookup(s){'':18s} {len(group):4d} {hits:6d} {hits / len(group):6.0%}")

    human = [r for r in scored if r["provenance"] == "human"]
    if human:
        print(f"\n  THE {len(human)} HUMAN-LABELLED CLAIMS, one by one")
        for r in human:
            mark = ("✓" if agrees(r) else "✗") if r["admissible"] else "∅"
            print(f"    {mark} {r['id']:10s} said {str(r['said']):20s} label {r['label']:20s} "
                  f"{r['n_lookups']} lookups")

    print(
        "\n  Reminder: 46 of these labels were rule-drafted, not established by a "
        "person.\n  Nothing above is a reportable accuracy figure until they are "
        "relabelled by hand."
    )


if __name__ == "__main__":
    main()
