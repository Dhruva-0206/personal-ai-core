"""
Retrieval: given a natural-language question, find and rank the most
relevant episodes across three lanes — vector similarity, full-text, and a
recency safety net — and merge them into one ranked result. This does NOT
answer the question — just retrieval and ranking. Answering with an LLM
comes later, once ranking itself is confirmed correct.
"""
import math
from concurrent.futures import ThreadPoolExecutor
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
    return _classify_state_status(result["total_states"], result["active_states"])


def _classify_state_status(total_states: int, active_states) -> str:
    """Shared by the per-episode and bulk status lookups so both encode identical logic."""
    active_states = active_states or 0
    if total_states == 0:
        return "no_state"
    if active_states > 0:
        return "current"
    return "superseded"


def _all_episode_state_statuses(session, speaker: str) -> dict[str, str]:
    """
    Same status logic as _episode_state_status(), computed for every one of
    this speaker's episodes in a single query, keyed by episode_id. Lets
    vector_search() avoid one round-trip per scanned episode.
    """
    rows = session.run(
        "MATCH (ep:Episode {speaker: $speaker}) "
        "OPTIONAL MATCH (ep)-[:HAS_STATE]->(s:State) "
        "RETURN ep.id AS episode_id, count(s) AS total_states, "
        "sum(CASE WHEN s.active THEN 1 ELSE 0 END) AS active_states",
        speaker=speaker,
    )
    return {
        row["episode_id"]: _classify_state_status(row["total_states"], row["active_states"])
        for row in rows
    }


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
    rows = list(session.run(
        "MATCH (ep:Episode {speaker: $speaker}) WHERE ep.embedding IS NOT NULL "
        "RETURN ep.id AS episode_id, ep.raw_text AS raw_text, ep.summary AS summary, "
        "ep.importance AS importance, ep.embedding AS embedding, ep.timestamp AS created_at",
        speaker=speaker,
    ))
    # One bulk query for every episode's state status (2 queries total
    # regardless of episode count), instead of one query per scanned episode.
    status_by_episode = _all_episode_state_statuses(session, speaker)
    results = []
    for row in rows:
        similarity = _cosine(query_embedding, row["embedding"])
        recency = _recency_weight(row["created_at"], config.RECENCY_HALF_LIFE_DAYS)
        combined_score = _combined_score(similarity, row["importance"], recency)

        state_status = status_by_episode.get(row["episode_id"], "no_state")
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


_ATTRIBUTE_CLASSIFY_PROMPT_TEMPLATE = """You are matching a user's question to one of their known tracked \
attributes. Known attributes: {attributes}.

Given the question, identify which ONE attribute (if any) the question is \
asking about. Respond with ONLY valid JSON, no prose, no markdown code \
fences, in exactly this shape: {{"attribute": "<one of the known attributes>"}} \
or {{"attribute": null}} if none of them apply. Never invent an attribute \
name that is not in the provided list — only ever return one of the exact \
strings given, or null."""


def _match_attribute_fast(session, speaker: str, question_lower: str) -> str | None:
    """
    Layers 1-2 of attribute matching only: exact match, then substring
    containment against known attribute names — cheap, deterministic, no
    LLM call. Proven correct via the "job" case, which was resolved by
    substring matching alone and never needed anything past this fast
    path.

    Factored out of match_question_to_attribute() so direct_state_lookup()
    can check this fast path on its own terms and decide what to do next
    (classify_temporal_intent() alone, or the combined classify_question()
    call) without going through match_question_to_attribute()'s own
    Layer 3, which would mean two separate LLM calls instead of one. Same
    queries, same behavior as before — this is an extraction, not a
    change to the layers themselves.
    """
    result = session.run(
        "MATCH (:Entity {speaker: $speaker})-[:OF_ENTITY]-(s:State) "
        "WHERE toLower(s.attribute) = $question_lower "
        "RETURN DISTINCT s.attribute AS attribute LIMIT 1",
        speaker=speaker, question_lower=question_lower,
    ).single()
    if result:
        return result["attribute"]

    if len(question_lower) >= config.ATTRIBUTE_SUBSTRING_MIN_LEN:
        result = session.run(
            "MATCH (:Entity {speaker: $speaker})-[:OF_ENTITY]-(s:State) "
            "WHERE toLower(s.attribute) CONTAINS $question_lower OR $question_lower CONTAINS toLower(s.attribute) "
            "RETURN DISTINCT s.attribute AS attribute ORDER BY size(s.attribute) DESC LIMIT 1",
            speaker=speaker, question_lower=question_lower,
        ).single()
        if result:
            return result["attribute"]

    return None


