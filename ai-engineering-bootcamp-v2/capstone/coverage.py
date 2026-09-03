"""How much of a filer's MD&A can be checked at all, year by year. Stage 1.

    .venv/bin/python coverage.py --ticker JPM

Reads data/history/, writes coverage.jsonl and prints the table. No model, no
agent, no labels — the design is in COVERAGE_STUDY.md.


TWO BINS, AND WHY BOTH ARE REPORTED

A claim can fail to be checkable for reasons that are not alike, and collapsing
them would hide the finding rather than produce it.

  STRUCTURAL — could a figure of this kind ever be tagged? A percentage cannot:
  no company files one. A non-GAAP measure cannot: it is management's own
  definition. A segment figure is tagged, but the SEC's JSON endpoints strip
  dimensions, so it cannot be reached — verified across 41,100 facts for
  Goldman, of which zero carried a segment qualifier.

  EMPIRICAL — for the claims that could be tagged, does a matching tag with
  annual data for that year actually exist? This is where the tagging regime
  shows up: a concept filed in 2015 and abandoned in 2021 changes this bin
  without anything in the prose changing at all.

The first is a property of the sentence. The second is a property of the year.


THE CLASSIFIERS ARE IMPORTED, NOT COPIED

Sentence splitting, typing, table rejection and section detection all come from
extract.py. Copying them would mean fixing the next extraction bug in one file
and not the other, and the two corpora would drift apart silently. The check at
the bottom of this file re-derives extract.py's own FY2025 JPMorgan counts and
fails loudly if they no longer agree.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import extract as ex
import prepare_evidence as pe

HERE = Path(__file__).parent
HISTORY = HERE / "data" / "history"
OUT = HERE / "coverage.jsonl"

# What kind of thing the figure is, and whether that kind can be reached at all.
STRUCTURAL = {
    "STATED": "reachable",           # firmwide flow — one lookup
    "BALANCE": "reachable",          # firmwide position — one lookup
    "DERIVED": "derivable",          # a change; needs two lookups and arithmetic
    "SEGMENT": "tagged_unreachable",  # exists, but dimensions are stripped by the API
    "RATIO": "rarely_tagged",
    "NON_GAAP": "never_tagged",      # management's own definition
    "FORWARD": "no_ground_truth",
    "NOT_A_CLAIM": "not_a_claim",
    "TABLE": "not_a_claim",
}


def claims_for_year(text: str, meta: dict) -> list[dict]:
    """extract.py's pipeline over one year's Item 7."""
    ticker = meta["ticker"]
    doc_fy = int(meta["fiscal_year"])
    section_index = ex.build_section_index(text, ticker)

    claims, offset, seq = [], 0, 0
    for raw in ex.SPLIT.split(text):
        start = text.find(raw, offset)
        offset = start + len(raw) if start >= 0 else offset
        sentence = " ".join(raw.split())
        if not sentence:
            continue

        figures = [(m.group(0), m.start(), m.end()) for m in ex.MONEY.finditer(sentence)]
        figures += [(m.group(0), m.start(), m.end()) for m in ex.PCT.finditer(sentence)]
        figures.sort(key=lambda f: f[1])
        if not figures:
            continue

        if ex.TABLE_MARKER.search(sentence) or len(figures) > ex.MAX_FIGURES_IN_PROSE:
            seq += 1
            claims.append({"id": f"{ticker}-{doc_fy}-{seq:04d}", "ticker": ticker,
                           "doc_fy": doc_fy, "type": "TABLE", "figure": None,
                           "fiscal_year": doc_fy, "raw_sentence": sentence})
            continue

        flags = ex.detect_flags(sentence, ticker)
        ctype = ex.claim_type(flags)
        section, _ = ex.section_for(section_index, start)
        has_comparison = "DERIVED" in flags

        for figure, fig_start, fig_end in figures:
            fy, _ = ex.fiscal_year_for(sentence, fig_end, doc_fy)
            if fy > doc_fy:
                ftype = "FORWARD"
            else:
                derived = ex.figure_is_derived(sentence, fig_start, has_comparison)
                if ctype in ("STATED", "DERIVED"):
                    ftype = "DERIVED" if derived else "STATED"
                elif ctype == "BALANCE" and figure.strip().endswith("%"):
                    ftype = "DERIVED" if derived else "RATIO"
                elif ctype == "RATIO" and not figure.strip().endswith("%"):
                    ftype = "BALANCE" if "BALANCE" in flags else "STATED"
                else:
                    ftype = ctype
            seq += 1
            claims.append({
                "id": f"{ticker}-{doc_fy}-{seq:04d}", "ticker": ticker, "doc_fy": doc_fy,
                "fiscal_year": fy, "figure": figure.strip(), "type": ftype,
                "section": section, "raw_sentence": sentence,
            })
    return claims


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="JPM")
    args = ap.parse_args()
    ticker = args.ticker.upper()

    manifest = [m for m in json.loads((HISTORY / "manifest.json").read_text())
                if m["ticker"] == ticker]
    facts = pe.company_facts(ticker)
    idf = pe.build_idf(facts)

    everything = []
    print(f"{'FY':>6} {'claims':>7} {'reachable':>10} {'with a tag':>11} "
          f"{'derivable':>10} {'segment':>8} {'ratio/nonGAAP':>14}")
    print("  " + "-" * 68)

    for meta in sorted(manifest, key=lambda m: m["fiscal_year"]):
        text = (HISTORY / meta["file"]).read_text(encoding="utf-8")
        claims = claims_for_year(text, meta)
        real = [c for c in claims if c["type"] not in ("TABLE", "NOT_A_CLAIM", "FORWARD")]

        # Empirical bin, for the kinds a single tag could reach.
        for c in real:
            c["structural"] = STRUCTURAL[c["type"]]
            c["has_tag"] = None
            if c["structural"] == "reachable":
                cands, _ = pe.candidates(facts, idf, c["raw_sentence"],
                                         c["fiscal_year"], limit=2)
                c["has_tag"] = bool(cands)
        everything.extend(real)

        n = len(real)
        kinds = Counter(c["structural"] for c in real)
        reach = kinds["reachable"]
        tagged = sum(1 for c in real if c["has_tag"])
        print(f"  {meta['fiscal_year']:>4} {n:>7} {reach:>10} {tagged:>11} "
              f"{kinds['derivable']:>10} {kinds['tagged_unreachable']:>8} "
              f"{kinds['rarely_tagged'] + kinds['never_tagged']:>14}")

    with OUT.open("w", encoding="utf-8") as fh:
        for c in everything:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    total = len(everything)
    tagged = sum(1 for c in everything if c["has_tag"])
    print(f"\n  {total:,} claims across {len(manifest)} filings")
    print(f"  {tagged:,} ({tagged / total:.0%}) have a reachable tag with data for their year")
    print(f"  wrote {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
