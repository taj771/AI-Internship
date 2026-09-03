"""
Durable memory for the SEC Claim Auditor — the store, and nothing else.

This file knows how to keep facts and hand them back. It does not know which
facts deserve keeping; that judgement lives in memory_gate.py, because it needs
to reach data.sec.gov and this file must stay testable with no network and no
API key. The split is the same one week 3 made between agent.py and sec_tool.py:
the thing that decides, and the thing that fetches, are different files.

MEMORY IS NOT CONTEXT
---------------------
Week 3's agent creates `InMemorySessionService()` *inside* audit(), so every
audit begins with the model knowing nothing — not across days, not across two
clicks of the same button. Making that history longer would not be memory. It
would be a larger prompt, thrown away at the same moment.

Memory is the part that outlives the process: written deliberately, stored
outside the program, and read back by a run that shares no variables with the
run that wrote it. The test is not "does the agent recall it" but "does it
recall it after the machine that learned it has been switched off".

WHERE IT LIVES
--------------
Postgres when DATABASE_URL is set, SQLite otherwise.

That is one decision, not two backends. The deployed service runs on Render's
free plan, whose disk is ephemeral — the same fact already recorded in
render.yaml as the reason week 4's annotation bench was never published. A free
service spins down after about fifteen minutes idle and returns with an empty
filesystem, so a SQLite file there would pass every local test and then quietly
forget everything between the demo and the grader clicking the link. Postgres
lives somewhere else and survives that.

SQLite is kept because it makes the whole store runnable with no account, no
connection string and no network: `pytest test_memory.py` proves durability by
opening the file, closing it, and opening it again in a new process. The SQL
below is written to run unmodified on both, so there is one code path and no
opportunity for the two to drift apart.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


# --- What we are willing to remember ---------------------------------------
#
# Three kinds, deliberately. An agent that remembers everything remembers
# nothing useful, and each of these earns its place by fixing something week 4
# measured rather than by being a plausible thing to store.

# Which us-gaap tag a given company files a metric under. Goldman Sachs does not
# file "Revenues"; it files "RevenuesNetOfInterestExpense". Learning that costs
# a failed lookup plus an ~8 MB fetch of every tag the company files (see
# _suggest_tags in sec_tool.py). Remembering it makes the second audit of that
# company one lookup instead of two.
KIND_TAG = "xbrl_tag"

# What a human means by a company name. `resolve_company("Coca-Cola")` returns
# COCA-COLA EUROPACIFIC PARTNERS plc, a UK bottler, with no error and no hint
# that anything went wrong — week 4 found a whole run that audited the wrong
# company and said nothing about it. The fix is durable and one line long, but
# only if it is written down somewhere that outlives the run that discovered it.
KIND_ALIAS = "company_alias"

# How this person wants answers presented. The only kind that is about the user
# rather than about the world.
KIND_PREFERENCE = "preference"

KINDS = (KIND_TAG, KIND_ALIAS, KIND_PREFERENCE)


# --- Provenance -------------------------------------------------------------
#
# Every row records where it came from and whether that origin is one we act on.
# This is Path B's provenance requirement, but it is not decoration: `trust` is
# read on the retrieval path, and a quarantined fact is never shown to the
# model. Without that, "provenance" would be a column nobody consults.

# The tool ran and returned status=found. The SEC confirmed this.
SOURCE_TOOL = "tool_observation"

# A human asserted it and data.sec.gov corroborated it before it was stored.
SOURCE_USER_VERIFIED = "user_stated_verified"

# A human asserted it and nothing corroborated it. Stored so the refusal is
# visible and demonstrable, never injected into a prompt.
SOURCE_USER_UNVERIFIED = "user_stated_unverified"

# A human stated a presentation preference. Nothing to verify — see the note on
# TRUST_TRUSTED below for why that is not a hole.
SOURCE_USER_PREFERENCE = "user_preference"

TRUST_TRUSTED = "trusted"
TRUST_QUARANTINED = "quarantined"


# --- Scope ------------------------------------------------------------------
#
# GLOBAL_SCOPE holds facts about the world; everything else is keyed by user.
#
# Only tag facts are global, and only because they cannot be asserted into
# existence: the sole way to write one is to run an audit in which data.sec.gov
# actually returned that tag for that company. A stranger hitting the public URL
# can therefore only add tag facts that are true. Aliases are per-user despite
# also being verified, because verification proves the target is a real filer,
# not that it is the one the speaker meant — "Coca-Cola means KO" is a statement
# about what a person intends, and one visitor should not get to decide it for
# everybody else.
GLOBAL_SCOPE = "*"


@dataclass
class Fact:
    """One remembered thing, with the provenance that decides whether it is used."""

    scope: str
    kind: str
    key: str
    value: str
    source: str
    trust: str
    observed_at: str
    # Free-form supporting evidence: the CIK behind an alias, the fiscal year a
    # tag was seen in, the official company name the SEC returned. Kept as JSON
    # rather than columns because it differs per kind and is shown to humans,
    # not queried.
    detail: dict[str, Any] = field(default_factory=dict)
    # How many times this fact has been recalled into a prompt. Cheap to keep,
    # and it is the only evidence that memory is being used rather than merely
    # written — a store full of facts with hits=0 is a store doing nothing.
    hits: int = 0

    @property
    def is_usable(self) -> bool:
        return self.trust == TRUST_TRUSTED

    def one_line(self) -> str:
        """How this fact is written into the prompt. Kept here so that what the
        model sees and what the UI shows can never disagree."""
        if self.kind == KIND_TAG:
            company = self.detail.get("company", self.key.split("|")[0])
            metric = self.key.split("|")[-1]
            return f'{company} files {metric} under us-gaap tag "{self.value}"'
        if self.kind == KIND_ALIAS:
            name = self.detail.get("company", "")
            return (
                f'when this user says "{self.key}" they mean {name} '
                f"(CIK {self.value}) — pass the ticker "
                f'"{self.detail.get("ticker", self.value)}" to the tool, not the name'
            )
        return f"{self.key}: {self.value}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- The store --------------------------------------------------------------

# Written once, in the dialect both engines share.
#
# The primary key is the natural one — (scope, kind, key) — rather than a
# surrogate id, because SERIAL and INTEGER PRIMARY KEY AUTOINCREMENT are spelled
# differently in the two engines and there is nothing an id would buy. It also
# makes "the same fact, learned again" an update instead of a duplicate row,
# which is the consolidation behaviour we want without a consolidation pass.
#
# Timestamps are TEXT rather than TIMESTAMPTZ for the same portability reason.
# Postgres would store them better; storing them identically in both is worth
# more than storing them well in one, because it is what lets the same test that
# runs against SQLite on a laptop run against Supabase unchanged.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_facts (
    scope       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}',
    source      TEXT NOT NULL,
    trust       TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, kind, key)
)
"""

