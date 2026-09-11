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
    return result


def _fallback(raw_text: str) -> dict:
    return {
        "summary": raw_text[:200],
        "importance": 0.5,
        "event_time": None,
        "entities": [],
        "states": [],
        "actions": [],
        "relations": [],
    }
