"""One concept, fifteen years: what was filed, what was written, what changed.

    .venv/bin/python stage3_panel.py

Reads join.jsonl, writes stage3_panel.svg. Stage 3.


WHAT THIS CAN AND CANNOT DRAW

The plan was two lines per concept — the filed figure and the figure management
wrote — diverging where they disagree. Stage 2 produced 61 verified joins across
3,915 claims, so for any single concept there are a handful of MD&A points and
not a series. Drawing a line through four dots spread over fifteen years would
be inventing data.

So the filed value is a line, because it is complete, and MD&A appears as
individual points where a claim actually verified. Gaps stay gaps. A reader can
see immediately that the prose side is sparse, which is the honest impression.

Two things are marked on the same axis because they are the finding:

**Restatement** — a year whose filed figure changed between filings. Every annual
report republishes prior years and the values can move.

**The end of a concept** — where a tag stops being filed. Goldman abandoned
PrincipalTransactionsRevenue after 2010; JPMorgan abandoned
FinancingReceivableAllowanceForCreditLosses after 2021, at the CECL changeover.
Both still return HTTP 200 with their historical data, so a dead tag is
indistinguishable from a live one until you look at which years it covers.

That refines the principle week 5 was built on. "Store the route, not the
answer" — but routes expire too, and unlike a stale figure a dead tag fails
silently.


FORM

Small multiples, one panel per concept, rather than eight series on shared axes.
The concepts differ by orders of magnitude, so a shared y-scale would flatten
most of them into the baseline and a second y-axis is never the answer. Each
panel carries its own scale, labelled.
"""

from __future__ import annotations

import json
from pathlib import Path

import prepare_evidence as pe

HERE = Path(__file__).parent
YEARS = list(range(2011, 2026))

# Concepts with verified MD&A joins in more than one year — the only ones where
# a panel shows anything the filed line does not already say.
CONCEPTS = [
    ("NetCashProvidedByUsedInOperatingActivities", "Cash from operating activities"),
    ("NetCashProvidedByUsedInFinancingActivities", "Cash from financing activities"),
    ("InvestmentBankingRevenue", "Investment banking revenue"),
    ("DividendsPreferredStock", "Preferred stock dividends"),
    ("StockRepurchaseProgramAuthorizedAmount1", "Buyback authorised"),
    ("TierOneRiskBasedCapital", "Tier 1 capital"),
]

INK, MUTED, RULE = "#0b0b0b", "#52514e", "#e3e3e0"
FILED, PROSE, ALERT = "#2a78d6", "#eb6834", "#b3261e"


def series(tag: str) -> dict[int, dict]:
    usd = pe.company_facts("JPM")["facts"]["us-gaap"].get(tag, {}).get("units", {}).get("USD")
    out = {}
    if not usd:
        return out
    for fy in YEARS:
        entries = [e for e in usd if e.get("end", "").startswith(str(fy))]
        v = pe.annual_value(usd, fy)
        if not v:
            continue
        vals = {e["val"] for e in entries
                if pe.annual_value([e], fy)}
        out[fy] = {"value": v["value"], "restated": len(vals) > 1}
    return out


