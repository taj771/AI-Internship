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

from checks import CHECKS, grade, grade_all, summarise
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
    "Twenty hand-built claims with SEC figures established by hand before any "
    "run, three code checks, every instruction version run three times, and an "
    "LLM judge validated against forty hand labels. Claims in claims.py, "
    "failures in taxonomy.md, checks in checks.py, judge in judge_notes.md."
)

if not batches["baseline"] or not batches["fixed"]:
    st.error("No recorded runs found. Run `.venv/bin/python run_batch.py` first.")
    st.stop()

base_scores = [score(records) for _, records in batches["baseline"]]
fixed_scores = [score(records) for _, records in batches["fixed"]]
base_verdicts = [verdict_score(records) for _, records in batches["baseline"]]
fixed_verdicts = [verdict_score(records) for _, records in batches["fixed"]]

# Three tabs rather than one long scroll. The two paths answer different
# questions — did the fix work, and can the judge be trusted — and the third
# exists so that a reader can disagree: every number on the first two tabs is
# computed from runs that can be opened and read there.
#
# A fourth tab was added after Week 4 was submitted, and before it was graded.
# It draws the pipeline the other three tabs report on. It computes nothing new
# and changes no number, no check and no recorded run — every figure in it is
# read from the same data the tabs above already display. It is here because a
# reader arriving at "the fix is inside the noise" needs to know what produced
# that sentence, and a diagram answers that faster than three tabs of charts.
tab_a, tab_b, tab_runs, tab_how = st.tabs(
    [
        "Path A — the fix, and the noise",
        "Path B — the judge",
        "Browse the runs",
        "🔀 How the eval works",
    ]
)


# --- Path A -----------------------------------------------------------------

