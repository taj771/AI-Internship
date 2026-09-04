"""Turn MD&A prose into candidate claims the auditor can be pointed at.

Phase 1 of CAPSTONE_BUILD_PLAN.md. Reads data/*-mdna.txt, writes claims.jsonl.
The rule this file implements is written down in EXTRACTION_RULES.md, and that
file was written first on purpose — a taxonomy invented after the labels is a
taxonomy invented to fit them.


TWO CHOICES THAT DECIDE MORE THAN THEY LOOK LIKE THEY DO

1. The rewrite is deterministic, not modelled.

   Every candidate has to be self-contained: agent.audit() answers NOT_CHECKABLE
   when a claim names no company, no item and no year, and MD&A prose names none
   of them — it says "we", and it says "2025" only sometimes.

   The obvious fix is to have a model rewrite each sentence. It is rejected here
   for one reason: a model rewriting "net earnings of $17.18 billion" can emit
   "$17.2 billion", and then the auditor is checking a figure the filing never
   contained. A rounding introduced by the extractor would appear downstream as a
   verdict about the bank. In a project whose entire subject is knowing when to
   trust a model's output, the extraction step is a strange place to take an
   unverified one.

   So the company and the fiscal year are supplied by wrapping the sentence,
   and the sentence itself is copied through byte-for-byte. Every figure in
   claims.jsonl appears in the filing exactly as written.

2. Type is assigned by precedence, but every match is recorded.

   A sentence can be a balance-sheet position AND a year-over-year comparison.
   `type` is the first match in precedence order — roughly "the strongest reason
   this cannot be checked" — because the results table needs one row per claim.
   `flags` keeps all of them, so re-deciding the precedence later is a re-sort of
   claims.jsonl rather than a re-run of the extractor.
"""

import json
import re
from pathlib import Path

DATA = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "claims.jsonl"


# --- Sentence splitting -----------------------------------------------------
#
# The lookbehinds guard abbreviations only. There is deliberately NO digit guard.
#
# The first version had one, to protect decimals like "$17.18" — and it was both
# unnecessary and destructive. Unnecessary because the split requires whitespace
# after the period, and a decimal point has none. Destructive because financial
# prose ends sentences with numbers constantly ("...for 2024. Diluted EPS..."),
# so the guard refused to split there. It silently swallowed Goldman's entire
# Executive Overview — net earnings, EPS, ROE, book value, all of it — into one
# ten-figure blob that was then discarded as a table. The most claim-dense
# paragraph in the filing, lost to a guard against a problem that did not exist.

_ABBR = (
    r"(?<!\bU\.S)(?<!\bInc)(?<!\bCo)(?<!\bCorp)(?<!\bLtd)(?<!\bNo)"
    r"(?<!\bvs)(?<!\bi\.e)(?<!\be\.g)(?<!\bMr)(?<!\bDr)(?<!\bSt)"
)
SPLIT = re.compile(_ABBR + r"[.]\s+(?=[A-Z“\"•])")

# A figure, and the two shapes it comes in. Kept as separate patterns because a
# percent and a dollar amount are checked against different things: a dollar
# amount is a filed fact, a percent is nearly always something computed from two.
MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:billion|million|trillion|thousand))?")
PCT = re.compile(r"\b\d+(?:\.\d+)?\s?%")

YEAR = re.compile(r"\b(20[12]\d)\b")


# --- Type detection ---------------------------------------------------------
#
# Precedence order, most-disqualifying first. See EXTRACTION_RULES.md for why
# each type exists; the patterns here are only the surface test for it.

REFERENCE = re.compile(
    r"\bsee\s+(note|part|item|“)|\bfurther information\b|\brefer to\b", re.I
)
RESPECTIVELY = re.compile(r"\brespectively\b", re.I)

