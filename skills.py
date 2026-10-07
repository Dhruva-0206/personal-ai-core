"""
Foundation of the agent/skills layer: a tiered skill registry with a
confirmation gate for high-stakes actions. No real calendar/purchasing
integration yet — set_reminder and make_purchase are stubs that prove the
tiering mechanism works; query_memory is the one real skill, backed by
Phase 2's retrieval layer.

Tiers:
  read_only        — executes immediately, no side effects.
  reversible_write — executes immediately, side effects are cheap to undo.
  high_stakes       — never executes on its own; run_skill() returns a
                      "needs_confirmation" description, and only an
                      explicit call to confirm_skill() (after a human
                      "yes") actually runs it.
"""
from dataclasses import dataclass
from typing import Callable

import retrieval

TIERS = {"read_only", "reversible_write", "high_stakes"}


@dataclass
class Skill:
    name: str
    tier: str
    description: str
    func: Callable[..., dict]
    parameters: dict
    # True for skills that read per-speaker data: run_skill() then passes
    # them the caller's trusted speaker as a `speaker` keyword, overwriting
    # anything the model put in its own arguments.
    speaker_scoped: bool = False


REGISTRY: dict[str, Skill] = {}


def register(skill: Skill):
    if skill.tier not in TIERS:
        raise ValueError(f"Unknown tier '{skill.tier}' for skill '{skill.name}'")
    REGISTRY[skill.name] = skill


QUERY_MEMORY_TOP_N = 5


def _query_memory(question: str, speaker: str = None) -> dict:
    """
    REAL skill: returns the top QUERY_MEMORY_TOP_N retrieval.retrieve() results
    for a question, scoped to `speaker` (None -> config.DEFAULT_SPEAKER).
    """
    results = retrieval.retrieve(question, speaker=speaker)
    if not results:
        return {"question": question, "results": [], "message": "No results found."}
    fields = ("summary", "combined_score", "similarity", "importance", "recency", "state_status", "lanes")
    return {
        "question": question,
        "results": [
            {
                **{k: r[k] for k in fields},
                "match_type": "direct_lookup" if "direct_state_lookup" in r["lanes"] else "fuzzy_relevance",
            }
            for r in results[:QUERY_MEMORY_TOP_N]
        ],
    }


def _set_reminder(text: str, time: str) -> dict:
    """STUB: no real persistence/scheduling yet — just proves the tier gates correctly."""
    print(f"Reminder set: '{text}' at {time}")
    return {"status": "confirmed", "skill": "set_reminder", "text": text, "time": time}


def _make_purchase(item: str, price: str) -> dict:
    """STUB: no real purchasing integration yet — just proves the tier gates correctly."""
    print(f"Purchase would execute: {item} for {price}")
    return {"status": "confirmed", "skill": "make_purchase", "item": item, "price": price}


register(Skill(
    name="query_memory",
    tier="read_only",
    description="Look up the most relevant stored memory for a question",
    func=_query_memory,
    parameters={
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
    speaker_scoped=True,
))
register(Skill(
    name="set_reminder",
    tier="reversible_write",
    description="Set a reminder",
    func=_set_reminder,
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "time": {"type": "string"},
        },
        "required": ["text", "time"],
    },
))
register(Skill(
    name="make_purchase",
    tier="high_stakes",
    description="Make a purchase",
    func=_make_purchase,
    parameters={
        "type": "object",
        "properties": {
            "item": {"type": "string"},
            "price": {"type": "string"},
        },
        "required": ["item", "price"],
    },
))


def to_openai_tools() -> list[dict]:
    """Builds the OpenAI function-calling "tools" list from the registry automatically."""
    return [
        {
            "type": "function",
            "function": {
                "name": skill.name,
                "description": skill.description,
                "parameters": skill.parameters,
            },
        }
        for skill in REGISTRY.values()
    ]


def _describe(skill: Skill, kwargs: dict) -> str:
    args_str = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
    return f"{skill.description}: {args_str}"


def _get_skill(name: str) -> Skill:
    skill = REGISTRY.get(name)
    if skill is None:
        raise ValueError(f"Unknown skill: '{name}'. Registered skills: {sorted(REGISTRY)}")
    return skill


def run_skill(name: str, speaker: str = None, /, **kwargs) -> dict:
    """
    read_only and reversible_write skills execute immediately. high_stakes
    skills never execute here — this returns a "needs_confirmation"
    description instead; only confirm_skill() actually runs them.

    `speaker` is the caller's trusted speaker (positional-only, so a
    model-supplied argument that happens to be called "speaker" or "name"
    lands in kwargs instead of colliding). Speaker-scoped skills receive it
    as a `speaker` keyword, replacing any "speaker" the model supplied.
    """
    skill = _get_skill(name)

    if skill.speaker_scoped:
        kwargs = {**kwargs, "speaker": speaker}

    if skill.tier == "read_only":
        return skill.func(**kwargs)

    if skill.tier == "reversible_write":
        result = skill.func(**kwargs)
        result["note"] = "Reversible action - executed without confirmation by design."
        return result

    # high_stakes
    return {
        "status": "needs_confirmation",
        "skill": name,
        "args": kwargs,
        "description": _describe(skill, kwargs),
    }


def confirm_skill(name: str, **kwargs) -> dict:
    """Actually executes a high_stakes skill. Only ever call this after an explicit human 'yes'."""
    skill = _get_skill(name)
    if skill.tier != "high_stakes":
        raise ValueError(f"confirm_skill() is only for high_stakes skills; '{name}' is tier '{skill.tier}'.")
    return skill.func(**kwargs)
