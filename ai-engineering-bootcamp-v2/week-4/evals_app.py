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
