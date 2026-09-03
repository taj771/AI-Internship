"""The pipeline diagram for the What we built tab.

Kept in its own file because it is a drawing, not logic, and mixing four hundred
characters of SVG path data into app.py makes the page hard to read for no gain.


WHAT THE PICTURE HAS TO SAY

One thing: a general model has a single input, and this tool has two that get
compared against each other.

That is the whole argument. A model asked about a filing reads the document —
management's prose and the audited figures together — and forms one blended
impression. It can tell you what the filing says. If the prose and the figures
disagree, it has no way to notice, because it never held them apart.

So the left column narrows to a point and the right column forks and rejoins.
Anything else in the drawing is labelling.


COLOURS

Text is `currentColor` so it inherits whatever Streamlit's theme sets, and the
boxes are drawn with a mid grey and a translucent fill that reads on both a
white and a near-black background. A palette that only works in light mode is
half a diagram.
"""

STROKE = "#8b949e"
ACCENT = "#0969da"
WARN = "#bc4c00"
GOOD = "#1a7f37"
FILL = "rgba(127,127,127,0.10)"


def _box(x, y, w, h, label, sub=None, colour=STROKE, dash=""):
    out = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" '
        f'fill="{FILL}" stroke="{colour}" stroke-width="1.5" {dash}/>'
        f'<text x="{x + w / 2}" y="{y + (20 if sub else h / 2 + 5)}" '
        f'text-anchor="middle" font-size="13.5" font-weight="600" '
        f'fill="currentColor">{label}</text>'
    )
    if sub:
        for i, line in enumerate(sub):
            out += (
                f'<text x="{x + w / 2}" y="{y + 38 + i * 15}" text-anchor="middle" '
                f'font-size="11.5" fill="currentColor" opacity="0.72">{line}</text>'
            )
    return out


def _arrow(x1, y1, x2, y2, colour=STROKE):
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{colour}" '
        f'stroke-width="1.5" marker-end="url(#head)"/>'
    )


# --- the pipeline, in detail -------------------------------------------------
#
# The comparison diagram above argues one point: a model has one input and this
# has two that get compared. This one answers the next question a reader asks —
# how — and carries the measured numbers at each stage so the picture is also
# the result.
#
# Flow is animated rather than static because the interesting property is the
# ORDER: retrieval happens before the figure is consulted, and that ordering is
# the difference between a measurement and a selection effect. A still diagram
# has to caption that; a moving one shows it. Disabled under
# prefers-reduced-motion, where the dashes simply stop.


def _flow(x1, y1, x2, y2, colour=ACCENT, delay=0.0, bend=None):
    """A connector that shows direction by moving along itself."""
    d = (f"M {x1} {y1} L {x2} {y2}" if bend is None
         else f"M {x1} {y1} Q {bend[0]} {bend[1]} {x2} {y2}")
    return (
        f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="1.6" '
        f'marker-end="url(#head)" opacity=".55"/>'
        f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2.2" '
        f'stroke-linecap="round" stroke-dasharray="5 15" class="flow" '
        f'style="animation-delay:{delay}s"/>'
    )


def _stage(x, y, w, h, n, title, lines, colour=ACCENT):
    out = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{FILL}" '
        f'stroke="{colour}" stroke-width="1.5"/>'
        f'<circle cx="{x + 17}" cy="{y + 17}" r="9.5" fill="{colour}"/>'
        f'<text x="{x + 17}" y="{y + 21}" text-anchor="middle" font-size="11" '
        f'font-weight="700" fill="#fff">{n}</text>'
        f'<text x="{x + 34}" y="{y + 21}" font-size="13" font-weight="600" '
        f'fill="currentColor">{title}</text>'
    )
    for i, line in enumerate(lines):
        out += (f'<text x="{x + 14}" y="{y + 40 + i * 14}" font-size="11" '
                f'fill="currentColor" opacity=".72">{line}</text>')
    return out


