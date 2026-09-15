"""
The agent loop: lets Nemotron pick a skill from free-form natural language
and invoke it, instead of a human typing `python cli.py skill <name> ...`
directly. The tiered confirmation gate from skills.py is NOT bypassed here
— a high_stakes tool call still stops and hands control back to the human,
exactly as it does on the direct CLI path.
"""
import json
import logging

import config
import llm_client
import skills

logger = logging.getLogger(__name__)

# Set by handle_request() only when it returns a needs_confirmation
# description, holding what's needed to actually run the skill afterward
# (skills.confirm_skill(pending["name"], **pending["args"])). cli.py reads
# this right after calling handle_request() so it can reuse the same
# confirm-and-run code path the direct `skill` command uses, instead of
# trying to re-parse the returned description string. None otherwise.
pending_confirmation: dict | None = None

# Narrow, literal match for one observed refusal phrasing (see
# test_agent_reliability.py). This is likely to miss rephrased refusals —
# that's an accepted limitation for now, not something to generalize with
# fuzzy matching or another LLM call. It's a detection signal for logging
# and a single retry, not an attempt to fix the model's underlying
# behavior.
_REFUSAL_PHRASE = "I can't help with that question using the available tools"


def _is_known_tool_calling_failure(finish_reason, content: str | None) -> bool:
    """
    True for either of the two observed non-tool-calling failure modes:
    a hallucinated fake tool-call written as plain text (contains a
    literal "<tool_call>"), or an explicit refusal to use any tool
    (matches _REFUSAL_PHRASE). Only ever meaningful when finish_reason is
    already not "tool_calls" — a normal successful tool call always
    short-circuits here and is completely unaffected.
    """
    if finish_reason == "tool_calls" or not content:
        return False
    return "<tool_call>" in content or _REFUSAL_PHRASE in content


def handle_request(user_message: str, speaker: str = None) -> str:
    global pending_confirmation
    pending_confirmation = None

    client = llm_client.get_client()

    messages = [{"role": "user", "content": user_message}]
    response = client.chat.completions.create(
        model=config.EXTRACTION_MODEL,
        messages=messages,
        tools=skills.to_openai_tools(),
    )
    message = response.choices[0].message
    finish_reason = response.choices[0].finish_reason

    if _is_known_tool_calling_failure(finish_reason, message.content):
        logger.warning(
            "Agent tool-calling failure detected (finish_reason=%r) — "
            "retrying once. Raw content: %r",
            finish_reason, message.content,
        )
        # Same request, retried exactly once. Whatever comes back is used
        # as final — no second retry, and the retry's own result is not
        # re-checked for triggering another retry (avoids a retry loop).
        response = client.chat.completions.create(
            model=config.EXTRACTION_MODEL,
            messages=messages,
            tools=skills.to_openai_tools(),
        )
        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason
        if _is_known_tool_calling_failure(finish_reason, message.content):
            logger.warning(
                "Agent tool-calling failure persisted after retry "
                "(finish_reason=%r) — proceeding anyway, not retrying "
                "again. Raw content: %r",
                finish_reason, message.content,
            )

    if finish_reason != "tool_calls":
        return message.content

    # Known limitation: only the first tool call is handled. A model
    # response with multiple tool_calls in one turn would silently drop
    # the rest — fine for this phase, revisit if/when that's observed.
    tool_call = message.tool_calls[0]
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)

    result = skills.run_skill(name, **args)

    if result.get("status") == "needs_confirmation":
        # A high_stakes skill must never auto-execute just because an LLM
        # chose to call it instead of a human typing the command — hand
        # control back exactly like the CLI's direct `skill` path does.
        # The actual "type yes" prompt is the CLI's job (same code path as
        # the direct `skill` command), not baked into this returned string.
        pending_confirmation = {
            "name": name,
            "args": args,
            "description": result["description"],
        }
        return result["description"]

    tool_message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
        ],
    }
    tool_result_message = {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": json.dumps(result),
    }

    follow_up = client.chat.completions.create(
        model=config.EXTRACTION_MODEL,
        messages=messages + [tool_message, tool_result_message],
    )
    return follow_up.choices[0].message.content
