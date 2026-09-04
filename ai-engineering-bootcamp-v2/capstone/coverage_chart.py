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

NAMES = {"JPM": "JPMorgan", "BAC": "Bank of America", "MS": "Morgan Stanley",
         "WFC": "Wells Fargo", "C": "Citigroup", "GS": "Goldman Sachs"}

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


def load(ticker: str | None = None) -> list[dict]:
    rows = [json.loads(line) for line in (HERE / "coverage.jsonl").open(encoding="utf-8")]
    return [r for r in rows if ticker is None or r["ticker"] == ticker]


def tally(ticker: str = "JPM") -> tuple[list[str], dict[str, list[int]], dict[str, int]]:
    rows = load(ticker)
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


def coverage_svg(ticker: str = "JPM") -> str:
    years, counts, totals = tally(ticker)
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
    p.append(f'<text x="{L}" y="20" class="title">Numeric claims in {NAMES[ticker]}\'s Item 7, '
             f'and how many can be checked against filed XBRL</text>')
    p.append(f'<text x="{L}" y="38" class="sub">Fiscal years {years[0]}–{years[-1]} · '
             f'{sum(totals.values()):,} claims</text>')

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


def band_summary(ticker: str = "JPM", year: str = "2025") -> list[dict]:
    """One row per band for the app's plain-language cards.

    Written to a small JSON file rather than recomputed on the page, because
    the app would otherwise parse a 3 MB coverage.jsonl on every visit to
    print five numbers that only change when the study is re-run.
    """
    years, counts, totals = tally(ticker)
    i = years.index(year)
    return [{"key": b.key, "short": b.short, "plain": b.plain,
             "example": b.example, "light": b.light, "dark": b.dark,
             "count": counts[b.key][i],
             "share": counts[b.key][i] / totals[year]} for b in BANDS]


def comparison_svg() -> str:
    """Five banks, five panels, one line each — deliberately not five lines.

    FORM

    A single chart with one line per bank fails the palette check outright: five
    categorical hues cannot clear the all-pairs separation floors, and lines
    cross, so adjacency is not enough to rescue them. Small multiples sidestep
    that entirely — every panel carries one series, so there is no categorical
    palette to validate and no colour anyone has to tell apart.

    It also stops the figure reading as a league table, which matters more here
    than the colour does. The four other banks sit behind each panel in grey, so
    a reader sees where a bank falls in the spread without the chart ranking
    them for him.

    WHAT THE PANELS DO NOT SAY

    A claim counts as checkable only when our own retrieval proposes a tag for
    it. That retrieval was built and tuned against JPMorgan, so a bank whose
    wording it reads badly and a bank that genuinely tags less of its narrative
    produce the same line. The levels are not comparable between panels; the
    slope inside one panel holds the matcher constant and is the safer read.

    The subtitle says so, on the figure, because a caveat that lives only in a
    document beside the figure is a caveat nobody reads.
    """
    order = ["JPM", "BAC", "MS", "WFC", "C"]
    series: dict[str, list[tuple[int, float]]] = {}
    for t in order:
        rows = load(t)
        pts = []
        # Citigroup has an FY2010 filing and the panels run 2011-2025. Plotting
        # it anyway put the first segment of its line outside its own panel,
        # overlapping the neighbour — a point drawn where it does not belong is
        # worse than a point not drawn.
        for fy in sorted({r["doc_fy"] for r in rows if 2011 <= r["doc_fy"] <= 2025}):
            rs = [r for r in rows if r["doc_fy"] == fy]
            chk = sum(1 for r in rs if r["structural"] == "reachable" and r["has_tag"])
            pts.append((fy, chk / len(rs)))
        series[t] = pts

    W, H = 940, 262
    L, R, T, B = 40, 16, 74, 34
    pw = (W - L - R - 4 * 14) / 5
    ph = H - T - B
    ymax = 0.60
    x0, x1 = 2011, 2025
    px = lambda p, fy: p + (fy - x0) / (x1 - x0) * pw
    py = lambda v: T + ph - v / ymax * ph

    out = []
    for i, t in enumerate(order):
        p0 = L + i * (pw + 14)
        out.append(f'<text x="{p0}" y="{T-11}" class="pt">{NAMES[t]}</text>')
        for g in (0.0, 0.2, 0.4, 0.6):
            y = py(g)
            out.append(f'<line x1="{p0}" y1="{y:.1f}" x2="{p0+pw:.1f}" y2="{y:.1f}" class="grid"/>')
            if i == 0:
                out.append(f'<text x="{p0-7}" y="{y+3.5:.1f}" text-anchor="end" '
                           f'class="tick">{g:.0%}</text>')
        # the other four, ghosted, for scale
        for other in order:
            if other == t:
                continue
            pts = " ".join(f"{px(p0, fy):.1f},{py(v):.1f}" for fy, v in series[other])
            out.append(f'<polyline points="{pts}" class="ghost"/>')
        pts = series[t]
        out.append('<polyline points="'
                   + " ".join(f"{px(p0, fy):.1f},{py(v):.1f}" for fy, v in pts)
                   + '" class="own"/>')
        for fy, v in pts:
            out.append(f'<circle cx="{px(p0, fy):.1f}" cy="{py(v):.1f}" r="2" class="dot">'
                       f'<title>{NAMES[t]} FY{fy}: {v:.0%} checkable</title></circle>')
        first, last = pts[0], pts[-1]
        out.append(f'<text x="{px(p0, last[0]):.1f}" y="{py(last[1])-8:.1f}" '
                   f'text-anchor="end" class="end">{last[1]:.0%}</text>')
        out.append(f'<text x="{p0}" y="{T+ph+15}" class="tick">{first[0]}</text>')
        out.append(f'<text x="{p0+pw:.1f}" y="{T+ph+15}" text-anchor="end" '
                   f'class="tick">{last[0]}</text>')

    return f'''<svg viewBox="0 0 {W} {H}" width="100%" role="img"
 aria-label="Five small line charts, one per bank, showing the share of numeric MD&amp;A
 claims with a matching filed concept each year. The level differs greatly between banks;
 the other four banks are drawn in grey behind each panel for scale."
 xmlns="http://www.w3.org/2000/svg"><style>
 .grid{{stroke:#e8e8e5;stroke-width:1}}
 .ghost{{fill:none;stroke:#d6d6d2;stroke-width:1.2}}
 .own{{fill:none;stroke:#0e9d8c;stroke-width:2}} .dot{{fill:#0e9d8c}}
 .tick{{font:10px ui-sans-serif,system-ui,sans-serif;fill:#6b6b66;font-variant-numeric:tabular-nums}}
 .pt{{font:600 12px ui-sans-serif,system-ui,sans-serif;fill:#0b0b0b}}
 .end{{font:600 11px ui-sans-serif,system-ui,sans-serif;fill:#0e9d8c;font-variant-numeric:tabular-nums}}
 .h1{{font:600 14px ui-sans-serif,system-ui,sans-serif;fill:#0b0b0b}}
 .h2{{font:11.5px ui-sans-serif,system-ui,sans-serif;fill:#52514e}}
 @media (prefers-color-scheme:dark){{
   .grid{{stroke:#2c2c2a}} .ghost{{stroke:#3f3f3c}}
   .own{{stroke:#03a290}} .dot{{fill:#03a290}} .end{{fill:#03a290}}
   .tick,.h2{{fill:#c3c2b7}} .pt,.h1{{fill:#ffffff}}
 }}</style>
 <text x="{L}" y="20" class="h1">Share of numeric claims with a matching filed concept, by bank</text>
 <text x="{L}" y="36" class="h2">The other four banks are grey behind each panel.</text>
 <text x="{L}" y="50" class="h2">Levels are not comparable across panels &#8212; retrieval was tuned on JPMorgan, so a bank
 we read badly and a bank that genuinely tags less look the same.</text>
 {"".join(out)}</svg>'''