# Regulatory requirements and definitions carry figures but assert nothing about
# what this bank did — "banks are required to hold a 2.5% buffer" is a fact about
# the rule, not about JPMorgan, and there is no XBRL fact for it.
# EXTRACTION_RULES.md rules market-wide statements out of scope, and until now
# nothing enforced it. "The S&P 500 Index increased by 16%" is a true, numeric,
# checkable sentence about the world — and there is no XBRL fact for the S&P 500,
# because Goldman does not file the S&P 500. A claim nothing could ever verify
# would consume a labelling slot and then sit in the results as an unexplained
# NOT_CHECKABLE, indistinguishable from a claim the agent failed on.
MARKET = re.compile(
    r"\b(S&P 500|MSCI|Dow Jones|Nasdaq|FTSE|Nikkei|Russell 2000|"
    r"the global economy|the U\.S\. economy|global equity prices|"
    r"unemployment rate|inflation rate|GDP|gross domestic product|"
    r"Treasury (?:yield|note|bond)|WTI|Brent|federal funds rate|"
    r"(?:Index|indices) (?:increased|decreased|rose|fell))\b",
    re.I,
)

DEFINITION = re.compile(
    r"\b(is defined as|are defined as|is calculated as|is required to|are required to|"
    r"requirement is|consists of|the concentration of .{0,60} by asset class)\b",
    re.I,
)

FORWARD = re.compile(
    r"\b(target|targets|targeting|expect|expects|expected|anticipat\w+|"
    r"forward-looking|we believe|intend\w*|plan to|goal|aim to|estimat\w+ (?:that|to)|"
    r"could|would likely|may increase|may decrease|"
    r"scenario assumes|adverse scenario|stress scenario|effective January)\b",
    re.I,
)

NON_GAAP = re.compile(
    r"\b(managed basis|managed-basis|non-GAAP|tangible common|tangible book|"
    r"adjusted (?:revenue|net|earnings|expense)|excluding (?:the|certain|significant)|"
    r"on a (?:fully )?taxable-equivalent basis|FTE basis)\b",
    re.I,
)

RATIO = re.compile(
    r"\b(ROE|ROTE|return on (?:average )?(?:common )?(?:tangible )?(?:shareholders|equity|assets)|"
    r"efficiency ratio|overhead ratio|CET1|common equity tier 1|tier 1|"
    r"capital ratio|leverage ratio|liquidity coverage|LCR|payout ratio|"
    r"per common share|per share|EPS|net interest margin|net yield|"
    r"effective (?:income )?tax rate|charge-off rate|coverage ratio|"
    r"G-SIB surcharge|stress capital buffer|capital conservation buffer|"
    r"surcharge|basis points|\bbps\b)\b",
    re.I,
)