with tab_a:
    overlap = max(base_scores) >= min(fixed_scores)

    top = st.columns(4)
    top[0].metric("Baseline", f"{sum(base_scores) / len(base_scores):.1f} / 20",
                  f"range {min(base_scores)}–{max(base_scores)}", delta_color="off")
    top[1].metric("After the fix", f"{sum(fixed_scores) / len(fixed_scores):.1f} / 20",
                  f"range {min(fixed_scores)}–{max(fixed_scores)}", delta_color="off")
    top[2].metric(
        "Difference in means",
        f"{sum(fixed_scores) / len(fixed_scores) - sum(base_scores) / len(base_scores):+.1f}",
    )
    top[3].metric("Baseline's own spread", f"{max(base_scores) - min(base_scores)} runs",
                  "nothing changed between them", delta_color="off")

    if overlap:
        st.warning(
            "**The ranges overlap, so the fix cannot be called an improvement.** "
            "Running the unchanged baseline three times scored "
            f"{', '.join(str(s) for s in base_scores)} out of 20. The gap between "
            "the two means is smaller than the baseline's variation with nothing "
            "changed at all — which is exactly what a single before-and-after "
            "screenshot would have hidden."
        )

    st.markdown(
        dot_plot({"baseline": base_scores, "fixed": fixed_scores},
                 "Every run drawn separately, not a bar of means",
                 "one dot per pass over the 20 claims · vertical rule marks the mean"),
        unsafe_allow_html=True,
    )

    st.divider()

    left, right = st.columns([1, 1], gap="large")

    with left:
        st.subheader("Per check")
        rows = []
        for name, _ in CHECKS:
            base = [summarise(grade_all(r))["per_check"][name] for _, r in batches["baseline"]]
            fixed = [summarise(grade_all(r))["per_check"][name] for _, r in batches["fixed"]]
            rows.append({"check": name,
                         "baseline": " ".join(str(v) for v in base),
                         "fixed": " ".join(str(v) for v in fixed)})
        rows.append({"check": "ALL THREE",
                     "baseline": " ".join(str(v) for v in base_scores),
                     "fixed": " ".join(str(v) for v in fixed_scores)})
        rows.append({"check": "verdict matches expected",
                     "baseline": " ".join(str(v) for v in base_verdicts),
                     "fixed": " ".join(str(v) for v in fixed_verdicts)})
        st.dataframe(rows, hide_index=True, use_container_width=True)
        st.caption("Three repeats per version, each out of 20.")

    with right:
        st.subheader("Which claims are unstable")
        st.caption(
            "P and F across the three repeats. A row that is not PPP or FFF "
            "flipped with nothing changed — that is where the agent is "
            "genuinely undecided, and where any real fix has to be measured."
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

    st.subheader("Run the checks")
    st.caption(
        "The checks read recorded runs and need no model and no network, so this "
        "is instant and free. Recording new runs is a separate, slower step: "
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
                hide_index=True, use_container_width=True,
            )
        else:
            st.info("No failures in this pass.")

    with st.expander("The two abandoned drafts of the rewrite"):
        st.caption(
            "Shown apart from the comparison above and never averaged into it: "
            "each is a different instruction text, run once. They are here "
            "because the path to the final wording is part of the result — the "
            "first draft broke the hardest claim in the set, and one run of each "
            "was not enough to notice."
        )
        for filename, description in DRAFTS.items():
            path = HERE / filename
            if path.exists():
                records = load_records(path)
                st.write(
                    f"**{description}** — {score(records)}/20 on the checks, "
                    f"{verdict_score(records)}/20 verdicts, single run"
                )


# --- Path B -----------------------------------------------------------------

with tab_b:
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
            "The three code checks catch four of the eight failures found by "
            "hand. This judge automates one of the rest — whether the reasoning "
            "asserts anything the tool results do not support — and is validated "
            "against 40 labelled runs. Full write-up in judge_notes.md."
        )

        cols = st.columns(4)
        cols[0].metric("Best judge TPR", f"{best['tpr']:.0%}",
                       f"{best['model']} · {best['version']}", delta_color="off")
        cols[1].metric(
            "Best judge TNR", f"{best['tnr']:.0%}",
            "never flagged a clean run" if best["fp"] == 0 else f"{best['fp']} false alarms",
            delta_color="off",
        )
        cols[2].metric("Ungrounded in the set",
                       f"{judge_rows[0]['positives']} of {judge_rows[0]['n']}",
                       f"{judge_rows[0]['positives'] / judge_rows[0]['n']:.0%} prevalence",
                       delta_color="off")
        cols[3].metric("A do-nothing judge scores", f"{constant:.0%}",
                       "agreement, with 0% TPR", delta_color="off")

        st.warning(
            f"**Why agreement is never reported alone.** A judge that answers "
            f"GROUNDED to everything — one return statement, no model — scores "
            f"**{constant:.0%} agreement** on this set and catches nothing. "
            f"gpt-4o-mini with the obvious prompt scored exactly that, missing 9 "
            f"of 11 real failures. Reported as agreement, it would have shipped."
        )

        st.markdown(
            grouped_bars(
                judge_rows,
                "How much of the failure each judge actually catches",
                "TPR: of the runs a human called ungrounded, how many were caught · "
                "TNR: of the clean runs, how many were left alone",
            ),
            unsafe_allow_html=True,
        )

        st.dataframe(
            [
                {"model": r["model"], "prompt": r["version"],
                 "TP": r["tp"], "FN": r["fn"], "FP": r["fp"], "TN": r["tn"],
                 "TPR": f"{r['tpr']:.0%}", "TNR": f"{r['tnr']:.0%}",
                 "agreement": f"{r['agreement']:.0%}"}
                for r in judge_rows
            ],
            hide_index=True, use_container_width=True,
        )
        st.caption(
            "The prompt refinement helped; the model mattered more. gpt-4o-mini "
            "with the better prompt still scores below gpt-4o with the worse "
            "one — the same capability gap Week 3 measured when gpt-4o-mini got "
            "the JPMorgan verdict wrong. Labels were drafted by Claude and "
            "spot-checked by the author, so these rates measure agreement with "
            "that analysis rather than independent human ground truth."
        )


# --- Browse the runs --------------------------------------------------------

with tab_runs:
    st.caption(
        "Every number on the other two tabs comes from these runs. This tab is "
        "read-only on purpose: the annotation bench writes notes back into the "
        "trace file, and on a shared server those would be editable by any "
        "visitor and lost on the next deploy."
    )

    all_passes = [(label, records)
                  for version in ("baseline", "fixed")
                  for label, records in batches[version]]

    pick = st.columns([1, 2])
    pass_label = pick[0].selectbox("Pass", [label for label, _ in all_passes])
    records = next(recs for label, recs in all_passes if label == pass_label)
    claim_id = pick[1].selectbox(
        "Claim",
        [r["trace_id"] for r in records],
        format_func=lambda tid: (
            f"{tid} — {next(r for r in records if r['trace_id'] == tid)['claim'][:70]}"
        ),
    )

    record = next(r for r in records if r["trace_id"] == claim_id)
    graded = grade(record)

    st.markdown(f"### {record['claim']}")

    meta = st.columns(5)
    meta[0].metric("Expected", record["expected_verdict"])
    meta[1].metric("Answered", (record.get("parsed") or {}).get("VERDICT") or "—")
    meta[2].metric("Lookups", record["n_tool_calls"])
    meta[3].metric("Seconds", record["duration_s"])
    meta[4].metric("Checks", f"{sum(1 for c in graded.values() if c['passed'])} / {len(graded)}")

    for name, result in graded.items():
        (st.success if result["passed"] else st.error)(
            f"**{name}** — {result['reason']}"
        )

    if record.get("why_this_claim"):
        with st.expander("Why this claim is in the set"):
            st.write(record["why_this_claim"])

    left, right = st.columns([1, 1], gap="large")

    with left:
        st.markdown("**The answer**")
        st.code(record.get("answer") or "(no answer)", language="text", wrap_lines=True)
        if record.get("your_notes"):
            st.markdown("**What a human said when reading it**")
            st.info(record["your_notes"])
            if record.get("your_failure_label"):
                st.caption(
                    f"labelled: {record['your_failure_label']} · "
                    f"{record.get('your_pass_fail', '')}"
                )

    with right:
        st.markdown("**Every step it took**")
        if not record["steps"]:
            st.warning("No steps — the agent answered without calling anything.")
        for number, step in enumerate(record["steps"], start=1):
            mark = {"THINK": "🧠", "ACT": "🔧", "OBSERVE": "👁"}.get(step["kind"], "•")
            st.markdown(
                f"**{number}. {mark} {step['kind']}** · turn {step.get('turn', '?')}"
            )
            full = step.get("response") or step.get("text")
            shown = json.dumps(full, indent=1) if isinstance(full, dict) else (full or step["detail"])
            st.code(shown, language="text", wrap_lines=True)


