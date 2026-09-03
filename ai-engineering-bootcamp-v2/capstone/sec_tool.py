"""
The real tool: look up what a company actually filed with the SEC.

This file knows nothing about agents, Gemini, or claims. It takes a company, an
XBRL tag and a year, and returns a number. That separation is deliberate — it
means the lookup can be run and checked by hand, with no model involved, before
anything else is allowed to depend on it.

Run it on its own to see that:

    .venv/bin/python sec_tool.py

Two decisions in here are worth understanding before reading the code.

**Why the caller must supply an exact XBRL tag, rather than the word "revenue".**

It would be friendlier to accept "revenue" and try a list of tags internally
until one worked. It would also destroy the thing this assignment is about. If
the tool silently tries five tags, the agent asks once, gets an answer, and
every run looks identical — a fixed workflow. By requiring an exact tag, the
guess belongs to the model, and when the guess is wrong the model has to look at
the failure and decide what to try next. That decision is the agent.

This is not a contrived difficulty. "Revenue" genuinely is filed under
`Revenues` by JPMorgan and Bank of America, and under `RevenuesNetOfInterest\
Expense` by Goldman Sachs, which files nothing at all under `Revenues`.

**Why a failed lookup returns suggestions instead of just "not found".**

A bare failure would leave the model guessing blind, and it would burn the step
budget flailing. So when a tag is not filed, the tool fetches everything that
company *does* file, picks out the tags whose names resemble what was asked
for, and returns those. The model reads that list and picks a real tag.

The assignment asks for exactly this:

    Tool errors returned as observations the model can see
"""

from __future__ import annotations

import json
import os
from datetime import date

import requests
from dotenv import load_dotenv

load_dotenv()

# The SEC requires every request to say who is making it, by name and email.
# Requests without this are refused outright — this is the single most common
# reason a first attempt at data.sec.gov returns nothing.
USER_AGENT = os.getenv("SEC_USER_AGENT", "").strip()

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

TIMEOUT = 30

# The full ticker list is 10,403 companies and about 800 KB. Fetched at most
# once per program run and kept here, so an agent that looks up three claims
# does not download it three times.
_ticker_cache: dict | None = None


def _headers() -> dict:
    if not USER_AGENT:
        raise RuntimeError(
            "SEC_USER_AGENT is not set in .env. The SEC refuses anonymous "
            "requests, so every lookup would fail with no useful error."
        )
    return {"User-Agent": USER_AGENT}


def _load_tickers() -> dict:
    """Fetch the SEC's ticker -> CIK list once and keep it."""
    global _ticker_cache
    if _ticker_cache is None:
        response = requests.get(TICKERS_URL, headers=_headers(), timeout=TIMEOUT)
        response.raise_for_status()
        _ticker_cache = response.json()
    return _ticker_cache


def resolve_company(company: str) -> tuple[str, str] | None:
    """Turn 'JPMorgan' or 'JPM' into the SEC's ten-digit company id.

    Returns (cik, official_name), or None if nothing matched.
    """
    query = company.strip().lower()
    entries = _load_tickers().values()

    # Ticker first, because it is unambiguous. "C" is Citigroup and nothing
    # else; matching on names would also hit every company with a C in it.
    for entry in entries:
        if entry["ticker"].lower() == query:
            return f"{entry['cik_str']:010d}", entry["title"]

    # Then exact name, then a name that starts with what was asked. Starts-with
    # rather than contains: "JPMorgan" should find JPMORGAN CHASE & CO, but
    # "Bank" should not silently pick whichever bank happens to be listed first.
    for entry in entries:
        if entry["title"].lower() == query:
            return f"{entry['cik_str']:010d}", entry["title"]

    for entry in entries:
        if entry["title"].lower().startswith(query):
            return f"{entry['cik_str']:010d}", entry["title"]

    return None


