"""
Live regression test: a historical "before X" question must return the state
that immediately preceded X, not just the most recently superseded state.
Before the fix the historical lookup ignored X and ran
ORDER BY superseded_at DESC LIMIT 1, so on a 4-state chain
Sydney -> Melbourne -> Pune -> Mumbai it answered "Pune" for "before Pune"
and for "before Melbourne" (only "before Mumbai" was right, by coincidence).

REQUIRES LIVE CREDENTIALS (Nebius + a reachable Neo4j Aura instance), makes
real LLM/database calls, takes a few minutes. Uses one throwaway speaker
(prefix "regress_anchor_") and deletes it when done; never touches "default".

    python test_historical_anchor_live.py

The direct_state_lookup checks are deterministic given the graph and the
classification call; the agent-level checks add LLM answer-generation
variance, so re-run once before treating a single agent-level failure as a
regression.
"""
import sys
import uuid

import agent
import config
import graph_engine
import pipeline
import retrieval

SPEAKER = f"regress_anchor_{uuid.uuid4().hex[:6]}"
CHAIN = ["Sydney", "Melbourne", "Pune", "Mumbai"]
EPISODES = [
    "I live in Sydney.",
    "I moved to Melbourne.",
    "I have moved to Pune.",
    "I am in Mumbai now.",
]

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


def direct(question):
    with graph_engine.get_driver().session() as session:
        return retrieval.direct_state_lookup(session, SPEAKER, question)


def check_direct(question, expected_value):
    """expected_value None means: must return no direct result at all."""
    r = direct(question)
    if expected_value is None:
        check(f"direct {question!r} -> None (no guess)", r is None, f"result: {r!r}")
    else:
        check(
            f"direct {question!r} -> {expected_value}",
            r is not None and r["entity"] == "User" and str(r["value"]).strip().lower() == expected_value.lower(),
            f"result: {r!r}",
        )


def check_agent(question, expected_substring):
    config.DEFAULT_SPEAKER = SPEAKER  # agent -> query_memory -> retrieve() resolves the speaker at call time
    try:
        answer = agent.handle_request(question) or ""
    except Exception as e:
        answer = f"<raised {e!r}>"
    check(f"agent {question!r} contains {expected_substring!r}",
          expected_substring.lower() in answer.lower(), f"answer: {answer!r}")


def setup():
    safe_print("== Setup: 4-state city chain on a throwaway speaker ==")
    for text in EPISODES:
        pipeline.ingest_episode(text, speaker=SPEAKER)
        safe_print(f"   logged: {text}")
    # Precondition: extraction must have produced exactly the chain this test
    # needs (extraction is non-deterministic, see CLAUDE.md) — fail setup
    # rather than let the checks below pass or fail vacuously.
    with graph_engine.get_driver().session() as session:
        rows = list(session.run(
            "MATCH (e:Entity {speaker: $s, name: 'User'})-[:OF_ENTITY]-(st:State {attribute: 'city'}) "
            "RETURN st.value AS v, st.active AS active ORDER BY st.created_at",
            s=SPEAKER,
        ))
        edges = session.run(
            "MATCH (:Entity {speaker: $s, name: 'User'})-[:OF_ENTITY]-(:State {attribute: 'city'})"
            "-[r:SUPERSEDES]->(:State) RETURN count(r) AS n",
            s=SPEAKER,
        ).single()
    values = [str(r["v"]).strip() for r in rows]
    if [v.lower() for v in values] != [c.lower() for c in CHAIN]:
        raise RuntimeError(f"User.city chain is {values}, expected {CHAIN}")
    if [r["active"] for r in rows] != [False, False, False, True]:
        raise RuntimeError(f"unexpected active flags: {[r['active'] for r in rows]}")
    if edges is None or edges["n"] < 3:
        raise RuntimeError("SUPERSEDES edges missing")


def test_anchored_before():
    safe_print("\n== 'before X' returns X's immediate predecessor ==")
    check_direct("Where did I live before Melbourne?", "Sydney")
    check_direct("Where did I live before Pune?", "Melbourne")
    check_direct("Where did I live before Mumbai?", "Pune")


def test_no_anchor_and_unknown_anchor():
    safe_print("\n== No anchor / unmatched anchor ==")
    check_direct("What was my previous city?", "Pune")          # no anchor: most recently superseded
    check_direct("Where did I live before Perth?", None)         # anchor not in the chain: no guess
    check_direct("Where did I live before Sydney?", None)        # anchor is the first state: nothing precedes it
    check_direct("Where do I live now?", "Mumbai")               # current-state path unaffected


def test_agent():
    safe_print("\n== Agent answers (LLM answer generation on top) ==")
    check_agent("Where did I live before Melbourne?", "Sydney")
    check_agent("Where did I live before Pune?", "Melbourne")


def cleanup():
    with graph_engine.get_driver().session() as session:
        graph_engine.reset_speaker(session, SPEAKER)


if __name__ == "__main__":
    original_speaker = config.DEFAULT_SPEAKER
    try:
        try:
            setup()
        except Exception as e:
            safe_print(f"\nSETUP FAILED: {e!r}")
            raise SystemExit(2)
        test_anchored_before()
        test_no_anchor_and_unknown_anchor()
        test_agent()
    finally:
        config.DEFAULT_SPEAKER = original_speaker
        try:
            cleanup()
        except Exception as e:
            safe_print(f"cleanup failed (delete speaker {SPEAKER} manually): {e!r}")
    safe_print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
