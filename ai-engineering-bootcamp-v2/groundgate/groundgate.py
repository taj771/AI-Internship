"""Refuse to let an answer out when its source is fiction.

    from groundgate import Gate, Run

    gate = Gate(source=my_invoice_system)
    verdict = gate.check(run)
    if verdict.outcome == "block":
        ...


THE FAILURE THIS EXISTS FOR

An assistant is asked how much the company spent on AWS last quarter. It answers
"$1.2 million, source: invoice INV-88421". The figure is right. There is no
invoice INV-88421.

That pairing — a correct number beside an invented citation — is worse than a
wrong number, and it is worse for a specific reason: **a wrong number is caught
by the next person who looks, and a wrong source is caught by nobody.** Whoever
checks the figure finds it correct and carries the citation forward into a
report, where it acquires the authority of something that was verified.

It is not rare. Asked which concept a bank filed a figure under, with the figure
stripped out of the question so it had to be recalled rather than repeated, a
general model named a concept the filer had never once used **five times in
eight** — and got the number itself right four times in eight. The measurement
is in ../capstone/COVERAGE_STUDY.md and reproduced by demo_sec.py here.


WHAT IT CHECKS, AND WHAT IT CANNOT

Three questions, in this order, because they fail in this order:

    1. Did it look?        an answer produced with no tool call at all
    2. Does the source     the cited identifier is absent from the system
       exist?              of record
    3. Does the source     the identifier is real and holds something else
       say that?

Only the second is unusual, and it is the one that needs a system of record.
Where there is none — an assistant summarising free text with no identifiers —
this degrades to check 1, which is much weaker, and the Gate says so rather than
returning a confident pass.

What it does **not** do is decide whether an answer is true. All three checks can
pass on an answer that is wrong: the assistant may have cited a real invoice,
quoted it correctly, and answered the wrong question. This narrows the ways an
answer can be unfounded; it does not establish that it is founded.


WHY THE DEFAULT FOR "DID NOT LOOK" IS FLAG AND NOT BLOCK

An answer that consulted nothing is not necessarily wrong — a model may know a
fact outright — and blocking it would stop correct answers. But it must not be
credited either. In the capstone's evaluation, nine of fifty runs made no tool
call, every one of them answered with the commonest correct label, and all nine
scored as hits. The honest score fell from 68% to 62% when they stopped being
credited. So: surfaced always, blocked only if the caller asks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Protocol, runtime_checkable

Outcome = Literal["pass", "flag", "block"]


@runtime_checkable
class Source(Protocol):
    """The system of record an answer claims to come from.

    Two methods, and only the first is required. `exists` answers "is this a
    real identifier here"; `value` answers "and what does it hold", which many
    systems cannot do cheaply and none is obliged to.
    """

    def exists(self, citation: str) -> bool: ...

    def value(self, citation: str) -> Any | None: ...


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Any = None

    @property
    def returned_something(self) -> bool:
        """A call that errored or came back empty did not consult anything.

        Counting it would make "did it look?" satisfiable by calling a tool and
        ignoring the failure, which is the shape of the bug this check exists to
        catch rather than a fix for it.
        """
        if self.result is None:
            return False
        if isinstance(self.result, (str, bytes, list, dict, tuple, set)):
            return len(self.result) > 0
        return True


@dataclass
class Run:
    """One recorded answer, and what the assistant did to produce it."""

    answer: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    claimed_value: Any = None       # optional, if the caller parsed it already


@dataclass
class Verdict:
    outcome: Outcome
    looked: bool
    citation: str | None
    citation_exists: bool | None     # None when no citation was found to check
    value_matches: bool | None       # None when not checked
    reasons: list[str]

    def __str__(self) -> str:
        return f"{self.outcome.upper()}: " + "; ".join(self.reasons)


# A citation is whatever the answer points at. The default reads the shape most
# assistants are asked to produce — a labelled line — and deliberately not free
# prose: guessing which noun in a sentence was meant as the source is the kind of
# inference that would make this component's own output unverifiable.
DEFAULT_CITATION = re.compile(
    r"(?im)^\s*(?:source|citation|reference|tag|id|document)\s*[:=]\s*(\S+)\s*$")


def default_extract_citation(answer: str) -> str | None:
    m = DEFAULT_CITATION.search(answer or "")
    if not m:
        return None
    return m.group(1).strip().strip(".,;`\"'")


class Gate:
    """Three checks between an assistant's answer and the person reading it.

    `source` is the only required argument. `extract_citation` is where a caller
    plugs in their own answer format, which most will need to: the default reads
    "Source: X" on its own line and nothing else, because a looser parser would
    silently return None on answers that did cite something and turn a blocked
    fabrication into a quiet pass.
    """

    def __init__(
        self,
        source: Source,
        extract_citation: Callable[[str], str | None] = default_extract_citation,
        compare: Callable[[Any, Any], bool] | None = None,
        block_when_no_lookup: bool = False,
    ) -> None:
        self.source = source
        self.extract_citation = extract_citation
        self.compare = compare
        self.block_when_no_lookup = block_when_no_lookup

    def check(self, run: Run) -> Verdict:
        reasons: list[str] = []

        looked = any(c.returned_something for c in run.tool_calls)
        if not looked:
            reasons.append(
                "answered without consulting anything"
                if not run.tool_calls
                else "every tool call came back empty or errored")

        citation = self.extract_citation(run.answer)
        citation_exists: bool | None = None
        value_matches: bool | None = None

        if citation is None:
            reasons.append("no citation to check")
        else:
            citation_exists = bool(self.source.exists(citation))
            if not citation_exists:
                reasons.append(f"cited `{citation}`, which is not in the source")
            elif run.claimed_value is not None:
                held = getattr(self.source, "value", lambda _: None)(citation)
                if held is None:
                    reasons.append(f"`{citation}` exists; its value could not be read")
                else:
                    value_matches = (self.compare or _close_enough)(run.claimed_value, held)
                    if not value_matches:
                        reasons.append(
                            f"`{citation}` exists but holds {held!r}, "
                            f"not {run.claimed_value!r}")

        # Order matters: a fabricated citation is the finding, whatever else is
        # true of the answer, so it decides the outcome before anything softer.
        if citation_exists is False:
            outcome: Outcome = "block"
        elif not looked and self.block_when_no_lookup:
            outcome = "block"
        elif not looked or citation is None or value_matches is False:
            outcome = "flag"
        else:
            outcome = "pass"

        if outcome == "pass":
            reasons.append("looked, cited a real source, and the source agrees")
        return Verdict(outcome, looked, citation, citation_exists, value_matches, reasons)

    def check_all(self, runs: Iterable[Run]) -> list[Verdict]:
        return [self.check(r) for r in runs]


def _close_enough(claimed: Any, held: Any) -> bool:
    """Numbers within 1.5%, everything else exactly.

    The tolerance is not arbitrary. Prose rounds to two or three significant
    figures — "$1.6 billion" against a filed 1,595,000,000 — so an exact
    comparison would report a disagreement that is only a rounding convention.
    Anything that is not a pair of numbers is compared as written.
    """
    try:
        a, b = float(claimed), float(held)
    except (TypeError, ValueError):
        return str(claimed).strip() == str(held).strip()
    if b == 0:
        return a == 0
    return abs(a - b) / abs(b) <= 0.015


def summarise(verdicts: list[Verdict]) -> dict[str, Any]:
    n = len(verdicts) or 1
    counts = {o: sum(1 for v in verdicts if v.outcome == o)
              for o in ("pass", "flag", "block")}
    return {
        "n": len(verdicts),
        **counts,
        "fabricated_citations": sum(1 for v in verdicts if v.citation_exists is False),
        "answered_without_looking": sum(1 for v in verdicts if not v.looked),
        "no_citation": sum(1 for v in verdicts if v.citation is None),
        "pass_rate": counts["pass"] / n,
    }
