"""Twenty concepts, five banks, fifteen years — the auditor, at a scope it can hold.

    python3 watchlist.py            # every bank
    python3 watchlist.py --ticker JPM

Writes watchlist.jsonl and watchlist_summary.json.


WHY THIS EXISTS, AND WHAT IT ADMITS

The study measured the pipeline end to end and it yields 0.6%: of 34,870 numeric
claims across five banks, 207 produce a comparison anyone can act on. Most of
that loss is not the model's fault and not fixable — 53% of what a bank writes
has no filed counterpart at all, because nobody files a ratio or a non-GAAP
measure. But the rest is one bottleneck, measured five ways and never beaten:
deciding which of ~900 filed concepts a sentence is about. Lexical labels 4%,
labels with definitions 2%, sentence embeddings 6%.

So this file stops trying. The scope inverts:

    the study      every number in Item 7, joined by retrieval
    the auditor    twenty concepts, joined by hand, once

A person wrote every line of WATCH below. Each entry is a plain-English name, the
us-gaap tags that carry it, the phrase a sentence must contain to count, and the
phrases that disqualify it. All of that is checkable by reading — which a cosine
score is not, and that is the whole difference. Retrieval had to be right about
900 concepts it had never seen. This has to be right about twenty, and a reader
can audit the mapping itself in ten minutes.

Recall drops and precision rises, which is the correct trade when the output is
a claim about a company. A tool that checks twenty numbers and is right is worth
more than one that checks everything and cannot say when it is wrong.


WHAT IT STILL WILL NOT DO

  Segments.  Every entry is firmwide, and any sentence naming a division is
  rejected. The SEC's JSON API strips dimensions — verified across 41,100 facts
  — so a segment figure cannot be reached whatever the mapping says.

  Changes.  "up 4%" is a movement, not an amount. The comparison is against a
  filed level, so sentences whose figure is a delta are rejected rather than
  compared against the wrong thing.

  Ratios and per-share figures.  Not filed as USD amounts.

  Adjudicate a gap.  A difference here means the sentence and the tag disagree.
  It is still far more often a scope difference than an error, and the output
  says how far apart, never whether anyone was wrong.


THE ALTERNATE-TAG LISTS ARE NOT INTERCHANGEABLE SYNONYMS

Where `tags` holds more than one, they are listed in the order a bank should be
asked, and the first one that bank actually files wins. Revenue is the sharp
case: JPMorgan and Morgan Stanley file RevenuesNetOfInterestExpense, Bank of
America and Citigroup file Revenues, and for a bank the two are $81 billion
apart. Asking for the wrong one returns a real number for the wrong thing, which
is the failure this project exists to catch.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import extract as ex
import prepare_evidence as pe

HERE = Path(__file__).parent
HISTORY = HERE / "data" / "history"

# Rejected everywhere: a figure that is a rate, a per-share amount, or a count
# of anything. None of them is filed as a USD amount, so a match would compare a
# dollar figure against something that is not one.
DISQUALIFY = re.compile(
    r"\b(per share|per diluted share|basis points|bps|ratio|margin|yield|"
    r"return on|effective tax rate|CET1|tier 1|payout)\b", re.I)

WATCH = [
    {"name": "Net income", "tags": ["NetIncomeLoss"],
     "must": r"\bnet income\b",
     "avoid": r"\b(applicable to|available to|attributable to non)"},

    {"name": "Total net revenue",
     "tags": ["RevenuesNetOfInterestExpense", "Revenues"],
     "must": r"\b(total net revenue|net revenue|total revenue)s?\b",
     "avoid": r"\b(managed|adjusted|excluding)\b"},

    {"name": "Net interest income", "tags": ["InterestIncomeExpenseNet"],
     "must": r"\bnet interest income\b",
     "avoid": r"\b(excluding|core|taxable[- ]equivalent|markets)\b"},

    {"name": "Noninterest income", "tags": ["NoninterestIncome"],
     "must": r"\bnoninterest (income|revenue)\b", "avoid": None},

    {"name": "Noninterest expense", "tags": ["NoninterestExpense"],
     "must": r"\bnoninterest expense\b", "avoid": r"\badjusted\b"},

    {"name": "Interest expense", "tags": ["InterestExpense"],
     "must": r"\b(total )?interest expense\b", "avoid": r"\bdeposits?\b"},

    {"name": "Interest income", "tags": ["InterestAndDividendIncomeOperating"],
     "must": r"\b(total )?interest (and dividend )?income\b", "avoid": r"\bnet\b"},

    {"name": "Income tax expense", "tags": ["IncomeTaxExpenseBenefit"],
     "must": r"\b(income tax expense|provision for income taxes)\b", "avoid": None},

    {"name": "Pre-tax income",
     "tags": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"],
     "must": r"\b(income|earnings) before income tax", "avoid": None},

    {"name": "Total assets", "tags": ["Assets"],
     "must": r"\btotal assets\b", "avoid": r"\b(average|risk[- ]weighted)\b"},

    {"name": "Total liabilities", "tags": ["Liabilities"],
     "must": r"\btotal liabilities\b", "avoid": None},

    {"name": "Stockholders' equity", "tags": ["StockholdersEquity"],
     "must": r"\b(total )?(stockholders|shareholders)[’']? equity\b",
     "avoid": r"\b(tangible|common equity tier|average)\b"},

    {"name": "Total deposits", "tags": ["Deposits"],
     "must": r"\b(total )?deposits\b",
     "avoid": r"\b(average|interest[- ]bearing|noninterest[- ]bearing|brokered|"
               r"consumer|wholesale|U\.S\.|domestic|foreign|time|savings)\b"},

    {"name": "Goodwill", "tags": ["Goodwill"],
     "must": r"\bgoodwill\b", "avoid": r"\bimpairment\b"},

    {"name": "Retained earnings", "tags": ["RetainedEarningsAccumulatedDeficit"],
     "must": r"\bretained earnings\b", "avoid": None},

    {"name": "Long-term debt", "tags": ["LongTermDebt"],
     "must": r"\blong[- ]term debt\b", "avoid": r"\bmaturities\b"},

    {"name": "Cash from operating activities",
     "tags": ["NetCashProvidedByUsedInOperatingActivities"],
     "must": r"\bcash (provided by|used in|flows? from)[^.]{0,30}operating activities\b",
     "avoid": None},

    {"name": "Preferred stock dividends", "tags": ["DividendsPreferredStock"],
     "must": r"\b(preferred stock dividends|dividends on preferred)\b", "avoid": None},

    {"name": "Provision for credit losses",
     "tags": ["ProvisionForLoanLeaseAndOtherLosses", "ProvisionForCreditLosses"],
     "must": r"\bprovision for (credit|loan)[^.]{0,12}losses\b", "avoid": None},

    {"name": "Accumulated other comprehensive income",
     "tags": ["AccumulatedOtherComprehensiveIncomeLossNetOfTax"],
     "must": r"\baccumulated other comprehensive\b", "avoid": None},
]


# Firmwide only, and the scope is carried by the heading, not the sentence.
#
# This cost more iterations than anything else here, so the failure is worth
# recording. "Net interest income was $4.8 billion" is an ordinary sentence with
# no segment name in it, and JPMorgan's firmwide figure that year was $44.9
# billion: it sits under a segment heading several paragraphs up. Rejecting
# sentences that *name* a division catches almost none of these.
#
# Three attempts at inferring scope generically all failed the same way. A
# blacklist of segment headings let every unrecognised heading through. A
# whitelist of section titles common to bank filings — "risk management",
# "results of operations" — was no better, because banks repeat those titles as
# SUB-headings inside each segment discussion. In JPMorgan's FY2013 filing the
# nearest heading above a $4.0 billion divisional provision is "Risk
# management:", 27,000 characters into the Consumer & Community Banking section.
#
# So scope is not inferred. It comes from extract.py's SECTION_MARKERS, which
# were hand-built per filer against the actual documents and know that
# JPMorgan's top-level headings are the ALL-CAPS ones. A bank without a marker
# list is skipped outright rather than measured on a guess — an auditor that
# quietly compares a division against the firm is worse than one that declines.
#
# The cost is honest and small: about half an hour of reading per bank to add a
# marker list. It is the same trade as the WATCH table itself — a person does
# the work once, and afterwards a reader can check it by looking.


def firmwide_index(text: str, ticker: str):
    """extract.py's own section index, or None if this filer has no marker list."""
    if ticker not in ex.SECTION_MARKERS:
        return None
    index = ex.build_section_index(text, ticker)
    return index or None


