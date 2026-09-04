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

from prepare_evidence import CIKS  # one map, so the two cannot drift apart


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

        # Try every document in the filing, not just the primary one.
        #
        # Wells Fargo does not put its MD&A in the 10-K body at all. Item 7
        # there reads "Information in response to this Item 7 can be found in
        # the Annual Report to Shareholders under 'Financial Review'. That
        # information is incorporated herein by reference." The discussion —
        # 406,000 characters of it — sits in a second document in the same
        # submission, and reading only the primary one returned zero years for
        # the whole filer without a single error.
        #
        # EDGAR does not label which document is which in any way worth
        # trusting: for FY2025 it reports the 89,000-character stub as primary
        # and the real filing as secondary. So all of them are tried and the
        # longest MD&A wins, which is the same "longest span" rule extract_mdna
        # already uses internally to pick the section's end.
        candidates = [d["name"] for d in list_filing_documents(cik_short, f["accession"])]
        if f["document"] and f["document"] not in candidates:
            candidates.insert(0, f["document"])
        if not candidates:
            print(f"  FY{year}  no documents listed — skipped")
            continue

        best: tuple[str, str] | None = None
        errors = []
        for doc in candidates:
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_short}/{f['accession']}/{doc}"
            try:
                mdna = extract_mdna(html_to_text(get_text(url)))
            except Exception as exc:  # noqa: BLE001 - one bad doc must not stop the rest
                errors.append(f"{doc}: {type(exc).__name__}")
                continue
            if mdna and (best is None or len(mdna) > len(best[1])):
                best = (doc, mdna)
            time.sleep(0.12)

        if best is None:
            note = f" ({'; '.join(errors)})" if errors else ""
            print(f"  FY{year}  Item 7 not located in any of "
                  f"{len(candidates)} document(s){note}")
            continue
        doc, mdna = best
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_short}/{f['accession']}/{doc}"

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

    # Merge, do not overwrite. This script is run once per filer, and writing
    # the manifest fresh each time meant the second filer silently deleted the
    # first one's index — the .txt files stayed on disk, so nothing errored and
    # nothing looked wrong until a study came back with a quarter of its corpus.
    # Keyed by (ticker, fiscal year) so a re-fetch replaces its own rows only.
    path = OUT / "manifest.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    merged = {(e["ticker"], str(e["fiscal_year"])): e for e in existing}
    merged.update({(e["ticker"], str(e["fiscal_year"])): e for e in manifest})
    rows = sorted(merged.values(), key=lambda e: (e["ticker"], str(e["fiscal_year"])))
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    others = len(rows) - len(manifest)
    print(f"\n{len(manifest)} of {len(by_year)} years captured -> {OUT}/")
    print(f"manifest holds {len(rows)} filings"
          + (f" ({others} from other runs, kept)" if others else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