# --- How the eval works ------------------------------------------------------
#
# Added after submission, before grading. Draws the pipeline the tabs above
# report on, using the numbers they already computed — nothing here recomputes
# anything, so the diagram cannot drift from the charts.
#
# Two graphs, because Path A and Path B fail in different ways and the failures
# are the point. Path A's is that a difference can sit inside its own noise.
# Path B's is that a judge can score well on a metric that a return statement
# also scores well on.

_BOX = 'style=filled fontname="Helvetica" fontsize=10 margin="0.16,0.10"'
_HAND = 'style=filled fillcolor="#eae4fb" color="#5b3fa0" fontcolor="#2e1d55"'
_AUTO = 'style=filled fillcolor="#eef1f8" color="#9aa4bf" fontcolor="#20242e"'
_DATA = 'shape=cylinder style=filled fillcolor="#fff4e0" color="#b26a00" fontcolor="#5c3600"'
_GOOD = 'style=filled fillcolor="#e6f4ea" color="#1a7f37" fontcolor="#12492a"'
_BAD = 'style=filled fillcolor="#fdecea" color="#c62828" fontcolor="#7d1d1d"'


def _seq(values: list[int]) -> str:
    return ", ".join(str(v) for v in values)


def path_a_dot() -> str:
    """The Path A pipeline, with this dashboard's own numbers in the boxes."""
    n_claims = len(batches["baseline"][0][1]) if batches["baseline"] else 0
    n_runs = sum(len(records) for group in batches.values() for _, records in group)
    n_batches = sum(len(group) for group in batches.values())
    overlapping = max(base_scores) >= min(fixed_scores)

    outcome = (
        (
            f"OVERLAPPING\\nbaseline spans {min(base_scores)}-{max(base_scores)} on its own.\\n"
            f"The gap between the two is\\n"
            f"{sum(fixed_scores)/len(fixed_scores) - sum(base_scores)/len(base_scores):+.1f} — inside it."
        )
        if overlapping
        else "SEPARATED\\nevery fixed run beat every baseline run"
    )

    return f"""
digraph patha {{
  rankdir=TB; bgcolor=transparent;
  node [shape=box {_BOX}];
  edge [fontname="Helvetica" fontsize=8 color="#9aa4bf"];

  claims [label="claims.py — {n_claims} claims\\nwritten to BREAK the agent\\nSEC figures established BY HAND\\nbefore any run" {_HAND}];
  why    [label="so the eval is not\\nmarking its own homework" shape=note {_HAND}];
  batch  [label="run_batch.py\\nrecords every step,\\nand which turn it belonged to" {_AUTO}];
  traces [label="traces*.jsonl\\n{n_runs} runs in {n_batches} batches" {_DATA}];
  read   [label="open_coding.py\\nall {n_claims} read BY HAND\\nbefore inventing categories" {_HAND}];
  tax    [label="taxonomy.md\\nsix failure types\\n(two found by calling the\\ntool by hand, before the model)" {_HAND}];
  checks [label="checks.py\\nautomated checks,\\ntested against runs already\\ngraded by hand" {_AUTO}];
  gap    [label="catch 4 of the 8\\nfailures found by hand.\\nThe rest need someone who\\nknows what the numbers mean" {_BAD}];
  instr  [label="instructions.py\\nBASELINE vs FIXED\\nselected by env var, so a\\nbaseline stays a baseline" {_AUTO}];
  reps   [label="THREE reps of EACH\\nnothing changed between them" {_HAND}];
  nums   [label="baseline  {_seq(base_scores)}\\nfixed     {_seq(fixed_scores)}   (of {n_claims})" {_DATA}];
  verdict [label="{outcome}" {_BAD if overlapping else _GOOD}];

  claims -> why [style=dashed arrowhead=none];
  claims -> batch -> traces -> read -> tax;
  tax -> checks -> gap;
  tax -> instr -> reps -> nums -> verdict;

  {{rank=same; claims; why;}}
  {{rank=same; checks; instr;}}
}}
"""


