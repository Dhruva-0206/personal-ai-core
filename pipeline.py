"""
Orchestrates turning one raw piece of input into graph writes:
  raw text -> extract() -> resolve each entity -> write episode, states,
  actions, relations.

This is source-agnostic on purpose: it doesn't care whether raw_text came
from manual typing, a calendar event description, a transcribed sentence of
audio, or a caption generated from a camera frame. Every ingestion adapter
we build later should end here, calling ingest_episode() with normalized text.
"""
import logging

import config
import extraction
import graph_engine
import llm_client

logger = logging.getLogger(__name__)


def ingest_episode(raw_text: str, speaker: str = None) -> dict:
    """
    Run the full pipeline for one raw episode. Returns a small summary dict
    of what got written, useful for CLI feedback and tests.
    """
    speaker = speaker or config.DEFAULT_SPEAKER
    parsed = extraction.extract(raw_text)

    episode_embedding = llm_client.embed(raw_text)

    driver = graph_engine.get_driver()
    with driver.session() as session:
        episode_id = graph_engine.create_episode(
            session, speaker=speaker, raw_text=raw_text,
            summary=parsed["summary"], importance=parsed["importance"],
            embedding=episode_embedding, event_time=parsed["event_time"],
        )

        resolved_names = {}  # extracted name -> canonical name
        for entity in parsed["entities"]:
            name = entity["name"]
            entity_embedding = llm_client.embed(name)
            canonical = graph_engine.resolve_entity(session, speaker, name, entity_embedding)
            resolved_names[name] = canonical
            graph_engine.link_episode_entity(session, episode_id, canonical, speaker)

        def canon(name: str) -> str:
            # Resolve a name that might not have gone through the entities
            # list explicitly (e.g. mentioned only inside a state/action).
            if not name:
                return name
            if name in resolved_names:
                return resolved_names[name]
            entity_embedding = llm_client.embed(name)
            canonical = graph_engine.resolve_entity(session, speaker, name, entity_embedding)
            resolved_names[name] = canonical
            return canonical

        for state in parsed["states"]:
            entity_name = canon(state["entity"])
            attribute_embedding = llm_client.embed(state["attribute"])
            attribute = graph_engine.resolve_attribute(
                session, speaker, entity_name, state["attribute"], attribute_embedding
            )
            graph_engine.create_state(
                session, speaker, entity_name, attribute, state["value"],
                episode_id, attribute_embedding
            )

        for action in parsed["actions"]:
            actor_name = canon(action["actor"])
            graph_engine.create_action(
                session, speaker, episode_id, actor_name, action["verb"], action.get("object", "")
            )

        for relation in parsed["relations"]:
            subject_name = canon(relation["subject"])
            object_name = canon(relation["object"])
            graph_engine.create_relation(
                session, speaker, episode_id, subject_name, relation["type"], object_name
            )

    return {
        "episode_id": episode_id,
        "summary": parsed["summary"],
        "importance": parsed["importance"],
        "entities": list(resolved_names.values()),
        "states_written": len(parsed["states"]),
        "actions_written": len(parsed["actions"]),
        "relations_written": len(parsed["relations"]),
    }
