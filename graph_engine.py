"""
The temporal knowledge graph itself. Every write and read is scoped to a
`speaker` (tenant) so different people's memories never mix.

Entity resolution is three layers, in order, first match wins:
  1. exact match (case-insensitive)
  2. substring containment
  3. embedding cosine similarity above config.ENTITY_SIMILARITY_THRESHOLD

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
import uuid
from datetime import datetime, timezone

from neo4j import GraphDatabase

import config

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

    # Layer 2: substring containment (longest existing name wins)
    if len(name_lower) >= config.ENTITY_SUBSTRING_MIN_LEN:
        result = session.run(
            "MATCH (e:Entity {speaker: $speaker}) "
            "WHERE toLower(e.name) CONTAINS $name_lower OR $name_lower CONTAINS toLower(e.name) "
            "RETURN e.name AS name ORDER BY size(e.name) DESC LIMIT 1",
            speaker=speaker, name_lower=name_lower,
        ).single()
        if result:
            return result["name"]

    # Layer 3: embedding cosine similarity above threshold
    rows = session.run(
        "MATCH (e:Entity {speaker: $speaker}) WHERE e.embedding IS NOT NULL "
        "RETURN e.name AS name, e.embedding AS embedding",
        speaker=speaker,
    )
    best_name, best_score = None, 0.0
    for row in rows:
        score = _cosine(embedding, row["embedding"])
        if score > best_score:
            best_name, best_score = row["name"], score
    if best_name and best_score >= config.ENTITY_SIMILARITY_THRESHOLD:
        return best_name

    # No match anywhere: create a new canonical entity
    session.run(
        "MERGE (e:Entity {speaker: $speaker, name: $name}) "
        "ON CREATE SET e.embedding = $embedding, e.created_at = datetime()",
        speaker=speaker, name=name, embedding=embedding,
    )
    return name


def resolve_attribute(session, speaker: str, entity_name: str, attribute: str,
                       embedding: list[float]) -> str:
    """
    Three-layer attribute-name resolution, mirroring resolve_entity but
    scoped to one entity's own state history rather than global: exact
    match (case-insensitive), then substring containment, then embedding
    cosine similarity above config.ATTRIBUTE_SIMILARITY_THRESHOLD, over the
    distinct attribute names ever used on States attached to this entity.
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

    # Layer 3: embedding cosine similarity above threshold
    rows = session.run(
        "MATCH (e:Entity {speaker: $speaker, name: $entity_name})-[:OF_ENTITY]-(s:State) "
        "WHERE s.attribute_embedding IS NOT NULL "
        "RETURN DISTINCT s.attribute AS attribute, s.attribute_embedding AS embedding",
        speaker=speaker, entity_name=entity_name,
    )
    best_attribute, best_score = None, 0.0
    for row in rows:
        score = _cosine(embedding, row["embedding"])
        if 0.64 <= score <= 0.80:
            logger.info(
                "Borderline attribute match: '%s' vs '%s' = %s",
                attribute, row["attribute"], score,
            )
        if score > best_score:
            best_attribute, best_score = row["attribute"], score
    if best_attribute and best_score >= config.ATTRIBUTE_SIMILARITY_THRESHOLD:
        return best_attribute

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
