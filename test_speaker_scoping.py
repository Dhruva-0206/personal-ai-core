"""
Regression test: the speaker passed to agent.handle_request() must actually
reach retrieval through the query_memory skill. Before the fix
handle_request(speaker=...) accepted the parameter but query_memory called
retrieval.retrieve(question) with no speaker, so every agent question was
answered from config.DEFAULT_SPEAKER's graph regardless.

Part 1 (offline, fake model client, no credentials): the speaker reaches
retrieve(); a model-supplied "speaker" argument cannot override it.
Part 2 (LIVE: Nebius + Neo4j Aura): two throwaway speakers with different
cities; query_memory and the agent answer from the right one. Uses speakers
prefixed "regress_speaker_" (deleted afterwards) and never changes or
writes config.DEFAULT_SPEAKER or the "default" speaker.

    python test_speaker_scoping.py

Part 2 includes LLM answer generation; re-run once before treating a single
agent-level failure as a regression.
"""
import sys
import types
import uuid

import agent
import config
import graph_engine
import llm_client
import pipeline
import retrieval
import skills

TAG = uuid.uuid4().hex[:6]
SPEAKER_A = f"regress_speaker_a_{TAG}"
SPEAKER_B = f"regress_speaker_b_{TAG}"

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
        safe_print(f"PASS  {label}")
    else:
        failed += 1
        safe_print(f"FAIL  {label}")
        if detail:
            safe_print(f"      {detail}")


# ---- Part 1: offline -------------------------------------------------------

def _response(content=None, finish="stop", tool_calls=None):
    message = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message, finish_reason=finish)])


def _tool_call(name, arguments):
    return types.SimpleNamespace(id="call_1", function=types.SimpleNamespace(name=name, arguments=arguments))


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        return self._responses.pop(0)


def _run_agent_with_fake_model(arguments_json, speaker, *, use_kwarg=True):
    """Runs handle_request against a fake model that calls query_memory; returns the speaker retrieve() saw."""
    seen = []
    real_retrieve, real_get_client = retrieval.retrieve, llm_client.get_client
    retrieval.retrieve = lambda question, speaker=None: (seen.append(speaker) or [])
    llm_client.get_client = lambda: _FakeClient([
        _response(None, "tool_calls", [_tool_call("query_memory", arguments_json)]),
        _response("done"),
    ])
    try:
        if use_kwarg:
            agent.handle_request("where do I live", speaker=speaker)
        else:
            agent.handle_request("where do I live")
    finally:
        retrieval.retrieve, llm_client.get_client = real_retrieve, real_get_client
    return seen


def test_offline():
    safe_print("== Part 1: speaker reaches retrieve() (offline, fake model) ==")
    seen = _run_agent_with_fake_model('{"question": "where do I live"}', "alice")
    check("handle_request(speaker='alice') -> retrieve() gets 'alice'", seen == ["alice"], f"retrieve saw: {seen}")

    seen = _run_agent_with_fake_model('{"question": "where do I live", "speaker": "mallory"}', "alice")
    check("a model-supplied 'speaker' argument cannot override the caller's speaker",
          seen == ["alice"], f"retrieve saw: {seen}")

    seen = _run_agent_with_fake_model('{"question": "where do I live"}', None, use_kwarg=False)
    check("no speaker given -> retrieve() gets None (falls back to the default speaker)",
          seen == [None], f"retrieve saw: {seen}")

    seen = []
    real_retrieve = retrieval.retrieve
    retrieval.retrieve = lambda question, speaker=None: (seen.append(speaker) or [])
    try:
        skills.run_skill("query_memory", "bob", question="x")
    finally:
        retrieval.retrieve = real_retrieve
    check("skills.run_skill('query_memory', 'bob', ...) -> retrieve() gets 'bob'", seen == ["bob"], f"retrieve saw: {seen}")


# ---- Part 2: live ----------------------------------------------------------

def _summaries(result):
    return " | ".join(str(r.get("summary")) for r in result.get("results", [])).lower()


def test_live():
    safe_print("\n== Part 2: live, two speakers with different cities ==")
    pipeline.ingest_episode("I live in Adelaide.", speaker=SPEAKER_A)
    pipeline.ingest_episode("I live in Brisbane.", speaker=SPEAKER_B)

    with graph_engine.get_driver().session() as session:
        for speaker, city in ((SPEAKER_A, "adelaide"), (SPEAKER_B, "brisbane")):
            row = session.run(
                "MATCH (:Entity {speaker: $s, name: 'User'})-[:OF_ENTITY]-(st:State {attribute: 'city', active: true}) "
                "RETURN st.value AS v", s=speaker).single()
            if row is None or city not in str(row["v"]).lower():
                raise RuntimeError(f"setup: {speaker} has no active User city {city!r} (got {row and row['v']!r})")

    a = skills.run_skill("query_memory", SPEAKER_A, question="where do I live")
    b = skills.run_skill("query_memory", SPEAKER_B, question="where do I live")
    check("query_memory for speaker A returns Adelaide and nothing of B's",
          "adelaide" in _summaries(a) and "brisbane" not in _summaries(a), f"summaries: {_summaries(a)!r}")
    check("query_memory for speaker B returns Brisbane and nothing of A's",
          "brisbane" in _summaries(b) and "adelaide" not in _summaries(b), f"summaries: {_summaries(b)!r}")

    default_before = config.DEFAULT_SPEAKER
    for speaker, want, other in ((SPEAKER_A, "adelaide", "brisbane"), (SPEAKER_B, "brisbane", "adelaide")):
        try:
            answer = agent.handle_request("where do I live", speaker=speaker) or ""
        except Exception as e:
            answer = f"<raised {e!r}>"
        check(f"agent.handle_request(speaker=...) answers {want!r} (not {other!r}) without touching DEFAULT_SPEAKER",
              want in answer.lower() and other not in answer.lower(), f"answer: {answer!r}")
    check("config.DEFAULT_SPEAKER was not modified", config.DEFAULT_SPEAKER == default_before)


def cleanup():
    with graph_engine.get_driver().session() as session:
        for speaker in (SPEAKER_A, SPEAKER_B):
            graph_engine.reset_speaker(session, speaker)


if __name__ == "__main__":
    test_offline()
    try:
        test_live()
    except Exception as e:
        failed += 1
        safe_print(f"FAIL  live part raised {e!r}")
    finally:
        try:
            cleanup()
        except Exception as e:
            safe_print(f"cleanup failed (delete {SPEAKER_A}, {SPEAKER_B} manually): {e!r}")
    safe_print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
