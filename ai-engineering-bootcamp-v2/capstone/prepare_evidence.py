"""Assemble the evidence for each of the 50 claims. Never the verdict.

    .venv/bin/python prepare_evidence.py

Phase 2b of CAPSTONE_BUILD_PLAN.md. Reads to_label.jsonl, writes it back with
an `evidence` block on every row, then label.py shows that block so the labeller
decides with the figures already in front of them.


THE LINE THIS FILE DOES NOT CROSS

No model is involved at any point. Candidate tags are chosen by counting how
many words of an XBRL tag's own label appear in the claim's sentence. Values
come from data.sec.gov. The comparison is subtraction and division.

That is the whole design constraint. The slow part of labelling is hunting for
the right tag among nine thousand and running lookups one at a time — mechanical
work, and mechanical work is exactly what should be automated. The judgement
that follows, which is whether a gap means CONTRADICTED or DEFINITION_MISMATCH
or NOT_CHECKABLE, stays with the person, because a model writing that would make
Phase 3 a measurement of two models agreeing rather than of one being right.

So this file will tell you "GS filed $18.397B under this tag, which is 1,946%
away from the claim". It will never tell you what that means.


WHY companyfacts RATHER THAN REPEATED LOOKUPS

One request per company returns every fact it has ever filed — about 8 MB. The
alternative is one HTTP call per candidate tag per claim, which for fifty claims
and six candidates each is three hundred requests against a rate-limited public
API for data already sitting in the first response.
"""

import json
import os
import re
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

TO_LABEL = HERE / "to_label.jsonl"
CACHE = HERE / ".cache"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

CIKS = {"GS": "0000886982", "JPM": "0000019617"}

SCALES = {"trillion": 1e12, "billion": 1e9, "million": 1e6, "thousand": 1e3}

# Words carrying no discriminating power between one XBRL tag and another. "Net"
# is deliberately absent — net revenue and revenue are different concepts, and
# dropping it would make the two indistinguishable.
STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "at", "by",
    "with", "from", "was", "were", "is", "are", "be", "been", "our", "we", "us",
    "its", "this", "that", "these", "those", "which", "as", "than", "compared",
    "primarily", "reflecting", "higher", "lower", "increase", "decrease",
    "including", "included", "related", "other", "total", "per", "value",
}


def headers() -> dict:
    return {"User-Agent": os.getenv("SEC_USER_AGENT", "").strip()}


def company_facts(ticker: str) -> dict:
    """Every fact this company has filed, cached on disk after the first call."""
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{ticker}-companyfacts.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    response = requests.get(FACTS_URL.format(cik=CIKS[ticker]), headers=headers(), timeout=90)
    response.raise_for_status()
    path.write_text(response.text, encoding="utf-8")
    return response.json()


def annual_value(entries: list[dict], fiscal_year: int) -> dict | None:
    """The annual figure for this year, using sec_tool's own rules.

    Deliberately duplicated rather than imported: sec_tool answers one tag at a
    time over HTTP, and this walks nine thousand tags already in memory. The two
    must agree, so the rules are kept identical and the balance-sheet case — no
    start date, fp "FY" — is handled the same way here.
    """
    durations, instants = [], []
    for entry in entries:
        end = entry.get("end")
        if not end or date.fromisoformat(end).year != fiscal_year:
            continue
        start = entry.get("start")
        if start:
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            if 350 <= days <= 380:
                durations.append(entry)
        elif entry.get("fp") == "FY":
            instants.append(entry)

    keep = durations or instants
    if not keep:
        return None
    tens = [e for e in keep if e.get("form") == "10-K"]
    keep = tens or keep
    newest = max(keep, key=lambda e: e.get("filed", ""))
    values = sorted({e["val"] for e in keep})
    return {
        "value": newest["val"],
        "restated": len(values) > 1,
        "all_values": values if len(values) > 1 else None,
        "kind": "position" if not newest.get("start") else "flow",
        "period": (
            f"{newest['start']} to {newest['end']}" if newest.get("start")
            else f"as of {newest['end']}"
        ),
    }


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOPWORDS and len(w) > 2}


def forms(word: str) -> set[str]:
    """A word and its plural/singular variants.

    Without this, "pre-tax earnings" never matches a tag labelled "Income Taxes",
    and the correct tag for the very first claim was invisible while deferred-tax
    and comprehensive-loss tags ranked above it. Proper stemming rules break on
    finance vocabulary — strip "es" and revenues becomes revenu, strip "s" and
    losses becomes losse — so instead every word expands to its candidate forms
    and two words match if any form is shared.
    """
    out = {word}
    if word.endswith("s"):
        out.add(word[:-1])
    if word.endswith("es"):
        out.add(word[:-2])
    return out


def expand(word_set: set[str]) -> set[str]:
    return {form for word in word_set for form in forms(word)}


def build_idf(facts: dict) -> dict[str, float]:
    """How informative each word is, from how many tag labels contain it.

    "Income", "loss" and "tax" appear in hundreds of labels and separate nothing;
    "allowance", "noninterest" and "deposits" appear in a handful and separate
    almost everything. Counting matched words equally let three worthless matches
    outrank one decisive match, which is exactly what went wrong on claim 1.
    """
    import math

    document_frequency: dict[str, int] = {}
    labels = 0
    for payload in facts.get("facts", {}).get("us-gaap", {}).values():
        label = payload.get("label")
        if not label:
            continue
        labels += 1
        for word in expand(words(label)):
            document_frequency[word] = document_frequency.get(word, 0) + 1
    return {w: math.log(labels / (1 + df)) for w, df in document_frequency.items()}


