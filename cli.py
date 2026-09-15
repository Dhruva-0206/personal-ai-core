"""
Manual test CLI for Phase 1. This is the only ingestion "adapter" that
exists yet — you type a sentence, it goes through the full pipeline.

Usage:
    python cli.py setup                     # create Neo4j constraints/indexes, run once
    python cli.py log "I moved to Austin"    # ingest one line
    python cli.py chat                       # interactive loop, one line at a time
    python cli.py history "Alex"             # show everything currently/previously true about an entity
    python cli.py reset <speaker> --confirm  # delete all graph data for one speaker
    python cli.py search "where do I live"   # rank episodes by relevance (no LLM answer yet)
    python cli.py skill <name> --k v ...     # run a registered skill (high_stakes ones ask to confirm)
"""
import sys
import logging

import graph_engine
import pipeline
import retrieval
import skills

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def cmd_setup():
    graph_engine.ensure_schema()
    print("Schema ready (constraints + indexes created if missing).")


def cmd_log(text: str):
    result = pipeline.ingest_episode(text)
    print(f"\nStored episode {result['episode_id']}")
    print(f"  summary:    {result['summary']}")
    print(f"  importance: {result['importance']}")
    print(f"  entities:   {result['entities']}")
    print(f"  states/actions/relations written: "
          f"{result['states_written']}/{result['actions_written']}/{result['relations_written']}")


def cmd_chat():
    print("Type a line and press enter to store it. Ctrl+C to quit.\n")
    try:
        while True:
            text = input("> ").strip()
            if text:
                cmd_log(text)
    except KeyboardInterrupt:
        print("\nbye")


def cmd_reset(speaker: str, args: list[str]):
    if "--confirm" not in args:
        print(f"WARNING: this will permanently delete ALL graph data for speaker "
              f"'{speaker}' (every Entity, Episode, State, Action, and Relation "
              f"scoped to it). Re-run with --confirm to proceed:\n"
              f"    python cli.py reset {speaker} --confirm")
        sys.exit(1)

    driver = graph_engine.get_driver()
    with driver.session() as session:
        result = graph_engine.reset_speaker(session, speaker)
    print(f"Reset complete for speaker '{speaker}': "
          f"{result['nodes_deleted']} nodes, "
          f"{result['relationships_deleted']} relationships deleted.")


def _fmt(value):
    return "n/a" if value is None else round(value, 3)


def cmd_search(question: str):
    results = retrieval.retrieve(question)
    if not results:
        print(f"No results for '{question}'.")
        return
    print(f"\nSearch results for '{question}':")
    for rank, r in enumerate(results, start=1):
        print(f"  #{rank}  combined_score={round(r['combined_score'], 3)}  "
              f"similarity={_fmt(r['similarity'])}  "
              f"importance={r['importance']}  "
              f"recency={_fmt(r['recency'])}  "
              f"state_status={r['state_status']}  "
              f"lanes={r['lanes']}  "
              f"summary: {r['summary']}")


def _parse_skill_args(argv: list[str]) -> dict:
    kwargs = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if not token.startswith("--"):
            raise ValueError(f"Expected a --flag, got '{token}'")
        key = token[2:]
        if i + 1 >= len(argv):
            raise ValueError(f"Missing value for --{key}")
        kwargs[key] = argv[i + 1]
        i += 2
    return kwargs


def cmd_skill(name: str, argv: list[str]):
    kwargs = _parse_skill_args(argv)

    skill = skills.REGISTRY.get(name)
    if skill is None:
        print(f"Unknown skill: '{name}'. Registered skills: {sorted(skills.REGISTRY)}")
        sys.exit(1)

    if skill.tier == "high_stakes":
        pending = skills.run_skill(name, **kwargs)
        print(pending["description"])
        answer = input("Type 'yes' to confirm, anything else to cancel: ")
        if answer == "yes":
            result = skills.confirm_skill(name, **kwargs)
            print(result)
        else:
            print("Cancelled.")
    else:
        result = skills.run_skill(name, **kwargs)
        print(result)


def cmd_history(entity_name: str):
    driver = graph_engine.get_driver()
    with driver.session() as session:
        rows = graph_engine.entity_history(session, "default", entity_name)
    if not rows:
        print(f"No history found for '{entity_name}'.")
        return
    print(f"\nHistory for '{entity_name}':")
    for row in rows:
        status = "ACTIVE" if row["active"] else f"superseded at {row['superseded_at']}"
        print(f"  {row['attribute']} = {row['value']}   [{status}]   (created {row['created_at']})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    if command == "setup":
        cmd_setup()
    elif command == "log" and len(sys.argv) > 2:
        cmd_log(" ".join(sys.argv[2:]))
    elif command == "chat":
        cmd_chat()
    elif command == "history" and len(sys.argv) > 2:
        cmd_history(" ".join(sys.argv[2:]))
    elif command == "reset" and len(sys.argv) > 2:
        cmd_reset(sys.argv[2], sys.argv[3:])
    elif command == "search" and len(sys.argv) > 2:
        cmd_search(" ".join(sys.argv[2:]))
    elif command == "skill" and len(sys.argv) > 2:
        cmd_skill(sys.argv[2], sys.argv[3:])
    else:
        print(__doc__)
        sys.exit(1)