# Segment names are per-filer rather than generic: "Equities" is a Goldman
# segment and an ordinary noun everywhere else, so a shared list would mistype
# JPMorgan sentences that merely mention equities markets.
SEGMENTS = {
    "GS": re.compile(
        r"\b(Global Banking & Markets|Global Banking and Markets|Asset & Wealth Management|"
        r"Asset and Wealth Management|Platform Solutions|Investment banking fees|"
        r"Fixed Income, Currency and Commodities|FICC|Equities (?:net revenues|revenues)|"
        r"Private banking and lending|Management and other fees)\b"
    ),
    # Segment names are not stable across years, and treating them as if they
    # were produces a trend that is really a blind spot. JPMorgan reorganised:
    # in FY2011 its segments were Investment Bank, Retail Financial Services,
    # Card Services, Commercial Banking, Treasury & Securities Services and
    # Asset Management; CCB, CIB and AWM did not exist. A pattern knowing only
    # the modern names finds zero segment claims in 2011 and about a fifth in
    # 2025, which looks like management shifting to segment-level disclosure and
    # is nothing of the kind.
    #
    # So every name the filer has used in the covered period is listed. Little
    # risk of cross-year contamination: a 2011 filing does not say "CCB" and a
    # 2025 filing does not say "Treasury & Securities Services". The exception
    # is "Investment Bank", which in modern filings appears only inside
    # "Commercial & Investment Bank" — hence the lookbehind.
    # Bank of America reorganised twice inside this corpus: six segments in
    # FY2011 (Deposits, Card Services, CRES, Global Commercial Banking, GBAM,
    # GWIM), five from FY2012, four by FY2024. Every name it has used is listed,
    # for the reason given above — a pattern holding only the modern four finds
    # nothing before 2015.
    #
    # These were not typed from memory. mine_segments.py reads them out of the
    # enumeration sentence each 10-K carries ("...through five business
    # segments: ..."), which is why the abbreviations are here too: the prose
    # says "GWIM" far more often than it spells the segment out.
    #
    # One name from that list is deliberately absent. "Deposits" was a reporting
    # segment for FY2011 only and is an ordinary noun in bank prose for all
    # fifteen years — it appears ten times in the FY2025 Item 7, none of them a
    # segment reference. Including it would move firmwide sentences into the
    # segment band, which is the band the study's main finding rests on.
    "BAC": re.compile(
        r"\b(Global Wealth & Investment Management|GWIM|"
        r"Consumer Real Estate Services|CRES|Consumer & Business Banking|CBB|"
        r"Global Commercial Banking|Global Banking & Markets|GBAM|"
        r"Legacy Assets & Servicing|LAS|Card Services|"
        r"Consumer Banking|Global Banking|Global Markets)\b"
    ),
    # Morgan Stanley renamed once: Institutional Securities / Global Wealth
    # Management Group / Asset Management through FY2012, then Institutional
    # Securities / Wealth Management / Investment Management from FY2013.
    "MS": re.compile(
        r"\b(Institutional Securities|Global Wealth Management Group|"
        r"Wealth Management|Investment Management|Asset Management)\b"
    ),
    # Wells Fargo reorganised in FY2020. Names are taken from the enumeration
    # sentence verbatim, not from mine_segments.py's split output — its
    # " and (?=[A-Z])" rule cuts "Consumer Banking and Lending" in half, and
    # half of a segment name is a different, much commoner phrase.
    "WFC": re.compile(
        r"\b(Consumer Banking and Lending|Corporate and Investment Banking|"
        r"Wealth and Investment Management|Wealth, Brokerage and Retirement|"
        r"Community Banking|Wholesale Banking|Commercial Banking|WIM|WBR)\b"
    ),
    # Citigroup is the one filer this cannot fully cover, and the gap is
    # recorded rather than papered over.
    #
    # Through FY2021 its segments are specific enough to match on: Global
    # Consumer Banking, Institutional Clients Group, Citi Holdings,
    # Corporate/Other. From FY2023 it renamed them to Services, Markets,
    # Banking and Wealth — four ordinary English words that appear on nearly
    # every page of a bank's Item 7 in a firmwide sense.
    #
    # Matching those would move hundreds of firmwide sentences into the segment
    # band, which is the band the study's main finding rests on; not matching
    # them undercounts Citi's segment claims for FY2023-2025. The undercount is
    # the safer error and it is stated in COVERAGE_STUDY.md, because a band that
    # is too small is visible as a low number while one inflated by false
    # positives looks exactly like a finding.
    "C": re.compile(
        r"\b(Global Consumer Banking|GCB|Institutional Clients Group|ICG|"
        r"Personal Banking and Wealth Management|PBWM|"
        r"U\.S\. Personal Banking|USPB|Citi Holdings|Corporate/Other)\b"
    ),
    "JPM": re.compile(
        r"\b(Consumer & Community Banking|CCB|Corporate & Investment Bank|CIB|"
        r"Commercial & Investment Bank|Asset & Wealth Management|AWM|"
        r"Corporate segment|Banking & Payments|Markets & Securities Services|"
        r"Home Lending|Card Services|Auto\b|"
        r"Retail Financial Services|Treasury & Securities Services|"
        r"Commercial Banking|Asset Management|Consumer & Business Banking|"
        r"Mortgage Banking|Corporate/Private Equity"
        r"|(?<![&d] )Investment Bank\b)"
    ),
}

