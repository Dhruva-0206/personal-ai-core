"""
Validates web_app.py in-process via Flask's test_client() — no server, no
ports, no background processes. Hits the real live Nebius/Neo4j infra
through the unmodified pipeline/retrieval/agent/skills code.

Run with: python test_web_app.py
"""
import sys

import web_app

client = web_app.app.test_client()


def print(*args, **kwargs):
    """Console-safe print — this script's own output can contain
    LLM-generated text (emoji etc.) that a Windows cp1252 console can't
    encode; degrade gracefully instead of crashing the test run."""
    text = " ".join(str(a) for a in args)
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.buffer.write(text.encode(encoding, errors="replace"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def step(n, description):
    print(f"\n{'=' * 20} STEP {n}: {description} {'=' * 20}")


step(1, 'POST /log text="Testing the new interface for the first time"')
resp = client.post("/log", data={"text": "Testing the new interface for the first time"})
body = resp.get_data(as_text=True)
print("status_code:", resp.status_code)
print("contains 'Stored:':", "Stored:" in body)
print("contains 'interface':", "interface" in body.lower())
# Print the message block for manual inspection
start = body.find('<div class="message')
end = body.find("</div>", start)
print("message block:", body[start:end + 6] if start != -1 else "(not found)")

step(2, "GET / — confirm new entry appears in recent memories")
resp = client.get("/")
body = resp.get_data(as_text=True)
print("status_code:", resp.status_code)
print("contains 'interface' in recent memories:", "interface" in body.lower())

step(3, 'POST /ask question="where do I live"')
resp = client.post("/ask", data={"question": "where do I live"})
body = resp.get_data(as_text=True)
print("status_code:", resp.status_code)
start = body.find('<div class="message')
end = body.find("</div>", start)
message_block = body[start:end + 6] if start != -1 else "(not found)"
print("message block:", message_block)
# Check specifically within the answer's own message block, not the whole
# page — the recent-memories list below it also contains past episode
# summaries mentioning "New York", which would make a whole-page substring
# check pass even if the actual answer got it wrong.
print("answer itself contains 'New York':", "New York" in message_block)

step(4, 'POST /ask question="buy me a coffee maker for $40"')
resp = client.post("/ask", data={"question": "buy me a coffee maker for $40"})
body = resp.get_data(as_text=True)
print("status_code:", resp.status_code)
print("contains confirm-box:", 'class="confirm-box"' in body)
print("contains 'Confirm' button:", ">Confirm<" in body)
print("contains 'Cancel' button:", ">Cancel<" in body)
print("contains 'Purchase would execute' (should be False, not yet executed):",
      "Purchase would execute" in body)
start = body.find('<div class="confirm-box">')
end = body.find("</div>", start)
print("confirm-box block:", body[start:end + 6] if start != -1 else "(not found)")
print("agent.pending_confirmation:", web_app.agent.pending_confirmation)

step(5, 'POST /confirm action="yes"')
resp = client.post("/confirm", data={"action": "yes"})
body = resp.get_data(as_text=True)
print("status_code:", resp.status_code)
print("contains 'Purchase would execute':", "Purchase would execute" in body)
print("contains 'Confirmed:':", "Confirmed:" in body)
start = body.find('<div class="message')
end = body.find("</div>", start)
print("message block:", body[start:end + 6] if start != -1 else "(not found)")
print("agent.pending_confirmation after confirm (should be None):", web_app.agent.pending_confirmation)
