"""Learn when the agent should not answer. Phase 4 of the build plan.

    .venv/bin/python calibrate.py

Reads traces.jsonl and labels.jsonl. Writes calibration.json — the rule Phase 5
applies to every verdict before showing it to anyone.

Numpy and scipy. No agents. The differentiator is a measurement, not a model.


WHAT THIS IS ACTUALLY DOING

Phase 3 established that the agent is right about six times in ten. That number
on its own is useless to a person doing the work: they cannot tell which six, so
they must re-check all ten, and the tool has added labour rather than removed it.

This file looks for features available BEFORE anyone knows the answer — how many
lookups were spent, whether the first tag hit, whether the figure was restated,
whether the claim is a segment figure — and asks which of them predict being
wrong. Then it sets a threshold: answer where the predicted error rate is under
the tolerance, abstain everywhere else.

The output is one sentence with a number in it. That sentence is the project.


CONFORMAL, AND ITS ASSUMPTION STATED RATHER THAN BURIED

The threshold is set by split conformal prediction, which gives a distribution-
free bound: on data exchangeable with the calibration set, the error rate among
answered claims is at most alpha.

Exchangeability is a real assumption and financial filings violate it. Accounting
standards change and tags migrate with them — this corpus already contains two
tags abandoned at exactly such a changeover, in 2010 and 2021. A rule calibrated
on FY2025 filings from two banks is not entitled to a guarantee about FY2027
filings from twenty. The bound is reported with that limit attached, because a
guarantee whose assumption is unstated is a stronger claim than the evidence
supports.


THE BASELINE THAT KEEPS THIS HONEST

Thirty-seven of fifty labels are NOT_CHECKABLE. An abstainer could therefore hit
any target error rate by answering only the claims it was always going to get
right, and look excellent while adding nothing. So every result below is printed
against two references: answering everything, and answering nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
ALPHA = 0.03  # the error rate the product promises among auto-accepted verdicts


def load() -> list[dict]:
    labels = {r["id"]: r for r in map(json.loads, (HERE / "labels.jsonl").open(encoding="utf-8"))}
    rows = []
    for rec in map(json.loads, (HERE / "traces.jsonl").open(encoding="utf-8")):
        claim_id = rec["trace_id"].rsplit("-r", 1)[0]
        label = labels.get(claim_id)
        if not label:
            continue
        parsed = rec.get("parsed") or {}
        calls = rec.get("tool_calls") or []
        observations = json.dumps(rec.get("observations") or [])
        admissible = (rec.get("evidence") or {}).get("admissible", True)

        rows.append(
            {
                "id": claim_id,
                "said": parsed.get("VERDICT"),
                "label": label["label_verdict"],
                "correct": bool(admissible) and parsed.get("VERDICT") == label["label_verdict"],
                "provenance": label.get("label_provenance", "unknown"),
                # --- features, all knowable before the answer is judged ---
                "n_lookups": rec.get("n_tool_calls", 0),
                "no_evidence": not admissible,
                # A first tag that worked means the agent knew where to look.
                "first_tag_failed": bool(calls) and (
                    "tag_not_filed" in observations or "no_annual_data" in observations
                ),
                "restated": "restated" in observations,
                "is_segment": bool(label.get("section")),
                "is_percentage": label["figure"].strip().endswith("%"),
                "claim_type": label.get("label_type") or label["type"],
                "said_not_checkable": parsed.get("VERDICT") == "NOT_CHECKABLE",
            }
        )
    return rows


def rate(rows: list[dict]) -> float:
    return sum(r["correct"] for r in rows) / len(rows) if rows else 0.0


def feature_report(rows: list[dict], features: list[str]) -> list[tuple[str, float, int, int]]:
    """For each boolean feature: accuracy when true, when false, and the gap.

    A feature is useful to abstain on only if the two rates differ. Printing both
    sides rather than a single correlation keeps a feature that is merely rare
    from looking decisive.
    """
    out = []
    for name in features:
        yes = [r for r in rows if r[name]]
        no = [r for r in rows if not r[name]]
        if not yes or not no:
            continue
        out.append((name, rate(no) - rate(yes), len(yes), len(no)))
    return sorted(out, key=lambda t: -abs(t[1]))


def conformal_threshold(scores: np.ndarray, correct: np.ndarray, alpha: float) -> float | None:
    """Lowest score at which the empirical error among answered claims is <= alpha.

    Split conformal in its simplest useful form. The score is a predicted
    probability of being correct; the threshold is swept rather than derived in
    closed form because the score here is discrete — a handful of distinct values
    from a handful of binary features — and a quantile of ten distinct values is
    a quantile in name only.
    """
    for tau in sorted(set(scores.tolist()), reverse=True):
        answered = scores >= tau
        if answered.sum() == 0:
            continue
        if (1 - correct[answered].mean()) <= alpha:
            best = tau
        else:
            break
    return locals().get("best")


def split_evaluate(rows: list[dict], features: list[str], alpha: float,
                   trials: int = 400, seed: int = 20260902) -> dict:
    """Fit the rule on half the data and measure it on the other half, repeatedly.

    THE MISTAKE THIS REPLACES

    The first version chose the threshold on all fifty rows and then reported the
    error rate on those same fifty. It printed "auto-accepted 24 of 50 at 0.0%
    error", which is not a finding — it is the definition of the threshold that
    was chosen. Any selection rule scored on its own selection set looks perfect.

    Split conformal means what the name says: one half decides the threshold, the
    other half, never seen, measures it. Repeating over many random splits gives
    a spread rather than a single flattering draw, and with fifty rows the spread
    is the honest headline.
    """
    rng = np.random.default_rng(seed)
    index = np.arange(len(rows))
    answered_frac, errors, taus = [], [], []

    for _ in range(trials):
        rng.shuffle(index)
        half = len(rows) // 2
        fit = [rows[i] for i in index[:half]]
        test = [rows[i] for i in index[half:]]

        ranked = feature_report(fit, features)
        strong = [(n, g) for n, g, _, _ in ranked if abs(g) >= 0.15]
        if not strong:
            continue

        def score_of(row: dict) -> float:
            return 1.0 - sum(g for n, g in strong if row[n])

        fit_scores = np.array([score_of(r) for r in fit])
        fit_correct = np.array([r["correct"] for r in fit], dtype=bool)
        tau = conformal_threshold(fit_scores, fit_correct, alpha)
        if tau is None:
            answered_frac.append(0.0)
            continue

        test_scores = np.array([score_of(r) for r in test])
        test_correct = np.array([r["correct"] for r in test], dtype=bool)
        answered = test_scores >= tau
        answered_frac.append(float(answered.mean()))
        taus.append(tau)
        if answered.sum():
            errors.append(float(1 - test_correct[answered].mean()))

    return {
        "trials": trials,
        "median_answered_frac": float(np.median(answered_frac)) if answered_frac else 0.0,
        "median_error": float(np.median(errors)) if errors else None,
        "error_p90": float(np.percentile(errors, 90)) if errors else None,
        "trials_meeting_target": (
            sum(1 for e in errors if e <= alpha) / len(errors) if errors else 0.0
        ),
    }


def main() -> None:
    rows = load()
    print(f"{len(rows)} runs · target error among answered <= {ALPHA:.0%}\n")

    print("  REFERENCE POINTS")
    print(f"    answer everything      {len(rows):3d} answered, "
          f"{1 - rate(rows):.0%} error")
    print(f"    answer nothing           0 answered, 0% error, 0 use")

    # `said_not_checkable` is deliberately excluded. It is the agent's own
    # output, and NOT_CHECKABLE is 74% of the labels, so a rule that prefers it
    # is the trivial baseline with extra steps — it would auto-accept exactly the
    # answers that agree with the commonest label and call that calibration.
    # Features must describe the SITUATION, not the answer.
    features = [
        "no_evidence", "first_tag_failed", "restated", "is_segment",
        "is_percentage", "n_lookups_ge_3",
    ]
    for row in rows:
        row["n_lookups_ge_3"] = row["n_lookups"] >= 3
    print("\n  WHICH FEATURES PREDICT BEING WRONG")
    print(f"    {'feature':22s} {'when true':>10s} {'when false':>11s} {'gap':>7s}")
    ranked = feature_report(rows, features)
    for name, gap, n_yes, n_no in ranked:
        yes = [r for r in rows if r[name]]
        no = [r for r in rows if not r[name]]
        print(f"    {name:22s} {rate(yes):9.0%} {rate(no):10.0%} {gap:+7.0%}"
              f"   (n={n_yes}/{n_no})")

    # A score from the features that actually separate. Deliberately a simple
    # additive rule rather than a fitted model: fifty rows cannot support fitting
    # weights, and a rule a person can read is a rule a person can challenge.
    strong = [(name, gap) for name, gap, _, _ in ranked if abs(gap) >= 0.15]
    print("\n  RULE — features with a gap of 15 points or more:")
    if not strong:
        print("    none. No feature separates well enough to abstain on.")
    for name, gap in strong:
        print(f"    {'avoid' if gap > 0 else 'prefer'} {name}  ({gap:+.0%})")

    scores = np.array([
        1.0 - sum(gap for name, gap in strong if r[name]) for r in rows
    ])
    correct = np.array([r["correct"] for r in rows], dtype=bool)

    tau = conformal_threshold(scores, correct, ALPHA)
    print(f"\n  IN-SAMPLE (NOT A RESULT — threshold chosen on these same rows)")
    if tau is None:
        print("    No threshold reaches the target. Even the most confident subset")
        print("    exceeds the tolerated error rate, so nothing can be auto-accepted.")
        answered_n, err = 0, 0.0
    else:
        answered = scores >= tau
        answered_n = int(answered.sum())
        err = float(1 - correct[answered].mean())
        print(f"    score >= {tau:.2f}")
        print(f"    auto-accepted {answered_n} of {len(rows)} at {err:.1%} error")
        print(f"    {len(rows) - answered_n} routed to a human")

    held = split_evaluate(rows, features, ALPHA)
    print(f"\n  HELD-OUT — fit on 25 rows, measured on the other 25, "
          f"{held['trials']} random splits")
    if held["median_error"] is None:
        print("    no split produced an answerable subset.")
    else:
        print(f"    answers      {held['median_answered_frac']:.0%} of claims (median)")
        print(f"    error        {held['median_error']:.0%} median, "
              f"{held['error_p90']:.0%} at the 90th percentile")
        print(f"    hits the {ALPHA:.0%} target in "
              f"{held['trials_meeting_target']:.0%} of splits")

    # The operating curve. "We cannot hit 3%" is true and useless on its own;
    # what a person deciding whether to adopt this needs is the trade — how much
    # of the work the tool can take off their desk at each error rate they might
    # be willing to tolerate. A null result at one alpha is a data point on a
    # curve, not the end of the enquiry.
    print("\n  OPERATING CURVE — coverage against tolerated error, held out")
    print(f"    {'alpha':>6s} {'answers':>9s} {'actual err':>11s} {'meets target':>13s}")
    curve = []
    for a in (0.03, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30):
        h = split_evaluate(rows, features, a, trials=200)
        curve.append({"alpha": a, **h})
        if h["median_error"] is None:
            print(f"    {a:6.0%} {'—':>9s} {'—':>11s} {'—':>13s}")
        else:
            print(f"    {a:6.0%} {h['median_answered_frac']:9.0%} "
                  f"{h['median_error']:11.0%} {h['trials_meeting_target']:13.0%}")

    print("\n  IS THE ABSTAINER DOING ANYTHING?")
    print(f"    answer everything:  {1 - rate(rows):.0%} error on 100% of claims")
    if held["median_error"] is not None:
        print(f"    abstainer:          {held['median_error']:.0%} error on "
              f"{held['median_answered_frac']:.0%} of claims")

    payload = {
        "alpha": ALPHA,
        "features": [{"name": n, "gap": round(g, 4)} for n, g in strong],
        "threshold": tau,
        "n_runs": len(rows),
        "n_answered": answered_n,
        "error_among_answered_IN_SAMPLE": round(err, 4),
        "held_out": held,
        "operating_curve": curve,
        "caveat": (
            "Calibrated on 50 runs, 46 of whose labels were rule-drafted rather "
            "than established by hand. Conformal assumes exchangeability, which "
            "financial filings violate across accounting-standard changes. Not "
            "reportable until the labels are redone."
        ),
    }
    (HERE / "calibration.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("\n  wrote calibration.json")


if __name__ == "__main__":
    main()
