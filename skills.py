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


REGISTRY: dict[str, Skill] = {}


def register(skill: Skill):
    if skill.tier not in TIERS:
        raise ValueError(f"Unknown tier '{skill.tier}' for skill '{skill.name}'")
    REGISTRY[skill.name] = skill


def _query_memory(question: str) -> dict:
    """REAL skill: returns the top retrieval.retrieve() result for a question."""
    results = retrieval.retrieve(question)
    if not results:
        return {"question": question, "summary": None, "message": "No results found."}
    top = results[0]
    return {
        "question": question,
        "summary": top["summary"],
        "combined_score": top["combined_score"],
        "similarity": top["similarity"],
        "importance": top["importance"],
        "recency": top["recency"],
        "state_status": top["state_status"],
        "lanes": top["lanes"],
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
))
register(Skill(
    name="set_reminder",
    tier="reversible_write",
    description="Set a reminder",
    func=_set_reminder,
))
register(Skill(
    name="make_purchase",
    tier="high_stakes",
    description="Make a purchase",
    func=_make_purchase,
))


def _describe(skill: Skill, kwargs: dict) -> str:
    args_str = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
    return f"{skill.description}: {args_str}"


def _get_skill(name: str) -> Skill:
    skill = REGISTRY.get(name)
    if skill is None:
        raise ValueError(f"Unknown skill: '{name}'. Registered skills: {sorted(REGISTRY)}")
    return skill


def run_skill(name: str, **kwargs) -> dict:
    """
    read_only and reversible_write skills execute immediately. high_stakes
    skills never execute here — this returns a "needs_confirmation"
    description instead; only confirm_skill() actually runs them.
    """
    skill = _get_skill(name)

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