def match_question_to_attribute(session, speaker: str, question: str) -> str | None:
    """
    Layers 1-2 are cheap and deterministic (_match_attribute_fast) —
    proven correct via the "job" case, which was resolved by substring
    matching alone and never needed anything past this fast path.

    Layer 3 is an LLM classification call against the speaker's known
    attribute vocabulary, only triggered when the fast path finds nothing.
    An embedding cosine similarity comparison between a full question and
    a single attribute-name word was tried here first and rejected — see
    CLAUDE.md for the observed scores showing it doesn't separate genuine
    matches from noise at the ATTRIBUTE_SIMILARITY_THRESHOLD calibrated for
    attribute-vs-attribute comparisons (that threshold is untouched by this
    function now; it's still used, unchanged, by graph_engine.resolve_attribute).
    """
    question_lower = question.strip().lower()

    attribute = _match_attribute_fast(session, speaker, question_lower)
    if attribute is not None:
        return attribute

    # Layer 3: LLM classification against the known attribute vocabulary
    rows = session.run(
        "MATCH (:Entity {speaker: $speaker})-[:OF_ENTITY]-(s:State) "
        "RETURN DISTINCT s.attribute AS attribute",
        speaker=speaker,
    )
    known_attributes = [row["attribute"] for row in rows]
    if not known_attributes:
        return None

    system_prompt = _ATTRIBUTE_CLASSIFY_PROMPT_TEMPLATE.format(attributes=known_attributes)
    classification = llm_client.extract_json(system_prompt, question)
    candidate = classification.get("attribute")
    if candidate in known_attributes:
        return candidate
    return None


_TEMPORAL_CLASSIFY_PROMPT = """You are classifying whether a user's question is asking about the \
CURRENT/present value of something, or a PAST/PREVIOUS value.

Respond with ONLY valid JSON, no prose, no markdown code fences, in \
exactly this shape: {"temporal": "current"} or {"temporal": "historical"}."""


def classify_temporal_intent(question: str) -> str:
    """
    Classifies a question as asking about the "current" or "historical"
    value of an attribute, via a single LLM call.

    Only ever called after match_question_to_attribute() has already
    resolved an attribute — never called speculatively when there's
    nothing to look up.

    Defaults to "current" whenever the response is missing, malformed, or
    anything other than exactly one of the two expected strings — that's
    the safer default, since misclassifying as "historical" would return
    a superseded value in place of the real answer.
    """
    classification = llm_client.extract_json(_TEMPORAL_CLASSIFY_PROMPT, question)
    temporal = classification.get("temporal")
    if temporal in ("current", "historical"):
        return temporal
    return "current"


SELF_ENTITY_NAME = "User"

_SUBJECT_RULES = """"subject" says WHO the question is about: the exact string "self" if it is about the \
person asking (I, me, my, mine, myself), otherwise the EXACT name of one of these known entities: \
{entities} — or null if it is about someone or something not in that list. Never invent a name that is \
not in the list."""

_ANCHOR_RULES = """"anchor" is only for "historical" questions that name a specific value as the \
reference point, e.g. "where did I live before Melbourne" -> "Melbourne"; give just that value as the user \
wrote it. Use null if the question is "current" or names no such value (e.g. "what was my previous city")."""

_TEMPORAL_SUBJECT_PROMPT_TEMPLATE = """You are classifying a user's question three ways: (1) "temporal": \
"current" if it asks about the CURRENT/present value of something, "historical" if it asks about a \
PAST/PREVIOUS value; (2) """ + _SUBJECT_RULES + """ (3) """ + _ANCHOR_RULES + """

Respond with ONLY valid JSON, no prose, no markdown code fences, in exactly this shape: \
{{"temporal": "<current or historical>", "subject": "<self, an entity name, or null>", "anchor": "<value as written, or null>"}}"""