# --- Section detection ------------------------------------------------------
#
# A sentence in a segment discussion rarely names its own segment. "Pre-tax
# earnings were $151 million for 2025, compared with a pre-tax loss of $997
# million for 2024" is Platform Solutions, and says so nowhere — the heading two
# paragraphs up carries the subject for the whole section.
#
# Withholding that from the agent does not test its judgement. It tests whether
# it can recover a word this pipeline deleted, which measures the extractor's
# lossiness and reports it as the agent's unreliability. Phase 5's per-filing
# report will always know which section it is reading, so sentence-only is not
# even the realistic case.
#
# Headings cannot be found by line structure: the HTML-to-text conversion split
# sentences mid-word and put every table cell on its own line. What does survive
# is that a segment discussion names its segment over and over. So: count
# canonical segment names in the preceding window and take the clear winner.
# A single passing mention is not enough — the Executive Overview names every
# segment once, and must stay firmwide.

# Canonical name -> every string the filer uses for it. JPMorgan writes the full
# name about six times and the abbreviation forty ("CIB" 56, "CCB" 38, "AWM" 35),
# so counting full names alone found no JPMorgan sections at all. Goldman writes
# its segments out in full.
# Markers, in document order, that begin a section. Each maps to the segment it
# introduces, or to None for firmwide text. The section of a sentence is the
# NEAREST PRECEDING marker — which is how a human reads a filing, and what the
# first version got wrong.
#
# The first version counted how often a segment was named in the preceding 3,000
# characters. Measured against the labelling set, that was wrong in both
# directions within the first four claims: it tagged a firmwide overview as CCB
# on two passing mentions, and it missed a genuine CCB section that named its
# sub-businesses ("Card Services", "Banking & Wealth Management") without ever
# writing "CCB". Frequency is not structure.
#
# The two filings are not laid out the same way, so the markers are per filer:
#
#   JPMorgan prints a complete set of ALL-CAPS headers — an effective table of
#   contents, each appearing exactly once.
#
#   Goldman prints no such headers. What it does do is put a segment's name
#   alone on its own line where that segment's discussion begins, so the anchor
#   is the line-alone form, and passing mentions inside prose are ignored.

SECTION_MARKERS = {
    "GS": [
        # Segment discussions: the name alone on a line, never a passing mention.
        (r"(?m)^Global Banking & Markets\s*$", "Global Banking & Markets"),
        (r"(?m)^Asset & Wealth Management\s*$", "Asset & Wealth Management"),
        (r"(?m)^Platform Solutions\s*$", "Platform Solutions"),
        # Firmwide anchors. Bare "Risk Management" is excluded deliberately — it
        # occurs 53 times, mostly as cross-references inside other sections, and
        # would drag firmwide over segment text it merely refers to.
        (r"Executive Overview", None),
        (r"Business Environment", None),
        (r"Critical Accounting Polic", None),
        (r"Balance Sheet and Funding Sources", None),
        (r"Regulatory Capital", None),
        (r"Liquidity Risk Management", None),
        (r"Market Risk Management", None),
        (r"Credit Risk Management", None),
        (r"Operational Risk Management", None),
    ],
    "JPM": [
        # The pre-2014 segments. Their absence was not visible as an error: with
        # no marker matching, every sentence in a FY2011 filing counted as
        # firmwide, and watchlist.py compared divisional figures against the
        # firm. That single gap produced 85 of its 132 checks and 1 of its 31
        # agreements. Read off the FY2011 and FY2013 filings, where every
        # top-level heading is ALL CAPS and appears exactly once.
        (r"(?m)^INVESTMENT BANK\s*$", "Investment Bank"),
        (r"(?m)^RETAIL FINANCIAL SERVICES\s*$", "Retail Financial Services"),
        (r"(?m)^CARD SERVICES(?: & AUTO)?\s*$", "Card Services"),
        (r"(?m)^TREASURY & SECURITIES SERVICES\s*$", "Treasury & Securities Services"),
        (r"(?m)^COMMERCIAL BANKING\s*$", "Commercial Banking"),
        (r"(?m)^ASSET MANAGEMENT\s*$", "Asset Management"),
        (r"(?m)^CORPORATE/PRIVATE EQUITY\s*$", "Corporate/Private Equity"),
        (r"(?m)^CORPORATE & INVESTMENT BANK\s*$", "Corporate & Investment Bank"),
        (r"(?m)^INTERNATIONAL OPERATIONS\s*$", "International Operations"),
        (r"(?m)^CONSUMER & COMMUNITY BANKING\s*$", "Consumer & Community Banking"),
        (r"(?m)^COMMERCIAL & INVESTMENT BANK\s*$", "Commercial & Investment Bank"),
        (r"(?m)^ASSET & WEALTH MANAGEMENT\s*$", "Asset & Wealth Management"),
        (r"(?m)^CORPORATE\s*$", "Corporate"),
        # Every other ALL-CAPS header is firmwide. Listed rather than matched by
        # a general capitals pattern, because the flattened text is full of
        # capitalised table cells that a general pattern would swallow.
        (r"(?m)^INTRODUCTION\s*$", None),
        (r"(?m)^EXECUTIVE OVERVIEW\s*$", None),
        (r"(?m)^CONSOLIDATED RESULTS OF OPERATIONS\s*$", None),
        (r"(?m)^CONSOLIDATED BALANCE SHEETS AND CASH FLOWS ANALYSIS\s*$", None),
        (r"(?m)^BUSINESS SEGMENT & CORPORATE RESULTS\s*$", None),
        (r"(?m)^[A-Z][A-Z ]+RISK MANAGEMENT\s*$", None),
        (r"(?m)^CREDIT PORTFOLIO\s*$", None),
        (r"(?m)^CONSUMER CREDIT PORTFOLIO\s*$", None),
        (r"(?m)^WHOLESALE CREDIT PORTFOLIO\s*$", None),
        (r"(?m)^ALLOWANCE FOR CREDIT LOSSES\s*$", None),
        (r"(?m)^CRITICAL ACCOUNTING ESTIMATES USED BY THE FIRM\s*$", None),
        (r"(?m)^ACCOUNTING AND REPORTING DEVELOPMENTS\s*$", None),
        (r"(?m)^FORWARD-LOOKING STATEMENTS\s*$", None),
    ],
}


