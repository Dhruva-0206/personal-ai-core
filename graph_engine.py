"""
The temporal knowledge graph itself. Every write and read is scoped to a
`speaker` (tenant) so different people's memories never mix.

Entity resolution is three layers, in order, first match wins:
  1. exact match (case-insensitive)
  2. whole-word containment (every word of the shorter name appears as a
     whole word in the other, e.g. "Rahul" / "my friend Rahul" — never a
     raw substring, so "Ann" and "Anna" stay distinct)
  3. embedding cosine similarity above config.ENTITY_SIMILARITY_THRESHOLD
     picks merge CANDIDATES only; an LLM then confirms whether one is
     really the same entity (similar-looking names are often different
     people, and a wrong merge destroys data while a wrong split doesn't)

Supersession is a write-time operation on (entity, attribute): any
currently-active State for that pair is flagged inactive, and the new State
links back to it via a SUPERSEDES edge. Nothing is ever deleted, so history
stays queryable.

Note on scale: entity-resolution similarity is computed in Python over all
of a speaker's entities, which is fine for a hackathon-scale graph. Once a
speaker's entity count grows large, swap this for Neo4j's native vector
index instead of changing the calling code's shape.
"""
import logging
import math
import re
import uuid
from datetime import datetime, timezone

from neo4j import GraphDatabase

import config
import llm_client

logger = logging.getLogger(__name__)

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        if not config.NEO4J_URI:
            raise RuntimeError(
                "NEO4J_URI is not set. Copy .env.example to .env and fill it in."
            )
        _driver = GraphDatabase.driver(
            config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
        )
    return _driver


def close_driver():
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def ensure_schema():
    """Create constraints/indexes if they don't exist yet. Safe to call every startup."""
    statements = [
        "CREATE CONSTRAINT entity_speaker_name IF NOT EXISTS "
        "FOR (e:Entity) REQUIRE (e.speaker, e.name) IS UNIQUE",
        "CREATE INDEX episode_speaker IF NOT EXISTS FOR (e:Episode) ON (e.speaker)",
        "CREATE INDEX episode_speaker_timestamp IF NOT EXISTS "
        "FOR (e:Episode) ON (e.speaker, e.timestamp)",
        "CREATE FULLTEXT INDEX episode_raw_text IF NOT EXISTS "
        "FOR (e:Episode) ON EACH [e.raw_text, e.summary]",
    ]
    with get_driver().session() as session:
        for stmt in statements:
            session.run(stmt)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _name_words(name: str) -> list[str]:
    """Lowercased alphanumeric words of an entity name ("My friend Ann" -> [my, friend, ann])."""
    return re.findall(r"[a-z0-9]+", name.lower())


def _names_share_whole_words(a: str, b: str) -> bool:
    """
    True if every word of the shorter name is a whole word of the other.
    Whole words, not raw substrings: "ann" is not a word of "anna".
    """
    words_a, words_b = set(_name_words(a)), set(_name_words(b))
    if not words_a or not words_b:
        return False
    return words_a <= words_b or words_b <= words_a


_ENTITY_MATCH_PROMPT_TEMPLATE = """A person's memory already contains these named entities: {candidates}. A new piece of information mentions an entity named {new_name!r}. Is it CLEARLY the very same real-world person, place or thing as one of the existing entities (the same name with different casing or a typo, a nickname, an abbreviation, or a full name vs. a short form)? Similar-looking but different names usually belong to DIFFERENT people or places, so if there is any real chance it is a different one, answer null. If it is clearly the same, respond with that EXACT existing name.

Output ONLY valid JSON, no prose: {{"match": "<exact existing name>" or null}}"""


def _llm_match_entity(candidates: list[str], new_name: str) -> str | None:
    """
    One LLM call: is new_name clearly the same entity as one of the
    embedding-similar candidates? Strictly validated — anything that isn't
    exactly one of `candidates` (an invented name, malformed or empty
    response, a failed call) is None, i.e. keep them distinct. Same
    closed-list pattern as _llm_match_attribute().
    """
    prompt = _ENTITY_MATCH_PROMPT_TEMPLATE.format(candidates=candidates, new_name=new_name)
    response = llm_client.extract_json(prompt, new_name)
    match = response.get("match") if isinstance(response, dict) else None
    logger.info(
        "Entity LLM match: new=%r candidates=%s -> raw=%r (%s)",
        new_name, candidates, match, "accepted" if match in candidates else "rejected/new",
    )
    return match if match in candidates else None