_COMBINED_CLASSIFY_PROMPT_TEMPLATE = """You are matching a user's question to one of their known tracked \
attributes, classifying whether it's asking about a CURRENT/present value or a PAST/PREVIOUS value, \
saying who it is about, and naming any anchor value. Known attributes: {attributes}.

Do all four in a single response. "attribute" is one of the known attributes (exact string), or null if \
none apply; "temporal" is "current" or "historical" regardless of whether an attribute matched; """ + _SUBJECT_RULES + """ """ + _ANCHOR_RULES + """

Respond with ONLY valid JSON, no prose, no markdown code fences, in exactly this shape: \
{{"attribute": "<one of the known attributes>", "temporal": "<current or historical>", "subject": "<self, an entity name, or null>", "anchor": "<value as written, or null>"}}. Never invent an \
attribute name that is not in the provided list — only ever return one of the exact strings given, or \
null."""


def _resolve_subject(raw, entity_names: list[str]) -> str | None:
    """
    Maps the model's "subject" answer onto an actual Entity name, or None.
    "self" resolves to the canonical self entity ("User", per
    extraction.py's self-reference normalization); anything else must
    case-insensitively equal an entity that really exists for this
    speaker. Never trusts an invented name — None means "don't guess".
    """
    if not isinstance(raw, str):
        return None
    wanted = raw.strip().lower()
    if wanted == "self":
        return SELF_ENTITY_NAME
    for name in entity_names:
        if name.lower() == wanted:
            return name
    return None


def _clean_anchor(raw, temporal: str) -> str | None:
    """
    The anchor is only meaningful for historical questions and must be a
    non-empty string; anything else is None ("no anchor named").
    """
    if temporal != "historical" or not isinstance(raw, str):
        return None
    return raw.strip() or None


def classify_temporal_and_subject(question: str, entity_names: list[str]) -> dict:
    """
    One LLM call returning {"temporal": ..., "subject": <entity name|None>,
    "anchor": <value string|None>}.
    Used when the cheap attribute layers already resolved the attribute.
    Same safe default as classify_temporal_intent() for temporal ("current");
    an unresolvable subject is None, and the caller must not look anything
    up in that case.
    """
    system_prompt = _TEMPORAL_SUBJECT_PROMPT_TEMPLATE.format(entities=entity_names)
    classification = llm_client.extract_json(system_prompt, question)
    temporal = classification.get("temporal")
    if temporal not in ("current", "historical"):
        temporal = "current"
    return {
        "temporal": temporal,
        "subject": _resolve_subject(classification.get("subject"), entity_names),
        "anchor": _clean_anchor(classification.get("anchor"), temporal),
    }


def classify_question(question: str, known_attributes: list[str], entity_names: list[str]) -> dict:
    """
    Combines attribute matching's LLM layer, temporal-intent
    classification and subject resolution (whose state is being asked
    about) into a single llm_client.extract_json() call, for the case where
    the cheap attribute-matching layers already came back empty and an LLM
    call is needed anyway.

    Validates each field independently: "attribute" is forced to None
    unless it's exactly one of known_attributes (never trust an invented
    name); "temporal" defaults to "current" unless it's exactly "current"
    or "historical" (misclassifying as "historical" would substitute a
    superseded value for the current one); "subject" resolves to an
    existing entity name via _resolve_subject() or None. If the call fails
    or parses badly, extract_json() returns {} and the result is
    {"attribute": None, "temporal": "current", "subject": None, "anchor": None}.
    "anchor" is the value a historical question names as its reference
    point ("before Melbourne" -> "Melbourne"), as free text; it is only ever
    used after being matched against the subject's real state values.
    """
    system_prompt = _COMBINED_CLASSIFY_PROMPT_TEMPLATE.format(
        attributes=known_attributes, entities=entity_names,
    )
    classification = llm_client.extract_json(system_prompt, question)

    attribute = classification.get("attribute")
    if attribute not in known_attributes:
        attribute = None

    temporal = classification.get("temporal")
    if temporal not in ("current", "historical"):
        temporal = "current"

    return {
        "attribute": attribute,
        "temporal": temporal,
        "subject": _resolve_subject(classification.get("subject"), entity_names),
        "anchor": _clean_anchor(classification.get("anchor"), temporal),
    }