_UPSERT = """
INSERT INTO memory_facts
    (scope, kind, key, value, detail, source, trust, observed_at, hits)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
ON CONFLICT (scope, kind, key) DO UPDATE SET
    value       = excluded.value,
    detail      = excluded.detail,
    source      = excluded.source,
    trust       = excluded.trust,
    observed_at = excluded.observed_at
"""

_SELECT_ALL = """
SELECT scope, kind, key, value, detail, source, trust, observed_at, hits
FROM memory_facts
WHERE scope IN (?, ?)
ORDER BY kind, key
"""


class MemoryStore:
    """Durable facts. Postgres if DATABASE_URL is set, else a SQLite file.

    Deliberately connection-per-operation rather than a held-open connection.
    Supabase's free tier pauses after about a week of inactivity and Render's
    free service sleeps after fifteen minutes, so a connection opened at import
    time is a connection that is dead by the time anybody uses it. Opening per
    call costs milliseconds and removes a whole class of "works until it has
    been quiet for a while" failure.
    """

    def __init__(self, dsn: str | None = None, sqlite_path: str | Path | None = None):
        self.dsn = dsn if dsn is not None else os.getenv("DATABASE_URL", "").strip()
        self.sqlite_path = Path(
            sqlite_path or os.getenv("MEMORY_DB_PATH", "memory.db")
        )
        self.is_postgres = bool(self.dsn)
        self.ensure_schema()

    @property
    def backend(self) -> str:
        """One short string for the sidebar, so a screenshot says which store
        was actually in use. A demo of durable memory that cannot show whether
        it ran against the durable backend is not evidence of much."""
        if self.is_postgres:
            # Never the whole DSN: it carries the password.
            host = self.dsn.split("@")[-1].split("/")[0] if "@" in self.dsn else "postgres"
            return f"Postgres · {host}"
        return f"SQLite · {self.sqlite_path.name}"

    # -- connection handling --

    def _connect(self):
        if self.is_postgres:
            import psycopg  # imported lazily so SQLite users need no driver

            return psycopg.connect(self.dsn)
        connection = sqlite3.connect(self.sqlite_path)
        return connection

    def _sql(self, statement: str) -> str:
        """Translate the portable `?` placeholder to Postgres's `%s`.

        The alternative was writing every statement twice. This is four lines
        and keeps a single source of truth for the SQL, which matters more than
        the small ugliness — two copies of an UPSERT is exactly the kind of pair
        that drifts apart six weeks later.
        """
        return statement.replace("?", "%s") if self.is_postgres else statement

    def _execute(self, statement: str, params: tuple = (), fetch: bool = False):
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(self._sql(statement), params)
            rows = cursor.fetchall() if fetch else None
            if not self.is_postgres:
                connection.commit()
            return rows

    def ensure_schema(self) -> None:
        self._execute(_SCHEMA)

    # -- writes --

    def upsert(self, fact: Fact) -> None:
        """Store a fact, replacing any earlier one with the same identity.

        No gate here on purpose. This method will happily store a poisoned fact
        as quarantined, which is the only way the poisoning demo can show what
        was refused rather than merely asserting that something was.
        """
        if fact.kind not in KINDS:
            raise ValueError(f"unknown memory kind: {fact.kind!r}")
        self._execute(
            _UPSERT,
            (
                fact.scope,
                fact.kind,
                fact.key,
                fact.value,
                json.dumps(fact.detail, sort_keys=True),
                fact.source,
                fact.trust,
                fact.observed_at or _now(),
            ),
        )

    def note_hits(self, facts: Iterable[Fact]) -> None:
        """Record that these facts were recalled into a prompt."""
        for fact in facts:
            self._execute(
                "UPDATE memory_facts SET hits = hits + 1 "
                "WHERE scope = ? AND kind = ? AND key = ?",
                (fact.scope, fact.kind, fact.key),
            )

    def forget(self, scope: str, kind: str, key: str) -> None:
        self._execute(
            "DELETE FROM memory_facts WHERE scope = ? AND kind = ? AND key = ?",
            (scope, kind, key),
        )

    def clear(self, scope: str | None = None) -> None:
        """Wipe. Used by the tests and by the UI's reset button, which exists so
        a demo can be run twice without the second run recalling the first."""
        if scope is None:
            self._execute("DELETE FROM memory_facts")
        else:
            self._execute("DELETE FROM memory_facts WHERE scope = ?", (scope,))

    # -- reads --

    def facts_for(self, user_id: str) -> list[Fact]:
        """Everything visible to this user: their own scope plus global facts."""
        rows = self._execute(_SELECT_ALL, (user_id, GLOBAL_SCOPE), fetch=True) or []
        return [
            Fact(
                scope=row[0],
                kind=row[1],
                key=row[2],
                value=row[3],
                detail=json.loads(row[4]) if row[4] else {},
                source=row[5],
                trust=row[6],
                observed_at=row[7],
                hits=row[8],
            )
            for row in rows
        ]

    def recall(self, user_id: str, claim: str) -> list[Fact]:
        """The facts worth putting in front of the model for *this* claim.

        Retrieval is substring matching on the company name, not embeddings, and
        that is a decision rather than a shortcut. The corpus is a handful of
        facts keyed by company; a claim either names the company or it does not.
        An embedding index here would add a dependency, a build step and a class
        of silent near-miss failures, in exchange for fuzzy matching on strings
        that are already exact. Week 2 is where retrieval had to be clever
        because the corpus was prose. This one is a lookup table.

        Preferences are always returned: "answer in billions" applies to every
        claim, so filtering it by relevance to the claim text would only ever
        drop it wrongly.
        """
        haystack = claim.lower()
        keep: list[Fact] = []

        for fact in self.facts_for(user_id):
            # Quarantined facts are read back for the UI, never for the model.
            # This single line is what makes provenance load-bearing.
            if not fact.is_usable:
                continue

            if fact.kind == KIND_PREFERENCE:
                keep.append(fact)
                continue

            if _mentions(haystack, _match_terms(fact)):
                keep.append(fact)

        return keep


