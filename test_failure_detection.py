"""
Isolated, deterministic test of agent.py's redesigned is_malformed_or_refused()
detection — no real API calls. Directly exercises the same gated check
handle_request() uses (finish_reason != "tool_calls" and
agent.is_malformed_or_refused(content)) against five constructed cases:
the three known failure variants (hallucinated <tool_call> tag text, raw
JSON prose, and a refusal using wording NOT in the heuristic fragment
list) plus a normal tool-call success and a normal plain-text answer.

Case 3 (novel-wording refusal) is expected to NOT be caught — that's the
heuristic's documented limitation being demonstrated, not a bug in this
test or in agent.py. Reported honestly either way.

Run with: python test_failure_detection.py
"""
import sys

import agent


def safe_print(*args):
    text = " ".join(str(a) for a in args)
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.buffer.write(text.encode(encoding, errors="replace"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def detect(finish_reason, content):
    """Same gating handle_request() applies before calling is_malformed_or_refused()."""
    return finish_reason != "tool_calls" and agent.is_malformed_or_refused(content)


results = []


def report(label, finish_reason, content, expected, note=""):
    actual = detect(finish_reason, content)
    status = "PASS" if actual == expected else "FAIL"
    results.append(status)
    safe_print(f"[{status}] {label}")
    safe_print(f"       finish_reason={finish_reason!r}  expected_detected={expected}  actual_detected={actual}")
    if note:
        safe_print(f"       note: {note}")
    safe_print(f"       content: {content!r}")
    safe_print("")


safe_print("=== Case 1: hallucinated <tool_call> tag text (known variant #1) ===")
report(
    "tool_call tag text is detected as structurally malformed",
    finish_reason="stop",
    content="\n<tool_call>\n<function=query_memory>\n<parameter=question>\nwhere do I live\n</parameter>\n</function>\n</tool_call>\n",
    expected=True,
)

safe_print("=== Case 2: raw JSON prose (known variant #2) ===")
report(
    "raw JSON tool-call prose is detected as structurally malformed",
    finish_reason="stop",
    content='{\n  "name": "query_memory",\n  "arguments": {\n    "question": "what was my job before becoming a data scientist"\n  }\n}',
    expected=True,
)

safe_print("=== Case 3: refusal with NOVEL wording, not in _REFUSAL_FRAGMENTS (known variant #3, deliberately re-worded) ===")
report(
    "refusal phrased differently than every fragment in the heuristic list",
    finish_reason="stop",
    content="Sorry, but I don't think I'm the right tool for figuring that out.",
    expected=False,
    note=(
        "Deliberately avoids all 5 fragments (\"don't have access\", \"can't help "
        "with that\", \"unable to\", \"no way to determine\", \"not able to\"). "
        "Expected to slip through undetected — this is the heuristic's known, "
        "documented limitation (a small fixed fragment list cannot generalize to "
        "unseen phrasing), not a defect in this implementation. PASS here means "
        "\"behaved as honestly expected\", not \"caught the failure\"."
    ),
)

safe_print("=== Case 4: normal successful tool-call response ===")
report(
    "a successful tool call is never flagged, regardless of content",
    finish_reason="tool_calls",
    content=None,
    expected=False,
)

safe_print("=== Case 5: normal plain-text answer, no tool needed (\"hello\") ===")
report(
    "a legitimate conversational answer is never flagged just for being plain text",
    finish_reason="stop",
    content="Hello! I'm doing well, thanks for asking. How can I help you today?",
    expected=False,
)

passed = results.count("PASS")
failed = results.count("FAIL")
safe_print(f"{passed} passed, {failed} failed (of {len(results)})")
if failed:
    raise SystemExit(1)