def _find_anchor_state(session, speaker: str, subject: str, attribute: str, anchor: str) -> str | None:
    """
    Returns the id of the subject's State whose value the anchor names, or
    None (never a guess). Values are matched the way entity and attribute
    names are elsewhere in the system: case-insensitive exact match first,
    then case-insensitive substring containment (either direction) — and
    the substring layer only counts if it is unambiguous (all containing
    matches carry the same value). When the same value occurs more than
    once in the history (e.g. Sydney, ..., Sydney again) the most recently
    created occurrence is used.
    """
    rows = list(session.run(
        "MATCH (e:Entity {speaker: $speaker, name: $subject})-[:OF_ENTITY]-(s:State {attribute: $attribute}) "
        "RETURN s.id AS id, s.value AS value ORDER BY s.created_at DESC",
        speaker=speaker, subject=subject, attribute=attribute,
    ))
    wanted = anchor.strip().lower()
    states = [(row["id"], str(row["value"]).strip().lower()) for row in rows if row["value"] is not None]

    for state_id, value in states:
        if value == wanted:
            return state_id

    partial = [(state_id, value) for state_id, value in states if wanted in value or value in wanted]
    if partial and len({value for _, value in partial}) == 1:
        return partial[0][0]
    return None


def direct_state_lookup(session, speaker: str, question: str) -> dict | None:
    """
    Checks known states before falling back to fuzzy multi-lane search.
    Scoped to the ONE entity the question is about: the classification
    call also resolves a subject ("self" -> the canonical "User" entity,
    or the exact name of an existing entity for third-party questions),
    and every state query filters on that entity name, so it cannot return
    another entity's state. If the subject can't be resolved to an
    existing entity, returns None (no guess) and the fuzzy lanes handle
    the question.

    Classification is one LLM call in either direction, never two:
    - If _match_attribute_fast() (the cheap exact/substring layers)
      resolves an attribute, only classify_temporal_and_subject() runs.
    - If the cheap layers find nothing, classify_question() runs once,
      doing attribute matching's LLM layer, temporal classification and
      subject resolution together.

    Once attribute, temporal and subject are known, "current" questions
    check the subject's active=true state. "Historical" questions that
    name an anchor value ("before Melbourne") return the state immediately
    preceding the anchor's state (see _find_anchor_state and the
    SUPERSEDES walk below); an anchor matching nothing returns None.
    "Historical" questions with no anchor ("my previous city") take the
    subject's single most recently superseded state via ORDER BY
    s.superseded_at DESC LIMIT 1.

    Returns None (not a guess) whenever the match is ambiguous or absent:
    no attribute match, an unresolvable subject, no active state for that
    attribute on the subject when asking about the current value (or more
    than one), or no superseded state at all when asking about a
    historical value.
    """
    question_lower = question.strip().lower()
    entity_names = [
        row["name"] for row in session.run(
            "MATCH (e:Entity {speaker: $speaker}) RETURN e.name AS name",
            speaker=speaker,
        )
    ]
    attribute = _match_attribute_fast(session, speaker, question_lower)

    if attribute is not None:
        classification = classify_temporal_and_subject(question, entity_names)
    else:
        rows = session.run(
            "MATCH (:Entity {speaker: $speaker})-[:OF_ENTITY]-(s:State) "
            "RETURN DISTINCT s.attribute AS attribute",
            speaker=speaker,
        )
        known_attributes = [row["attribute"] for row in rows]
        if not known_attributes:
            return None

        classification = classify_question(question, known_attributes, entity_names)
        attribute = classification["attribute"]
        if attribute is None:
            return None

    temporal = classification["temporal"]
    subject = classification["subject"]
    anchor = classification.get("anchor")
    if subject is None:
        return None

    if temporal == "historical" and anchor is not None:
        # "before X": find the state whose value is X, then its immediate
        # predecessor. SUPERSEDES edges run from each new State to EVERY
        # older inactive State of the pair (graph_engine.create_state), so
        # the immediate predecessor is the most recently created State the
        # anchor state supersedes. An anchor that matches no state in the
        # chain returns None — never a fallback to the most recent one.
        anchor_id = _find_anchor_state(session, speaker, subject, attribute, anchor)
        if anchor_id is None:
            return None
        rows = list(session.run(
            "MATCH (:State {id: $anchor_id})-[:SUPERSEDES]->(p:State) "
            "RETURN p.value AS value ORDER BY p.created_at DESC LIMIT 1",
            anchor_id=anchor_id,
        ))
        if not rows:
            return None
        return {
            "source": "direct_lookup",
            "attribute": attribute,
            "value": rows[0]["value"],
            "entity": subject,
            "confidence": "high",
            "state_status": "superseded",
        }

    if temporal == "historical":
        rows = list(session.run(
            "MATCH (e:Entity {speaker: $speaker, name: $subject})-[:OF_ENTITY]-(s:State {attribute: $attribute}) "
            "WHERE s.active = false "
            "RETURN e.name AS entity, s.value AS value "
            "ORDER BY s.superseded_at DESC LIMIT 1",
            speaker=speaker, subject=subject, attribute=attribute,
        ))
        if not rows:
            return None

        row = rows[0]
        return {
            "source": "direct_lookup",
            "attribute": attribute,
            "value": row["value"],
            "entity": row["entity"],
            "confidence": "high",
            "state_status": "superseded",
        }

    rows = list(session.run(
        "MATCH (e:Entity {speaker: $speaker, name: $subject})-[:OF_ENTITY]-(s:State {attribute: $attribute}) "
        "WHERE s.active = true "
        "RETURN e.name AS entity, s.value AS value",
        speaker=speaker, subject=subject, attribute=attribute,
    ))
    if len(rows) != 1:
        return None

    row = rows[0]
    return {
        "source": "direct_lookup",
        "attribute": attribute,
        "value": row["value"],
        "entity": row["entity"],
        "confidence": "high",
        "state_status": "current",
    }


