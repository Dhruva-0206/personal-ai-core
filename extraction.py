"""
The single extraction prompt that turns one raw piece of input (a message,
a transcript line, a manual log entry) into typed graph primitives, plus a
self-assigned importance score. One call does all of this — there is no
separate scoring model.
"""
import llm_client

SYSTEM_PROMPT = """You are a memory extraction engine. Given one piece of raw \
input from a person's life, extract structured facts as JSON. Output ONLY \
valid JSON, no prose, no markdown code fences.

Schema:
{
  "summary": "one sentence summarizing this input",
  "importance": 0.0-1.0,
  "event_time": "ISO 8601 datetime if a specific time is mentioned or implied, else null",
  "entities": [{"name": "canonical name as mentioned", "type": "person|place|organization|thing|concept"}],
  "states": [{"entity": "entity name", "attribute": "short attribute name e.g. city, job, relationship_status", "value": "current value of that attribute"}],
  "actions": [{"actor": "entity name performing the action", "verb": "short verb", "object": "entity name or free text object, may be empty"}],
  "relations": [{"subject": "entity name", "type": "relation type e.g. works_at, friend_of, located_in", "object": "entity name"}]
}

Whenever the input refers to the person speaking/logging this entry themselves \
(via "I", "me", "my", "mine", or their own name), extract that entity with the \
exact literal name "User" every time, so it is always the same canonical \
string regardless of phrasing.

When applicable, prefer these attribute names: city, job, relationship_status, \
employer, health_condition — but use a different short attribute name if none \
of these fit.

Importance scoring rubric (self-assign, do not skip this):
- 0.1-0.2: incidental filler, small talk, no lasting relevance
- 0.35-0.45: sensory detail or secondary context (NEVER score below 0.35 if it describes a real sensory or emotional detail)
- 0.5-0.6: notable but ordinary — a normal day-to-day fact worth remembering
- 0.7-0.85: major — job change, health event, significant decision
- 0.9-1.0: life-defining — marriage, birth, diagnosis, relocation, career milestone

Only extract states for facts that could change over time (attributes), not \
one-off events — one-off events belong in "actions" instead. If the input \
mentions no identifiable entities, return empty lists but still fill in \
"summary" and "importance"."""


SELF_REFERENCE_ALIASES = {"i", "me", "my", "mine", "myself", "user"}


def _canon_self_reference(name):
    if isinstance(name, str) and name.strip().lower() in SELF_REFERENCE_ALIASES:
        return "User"
    return name


def _normalize_self_references(result: dict) -> dict:
    """
    Deterministically rewrite every entity-name occurrence in the parsed
    result to "User" if it refers to the speaker (I/me/my/mine/myself),
    so canonicalization never depends on the LLM following the prompt.
    """
    for entity in result.get("entities", []):
        entity["name"] = _canon_self_reference(entity.get("name"))
    for state in result.get("states", []):
        state["entity"] = _canon_self_reference(state.get("entity"))
    for action in result.get("actions", []):
        action["actor"] = _canon_self_reference(action.get("actor"))
        if "object" in action:
            action["object"] = _canon_self_reference(action.get("object"))
    for relation in result.get("relations", []):
        relation["subject"] = _canon_self_reference(relation.get("subject"))
        relation["object"] = _canon_self_reference(relation.get("object"))
    return result


def extract(raw_text: str) -> dict:
    """
    Run extraction on one raw episode. Returns a dict matching the schema
    above, or a minimal fallback dict if the model call/parse fails, so a
    single bad response never crashes the whole ingestion run.
    """
    result = llm_client.extract_json(SYSTEM_PROMPT, raw_text)
    if not result:
        return _fallback(raw_text)

    result.setdefault("summary", raw_text[:200])
    result.setdefault("importance", 0.5)
    result.setdefault("event_time", None)
    result.setdefault("entities", [])
    result.setdefault("states", [])
    result.setdefault("actions", [])
    result.setdefault("relations", [])
    return _normalize_self_references(result)


def _fallback(raw_text: str) -> dict:
    result = {
        "summary": raw_text[:200],
        "importance": 0.5,
        "event_time": None,
        "entities": [],
        "states": [],
        "actions": [],
        "relations": [],
    }
    return _normalize_self_references(result)