def _annual_entries(units: list[dict], fiscal_year: int) -> list[dict]:
    """Keep only the entries that cover a full year ending in fiscal_year.

    Two traps are handled here.

    The first is quarters. A company files the same tag every quarter, so most
    entries cover three months. Filtering on duration rather than on the 'fp'
    field works for companies whose fiscal year does not end in December: a
    June year-end runs 2021-07-01 to 2022-06-30, which is still fiscal 2022.

    The third is positions, and it was a silent hole in this function until
    2026-09-02. A balance-sheet fact — total assets, deposits, loans, equity —
    has no duration at all: the SEC returns start: null, because total assets is
    a position on a date rather than a flow across a year. The original code
    began `if not start or not end: continue`, so every such fact was discarded
    before the year filter ever ran. The tool then returned nothing, and the
    agent, following its instruction correctly, answered NOT_CHECKABLE.

    That is the worst shape a bug can take here. The claim was checkable, the
    data was filed, and the failure was reported as a property of the filing.
    In a project whose output is a statement about how far the agent can be
    trusted, a limitation of ours appearing in the results as a limitation of
    the SEC's data is precisely the error being measured.

    Measured cost on the FY2025 GS and JPM MD&A: 115 of 964 extracted candidates
    were unreachable for this reason.

    The second is restatement, and it is the dangerous one. Every annual report
    republishes the previous years alongside the current one, so JPMorgan's 2023
    net income appears in the 2023, 2024 and 2025 filings — three entries, same
    fact. Code that took "the most recent entry" would sometimes hand back a
    figure for the wrong year while looking entirely correct. Nothing would
    raise an error. The answer would just be wrong.
    """
    durations = []
    instants = []

    for unit in units:
        end = unit.get("end")
        if not end or date.fromisoformat(end).year != fiscal_year:
            continue

        start = unit.get("start")

        if start:
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            if 350 <= days <= 380:
                durations.append(unit)
            continue

        # No start date. This is a position, not a flow — see the third trap in
        # the docstring. The annual one is the balance sheet in the annual
        # report, which the SEC marks fp "FY"; every other instant in the year
        # is a quarter end. There is deliberately no fallback to "the latest
        # instant in the year": if no FY instant exists, the company has not
        # filed its annual balance sheet yet, and handing back a Q3 position
        # labelled as the annual figure would be worse than returning nothing.
        if unit.get("fp") == "FY":
            instants.append(unit)

    # A tag is one kind or the other, never both. Durations win where a tag
    # somehow carries both, because that is the shape the year filter above was
    # actually written for.
    keep = durations or instants

    # Prefer the annual report over anything else that happens to cover a year.
    annual_reports = [u for u in keep if u.get("form") == "10-K"]
    return annual_reports or keep


def _suggest_tags(cik: str, wanted_tag: str, limit: int = 12) -> list[str]:
    """List tags this company does file, that resemble the one asked for.

    Called only after a lookup has failed. Downloads roughly 8 MB and takes
    about a second, which is affordable once but would not be as a first step —
    hence only on failure.
    """
    try:
        response = requests.get(FACTS_URL.format(cik=cik), headers=_headers(), timeout=TIMEOUT)
        response.raise_for_status()
        filed_tags = list(response.json().get("facts", {}).get("us-gaap", {}).keys())
    except Exception:
        return []

    # Match on the meaningful words inside the requested tag. A request for
    # "Revenues" that failed should surface RevenuesNetOfInterestExpense;
    # a request for "NetIncomeLoss" should surface the ...AvailableToCommon
    # variants. Splitting a CamelCase tag into words does that without needing
    # a hand-written map of synonyms.
    words = []
    current = ""
    for char in wanted_tag:
        if char.isupper() and current:
            words.append(current.lower())
            current = char
        else:
            current += char
    if current:
        words.append(current.lower())

    # Match on the singular stem, not the literal word. Testing caught this:
    # a failed lookup of "Revenues" found nothing for Microsoft, because
    # Microsoft's tag is RevenueFromContractWithCustomerExcludingAssessedTax —
    # "revenue", singular. One letter, and the model gets no help at all.
    #
    # Only trim the 's' on longer words, so "loss" does not become "los" and
    # start matching every tag with those three letters in it.
    words = [
        w[:-1] if w.endswith("s") and len(w) > 5 else w
        for w in words
        if len(w) > 3
    ]

    scored = []
    for tag in filed_tags:
        # Never suggest the tag that just failed. Testing caught this: asking
        # Microsoft for "Revenues" came back "no annual figure" and then
        # helpfully suggested "Revenues", which is advice to do the same thing
        # again. An agent following it would spend its whole step budget in a
        # circle.
        if tag == wanted_tag:
            continue

        lowered = tag.lower()
        score = sum(1 for w in words if w in lowered)
        if not score:
            continue

        # A tag that *starts* with the concept is almost always the headline
        # figure; one that merely mentions it somewhere is usually a footnote
        # breakdown. Compare, for Goldman: RevenuesNetOfInterestExpense is the
        # total, while InvestmentBankingRevenue is one segment of it.
        #
        # This weighting is not cosmetic. Ranking by length alone pushed
        # Microsoft's actual revenue tag —
        # RevenueFromContractWithCustomerExcludingAssessedTax, fifty characters
        # long — past the cutoff entirely, leaving the model a list of twelve
        # deferred-revenue footnotes and no way to succeed.
        if any(lowered.startswith(w) for w in words):
            score += 3

        scored.append((-score, len(tag), tag))

    return [tag for _, _, tag in sorted(scored)[:limit]]