def _run_lane_in_own_session(driver, lane_fn, *args, **kwargs):
    """
    Opens a fresh Neo4j session for lane_fn and runs it. Used to run the
    three fuzzy lanes concurrently in retrieve(): the Driver is
    thread-safe and meant to be shared, but a Session is explicitly NOT
    thread-safe (per the neo4j Python driver's own contract) — so each
    concurrently-running lane gets its own session instead of sharing the
    one already open for direct_state_lookup()/the final merge loop.
    """
    with driver.session() as session:
        return lane_fn(session, *args, **kwargs)


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
    Checks direct_state_lookup() first — if the question maps unambiguously
    to one known state (active for a current-value question, or the most
    recently superseded one for a historical-value question), that's
    prepended to the front of the results with combined_score=1.0,
    lanes=["direct_state_lookup"], and state_status matching whichever
    branch direct_state_lookup() took. The three fuzzy lanes below still
    run and merge exactly as before either way; direct lookup only ever
    adds a result, never replaces the fuzzy pass.

    Runs all three lanes unconditionally, merges by episode_id (keeping the
    highest raw lane score and recording every contributing lane), applies
    the supersession penalty exactly once per merged candidate regardless
    of which lane(s) surfaced it, then sorts and returns the top_k.

    The three lanes are independent (none consumes another's output) and
    each does its own Neo4j network I/O, so they run concurrently via
    ThreadPoolExecutor — pure latency optimization, no change to what any
    lane returns or how results are merged/scored. direct_state_lookup()
    (and the query_embedding call before it) stays sequential and runs
    first, since a successful direct lookup already short-circuits
    downstream logic elsewhere in the caller; only the three fuzzy lanes
    run concurrently with each other. Each concurrent lane opens its own
    session via _run_lane_in_own_session() rather than sharing the
    session used for direct_state_lookup()/the final merge loop, since
    Neo4j Session objects are not thread-safe.

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
        direct_result = direct_state_lookup(session, speaker, question)

        with ThreadPoolExecutor(max_workers=3) as executor:
            vector_future = executor.submit(
                _run_lane_in_own_session, driver, vector_search, speaker, query_embedding, top_k=top_k
            )
            fulltext_future = executor.submit(
                _run_lane_in_own_session, driver, fulltext_lane, speaker, question, limit=top_k
            )
            recent_future = executor.submit(
                _run_lane_in_own_session, driver, recent_lane, speaker
            )

            vector_results = vector_future.result()
            fulltext_results = fulltext_future.result()
            recent_results = recent_future.result()

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

        if direct_result is not None:
            final_results.insert(0, {
                "episode_id": None,
                "raw_text": None,
                "summary": f"{direct_result['entity']}: {direct_result['attribute']} = {direct_result['value']}",
                "importance": None,
                "similarity": None,
                "recency": None,
                "combined_score": 1.0,
                "state_status": direct_result["state_status"],
                "lanes": ["direct_state_lookup"],
                "attribute": direct_result["attribute"],
                "value": direct_result["value"],
                "entity": direct_result["entity"],
                "confidence": direct_result["confidence"],
            })

        return final_results[:top_k]