def _tool(x, y, w, h, label, lines, colour=STROKE, dim=False):
    """What actually does the work at a stage. Drawn to the right of it."""
    op = ".45" if dim else "1"
    out = (f'<g opacity="{op}">'
           f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="none" '
           f'stroke="{colour}" stroke-width="1.2" stroke-dasharray="4 3"/>'
           f'<text x="{x + 12}" y="{y + 18}" font-size="10.5" font-weight="700" '
           f'letter-spacing=".07em" fill="{colour}">{label}</text>')
    for i, line in enumerate(lines):
        out += (f'<text x="{x + 12}" y="{y + 35 + i * 13}" font-size="10.5" '
                f'fill="currentColor" opacity=".78">{line}</text>')
    return out + "</g>"


def pipeline_svg() -> str:
    """The pipeline, with the machinery named at every stage.

    An architecture diagram that only shows boxes labelled "extract" and
    "compare" tells a reader nothing they could not have guessed. What is worth
    drawing is which component does the work, because that is where the design
    decisions are: a deterministic splitter rather than a model, embeddings over
    tag definitions rather than over the filing text, a live API call rather
    than a cache, and a memory layer switched off on purpose.

    Two things are drawn dimmed rather than omitted. Memory exists and is
    disabled for this study, because an agent that learns from claim 3 makes
    claim 40 non-independent and a per-claim number stops meaning anything. The
    agent is optional — the pipeline runs without it, and what it adds is
    measured rather than assumed. Leaving either out would make the picture
    tidier than the system.
    """
    W, H = 1000, 700
    SX, SW = 34, 430          # stage column
    TX, TW = 512, 454         # tool column
    p = []

    p.append('<text x="34" y="24" font-size="15" font-weight="700" '
             'fill="currentColor">What runs at each stage, and what does the work</text>')
    p.append('<text x="34" y="43" font-size="11.5" fill="currentColor" opacity=".7">'
             'JPMorgan Chase, Item 7, FY2011&#8211;2025. Every number below is measured.</text>')
    p.append(f'<text x="{SX}" y="66" font-size="10" font-weight="700" letter-spacing=".1em" '
             f'fill="currentColor" opacity=".5">STAGE</text>')
    p.append(f'<text x="{TX}" y="66" font-size="10" font-weight="700" letter-spacing=".1em" '
             f'fill="currentColor" opacity=".5">WHAT DOES IT</text>')

    # --- the two layers of the filing. Height 52, not 38: _box puts the title
    # at y+20 and the subtitle at y+38, so a 38-tall box lands the subtitle on
    # its own border.
    p.append(_box(SX, 76, SW, 52, "Item 7 &#8212; the written story",
                  ["management explaining the year &#183; UNAUDITED"], WARN))
    p.append(_tool(TX, 76, TW, 52, "TWO SOURCES, ONE FILING", [
        "prose: fetch_history.py &#183; 15 filings from EDGAR archives",
        "figures: 825 tagged concepts a year, audited &#183; used at stage 3"], WARN))

    # 1 EXTRACT
    p.append(_flow(SX + 60, 128, SX + 60, 140, WARN))
    p.append(_stage(SX, 142, SW, 84, "1", "Extract", [
        "sentences &#8594; one claim per figure &#183; 8 types &#183; tables rejected",
        "section from the nearest preceding header",
        "6,655 claims across 15 filings"]))
    p.append(_tool(TX, 142, TW, 84, "NO MODEL &#8212; DETERMINISTIC", [
        "regex splitter, IDF-typed, per-filer segment names",
        "same input, same output, every time",
        "extract.py &#183; coverage.py"]))

    # 2 RETRIEVE
    p.append(_flow(SX + 60, 226, SX + 60, 252, ACCENT))
    p.append(_stage(SX, 254, SW, 92, "2", "Retrieve &#8212; before seeing the figure", [
        "embed the sentence, compare against every tag definition",
        "keep concepts above cosine 0.55 &#183; usually none, sometimes two",
        "the number is withheld until stage 3"], ACCENT))
    p.append(_tool(TX, 254, TW, 92, "EMBEDDINGS", [
        "text-embedding-3-small &#183; 825 tag definitions, embedded once",
        "cosine over a 1536-dim matrix &#8212; no vector DB at this size",
        "threshold set from a perturbation null, not chosen",
        "embed_tags.py &#183; join.py"], ACCENT))

    # 3 COMPARE
    p.append(_flow(SX + 60, 346, SX + 60, 372, ACCENT))
    p.append(_stage(SX, 374, SW, 84, "3", "Compare &#8212; like with like", [
        "a level against a filed total",
        "a change against the difference between two years",
        "&#8220;increased by $1.6bn&#8221; is not a total and never matches one"]))
    p.append(_tool(TX, 374, TW, 84, "LIVE SEC LOOKUP", [
        "data.sec.gov companyconcept &#183; fetched every run, never cached",
        "restatement detected when filings disagree",
        "sec_tool.py"], GOOD))

    # 4 DECIDE
    p.append(_flow(SX + 60, 458, SX + 60, 484, GOOD))
    p.append(_stage(SX, 486, SW, 84, "4", "Decide", [
        "verified 61 &#183; review 713 &#183; no counterpart 3,141",
        "80% abstention &#8212; most figures in prose are changes,",
        "subtotals or segment-level, and are not filed at all"], GOOD))
    p.append(_tool(TX, 486, TW, 84, "THREE BUCKETS", [
        "verified &#8212; a filed figure matches",
        "review &#8212; concept found, figure differs &#183; a queue for a person",
        "no counterpart &#8212; nothing filed resembles it"], GOOD))

    # --- memory, present and switched off
    p.append(_tool(SX, 590, SW, 84, "MEMORY &#8212; BUILT, THEN SWITCHED OFF", [
        "stores where to look (the XBRL tag), never what was found",
        "a cached figure goes stale silently when a filing is restated",
        "off here so runs stay independent and a per-claim number means",
        "something &#183; memory.py, memory_gate.py"], WARN, dim=True))

    # --- the agent, optional
    p.append(_tool(TX, 590, TW, 84, "AGENT &#8212; OPTIONAL, MEASURED", [
        "Google ADK + gpt-4o with the SEC lookup tool",
        "alone with the tool: 28 of 40 concepts identified",
        "handed stage 2&#8217;s shortlist: 36 of 40 &#8212; an upper bound",
        "agent.py &#183; stage4_grid.py"], ACCENT, dim=True))
    p.append(_flow(SX + 300, 570, SX + 300, 588, WARN, delay=1.4))
    p.append(_flow(TX + 200, 570, TX + 200, 588, ACCENT, delay=1.6))

    return (
        f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
        'aria-label="Four pipeline stages down the left, and what performs each on the '
        'right. Extraction is deterministic with no model. Retrieval embeds the sentence '
        'against 825 tag definitions and keeps concepts above a null-calibrated cosine of '
        '0.55, before the figure is consulted. Comparison fetches filed values live from '
        'data.sec.gov and compares levels against totals and changes against differences. '
        'The result is sorted into verified, review, or no counterpart, abstaining on 80 '
        'percent. A memory layer and an LLM agent are shown dimmed: both exist, memory is '
        'switched off so runs stay independent, and the agent is optional." '
        'xmlns="http://www.w3.org/2000/svg">'
        f'<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{STROKE}"/></marker></defs>'
        '<style>'
        '.flow{stroke-dashoffset:200;animation:dash 3.2s linear infinite}'
        '@keyframes dash{to{stroke-dashoffset:0}}'
        '@media (prefers-reduced-motion:reduce){.flow{animation:none;opacity:0}}'
        '</style>' + "".join(p) + '</svg>')
