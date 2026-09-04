"""Two systems of record: one for tests, one real.

A Source answers "is this identifier real here", and optionally "what does it
hold". Everything else about a caller's system stays behind that.


WHY THE SEC ONE IS THE REFERENCE AND NOT A MOCK

A citation checker demonstrated against a dictionary proves the code runs. It
does not establish that fabricated citations happen, which is the whole premise,
and a reader is entitled to ask. So the reference source is a real filer's
actual filed concepts, fetched from data.sec.gov, and demo_sec.py runs the gate
over answers a model really produced. The fabrications in that demo were not
constructed for it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent


class DictSource:
    """A system of record that is a dictionary. For tests and for trying it out."""

    def __init__(self, rows: dict[str, Any]) -> None:
        self.rows = rows

    def exists(self, citation: str) -> bool:
        return citation in self.rows

    def value(self, citation: str) -> Any | None:
        return self.rows.get(citation)


class SecTagSource:
    """Every accounting concept one company has actually filed with the SEC.

    `exists` is the interesting one: a model will happily name a concept that is
    real in the us-gaap taxonomy, spelled correctly, and never once used by the
    company being asked about. That answer is indistinguishable from a good one
    without the filer's own fact set to check against.

    A namespace prefix is stripped before lookup. "us-gaap_InterestExpense" is
    the right concept with the namespace glued on, and reporting it as a
    fabrication would be a formatting complaint dressed as a finding — the same
    unfairness this component exists to prevent, pointed the other way.
    """

    def __init__(self, ticker: str, facts: dict | None = None) -> None:
        self.ticker = ticker.upper()
        self._facts = facts

    @property
    def facts(self) -> dict:
        if self._facts is None:
            self._facts = _load_facts(self.ticker)
        return self._facts

    @staticmethod
    def _clean(citation: str) -> str:
        import re
        return re.sub(r"^(us[-_]?gaap|ifrs|dei|srt)[:_]", "", citation.strip(), flags=re.I)

    def exists(self, citation: str) -> bool:
        return self._clean(citation) in self.facts

    def value(self, citation: str) -> Any | None:
        # Deliberately unimplemented. A tag's value depends on which fiscal year
        # and which period type is meant, and guessing would produce comparisons
        # against the wrong figure — the failure mode ../capstone documents at
        # length. A caller who knows the year should pass a comparison in.
        return None


def _load_facts(ticker: str) -> dict:
    """The filer's concepts, from the capstone's cache if present, else the SEC."""
    cached = HERE.parent / "capstone" / ".cache" / f"{ticker}-companyfacts.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))["facts"]["us-gaap"]

    import os
    import requests

    ciks = {"JPM": "0000019617", "GS": "0000886982", "BAC": "0000070858",
            "WFC": "0000072971", "C": "0000831001", "MS": "0000895421"}
    agent = os.getenv("SEC_USER_AGENT")
    if not agent:
        raise RuntimeError(
            "data.sec.gov refuses requests that do not identify who is making "
            "them. Set SEC_USER_AGENT to a name and email.")
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{ciks[ticker]}.json"
    r = requests.get(url, headers={"User-Agent": agent}, timeout=90)
    r.raise_for_status()
    return r.json()["facts"]["us-gaap"]