# The figure has to be the one the phrase governs.
#
# A first version accepted any figure in a sentence containing the phrase, and
# agreement came out at 5%. The reason was not the mapping. "The effect of the
# Durbin Amendment will likely reduce annualized net income by approximately
# $600 million" contains "net income" and a figure, and the figure is a forecast
# of a change to it. So did "Noninterest expense was $19.5 billion, an increase
# of $3.0 billion, or 18%" — three figures, one of them the amount and two of
# them not.
#
# A person reading that sentence has no difficulty: the number is the one right
# after the phrase, joined by a copula. That is what this encodes — the phrase,
# then a short window, then a linking word, then the figure. Anything further
# away, or reached through "increase of" or "compared with", is a different
# quantity that merely sits nearby.
LINK = r"(?:\s+(?:was|were|of|at|to|totall?ed|totaling|totalling|reached|" \
       r"stood at|amounted to|is|are)\s+|\s*[:\u2014-]\s*)"

# Words that, between the phrase and the figure, mean the figure is a movement
# or a comparison rather than the level itself.
DELTA = re.compile(
    r"\b(increase|decrease|higher|lower|up|down|compared|versus|vs|"
    r"change|growth|decline|reduce|reduced|reduction|improv)", re.I)


# A word immediately before the phrase can narrow it to a component of the
# thing, inside a section that is otherwise firmwide. "Remaining goodwill of
# $101 million associated with the Private Equity business" sits under a
# firmwide heading, matches "goodwill", and is 99.8% away from the firm's
# goodwill because it is one business's residue. Sections cannot catch this —
# it is a qualifier, not a heading.
NARROWED = re.compile(
    r"\b(remaining|related|associated|attributable|allocated|specific|"
    r"asset-specific|residual|portion|component|excluding|other|net of)"
    r"[\w\s,'’-]{0,24}$", re.I)


