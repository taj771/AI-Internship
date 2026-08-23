"""
The check suite as pytest tests.

    .venv/bin/pytest test_checks.py -q          # every recorded run
    .venv/bin/pytest test_checks.py -q -k C09   # one run
    .venv/bin/pytest test_checks.py -q -k logic # only the tests of the checks themselves

Two kinds of test live here and they fail for opposite reasons.

**The trace tests** assert that each recorded run satisfies each check. On the
baseline they are red — four runs fail — and that is the correct result, not a
broken test file. Red means the agent misbehaved on those claims, which is what
the week set out to demonstrate. After the step 8 fix they should go green
without anything in this file changing.

**The logic tests** assert that the checks themselves behave, using hand-written
records rather than recorded ones. These must always be green. They exist
because two earlier drafts of the checks flagged runs a human had passed, and
without a test pinning the fixed behaviour down, the same mistake can be
reintroduced by a later tidy-up that looks harmless.
"""

from __future__ import annotations

import pytest

from checks import CHECKS, check_answer_format, check_contradicted_was_searched, parse_money
from trace_log import load_records

RECORDS = load_records()

# Skip rather than fail when nothing has been recorded. A missing trace file
# means run_batch.py has not been run, which is a different problem from the
# agent misbehaving, and reporting it as twenty failed assertions would bury it.
pytestmark = pytest.mark.skipif(not RECORDS, reason="no runs recorded — run run_batch.py first")


def _ids(records):
    return [r["trace_id"] for r in records]


@pytest.mark.parametrize("record", RECORDS, ids=_ids(RECORDS))
@pytest.mark.parametrize("name,fn", CHECKS, ids=[name for name, _ in CHECKS])
def test_recorded_run(name, fn, record):
    """Every recorded run must satisfy every check."""
    passed, reason = fn(record)
    assert passed, f"{record['trace_id']}: {reason}"


# --- tests of the checks themselves -----------------------------------------


def test_logic_money_units_are_compared_numerically():
    """$94,950 million and $94.95B are the same amount.

    C14 restated the filed figure in the claim's own units. An early draft
    compared the strings and failed that correct run.
    """
    assert parse_money("$94,950 million") == pytest.approx(parse_money("$94.95B"))
    assert parse_money("$3.7 billion") == pytest.approx(3.7e9)
    assert parse_money("$9 trillion") == pytest.approx(9e12)
    assert parse_money("none found") is None
    assert parse_money(None) is None


def test_logic_large_claim_needs_no_search():
    """A claim four times the filed figure cannot be a segment of it.

    This is the C04 case. Requiring a search before every CONTRADICTED flagged
    that correct run, so the size condition was added.
    """
    record = {
        "parsed": {"VERDICT": "CONTRADICTED", "CLAIMED": "$200 billion", "FILED": "$49.55 billion"},
        "steps": [{"kind": "OBSERVE", "turn": 2, "response": {"status": "found"}}],
        "tool_calls": [{"turn": 1, "args": {"xbrl_tag": "NetIncomeLoss"}}],
    }
    passed, _ = check_contradicted_was_searched(record)
    assert passed


def test_logic_slightly_larger_claim_needs_a_search():
    """The C03 regression: $132.3bn claimed against $128.69bn filed.

    An earlier version exempted every claim above the filed figure, on the
    grounds that a segment cannot exceed its total. The matching instruction
    rule then licensed the agent to rule CONTRADICTED on JPMorgan's
    managed-basis claim without looking, turning a passing run into a failing
    one — and this check could not see it, because it shared the misconception.
    A claim a few percent high is exactly what a non-GAAP figure looks like.
    """
    record = {
        "parsed": {
            "VERDICT": "CONTRADICTED",
            "CLAIMED": "$132.3 billion",
            "FILED": "$128.69 billion",
        },
        "steps": [{"kind": "OBSERVE", "turn": 2, "response": {"status": "found"}}],
        "tool_calls": [{"turn": 1, "args": {"xbrl_tag": "Revenues"}}],
    }
    passed, reason = check_contradicted_was_searched(record)
    assert not passed
    assert "no tag was tried" in reason


def test_logic_small_claim_needs_a_search():
    """A claim well below the filed figure might be a segment — the C09 case."""
    record = {
        "parsed": {"VERDICT": "CONTRADICTED", "CLAIMED": "$7.4 billion", "FILED": "$47.37 billion"},
        "steps": [{"kind": "OBSERVE", "turn": 2, "response": {"status": "found"}}],
        "tool_calls": [{"turn": 1, "args": {"xbrl_tag": "Revenues"}}],
    }
    passed, reason = check_contradicted_was_searched(record)
    assert not passed
    assert "segment" in reason


def test_logic_small_claim_with_a_search_passes():
    """Same shape as C09, but the agent tested an alternative afterwards."""
    record = {
        "parsed": {"VERDICT": "CONTRADICTED", "CLAIMED": "$7.4 billion", "FILED": "$47.37 billion"},
        "steps": [{"kind": "OBSERVE", "turn": 2, "response": {"status": "found"}}],
        "tool_calls": [
            {"turn": 1, "args": {"xbrl_tag": "Revenues"}},
            {"turn": 3, "args": {"xbrl_tag": "InvestmentBankingRevenue"}},
        ],
    }
    passed, _ = check_contradicted_was_searched(record)
    assert passed


def test_logic_prose_answer_fails_format():
    """The C21 and C22 shape: a helpful sentence with no VERDICT line."""
    record = {
        "answer": "The claim is too vague to identify the company. Please provide a name.",
        "parsed": {f: None for f in ("VERDICT", "CLAIMED", "FILED", "TAG", "REASONING")},
    }
    passed, reason = check_answer_format(record)
    assert not passed
    assert "missing" in reason


def test_logic_invented_verdict_fails_format():
    """A verdict outside the four allowed words is the model inventing a category."""
    record = {
        "answer": "VERDICT: PARTIALLY_SUPPORTED\nCLAIMED: x\nFILED: y\nTAG: z\nREASONING: w",
        "parsed": {
            "VERDICT": "PARTIALLY_SUPPORTED",
            "CLAIMED": "x",
            "FILED": "y",
            "TAG": "z",
            "REASONING": "w",
        },
    }
    passed, reason = check_answer_format(record)
    assert not passed
    assert "not one of the four" in reason