def build_section_index(text: str, ticker: str) -> list[tuple[int, str | None]]:
    """Every section boundary in the document, sorted by position."""
    index = []
    # A filer with no marker list yields no sections rather than a KeyError.
    # Section is metadata — it does not enter any coverage bin — so a new filer
    # is still fully measurable before someone has read its headings. What it
    # must not do is look measured when it is not: section_for returns None and
    # the app shows a blank rather than inventing one.
    for pattern, name in SECTION_MARKERS.get(ticker, []):
        for match in re.finditer(pattern, text):
            index.append((match.start(), name))
    index.sort()
    return index


def section_for(index: list[tuple[int, str | None]], offset: int) -> tuple[str | None, int]:
    """The section a sentence sits in, and how far back its header was.

    The distance replaces the old mention count as the audit trail: a section
    attributed from a header 200 characters up is solid, one attributed from
    40,000 characters up means the marker list has a gap.
    """
    section, at = None, None
    for position, name in index:
        if position > offset:
            break
        section, at = name, position
    return section, (offset - at if at is not None else -1)


BALANCE = re.compile(
    r"\b(total assets|total liabilities|total deposits|deposits (?:were|of|totaled)|"
    r"loans (?:were|of|totaled)|total loans|stockholders.? equity|shareholders.? equity|"
    r"book value|tangible book value|allowance for credit losses|"
    r"total capital|risk-weighted assets|assets under (?:management|supervision)|AUM|AUS|"
    r"interest-earning assets|interest-bearing|carrying value|outstanding was|"
    r"as of December 3[01])\b",
    re.I,
)

# --- Table detection --------------------------------------------------------
#
# The fetched MD&A text flattens tables into unlabelled runs of numbers, and a
# figure without its row and column header is not a claim. Two signals, because
# neither works alone:
#
#   Density fails. The densest run in the corpus is a 124-figure table, but the
#   second and third densest are "Net revenue was $78.5 billion, up 12%" — real
#   prose claims, short, and almost all number by word count. A density cutoff
#   that catches the table throws those away.
#
#   So: an explicit marker, or an absolute count. Prose in this corpus tops out
#   around six figures in one sentence ("the provision was $10.7 billion, net
#   charge-offs were $8.6 billion and..."). Past eight, it is a table every time.

