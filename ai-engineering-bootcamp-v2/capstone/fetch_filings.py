"""Download bank 10-K filings from SEC EDGAR and extract the MD&A section.

Why MD&A and not the whole filing
---------------------------------
A large-bank 10-K runs 400-600 pages. Most of it is risk-factor boilerplate,
exhibit indexes, and audited statement tables that repeat near-identical
language across every filer and every year. Indexing all of it does not make
the RAG system smarter; it makes retrieval worse, because near-duplicate
boilerplate chunks compete with the one chunk that actually answers the
question. (This is the same failure the live-session notebook demonstrates
with duplicate chunks, arriving by a different road.)

Item 7 -- Management's Discussion and Analysis -- is the section where
management asserts numbers *in prose*: "net interest income increased 12% to
$92.4 billion". That is exactly the claim shape the capstone auditor has to
check against XBRL, so scoping the corpus here means Week 2 retrieval work
carries directly into the capstone rather than being thrown away.

EDGAR access rules
------------------
The SEC requires a declared User-Agent with a real contact address and asks for
no more than 10 requests/second. We are well under that, but the header is
mandatory -- without it EDGAR returns 403, not a rate-limit message, which is
easy to misread as a bad URL.

Usage
-----
    .venv/bin/python fetch_filings.py            # all banks, latest 10-K
    .venv/bin/python fetch_filings.py --years 2  # last 2 annual filings each
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import warnings
from pathlib import Path

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# Inline-XBRL filings open with an XML declaration, so bs4 warns that we handed
# an XML document to an HTML parser. That is deliberate: we want the HTML
# parser's leniency, and we only ever call get_text(). The warning is noise.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

DATA_DIR = Path(__file__).parent / "data"

# SEC asks that automated clients identify themselves with a contact address.
USER_AGENT = "AI-Certificate coursework taj.aravinda@gmail.com"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

# Politeness delay between EDGAR requests. The published ceiling is 10 req/sec;
# we sit far below it because nothing here is latency-sensitive and a throttled
# IP costs far more time than the sleeps do.
REQUEST_DELAY_SEC = 0.5

# How many documents from one filing to try before giving up. The annual-report
# exhibit is the largest non-XBRL document in every filing checked, so it is
# reached on the second attempt; the cap exists so a filer with an unfamiliar
# layout costs a few wasted downloads rather than a few hundred.
MAX_DOCUMENTS_PER_FILING = 4

# Four large US banks with materially different business mixes -- a universal
# bank, two commercial-heavy peers, and a markets-heavy one. The mix matters:
# if every filer were a retail bank, a question like "who has the largest
# trading book" would have no discriminating answer in the corpus, and the
# retrieval evaluation would not be able to tell precision from luck.
BANKS = [
    {"ticker": "JPM", "name": "JPMorgan Chase", "cik": "0000019617"},
    {"ticker": "BAC", "name": "Bank of America", "cik": "0000070858"},
    {"ticker": "WFC", "name": "Wells Fargo", "cik": "0000072971"},
    {"ticker": "GS", "name": "Goldman Sachs", "cik": "0000886982"},
]


def get_json(url: str) -> dict:
    time.sleep(REQUEST_DELAY_SEC)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_text(url: str) -> str:
    time.sleep(REQUEST_DELAY_SEC)
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.text


def find_10k_filings(cik: str, limit: int) -> list[dict]:
    """Return the most recent `limit` 10-K filings for a CIK.

    The submissions endpoint returns filings as a struct-of-arrays (a dict of
    parallel lists) rather than a list of records, so we zip them back into
    per-filing dicts before filtering.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    data = get_json(url)
    recent = data["filings"]["recent"]

    filings = []
    for form, acc, doc, date, period in zip(
        recent["form"],
        recent["accessionNumber"],
        recent["primaryDocument"],
        recent["filingDate"],
        # The period the filing *covers*, which is the year a reader means by
        # "the 2025 10-K". Banks file in February, so labelling by filing date
        # would tag every FY2025 filing as 2026 and make every citation wrong.
        recent["reportDate"],
    ):
        # Exact match only: "10-K/A" is an amendment and "10-K405" is a legacy
        # variant, and mixing them in would put two versions of the same year's
        # MD&A in the corpus -- the duplicate-chunk problem again.
        if form != "10-K":
            continue
        filings.append(
            {
                "accession": acc.replace("-", ""),
                "document": doc,
                "filing_date": date,
                "period_end": period,
            }
        )
        if len(filings) >= limit:
            break
    return filings