def resolve_entity(session, speaker: str, name: str, embedding: list[float]) -> str:
    """
    Three-layer entity resolution. Returns the canonical entity id (its
    `name`, which is unique per speaker), creating a new Entity node if
    nothing matches.
    """
    name_lower = name.strip().lower()

    # Layer 1: exact match, case-insensitive
    result = session.run(
        "MATCH (e:Entity {speaker: $speaker}) WHERE toLower(e.name) = $name_lower "
        "RETURN e.name AS name LIMIT 1",
        speaker=speaker, name_lower=name_lower,
    ).single()
    if result:
        return result["name"]

    existing = list(session.run(
        "MATCH (e:Entity {speaker: $speaker}) "
        "RETURN e.name AS name, e.embedding AS embedding",
        speaker=speaker,
    ))

    # Layer 2: whole-word containment (longest existing name wins)
    if len(name_lower) >= config.ENTITY_SUBSTRING_MIN_LEN:
        matches = [row["name"] for row in existing if _names_share_whole_words(name, row["name"])]
        if matches:
            return max(matches, key=len)

    # Layer 3: embedding similarity proposes candidates; the LLM decides.
    scored = sorted(
        ((_cosine(embedding, row["embedding"]), row["name"])
         for row in existing if row["embedding"] is not None),
        reverse=True,
    )
    candidates = [n for score, n in scored if score >= config.ENTITY_SIMILARITY_THRESHOLD]
    if candidates:
        matched = _llm_match_entity(candidates, name)
        if matched:
            return matched

    # No match anywhere: create a new canonical entity
    session.run(
        "MERGE (e:Entity {speaker: $speaker, name: $name}) "
        "ON CREATE SET e.embedding = $embedding, e.created_at = datetime()",
        speaker=speaker, name=name, embedding=embedding,
    )
    return name


_ATTRIBUTE_MATCH_PROMPT_TEMPLATE = """A person's record already tracks these attributes for them: {existing}. A new piece of information uses the attribute name {new_name!r} with value {value!r}. Does this describe the SAME underlying fact as one of the existing attributes (just named differently), or is it a genuinely NEW, distinct attribute? If it matches an existing one, respond with that EXACT existing name. If it's genuinely new, respond with null.

Output ONLY valid JSON, no prose: {{"match": "<exact existing name>" or null}}"""


def _llm_match_attribute(existing: list[str], new_name: str, value) -> str | None:
    """
    One LLM call: is new_name the same underlying fact as one of the
    entity's existing active attribute names? Strictly validated — anything
    that isn't exactly one of `existing` (an invented name, malformed or
    empty response) is treated as None, i.e. "genuinely new".
    """
    prompt = _ATTRIBUTE_MATCH_PROMPT_TEMPLATE.format(
        existing=existing, new_name=new_name, value=value,
    )
    response = llm_client.extract_json(prompt, f"{new_name}: {value}")
    match = response.get("match") if isinstance(response, dict) else None
    logger.info(
        "Attribute LLM match: new=%r value=%r existing=%s -> raw=%r (%s)",
        new_name, value, existing, match, "accepted" if match in existing else "rejected/new",
    )
    return match if match in existing else None


