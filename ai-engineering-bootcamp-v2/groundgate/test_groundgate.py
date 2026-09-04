"""The gate's own checks, tested against cases it has to get right.

    ../capstone/.venv/bin/pytest test_groundgate.py -q

Two of these exist because the capstone's checks got them wrong first: crediting
an answer whose tool call came back empty, and calling a namespace prefix a
fabrication.
"""

from groundgate import Gate, Run, ToolCall, summarise
from sources import DictSource

SOURCE = DictSource({"INV-88421": 1_200_000, "INV-90001": 840_000})
CITED = lambda a: (a or None)


def run(answer, calls=(), value=None):
    return Run(answer=answer, tool_calls=list(calls), claimed_value=value)


def test_passes_when_everything_checks_out():
    g = Gate(SOURCE, extract_citation=CITED)
    v = g.check(run("INV-88421", [ToolCall("invoices", result={"id": "INV-88421"})],
                    value=1_200_000))
    assert v.outcome == "pass"


def test_blocks_a_citation_that_does_not_exist():
    g = Gate(SOURCE, extract_citation=CITED)
    v = g.check(run("INV-00000", [ToolCall("invoices", result={"x": 1})]))
    assert v.outcome == "block" and v.citation_exists is False


def test_a_fabricated_citation_blocks_even_when_it_looked():
    """The dangerous pair: a real lookup and an invented source."""
    g = Gate(SOURCE, extract_citation=CITED)
    v = g.check(run("INV-00000", [ToolCall("invoices", result={"rows": [1, 2]})]))
    assert v.outcome == "block"


def test_flags_an_answer_that_consulted_nothing():
    g = Gate(SOURCE, extract_citation=CITED)
    v = g.check(run("INV-88421"))
    assert v.outcome == "flag" and v.looked is False


def test_an_empty_tool_result_is_not_a_lookup():
    """Nine runs in the capstone scored as correct having consulted nothing.

    A call that returns nothing must not satisfy "did it look", or the check is
    satisfiable by calling a tool and ignoring the failure.
    """
    g = Gate(SOURCE, extract_citation=CITED)
    v = g.check(run("INV-88421", [ToolCall("invoices", result=[])]))
    assert v.looked is False


def test_blocks_a_no_lookup_answer_when_asked_to():
    g = Gate(SOURCE, extract_citation=CITED, block_when_no_lookup=True)
    assert g.check(run("INV-88421")).outcome == "block"


def test_flags_when_there_is_no_citation_to_check():
    g = Gate(SOURCE)
    v = g.check(run("We spent $1.2m.", [ToolCall("invoices", result={"a": 1})]))
    assert v.outcome == "flag" and v.citation is None


def test_flags_a_real_source_holding_a_different_value():
    g = Gate(SOURCE, extract_citation=CITED)
    v = g.check(run("INV-90001", [ToolCall("invoices", result={"a": 1})], value=1_200_000))
    assert v.outcome == "flag" and v.value_matches is False


def test_rounding_is_not_a_disagreement():
    """Prose rounds. 1.6 billion against a filed 1,595,000,000 is agreement."""
    src = DictSource({"X": 1_595_000_000})
    g = Gate(src, extract_citation=CITED)
    v = g.check(run("X", [ToolCall("t", result={"a": 1})], value=1_600_000_000))
    assert v.outcome == "pass"


def test_default_citation_parser_reads_a_labelled_line():
    from groundgate import default_extract_citation as parse
    assert parse("The total was $1.2m.\nSource: INV-88421") == "INV-88421"
    assert parse("no citation here") is None


def test_namespace_prefix_is_not_a_fabrication():
    """The right concept with the namespace glued on is a formatting difference."""
    from sources import SecTagSource
    s = SecTagSource("JPM", facts={"InterestExpense": {}})
    assert s.exists("us-gaap_InterestExpense")
    assert s.exists("us-gaap:InterestExpense")
    assert not s.exists("InterestExpenseThatNobodyFiles")


def test_summarise_counts_what_matters():
    g = Gate(SOURCE, extract_citation=CITED)
    s = summarise(g.check_all([
        run("INV-88421", [ToolCall("t", result={"a": 1})]),
        run("INV-00000", [ToolCall("t", result={"a": 1})]),
        run("INV-88421"),
    ]))
    assert s["n"] == 3 and s["fabricated_citations"] == 1
    assert s["answered_without_looking"] == 1 and s["block"] == 1
