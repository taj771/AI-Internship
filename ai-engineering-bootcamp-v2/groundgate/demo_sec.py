"""The gate, run over answers a model actually gave. The proof, not a mock.

    python3 demo_sec.py

Reads blind_probe.json — 40 claims put to gpt-4o with every figure stripped out
of the question, recorded by blind_probe.py — and asks the gate one thing about
each answer: is the accounting concept it named one JPMorgan has ever filed?

Nothing here is scored against a human label. The check is against the filer's
own fact set, so it is decidable by anyone who doubts it.


WHY THE BLINDED RUN AND NOT THE OTHERS

The capstone has two recorded experiments asking the same question with the
figure left in, and both show almost no fabrication — 1 in 25, 1 in 40. Reading
the transcripts explains it: the model answered "I can't access SEC filings" and
was entirely right to. It declined 24 times out of 25.

Strip the figure out and declining costs it the whole answer, which is the
position an assistant is in when a user asks it something and expects a reply.
That is the case this component exists for, so that is the run it is measured on.
"""

from __future__ import annotations

import json
from pathlib import Path

from groundgate import Gate, Run, ToolCall, summarise
from sources import SecTagSource

HERE = Path(__file__).parent


def main() -> int:
    probe = json.loads((HERE / "blind_probe.json").read_text(encoding="utf-8"))
    rows = probe["results"]
    source = SecTagSource(probe["ticker"])

    # The probe records the tag each answer named rather than the answer text,
    # so the citation is handed to the gate directly. That is what
    # extract_citation is for — a caller's answer format is their own.
    gate = Gate(source=source, extract_citation=lambda a: (a or None))

    runs, declined = [], []
    for r in rows:
        tag = (r.get("tag") or "").strip()
        if not tag or tag.upper() == "UNKNOWN":
            declined.append(r)
            continue
        runs.append(Run(answer=tag, tool_calls=[]))     # no tools, by construction

    verdicts = gate.check_all(runs)
    s = summarise(verdicts)
    real = s["n"] - s["fabricated_citations"]

    print(f"  {probe['model']} · {probe['n']} claims · {probe['ticker']} · no tools, "
          f"figure stripped from the question\n")
    print(f"    {'declined to answer':32} {len(declined):>4}")
    print(f"    {'named a concept the filer files':32} {real:>4}")
    print(f"    {'FABRICATED a concept':32} {s['fabricated_citations']:>4}"
          f"   {s['fabricated_citations']/max(1,len(runs)):.0%} of the answers it gave")
    print(f"\n    every one of those {len(runs)} answers is BLOCKED or PASSED by the gate "
          f"with no human involved")

    bad = [v for v in verdicts if v.citation_exists is False]
    if bad:
        print("\n  fabricated concepts, verbatim:")
        for v in bad[:8]:
            row = next(r for r in rows if (r.get("tag") or "").strip() == v.citation)
            print(f"    {v.citation}")
            print(f"       beside the value {row['value']}   (the sentence asked about "
                  f"{row['figure']}, FY{row['fy']})")

    print("\n  A fabricated concept exists in the us-gaap taxonomy, is spelled")
    print("  correctly, reads plausibly — and the filer has never once used it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
