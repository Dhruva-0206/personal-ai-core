"""
Live regression suite: consolidates the scenarios validated by hand against
live infrastructure into one repeatable script.

REQUIRES LIVE CREDENTIALS: a populated .env with Nebius Token Factory and
Neo4j Aura credentials, and a reachable (not auto-paused) Aura instance. It
makes real LLM and database calls (roughly 5 ingests plus 4 agent
questions), takes a few minutes, and RESETS the "default" speaker's data
(graph_engine.reset_speaker) before running — do not run it against a graph
you want to keep.

Run manually or periodically, not as part of the fast offline suite:
    python test_regression_live.py        # live
    python test_offline.py                # offline, no credentials

Answers come from an LLM, so a single failure may be model flakiness rather
than a regression — re-run before treating one failed check as a bug.
"""
import sys

import agent
import graph_engine
import pipeline
import skills

SPEAKER = "default"

CHAIN = [
    "I live in Sydney",
    "I moved to Melbourne",
    "I work as a software engineer",
    "I changed careers and now work as a data scientist",
    "I changed jobs again and now work as a product manager",
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


def check_answer(question, expected_substring):
    """Asks the agent, then asserts the answer contains expected_substring (case-insensitive)."""
    try:
        answer = agent.handle_request(question) or ""
    except Exception as e:  # a crash is a failure of this check, not of the whole run
        check(f"agent: {question!r} contains {expected_substring!r}", False, f"raised {e!r}")
        return
    check(
        f"agent: {question!r} contains {expected_substring!r}",
        expected_substring.lower() in answer.lower(),
        f"answer was: {answer!r}",
    )


def setup():
    safe_print("== Setup: reset speaker, log the standard validation chain ==")
    driver = graph_engine.get_driver()
    with driver.session() as session:
        graph_engine.reset_speaker(session, SPEAKER)
    for text in CHAIN:
        pipeline.ingest_episode(text)
        safe_print(f"   logged: {text}")


def test_agent_answers():
    safe_print("\n== Agent answers (current and historical state) ==")
    check_answer("where do I live now", "Melbourne")
    check_answer("where did I live before Melbourne", "Sydney")
    check_answer("what is my current job", "product manager")
    check_answer("what was my previous job", "data scientist")


def test_confirmation_gate():
    safe_print("\n== High-stakes confirmation gate ==")
    args = {"item": "test item", "price": "$1"}
    result = skills.run_skill("make_purchase", **args)
    check("run_skill(make_purchase) returns needs_confirmation (never auto-executes)",
          result.get("status") == "needs_confirmation", f"result was: {result!r}")
    confirmed = skills.confirm_skill("make_purchase", **args)
    check("confirm_skill(make_purchase) executes and returns confirmed",
          confirmed.get("status") == "confirmed", f"result was: {confirmed!r}")


if __name__ == "__main__":
    try:
        setup()
    except Exception as e:
        safe_print(f"\nSETUP FAILED (credentials/Aura reachable?): {e!r}")
        raise SystemExit(2)
    test_agent_answers()
    test_confirmation_gate()
    safe_print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
