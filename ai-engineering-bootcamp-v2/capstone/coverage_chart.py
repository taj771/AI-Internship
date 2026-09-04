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


PALETTE — ORDERED BY WHETHER IT CAN BE FIXED

The five hues are not decoration and they are not slots 1-5 of a stock ramp.
Teal is the one band that answers yes; the four that follow are ordered by how
recoverable they are — arithmetic away, a harder parser away, possibly our own
retrieval's fault, impossible — and the hue walks from warm to violet as that
hope runs out. A reader who never reads the legend still sees one green base
under a rising column of everything we could not reach.

Both modes validated as a set before use (OKLab x100, adjacent pairlist):

  light  #0e9d8c #e0a300 #ea5a1f #c62368 #7a4fd1
         CVD dE 10.2 worst adjacent, normal-vision 16.2, all inside L 0.43-0.77
  dark   #03a290 #c08700 #bf3002 #d44489 #8a6ee2
         CVD dE 12.8 worst adjacent, normal-vision 15.1, all >= 3:1 on surface

Light mode returns a contrast warning on the amber (2.17:1). That obliges
visible relief rather than a different colour — hence the totals printed above
every bar, the plain-language cards the app renders beneath the figure, and the
table view. The dark steps are the same five hues re-stepped for the dark
surface, not a second palette.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).parent

class Band(NamedTuple):
    """One reason a sentence can or cannot be checked.

    Five fields carry the same idea at four lengths, because four places need
    it: the legend has room for a phrase, the tooltip for a clause, the app's
    cards for a sentence, and a reader who has never opened a 10-K needs a real
    sentence out of one before any of it means anything.

    The legend used to read "Checkable / No tag filed / Comparison / Segment /
    Ratio / non-GAAP" and the first person to look at it asked what those meant.
    A legend that needs its author standing next to it is not a legend.
    """

    key: str
    short: str      # legend, under ~26 chars — the whole row must fit 900px
    long: str       # tooltip and table column
    plain: str      # the app's card: what this means, no jargon
    example: str    # a real JPMorgan FY2025 sentence in this band
    light: str
    dark: str


BANDS = [
    Band("checkable", "Checkable",
         "tag found, with data for that year",
         "The bank names a number, and its own filed spreadsheet has a line "
         "with that name. We can look it up and compare.",
         "JPMorganChase had $4.4 trillion in assets and $362.4 billion in "
         "stockholders' equity as of December 31, 2025",
         "#0e9d8c", "#03a290"),

    Band("derivable", "A change, not an amount",
         "a movement; needs two lookups and arithmetic",
         "The number is a movement, not an amount. Nobody files a movement — "
         "checking it means looking up two years and subtracting.",
         "Noninterest expense was $95.6 billion, up 4%, driven by higher "
         "compensation expense",
         "#e0a300", "#c08700"),

    Band("segment", "One part of the bank",
         "filed and audited, but the JSON API strips the dimension",
         "The figure covers one division or product. The bank did file it and "
         "an auditor did sign it — but the SEC's public download hands back "
         "whole-bank totals only and drops the by-division detail.",
         "Net charge-offs were $9.8 billion, up $1.2 billion, predominantly "
         "driven by Wholesale and Card Services",
         "#ea5a1f", "#bf3002"),

    Band("no_tag", "Nothing filed by that name",
         "no matching us-gaap tag with data for that year",
         "The right kind of number to look up, but nothing in the filed "
         "spreadsheet is named that. Sometimes it genuinely was never filed; "
         "sometimes our search missed it — so this band is an upper bound.",
         "Equity Markets revenue was $13.3 billion, up 33%",
         "#c62368", "#d44489"),

    Band("untagged", "Never filed by anyone",
         "a ratio, or the bank's own measure",
         "Not a filed number at all — someone calculated it, sometimes with "
         "their own private recipe. There is no filed value to compare it to.",
         "JPMorganChase reported net income of $57.0 billion for 2025, down "
         "2%, earnings per share of $20.02, ROE of 17% and ROTCE of 20%",
         "#7a4fd1", "#8a6ee2"),
]


def tally() -> tuple[list[str], dict[str, list[int]], dict[str, int]]:
    rows = [json.loads(line) for line in (HERE / "coverage.jsonl").open(encoding="utf-8")]
    years = sorted({str(r["doc_fy"]) for r in rows})
    counts = {b.key: [] for b in BANDS}
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
        for b in BANDS:
            counts[b.key].append(got[b.key])
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
        for b in BANDS:
            v = counts[b.key][i]
            if not v:
                continue
            h = v / ymax * plot_h
            y = T + plot_h - acc - h
            # 2px surface gap between stacked segments
            p.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                f'height="{max(h-2,0.6):.1f}" rx="1.5" class="b-{b.key}">'
                f'<title>FY{year} · {b.short} — {b.long}: {v} claims</title></rect>'
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
    for b in BANDS:
        p.append(f'<rect x="{lx}" y="{H-30}" width="10" height="10" rx="2" class="b-{b.key}"/>')
        p.append(f'<text x="{lx+15}" y="{H-21}" class="legend">{b.short}</text>')
        lx += 19 + len(b.short) * 6.05

    swatches = "".join(f'.b-{b.key}{{fill:{b.light}}} ' for b in BANDS)
    dark = "".join(f'.b-{b.key}{{fill:{b.dark}}} ' for b in BANDS)

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


def band_summary(year: str = "2025") -> list[dict]:
    """One row per band for the app's plain-language cards.

    Written to a small JSON file rather than recomputed on the page, because
    the app would otherwise parse a 3 MB coverage.jsonl on every visit to
    print five numbers that only change when the study is re-run.
    """
    years, counts, totals = tally()
    i = years.index(year)
    return [{"key": b.key, "short": b.short, "plain": b.plain,
             "example": b.example, "light": b.light, "dark": b.dark,
             "count": counts[b.key][i],
             "share": counts[b.key][i] / totals[year]} for b in BANDS]


def table_rows() -> list[dict]:
    """The relief the light-mode contrast warning obliges."""
    years, counts, totals = tally()
    out = []
    for i, year in enumerate(years):
        row = {"fiscal year": year}
        for b in BANDS:
            row[b.short] = counts[b.key][i]
        row["total"] = totals[year]
        row["checkable %"] = f"{counts['checkable'][i] / totals[year]:.0%}"
        out.append(row)
    return out


if __name__ == "__main__":
    svg = coverage_svg()
    (HERE / "coverage_chart.svg").write_text(svg, encoding="utf-8")
    print(f"wrote coverage_chart.svg ({len(svg):,} bytes)")
    summary = band_summary()
    (HERE / "coverage_bands.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote coverage_bands.json ({len(summary)} bands, FY2025)")
    for r in table_rows():
        print(r)
