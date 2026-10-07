"""
Regression test: entity resolution must not merge distinct, similar-looking
names ("Ann" / "Anna"). Before the fix, Layer 2 used raw substring
containment ("anna" contains "ann") and Layer 3 merged on name-embedding
cosine alone (Ann/Anna = 0.954 >= 0.93), so logging Anna's city superseded
Ann's.

Part 1 (offline, no credentials): the whole-word matching helper.
Part 2 (LIVE: Nebius + Neo4j Aura): Ann in Hobart, then Anna in Darwin, then
Ann again — Ann's record must be untouched by Anna, and a later mention of
"Ann" must still resolve to Ann. Uses one throwaway speaker
(prefix "regress_entity_"), deleted afterwards; never touches "default".

    python test_entity_resolution_live.py

Part 2 involves LLM extraction/judgment, so re-run once before treating a
single live failure as a regression.
"""
import sys
import uuid

import graph_engine
import pipeline

SPEAKER = f"regress_entity_{uuid.uuid4().hex[:6]}"

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


def test_whole_word_helper():
    safe_print("== Part 1: whole-word matching helper (offline) ==")
    f = graph_engine._names_share_whole_words
    check("'Ann' vs 'Anna' are NOT a whole-word match", not f("Ann", "Anna"))
    check("'Anne' vs 'Ann' are NOT a whole-word match", not f("Anne", "Ann"))
    check("'Ann' vs 'my friend Ann' ARE a whole-word match", f("Ann", "my friend Ann"))
    check("'Rahul' vs 'Rahul Sharma' ARE a whole-word match", f("Rahul", "Rahul Sharma"))
    check("case-insensitive: 'sydney' vs 'Sydney'", f("sydney", "Sydney"))
    check("empty names never match", not f("", "Ann") and not f("Ann", "  "))


def city(entity):
    """Returns (active_value, [inactive values]) for the entity's city states."""
    with graph_engine.get_driver().session() as session:
        rows = list(session.run(
            "MATCH (e:Entity {speaker: $s, name: $e})-[:OF_ENTITY]-(st:State {attribute: 'city'}) "
            "RETURN st.value AS v, st.active AS active ORDER BY st.created_at",
            s=SPEAKER, e=entity,
        ))
    active = [r["v"] for r in rows if r["active"]]
    return (active[0] if active else None), [r["v"] for r in rows if not r["active"]]


def entity_names():
    with graph_engine.get_driver().session() as session:
        return [r["n"] for r in session.run(
            "MATCH (e:Entity {speaker: $s}) RETURN e.name AS n", s=SPEAKER)]


def test_live():
    safe_print("\n== Part 2: live Ann / Anna ==")
    for text in ("My friend Ann lives in Hobart.", "My friend Anna lives in Darwin."):
        pipeline.ingest_episode(text, speaker=SPEAKER)
        safe_print(f"   logged: {text}")

    names = entity_names()
    check("both 'Ann' and 'Anna' exist as separate entities",
          "Ann" in names and "Anna" in names, f"entities: {names}")
    ann_active, ann_old = city("Ann")
    anna_active, _ = city("Anna")
    check("Ann's city is still Hobart and active (Anna did not supersede it)",
          ann_active is not None and "hobart" in str(ann_active).lower() and not ann_old,
          f"Ann: active={ann_active!r} inactive={ann_old!r}")
    check("Anna's city is Darwin and active",
          anna_active is not None and "darwin" in str(anna_active).lower(), f"Anna: active={anna_active!r}")

    pipeline.ingest_episode("My friend Ann moved to Perth.", speaker=SPEAKER)
    safe_print("   logged: My friend Ann moved to Perth.")
    names = entity_names()
    check("re-mentioning 'Ann' creates no duplicate Ann entity",
          sum(1 for n in names if n.lower() == "ann") == 1, f"entities: {names}")
    ann_active, ann_old = city("Ann")
    anna_active, _ = city("Anna")
    check("re-mentioned Ann resolves to Ann: Perth active, Hobart superseded",
          ann_active is not None and "perth" in str(ann_active).lower()
          and any("hobart" in str(v).lower() for v in ann_old),
          f"Ann: active={ann_active!r} inactive={ann_old!r}")
    check("Anna's Darwin is untouched by Ann's move",
          anna_active is not None and "darwin" in str(anna_active).lower(), f"Anna: active={anna_active!r}")


def cleanup():
    with graph_engine.get_driver().session() as session:
        graph_engine.reset_speaker(session, SPEAKER)


if __name__ == "__main__":
    test_whole_word_helper()
    try:
        test_live()
    except Exception as e:
        failed += 1
        safe_print(f"FAIL  live part raised {e!r}")
    finally:
        try:
            cleanup()
        except Exception as e:
            safe_print(f"cleanup failed (delete speaker {SPEAKER} manually): {e!r}")
    safe_print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
