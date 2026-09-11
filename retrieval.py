"""
Retrieval: given a natural-language question, find and rank the most
relevant episodes across three lanes — vector similarity, full-text, and a
recency safety net — and merge them into one ranked result. This does NOT
answer the question — just retrieval and ranking. Answering with an LLM
comes later, once ranking itself is confirmed correct.
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


def _episode_state_status(session, speaker: str, episode_id: str) -> str:
    """
    "current" if the episode wrote at least one still-active State,
    "superseded" if it wrote at least one State but every one is now
    inactive, "no_state" if it wrote none.
    """
    result = session.run(
        "MATCH (ep:Episode {speaker: $speaker, id: $episode_id}) "
        "OPTIONAL MATCH (ep)-[:HAS_STATE]->(s:State) "
        "RETURN count(s) AS total_states, "
        "sum(CASE WHEN s.active THEN 1 ELSE 0 END) AS active_states",
        speaker=speaker, episode_id=episode_id,
    ).single()
    total_states = result["total_states"]
    active_states = result["active_states"] or 0
    if total_states == 0:
        return "no_state"
    if active_states > 0:
        return "current"
    return "superseded"


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
        "RETURN ep.id AS episode_id, ep.raw_text AS raw_text, ep.summary AS summary, "
        "ep.importance AS importance, ep.embedding AS embedding, ep.timestamp AS created_at",
        speaker=speaker,
    )
    results = []
    for row in rows:
        similarity = _cosine(query_embedding, row["embedding"])
        recency = _recency_weight(row["created_at"], config.RECENCY_HALF_LIFE_DAYS)
        combined_score = _combined_score(similarity, row["importance"], recency)

        state_status = _episode_state_status(session, speaker, row["episode_id"])
        if state_status == "superseded":
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


def fulltext_lane(session, speaker: str, query_text: str, limit: int = 12) -> list[dict]:
    """
    Keyword lane over the episode_raw_text fulltext index (created in
    graph_engine.ensure_schema()). Raw Lucene scores are min-max normalized
    into [config.FULLTEXT_SCORE_MIN, config.FULLTEXT_SCORE_MAX] so they're
    comparable to the other lanes; a single result gets the max of that
    range since there's no spread to scale against.
    """
    rows = list(session.run(
        "CALL db.index.fulltext.queryNodes('episode_raw_text', $query_text) "
        "YIELD node, score "
        "WHERE node.speaker = $speaker "
        "RETURN node.id AS episode_id, node.raw_text AS raw_text, "
        "node.summary AS summary, node.importance AS importance, score "
        "ORDER BY score DESC LIMIT $limit",
        query_text=query_text, speaker=speaker, limit=limit,
    ))
    if not rows:
        return []

    scores = [row["score"] for row in rows]
    min_score, max_score = min(scores), max(scores)
    span = max_score - min_score

    results = []
    for row in rows:
        if span == 0:
            normalized = config.FULLTEXT_SCORE_MAX
        else:
            normalized = config.FULLTEXT_SCORE_MIN + (row["score"] - min_score) / span * (
                config.FULLTEXT_SCORE_MAX - config.FULLTEXT_SCORE_MIN
            )
        results.append({
            "episode_id": row["episode_id"],
            "raw_text": row["raw_text"],
            "summary": row["summary"],
            "importance": row["importance"],
            "score": normalized,
            "lane": "fulltext",
        })
    return results


def recent_lane(session, speaker: str) -> list[dict]:
    """
    Safety net: the most recently logged episodes always come back with a
    fixed score, regardless of content, so something-you-just-said stays
    retrievable even if it scores poorly on similarity/fulltext.
    """
    rows = session.run(
        "MATCH (ep:Episode {speaker: $speaker}) "
        "WHERE ep.timestamp >= datetime() - duration({minutes: $minutes}) "
        "RETURN ep.id AS episode_id, ep.raw_text AS raw_text, ep.summary AS summary, "
        "ep.importance AS importance "
        "ORDER BY ep.timestamp DESC LIMIT $limit",
        speaker=speaker, minutes=config.RECENT_WINDOW_MINUTES, limit=config.RECENT_LANE_MAX,
    )
    return [
        {
            "episode_id": row["episode_id"],
            "raw_text": row["raw_text"],
            "summary": row["summary"],
            "importance": row["importance"],
            "score": config.RECENT_LANE_SCORE,
            "lane": "recent",
        }
        for row in rows
    ]


# lanes means "which lane(s) returned this candidate", not "which lane(s)
# found it relevant" — use the similarity/importance/recency fields
# alongside it to judge actual relevance, not lanes membership alone.
def _merge_candidate(merged: dict, episode_id: str, raw_score: float, lane: str, fields: dict):
    entry = merged.get(episode_id)
    if entry is None:
        merged[episode_id] = {**fields, "raw_score": raw_score, "lanes": [lane]}
        return
    if lane not in entry["lanes"]:
        entry["lanes"].append(lane)
    if raw_score > entry["raw_score"]:
        entry["raw_score"] = raw_score


def retrieve(question: str, speaker: str = None, top_k: int = 12) -> list[dict]:
    """
    Runs all three lanes unconditionally, merges by episode_id (keeping the
    highest raw lane score and recording every contributing lane), applies
    the supersession penalty exactly once per merged candidate regardless
    of which lane(s) surfaced it, then sorts and returns the top_k.

    For the vector lane, the pre-penalty score is recovered before merging
    (dividing back out config.OUTDATED_STATE_PENALTY when vector_search
    already applied it) so the penalty is never applied twice to the same
    episode — vector_search's own standalone behavior is unchanged, this
    recovery only affects what feeds the merge.
    """
    speaker = speaker or config.DEFAULT_SPEAKER
    query_embedding = llm_client.embed(question)
    driver = graph_engine.get_driver()
    with driver.session() as session:
        vector_results = vector_search(session, speaker, query_embedding, top_k=top_k)
        fulltext_results = fulltext_lane(session, speaker, question, limit=top_k)
        recent_results = recent_lane(session, speaker)

        merged = {}

        for r in vector_results:
            raw_score = r["combined_score"]
            if r["state_status"] == "superseded":
                raw_score = raw_score / config.OUTDATED_STATE_PENALTY
            _merge_candidate(merged, r["episode_id"], raw_score, "vector", {
                "episode_id": r["episode_id"],
                "raw_text": r["raw_text"],
                "summary": r["summary"],
                "importance": r["importance"],
                "similarity": r["similarity"],
                "recency": r["recency"],
            })

        for r in fulltext_results:
            _merge_candidate(merged, r["episode_id"], r["score"], "fulltext", {
                "episode_id": r["episode_id"],
                "raw_text": r["raw_text"],
                "summary": r["summary"],
                "importance": r["importance"],
                "similarity": None,
                "recency": None,
            })

        for r in recent_results:
            _merge_candidate(merged, r["episode_id"], r["score"], "recent", {
                "episode_id": r["episode_id"],
                "raw_text": r["raw_text"],
                "summary": r["summary"],
                "importance": r["importance"],
                "similarity": None,
                "recency": None,
            })

        final_results = []
        for episode_id, entry in merged.items():
            state_status = _episode_state_status(session, speaker, episode_id)
            combined_score = entry["raw_score"]
            if state_status == "superseded":
                combined_score *= config.OUTDATED_STATE_PENALTY
            final_results.append({
                "episode_id": episode_id,
                "raw_text": entry["raw_text"],
                "summary": entry["summary"],
                "importance": entry["importance"],
                "similarity": entry["similarity"],
                "recency": entry["recency"],
                "combined_score": combined_score,
                "state_status": state_status,
                "lanes": entry["lanes"],
            })

        final_results.sort(key=lambda r: r["combined_score"], reverse=True)
        return final_results[:top_k]
