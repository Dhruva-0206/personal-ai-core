"""
Throwaway diagnostic: how often does agent.handle_request("where do I
live") produce a real tool call vs. an explicit refusal vs. a hallucinated
fake tool-call-as-text? Does NOT modify agent.py/skills.py — instead spies
on the shared OpenAI client's chat.completions.create() so the RAW
response from each call agent.py makes internally can be inspected,
while still exercising the real, unmodified handle_request().

Run with: python test_agent_reliability.py
"""
import sys

import agent
import llm_client


def safe_print(*args):
    text = " ".join(str(a) for a in args)
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.buffer.write(text.encode(encoding, errors="replace"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


client = llm_client.get_client()
_original_create = client.chat.completions.create
captured = []


def _spy_create(*args, **kwargs):
    response = _original_create(*args, **kwargs)
    captured.append(response)
    return response


client.chat.completions.create = _spy_create

rows = []

for i in range(1, 11):
    captured.clear()
    final_answer = agent.handle_request("where do I live")

    first_response = captured[0]
    message = first_response.choices[0].message
    finish_reason = first_response.choices[0].finish_reason
    content = message.content
    tool_calls = getattr(message, "tool_calls", None)

    # Reuse agent.py's own detection function rather than reimplementing
    # it, so this diagnostic can never silently drift out of sync with
    # what handle_request() actually checks for.
    retry_triggered = finish_reason != "tool_calls" and agent.is_malformed_or_refused(content)

    if tool_calls:
        behavior = "real_tool_call"
        tool_desc = f"{tool_calls[0].function.name}({tool_calls[0].function.arguments})"
    elif content and "<tool_call>" in content:
        behavior = "hallucinated_text_tool_call"
        tool_desc = ""
    elif content:
        behavior = "explicit_refusal_or_direct_answer"
        tool_desc = ""
    else:
        behavior = "empty_content_no_tool_calls"
        tool_desc = ""

    rows.append({
        "call": i,
        "finish_reason": finish_reason,
        "content_empty": not bool(content),
        "contains_tool_call_tag": bool(content and "<tool_call>" in content),
        "behavior": behavior,
        "tool_desc": tool_desc,
        "final_answer": final_answer,
        "retry_triggered": retry_triggered,
        "raw_calls_made": len(captured),
    })

safe_print(f"\n{'#':<3}{'finish_reason':<15}{'content_empty':<15}{'<tool_call> tag':<17}{'behavior':<30}{'retry_triggered':<16}{'raw_calls':<10}")
for r in rows:
    safe_print(
        f"{r['call']:<3}{str(r['finish_reason']):<15}{str(r['content_empty']):<15}"
        f"{str(r['contains_tool_call_tag']):<17}{r['behavior']:<30}"
        f"{str(r['retry_triggered']):<16}{r['raw_calls_made']:<10}"
    )

safe_print("\n--- Details per call ---")
for r in rows:
    safe_print(f"\nCall {r['call']}: behavior={r['behavior']}")
    safe_print(f"  finish_reason: {r['finish_reason']}")
    safe_print(f"  content_empty: {r['content_empty']}")
    if r["tool_desc"]:
        safe_print(f"  tool call: {r['tool_desc']}")
    safe_print(f"  final_answer: {r['final_answer']!r}")

safe_print("\n--- Behavior counts ---")
counts = {}
for r in rows:
    counts[r["behavior"]] = counts.get(r["behavior"], 0) + 1
for behavior, count in counts.items():
    safe_print(f"  {behavior}: {count}/10")

retries = sum(1 for r in rows if r["retry_triggered"])
safe_print(f"\nretries triggered: {retries}/10")
