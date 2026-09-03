"""The figure: what JPMorgan puts in Item 7 that cannot be checked, by year.

Reads coverage.jsonl, returns inline SVG. No plotting library — the chart is
fifteen stacked bars, and a dependency that renders fifteen bars is a dependency
that also has to be installed on the deploy host.


WHY STACKED COUNTS RATHER THAN SHARES

Two things changed at once and a share chart would hide one of them. The volume
of numeric prose collapsed — 16.9 numeric claims per 10,000 characters in FY2011
against 7.0 in FY2025 — and its composition shifted toward figures the public
API cannot reach. Normalising to 100% would flatten the first and leave only the
second, which is the smaller finding.

Counts show both: the bars get shorter AND the blue band shrinks faster than the
bar does.


THE FIVE BANDS ARE FIVE DIFFERENT REASONS

Collapsing them into "checkable / not" would answer a smaller question. A
percentage is unverifiable because nobody files a percentage. A segment figure
is unverifiable because the SEC's JSON endpoints strip dimensions — the data is
filed and audited, and the API will not hand it over. A non-GAAP measure is
unverifiable by definition. Those have different fixes, and one of them is not a
fix at all.


PALETTE

Slots 1-5 of the reference categorical palette, validated in both modes before
use: worst adjacent CVD ΔE 9.1 light / 8.4 dark, worst adjacent normal-vision
ΔE 19.6 / 19.3. Light mode returns a contrast warning on three slots, which
obliges visible relief — hence the totals printed above every bar and the table
view rendered beneath the figure.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent

# Short label for the legend, long one for the tooltip and the table. A legend
# carrying the full explanation ran to 870px against a 900px canvas and pushed
# the last entry off the edge — measured, not guessed.
BANDS = [
    ("checkable", "Checkable",        "tag found, data for that year",   "#2a78d6", "#3987e5"),
    ("no_tag",    "No tag filed",     "no matching us-gaap tag",          "#eb6834", "#d95926"),
    ("derivable", "Comparison",       "a change; needs two lookups",      "#1baf7a", "#199e70"),
    ("segment",   "Segment",          "tagged, but the API strips dimensions", "#eda100", "#c98500"),
    ("untagged",  "Ratio / non-GAAP", "not filed at all",                 "#e87ba4", "#d55181"),
]


def tally() -> tuple[list[str], dict[str, list[int]], dict[str, int]]:
    rows = [json.loads(line) for line in (HERE / "coverage.jsonl").open(encoding="utf-8")]
    years = sorted({str(r["doc_fy"]) for r in rows})
    counts = {key: [] for key, *_ in BANDS}
    totals = {}
    for year in years:
        got = Counter()
        for r in rows:
            if str(r["doc_fy"]) != year:
                continue
            s = r["structural"]
            if s == "reachable":
                got["checkable" if r["has_tag"] else "no_tag"] += 1
            elif s == "derivable":
                got["derivable"] += 1
            elif s == "tagged_unreachable":
                got["segment"] += 1
            elif s in ("rarely_tagged", "never_tagged"):
                got["untagged"] += 1
        for key, *_ in BANDS:
            counts[key].append(got[key])
        totals[year] = sum(got.values())
    return years, counts, totals


def coverage_svg() -> str:
    years, counts, totals = tally()
    W, H = 900, 430
    L, R, T, B = 52, 14, 58, 64            # margins
    plot_w, plot_h = W - L - R, H - T - B
    top = max(totals.values())
    ymax = int((top + 99) // 100 * 100)
    step = plot_w / len(years)
    bw = min(38.0, step * 0.62)

    p = []
    # recessive grid, labelled
    for g in range(0, ymax + 1, 200):
        y = T + plot_h - g / ymax * plot_h
        p.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" '
                 f'class="grid"/>')
        p.append(f'<text x="{L-9}" y="{y+4:.1f}" text-anchor="end" class="tick">{g:,}</text>')

    for i, year in enumerate(years):
        x = L + step * i + (step - bw) / 2
        acc = 0.0
        for key, short, long, _, _ in BANDS:
            v = counts[key][i]
            if not v:
                continue
            h = v / ymax * plot_h
            y = T + plot_h - acc - h
            # 2px surface gap between stacked segments
            p.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                f'height="{max(h-2,0.6):.1f}" rx="1.5" class="b-{key}">'
                f'<title>FY{year} · {short} ({long}): {v} claims</title></rect>'
            )
            acc += h
        p.append(f'<text x="{x+bw/2:.1f}" y="{T+plot_h-acc-7:.1f}" text-anchor="middle" '
                 f'class="total">{totals[year]}</text>')
        p.append(f'<text x="{x+bw/2:.1f}" y="{T+plot_h+17:.1f}" text-anchor="middle" '
                 f'class="tick">{year[2:]}</text>')

    p.append(f'<line x1="{L}" y1="{T+plot_h}" x2="{W-R}" y2="{T+plot_h}" class="axis"/>')
    p.append(f'<text x="{L}" y="20" class="title">Numeric claims in JPMorgan\'s Item 7, '
             f'and how many can be checked against filed XBRL</text>')
    p.append(f'<text x="{L}" y="38" class="sub">Fiscal years 2011–2025 · '
             f'{sum(totals.values()):,} claims · FY2010 unavailable</text>')

    # legend — always present for >= 2 series
    lx = L
    for key, short, _, _, _ in BANDS:
        p.append(f'<rect x="{lx}" y="{H-30}" width="10" height="10" rx="2" class="b-{key}"/>')
        p.append(f'<text x="{lx+15}" y="{H-21}" class="legend">{short}</text>')
        lx += 20 + len(short) * 6.3

    swatches = "".join(
        f'.b-{k}{{fill:{lt}}} ' for k, _, _, lt, _ in BANDS)
    dark = "".join(f'.b-{k}{{fill:{dk}}} ' for k, _, _, _, dk in BANDS)

    return f'''<svg viewBox="0 0 {W} {H}" width="100%" role="img"
 aria-label="Stacked bars, fiscal years 2011 to 2025. Total numeric claims in JPMorgan's
 MD&amp;A fall from 896 to 278, and the checkable share falls from 53 percent to 45 percent."
 xmlns="http://www.w3.org/2000/svg"><style>
 .grid{{stroke:#e3e3e0;stroke-width:1}} .axis{{stroke:#b8b8b3;stroke-width:1}}
 .tick{{font:11px ui-sans-serif,system-ui,sans-serif;fill:#52514e;font-variant-numeric:tabular-nums}}
 .total{{font:600 10.5px ui-sans-serif,system-ui,sans-serif;fill:#0b0b0b;font-variant-numeric:tabular-nums}}
 .title{{font:600 14px ui-sans-serif,system-ui,sans-serif;fill:#0b0b0b}}
 .sub{{font:11.5px ui-sans-serif,system-ui,sans-serif;fill:#52514e}}
 .legend{{font:11px ui-sans-serif,system-ui,sans-serif;fill:#52514e}}
 {swatches}
 rect[class^="b-"]{{stroke:#fcfcfb;stroke-width:0.6}}
 @media (prefers-color-scheme:dark){{
   .grid{{stroke:#2f2f2d}} .axis{{stroke:#4a4a47}}
   .tick,.sub,.legend{{fill:#c3c2b7}} .total,.title{{fill:#ffffff}}
   {dark}
   rect[class^="b-"]{{stroke:#1a1a19}}
 }}</style>{"".join(p)}</svg>'''


def table_rows() -> list[dict]:
    """The relief the light-mode contrast warning obliges."""
    years, counts, totals = tally()
    out = []
    for i, year in enumerate(years):
        row = {"fiscal year": year}
        for key, short, _, _, _ in BANDS:
            row[short] = counts[key][i]
        row["total"] = totals[year]
        row["checkable %"] = f"{counts['checkable'][i] / totals[year]:.0%}"
        out.append(row)
    return out


if __name__ == "__main__":
    svg = coverage_svg()
    (HERE / "coverage_chart.svg").write_text(svg, encoding="utf-8")
    print(f"wrote coverage_chart.svg ({len(svg):,} bytes)")
    for r in table_rows():
        print(r)
