"""
Throwaway diagnostic: does nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B on Nebius
Token Factory actually return usable tool_calls, and how does that
interact with the reasoning_content/content split? Does NOT touch
skills.py, agent logic, or the real skill registry — just prints raw
responses so we can decide how to proceed.

Run with: python test_tool_calling.py
"""
import json

import config
import llm_client

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]

MESSAGES = [{"role": "user", "content": "What's the weather in Tokyo?"}]


def run_once(call_number: int):
    client = llm_client.get_client()
    response = client.chat.completions.create(
        model=config.EXTRACTION_MODEL,
        messages=MESSAGES,
        tools=TOOLS,
    )
    message = response.choices[0].message

    print(f"\n{'=' * 20} CALL {call_number} {'=' * 20}")

    print("\n--- content ---")
    print(repr(message.content))

    print("\n--- reasoning_content ---")
    if hasattr(message, "reasoning_content"):
        print(repr(message.reasoning_content))
    else:
        print("(attribute does not exist on this message object)")

    print("\n--- tool_calls ---")
    if message.tool_calls:
        for i, tc in enumerate(message.tool_calls):
            print(f"  tool_call[{i}]:")
            print(f"    id: {tc.id}")
            print(f"    type: {tc.type}")
            print(f"    function.name: {tc.function.name}")
            print(f"    function.arguments (raw string): {tc.function.arguments!r}")
    else:
        print(repr(message.tool_calls))

    print("\n--- finish_reason ---")
    print(repr(response.choices[0].finish_reason))


if __name__ == "__main__":
    for n in range(1, 4):
        run_once(n)
