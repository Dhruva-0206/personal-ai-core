"""
Live regression test: direct_state_lookup() must be scoped to the entity the
question is about. Before the fix its state queries filtered only on
attribute, so with Rahul holding the only active city, "where do I live"
returned Rahul's Canberra as if it were the user's.

REQUIRES LIVE CREDENTIALS (Nebius + a reachable Neo4j Aura instance), makes
real LLM/database calls, takes a few minutes. Unlike test_regression_live.py
it does NOT touch the "default" speaker: it uses two throwaway speakers
(prefix "regress_subject_") and deletes them when done.

    python test_subject_scoping_live.py

Structural checks (direct_state_lookup return values) are deterministic given
the graph; agent-answer checks depend on an LLM, so re-run once before
treating a single agent-level failure as a regression.
"""
import sys
import uuid

import agent
import config
import graph_engine
import pipeline
import retrieval

TAG = uuid.uuid4().hex[:6]
WITH_CITY = f"regress_subject_with_city_{TAG}"
NO_CITY = f"regress_subject_no_city_{TAG}"

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


def active_state(speaker, entity, attribute):
    with graph_engine.get_driver().session() as session:
        row = session.run(
            "MATCH (e:Entity {speaker: $s, name: $e})-[:OF_ENTITY]-(st:State {attribute: $a}) "
            "WHERE st.active = true RETURN st.value AS v LIMIT 1",
            s=speaker, e=entity, a=attribute,
        ).single()
    return row["v"] if row else None


def direct(speaker, question):
    with graph_engine.get_driver().session() as session:
        return retrieval.direct_state_lookup(session, speaker, question)


def agent_answer(speaker, question):
    config.DEFAULT_SPEAKER = speaker  # agent -> query_memory -> retrieve() resolves the speaker at call time
    try:
        return agent.handle_request(question) or ""
    except Exception as e:
        return f"<raised {e!r}>"


def claims_canberra_as_home(answer):
    """
    True if the answer asserts Canberra as the USER's own home. Merely
    mentioning Rahul's Canberra (a correct answer often does, to explain what
    it ignored) is not a claim. Phrase-based, so a novel phrasing could slip
    through: the deterministic evidence is the direct_state_lookup checks.
    """
    lowered = answer.lower().replace("*", "")
    return any(p in lowered for p in (
        "you live in canberra", "you currently live in canberra", "you reside in canberra",
        "you are in canberra", "you're in canberra", "your home is canberra", "your city is canberra",
    ))


def setup():
    safe_print("== Setup ==")
    for text in ("I live in Adelaide.", "My friend Rahul lives in Canberra."):
        pipeline.ingest_episode(text, speaker=WITH_CITY)
        safe_print(f"   [{WITH_CITY}] logged: {text}")
    pipeline.ingest_episode("My friend Rahul lives in Canberra.", speaker=NO_CITY)
    safe_print(f"   [{NO_CITY}] logged: My friend Rahul lives in Canberra.")

    # Preconditions: extraction must have produced the graph this test needs
    # (a known extraction non-determinism, see CLAUDE.md) — otherwise the
    # checks below would be vacuous, so fail setup rather than pass them.
    problems = []
    if active_state(WITH_CITY, "User", "city") is None:
        problems.append("User has no active city in the with-city graph")
    if active_state(WITH_CITY, "Rahul", "city") is None:
        problems.append("Rahul has no active city in the with-city graph")
    if active_state(NO_CITY, "Rahul", "city") is None:
        problems.append("Rahul has no active city in the no-city graph")
    if active_state(NO_CITY, "User", "city") is not None:
        problems.append("User unexpectedly has a city in the no-city graph")
    if problems:
        raise RuntimeError("; ".join(problems))


def test_user_and_rahul_both_have_cities():
    safe_print("\n== User and Rahul both have a city ==")
    r = direct(WITH_CITY, "where do I live")
    check("direct 'where do I live' -> User/Adelaide (not Rahul/Canberra)",
          r is not None and r["entity"] == "User" and "adelaide" in r["value"].lower(), f"result: {r!r}")
    r = direct(WITH_CITY, "where does Rahul live")
    check("direct 'where does Rahul live' -> Rahul/Canberra",
          r is not None and r["entity"] == "Rahul" and "canberra" in r["value"].lower(), f"result: {r!r}")

    answer = agent_answer(WITH_CITY, "where do I live")
    check("agent 'where do I live' says Adelaide and does not claim Canberra as the user's home",
          "adelaide" in answer.lower() and not claims_canberra_as_home(answer), f"answer: {answer!r}")
    answer = agent_answer(WITH_CITY, "where does Rahul live")
    check("agent 'where does Rahul live' says Canberra",
          "canberra" in answer.lower(), f"answer: {answer!r}")


def test_user_has_no_city():
    safe_print("\n== Only Rahul has a city; User has none ==")
    r = direct(NO_CITY, "where do I live")
    check("direct 'where do I live' returns no direct result (never Rahul's state)",
          r is None, f"result: {r!r}")
    r = direct(NO_CITY, "where does Rahul live")
    check("direct 'where does Rahul live' -> Rahul/Canberra",
          r is not None and r["entity"] == "Rahul" and "canberra" in r["value"].lower(), f"result: {r!r}")

    answer = agent_answer(NO_CITY, "where do I live")
    check("agent 'where do I live' does not assert Canberra as the user's home",
          not claims_canberra_as_home(answer), f"answer: {answer!r}")


def cleanup():
    with graph_engine.get_driver().session() as session:
        for speaker in (WITH_CITY, NO_CITY):
            graph_engine.reset_speaker(session, speaker)


if __name__ == "__main__":
    original_speaker = config.DEFAULT_SPEAKER
    try:
        try:
            setup()
        except Exception as e:
            safe_print(f"\nSETUP FAILED: {e!r}")
            raise SystemExit(2)
        test_user_and_rahul_both_have_cities()
        test_user_has_no_city()
    finally:
        config.DEFAULT_SPEAKER = original_speaker
        try:
            cleanup()
        except Exception as e:
            safe_print(f"cleanup failed (delete speakers {WITH_CITY}, {NO_CITY} manually): {e!r}")
    safe_print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
