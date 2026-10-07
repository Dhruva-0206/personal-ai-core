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

# Small, deliberately non-exhaustive set of low-level phrase fragments
# seen in genuine refusals so far. This is a heuristic net, not a
# generalized refusal classifier — three independent real occurrences
# have now used three different exact phrasings (see CLAUDE.md), so this
# list WILL miss a sufficiently novel refusal. It exists to catch more
# than one literal string, not to catch all of them.
_REFUSAL_FRAGMENTS = (
    "don't have access",
    "can't help with that",
    "unable to",
    "no way to determine",
    "not able to",
)


def _has_structural_malformation(content: str) -> bool:
    """
    True if the content looks like raw structured data leaked into what
    should be natural language, rather than one specific known-bad
    string. Catches the <tool_call> tag variant AND the raw-JSON-prose
    variant we've now seen (both share the shape "looks like a function
    call written as text") without hardcoding either exact string, plus
    XML/tag-like leaks in general (not just this one tag name).
    """
    has_json_braces = "{" in content and "}" in content
    has_call_shaped_key = (
        '"name"' in content or '"function"' in content or '"arguments"' in content
    )
    looks_like_a_tag = content.lstrip().startswith("<")
    return (has_json_braces and has_call_shaped_key) or looks_like_a_tag


def _has_refusal_heuristic_match(content: str) -> bool:
    """True if content contains any of the small refusal fragment list, case-insensitive."""
    content_lower = content.lower()
    return any(fragment in content_lower for fragment in _REFUSAL_FRAGMENTS)


def _categorize_failure(content: str) -> str:
    """Logging-only label for which check tripped is_malformed_or_refused()."""
    if _has_structural_malformation(content):
        return "structural"
    if _has_refusal_heuristic_match(content):
        return "refusal_heuristic"
    return "unknown"


def is_malformed_or_refused(content: str) -> bool:
    """
    True if content is structurally malformed (structured data leaked
    into natural language) or matches the small refusal-heuristic
    fragment list. Structural signals generalize beyond the exact strings
    seen so far; the refusal check does not fully generalize — see
    _REFUSAL_FRAGMENTS.

    Takes only content, not finish_reason: this function has no opinion
    on whether a tool call was expected. The caller (handle_request) is
    responsible for only calling this when finish_reason != "tool_calls"
    on a request that had tools available — never call this on a
    successful tool-call response, and never call it to second-guess a
    request that never offered any tool in the first place (a plain
    conversational answer like "hello, how are you" must never be flagged
    just because it's plain text with no tool call).
    """
    if not content:
        return False
    return _has_structural_malformation(content) or _has_refusal_heuristic_match(content)


class _ControlledError(Exception):
    """A failure with a message safe to show the user (not a bug in this code)."""


def _chat(client, **kwargs):
    """One model call. Any failure of the call itself becomes a controlled error."""
    try:
        return client.chat.completions.create(**kwargs)
    except Exception as e:
        logger.exception("Model request failed")
        raise _ControlledError(f"the language model request failed ({type(e).__name__}).") from e


def _first_message(response):
    """(message, finish_reason) of the first choice, or a controlled error for an empty/odd response."""
    choices = getattr(response, "choices", None)
    if not choices:
        raise _ControlledError("the language model returned an empty response.")
    return choices[0].message, choices[0].finish_reason


def _non_empty(content) -> str:
    if content is None or not str(content).strip():
        raise _ControlledError("the language model returned an empty answer.")
    return content


def handle_request(user_message: str, speaker: str = None) -> str:
    """
    Always returns a string. Failures of the model call, an empty or odd
    response shape, malformed/unknown/incomplete tool calls and skills that
    raise are turned into a short error message instead of an exception.
    These guards cover response-SHAPE failures that happen before (or
    around) the content-quality retry in _handle_request(), which stays
    exactly as it was.
    """
    try:
        return _handle_request(user_message, speaker)
    except _ControlledError as e:
        logger.warning("Agent request failed: %s", e)
        return f"Sorry, I couldn't complete that: {e}"
    except Exception as e:
        logger.exception("Unexpected failure in agent.handle_request")
        return f"Sorry, something went wrong while handling that request ({type(e).__name__}). Please try again."


