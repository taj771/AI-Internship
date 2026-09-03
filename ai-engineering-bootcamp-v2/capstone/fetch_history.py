"""Fetch one filer's Item 7 for every year XBRL has existed. Stage 1 of the study.

    .venv/bin/python fetch_history.py --ticker JPM --from 2010 --to 2025

Writes data/history/<TICKER>-<FY>-mdna.txt and data/history/manifest.json.
Reuses fetch_filings.py — the HTML-to-text conversion and the Item 7 heading
patterns are the fiddly part and they already work.


WHY THIS IS NOT JUST fetch_filings.py WITH A BIGGER LIMIT

find_10k_filings reads data.sec.gov's `filings.recent` block, which holds about
a thousand filings of every type. For a bank that files constantly, a thousand
filings is roughly one year. JPMorgan's recent block contains exactly one 10-K;
the other sixty-nine are in archive blocks listed under `filings.files`, and
nothing that only reads `recent` will ever see them.

That is a silent failure rather than an error: ask for sixteen years and you get
one, with no complaint.


WHY 2010 AND NOT 2000

XBRL did not exist before 2009. JPMorgan filed 1 tagged fact for 2006, 89 for
2007, 720 for 2008, and about three thousand a year from 2010. Claims from the
earlier years would bin as "not checkable" because the tagging regime had not
been built yet, which is a fact about SEC rulemaking and not about disclosure
practice. Including them would produce a dramatic downward trend that means
nothing.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from fetch_filings import (
    extract_mdna,
    get_json,
    get_text,
    html_to_text,
    list_filing_documents,
)

HERE = Path(__file__).parent
OUT = HERE / "data" / "history"

CIKS = {"JPM": "0000019617", "GS": "0000886982", "BAC": "0000070858", "WFC": "0000072971"}


def all_10k_filings(cik: str) -> list[dict]:
    """Every 10-K a filer has ever submitted, across recent and archive blocks."""
    base = get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    blocks = [base["filings"]["recent"]]

    for archive in base["filings"].get("files", []):
        blocks.append(get_json(f"https://data.sec.gov/submissions/{archive['name']}"))
        time.sleep(0.12)  # the SEC asks for under ten requests a second

    filings = []
    for block in blocks:
        for form, acc, doc, filed, period in zip(
            block["form"], block["accessionNumber"],
            block.get("primaryDocument", [""] * len(block["form"])),
            block["filingDate"], block["reportDate"],
        ):
            # Exact match only. "10-K/A" is an amendment and "10-K405" a legacy
            # variant; including them would put two versions of one year's MD&A
            # in the corpus and double-count every claim in it.
            if form != "10-K" or not period:
                continue
            filings.append({
                "accession": acc.replace("-", ""),
                "document": doc,
                "filing_date": filed,
                "period_end": period,
                "fiscal_year": int(period[:4]),
            })
    return filings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="JPM")
    ap.add_argument("--from", dest="start", type=int, default=2010)
    ap.add_argument("--to", dest="end", type=int, default=2025)
    args = ap.parse_args()

    ticker = args.ticker.upper()
    cik = CIKS[ticker]
    OUT.mkdir(parents=True, exist_ok=True)

    filings = all_10k_filings(cik)
    wanted = sorted(
        (f for f in filings if args.start <= f["fiscal_year"] <= args.end),
        key=lambda f: f["fiscal_year"],
    )
    # One filing per fiscal year; if a year somehow has two, keep the earliest
    # filed, which is the original rather than a refiling.
    by_year: dict[int, dict] = {}
    for f in wanted:
        by_year.setdefault(f["fiscal_year"], f)

    print(f"{ticker}: {len(filings)} 10-K filings on EDGAR, "
          f"{len(by_year)} within FY{args.start}-{args.end}\n")

    manifest = []
    for year, f in sorted(by_year.items()):
        cik_short = str(int(cik))
        doc = f["document"]
        if not doc:
            docs = list_filing_documents(cik_short, f["accession"])
            doc = docs[0]["name"] if docs else None
        if not doc:
            print(f"  FY{year}  no primary document found — skipped")
            continue

        url = f"https://www.sec.gov/Archives/edgar/data/{cik_short}/{f['accession']}/{doc}"
        try:
            html = get_text(url)
            text = html_to_text(html)
            mdna = extract_mdna(text)
        except Exception as exc:  # noqa: BLE001 - one bad year must not stop the rest
            print(f"  FY{year}  FAILED  {type(exc).__name__}: {exc}")
            continue

        if not mdna:
            print(f"  FY{year}  Item 7 not located in {doc} ({len(text):,} chars of text)")
            continue

        path = OUT / f"{ticker}-{year}-mdna.txt"
        path.write_text(mdna, encoding="utf-8")
        manifest.append({
            "ticker": ticker, "cik": cik, "fiscal_year": str(year),
            "period_end": f["period_end"], "filing_date": f["filing_date"],
            "source_document": doc, "source_url": url,
            "file": path.name, "chars": len(mdna),
        })
        print(f"  FY{year}  {len(mdna):>8,} chars  {doc}")
        time.sleep(0.25)

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n{len(manifest)} of {len(by_year)} years captured -> {OUT}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