def lookup_filed_figure(company: str, xbrl_tag: str, fiscal_year: int) -> dict:
    """Look up one figure a company actually filed with the US SEC.

    Use this for every numeric claim. Never answer from your own knowledge —
    you do not know what a company filed, and a plausible wrong number is worse
    than admitting the claim cannot be checked.

    You must supply an exact XBRL tag. There is no single tag for "revenue":
    different companies file it under different names, and a wrong guess
    returns no data along with a list of tags that company does file. Read that
    list and call this tool again with one of them.

    Common starting guesses:
      revenue      -> Revenues, RevenuesNetOfInterestExpense
      net income   -> NetIncomeLoss, ProfitLoss
      total assets -> Assets
      equity       -> StockholdersEquity
      cash         -> CashAndCashEquivalentsAtCarryingValue

    Args:
        company: Company name or ticker, e.g. "JPMorgan" or "JPM".
        xbrl_tag: Exact us-gaap tag, e.g. "Revenues". Case-sensitive.
        fiscal_year: Four-digit year the claim is about, e.g. 2022.
    """
    resolved = resolve_company(company)
    if resolved is None:
        return {
            "status": "company_not_found",
            "detail": f"No SEC filer matches '{company}'. Try the stock ticker instead.",
        }

    cik, official_name = resolved

    try:
        response = requests.get(
            CONCEPT_URL.format(cik=cik, tag=xbrl_tag),
            headers=_headers(),
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        # Returned rather than raised. An exception escaping a tool kills the
        # whole agent run; a returned error is something the model can read and
        # respond to, which is what the assignment asks for.
        return {"status": "network_error", "detail": f"{type(exc).__name__}: {exc}"}

    if response.status_code == 404:
        return {
            "status": "tag_not_filed",
            "company": official_name,
            "detail": f"{official_name} files nothing under us-gaap/{xbrl_tag}.",
            "tags_this_company_does_file": _suggest_tags(cik, xbrl_tag),
        }

    if response.status_code != 200:
        return {"status": "http_error", "detail": f"HTTP {response.status_code}"}

    payload = response.json()
    usd_entries = payload.get("units", {}).get("USD", [])
    entries = _annual_entries(usd_entries, fiscal_year)

    if not entries:
        # The tag is right but the year is not — a different failure from a
        # wrong tag, and it needs a different hint. Telling the model which
        # years this tag *does* cover lets it see at once whether it asked for
        # a year the company had not yet reported, or whether this tag is only
        # used in some years and a different one is needed.
        available_years = sorted(
            {
                date.fromisoformat(u["end"]).year
                for u in usd_entries
                if u.get("start")
                and u.get("end")
                and 350
                <= (date.fromisoformat(u["end"]) - date.fromisoformat(u["start"])).days
                <= 380
            }
        )
        return {
            "status": "no_annual_data",
            "company": official_name,
            "detail": (
                (
                    f"{official_name} has filed us-gaap/{xbrl_tag} at some point, "
                    "but never as an annual figure — only quarterly or partial "
                    "periods. This usually means the tag is obsolete and the "
                    "company reports the concept under a different name now. Do "
                    "not retry this tag for another year; pick a different one "
                    "from the list below."
                )
                if not available_years
                else (
                    f"{official_name} files us-gaap/{xbrl_tag}, but has no annual "
                    f"figure ending in {fiscal_year}. It does have annual figures "
                    f"for {available_years}."
                )
            ),
            "annual_years_available_for_this_tag": available_years,
            "tags_this_company_does_file": _suggest_tags(cik, xbrl_tag),
        }

    # Distinct values, not distinct entries. Three filings repeating the same
    # number is one fact. Three filings disagreeing is a restatement, and the
    # model should be told rather than shown whichever one sorted first.
    values = sorted({entry["val"] for entry in entries})
    newest = max(entries, key=lambda e: e["filed"])

    result = {
        "status": "found",
        "company": official_name,
        "xbrl_tag": xbrl_tag,
        "label": payload.get("label"),
        "fiscal_year": fiscal_year,
        "period": (
            f"{newest['start']} to {newest['end']}"
            if newest.get("start")
            else f"as of {newest['end']}"
        ),
        # Flow or position. Recorded because Phase 4 needs candidate signals for
        # what predicts a wrong verdict, and this one is free.
        "fact_kind": "flow" if newest.get("start") else "position",
        "value_usd": newest["val"],
        "value_readable": f"${newest['val'] / 1e9:.2f}B",
        "form": newest.get("form"),
        "filed": newest.get("filed"),
        "source": f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{xbrl_tag}.json",
    }

    if len(values) > 1:
        result["restated"] = True
        result["all_reported_values"] = [f"${v / 1e9:.2f}B" for v in values]
        result["note"] = (
            "This figure was reported differently in different filings. The "
            "value above is from the most recently filed one."
        )

    return result


if __name__ == "__main__":
    # Checked by hand against data.sec.gov before the agent was allowed to use
    # this. The expected values are written down so a future change that breaks
    # the lookup is visible immediately rather than showing up as a strange
    # verdict three steps later.
    checks = [
        ("JPM", "Revenues", 2022, "expect $128.69B"),
        ("BAC", "Revenues", 2022, "expect $94.95B"),
        ("GS", "Revenues", 2022, "expect FAILURE — Goldman does not use this tag"),
        ("GS", "RevenuesNetOfInterestExpense", 2022, "expect $47.37B"),
        ("JPM", "NetIncomeLoss", 2023, "expect $49.55B, filed three times"),
        ("Microsoft", "Revenues", 2022, "expect FAILURE — different tag, non-December year end"),
        # Positions. Every one of these returned nothing before 2026-09-02, and
        # the agent reported them as claims the SEC had no data for. Verified by
        # hand against data.sec.gov the day the fix was written.
        ("GS", "Assets", 2025, "expect $1809.3B as of 2025-12-31"),
        ("JPM", "Assets", 2025, "expect $4424.9B as of 2025-12-31"),
        ("JPM", "Deposits", 2025, "expect $2559.3B as of 2025-12-31"),
        ("GS", "StockholdersEquity", 2025, "expect $125.0B as of 2025-12-31"),
        # The one that proves the rule is not "December 31". Apple's fiscal year
        # ends in September, and the annual position is marked fp FY all the
        # same — which is why the filter keys on fp rather than on a date.
        ("Apple", "Assets", 2023, "expect $352.6B as of 2023-09-30"),
    ]

    for company, tag, year, expectation in checks:
        print(f"\n--- {company} / {tag} / {year}   ({expectation})")
        result = lookup_filed_figure(company, tag, year)
        if result["status"] == "found":
            print(f"    {result['value_readable']}  ({result['period']}, filed {result['filed']})")
            if result.get("restated"):
                print(f"    restated: {result['all_reported_values']}")
        else:
            print(f"    {result['status']}: {result.get('detail')}")
            suggestions = result.get("tags_this_company_does_file")
            if suggestions:
                print(f"    suggested: {json.dumps(suggestions[:5])}")