def path_b_dot(rows: list[dict]) -> str:
    """The judge validation, and the trivial baseline that makes it mean something."""
    if not rows:
        return ""

    worst, best = rows[0], rows[-1]
    n = best.get("n", 0)
    negatives = best.get("negatives", 0)
    positives = best.get("positives", 0)
    trivial = negatives / n if n else 0.0

    def pct(value) -> str:
        return f"{value * 100:.0f}%" if value is not None else "—"

    return f"""
digraph pathb {{
  rankdir=TB; bgcolor=transparent;
  node [shape=box {_BOX}];
  edge [fontname="Helvetica" fontsize=8 color="#9aa4bf"];

  labels [label="{n} runs labelled BY HAND\\n{positives} ungrounded, {negatives} clean" {_HAND}];
  judge  [label="judge.py\\nsame runs, four configurations\\n(two models x two prompts)" {_AUTO}];
  bad    [label="{worst['model']} · {worst['version']}\\nTPR {pct(worst['tpr'])}  ·  agreement {pct(worst['agreement'])}" {_BAD}];
  good   [label="{best['model']} · {best['version']}\\nTPR {pct(best['tpr'])}  ·  agreement {pct(best['agreement'])}" {_GOOD}];
  triv   [label="BASELINE: always answer GROUNDED\\none return statement, no model\\nTPR 0%  ·  agreement {pct(trivial)}" {_BAD}];
  lesson [label="Read the first row against the last.\\nA judge that answers GROUNDED to\\neverything scores the same agreement\\nas the small model with the obvious\\nprompt — while missing {positives - round((worst['tpr'] or 0) * positives)} of {positives}\\nreal failures.\\n\\nReport TPR. Never agreement." shape=note {_HAND}];

  labels -> judge;
  judge -> bad; judge -> good;
  labels -> triv [label="  compare against"];
  bad -> lesson [style=dashed arrowhead=none];
  triv -> lesson [style=dashed arrowhead=none];

  {{rank=same; bad; good; triv;}}
}}
"""


with tab_how:
    st.subheader("Path A — how a failure becomes a measured comparison")
    st.caption(
        "The numbers in these boxes are read from the same recorded runs the "
        "other tabs chart, not typed in here. Purple is a step a human did; "
        "grey is a step code did."
    )
    st.graphviz_chart(path_a_dot(), use_container_width=True)

    st.markdown(
        """
**Three things this shape is arguing.**

Ground truth is established at the top, by hand, *before* any run. If the
expected verdicts came from the agent's own answers the evaluation would be
marking its own homework, and every consistent mistake would score as correct.

The runs are read by hand *before* the failure categories exist. I had a list of
suspected failures going in. The two worst things I found were not on it — and
two of them were found by calling the tool by hand, with no model involved at
all.

The two branches out of `taxonomy.md` do different jobs. Checks turn a failure
into something that can be re-run; the instruction rewrite tries to remove it.
Only the right-hand branch is a claim about the agent getting better, and only
that branch needs repetition to be believed.
"""
    )

    st.divider()

    rows = judge_results()
    if rows:
        st.subheader("Path B — why a judge needs a baseline before it needs a score")
        st.caption(
            "Same reading: every rate is computed from the hand-labelled set, "
            "not written into the diagram."
        )
        st.graphviz_chart(path_b_dot(rows), use_container_width=True)

        st.markdown(
            """
The trivial baseline is the whole point of this graph. It is not a model, a
prompt or a pipeline — it is `return "GROUNDED"`. It scores well on agreement
for one reason: most runs are clean, so a judge that never raises an alarm is
right most of the time.

If I had reported agreement, I would have shipped the small model with the
obvious prompt and believed it worked.

**Judge caveat, carried from the write-up:** the labels were drafted with AI
help and spot-checked, not produced independently. So these rates measure "does
a smaller model reproduce this analysis" rather than "does it match a human",
and with so few positives one reclassified run moves TPR by several points.
"""
        )

    st.divider()
    st.caption(
        "Added after this week was submitted and before it was graded. It draws "
        "the pipeline; it computes nothing and changes no recorded run, no check "
        "and no number. See week-4/README.md."
    )
