"""The comparison diagram for the How it works tab.

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


def comparison_svg() -> str:
    L, R = 30, 470          # left and right column origins
    W = 380                 # column width
    parts = []

    # --- headings
    parts.append(
        f'<text x="{L + W / 2}" y="26" text-anchor="middle" font-size="15" '
        f'font-weight="700" fill="currentColor">Asking a general model</text>'
    )
    parts.append(
        f'<text x="{R + W / 2}" y="26" text-anchor="middle" font-size="15" '
        f'font-weight="700" fill="{ACCENT}">This tool</text>'
    )

    # --- left: one path, narrowing to an answer
    parts.append(_box(L, 50, W, 46, "A question about the filing"))
    parts.append(_arrow(L + W / 2, 96, L + W / 2, 128))
    parts.append(_box(L, 130, W, 62, "It reads or recalls the document",
                      ["the written story and the audited figures",
                       "arrive together, as one text"]))
    parts.append(_arrow(L + W / 2, 192, L + W / 2, 224))
    parts.append(_box(L, 226, W, 62, "One blended impression",
                      ["nothing holds the two apart, so a",
                       "disagreement between them is invisible"]))
    parts.append(_arrow(L + W / 2, 288, L + W / 2, 320))
    parts.append(_box(L, 322, W, 66, "An answer", [
        "often the right number —",
        "with no source, or one it composed"], colour=WARN))

    # --- right: two sources, compared
    parts.append(_box(R, 50, W, 46, "The 10-K filing"))
    parts.append(_arrow(R + W / 2, 96, R + 100, 126))
    parts.append(_arrow(R + W / 2, 96, R + W - 100, 126))
    parts.append(_box(R, 128, 180, 64, "The written story",
                      ["management explaining", "UNAUDITED"]))
    parts.append(_box(R + 200, 128, 180, 64, "The filed figures",
                      ["tagged XBRL data", "AUDITED"]))
    parts.append(_arrow(R + 90, 192, R + W / 2 - 20, 222, ACCENT))
    parts.append(_arrow(R + 290, 192, R + W / 2 + 20, 222, ACCENT))
    parts.append(_box(R, 224, W, 46, "Compare them", colour=ACCENT))
    parts.append(_arrow(R + W / 2, 270, R + W / 2, 300, ACCENT))
    parts.append(_box(R, 302, W, 60, "A verdict, and where it came from",
                      ["the exact XBRL tag, the figure, the SEC link"], colour=ACCENT))
    parts.append(_arrow(R + W / 2, 362, R + W / 2, 392, GOOD))
    parts.append(_box(R, 394, W, 60, "Trust layer",
                      ["show it, or route it to a person"], colour=GOOD, dash=''))

    # --- the line that makes the point
    parts.append(
        f'<text x="{L + W / 2}" y="418" text-anchor="middle" font-size="12.5" '
        f'font-style="italic" fill="currentColor" opacity="0.85">'
        f'answers “what does the filing say?”</text>'
    )
    parts.append(
        f'<text x="{R + W / 2}" y="478" text-anchor="middle" font-size="12.5" '
        f'font-style="italic" fill="{ACCENT}">'
        f'answers “is what it says backed by what it filed?”</text>'
    )

    return (
        '<svg viewBox="0 0 880 500" width="100%" role="img" '
        'aria-label="A general model reads a filing as one blended text and '
        'produces an answer without a verifiable source. This tool holds the '
        'unaudited written story and the audited filed figures apart, compares '
        'them, reports the exact tag it used, and decides whether the verdict '
        'is trustworthy enough to show without human review." '
        'xmlns="http://www.w3.org/2000/svg">'
        f'<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{STROKE}"/></marker></defs>'
        + "".join(parts)
        + "</svg>"
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


def pipeline_svg() -> str:
    W, H = 960, 660
    p = []

    p.append(f'<text x="30" y="24" font-size="15" font-weight="700" '
             f'fill="currentColor">One filing, two layers, four stages</text>')
    p.append(f'<text x="30" y="43" font-size="11.5" fill="currentColor" opacity=".7">'
             f'Numbers are measured on JPMorgan Chase, Item 7, fiscal years 2011–2025.</text>')

    # --- the two layers
    p.append(f'<rect x="30" y="62" width="900" height="74" rx="7" fill="none" '
             f'stroke="{STROKE}" stroke-width="1" stroke-dasharray="3 3"/>')
    p.append(f'<text x="42" y="80" font-size="10.5" font-weight="600" '
             f'letter-spacing=".1em" fill="currentColor" opacity=".6">THE 10-K</text>')
    p.append(_box(52, 88, 400, 40, "Item 7 — the written story",
                  ["management explaining the year · UNAUDITED"], WARN))
    p.append(_box(508, 88, 400, 40, "XBRL — the filed figures",
                  ["825 tagged concepts per year · AUDITED"], GOOD))

    # --- stage 1
    p.append(_flow(252, 128, 252, 168, WARN))
    p.append(_stage(52, 170, 400, 92, "1", "Extract", [
        "split Item 7 into sentences, keep the numeric ones",
        "one claim per figure — a sentence with three numbers",
        "makes three claims, because one verdict cannot cover three",
        "6,655 claims across 15 filings"]))

    # --- stage 2, the ordering that matters
    p.append(_flow(252, 262, 252, 302, ACCENT))
    p.append(_flow(708, 128, 708, 302, GOOD, delay=0.4))
    p.append(_stage(52, 304, 400, 92, "2", "Retrieve — before seeing the figure", [
        "embed the sentence · embed all 825 tag definitions",
        "keep every concept above cosine 0.55",
        "usually none, sometimes one or two",
        "the threshold is set from a null, not chosen"], ACCENT))
    p.append(f'<text x="470" y="330" font-size="10.5" font-weight="600" fill="{ACCENT}">'
             f'the number is</text>')
    p.append(f'<text x="470" y="344" font-size="10.5" font-weight="600" fill="{ACCENT}">'
             f'withheld here</text>')
    p.append(f'<text x="470" y="362" font-size="10" fill="currentColor" opacity=".65">'
             f'so the comparison that</text>')
    p.append(f'<text x="470" y="375" font-size="10" fill="currentColor" opacity=".65">'
             f'follows is a test, not</text>')
    p.append(f'<text x="470" y="388" font-size="10" fill="currentColor" opacity=".65">'
             f'a selection effect</text>')

    # --- stage 3
    p.append(_flow(252, 396, 252, 436, ACCENT))
    p.append(_flow(708, 302, 300, 436, GOOD, delay=0.8, bend=(700, 420)))
    p.append(_stage(52, 438, 400, 78, "3", "Compare — like with like", [
        "a level against a filed total",
        "a change against the difference between two years",
        "“increased by $1.6bn” is not a total and never matches one"]))

    # --- stage 4 and the outcomes
    p.append(_flow(252, 516, 252, 552, ACCENT))
    p.append(_stage(52, 554, 400, 74, "4", "Decide", [
        "verified 61 · review 713 · no counterpart 3,141",
        "80% abstention, because most figures in prose are",
        "changes, subtotals or segment-level and are not filed"], GOOD))

    # --- the outcomes, spelled out
    # Drawn shapes rather than emoji: the question-mark glyph fell back to a
    # tofu box in rendering, and a legend that depends on a font having a
    # character is a legend that breaks silently on someone else's machine.
    outs = [("Verified", "a filed figure matches", GOOD, 560),
            ("Review", "concept found, figure differs", WARN, 596),
            ("No counterpart", "nothing filed resembles it", STROKE, 632)]
    for name, sub, colour, y in outs:
        p.append(f'<circle cx="506" cy="{y-4}" r="5" fill="{colour}"/>')
        p.append(f'<text x="520" y="{y}" font-size="12" font-weight="600" '
                 f'fill="{colour}">{name}</text>')
        p.append(f'<text x="620" y="{y}" font-size="11" fill="currentColor" '
                 f'opacity=".7">{sub}</text>')
    p.append(_flow(452, 590, 494, 590, GOOD, delay=1.2))

    return (
        f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
        'aria-label="The pipeline in four stages. A 10-K holds an unaudited written '
        'story and audited tagged figures. Stage one extracts one claim per figure. '
        'Stage two retrieves candidate concepts from the sentence alone, before the '
        'figure is consulted. Stage three compares levels against totals and changes '
        'against differences. Stage four sorts the result into verified, review, or '
        'no counterpart, abstaining on 80 percent." '
        'xmlns="http://www.w3.org/2000/svg">'
        f'<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{STROKE}"/></marker></defs>'
        '<style>'
        '.flow{stroke-dashoffset:200;animation:dash 3.2s linear infinite}'
        '@keyframes dash{to{stroke-dashoffset:0}}'
        '@media (prefers-reduced-motion:reduce){.flow{animation:none;opacity:0}}'
        '</style>'
        + "".join(p) + '</svg>'
    )