def resolve_attribute(session, speaker: str, entity_name: str, attribute: str,
                       embedding: list[float], value=None) -> str:
    """
    Three-layer attribute-name resolution, mirroring resolve_entity but
    scoped to one entity's own state history rather than global: exact
    match (case-insensitive), then substring containment, then an LLM
    judgment (see Layer 3 below) over the entity's active attribute names.
    Returns the attribute unchanged if nothing matches — it's new.
    """
    attribute_lower = attribute.strip().lower()

    # Layer 1: exact match, case-insensitive
    result = session.run(
        "MATCH (e:Entity {speaker: $speaker, name: $entity_name})-[:OF_ENTITY]-(s:State) "
        "WHERE toLower(s.attribute) = $attribute_lower "
        "RETURN s.attribute AS attribute LIMIT 1",
        speaker=speaker, entity_name=entity_name, attribute_lower=attribute_lower,
    ).single()
    if result:
        return result["attribute"]

    # Layer 2: substring containment (longest existing attribute wins)
    if len(attribute_lower) >= config.ATTRIBUTE_SUBSTRING_MIN_LEN:
        result = session.run(
            "MATCH (e:Entity {speaker: $speaker, name: $entity_name})-[:OF_ENTITY]-(s:State) "
            "WHERE toLower(s.attribute) CONTAINS $attribute_lower OR $attribute_lower CONTAINS toLower(s.attribute) "
            "RETURN s.attribute AS attribute ORDER BY size(s.attribute) DESC LIMIT 1",
            speaker=speaker, entity_name=entity_name, attribute_lower=attribute_lower,
        ).single()
        if result:
            return result["attribute"]

    # Layer 3: LLM judgment against this entity's own active attribute names.
    # Embedding similarity on bare attribute-name strings was tested against
    # real drift (goal vs training, cosine 0.67) and found unreliable — see
    # CLAUDE.md. Replaced with LLM judgment against the entity's own
    # existing vocabulary, same approach already proven for
    # question-to-attribute matching in retrieval.py.
    rows = session.run(
        "MATCH (e:Entity {speaker: $speaker, name: $entity_name})-[:OF_ENTITY]-(s:State {active: true}) "
        "RETURN DISTINCT s.attribute AS attribute",
        speaker=speaker, entity_name=entity_name,
    )
    existing = [row["attribute"] for row in rows]
    if existing:
        matched = _llm_match_attribute(existing, attribute, value)
        if matched:
            return matched

    # No match anywhere: it's a new attribute name for this entity
    return attribute


def create_episode(session, speaker: str, raw_text: str, summary: str,
                    importance: float, embedding: list[float], event_time) -> str:
    episode_id = str(uuid.uuid4())
    session.run(
        "CREATE (ep:Episode {id: $id, speaker: $speaker, raw_text: $raw_text, "
        "summary: $summary, importance: $importance, embedding: $embedding, "
        "event_time: $event_time, timestamp: datetime()})",
        id=episode_id, speaker=speaker, raw_text=raw_text, summary=summary,
        importance=importance, embedding=embedding, event_time=event_time,
    )
    return episode_id


def link_episode_entity(session, episode_id: str, entity_name: str, speaker: str):
    session.run(
        "MATCH (ep:Episode {id: $episode_id}), (e:Entity {speaker: $speaker, name: $name}) "
        "MERGE (ep)-[:INVOLVES]->(e)",
        episode_id=episode_id, speaker=speaker, name=entity_name,
    )


def create_state(session, speaker: str, entity_name: str, attribute: str,
                  value: str, episode_id: str, attribute_embedding: list[float]):
    """
    Write a new State for (entity, attribute), superseding whichever State(s)
    were previously active for that pair. Nothing is deleted. `attribute`
    is expected to already be canonicalized (see graph_engine.resolve_attribute)
    so this matching stays a plain equality check on entity+attribute.
    """
    # Deactivate currently-active states for this (entity, attribute)
    session.run(
        "MATCH (e:Entity {speaker: $speaker, name: $name})-[:OF_ENTITY]-(s:State {attribute: $attribute}) "
        "WHERE s.active = true "
        "SET s.active = false, s.superseded_at = datetime()",
        speaker=speaker, name=entity_name, attribute=attribute,
    )
    state_id = str(uuid.uuid4())
    session.run(
        "MATCH (e:Entity {speaker: $speaker, name: $name}), (ep:Episode {id: $episode_id}) "
        "CREATE (s:State {id: $state_id, attribute: $attribute, value: $value, "
        "attribute_embedding: $attribute_embedding, active: true, created_at: datetime()}) "
        "CREATE (s)-[:OF_ENTITY]->(e) "
        "CREATE (ep)-[:HAS_STATE]->(s) "
        "WITH s, e "
        "OPTIONAL MATCH (e)-[:OF_ENTITY]-(old:State {attribute: $attribute}) "
        "WHERE old.id <> s.id AND old.active = false AND old.superseded_at IS NOT NULL "
        "FOREACH (_ IN CASE WHEN old IS NOT NULL THEN [1] ELSE [] END | "
        "  MERGE (s)-[:SUPERSEDES]->(old))",
        speaker=speaker, name=entity_name, episode_id=episode_id,
        state_id=state_id, attribute=attribute, value=value,
        attribute_embedding=attribute_embedding,
    )


