"""
Tests for the durable memory store and its write gate.

Run:  .venv/bin/pytest test_memory.py -v

These run against SQLite, with no DATABASE_URL, no API key and no network. That
is deliberate: the assignment's bar is "survives process restart", and a test
that can only be run by someone holding a Supabase connection string is a test
that stops being run. The SQL is identical on both engines (see the note above
_SCHEMA in memory.py), so what passes here is what runs in production.

What these do NOT prove is that the Supabase deployment works — that is a
connection, not logic, and it is checked separately at deploy time. Keeping the
two apart is the point: when the deployed page misbehaves, a green run here says
the memory logic is fine and the problem is configuration.

The one test that matters is test_fact_survives_a_real_process_restart. The rest
guard the write gate, which is where the interesting mistakes are.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import memory_gate
from memory import (
    GLOBAL_SCOPE,
    KIND_ALIAS,
    KIND_PREFERENCE,
    KIND_TAG,
    SOURCE_TOOL,
    SOURCE_USER_UNVERIFIED,
    TRUST_QUARANTINED,
    TRUST_TRUSTED,
    Fact,
    MemoryStore,
    _now,
    as_prompt_block,
)

HERE = Path(__file__).parent


@pytest.fixture
def store(tmp_path):
    """A store on a throwaway SQLite file.

    dsn="" rather than None: None means "read DATABASE_URL from the
    environment", and a developer with Supabase configured in their shell would
    otherwise have these tests quietly write to production.
    """
    return MemoryStore(dsn="", sqlite_path=tmp_path / "test-memory.db")


# --- The bar the assignment actually sets -----------------------------------


def test_fact_survives_a_real_process_restart(tmp_path):
    """Write in one process, let it die, read it back in another.

    This is the whole assignment in one test, and the reason it uses subprocess
    rather than closing and reopening a connection is that closing a connection
    proves nothing — the process still holds the interpreter, the module state
    and anything cached in it. Only an exit proves the fact was on disk and not
    in RAM.

    In production the equivalent event is Render's free instance sleeping after
    fifteen minutes idle and coming back with an empty filesystem. Postgres is
    what makes that survivable there; here, SQLite plus a genuinely dead process
    demonstrates the same property with nothing to configure.
    """
    db = tmp_path / "restart.db"

    writer = (
        "import sys; sys.path.insert(0, %r)\n"
        "from memory import MemoryStore, Fact, GLOBAL_SCOPE, KIND_TAG, "
        "SOURCE_TOOL, TRUST_TRUSTED, _now\n"
        "store = MemoryStore(dsn='', sqlite_path=%r)\n"
        "store.upsert(Fact(scope=GLOBAL_SCOPE, kind=KIND_TAG, "
        "key='goldman sachs|revenue', value='RevenuesNetOfInterestExpense', "
        "source=SOURCE_TOOL, trust=TRUST_TRUSTED, observed_at=_now(), "
        "detail={'company': 'GOLDMAN SACHS GROUP INC', 'match': ['goldman sachs']}))\n"
        "print('written')\n" % (str(HERE), str(db))
    )

    completed = subprocess.run(
        [sys.executable, "-c", writer],
        capture_output=True,
        text=True,
        cwd=HERE,
    )
    assert completed.returncode == 0, completed.stderr
    assert "written" in completed.stdout

    # The writing process is gone. Nothing is shared with it but the file.
    reader = MemoryStore(dsn="", sqlite_path=db)
    recalled = reader.recall("anyone", "Goldman Sachs had revenue of $46 billion in 2023")

    assert len(recalled) == 1
    assert recalled[0].value == "RevenuesNetOfInterestExpense"


def test_recall_is_scoped_to_the_claim(store):
    """Memory is retrieved, not dumped. A claim about one bank must not drag in
    facts about another — that is how a small store becomes a large prompt."""
    for company, tag in [
        ("goldman sachs", "RevenuesNetOfInterestExpense"),
        ("bank of america", "Revenues"),
    ]:
        store.upsert(
            Fact(
                scope=GLOBAL_SCOPE,
                kind=KIND_TAG,
                key=f"{company}|revenue",
                value=tag,
                source=SOURCE_TOOL,
                trust=TRUST_TRUSTED,
                observed_at=_now(),
                detail={"match": [company]},
            )
        )

    recalled = store.recall("u1", "Goldman Sachs revenue in 2023 was $46 billion")
    assert [f.value for f in recalled] == ["RevenuesNetOfInterestExpense"]


def test_preferences_are_recalled_for_every_claim(store):
    """"Answer in billions" applies to all claims, so relevance filtering could
    only ever drop it wrongly."""
    store.upsert(
        Fact(
            scope="u1",
            kind=KIND_PREFERENCE,
            key="units",
            value="always state figures in billions",
            source="user_preference",
            trust=TRUST_TRUSTED,
            observed_at=_now(),
        )
    )
    recalled = store.recall("u1", "Some claim naming no company at all")
    assert len(recalled) == 1

    # ...and belongs to its owner only.
    assert store.recall("someone-else", "Some claim") == []


# --- Provenance has to be load-bearing --------------------------------------


def test_quarantined_facts_are_stored_but_never_recalled(store):
    """The line that makes provenance real rather than decorative.

    A quarantined fact must remain readable — the UI shows what was refused, and
    a refusal nobody can see is indistinguishable from a bug. But it must never
    reach the model.
    """
    poisoned = Fact(
        scope=GLOBAL_SCOPE,
        kind=KIND_TAG,
        key="goldman sachs|revenue",
        value="TotallyRealTag",
        source=SOURCE_USER_UNVERIFIED,
        trust=TRUST_QUARANTINED,
        observed_at=_now(),
        detail={"match": ["goldman sachs"], "refused_because": "not filed"},
    )
    store.upsert(poisoned)

    assert store.recall("u1", "Goldman Sachs revenue 2023") == []          # model
    assert len(store.facts_for("u1")) == 1                                 # UI

    # And it must not sneak in through the prompt builder either.
    assert as_prompt_block(store.recall("u1", "Goldman Sachs revenue 2023")) == ""


def test_ticker_matching_respects_word_boundaries(store):
    """Citigroup's ticker is "C".

    With plain substring matching, every claim containing the letter c — which
    is every claim — would recall Citigroup's facts. The store would appear to
    work and would silently poison unrelated audits with an irrelevant tag.
    """
    store.upsert(
        Fact(
            scope=GLOBAL_SCOPE,
            kind=KIND_TAG,
            key="citigroup|revenue",
            value="Revenues",
            source=SOURCE_TOOL,
            trust=TRUST_TRUSTED,
            observed_at=_now(),
            detail={"match": ["citigroup", "c"]},
        )
    )

    assert store.recall("u1", "Microsoft cloud revenue increased in 2024") == []
    assert len(store.recall("u1", "C reported revenue of $75 billion")) == 1


def test_official_names_with_regex_characters_do_not_crash(store):
    """The SEC's official title for Bank of America is "BANK OF AMERICA CORP
    /DE/". Unescaped, those slashes are a regex, and several filers carry dots
    and parentheses."""
    store.upsert(
        Fact(
            scope=GLOBAL_SCOPE,
            kind=KIND_TAG,
            key="bank of america|revenue",
            value="Revenues",
            source=SOURCE_TOOL,
            trust=TRUST_TRUSTED,
            observed_at=_now(),
            detail={"match": ["bank of america corp /de/", "bank of america"]},
        )
    )
    assert len(store.recall("u1", "Bank of America revenue in 2022")) == 1


# --- The write gate ----------------------------------------------------------


@pytest.fixture
def no_network(monkeypatch):
    """Stop the gate reaching for the SEC's 800 KB ticker file during tests.

    _ticker_for_cik already swallows failures, so without this the tests would
    still pass — slowly, and differently depending on whether the machine is
    online. A test whose result depends on the network is a test that will one
    day fail for a reason that has nothing to do with the code.
    """
    monkeypatch.setattr(memory_gate, "_load_tickers", lambda: {})


def _pair(tag: str, status: str, company: str = "Goldman Sachs") -> tuple[dict, dict]:
    """One ACT/OBSERVE pair in the shape agent.py records."""
    act = {
        "kind": "ACT",
        "tool": "lookup_filed_figure",
        "args": {"company": company, "xbrl_tag": tag, "fiscal_year": 2022},
        "turn": 1,
    }
    response: dict = {"status": status, "company": f"{company.upper()} GROUP INC"}
    if status == "found":
        response |= {
            "xbrl_tag": tag,
            "fiscal_year": 2022,
            "value_usd": 47_365_000_000,
            "value_readable": "$47.37B",
            "source": (
                "https://data.sec.gov/api/xbrl/companyconcept/"
                f"CIK0000886982/us-gaap/{tag}.json"
            ),
        }
    return act, {"kind": "OBSERVE", "response": response, "turn": 2}


def test_learns_nothing_when_the_first_guess_worked(no_network):
    """Bank of America files revenue under Revenues, which is already the
    model's opening guess. Remembering it would add a row that never changes a
    future run — the definition of memory that costs and does not pay."""
    act, observe = _pair("Revenues", "found", company="Bank of America")
    assert memory_gate.propose_from_trace([act, observe]) == []


def test_learns_the_tag_it_had_to_recover_to_find(no_network):
    """The Goldman case: Revenues fails, RevenuesNetOfInterestExpense works.

    This is the only shape that produces a write, and it is the shape worth
    remembering — a later run cannot derive it, and rediscovering it costs a
    wasted model turn plus the ~8 MB tag listing _suggest_tags downloads.
    """
    trace = [
        *_pair("Revenues", "tag_not_filed"),
        *_pair("RevenuesNetOfInterestExpense", "found"),
    ]
    proposals = memory_gate.propose_from_trace(trace)

    assert len(proposals) == 1
    fact = proposals[0]
    assert fact.value == "RevenuesNetOfInterestExpense"
    assert fact.key == "goldman sachs|revenue"
    assert fact.scope == GLOBAL_SCOPE
    assert fact.source == SOURCE_TOOL
    assert fact.trust == TRUST_TRUSTED
    # Recovered from the observation itself rather than by re-resolving the
    # name, which would re-run the matching that produced the Coca-Cola bug.
    assert fact.detail["cik"] == "0000886982"


def test_learns_from_a_same_turn_hedge(no_network):
    """Week 4's finding: this agent often fires both tags in the *same* turn
    rather than reading the first result and reacting.

    If the gate grouped by turn number it would see no failure preceding the
    success and would learn nothing — from precisely the runs worth learning
    from. Pairing by position in the trace is what makes this work.
    """
    act_a, obs_a = _pair("Revenues", "tag_not_filed")
    act_b, obs_b = _pair("RevenuesNetOfInterestExpense", "found")
    for entry in (act_a, act_b, obs_a, obs_b):
        entry["turn"] = 1  # everything in one turn, as recorded in traces.jsonl

    proposals = memory_gate.propose_from_trace([act_a, act_b, obs_a, obs_b])
    assert [f.value for f in proposals] == ["RevenuesNetOfInterestExpense"]


def test_no_figure_is_ever_stored(no_network):
    """The rule the whole design rests on: remember where to look, never what
    was found.

    sec_tool's _annual_entries documents that every annual report republishes
    prior years and the values can disagree. A remembered figure would be right
    when written and silently wrong after the next restatement — and the agent
    would repeat it with the confidence of something it had checked.
    """
    trace = [
        *_pair("Revenues", "tag_not_filed"),
        *_pair("RevenuesNetOfInterestExpense", "found"),
    ]
    proposals = memory_gate.propose_from_trace(trace)
    serialised = json.dumps([f.__dict__ for f in proposals], default=str)

    for forbidden in ("47365000000", "47.37", "value_usd", "value_readable"):
        assert forbidden not in serialised, f"a figure leaked into memory: {forbidden}"


def test_at_most_three_writes_per_run(no_network):
    """A cap enforced in code rather than requested in a prompt. A cap the model
    is asked to respect holds until the run that does not respect it."""
    trace = []
    for company in ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]:
        trace += _pair("Revenues", "tag_not_filed", company=company)
        trace += _pair("RevenuesNetOfInterestExpense", "found", company=company)

    assert len(memory_gate.propose_from_trace(trace)) == memory_gate.MAX_WRITES_PER_TURN


def test_metric_grouping_lets_a_later_claim_find_the_fact():
    """A fact learned from a 2022 revenue claim must be found by a 2023 revenue
    claim. That only works if both are filed under the metric a human names —
    "revenue" — rather than under the tag it happened to be found beneath."""
    assert memory_gate.metric_for_tag("RevenuesNetOfInterestExpense") == "revenue"
    assert memory_gate.metric_for_tag("Revenues") == "revenue"
    assert memory_gate.metric_for_tag("NetIncomeLoss") == "net income"
    # Unmapped tags still produce something readable rather than being dropped.
    assert memory_gate.metric_for_tag("OtherAssetsNoncurrent") == "other assets noncurrent"


# --- Forgetting --------------------------------------------------------------


def test_relearning_updates_rather_than_duplicates(store):
    """(scope, kind, key) is the primary key, so learning the same fact twice is
    an update. That is the consolidation Path B offers as a stretch item,
    obtained by choosing the right key instead of by writing a merge pass."""
    for tag in ["Revenues", "RevenuesNetOfInterestExpense"]:
        store.upsert(
            Fact(
                scope=GLOBAL_SCOPE,
                kind=KIND_TAG,
                key="goldman sachs|revenue",
                value=tag,
                source=SOURCE_TOOL,
                trust=TRUST_TRUSTED,
                observed_at=_now(),
                detail={"match": ["goldman sachs"]},
            )
        )

    facts = store.facts_for("u1")
    assert len(facts) == 1
    assert facts[0].value == "RevenuesNetOfInterestExpense"  # the later one wins


def test_forget_removes_one_fact(store):
    store.upsert(
        Fact(
            scope="u1",
            kind=KIND_ALIAS,
            key="coca-cola",
            value="0000021344",
            source="user_stated_verified",
            trust=TRUST_TRUSTED,
            observed_at=_now(),
            detail={"match": ["coca-cola"]},
        )
    )
    assert len(store.facts_for("u1")) == 1

    store.forget("u1", KIND_ALIAS, "coca-cola")
    assert store.facts_for("u1") == []