def list_filing_documents(cik_short: str, accession: str) -> list[dict]:
    """Return the filing's HTML documents, largest first.

    Used only when the primary document turns out not to contain the MD&A. Two
    families of file are filtered out first:

    - `R<n>.htm` -- hundreds of them, generated by EDGAR's XBRL viewer. Each is
      one rendered table from the financial statements, so they are numerous,
      large enough to outrank the real exhibit on size, and contain no prose.
    - `...ex<digits>...` -- the numbered exhibits (material contracts, subsidiary
      lists, certifications). The annual-report exhibit that carries the MD&A is
      conventionally EX-13 but is *not* named that way in the file listing, so
      we exclude the ones we can identify and rank what remains by size.
    """
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_short}/{accession}/index.json"
    items = get_json(url)["directory"]["item"]

    xbrl_viewer = re.compile(r"^R\d+\.htm$", re.IGNORECASE)
    numbered_exhibit = re.compile(r"ex\d", re.IGNORECASE)

    docs = [
        {"name": it["name"], "size": int(it["size"])}
        for it in items
        if it["name"].lower().endswith(".htm")
        and not xbrl_viewer.match(it["name"])
        and not numbered_exhibit.search(it["name"])
    ]
    return sorted(docs, key=lambda d: d["size"], reverse=True)


def html_to_text(html: str) -> str:
    """Strip an EDGAR HTML filing down to readable prose.

    EDGAR filings are HTML with inline XBRL tags wrapped around most numbers.
    BeautifulSoup's get_text() keeps the number and drops the tag, which is what
    we want: the prose claim "net income of $49.6 billion" survives intact.
    """
    soup = BeautifulSoup(html, "lxml")

    # Script/style never contain filing prose; <ix:header> holds XBRL metadata
    # that would otherwise show up as a wall of context-ref junk at the top.
    for tag in soup(["script", "style"]):
        tag.decompose()

    text = soup.get_text(separator="\n")

    # Filings are laid out in tables, so raw extraction yields long runs of
    # blank lines and non-breaking spaces. Collapse them or the text splitter
    # will spend its chunk budget on whitespace.
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


# Below this, whatever we sliced is a cross-reference stub or a table-of-
# contents row, not a real section. Every large-bank MD&A tested runs 300k+
# characters, so the threshold is not close to a real boundary case.
MIN_MDNA_CHARS = 20_000

# Where the MD&A can begin. Three forms, because the four banks in BANKS use
# three different ones -- which is the whole reason this function is not two
# lines long:
#
#   1. "Item 7. Management's Discussion and Analysis..." with the body directly
#      underneath. The textbook form (Bank of America, Goldman Sachs).
#   2. A bare "Management's discussion and analysis" heading, because Item 7 is
#      only a page cross-reference ("appears on pages 46-160"). JPMorgan does
#      this -- so keying on "Item 7" alone finds the pointer, not the prose.
#   3. "Financial Review" -- Wells Fargo's Item 7 incorporates by reference to
#      a section of the Annual Report that is not called MD&A at all.
#
# Forms 2 and 3 are anchored to start-of-line so that the thousands of mid-
# sentence cross-references ("Refer to Management's discussion and analysis on
# page 97") cannot masquerade as section starts.
START_PATTERNS = [
    re.compile(r"item\s*7\s*[.:\-—]?\s*management[’']s discussion", re.IGNORECASE),
    re.compile(r"(?m)^\s*management[’']s discussion and analysis", re.IGNORECASE),
    re.compile(r"(?m)^\s*financial review\s*$", re.IGNORECASE),
]

# Where it can end: the next 10-K item, or -- for filers whose MD&A sits inside
# an annual-report exhibit with no Item numbering at all -- the auditor's report
# that opens the audited financial statements.
END_PATTERNS = [
    re.compile(r"(?m)^\s*item\s*7a\s*[.:\-—]?\s*\n?\s*quantitative", re.IGNORECASE),
    re.compile(r"(?m)^\s*item\s*8\s*[.:\-—]?\s*\n?\s*financial\s+statements", re.IGNORECASE),
    re.compile(
        r"(?m)^\s*report of independent registered public accounting firm",
        re.IGNORECASE,
    ),
]