def _handle_request(user_message: str, speaker: str = None) -> str:
    global pending_confirmation
    pending_confirmation = None

    client = llm_client.get_client()

    messages = [{"role": "user", "content": user_message}]
    response = _chat(
        client,
        model=config.EXTRACTION_MODEL,
        messages=messages,
        tools=skills.to_openai_tools(),
    )
    message, finish_reason = _first_message(response)

    # is_malformed_or_refused() takes only content — the finish_reason !=
    # "tool_calls" gate below is what makes this safe to call: a
    # successful tool call never reaches it, and a plain conversational
    # answer (finish_reason == "stop" with no tool offered as relevant)
    # only gets flagged if its content actually looks structurally
    # malformed or refusal-shaped, not just because it's plain text.
    if finish_reason != "tool_calls" and is_malformed_or_refused(message.content):
        logger.warning(
            "Agent tool-calling failure detected (finish_reason=%r, check=%s) — "
            "retrying once. Raw content: %r",
            finish_reason, _categorize_failure(message.content), message.content,
        )
        # Same request, retried exactly once. Whatever comes back is used
        # as final — no second retry, and the retry's own result is not
        # re-checked for triggering another retry (avoids a retry loop).
        response = _chat(
            client,
            model=config.EXTRACTION_MODEL,
            messages=messages,
            tools=skills.to_openai_tools(),
        )
        message, finish_reason = _first_message(response)
        if finish_reason != "tool_calls" and is_malformed_or_refused(message.content):
            logger.warning(
                "Agent tool-calling failure persisted after retry "
                "(finish_reason=%r, check=%s) — proceeding anyway, not retrying "
                "again. Raw content: %r",
                finish_reason, _categorize_failure(message.content), message.content,
            )

    if finish_reason != "tool_calls":
        return _non_empty(message.content)

    # Known limitation: only the first tool call is handled. A model
    # response with multiple tool_calls in one turn would silently drop
    # the rest — fine for this phase, revisit if/when that's observed.
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        raise _ControlledError("the model asked to use a tool but did not say which one.")
    tool_call = tool_calls[0]
    name = tool_call.function.name
    try:
        raw_args = json.loads(tool_call.function.arguments)
    except (TypeError, ValueError) as e:
        raise _ControlledError(f"the model sent unreadable arguments for '{name}'.") from e

    args, problem = skills.validate_args(name, raw_args)
    if problem:
        raise _ControlledError(f"the model's tool call was invalid: {problem}.")

    try:
        result = skills.run_skill(name, speaker, **args)
    except Exception as e:
        logger.exception("Skill %r raised", name)
        raise _ControlledError(f"the '{name}' skill failed ({type(e).__name__}).") from e

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

    follow_up_messages = messages + [tool_message, tool_result_message]
    follow_up = _chat(
        client,
        model=config.EXTRACTION_MODEL,
        messages=follow_up_messages,
    )
    content = _first_message(follow_up)[0].content

    # Same detection and single-retry policy as the tool-selection call
    # above, applied to the FOLLOW-UP call. WARNINGs say "follow-up call" so
    # the two failure sites stay distinguishable in logs.
    if is_malformed_or_refused(content):
        logger.warning(
            "Agent follow-up call failure detected (check=%s) — retrying "
            "the follow-up call once. Raw content: %r",
            _categorize_failure(content), content,
        )
        follow_up = _chat(
            client,
            model=config.EXTRACTION_MODEL,
            messages=follow_up_messages,
        )
        content = _first_message(follow_up)[0].content
        if is_malformed_or_refused(content):
            logger.warning(
                "Agent follow-up call failure persisted after retry "
                "(check=%s) — proceeding anyway, not retrying again. "
                "Raw content: %r",
                _categorize_failure(content), content,
            )
    return _non_empty(content)
