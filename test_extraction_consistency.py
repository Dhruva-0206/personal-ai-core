"""
Diagnostic only: calls extraction.extract() directly (no pipeline, no graph
writes) N times on identical input and reports how consistently a State is
produced. Makes real LLM calls.
"""
import sys
from collections import Counter

import extraction

RUNS = 10
SENTENCES = [
    "I started training for a marathon in December.",
    "I'm registered for the Hyrox event in 2027",
]


def run(text: str):
    print(f"\n=== {text!r} x{RUNS} ===")
    rows = []
    for i in range(1, RUNS + 1):
        r = extraction.extract(text)
        # extract() returns a _fallback() dict on model/parse failure; its
        # summary is the raw text itself, so flag it rather than count it as "no state".
        fallback = r.get("summary") == text[:200]
        states = [(s.get("entity"), s.get("attribute"), s.get("value")) for s in r["states"]]
        ents = [e.get("name") for e in r["entities"]]
        rows.append((fallback, states, r["importance"], ents))
        print(f"{i:2d}. state={'YES' if states else 'no '} fallback={fallback} "
              f"importance={r['importance']} entities={ents}\n    states={states}")
    valid = [r for r in rows if not r[0]]
    with_state = [r for r in valid if r[1]]
    print(f"\nSummary: {len(with_state)}/{len(valid)} valid runs produced a State "
          f"({len(rows) - len(valid)} fallback runs excluded)")
    print("  attributes:", Counter(s[1] for r in with_state for s in r[1]))
    print("  values:    ", Counter(s[2] for r in with_state for s in r[1]))
    print("  state entity:", Counter(s[0] for r in with_state for s in r[1]))
    print("  importance:", Counter(r[2] for r in valid))
    print("  entity sets:", Counter(tuple(r[3]) for r in valid))


if __name__ == "__main__":
    for t in SENTENCES:
        run(t)