# A heading is a real section start only if body prose follows it. html_to_text
# emits one line per source paragraph, so a genuine paragraph arrives as a very
# long line while table-of-contents rows, page numbers, and index entries arrive
# as a run of short ones. 300 characters is comfortably above the longest TOC row
# seen (~90) and below the shortest opening paragraph seen (~450).
PROSE_LINE_CHARS = 300
PROSE_WINDOW_CHARS = 1500

# A heading text repeated at least this many times is a per-page running header,
# not a section start. The banks tested repeat theirs 40-50 times; nothing else
# in a filing repeats a full heading more than a handful of times.
RUNNING_HEADER_MIN_REPEATS = 5


def _heading_line(text: str, pos: int) -> str:
    """The single line the match sits on."""
    newline = text.find("\n", pos)
    return text[pos:] if newline == -1 else text[pos:newline]


def _normalize(line: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", line.lower()).strip()


def _followed_by_prose(text: str, pos: int, relaxed: bool = False) -> bool:
    """True if body text, not an index, follows the heading at `pos`."""
    line_end = text.find("\n", pos)
    if line_end == -1:
        return False
    window = text[line_end : line_end + PROSE_WINDOW_CHARS]
    if not relaxed:
        return any(len(line) >= PROSE_LINE_CHARS for line in window.split("\n"))
    # Relaxed: measure paragraphs rather than lines.
    #
    # The strict test assumes the converter leaves a paragraph on one line,
    # which is true of JPMorgan's and Goldman's filings and false of Morgan
    # Stanley's: its HTML hard-wraps mid-sentence, so an ordinary paragraph
    # arrives as a run of 55-character lines. Every genuine Item 7 heading was
    # rejected as an index entry and the filer returned zero years for 2011-2018
    # with no error — the same silent shape as the manifest overwrite and the
    # Wells Fargo exhibit.
    #
    # It is a fallback and not the default because it is strictly more
    # permissive, and more surviving starts changes which one filter 3 picks:
    # applied unconditionally it let a later decoy win in JPMorgan FY2025 and
    # Bank of America FY2011, cutting both spans below MIN_MDNA_CHARS and losing
    # two years that already worked. Running it only when nothing at all
    # survives leaves every captured year byte-identical.
    blocks = [" ".join(b.split()) for b in window.split("\n\n")]
    return any(len(b) >= PROSE_LINE_CHARS for b in blocks)


def extract_mdna(text: str) -> str | None:
    """Slice the MD&A out of a filing's text, or return None if it isn't there.

    Every start pattern fires many times per filing, and the decoys differ by
    filer, which is why this is not a two-line function. Three filters run in
    order, each removing one specific kind of decoy observed in the filings:

    1. **Prose filter.** Drop headings that are followed by short lines rather
       than a paragraph. This removes table-of-contents rows ("Item 7. |
       Management's Discussion... | 25 | Item 7A.") and the section's own
       internal contents list, both of which use the exact heading text.

    2. **Running-header collapse.** JPMorgan and Goldman print the words
       "Management's Discussion and Analysis" at the top of every page of the
       section -- 40-50 identical matches, each genuinely followed by prose, so
       filter 1 cannot touch them. Any heading text repeating that often is a
       running header, and only its first occurrence is the section start.

    3. **Longest span picks the end, last surviving start picks the start.**
       The end markers also appear in the table of contents, where they sit a
       few hundred characters apart; only the real Item 7A/Item 8 has the whole
       discussion in front of it, so the longest span identifies the true end.
       The start is then the *last* surviving heading before that end -- because
       the remaining decoys (mid-sentence cross-references like "see Item 7.
       MD&A and Note 4") all occur earlier in the document, inside Items 1-6.
       Taking the longest span for the start instead would begin the extract in
       the Risk Factors and swallow 160k characters that are not MD&A.

    Returns None when nothing clears MIN_MDNA_CHARS, which is the caller's
    signal to go looking in the filing's exhibits.
    """
    ends = sorted({m.start() for pat in END_PATTERNS for m in pat.finditer(text)})
    if not ends:
        return None

    starts = sorted({m.start() for pat in START_PATTERNS for m in pat.finditer(text)})
    strict = [p for p in starts if _followed_by_prose(text, p)]
    starts = strict or [p for p in starts if _followed_by_prose(text, p, relaxed=True)]
    if not starts:
        return None

    # Filter 2: group by heading text, and keep only the first occurrence of any
    # group large enough to be a running header.
    groups: dict[str, list[int]] = {}
    for pos in starts:
        groups.setdefault(_normalize(_heading_line(text, pos)), []).append(pos)
    surviving = sorted(
        pos
        for positions in groups.values()
        for pos in (positions[:1] if len(positions) >= RUNNING_HEADER_MIN_REPEATS else positions)
    )

    # +200 so a heading is not terminated by an end marker belonging to the same
    # heading block (a contents list puts Item 7 and Item 7A on adjacent lines).
    paired = [(p, next((e for e in ends if e > p + 200), None)) for p in surviving]
    paired = [(p, e) for p, e in paired if e is not None]
    if not paired:
        return None

    # Filter 3.
    _, end = max(paired, key=lambda pair: pair[1] - pair[0])
    start = max(p for p, e in paired if e == end)

    if end - start < MIN_MDNA_CHARS:
        return None
    return text[start:end].strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--years",
        type=int,
        default=1,
        help="How many recent 10-K filings to fetch per bank (default: 1)",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []

    for bank in BANKS:
        print(f"\n=== {bank['name']} ({bank['ticker']}) ===")
        try:
            filings = find_10k_filings(bank["cik"], args.years)
        except requests.HTTPError as exc:
            print(f"  ! submissions lookup failed: {exc}", file=sys.stderr)
            continue

        if not filings:
            print("  ! no 10-K filings found", file=sys.stderr)
            continue

        for filing in filings:
            cik_short = bank["cik"].lstrip("0")
            base = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_short}/"
                f"{filing['accession']}"
            )
            fiscal_year = filing["period_end"][:4]

            # Try the primary document first, then -- if the MD&A is not in it
            # -- the largest other HTML documents in the same filing. Wells
            # Fargo's Item 7 is a one-line pointer to the annual-report exhibit,
            # so for that filer the primary document genuinely does not contain
            # the section and no amount of better regex would find it there.
            candidates = [filing["document"]]
            found = None

            for attempt in range(MAX_DOCUMENTS_PER_FILING):
                if attempt >= len(candidates):
                    break
                doc = candidates[attempt]
                url = f"{base}/{doc}"
                print(f"  fetching {filing['filing_date']} -> {doc}")

                try:
                    text = html_to_text(get_text(url))
                except requests.HTTPError as exc:
                    print(f"  ! download failed: {exc}", file=sys.stderr)
                    break

                mdna = extract_mdna(text)
                if mdna is not None:
                    found = {"mdna": mdna, "doc": doc, "url": url, "full_chars": len(text)}
                    break

                print(
                    f"      no MD&A in {doc} ({len(text):,} chars) — "
                    "checking other documents in the filing"
                )
                if len(candidates) == 1:
                    try:
                        others = list_filing_documents(cik_short, filing["accession"])
                    except requests.HTTPError as exc:
                        print(f"  ! filing index failed: {exc}", file=sys.stderr)
                        break
                    candidates += [d["name"] for d in others if d["name"] != doc]

            if found is None:
                print(
                    f"  ! {bank['ticker']} {fiscal_year}: MD&A not found in any candidate "
                    "document",
                    file=sys.stderr,
                )
                continue

            out = DATA_DIR / f"{bank['ticker']}-{fiscal_year}-mdna.txt"
            out.write_text(found["mdna"], encoding="utf-8")
            print(
                f"  ok  MD&A {len(found['mdna']):,} chars "
                f"(from {found['full_chars']:,}) -> {out.name}"
            )

            manifest.append(
                {
                    "ticker": bank["ticker"],
                    "company": bank["name"],
                    "cik": bank["cik"],
                    "filing_date": filing["filing_date"],
                    "fiscal_year": fiscal_year,
                    "period_end": filing["period_end"],
                    # The document the text actually came from, which is not
                    # always the filing's primary document. A citation that
                    # pointed at the primary document for Wells Fargo would send
                    # a reader to a page that does not contain the quoted text.
                    "source_document": found["doc"],
                    "source_url": found["url"],
                    "file": out.name,
                    "chars": len(found["mdna"]),
                }
            )

    # The manifest is what makes retrieval citable: every chunk carries metadata
    # pointing back to a row here, and every row points back to an EDGAR URL a
    # grader can open.
    manifest_path = DATA_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {len(manifest)} filings to {DATA_DIR}/ (manifest.json)")
    return 0 if manifest else 1


if __name__ == "__main__":
    raise SystemExit(main())
