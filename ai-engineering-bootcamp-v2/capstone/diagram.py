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