def anchored_figure(sentence: str, must: str) -> tuple[str, int] | None:
    """The figure a phrase governs, or None if the phrase governs no figure here.

    Returns the figure text and the offset just past it, so the caller can ask
    extract.py which fiscal year it belongs to.
    """
    m = re.search(must, sentence, re.I)
    if not m:
        return None
    if NARROWED.search(sentence[max(0, m.start() - 40): m.start()]):
        return None
    window = sentence[m.end(): m.end() + 60]
    link = re.match(LINK, window)
    if not link:
        return None
    rest = window[link.end():]
    if DELTA.search(window[:link.end()]):
        return None
    fig = ex.MONEY.match(rest.lstrip())
    if not fig:
        return None
    lead = len(rest) - len(rest.lstrip())
    end = m.end() + link.end() + lead + fig.end()
    return fig.group(0).strip(), end


def resolve(facts: dict, entry: dict, fy: int) -> tuple[str, float] | None:
    """The first listed tag this bank actually files, with a value for that year."""
    for tag in entry["tags"]:
        usd = facts.get(tag, {}).get("units", {}).get("USD")
        if not usd:
            continue
        v = pe.annual_value(usd, fy)
        if v:
            return tag, v["value"]
    return None


def restated(facts: dict, tag: str, fy: int) -> bool:
    usd = facts.get(tag, {}).get("units", {}).get("USD") or []
    vals = {e["val"] for e in usd
            if e.get("end", "").startswith(str(fy)) and pe.annual_value([e], fy)}
    return len(vals) > 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default=None)
    a = ap.parse_args()

    manifest = json.loads((HISTORY / "manifest.json").read_text())
    if a.ticker:
        manifest = [m for m in manifest if m["ticker"] == a.ticker.upper()]

    out, per_bank, skipped = [], Counter(), set()
    facts_cache: dict[str, dict] = {}

    for m in sorted(manifest, key=lambda m: (m["ticker"], str(m["fiscal_year"]))):
        t, doc_fy = m["ticker"], int(m["fiscal_year"])
        facts = facts_cache.setdefault(t, pe.company_facts(t)["facts"]["us-gaap"])
        text = (HISTORY / m["file"]).read_text(encoding="utf-8")

        index = firmwide_index(text, t)
        if index is None:
            skipped.add(t)
            continue
        # Where each sentence sits in the document.
        #
        # coverage.claims_for_year hands back sentences with their whitespace
        # collapsed, so text.find(sentence) returns -1 for almost all of them —
        # and the scope filter, written against that, silently never fired.
        # Offsets are therefore rebuilt by walking the split exactly as the
        # extractor does, which is also the only way the two can agree about
        # where a sentence begins.
        offsets, cursor = {}, 0
        for raw in ex.SPLIT.split(text):
            at = text.find(raw, cursor)
            if at >= 0:
                cursor = at + len(raw)
            offsets.setdefault(" ".join(raw.split()), at if at >= 0 else cursor)
        seen = set()
        for c in ex_claims(text, m):
            # Firmwide only. A segment figure cannot be reached whatever the
            # mapping says, and a sentence is considered once however many
            # figures extract.py split out of it — the anchoring below picks
            # which figure the concept governs.
            sentence = c["raw_sentence"]
            if sentence in seen:
                continue
            seen.add(sentence)
            if DISQUALIFY.search(sentence) or ex.SEGMENTS[t].search(sentence):
                continue
            section, _ = ex.section_for(index, offsets.get(sentence, 0))
            if section is not None:          # a named segment section
                continue

            for entry in WATCH:
                if entry["avoid"] and re.search(entry["avoid"], sentence, re.I):
                    continue
                found = anchored_figure(sentence, entry["must"])
                if not found:
                    continue
                figure, end = found
                fy, _ = ex.fiscal_year_for(sentence, end, doc_fy)
                if fy > doc_fy:
                    continue
                hit = resolve(facts, entry, fy)
                if not hit:
                    continue
                tag, filed = hit
                claimed = pe.parse_claimed(figure)
                if claimed is None or not filed:
                    continue
                gap = (claimed - abs(filed)) / abs(filed)
                out.append({
                    "ticker": t, "name": entry["name"], "tag": tag,
                    "doc_fy": doc_fy, "fy": fy,
                    "figure": figure, "claimed": claimed, "filed": filed,
                    "gap": gap, "agrees": abs(gap) <= 0.015,
                    "restated": restated(facts, tag, fy),
                    "sentence": sentence[:400],
                })
                per_bank[t] += 1
                break          # one concept per sentence, first match wins

    with (HERE / "watchlist.jsonl").open("w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    if skipped:
        print(f"  skipped {', '.join(sorted(skipped))} — no section markers in "
              f"extract.py, so firmwide cannot be told from segment\n")
    print(f"  {'bank':5} {'checks':>8} {'agree':>7} {'rate':>7} {'concepts':>10}")
    print("  " + "-" * 44)
    summary = []
    for t in sorted(per_bank):
        rs = [r for r in out if r["ticker"] == t]
        ok = sum(r["agrees"] for r in rs)
        names = len({r["name"] for r in rs})
        print(f"  {t:5} {len(rs):>8,} {ok:>7,} {ok/len(rs):>6.0%} {names:>10}")
        summary.append({"ticker": t, "checks": len(rs), "agree": ok,
                        "rate": ok / len(rs), "concepts": names})
    ok = sum(r["agrees"] for r in out)
    print(f"\n  {len(out):,} checks · {ok:,} agree within 1.5% ({ok/len(out):.0%})")
    print(f"  {sum(r['restated'] for r in out):,} involve a figure that was later restated")
    (HERE / "watchlist_summary.json").write_text(
        json.dumps({"per_bank": summary, "watch": [w["name"] for w in WATCH]},
                   indent=2), encoding="utf-8")
    print("  wrote watchlist.jsonl and watchlist_summary.json")
    return 0


def ex_claims(text: str, meta: dict) -> list[dict]:
    """coverage.py's extraction, imported so the two corpora cannot drift."""
    import coverage
    return coverage.claims_for_year(text, meta)


if __name__ == "__main__":
    raise SystemExit(main())