def create_action(session, speaker: str, episode_id: str, actor_name: str,
                   verb: str, object_name: str):
    action_id = str(uuid.uuid4())
    session.run(
        "MATCH (ep:Episode {id: $episode_id}), (actor:Entity {speaker: $speaker, name: $actor_name}) "
        "CREATE (a:Action {id: $action_id, verb: $verb, object_name: $object_name}) "
        "CREATE (ep)-[:HAS_ACTION]->(a) "
        "CREATE (a)-[:BY_ENTITY]->(actor)",
        episode_id=episode_id, speaker=speaker, actor_name=actor_name,
        action_id=action_id, verb=verb, object_name=object_name or "",
    )


def create_relation(session, speaker: str, episode_id: str, subject_name: str,
                     rel_type: str, object_name: str):
    relation_id = str(uuid.uuid4())
    session.run(
        "MATCH (ep:Episode {id: $episode_id}), "
        "(subj:Entity {speaker: $speaker, name: $subject_name}), "
        "(obj:Entity {speaker: $speaker, name: $object_name}) "
        "CREATE (r:Relation {id: $relation_id, type: $rel_type, created_at: datetime()}) "
        "CREATE (ep)-[:HAS_RELATION]->(r) "
        "CREATE (r)-[:FROM_ENTITY]->(subj) "
        "CREATE (r)-[:TO_ENTITY]->(obj)",
        episode_id=episode_id, speaker=speaker, subject_name=subject_name,
        rel_type=rel_type, object_name=object_name, relation_id=relation_id,
    )


def reset_speaker(session, speaker: str) -> dict:
    """
    Deletes every node scoped to `speaker`. Entity and Episode carry the
    speaker property directly; State/Action/Relation don't, so they're
    reached by walking out from the Entity/Episode nodes that own them
    (OF_ENTITY/HAS_STATE, BY_ENTITY/HAS_ACTION, FROM_ENTITY/TO_ENTITY/
    HAS_RELATION) before deleting everything together.
    """
    result = session.run(
        "MATCH (n {speaker: $speaker}) WHERE n:Entity OR n:Episode "
        "OPTIONAL MATCH (n)-[:OF_ENTITY|HAS_STATE|BY_ENTITY|HAS_ACTION|FROM_ENTITY|TO_ENTITY|HAS_RELATION]-(m) "
        "WHERE m:State OR m:Action OR m:Relation "
        "WITH collect(DISTINCT n) AS ns, collect(DISTINCT m) AS ms "
        "UNWIND ns + ms AS x "
        "DETACH DELETE x",
        speaker=speaker,
    )
    summary = result.consume()
    return {
        "nodes_deleted": summary.counters.nodes_deleted,
        "relationships_deleted": summary.counters.relationships_deleted,
    }


def get_recent_episodes(session, speaker: str, limit: int = 10) -> list[dict]:
    """The most recent Episode nodes for this speaker, newest first."""
    rows = session.run(
        "MATCH (ep:Episode {speaker: $speaker}) "
        "RETURN ep.id AS episode_id, ep.summary AS summary, "
        "ep.importance AS importance, ep.timestamp AS timestamp "
        "ORDER BY ep.timestamp DESC LIMIT $limit",
        speaker=speaker, limit=limit,
    )
    return [dict(row) for row in rows]


def entity_history(session, speaker: str, entity_name: str) -> list[dict]:
    """All states (active and superseded) for an entity, newest first — a quick sanity check tool."""
    rows = session.run(
        "MATCH (e:Entity {speaker: $speaker, name: $name})-[:OF_ENTITY]-(s:State) "
        "RETURN s.attribute AS attribute, s.value AS value, s.active AS active, "
        "s.created_at AS created_at, s.superseded_at AS superseded_at "
        "ORDER BY s.created_at DESC",
        speaker=speaker, name=entity_name,
    )
    return [dict(row) for row in rows]
