"""A filer's reporting segments, year by year, read out of its own filings.

    python3 mine_segments.py --ticker BAC

Prints what each filing says. It does not edit extract.py — a name goes into
SEGMENTS only after a person has read it.


WHY THIS EXISTS RATHER THAN A TYPED LIST

extract.py's SEGMENTS docstring makes the point that decides this file: segment
names are not stable across years, and a pattern that knows only the modern ones
finds no segment claims in the early years and plenty in the recent ones. That
looks like a filer moving its disclosure down to segment level and is nothing of
the kind — it is the detector's blind spot drawn as a trend. It has already
happened here once: a "0% → 20%" finding that had to be retracted and remeasured
as 3% → 20%.

Typing the names from memory reproduces that failure quietly. Ask anyone, or
anything, for Bank of America's 2012 reporting segments and you will get
something plausible — and plausible is the whole problem, because a wrong list
does not error, it returns a number.

Bank of America over this corpus: six segments in FY2011, five from FY2012, four
by FY2024. Three different answers to "what are BAC's segments", all correct, for
different years.


HOW IT READS THEM

Every 10-K states its segments in a sentence, because it has to:

    "...through five business segments: Consumer & Business Banking (CBB),
     Consumer Real Estate Services (CRES), Global Wealth & Investment Management
     (GWIM), Global Banking and Global Markets, with the remaining operations
     recorded in All Other."

So the enumeration is parsed rather than the headings. An earlier draft looked
for standalone heading-shaped lines and returned 1,264 candidates for BAC —
flattened table row labels, mostly, which is the same table-flattening problem
that decided how claims are rejected in extract.py. The enumeration sentence is
one place, written by the filer, and it names exactly the segments and their
abbreviations.

Parentheses are captured as separate names: the prose uses "GWIM" far more often
than "Global Wealth & Investment Management", and a pattern holding only the
long form finds almost nothing.


WHAT IT CANNOT DO

It proposes; it does not decide. The output should be read against one of the
filer's 10-Ks — the enumeration is quoted in full for exactly that reason. What
it does guarantee is that no name in the list was invented, which the alternative
could not.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
HISTORY = HERE / "data" / "history"

# "through five business segments: A, B and C" / "results of our operations
# through the following four business segments: ..." — the count word is what
# separates the enumeration from the many passing mentions of "our business
# segments", of which BAC's FY2013 filing has forty-two.
ENUM = re.compile(
    r"\b(?:two|three|four|five|six|seven|eight|nine)\s+"
    r"(?:business|reportable|operating)\s+segments?\s*[:,]?\s*(.{20,320}?)"
    r"(?:\s*,?\s*with the remaining|\.\s|\bare reported\b)", re.I | re.S)

NOISE = re.compile(r"^(?:and|the|our|its|we|report|results?|through|following)$", re.I)


def names_from(clause: str) -> list[str]:
    """Split an enumeration clause into segment names and their abbreviations."""
    out: list[str] = []
    for abbr in re.findall(r"\(([A-Z][A-Za-z&]{1,12})\)", clause):
        out.append(abbr)
    clause = re.sub(r"\([^)]*\)", "", clause)
    for part in re.split(r",| and (?=[A-Z])", clause):
        name = " ".join(part.split()).strip(" .;:")
        # A segment name is a title-cased phrase; anything else here is either
        # connective tissue or the sentence running on past the list.
        if 3 <= len(name) <= 60 and name[:1].isupper() and not NOISE.match(name):
            out.append(name)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    a = ap.parse_args()
    ticker = a.ticker.upper()

    manifest = [m for m in json.loads((HISTORY / "manifest.json").read_text())
                if m["ticker"] == ticker]
    if not manifest:
        print(f"No filings for {ticker}. Run fetch_history.py --ticker {ticker} first.")
        return 1

    years: defaultdict[str, list[str]] = defaultdict(list)
    quiet = []
    for m in sorted(manifest, key=lambda m: str(m["fiscal_year"])):
        fy = str(m["fiscal_year"])
        text = " ".join((HISTORY / m["file"]).read_text(encoding="utf-8").split())
        match = ENUM.search(text)
        if not match:
            quiet.append(fy)
            continue
        found = names_from(match.group(1))
        print(f"  FY{fy}  {len(found):>2} names")
        print(f"        “…{match.group(0)[:240].strip()}…”")
        print(f"        {found}\n")
        for name in found:
            years[name].append(fy[2:])

    if quiet:
        print(f"  no enumeration found for FY{', FY'.join(quiet)} — read those by hand\n")

    print(f"  {ticker}: {len(years)} distinct names across {len(manifest)} filings")
    print(f"  {'years':>5}  {'span':<12} name")
    print("  " + "-" * 62)
    for name, ys in sorted(years.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"  {len(ys):>5}  {ys[0]}–{ys[-1]:<10} {name}")
    print("\n  Regex-ready alternation:\n")
    print("      " + "|".join(re.escape(n) for n in sorted(years, key=len, reverse=True)))
    print("\n  Read this against one of the filer's 10-Ks before pasting it into "
          "extract.py's SEGMENTS. The enumerations are quoted above for that.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