def parse_claimed(figure: str) -> float | None:
    """The claimed figure as a number of dollars, or None for a percentage.

    A percentage cannot be compared against a filed figure at all — no company
    files a percentage — so returning None here is what makes the report say
    "derived, needs two figures" instead of inventing a comparison.
    """
    if figure.strip().endswith("%"):
        return None
    match = re.search(r"\$?\s?([\d,]+(?:\.\d+)?)\s*(trillion|billion|million|thousand)?", figure, re.I)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    scale = SCALES.get((match.group(2) or "").lower(), 1.0)
    return value * scale


def candidates(
    facts: dict, idf: dict[str, float], sentence: str, fiscal_year: int, limit: int = 6
) -> list[dict]:
    """Tags whose own label overlaps the sentence AND that have data for the year.

    Requiring a value for the year before scoring is what keeps obsolete tags out
    of the list. Two of the first three claims hit a tag that existed but had been
    dead since 2010 and 2021 — the sort of dead end that costs a labeller five
    minutes and the agent its entire three-lookup budget.
    """
    sentence_forms = expand(words(sentence))
    scored, unfiled = [], []
    for tag, payload in facts.get("facts", {}).get("us-gaap", {}).items():
        usd = payload.get("units", {}).get("USD")
        if not usd:
            continue
        label = payload.get("label") or tag
        label_words = words(label)

        matched = {w for w in label_words if forms(w) & sentence_forms}
        if len(matched) < 2:
            continue

        # Sum of informativeness, then divided by the square root of the label's
        # length rather than its length. Dividing by length outright hands the
        # top spot to any two-word label with a coincidental hit; not dividing at
        # all rewards sprawling labels for sheer surface area.
        weight = sum(max(idf.get(f, 0.0) for f in forms(w)) for w in matched)
        score = weight / max(len(label_words), 1) ** 0.5
        if weight < 4.0:
            continue

        found = annual_value(usd, fiscal_year)
        if not found:
            # A strong label match with no figure for this year is worth showing
            # rather than dropping. "GoodwillImpairmentLoss matches your sentence
            # and Goldman last filed it in 2014" tells the labeller the concept
            # is right and the data is absent — which is a different verdict from
            # "no tag covers this at all", and invisible if the row is silently
            # removed.
            years = sorted(
                {
                    e["end"][:4]
                    for e in usd
                    if annual_value([e], int(e["end"][:4]))
                }
            )
            # Only worth showing if the company HAS filed this annually at some
            # point. An empty year list means the tag only ever appears in
            # quarterly or dimensional facts, which tells the labeller nothing
            # and was firing on 48 of 50 claims — noise dressed as evidence.
            if years:
                unfiled.append({"tag": tag, "label": label, "weight": round(weight, 2),
                                "years_filed": years})
            continue
        scored.append(
            {
                "tag": tag,
                "label": label,
                "score": round(score, 3),
                "matched": sorted(matched),
                **found,
            }
        )
    scored.sort(key=lambda c: (-c["score"], c["tag"]))
    unfiled.sort(key=lambda c: -c["weight"])
    return scored[:limit], unfiled[:5]


def build_note(row: dict, claimed: float | None, cands: list[dict]) -> str:
    """A factual summary. States what was found and how far off, never why."""
    lines = []
    if claimed is None:
        lines.append(
            "PERCENTAGE — no company files a percentage. Verifying this needs the "
            "figures for both years and the arithmetic done by hand."
        )
    if not cands:
        lines.append(
            f"No us-gaap tag with annual {row['fiscal_year']} data has two or more "
            "label words in common with this sentence. Either the concept is a "
            "company-specific line item, or the wording differs from the tag's."
        )
    for c in cands:
        line = f"{c['tag']} = ${c['value'] / 1e9:,.2f}B ({c['period']}, {c['kind']})"
        if claimed:
            gap = (c["value"] - claimed) / claimed * 100
            line += f" — {gap:+,.1f}% vs claim"
            if abs(gap) <= 1:
                line += "  ← within 1%"
        if c["restated"]:
            line += "  [RESTATED: " + ", ".join(f"${v/1e9:,.2f}B" for v in c["all_values"]) + "]"
        lines.append(line)
    return "\n".join(lines)


def main() -> None:
    rows = [json.loads(line) for line in TO_LABEL.open(encoding="utf-8")]
    facts_by_ticker = {t: company_facts(t) for t in sorted({r["ticker"] for r in rows})}
    idf_by_ticker = {t: build_idf(f) for t, f in facts_by_ticker.items()}

    for row in rows:
        claimed = parse_claimed(row["figure"])
        cands, unfiled = candidates(
            facts_by_ticker[row["ticker"]],
            idf_by_ticker[row["ticker"]],
            row["raw_sentence"],
            row["fiscal_year"],
        )
        row["evidence"] = {
            "claimed_usd": claimed,
            "candidates": cands,
            "summary": build_note(row, claimed, cands),
            "matched_but_not_filed": unfiled,
            "near_match": [c["tag"] for c in cands
                           if claimed and abs((c["value"] - claimed) / claimed) <= 0.01],
        }

    with TO_LABEL.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    unf = sum(1 for r in rows if r["evidence"]["matched_but_not_filed"])
    with_cands = sum(1 for r in rows if r["evidence"]["candidates"])
    near = sum(1 for r in rows if r["evidence"]["near_match"])
    pct = sum(1 for r in rows if r["evidence"]["claimed_usd"] is None)
    print(f"evidence prepared for {len(rows)} claims\n")
    print(f"  candidate tags found      {with_cands:3d}")
    print(f"  a candidate within 1%     {near:3d}")
    print(f"  percentages (need 2 yrs)  {pct:3d}")
    print(f"  nothing found             {len(rows) - with_cands:3d}")
    print(f"  matched but not filed     {unf:3d}   (right concept, no data that year)")


if __name__ == "__main__":
    main()