def filer_summary() -> list[dict]:
    """Per-bank headline numbers, computed rather than typed into the page.

    The app used to carry JPMorgan's density and segment share as literals in
    its own source. With one filer that was merely brittle; with five it would
    be five chances to leave a stale number under a chart that had moved on.
    Everything here comes out of coverage.jsonl and the filing manifest.

    Density is claims per 10,000 characters of Item 7, because the raw count
    falls partly for the dull reason that the documents got shorter.
    """
    manifest = json.loads((HERE / "data" / "history" / "manifest.json").read_text())
    chars = {(m["ticker"], int(m["fiscal_year"])): m["chars"] for m in manifest}

    out = []
    for t in sorted({r["ticker"] for r in load()}):
        rows = [r for r in load(t) if 2011 <= r["doc_fy"] <= 2025]
        years = sorted({r["doc_fy"] for r in rows})
        first, last = years[0], years[-1]

        def band(fy):
            rs = [r for r in rows if r["doc_fy"] == fy]
            chk = sum(1 for r in rs if r["structural"] == "reachable" and r["has_tag"])
            seg = sum(1 for r in rs if r["structural"] == "tagged_unreachable")
            n = len(rs) or 1
            c = chars.get((t, fy)) or 0
            return {"fy": fy, "claims": len(rs), "checkable": chk / n, "segment": seg / n,
                    "density": (len(rs) / c * 10_000) if c else None}

        out.append({"ticker": t, "name": NAMES[t], "years": len(years),
                    "claims": len(rows), "first": band(first), "last": band(last)})
    return out


def table_rows(ticker: str = "JPM") -> list[dict]:
    """The relief the light-mode contrast warning obliges."""
    years, counts, totals = tally(ticker)
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
    import sys

    tickers = sys.argv[1:] or sorted({r["ticker"] for r in load()})
    for t in tickers:
        svg = coverage_svg(t)
        (HERE / f"coverage_chart_{t}.svg").write_text(svg, encoding="utf-8")
        summary = band_summary(t)
        (HERE / f"coverage_bands_{t}.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        years, _, totals = tally(t)
        print(f"  {t:4} {len(years):>2} years  {sum(totals.values()):>6,} claims  "
              f"-> coverage_chart_{t}.svg, coverage_bands_{t}.json")
    (HERE / "coverage_summary.json").write_text(
        json.dumps(filer_summary(), indent=2), encoding="utf-8")
    print("  wrote coverage_summary.json")
    comp = comparison_svg()
    (HERE / "coverage_comparison.svg").write_text(comp, encoding="utf-8")
    print(f"  wrote coverage_comparison.svg ({len(comp):,} bytes)")