TABLE_MARKER = re.compile(
    r"\b(the table below|the following table[s]?|the tables below|"
    r"\(in millions|\(in billions|\(in thousands|"
    r"as of or for the year ended|December 3[01],\s*\(in|"
    r"league table|rank share|wallet share)\b",
    re.I,
)
MAX_FIGURES_IN_PROSE = 8

DERIVED = re.compile(
    r"\b(compared with|compared to|versus|vs\.|higher than|lower than|"
    r"increase[sd]?|decrease[sd]?|grew|growth of|declin\w+|rose|fell|improved|"
    r"up from|down from|change[sd]? from|year-over-year|year over year|"
    r"\d+\s?%\s+(?:higher|lower|increase|decrease))\b",
    re.I,
)

# Order is the precedence. Changing it re-types the corpus, so it lives in one
# place and the flags below keep every match regardless of who wins.
PRECEDENCE = ["NOT_A_CLAIM", "FORWARD", "NON_GAAP", "RATIO", "SEGMENT", "BALANCE", "DERIVED"]


def detect_flags(sentence: str, ticker: str) -> list[str]:
    """Every type whose surface pattern this sentence matches, unordered."""
    flags = []
    if (
        REFERENCE.search(sentence)
        or RESPECTIVELY.search(sentence)
        or DEFINITION.search(sentence)
        or MARKET.search(sentence)
    ):
        flags.append("NOT_A_CLAIM")
    if FORWARD.search(sentence):
        flags.append("FORWARD")
    if NON_GAAP.search(sentence):
        flags.append("NON_GAAP")
    if RATIO.search(sentence):
        flags.append("RATIO")
    if SEGMENTS[ticker].search(sentence):
        flags.append("SEGMENT")
    if BALANCE.search(sentence):
        flags.append("BALANCE")
    if DERIVED.search(sentence):
        flags.append("DERIVED")
    return flags


# Whether *this* figure is the derived one, not whether its sentence contains a
# comparison somewhere. "Other principal transactions revenues were $1.59 billion
# for 2025, 66% lower than 2024" holds both kinds: $1.59 billion is one lookup,
# 66% is two lookups and a division. Typing the sentence would file them together
# and make the by-type results table meaningless, which is the one table Phase 3
# exists to produce.
DELTA_LEAD = re.compile(
    r"\b(up|down|higher|lower|increase[sd]?|decrease[sd]?|declin\w+|grew|growth|"
    r"rose|fell|improved|compared with|compared to|versus|vs\.)\s*$"
    # "of" is a delta word only behind another one. "an increase of $5 billion"
    # is a change; "net earnings of $17.18 billion" is a level, and treating the
    # bare preposition as a signal typed Goldman's headline figure as derived.
    r"|\b(?:increase[sd]?|decrease[sd]?|declin\w+|growth|reduction|gain|loss)\s+of\s*$",
    re.I,
)


def figure_is_derived(sentence: str, fig_start: int, sentence_has_comparison: bool) -> bool:
    lead = sentence[max(0, fig_start - 32):fig_start]
    if DELTA_LEAD.search(lead):
        return True
    # A bare percent in a comparing sentence is the comparison. A bare dollar
    # amount in one is normally the anchor the comparison is made against.
    return sentence_has_comparison and sentence[fig_start:fig_start + 12].lstrip()[:1].isdigit()


def claim_type(flags: list[str]) -> str:
    for candidate in PRECEDENCE:
        if candidate in flags:
            return candidate
    return "STATED"


# --- Fiscal year ------------------------------------------------------------