def prose_points() -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {}
    for line in (HERE / "join.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        # Levels only. A verified CHANGE was matched against a year-over-year
        # difference, so its value belongs on a difference axis, not a level
        # one — plotting it here put a correct match visibly off the line and
        # would read as a disagreement that is not there.
        if r["bucket"] == "verified" and not r["is_change"]:
            out.setdefault(r["matched_tag"], {})[r["fiscal_year"]] = r["claimed"]
    return out


def panel(tag: str, title: str, filed: dict, prose: dict, x: float, y: float,
          w: float, h: float) -> str:
    p = [f'<text x="{x}" y="{y-24}" class="pt">{title}</text>']
    if not filed:
        return "".join(p) + f'<text x="{x}" y="{y+h/2}" class="pm">not filed</text>'

    vals = [d["value"] for d in filed.values()] + list(prose.values())
    lo, hi = min(vals + [0]), max(vals)
    span = (hi - lo) or 1
    px = lambda fy: x + (fy - YEARS[0]) / (len(YEARS) - 1) * w
    py = lambda v: y + h - (v - lo) / span * h

    p.append(f'<line x1="{x}" y1="{y+h:.1f}" x2="{x+w}" y2="{y+h:.1f}" class="ax"/>')
    p.append(f'<text x="{x}" y="{y-8}" class="pm">peak ${hi/1e9:,.1f}B</text>')

    pts = [(px(fy), py(d["value"])) for fy, d in sorted(filed.items())]
    # Break the line where a year is missing: a concept that stopped being filed
    # must read as stopping, not as a straight segment across the gap.
    runs, run, prev = [], [], None
    for fy, d in sorted(filed.items()):
        if prev is not None and fy - prev > 1:
            runs.append(run); run = []
        run.append((px(fy), py(d["value"]))); prev = fy
    runs.append(run)
    for r in runs:
        if len(r) > 1:
            p.append('<polyline points="' + " ".join(f"{a:.1f},{b:.1f}" for a, b in r)
                     + '" class="ln"/>')
    for fy, d in sorted(filed.items()):
        cls = "dr" if d["restated"] else "df"
        p.append(f'<circle cx="{px(fy):.1f}" cy="{py(d["value"]):.1f}" r="{3.4 if d["restated"] else 2.4}" '
                 f'class="{cls}"><title>FY{fy} filed ${d["value"]/1e9:,.2f}B'
                 f'{" — RESTATED" if d["restated"] else ""}</title></circle>')
    for fy, v in sorted(prose.items()):
        if fy in filed:
            p.append(f'<rect x="{px(fy)-3.6:.1f}" y="{py(v)-3.6:.1f}" width="7.2" height="7.2" '
                     f'rx="1.4" class="dp"><title>FY{fy} MD&amp;A stated ${v/1e9:,.2f}B</title></rect>')

    last = max(filed)
    if last < YEARS[-1]:
        p.append(f'<line x1="{px(last):.1f}" y1="{y}" x2="{px(last):.1f}" y2="{y+h}" class="dead"/>')
        p.append(f'<text x="{px(last)+4:.1f}" y="{y+11}" class="pa">last filed {last}</text>')
    return "".join(p)


def main() -> None:
    prose = prose_points()
    W, H = 940, 590
    cols, cw, ch = 2, 380, 118
    parts = []
    for i, (tag, title) in enumerate(CONCEPTS):
        x = 60 + (i % cols) * (cw + 90)
        y = 122 + (i // cols) * (ch + 78)
        parts.append(panel(tag, title, series(tag), prose.get(tag, {}), x, y, cw, ch))
        for fy in (2011, 2015, 2020, 2025):
            px = x + (fy - YEARS[0]) / (len(YEARS) - 1) * cw
            parts.append(f'<text x="{px:.1f}" y="{y+ch+15}" text-anchor="middle" class="pm">{fy}</text>')

    legend = (
        f'<circle cx="62" cy="62" r="2.4" class="df"/><text x="72" y="66" class="pm">filed each year</text>'
        f'<circle cx="196" cy="62" r="3.4" class="dr"/><text x="206" y="66" class="pm">restated</text>'
        f'<rect x="290" y="58.5" width="7.2" height="7.2" rx="1.4" class="dp"/>'
        f'<text x="304" y="66" class="pm">stated in MD&amp;A, and verified</text>'
        f'<line x1="530" y1="55" x2="530" y2="69" class="dead"/>'
        f'<text x="538" y="66" class="pm">concept stops being filed</text>')

    svg = f'''<svg viewBox="0 0 {W} {H}" width="100%" role="img"
 aria-label="Six small-multiple panels, JPMorgan 2011 to 2025. Each shows a concept's
 filed value each year as a line, restated years as larger dots, and the few years where
 an MD&amp;A claim was verified against it as squares."
 xmlns="http://www.w3.org/2000/svg"><style>
 .ln{{fill:none;stroke:{FILED};stroke-width:1.6}} .df{{fill:{FILED}}}
 .dr{{fill:none;stroke:{ALERT};stroke-width:1.8}} .dp{{fill:{PROSE}}}
 .ax{{stroke:{RULE};stroke-width:1}} .dead{{stroke:{MUTED};stroke-width:1;stroke-dasharray:2 2}}
 .pt{{font:600 12.5px ui-sans-serif,system-ui,sans-serif;fill:{INK}}}
 .pm{{font:10.5px ui-sans-serif,system-ui,sans-serif;fill:{MUTED};font-variant-numeric:tabular-nums}}
 .pa{{font:9.5px ui-sans-serif,system-ui,sans-serif;fill:{MUTED}}}
 .h1{{font:600 15px ui-sans-serif,system-ui,sans-serif;fill:{INK}}}
 .h2{{font:11.5px ui-sans-serif,system-ui,sans-serif;fill:{MUTED}}}
 @media (prefers-color-scheme:dark){{
   .pt,.h1{{fill:#fff}} .pm,.h2,.pa{{fill:#c3c2b7}} .ax{{stroke:#2f2f2d}}
   .ln,.df{{stroke:#3987e5;fill:#3987e5}} .ln{{fill:none}}
   .dp{{fill:#d95926}} .dr{{stroke:#f0776a}} .dead{{stroke:#7c8794}}
 }}</style>
 <text x="60" y="30" class="h1">JPMorgan concepts, 2011&#8211;2025: what was filed, and where the prose could be checked against it</text>
 <text x="60" y="46" class="h2">The filed line is complete. MD&#38;A appears only where a claim verified &#8212; 61 of 3,915 claims. Gaps are gaps.</text>
 {legend}{"".join(parts)}</svg>'''
    (HERE / "stage3_panel.svg").write_text(svg, encoding="utf-8")
    print(f"wrote stage3_panel.svg ({len(svg):,} bytes)")
    for tag, title in CONCEPTS:
        s = series(tag); pr = prose.get(tag, {})
        rs = [fy for fy, d in s.items() if d["restated"]]
        print(f"  {title:34s} filed {min(s) if s else '-'}-{max(s) if s else '-'}  "
              f"MD&A points {len(pr):>2}  restated {rs if rs else '-'}")


if __name__ == "__main__":
    main()
