"""
The evaluation dashboard: what the checks say, across repeated runs.

    .venv/bin/streamlit run evals_app.py

This is the page the assignment asks for — a UI that triggers and displays eval
results — and it is built around the week's actual finding rather than around a
before-and-after bar chart.

WHY A DOT PLOT AND NOT BARS

The obvious chart is two bars, baseline and fixed, captioned "80% -> 90%". It
would be the wrong chart, because it would hide the only thing worth knowing.

Running the unchanged baseline three times scores 15, 16 and 17 out of 20. The
agent is not deterministic, and its own spread is 2 runs wide. A bar of means
draws that as a single confident height and implies a precision the measurement
does not have — the reader cannot see that the two ranges overlap almost
entirely.

So every individual run is drawn as its own dot, with the mean marked. The
overlap is then visible rather than asserted, and a reader can disagree with the
conclusion by looking at the same picture.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import streamlit as st

from checks import CHECKS, grade_all, summarise
from trace_log import load_records

st.set_page_config(page_title="Evals — SEC Claim Auditor", page_icon="📊", layout="wide")


# --- where the runs live ----------------------------------------------------

# Files recorded before instruction_version existed as a field. Named here so
# the mapping is explicit rather than guessed from a filename at read time.
LEGACY = {
    "traces.jsonl": ("baseline", "baseline-rep1"),
    "traces-after-fix-3.jsonl": ("fixed", "fixed-rep1"),
}

# The two abandoned drafts of the rewrite. Kept on disk and shown separately,
# never mixed into the comparison: they are different instruction texts, and
# folding them in would compare three things while claiming to compare two.
DRAFTS = {
    "traces-after-fix.jsonl": "draft 1 — rules added, weakly worded",
    "traces-after-fix-2.jsonl": "draft 2 — rules moved into the verdict definitions",
}

HERE = Path(__file__).parent


@st.cache_data(show_spinner=False)
def load_batches() -> dict[str, list[tuple[str, list[dict]]]]:
    """Every recorded batch, grouped by which instruction text produced it."""
    batches: dict[str, list[tuple[str, list[dict]]]] = {"baseline": [], "fixed": []}

    for filename, (version, label) in LEGACY.items():
        path = HERE / filename
        if path.exists():
            batches[version].append((label, load_records(path)))

    reps = HERE / "traces-reps.jsonl"
    if reps.exists():
        grouped: dict[str, list[dict]] = {}
        for record in load_records(reps):
            grouped.setdefault(record["run_label"], []).append(record)
        for label in sorted(grouped):
            version = record_version(grouped[label], label)
            batches.setdefault(version, []).append((label, grouped[label]))

    for version in batches:
        batches[version].sort(key=lambda pair: pair[0])
    return batches


def record_version(records: list[dict], label: str) -> str:
    """Which instruction produced this batch — from the record, or the label."""
    stamped = {r.get("instruction_version") for r in records if r.get("instruction_version")}
    if len(stamped) == 1:
        return stamped.pop()
    return "baseline" if label.startswith("baseline") else "fixed"


def score(records: list[dict]) -> int:
    return summarise(grade_all(records))["passed_all"]


def verdict_score(records: list[dict]) -> int:
    return sum(1 for r in records if r["parsed"]["VERDICT"] == r["expected_verdict"])


# --- the chart --------------------------------------------------------------

# Categorical slots 1 and 2 from the reference palette, validated for both
# modes: all six checks pass, worst adjacent CVD ΔE 24.7 light / 26.8 dark.
STYLE = """
<style>
.viz-root {
  --surface-1: #fcfcfb;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --grid: #e6e5e1;
  --series-1: #2a78d6;
  --series-2: #eb6834;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root {
    --surface-1: #1a1a19;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --grid: #34332f;
    --series-1: #3987e5;
    --series-2: #d95926;
  }
}
:root[data-theme="dark"] .viz-root {
  --surface-1: #1a1a19;
  --text-primary: #ffffff;
  --text-secondary: #c3c2b7;
  --grid: #34332f;
  --series-1: #3987e5;
  --series-2: #d95926;
}
.viz-root text { fill: var(--text-secondary); font-size: 13px; }
.viz-root text.row { fill: var(--text-primary); font-size: 14px; font-weight: 600; }
.viz-root text.tick { font-size: 12px; font-variant-numeric: tabular-nums; }
.viz-root text.mean { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
</style>
"""

X_MIN, X_MAX = 12, 20
PLOT_LEFT, PLOT_RIGHT = 150, 720
ROW_Y = {"baseline": 78, "fixed": 148}
SERIES = {"baseline": "var(--series-1)", "fixed": "var(--series-2)"}


def x_for(value: float) -> float:
    span = (value - X_MIN) / (X_MAX - X_MIN)
    return PLOT_LEFT + span * (PLOT_RIGHT - PLOT_LEFT)


def dot_plot(series: dict[str, list[int]], title: str, subtitle: str) -> str:
    """One dot per run, mean marked. Individual runs, never a bar of means."""
    parts = [
        f'<div class="viz-root"><svg viewBox="0 0 760 235" width="100%" '
        f'role="img" aria-label="{html.escape(title)}">',
        f'<text x="0" y="18" class="row">{html.escape(title)}</text>',
        f'<text x="0" y="38">{html.escape(subtitle)}</text>',
    ]

    # Recessive solid hairline grid, one shade off the surface. Never dashed.
    for tick in range(X_MIN, X_MAX + 1):
        x = x_for(tick)
        parts.append(
            f'<line x1="{x:.1f}" y1="58" x2="{x:.1f}" y2="185" '
            f'stroke="var(--grid)" stroke-width="1"/>'
        )
        parts.append(f'<text x="{x:.1f}" y="205" text-anchor="middle" class="tick">{tick}</text>')

    parts.append(
        '<text x="{:.0f}" y="226" text-anchor="middle">'
        "runs passing all three checks, out of 20 · axis starts at 12"
        "</text>".format((PLOT_LEFT + PLOT_RIGHT) / 2)
    )

    for name, scores in series.items():
        y = ROW_Y[name]
        colour = SERIES[name]
        parts.append(f'<text x="0" y="{y + 5}" class="row">{html.escape(name)}</text>')

        lo, hi = min(scores), max(scores)
        parts.append(
            f'<line x1="{x_for(lo):.1f}" y1="{y}" x2="{x_for(hi):.1f}" y2="{y}" '
            f'stroke="{colour}" stroke-width="2" opacity="0.35"/>'
        )

        # The mean rule goes down before the dots, so a run sitting exactly on
        # the mean is still a countable dot rather than a line through a
        # semicircle. Three runs have to look like three runs.
        mean = sum(scores) / len(scores)
        mx = x_for(mean)
        parts.append(
            f'<line x1="{mx:.1f}" y1="{y - 22}" x2="{mx:.1f}" y2="{y + 22}" '
            f'stroke="{colour}" stroke-width="2"/>'
        )

        # Duplicate scores are nudged apart rather than drawn on top of each
        # other, and every dot carries a 2px surface ring so overlaps stay
        # countable.
        seen: dict[int, int] = {}
        for value in scores:
            offset = seen.get(value, 0)
            seen[value] = offset + 1
            dy = 0 if offset == 0 else (8 if offset % 2 else -8) * ((offset + 1) // 2)
            parts.append(
                f'<circle cx="{x_for(value):.1f}" cy="{y + dy}" r="5.5" fill="{colour}" '
                f'stroke="var(--surface-1)" stroke-width="2"/>'
            )

        # Selective direct labels: the mean only, never a number on every dot.
        parts.append(
            f'<text x="{mx:.1f}" y="{y - 28}" text-anchor="middle" class="mean">'
            f"mean {mean:.1f}</text>"
        )

    parts.append("</svg></div>")
    return STYLE + "".join(parts)


BAR_ROW = 46
BAR_LEFT = 190


def grouped_bars(rows: list[dict], title: str, subtitle: str) -> str:
    """TPR and TNR per judge configuration, on one shared 0-100% axis.

    Two measures on one chart only because they share a scale and a meaning —
    both are "percentage of a labelled class handled correctly". A second axis
    would be the usual way to get this wrong.

    Bars rather than dots here, unlike the run-score chart above, because these
    are single computed rates rather than repeated observations: there is no
    spread to hide. A bar is honest about a proportion and dishonest about a
    distribution.
    """
    height = 74 + len(rows) * BAR_ROW + 44
    plot_right = 700
    parts = [
        f'<div class="viz-root"><svg viewBox="0 0 760 {height}" width="100%" '
        f'role="img" aria-label="{html.escape(title)}">',
        f'<text x="0" y="18" class="row">{html.escape(title)}</text>',
        f'<text x="0" y="38">{html.escape(subtitle)}</text>',
        # Legend: two series, so one is always present.
        f'<rect x="0" y="52" width="10" height="10" rx="2" fill="var(--series-1)"/>',
        f'<text x="16" y="61">TPR</text>',
        f'<rect x="56" y="52" width="10" height="10" rx="2" fill="var(--series-2)"/>',
        f'<text x="72" y="61">TNR</text>',
    ]

    def bar_x(fraction: float) -> float:
        return BAR_LEFT + fraction * (plot_right - BAR_LEFT)

    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        x = bar_x(tick)
        parts.append(
            f'<line x1="{x:.1f}" y1="74" x2="{x:.1f}" y2="{74 + len(rows) * BAR_ROW}" '
            f'stroke="var(--grid)" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{74 + len(rows) * BAR_ROW + 18}" '
            f'text-anchor="middle" class="tick">{tick:.0%}</text>'
        )

    for index, row in enumerate(rows):
        top = 74 + index * BAR_ROW
        label = f"{row['model']} · {row['version']}"
        parts.append(f'<text x="0" y="{top + 26}" class="row">{html.escape(label)}</text>')

        # Two thin bars with a 2px surface gap between them, anchored at zero,
        # rounded only at the data end.
        for offset, (value, colour) in enumerate(
            ((row["tpr"], "var(--series-1)"), (row["tnr"], "var(--series-2)"))
        ):
            y = top + 8 + offset * 15
            width = max(bar_x(value) - BAR_LEFT, 0)
            parts.append(
                f'<rect x="{BAR_LEFT}" y="{y}" width="{width:.1f}" height="13" '
                f'rx="4" fill="{colour}"/>'
            )
            # Direct label outside the bar end, so a short bar never clips it.
            parts.append(
                f'<text x="{bar_x(value) + 8:.1f}" y="{y + 11}" class="tick">'
                f"{value:.0%}</text>"
            )

    parts.append("</svg></div>")
    return STYLE + "".join(parts)


@st.cache_data(show_spinner=False)
def judge_results() -> list[dict]:
    """Rates per (model, prompt version), read from the cached judgements."""
    from grounding import load_labels, rates

    cache_path = HERE / "judgements_grounding.jsonl"
    labels = load_labels()
    if not cache_path.exists() or not labels:
        return []

    verdicts: dict[tuple[str, str, str], str] = {}
    for line in cache_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            verdicts[(entry.get("model", ""), entry["version"], entry["key"])] = entry["verdict"]

    combos = sorted({(model, version) for model, version, _ in verdicts})
    rows = []
    for model, version in combos:
        pairs = [
            (labels[k]["label"], verdicts[(model, version, k)])
            for k in labels
            if (model, version, k) in verdicts
        ]
        if not pairs:
            continue
        rows.append({"model": model, "version": version, **rates(pairs)})

    # Worst first, so the chart reads as a progression: the small model with the
    # obvious prompt, then each thing that helped. Ordering rows by value is
    # fine; what must never follow rank is the colour, and here TPR and TNR keep
    # their hues whatever order the rows land in.
    rows.sort(key=lambda r: (r["tpr"], r["tnr"]))
    return rows


# --- page -------------------------------------------------------------------

batches = load_batches()

st.title("📊 SEC Claim Auditor — evaluation")
st.caption(
    "Twenty hand-built claims with SEC figures established by hand, three code "
    "checks, and every instruction version run three times. Details of the "
    "claims in claims.py, of the failures in taxonomy.md, of the checks in "
    "checks.py."
)

if not batches["baseline"] or not batches["fixed"]:
    st.error("No recorded runs found. Run `.venv/bin/python run_batch.py` first.")
    st.stop()

base_scores = [score(records) for _, records in batches["baseline"]]
fixed_scores = [score(records) for _, records in batches["fixed"]]
base_verdicts = [verdict_score(records) for _, records in batches["baseline"]]
fixed_verdicts = [verdict_score(records) for _, records in batches["fixed"]]

overlap = max(base_scores) >= min(fixed_scores)

top = st.columns(4)
top[0].metric("Baseline", f"{sum(base_scores) / len(base_scores):.1f} / 20",
              f"range {min(base_scores)}–{max(base_scores)}", delta_color="off")
top[1].metric("After the fix", f"{sum(fixed_scores) / len(fixed_scores):.1f} / 20",
              f"range {min(fixed_scores)}–{max(fixed_scores)}", delta_color="off")
top[2].metric("Difference in means",
              f"{sum(fixed_scores) / len(fixed_scores) - sum(base_scores) / len(base_scores):+.1f}")
top[3].metric("Baseline's own spread", f"{max(base_scores) - min(base_scores)} runs",
              "nothing changed between them", delta_color="off")

if overlap:
    st.warning(
        "**The ranges overlap, so the fix cannot be called an improvement.** "
        "Running the unchanged baseline three times scored "
        f"{', '.join(str(s) for s in base_scores)} out of 20. The gap between "
        "the two means is smaller than the baseline's variation with nothing "
        "changed at all — which is what a single before-and-after screenshot "
        "would have hidden."
    )

st.markdown(
    dot_plot({"baseline": base_scores, "fixed": fixed_scores},
             "Every run drawn separately, not a bar of means",
             "one dot per pass over the 20 claims · vertical rule marks the mean"),
    unsafe_allow_html=True,
)

st.divider()

# --- the table view, which is also the accessibility fallback ---------------

left, right = st.columns([1, 1], gap="large")

with left:
    st.subheader("Per check")
    rows = []
    for name, _ in CHECKS:
        base = [summarise(grade_all(r))["per_check"][name] for _, r in batches["baseline"]]
        fixed = [summarise(grade_all(r))["per_check"][name] for _, r in batches["fixed"]]
        rows.append(
            {
                "check": name,
                "baseline": " ".join(str(v) for v in base),
                "fixed": " ".join(str(v) for v in fixed),
            }
        )
    rows.append(
        {
            "check": "ALL THREE",
            "baseline": " ".join(str(v) for v in base_scores),
            "fixed": " ".join(str(v) for v in fixed_scores),
        }
    )
    rows.append(
        {
            "check": "verdict matches expected",
            "baseline": " ".join(str(v) for v in base_verdicts),
            "fixed": " ".join(str(v) for v in fixed_verdicts),
        }
    )
    st.dataframe(rows, hide_index=True, use_container_width=True)
    st.caption("Three repeats per version, each out of 20.")

with right:
    st.subheader("Which claims are unstable")
    st.caption(
        "P and F across the three repeats. A row that is not PPP or FFF flipped "
        "with nothing changed — that is where the agent is genuinely undecided, "
        "and where any real fix has to be measured."
    )
    _, first_batch = batches["baseline"][0]
    ids = [record["trace_id"] for record in first_batch]
    grid = []
    for tid in ids:
        row = {"claim": tid}
        for version in ("baseline", "fixed"):
            marks = ""
            for _, records in batches[version]:
                rec = next(r for r in records if r["trace_id"] == tid)
                marks += "P" if grade_all([rec])[0]["passed_all"] else "F"
            row[version] = marks
        row["stable"] = "" if len(set(row["baseline"] + row["fixed"])) == 1 else "flips"
        grid.append(row)
    st.dataframe(grid, hide_index=True, use_container_width=True, height=430)

st.divider()

# --- run the suite on demand ------------------------------------------------

st.subheader("Run the checks")
st.caption(
    "The checks read recorded runs and need no model and no network, so this is "
    "instant and free. Recording new runs is a separate, slower step: "
    "`.venv/bin/python run_batch.py`."
)

choice = st.selectbox(
    "Which recorded pass",
    [label for version in ("baseline", "fixed") for label, _ in batches[version]],
)

if st.button("Run the three checks", type="primary"):
    records = next(
        recs
        for version in ("baseline", "fixed")
        for label, recs in batches[version]
        if label == choice
    )
    graded = grade_all(records)
    stats = summarise(graded)
    st.success(f"{stats['passed_all']} of {stats['total']} runs pass all three checks.")

    failures = [
        (g["trace_id"], name, g["checks"][name]["reason"])
        for g in graded
        for name, _ in CHECKS
        if not g["checks"][name]["passed"]
    ]
    if failures:
        st.dataframe(
            [{"claim": t, "check": n, "why": why} for t, n, why in failures],
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info("No failures in this pass.")

st.divider()

# --- path B: the validated judge --------------------------------------------

st.subheader("Path B — the LLM judge, and whether it can be trusted")

judge_rows = judge_results()

if not judge_rows:
    st.caption(
        "No judge runs recorded yet. See judge_notes.md, or run "
        "`.venv/bin/python judge.py --compare`."
    )
else:
    best = max(judge_rows, key=lambda r: (r["tpr"], r["tnr"]))
    constant = judge_rows[0]["negatives"] / judge_rows[0]["n"]

    st.caption(
        "The three code checks catch four of the eight failures found by hand. "
        "This judge automates one of the rest — whether the reasoning asserts "
        "anything the tool results do not support — and is validated against 40 "
        "labelled runs. Full write-up in judge_notes.md."
    )

    cols = st.columns(4)
    cols[0].metric("Best judge TPR", f"{best['tpr']:.0%}",
                   f"{best['model']} · {best['version']}", delta_color="off")
    cols[1].metric("Best judge TNR", f"{best['tnr']:.0%}",
                   f"never flagged a clean run" if best["fp"] == 0 else f"{best['fp']} false alarms",
                   delta_color="off")
    cols[2].metric("Ungrounded in the set", f"{judge_rows[0]['positives']} of {judge_rows[0]['n']}",
                   f"{judge_rows[0]['positives'] / judge_rows[0]['n']:.0%} prevalence",
                   delta_color="off")
    cols[3].metric("A do-nothing judge scores", f"{constant:.0%}",
                   "agreement, with 0% TPR", delta_color="off")

    st.warning(
        f"**Why agreement is never reported alone.** A judge that answers "
        f"GROUNDED to everything — one return statement, no model — scores "
        f"**{constant:.0%} agreement** on this set and catches nothing. "
        f"gpt-4o-mini with the obvious prompt scored exactly that, missing 9 of "
        f"11 real failures. Reported as agreement, it would have shipped."
    )

    st.markdown(
        grouped_bars(judge_rows,
                     "How much of the failure each judge actually catches",
                     "TPR: of the runs a human called ungrounded, how many were caught · "
                     "TNR: of the clean runs, how many were left alone"),
        unsafe_allow_html=True,
    )

    st.dataframe(
        [
            {
                "model": r["model"],
                "prompt": r["version"],
                "TP": r["tp"], "FN": r["fn"], "FP": r["fp"], "TN": r["tn"],
                "TPR": f"{r['tpr']:.0%}",
                "TNR": f"{r['tnr']:.0%}",
                "agreement": f"{r['agreement']:.0%}",
            }
            for r in judge_rows
        ],
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        "The prompt refinement helped; the model mattered more. gpt-4o-mini "
        "with the better prompt still scores below gpt-4o with the worse one — "
        "the same capability gap Week 3 measured when gpt-4o-mini got the "
        "JPMorgan verdict wrong. Labels were drafted by Claude and spot-checked "
        "by the author, so these rates measure agreement with that analysis "
        "rather than independent human ground truth."
    )

with st.expander("The two abandoned drafts of the rewrite"):
    st.caption(
        "Shown apart from the comparison above and never averaged into it: each "
        "is a different instruction text, run once. They are here because the "
        "path to the final wording is part of the result — the first draft broke "
        "the hardest claim in the set, and one run of each was not enough to "
        "notice."
    )
    for filename, description in DRAFTS.items():
        path = HERE / filename
        if path.exists():
            records = load_records(path)
            st.write(
                f"**{description}** — {score(records)}/20 on the checks, "
                f"{verdict_score(records)}/20 verdicts, single run"
            )