def fiscal_year_for(sentence: str, figure_end: int, default: int) -> tuple[int, str]:
    """Which year the figure belongs to, and where that was decided.

    MD&A writes the year after the figure — "$58.28 billion for 2025, 9% higher
    than 2024" — so the nearest year *following* the figure is the right one and
    the nearest year overall is not. Getting this backwards would attach every
    current-year figure to the prior year, and the resulting claim would be a
    genuine contradiction that the extractor manufactured.
    """
    following = [m for m in YEAR.finditer(sentence) if m.start() >= figure_end]
    if following:
        return int(following[0].group(1)), "sentence_after_figure"

    any_year = YEAR.findall(sentence)
    if any_year:
        return int(max(any_year)), "sentence_any"

    return default, "manifest_default"


# --- Assembly ---------------------------------------------------------------


def build_claim(
    company: str,
    ticker: str,
    doc_fy: int,
    fy: int,
    sentence: str,
    figure: str,
    section: str | None = None,
) -> str:
    """Wrap the sentence so it names a company and a year without altering it.

    Two different years, and conflating them was a real bug caught by hand-check.
    `doc_fy` is the filing the sentence was taken from — always 2025 here.
    `fy` is the year the figure is about, which is often 2024 or 2023, because
    every annual report restates the prior years alongside the current one. A
    claim about FY2024 made inside the FY2025 filing is not an error; it is the
    restatement case, and it is the case the calibration layer most needs.

    The sentence is quoted, not paraphrased. Everything the agent needs that the
    filing does not say out loud is added outside the quotation marks, which is
    the only arrangement where "the figure was copied exactly" stays true by
    construction rather than by inspection.
    """
    where = (
        f"in the “{section}” section of Item 7" if section else "in Item 7"
    )
    return (
        f"{company} ({ticker}) wrote the following {where} of its fiscal year "
        f"{doc_fy} Form 10-K: “{sentence}” "
        f"Check the figure {figure} against what {ticker} filed for fiscal year {fy}."
    )


