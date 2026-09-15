"""
Isolated, deterministic test of agent.py's detection + single-retry logic
— constructs fake response objects instead of relying on the real model
happening to fail, so this proves the retry mechanism itself works
correctly regardless of whether the live model fails during any given
test run. Monkey-patches llm_client.get_client() to return a scripted
fake client for the duration of each test, then restores it.

Run with: python test_agent_retry_isolated.py
"""
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

    def create(self, **kwargs):
        response = self.responses[self.calls]
        self.calls += 1
        return response


class FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=FakeChatCompletions(responses))


def with_fake_client(responses, fn):
    fake = FakeClient(responses)
    original_get_client = llm_client.get_client
    llm_client.get_client = lambda: fake
    try:
        return fn(fake)
    finally:
        llm_client.get_client = original_get_client


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


# --- Test 1: hallucinated <tool_call> text, retry succeeds with a direct answer ---
safe_print("\n=== Test 1: hallucinated <tool_call> text failure, retry succeeds (direct answer) ===")
responses_1 = [
    make_response(
        finish_reason="stop",
        content='\n<tool_call>\n<function=make_note>\n<parameter=x>\n1\n</parameter>\n</function>\n</tool_call>\n',
    ),
    make_response(finish_reason="stop", content="Hello, real answer after retry!"),
]


def run_test_1(fake):
    answer = agent.handle_request("hello")
    check("final answer is the retry's content", answer == "Hello, real answer after retry!")
    check("exactly 2 raw calls made (1 failure + 1 retry, no more)", fake.chat.completions.calls == 2)


with_fake_client(responses_1, run_test_1)


# --- Test 2: explicit refusal phrase, retry succeeds with a real tool call ---
safe_print("\n=== Test 2: explicit refusal failure, retry succeeds (real tool call -> skill -> follow-up) ===")
responses_2 = [
    make_response(
        finish_reason="stop",
        content="I can't help with that question using the available tools.",
    ),
    make_response(
        finish_reason="tool_calls",
        content=None,
        tool_calls=[make_tool_call("set_reminder", '{"text": "call mom", "time": "6pm"}')],
    ),
    make_response(finish_reason="stop", content="Reminder confirmed!"),
]


def run_test_2(fake):
    answer = agent.handle_request("remind me to call mom at 6pm")
    check("final answer is the follow-up call's content", answer == "Reminder confirmed!")
    check("exactly 3 raw calls made (1 failure + 1 retry + 1 follow-up)", fake.chat.completions.calls == 3)


with_fake_client(responses_2, run_test_2)


# --- Test 3: retry ALSO fails the same check — must not retry a second time ---
safe_print("\n=== Test 3: retry also fails the same check — no second retry, uses retry's result anyway ===")
responses_3 = [
    make_response(finish_reason="stop", content="I can't help with that question using the available tools."),
    make_response(finish_reason="stop", content="I can't help with that question using the available tools."),
]


def run_test_3(fake):
    answer = agent.handle_request("remind me to call mom at 6pm")
    check("final answer is the (still-failing) retry's content",
          answer == "I can't help with that question using the available tools.")
    check("exactly 2 raw calls made (1 failure + 1 retry, NOT a third call)", fake.chat.completions.calls == 2)


with_fake_client(responses_3, run_test_3)


# --- Test 4: normal successful path is completely unaffected (no extra calls) ---
safe_print("\n=== Test 4: normal tool_calls success on the FIRST call — no retry triggered ===")
responses_4 = [
    make_response(
        finish_reason="tool_calls",
        content=None,
        tool_calls=[make_tool_call("set_reminder", '{"text": "call mom", "time": "6pm"}')],
    ),
    make_response(finish_reason="stop", content="Reminder confirmed, no retry needed!"),
]


def run_test_4(fake):
    answer = agent.handle_request("remind me to call mom at 6pm")
    check("final answer is correct", answer == "Reminder confirmed, no retry needed!")
    check("exactly 2 raw calls made (normal path: tool call + follow-up, no retry)",
          fake.chat.completions.calls == 2)


with_fake_client(responses_4, run_test_4)


safe_print(f"\n{passed} passed, {failed} failed")
if failed:
    raise SystemExit(1)
