"""
Isolated, deterministic test of agent.handle_request()'s response-shape and
tool-call guards (fake client, no real API/graph calls). These failures used
to raise out of handle_request — they happen before or around the content-
quality retry covered by test_agent_retry_isolated.py and
test_followup_retry_isolated.py. Each case below was an uncaught exception
(or, for empty answers, a None return) found by the QA audit, plus a few
adjacent shapes. The contract now: handle_request ALWAYS returns a non-empty
string and never raises; a rejected high-stakes call never becomes a pending
confirmation.

Run with: python test_agent_guards_isolated.py
"""
import logging
import sys
from types import SimpleNamespace

import agent
import llm_client
import retrieval

logging.disable(logging.CRITICAL)  # these cases deliberately log warnings/exceptions

passed = 0
failed = 0


def safe_print(*args):
    text = " ".join(str(a) for a in args)
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.buffer.write(text.encode(encoding, errors="replace"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        safe_print(f"  [PASS] {label}")
    else:
        failed += 1
        safe_print(f"  [FAIL] {label}")
        if detail:
            safe_print(f"         {detail}")


def response(finish_reason, content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason, message=message)])


def tool_call(name, arguments):
    return SimpleNamespace(id="call-1", type="function", function=SimpleNamespace(name=name, arguments=arguments))


class FakeClient:
    """Each item in `script` is a response object to return, or an Exception instance to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def run(script):
    """Returns (answer_or_None, raised_exception_or_None, client)."""
    client = FakeClient(script)
    real = llm_client.get_client
    llm_client.get_client = lambda: client
    try:
        return agent.handle_request("some question"), None, client
    except BaseException as e:  # the whole point: nothing should escape
        return None, e, client
    finally:
        llm_client.get_client = real


def check_controlled(label, script, *, expect_in_answer=None, expect_calls=None):
    answer, raised, client = run(script)
    ok = raised is None and isinstance(answer, str) and answer.strip() != ""
    detail = f"raised {raised!r}" if raised else f"answer: {answer!r}"
    check(f"{label}: returns a non-empty string, never raises", ok, detail)
    if ok and expect_in_answer:
        check(f"{label}: message mentions {expect_in_answer!r}", expect_in_answer in answer.lower(), f"answer: {answer!r}")
    if expect_calls is not None:
        check(f"{label}: exactly {expect_calls} model call(s)", client.calls == expect_calls, f"calls: {client.calls}")
    return answer


def test_tool_call_shape():
    safe_print("\nTool-call shape")
    check_controlled("T041 malformed tool JSON", [response("tool_calls", tool_calls=[tool_call("set_reminder", "{not json")])],
                     expect_in_answer="unreadable")
    check_controlled("T042 unknown tool", [response("tool_calls", tool_calls=[tool_call("does_not_exist", "{}")])],
                     expect_in_answer="unknown skill")
    check_controlled("T043 missing required argument", [response("tool_calls", tool_calls=[tool_call("set_reminder", '{"text": "x"}')])],
                     expect_in_answer="missing required")
    check_controlled("T088 finish_reason=tool_calls but empty tool_calls list", [response("tool_calls", tool_calls=[])])
    check_controlled("T088b finish_reason=tool_calls but tool_calls is None", [response("tool_calls", tool_calls=None)])
    check_controlled("T089 arguments is a JSON list, not an object", [response("tool_calls", tool_calls=[tool_call("set_reminder", "[]")])],
                     expect_in_answer="not a json object")
    check_controlled("wrong argument type (time is a number)",
                     [response("tool_calls", tool_calls=[tool_call("set_reminder", '{"text": "x", "time": 9}')])],
                     expect_in_answer="must be a string")
    check_controlled("empty arguments string", [response("tool_calls", tool_calls=[tool_call("set_reminder", "")])])


def test_high_stakes_validation():
    safe_print("\nHigh-stakes validation (T090)")
    agent.clear_pending()
    check_controlled("make_purchase with no arguments", [response("tool_calls", tool_calls=[tool_call("make_purchase", "{}")])],
                     expect_in_answer="missing required")
    check("rejected purchase did NOT become a pending confirmation", agent.get_pending() is None,
          f"pending: {agent.get_pending()!r}")
    answer, raised, _ = run([response("tool_calls", tool_calls=[tool_call("make_purchase", '{"item": "lamp", "price": "$5"}')])])
    check("a valid purchase still becomes a pending confirmation (unchanged behavior)",
          raised is None and agent.get_pending() is not None and agent.get_pending()["args"] == {"item": "lamp", "price": "$5"},
          f"answer={answer!r} pending={agent.get_pending()!r}")
    agent.clear_pending()


def test_response_shape():
    safe_print("\nResponse shape")
    check_controlled("T044 empty choices", [SimpleNamespace(choices=[])])
    check_controlled("choices is None", [SimpleNamespace(choices=None)])
    check_controlled("T045 stop with content=None", [response("stop", content=None)])
    check_controlled("stop with whitespace-only content", [response("stop", content="   ")])


def test_service_failures():
    safe_print("\nService / skill failures")
    check_controlled("T046 model call raises", [RuntimeError("Injected Nebius unavailable")], expect_in_answer="language model request failed", expect_calls=1)
    check_controlled("T091 refusal, then the retry call raises",
                     [response("stop", content="I can't help with that question using the available tools."), RuntimeError("Injected retry outage")],
                     expect_in_answer="language model request failed", expect_calls=2)

    real = retrieval.retrieve
    retrieval.retrieve = lambda question, speaker=None: (_ for _ in ()).throw(RuntimeError("Injected tool failure"))
    try:
        check_controlled("T048 skill raises",
                         [response("tool_calls", tool_calls=[tool_call("query_memory", '{"question": "x"}')])],
                         expect_in_answer="skill failed", expect_calls=1)
    finally:
        retrieval.retrieve = real

    check_controlled("follow-up call raises",
                     [response("tool_calls", tool_calls=[tool_call("set_reminder", '{"text": "x", "time": "9am"}')]), RuntimeError("down")],
                     expect_in_answer="language model request failed", expect_calls=2)
    check_controlled("follow-up returns empty choices",
                     [response("tool_calls", tool_calls=[tool_call("set_reminder", '{"text": "x", "time": "9am"}')]), SimpleNamespace(choices=[])])
    check_controlled("follow-up returns content=None",
                     [response("tool_calls", tool_calls=[tool_call("set_reminder", '{"text": "x", "time": "9am"}')]), response("stop", content=None)])


def test_unchanged_behavior():
    safe_print("\nUnchanged behavior")
    answer, raised, client = run([response("stop", content="Hello there!")])
    check("plain answer returned as-is, one call", raised is None and answer == "Hello there!" and client.calls == 1, f"{answer!r} {raised!r}")

    answer, raised, client = run([
        response("tool_calls", tool_calls=[tool_call("set_reminder", '{"text": "call mom", "time": "6pm", "bogus": 1}')]),
        response("stop", content="Reminder set."),
    ])
    check("normal tool call + follow-up works; undeclared extra argument is dropped, not an error",
          raised is None and answer == "Reminder set." and client.calls == 2, f"{answer!r} {raised!r}")


if __name__ == "__main__":
    test_tool_call_shape()
    test_high_stakes_validation()
    test_response_shape()
    test_service_failures()
    test_unchanged_behavior()
    safe_print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