def extract() -> list[dict]:
    manifest = {m["ticker"]: m for m in json.loads((DATA / "manifest.json").read_text())}
    claims = []

    for ticker in ("GS", "JPM"):
        meta = manifest[ticker]
        text = (DATA / meta["file"]).read_text(encoding="utf-8")
        default_fy = int(meta["fiscal_year"])

        # Offsets are tracked so a labeller can find the sentence in the source
        # file. A claim whose provenance is "somewhere in a 300 KB text file" is
        # not one anybody can check by hand, and hand-checking is Phase 2.
        section_index = build_section_index(text, ticker)
        offset = 0
        seq = 0
        for raw in SPLIT.split(text):
            start = text.find(raw, offset)
            offset = start + len(raw) if start >= 0 else offset
            sentence = " ".join(raw.split())
            if not sentence:
                continue

            figures = [(m.group(0), m.start(), m.end()) for m in MONEY.finditer(sentence)]
            figures += [(m.group(0), m.start(), m.end()) for m in PCT.finditer(sentence)]
            figures.sort(key=lambda f: f[1])
            if not figures:
                continue

            # A table run gets one record, not one per figure. Emitting 124 rows
            # for a single flattened table would drown the labelling queue, but
            # dropping it silently would leave no way to check what extraction
            # threw away — and EXTRACTION_RULES.md commits to hand-checking the
            # drops, which is only possible if they are written down.
            if TABLE_MARKER.search(sentence) or len(figures) > MAX_FIGURES_IN_PROSE:
                seq += 1
                claims.append(
                    {
                        "id": f"{ticker}-{seq:04d}",
                        "ticker": ticker,
                        "company": meta["company"],
                        "fiscal_year": default_fy,
                        "type": "TABLE",
                        "flags": ["TABLE"],
                        "figure_count": len(figures),
                        "claim": None,
                        "raw_sentence": sentence,
                        "char_offset": start,
                        "source_url": meta["source_url"],
                    }
                )
                continue

            # A sentence bundling three figures becomes three candidates. One
            # verdict cannot describe three numbers, so one claim must not carry
            # them.
            flags = detect_flags(sentence, ticker)
            ctype = claim_type(flags)

            section, section_distance = section_for(section_index, start)
            has_comparison = "DERIVED" in flags
            for figure, fig_start, fig_end in figures:
                fy, fy_source = fiscal_year_for(sentence, fig_end, default_fy)
                derived = figure_is_derived(sentence, fig_start, has_comparison)

                # A figure dated after the filing's own fiscal year cannot be
                # checked against filed data, because that year has not been
                # filed. In a FY2025 10-K a 2026 figure is always a target, a
                # scenario, or a requirement taking effect later — never a
                # result. This is a rule about what evidence can exist, so it
                # outranks whatever the sentence's wording suggested, and it
                # removes the need to keep inventing patterns for each new way
                # management finds to write about the future.
                if fy > default_fy:
                    seq += 1
                    claims.append(
                        {
                            "id": f"{ticker}-{seq:04d}",
                            "ticker": ticker,
                            "company": meta["company"],
                            "fiscal_year": fy,
                            "fiscal_year_source": fy_source,
                            "figure": figure.strip(),
                            "type": "FORWARD",
                            "flags": flags + ["FUTURE_YEAR"],
                            "claim": None,
                            "raw_sentence": sentence,
                            "char_offset": start,
                            "source_url": meta["source_url"],
                        }
                    )
                    continue

                # Sentence-level types outrank the per-figure call: a segment
                # figure is a segment figure whether or not it is a delta.
                if ctype in ("STATED", "DERIVED"):
                    ftype = "DERIVED" if derived else "STATED"
                elif ctype == "BALANCE" and figure.strip().endswith("%"):
                    # The mirror of the rule below. A position is an amount of
                    # money on a date; it is never a percentage. In "investment
                    # management revenues were $10.60 billion, 11% higher than
                    # 2023... higher average assets under supervision", the
                    # phrase that triggered BALANCE is real, but the 11% is a
                    # growth rate. Left alone it would put a derived claim into
                    # the balance-sheet stratum and quietly bias that estimate.
                    ftype = "DERIVED" if derived else "RATIO"
                elif ctype == "RATIO" and not figure.strip().endswith("%"):
                    # "average interest-earning assets were $3.8 trillion ... the
                    # yield was 5.05%" is one ratio and one balance. A ratio is a
                    # percent or a per-share amount; a dollar total in a
                    # ratio-bearing sentence is the quantity underneath it.
                    ftype = "BALANCE" if "BALANCE" in flags else "STATED"
                else:
                    ftype = ctype
                seq += 1
                claims.append(
                    {
                        "id": f"{ticker}-{seq:04d}",
                        "ticker": ticker,
                        "company": meta["company"],
                        "cik": meta["cik"],
                        "fiscal_year": fy,
                        "fiscal_year_source": fy_source,
                        "figure": figure.strip(),
                        "figure_kind": "percent" if figure.strip().endswith("%") else "money",
                        "type": ftype,
                        "sentence_type": ctype,
                        "flags": flags,
                        "section": section,
                        "section_distance": section_distance,
                        "claim": build_claim(
                            meta["company"],
                            ticker,
                            default_fy,
                            fy,
                            sentence,
                            figure.strip(),
                            section,
                        ),
                        "raw_sentence": sentence,
                        "char_offset": start,
                        "source_url": meta["source_url"],
                        "source_document": meta["source_document"],
                    }
                )

    return claims


def main() -> None:
    claims = extract()
    with OUT.open("w", encoding="utf-8") as fh:
        for claim in claims:
            fh.write(json.dumps(claim, ensure_ascii=False) + "\n")

    print(f"{len(claims)} candidates -> {OUT.name}\n")

    by_ticker: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for claim in claims:
        by_ticker[claim["ticker"]] = by_ticker.get(claim["ticker"], 0) + 1
        by_type[claim["type"]] = by_type.get(claim["type"], 0) + 1

    for ticker, count in sorted(by_ticker.items()):
        print(f"  {ticker:5s} {count:5d}")
    print()
    for ctype in PRECEDENCE + ["STATED", "TABLE"]:
        count = by_type.get(ctype, 0)
        share = 100 * count / len(claims) if claims else 0
        print(f"  {ctype:14s} {count:5d}  {share:5.1f}%")


if __name__ == "__main__":
    main()
