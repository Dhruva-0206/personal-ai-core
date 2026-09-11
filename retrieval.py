"""
First retrieval capability: given a natural-language question, find and
rank the most relevant episodes using vector similarity plus the
importance/recency scoring formula. This does NOT answer the question —
just retrieval and ranking. Answering with an LLM comes later, once
ranking itself is confirmed correct.
"""
import math
from datetime import datetime, timezone

import config
import graph_engine
import llm_client
from graph_engine import _cosine


def _recency_weight(created_at, half_life_days: float) -> float:
    """Exponential decay based on age in days: exp(-ln(2) * age_days / half_life_days)."""
    if hasattr(created_at, "to_native"):
        created_at = created_at.to_native()
    age_days = (datetime.now(timezone.utc) - created_at).total_seconds() / 86400.0
    return math.exp(-math.log(2) * age_days / half_life_days)


def _combined_score(vector_similarity: float, importance: float, recency: float) -> float:
    """
    Weighted sum of similarity/importance/recency. Landmark bypass: an
    episode important enough to clear IMPORTANCE_LANDMARK_FLOOR gets
    recency forced to 1.0 first, so old-but-important episodes don't lose
    to recent trivial ones.
    """
    if importance >= config.IMPORTANCE_LANDMARK_FLOOR:
        recency = 1.0
    return (
        config.WEIGHT_SIMILARITY * vector_similarity
        + config.WEIGHT_IMPORTANCE * importance
        + config.WEIGHT_RECENCY * recency
    )


def vector_search(session, speaker: str, query_embedding: list[float], top_k: int = 12) -> list[dict]:
    """
    Full per-speaker scan of Episode nodes (matches graph_engine's existing
    approach for entity/attribute resolution at this scale — no Neo4j
    vector index yet). Returns the top_k episodes ranked by combined score.

    Episodes whose every written State has since been superseded get
    combined_score scaled by config.OUTDATED_STATE_PENALTY — a fully
    outdated fact shouldn't outrank the current one purely on text
    similarity. Episodes with zero states, or with at least one still-active
    state, are never penalized.
    """
    rows = session.run(
        "MATCH (ep:Episode {speaker: $speaker}) WHERE ep.embedding IS NOT NULL "
        "OPTIONAL MATCH (ep)-[:HAS_STATE]->(s:State) "
        "WITH ep, count(s) AS total_states, "
        "sum(CASE WHEN s.active THEN 1 ELSE 0 END) AS active_states "
        "RETURN ep.id AS episode_id, ep.raw_text AS raw_text, ep.summary AS summary, "
        "ep.importance AS importance, ep.embedding AS embedding, ep.timestamp AS created_at, "
        "total_states, active_states",
        speaker=speaker,
    )
    results = []
    for row in rows:
        similarity = _cosine(query_embedding, row["embedding"])
        recency = _recency_weight(row["created_at"], config.RECENCY_HALF_LIFE_DAYS)
        combined_score = _combined_score(similarity, row["importance"], recency)

        total_states = row["total_states"]
        active_states = row["active_states"] or 0
        if total_states == 0:
            state_status = "no_state"
        elif active_states > 0:
            state_status = "current"
        else:
            state_status = "superseded"
            combined_score *= config.OUTDATED_STATE_PENALTY

        results.append({
            "episode_id": row["episode_id"],
            "raw_text": row["raw_text"],
            "summary": row["summary"],
            "importance": row["importance"],
            "similarity": similarity,
            "recency": recency,
            "combined_score": combined_score,
            "state_status": state_status,
        })
    results.sort(key=lambda r: r["combined_score"], reverse=True)
    return results[:top_k]


def retrieve(question: str, speaker: str = None, top_k: int = 12) -> list[dict]:
    speaker = speaker or config.DEFAULT_SPEAKER
    query_embedding = llm_client.embed(question)
    driver = graph_engine.get_driver()
    with driver.session() as session:
        return vector_search(session, speaker, query_embedding, top_k=top_k)
