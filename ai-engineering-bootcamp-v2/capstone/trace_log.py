"""
Recording runs to disk, and reading them back.

Week 3 could show a trace and could not keep one. The agent produced a full
account of its own reasoning, drew it on a Streamlit page, and then the process
ended and the account went with it. Nothing accumulated. That is fine for a demo
and useless for evaluation: you cannot find a pattern in twenty runs you no
longer have.

This file is the whole of the "Trace" step. It does not judge anything — no
pass, no fail, no opinion. Judgement is a human's job in step 5 and a check's
job in step 7, and mixing either of them in here would quietly decide the
answers before the analysis starts.


WHY JSONL AND NOT CSV OR A DATABASE

One run, one line, each line a complete JSON object. Appending a run means
writing one more line, so a crashed batch leaves nineteen good records rather
than one corrupt file. Reading a run means parsing one line, so the file can
grow past what fits in memory without anything changing.

CSV was the obvious alternative and fails on shape: a run holds a list of steps,
each step holds a dictionary of tool arguments, and flattening that into columns
either loses the nesting or invents thirty columns most rows leave empty. The
course's own sample pack ships JSONL for the same reason, with the CSV as a
convenience copy for people who want to annotate in a spreadsheet.


WHY THE HUMAN FIELDS ARE WRITTEN EMPTY

Every record is created with three blank fields — notes, pass/fail, label —
waiting for a person. They are deliberately part of the record rather than a
separate annotation file, so a trace and the judgement made about it cannot
drift apart or be joined up wrongly later.

They are blank and not absent because a missing field reads as "nobody has
looked at this yet" and an empty string reads as the same thing, but only one of
them survives being loaded into a table without turning into a null that has to
be special-cased everywhere.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# Default location. Kept beside the code rather than in a data directory because
# there is exactly one of these files and hiding it makes it easier to forget it
# is the evidence the whole week rests on.
DEFAULT_PATH = Path(__file__).parent / "traces.jsonl"

# The five lines the agent's instruction promises to emit, in the order it
# promises to emit them. Named here rather than inside the parser because step 7
# will want to assert against this exact list — the format check and the parser
# have to agree on what the format is, and the way to guarantee that is to have
# one definition of it.
ANSWER_FIELDS = ("VERDICT", "CLAIMED", "FILED", "TAG", "REASONING")

# The only four verdicts the instruction allows. Anything else in a VERDICT line
# is the model inventing a category, which is a failure whether or not the
# reasoning underneath it is sound.
ALLOWED_VERDICTS = ("SUPPORTED", "CONTRADICTED", "DEFINITION_MISMATCH", "NOT_CHECKABLE")


def parse_answer(answer: str) -> dict:
    """Pull the five promised fields out of the agent's final text.

    This parser is deliberately forgiving, and that is a design decision rather
    than laziness.

    The tempting version raises an error when a field is missing. It would be
    wrong here, because a missing field is not a problem with the *recording* —
    it is one of the failures we are trying to catch. An agent that answers with
    a verdict and no TAG line has broken rule 2 of its own instruction, and the
    job of this function is to write that down faithfully so a check in step 7
    can count it. A parser that threw would destroy the evidence and stop the
    batch besides.

    So: every field the agent supplied comes back with its value, and every
    field it did not comes back as None. None here always means "the agent did
    not say", never "the recording failed".
    """
    found = {field: None for field in ANSWER_FIELDS}

    if not answer:
        return found

    # Line-oriented rather than a single regex over the whole text, because the
    # REASONING value legitimately runs to two sentences and may itself contain
    # a colon. Matching on "line starts with a known field name" keeps that from
    # being mistaken for a new field.
    #
    # `current` is what makes a wrapped value survive. The instruction allows
    # REASONING two sentences, and two sentences wrap; the first version of this
    # parser took only the line that carried the field name and threw the rest
    # away, so a reasoning that ran over kept half of itself and looked complete.
    # A line that names no field is therefore treated as a continuation of the
    # field above it rather than as noise to skip.
    current = None

    for line in answer.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        matched = None
        for field in ANSWER_FIELDS:
            if stripped.upper().startswith(f"{field}:"):
                matched = field
                break

        if matched:
            found[matched] = stripped[len(matched) + 1 :].strip()
            current = matched
        elif current:
            found[current] = f"{found[current]} {stripped}".strip()

    return found


def build_record(
    trace_id: str,
    claim: str,
    answer: str | None,
    trace: list[dict],
    *,
    provider: str,
    model: str,
    duration_s: float | None = None,
    error: str | None = None,
    expected_verdict: str | None = None,
    why_this_claim: str | None = None,
    run_label: str = "baseline",
    instruction_version: str = "",
) -> dict:
    """Assemble one run into the record that gets written to disk.

    `expected_verdict` and `why_this_claim` describe the claim, not the run.
    They are what we believed before the agent answered — checked by hand
    against data.sec.gov — and they are recorded so that a disagreement between
    expectation and outcome is visible without going back to a separate
    spreadsheet.

    They are not a grade. A run can match the expected verdict and still be a
    bad run: right answer, wrong tag, no evidence, four wasted lookups. Step 5
    is reading for exactly that, which is why the human fields stay blank here
    even when the verdict happens to line up.
    """
    # Tool calls are pulled out of the step list into their own field because
    # almost every question worth asking is about them — how many, which tags,
    # did the model retry, did it ever call the tool at all — and a check that
    # has to re-filter the step list every time it wants one of those answers is
    # a check that will eventually filter it slightly differently somewhere.
    tool_calls = [
        {
            "tool": step.get("tool"),
            "args": step.get("args", {}),
            # Which model turn asked for this. Two calls sharing a turn number
            # were requested together, before either result existed; two calls
            # with different turn numbers mean the second was chosen after
            # reading the first. Recorded as a number rather than as a
            # "hedged: true" flag on purpose — this file records what happened
            # and step 7 decides what counts as bad.
            "turn": step.get("turn"),
        }
        for step in trace
        if step.get("kind") == "ACT"
    ]

    observations = [
        step.get("response") for step in trace if step.get("kind") == "OBSERVE"
    ]

    return {
        "trace_id": trace_id,
        # Which pass of the whole claim set this run belongs to. Step 8 changes
        # the agent and runs the identical twenty claims again, and the two sets
        # have to be comparable line by line: same claim, same expectation, one
        # thing different. Naming the pass inside the record means the before
        # and the after can sit in one file, or in two, without the comparison
        # depending on which file someone happened to open.
        "run_label": run_label,
        # UTC and ISO format, so that runs recorded on a laptop and runs
        # recorded anywhere else sort together correctly.
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # --- what was asked ---
        "claim": claim,
        "expected_verdict": expected_verdict,
        "why_this_claim": why_this_claim,
        # --- which system answered ---
        # Recorded per run, not per file. A batch run half on gpt-4o and half on
        # gemini would otherwise be indistinguishable afterwards, and week 3
        # already established that the engine changes the verdict on hard
        # claims. A trace that cannot name its own engine cannot be compared
        # with anything.
        "provider": provider,
        "model": model,
        # Which of the two instruction texts steered this run. Stored per run
        # rather than inferred from the filename, because the comparison this
        # week rests entirely on knowing which text produced which result, and
        # a filename is a convention that someone eventually gets wrong.
        "instruction_version": instruction_version,
        # --- what happened ---
        "steps": trace,
        "tool_calls": tool_calls,
        "n_tool_calls": len(tool_calls),
        "observations": observations,
        "answer": answer,
        "parsed": parse_answer(answer or ""),
        "duration_s": duration_s,
        # A run that crashed is still a run and still gets a line. Dropping it
        # would quietly improve every rate we compute afterwards, which is the
        # kind of measurement error that flatters the thing being measured.
        "error": error,
        # --- what a human thought, filled in during step 5 ---
        "your_notes": "",
        "your_pass_fail": "",
        "your_failure_label": "",
    }


def append_record(record: dict, path: Path = DEFAULT_PATH) -> None:
    """Add one run to the end of the file, creating it if needed."""
    # ensure_ascii=False so a company name with an accent in it stays readable
    # when the file is opened by eye, which it will be, repeatedly.
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def load_records(path: Path = DEFAULT_PATH) -> list[dict]:
    """Read every run back. Returns [] if nothing has been recorded yet."""
    if not Path(path).exists():
        return []

    records = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def save_records(records: list[dict], path: Path = DEFAULT_PATH) -> None:
    """Rewrite the whole file. Used when annotations are edited, not when runs
    are added — adding uses append_record, which cannot lose the other lines."""
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


if __name__ == "__main__":
    # Checked without an agent and without a network, the same way sec_tool.py
    # is: hand a known answer to the parser and confirm it comes back in pieces,
    # including the piece that is missing.
    sample = """VERDICT: DEFINITION_MISMATCH
CLAIMED: $132.3 billion
FILED: $128.69B
REASONING: The claimed figure is JPMorgan's managed-basis revenue: real, but
not what was filed under Revenues."""

    parsed = parse_answer(sample)
    for field in ANSWER_FIELDS:
        value = parsed[field]
        marker = "  " if value is not None else "!!"
        print(f"{marker} {field:<10} {value}")
    print("\nTAG is None above, and should be — the sample answer omits it.")
    print("That is a rule-2 violation, recorded rather than raised.")
