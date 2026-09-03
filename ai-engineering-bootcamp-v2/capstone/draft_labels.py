"""Draft the remaining labels from the conventions established by hand.

    .venv/bin/python draft_labels.py

READ THIS BEFORE QUOTING ANY NUMBER THAT DEPENDS ON THESE LABELS

These are NOT ground truth. Ground truth for this project means a verdict a
person established from data.sec.gov before the agent ran, and only four rows
meet that description — GS-0279, GS-0055, JPM-0028 and JPM-0114, labelled by
hand on 2026-09-02. Every other row here was produced by the rules below and is
stamped `label_provenance: "rule_drafted"`.

The distinction is not bookkeeping. The agent under evaluation reads the same
SEC data these rules read, so the two will agree for reasons that have nothing
to do with the agent being correct. Any accuracy figure computed against these
rows measures how similar two procedures are, not whether either is right.

What they are good for is making Phases 3, 4 and 5 run end to end on realistic
data, so the machinery can be built and debugged now and the labels replaced
later. That is a normal way to build a pipeline. It is not a normal way to
report a result.

Before Demo Day, relabel by hand — the whole fifty if there is time, and at
minimum a stratified twenty — and rerun the scoring. Report only those numbers.


THE RULES, AND WHERE EACH CAME FROM

Every rule below is a convention settled while labelling the first four claims,
written out so that the reason for each verdict is inspectable rather than
buried in a judgement nobody can reconstruct. Each drafted row records which
rule fired in `label_rule`.

  R1  A percentage with no obtainable base figure -> NOT_CHECKABLE
      From GS-0055. No company files a percentage; verifying one needs the
      figure for two years, and if either is missing so is the arithmetic.

  R2  Segment claim with a firmwide figure filed -> DEFINITION_MISMATCH
      From GS-0279 and JPM-0114. Segment results are an audited disclosure, so
      the claimed figure is corroborated; the reachable tag measures a different
      scope. This is the convention the labeller chose over NOT_CHECKABLE, and
      it is the single most consequential rule here.

  R3  Nothing filed matches the sentence at all -> NOT_CHECKABLE
      From GS-0055 again. No us-gaap concept covers the line item.

  R4  A candidate within 1%, firmwide, not a segment -> SUPPORTED
      The rounding tolerance the agent's own instruction uses.

  R5  Third-level narrative breakdown -> NOT_CHECKABLE
      From JPM-0028. A figure that exists only as management explaining a
      component of a component is not corroborated by anything audited.

  R6  Anything else -> NOT_CHECKABLE, flagged low confidence
      Deliberately the residual. NOT_CHECKABLE is the conservative answer, and
      a rule set that guessed here would manufacture confidence it has not
      earned. Rows on R6 are the first ones to relabel by hand.
"""

import json
from pathlib import Path

HERE = Path(__file__).parent
TO_LABEL = HERE / "to_label.jsonl"
LABELS = HERE / "labels.jsonl"

# Phrases that mark a figure as a component of a component — the JPM-0028 shape.
NESTED = ("which included", "consisted of", "of which", "including $", "primarily")


def draft(row: dict) -> tuple[str, str, str, bool]:
    """Returns (verdict, rule, note, confident)."""
    evidence = row.get("evidence") or {}
    cands = evidence.get("candidates") or []
    claimed = evidence.get("claimed_usd")
    near = evidence.get("near_match") or []
    section = row.get("section")
    sentence = row["raw_sentence"].lower()

    if claimed is None:
        return (
            "NOT_CHECKABLE",
            "R1",
            "Percentage. No company files a percentage; verifying it needs the "
            "figure for both years and the arithmetic by hand.",
            True,
        )

    if not cands:
        return (
            "NOT_CHECKABLE",
            "R3",
            "No us-gaap tag with annual data for this year matches the sentence. "
            "Likely a company-specific line item.",
            True,
        )

    if near and not section:
        tag = near[0]
        value = next(c["value"] for c in cands if c["tag"] == tag)
        return (
            "SUPPORTED",
            "R4",
            f"Firmwide claim. {tag} = ${value / 1e9:,.2f}B, within 1% of the "
            "claimed figure.",
            True,
        )

    if section:
        best = cands[0]
        return (
            "DEFINITION_MISMATCH",
            "R2",
            f"{section} segment figure. Nearest reachable tag {best['tag']} = "
            f"${best['value'] / 1e9:,.2f}B is firmwide — right concept, wrong "
            "scope. Segment results are an audited disclosure, so the claimed "
            "figure is corroborated. Consistent with GS-0279.",
            True,
        )

    if any(phrase in sentence for phrase in NESTED):
        return (
            "NOT_CHECKABLE",
            "R5",
            "Narrative breakdown of a component. The figure exists only as "
            "management explaining part of a larger number; nothing audited "
            "corroborates that slice.",
            True,
        )

    best = cands[0]
    gap = (best["value"] - claimed) / claimed * 100
    return (
        "NOT_CHECKABLE",
        "R6",
        f"No rule fits cleanly. Closest candidate {best['tag']} = "
        f"${best['value'] / 1e9:,.2f}B, {gap:+,.1f}% from the claim. "
        "RELABEL THIS ONE BY HAND FIRST.",
        False,
    )


def main() -> None:
    rows = [json.loads(line) for line in TO_LABEL.open(encoding="utf-8")]
    existing = {}
    if LABELS.exists():
        existing = {r["id"]: r for r in map(json.loads, LABELS.open(encoding="utf-8"))}

    # Anything already on disk was labelled by a person. It is never overwritten,
    # and it is stamped so the two kinds never blur together in the results.
    for row in existing.values():
        row.setdefault("label_provenance", "human")
        row.setdefault("label_rule", None)

    drafted = 0
    for row in rows:
        if row["id"] in existing:
            continue
        verdict, rule, note, confident = draft(row)
        out = dict(row)
        out["label_verdict"] = verdict
        out["label_type"] = "SEGMENT" if row.get("section") else row["type"]
        out["label_true_figure"] = None
        out["label_xbrl_tag"] = (
            (out.get("evidence", {}).get("candidates") or [{}])[0].get("tag")
        )
        out["label_note"] = note
        out["label_provenance"] = "rule_drafted"
        out["label_rule"] = rule
        out["label_confident"] = confident
        existing[row["id"]] = out
        drafted += 1

    with LABELS.open("w", encoding="utf-8") as fh:
        for key in sorted(existing):
            fh.write(json.dumps(existing[key], ensure_ascii=False) + "\n")

    from collections import Counter
    verdicts = Counter(r["label_verdict"] for r in existing.values())
    rules = Counter(r.get("label_rule") for r in existing.values())
    prov = Counter(r.get("label_provenance") for r in existing.values())

    print(f"{len(existing)} labels ({drafted} newly drafted) -> labels.jsonl\n")
    print("  provenance:")
    for k, v in sorted(prov.items()):
        print(f"    {str(k):14s} {v:3d}")
    print("\n  verdicts:")
    for k, v in verdicts.most_common():
        print(f"    {k:22s} {v:3d}")
    print("\n  rule fired:")
    for k, v in sorted(rules.items(), key=lambda kv: (kv[0] is None, kv[0])):
        print(f"    {str(k):14s} {v:3d}")
    weak = [r["id"] for r in existing.values() if r.get("label_confident") is False]
    print(f"\n  relabel these by hand first ({len(weak)}): {', '.join(weak[:12])}")


if __name__ == "__main__":
    main()
