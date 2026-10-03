"""
Isolated, deterministic test of agent.py's detection + single-retry logic on
the FOLLOW-UP answer-generation call (the second call, after a tool result).
Fake response objects, no real API calls — same approach as
test_agent_retry_isolated.py, which covers the tool-selection call.
Uses the set_reminder stub skill, so no graph/Neo4j access either.

Run with: python test_followup_retry_isolated.py
"""
import logging
import sys
from types import SimpleNamespace

import agent
import llm_client


def safe_print(*args):
    text = " ".join(str(a) for a in args)
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.buffer.write(text.encode(encoding, errors="replace"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def make_response(finish_reason, content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(finish_reason=finish_reason, message=message)
    return SimpleNamespace(choices=[choice])


def make_tool_call(name, arguments):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(id="fake-tool-call-id", type="function", function=function)


class FakeChatCompletions:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0
        self.messages_seen = []

    def create(self, **kwargs):
        self.messages_seen.append(kwargs["messages"])
        response = self.responses[self.calls]  # IndexError here == unexpected extra call (a loop)
        self.calls += 1
        return response


class FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=FakeChatCompletions(responses))


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


def run_with_fake(responses, fn):
    fake = FakeClient(responses)
    handler = ListHandler()
    agent.logger.addHandler(handler)
    original_get_client = llm_client.get_client
    llm_client.get_client = lambda: fake
    try:
        return fn(fake, handler)
    finally:
        llm_client.get_client = original_get_client
        agent.logger.removeHandler(handler)


passed = 0
failed = 0


def check(label, condition):
    global passed, failed
    status = "PASS" if condition else "FAIL"
    safe_print(f"  [{status}] {label}")
    if condition:
        passed += 1
    else:
        failed += 1


REMINDER_CALL = make_response(
    finish_reason="tool_calls", content=None,
    tool_calls=[make_tool_call("set_reminder", '{"text": "call mom", "time": "6pm"}')],
)
BAD_FOLLOW_UP = "\n<tool_call>\n<function=query_memory>\n</function>\n</tool_call>\n"


# --- Case 1: malformed follow-up -> one retry -> clean answer ---
safe_print("\n=== Case 1: malformed follow-up content triggers exactly one retry ===")
def case1(fake, handler):
    answer = agent.handle_request("remind me to call mom at 6pm")
    check("final answer is the retry's clean content", answer == "Reminder is set.")
    check("3 raw calls (tool selection + follow-up + 1 follow-up retry)", fake.chat.completions.calls == 3)
    check("retry used the SAME follow-up messages (incl. tool result)",
          fake.chat.completions.messages_seen[1] == fake.chat.completions.messages_seen[2]
          and fake.chat.completions.messages_seen[2][-1]["role"] == "tool")
    check("exactly 1 WARNING, labeled as the follow-up call",
          len(handler.records) == 1 and "follow-up call" in handler.records[0])
run_with_fake([REMINDER_CALL, make_response("stop", BAD_FOLLOW_UP), make_response("stop", "Reminder is set.")], case1)

# --- Case 2: clean follow-up is untouched ---
safe_print("\n=== Case 2: clean follow-up content is untouched (no retry, no warnings) ===")
def case2(fake, handler):
    answer = agent.handle_request("remind me to call mom at 6pm")
    check("final answer returned as-is", answer == "Reminder confirmed, no retry needed!")
    check("exactly 2 raw calls", fake.chat.completions.calls == 2)
    check("no WARNINGs", handler.records == [])
run_with_fake([REMINDER_CALL, make_response("stop", "Reminder confirmed, no retry needed!")], case2)

# --- Case 3: both original and retry malformed -> accept retry, two WARNINGs, no loop ---
safe_print("\n=== Case 3: follow-up AND retry both malformed -> accepted, 2 WARNINGs, no 3rd follow-up call ===")
def case3(fake, handler):
    second_bad = '{"name": "query_memory", "arguments": {"question": "x"}}'
    answer = agent.handle_request("remind me to call mom at 6pm")
    check("retry's (still-malformed) content is accepted as final", answer == second_bad)
    check("exactly 3 raw calls (no further retry, no loop)", fake.chat.completions.calls == 3)
    check("exactly 2 WARNINGs, both labeled follow-up call",
          len(handler.records) == 2 and all("follow-up call" in r for r in handler.records))
    check("second WARNING flags that the failure persisted after retry", "persisted" in handler.records[1])
run_with_fake([REMINDER_CALL, make_response("stop", BAD_FOLLOW_UP),
               make_response("stop", '{"name": "query_memory", "arguments": {"question": "x"}}')], case3)

# --- Case 4: tool-selection failure handling is unchanged and distinguishable in logs ---
safe_print("\n=== Case 4: tool-selection retry still works, and its WARNING is distinguishable ===")
def case4(fake, handler):
    answer = agent.handle_request("remind me to call mom at 6pm")
    check("final answer correct", answer == "Done.")
    check("3 raw calls (failed selection + retried selection + follow-up)", fake.chat.completions.calls == 3)
    check("1 WARNING, labeled tool-calling (NOT follow-up call)",
          len(handler.records) == 1 and "tool-calling" in handler.records[0]
          and "follow-up call" not in handler.records[0])
run_with_fake([make_response("stop", BAD_FOLLOW_UP), REMINDER_CALL, make_response("stop", "Done.")], case4)

safe_print(f"\n{passed} passed, {failed} failed")
if failed:
    raise SystemExit(1)
