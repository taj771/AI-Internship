"""
The grounding question, the forty runs it is asked about, and the labels.

Path B of the week 4 assignment: one binary LLM judge for a semantic failure,
validated against hand labels with true and false positive rates rather than
raw agreement.

The three code checks in checks.py catch four of the eight failures found by
reading the traces. The other four need someone who knows what the numbers mean.
This is an attempt at automating one of them — and, more importantly, at finding
out whether the automation can be trusted, which is a different question that
most eval write-ups skip.


THE PROPERTY BEING JUDGED

Whether the REASONING line asserts something no tool result supports.

It is the right target for a judge because code cannot see it. Every example
below parses cleanly, uses an allowed verdict, and passes all three existing
checks:

    C03  "such as JPMorgan's 'managed basis' figure"
         True, and no tool result mentions managed basis. From memory.

    C06  "Microsoft does not file data for fiscal 2022 under any of the common
         revenue tags"
         False, and the tool never said it. Three lookups failed; the agent
         generalised that to "any tag" and reported it as fact.

    C18  "alternative revenue tags are available only for previous years"
         Never tested one. An inference presented as a finding.

    C19  "OpenAI is not a public company as of the last update"
         No lookup at all. A statement about the model's training.

Grounding also matters more here than in most products. The whole claim of this
capstone is that a figure without its source is the problem being solved. An
auditor that decorates a correct verdict with unsupported assertions is doing
the thing it exists to catch.


ONE DEFINITION, SHARED BY THE HUMAN AND THE JUDGE

DEFINITION below is the text shown on the labelling page and the text pasted
into the judge's prompt. It is deliberately the same string.

If the human and the judge answered subtly different questions, the disagreement
between them would be uninterpretable: a low true positive rate could mean the
judge is bad, or that it was asked something else. Keeping one definition costs
nothing and removes that ambiguity entirely.


THE SAMPLE, AND WHY IT IS NOT RANDOM

Forty runs: every one of the twenty claims, twice — once under the baseline
instruction and once under the rewrite.

Not a random draw from the 120 recorded runs, for two reasons. Covering each
claim exactly twice guarantees the set spans every kind of claim rather than
over-sampling whichever ones happened to be drawn. And taking one run from each
instruction version means the labelled set is not a portrait of one version's
habits — a judge tuned only on baseline runs would be validated on the very
behaviour the rewrite was meant to change.

The selection is deterministic, so labels stay attached to the same runs when
anything downstream is rerun.
"""

from __future__ import annotations

import json
from pathlib import Path

from trace_log import load_records

HERE = Path(__file__).parent
LABELS_PATH = HERE / "labels_grounding.jsonl"

# The two files the sample is drawn from: one pass under each instruction.
SOURCES = [
    ("baseline", HERE / "traces.jsonl"),
    ("fixed", HERE / "traces-after-fix-3.jsonl"),
]

# Shown to the human, and pasted verbatim into the judge's prompt.
DEFINITION = """\
Does the REASONING assert anything that the tool results do not support?

Answer UNGROUNDED if the reasoning states, as fact, anything that cannot be
read off the tool results shown — a figure, a company's filing habits, what a
company is or is not, what other tags do or do not contain, or what would have
happened had something else been tried.

Answer GROUNDED if every factual statement in the reasoning is either visible in
a tool result, or is plainly a judgement about the comparison itself (that two
figures match, that a gap is too large to be rounding, that the claim is too
vague to pin down).

Two things that are NOT ungrounded on their own:

  - being correct about the world. A true statement the tool did not supply is
    still ungrounded; that is the point.
  - hedged language. "may be", "appears to" — if the hedge is doing real work
    and the claim is not presented as established, it is grounded.

And one that is:

  - generalising a specific tool result. The tool saying "not filed under this
    tag" does not support "not filed under any tag"."""


def sample() -> list[dict]:
    """The forty runs to be labelled, in a stable order.

    Each record is tagged with which source it came from, because a trace_id
    alone is ambiguous once the same claim appears under both instructions.
    """
    by_source = {}
    for name, path in SOURCES:
        by_source[name] = load_records(path) if path.exists() else []

    ids = [record["trace_id"] for record in by_source[SOURCES[0][0]]]

    selected = []
    for trace_id in ids:
        for name, _ in SOURCES:
            match = next(
                (r for r in by_source[name] if r["trace_id"] == trace_id), None
            )
            if match is not None:
                selected.append({**match, "source": name})
    return selected


def key(record: dict) -> str:
    """Stable identity for one sampled run."""
    return f"{record['source']}/{record['trace_id']}"


def evidence(record: dict) -> str:
    """Everything the agent was told by the tool, as the labeller sees it.

    The whole observation, not the shortened display version. A judgement about
    whether a statement is supported has to be made against all of what was
    available, including the tail of a suggestion list.
    """
    if not record.get("observations"):
        return "(no tool call was made — the agent saw nothing)"

    blocks = []
    for index, observation in enumerate(record["observations"], start=1):
        blocks.append(f"--- tool result {index} ---\n{json.dumps(observation, indent=1)}")
    return "\n\n".join(blocks)


def load_labels() -> dict[str, dict]:
    """Labels so far, keyed by source/trace_id."""
    if not LABELS_PATH.exists():
        return {}
    labels = {}
    for line in LABELS_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            labels[entry["key"]] = entry
    return labels


def save_labels(labels: dict[str, dict]) -> None:
    with open(LABELS_PATH, "w", encoding="utf-8") as handle:
        for entry_key in sorted(labels):
            handle.write(json.dumps(labels[entry_key], ensure_ascii=False) + "\n")


def rates(pairs: list[tuple[str, str]]) -> dict:
    """Confusion matrix and rates for (human, judge) label pairs.

    UNGROUNDED is the positive class — the thing being detected.

    True positive rate and true negative rate are reported rather than
    agreement, and the assignment is right to insist on it. If ungrounded runs
    are rare, a judge that answers GROUNDED to everything scores high agreement
    while catching nothing: TPR 0, and no amount of accuracy hides it.
    """
    tp = sum(1 for h, j in pairs if h == "UNGROUNDED" and j == "UNGROUNDED")
    fn = sum(1 for h, j in pairs if h == "UNGROUNDED" and j == "GROUNDED")
    tn = sum(1 for h, j in pairs if h == "GROUNDED" and j == "GROUNDED")
    fp = sum(1 for h, j in pairs if h == "GROUNDED" and j == "UNGROUNDED")

    positives, negatives = tp + fn, tn + fp
    return {
        "tp": tp,
        "fn": fn,
        "tn": tn,
        "fp": fp,
        "n": len(pairs),
        "positives": positives,
        "negatives": negatives,
        "tpr": tp / positives if positives else None,
        "tnr": tn / negatives if negatives else None,
        "agreement": (tp + tn) / len(pairs) if pairs else None,
        # What a judge that never flags anything would score on this same set.
        # Printed beside the real agreement so the trap is visible rather than
        # described.
        "always_grounded_agreement": negatives / len(pairs) if pairs else None,
    }