def _match_terms(fact: Fact) -> list[str]:
    """The strings that should make a claim recall this fact.

    `detail["match"]` is written by the gate and holds every name the company
    might be called — what the user typed, the SEC's official title, the ticker.
    The fallback to the key's first field is for rows written before that field
    existed, and for anything hand-inserted with psql.
    """
    terms = [str(t).lower() for t in fact.detail.get("match", []) if str(t).strip()]
    return terms or [fact.key.split("|")[0].strip().lower()]


def _mentions(haystack: str, terms: list[str]) -> bool:
    """Does the claim name this company?

    Word-boundary matching, not plain `in`. A ticker is one to four letters, and
    plain substring matching on Citigroup's ticker "C" would recall Citigroup
    facts for every claim containing the letter c — which is every claim. The
    boundaries also stop "GS" matching the middle of a longer word.

    `re.escape` because official SEC titles contain regex metacharacters:
    "BANK OF AMERICA CORP /DE/" has slashes, and several filers have dots and
    parentheses in their names.
    """
    for term in terms:
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack):
            return True
    return False


def as_prompt_block(facts: list[Fact]) -> str:
    """Render recalled facts as the text the agent is given.

    Framed explicitly as things learned in earlier sessions, and explicitly as
    hints rather than answers. The agent must still call the tool: a remembered
    tag says where to look, never what will be found there. That distinction is
    the whole reason this store keeps tags and not values — see the note in
    memory_gate.py.
    """
    if not facts:
        return ""

    lines = [f"  - {fact.one_line()}" for fact in facts]
    return (
        "\nWHAT YOU LEARNED IN EARLIER SESSIONS\n"
        "These were established in previous audits, by this same tool, and have\n"
        "been kept between sessions. Treat them as a starting point that saves a\n"
        "failed lookup — not as an answer. You must still call the tool and\n"
        "report the figure it returns now.\n" + "\n".join(lines) + "\n"
    )
