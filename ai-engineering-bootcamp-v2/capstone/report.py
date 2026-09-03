"""Turn many verdicts into one report about a filing. Phase 5 of the build plan.

    .venv/bin/python report.py GS

One claim in and one verdict out is a demo. A person auditing a bank does not
have a claim; they have a filing, and they want to know where in it to spend
their morning. That is the same engine in a loop, plus the abstention layer
deciding which rows they actually have to read.


THE ABSTENTION LAYER IS THE PRODUCT, NOT A GARNISH

Without it this is a list of verdicts nobody can act on: the agent is right about
six times in ten and does not say which six, so a reader has to check all of them
and the report has added work rather than removed it.

With it the report splits into two piles — one that can be taken at face value
and one that needs a person — and only the second pile costs anyone time.

At the moment the second pile is everything. calibrate.py found no subset
trustworthy at 3% on the current evidence, so this report shows every row as
needing review and says so on its face. That is the honest rendering of a
calibration that has not yet earned an auto-accept, and it is deliberately not
hidden behind an encouraging summary.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent

VERDICT_DISPLAY = {
    "SUPPORTED": ("VERIFIED", "✅"),
    "CONTRADICTED": ("CONTRADICTED", "❌"),
    "DEFINITION_MISMATCH": ("BASIS MISMATCH", "⚠️"),
    "NOT_CHECKABLE": ("NOT IN XBRL", "❔"),
}


def load_json(name: str, default):
    path = HERE / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def load_jsonl(name: str) -> list[dict]:
    path = HERE / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.open(encoding="utf-8")]


def auto_acceptable(row: dict, calibration: dict) -> tuple[bool, str]:
    """Whether this verdict can be shown without a human reading it.

    Returns the decision and the reason, because a reader who is told to check
    something is owed the reason — "the tool is unsure" is not review guidance,
    while "this is a segment figure and the tool is wrong on those three times in
    four" tells them what to look at.
    """
    if calibration.get("threshold") is None:
        return False, "no calibrated threshold reaches the target error rate"
    if row.get("no_evidence"):
        return False, "answered without consulting any filed data"
    for feature in calibration.get("features", []):
        if feature["gap"] > 0 and row.get(feature["name"]):
            return False, f"{feature['name']} — {feature['gap']:.0%} more errors when true"
    return True, "within the calibrated confidence band"


def build(ticker: str) -> dict:
    labels = {r["id"]: r for r in load_jsonl("labels.jsonl")}
    calibration = load_json("calibration.json", {})
    manifest = {m["ticker"]: m for m in load_json("data/manifest.json", [])}

    rows = []
    for record in load_jsonl("traces.jsonl"):
        claim_id = record["trace_id"].rsplit("-r", 1)[0]
        if not claim_id.startswith(ticker + "-"):
            continue
        label = labels.get(claim_id)
        if not label:
            continue
        parsed = record.get("parsed") or {}
        observations = json.dumps(record.get("observations") or [])
        row = {
            "id": claim_id,
            "verdict": parsed.get("VERDICT"),
            "claimed": parsed.get("CLAIMED"),
            "filed": parsed.get("FILED"),
            "tag": parsed.get("TAG"),
            "reasoning": parsed.get("REASONING"),
            "sentence": label["raw_sentence"],
            "figure": label["figure"],
            "section": label.get("section"),
            "fiscal_year": label["fiscal_year"],
            "source_url": label["source_url"],
            "n_lookups": record.get("n_tool_calls", 0),
            "no_evidence": not (record.get("evidence") or {}).get("admissible", True),
            "is_segment": bool(label.get("section")),
            "is_percentage": label["figure"].strip().endswith("%"),
            "first_tag_failed": "tag_not_filed" in observations or "no_annual_data" in observations,
            "restated": "restated" in observations,
            # Carried so the report can be checked against the answer key. Never
            # shown as the tool's own output — a deployed report has no labels.
            "label": label.get("label_verdict"),
            "label_provenance": label.get("label_provenance"),
        }
        row["auto"], row["why"] = auto_acceptable(row, calibration)
        rows.append(row)

    rows.sort(key=lambda r: (r["auto"], r["id"]))
    return {
        "ticker": ticker,
        "company": manifest.get(ticker, {}).get("company", ticker),
        "fiscal_year": manifest.get(ticker, {}).get("fiscal_year", "?"),
        "source_url": manifest.get(ticker, {}).get("source_url"),
        "rows": rows,
        "counts": Counter(r["verdict"] for r in rows),
        "n_auto": sum(1 for r in rows if r["auto"]),
        "calibration": calibration,
    }


def render(report: dict) -> str:
    out = []
    out.append(f"{report['company'].upper()} — FY{report['fiscal_year']} Form 10-K, Item 7")
    out.append(f"{len(report['rows'])} numeric claims checked against filed XBRL")
    out.append("")
    counts = report["counts"]
    pairs = [
        ("SUPPORTED", "DEFINITION_MISMATCH"),
        ("CONTRADICTED", "NOT_CHECKABLE"),
    ]
    for left, right in pairs:
        left_label, left_icon = VERDICT_DISPLAY[left]
        right_label, right_icon = VERDICT_DISPLAY[right]
        out.append(
            f"  {left_icon} {left_label:<16s}{counts.get(left, 0):>3d}"
            f"      {right_icon} {right_label:<16s}{counts.get(right, 0):>3d}"
        )
    out.append("")

    alpha = report["calibration"].get("alpha")
    if report["n_auto"]:
        out.append(
            f"  Auto-accepted {report['n_auto']} of {len(report['rows'])} at "
            f"<={alpha:.0%} error. {len(report['rows']) - report['n_auto']} need a human."
        )
    else:
        out.append(
            f"  Auto-accepted 0 of {len(report['rows'])}. No subset of these verdicts is"
        )
        out.append(
            f"  trustworthy at <={alpha:.0%} error on current evidence, so every row"
        )
        out.append("  below needs a human. See calibration.json for why.")
    out.append("  Every flag links to the sentence and the SEC source URL.")
    return "\n".join(out)


def main() -> None:
    ticker = (sys.argv[1] if len(sys.argv) > 1 else "GS").upper()
    report = build(ticker)
    if not report["rows"]:
        print(f"No runs found for {ticker}. Run run_claims.py first.")
        return

    print(render(report))
    print("\n  " + "-" * 68)
    for row in report["rows"]:
        label, icon = VERDICT_DISPLAY.get(row["verdict"], ("UNKNOWN", "•"))
        flag = "review" if not row["auto"] else "auto"
        print(f"\n  {icon} {row['id']:10s} {label:16s} [{flag}]  {row['figure']}")
        print(f"     {row['sentence'][:96]}")
        if not row["auto"]:
            print(f"     why reviewed: {row['why']}")


if __name__ == "__main__":
    main()
