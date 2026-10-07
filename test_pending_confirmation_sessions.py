"""
Isolated test (fake model, fake purchase skill, no Neo4j/Nebius) of per-client
scoping of pending high-stakes confirmations in the web app. Before the fix
agent.pending_confirmation was one module-level global, so (a) any client
could confirm another client's pending purchase and (b) any unrelated ask
silently cleared a pending confirmation.

Behavior under test (driven only through HTTP, like a real browser):
  - each client has its own pending action; others can't see or confirm it
  - an unrelated ask does NOT clear the client's own pending action; only an
    explicit confirm or cancel does (chosen as the safer default: silently
    dropping it could lose an approval the user still means to give)
  - a newer purchase request in the same session replaces the older pending
    one, so only the latest action can ever be confirmed
  - a forged session cookie gets a fresh, empty session

Run with: python test_pending_confirmation_sessions.py
"""
import logging
import re
import sys
from types import SimpleNamespace

import llm_client
import skills
import web_app

logging.disable(logging.CRITICAL)

passed = 0
failed = 0


def safe_print(*args):
    text = " ".join(str(a) for a in args)
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.buffer.write(text.encode(encoding, errors="replace"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        safe_print(f"  [PASS] {label}")
    else:
        failed += 1
        safe_print(f"  [FAIL] {label}")
        if detail:
            safe_print(f"         {detail}")


# ---- fakes ------------------------------------------------------------------

executions = []  # (item, price) for every time the purchase skill really ran


def _fake_purchase(item, price):
    executions.append((item, price))
    return {"status": "confirmed", "skill": "make_purchase", "item": item, "price": price}


class _Router:
    """Fake model: 'buy a <item>' -> make_purchase tool call; anything else -> a plain answer."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        text = kwargs["messages"][-1]["content"]
        match = re.match(r"buy a (\w+)", text)
        if match:
            call = SimpleNamespace(id="c1", type="function", function=SimpleNamespace(
                name="make_purchase", arguments='{"item": "%s", "price": "$5"}' % match.group(1)))
            message = SimpleNamespace(content=None, tool_calls=[call])
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=message)])
        message = SimpleNamespace(content="Hello! How can I help?", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=message)])


def ask(client, question):
    return client.post("/ask", data={"question": question}).get_data(as_text=True)


def confirm(client, action):
    return client.post("/confirm", data={"action": action}).get_data(as_text=True)


def home(client):
    return client.get("/").get_data(as_text=True)


def shows_confirm_box(body, item=None):
    return 'class="confirm-box"' in body and (item is None or f"item=&#39;{item}&#39;" in body or f"item='{item}'" in body)


def fresh_client():
    return web_app.app.test_client()


def main():
    web_app._recent_episodes = lambda: []  # the page would otherwise query Neo4j
    real_func, real_get_client = skills.REGISTRY["make_purchase"].func, llm_client.get_client
    skills.REGISTRY["make_purchase"].func = _fake_purchase
    llm_client.get_client = lambda: _Router()
    try:
        safe_print("Isolation between clients")
        a, b = fresh_client(), fresh_client()
        body = ask(a, "buy a lamp")
        check("client A sees its pending purchase", shows_confirm_box(body, "lamp"))
        check("client B does NOT see A's pending purchase", not shows_confirm_box(home(b)))
        body = confirm(b, "yes")
        check("client B's confirm says 'Nothing pending'", "Nothing pending" in body, "body had no 'Nothing pending'")
        check("client B's confirm executed NOTHING", executions == [], f"executions: {executions}")
        check("A's pending purchase is still there after B's attempt", shows_confirm_box(home(a), "lamp"))

        forged = fresh_client()
        forged.set_cookie("session", "forged-value")
        body = confirm(forged, "yes")
        check("a client with a forged session cookie gets 'Nothing pending' and executes nothing",
              "Nothing pending" in body and executions == [], f"executions: {executions}")

        safe_print("\nUnrelated asks do not clear a pending action")
        body = ask(a, "hello")
        check("A asks something unrelated: answer is returned", "Hello! How can I help?" in body)
        check("A's pending purchase survives the unrelated ask", shows_confirm_box(body, "lamp"))

        safe_print("\nExplicit confirm / cancel")
        body = confirm(a, "yes")
        check("A confirms: the purchase runs exactly once, with A's own item",
              executions == [("lamp", "$5")], f"executions: {executions}")
        check("A's pending action is cleared after confirming", not shows_confirm_box(home(a)))
        body = confirm(a, "yes")
        check("A confirming again says 'Nothing pending' and does not re-run it",
              "Nothing pending" in body and len(executions) == 1, f"executions: {executions}")

        executions.clear()
        ask(a, "buy a desk")
        body = confirm(a, "no")
        check("A cancels: nothing executes and the pending action is cleared",
              "Cancelled" in body and executions == [] and not shows_confirm_box(home(a)), f"executions: {executions}")

        safe_print("\nTwo clients pending at once")
        executions.clear()
        ask(a, "buy a lamp")
        ask(b, "buy a desk")
        confirm(a, "yes")
        check("A confirming runs only A's purchase", executions == [("lamp", "$5")], f"executions: {executions}")
        check("B's pending purchase is untouched by A's confirm", shows_confirm_box(home(b), "desk"))
        confirm(b, "yes")
        check("B then confirms its own", executions == [("lamp", "$5"), ("desk", "$5")], f"executions: {executions}")

        safe_print("\nA newer request replaces the older pending one")
        executions.clear()
        ask(a, "buy a lamp")
        ask(a, "buy a chair")
        confirm(a, "yes")
        check("only the latest requested action runs, once", executions == [("chair", "$5")], f"executions: {executions}")
    finally:
        skills.REGISTRY["make_purchase"].func = real_func
        llm_client.get_client = real_get_client


if __name__ == "__main__":
    main()
    safe_print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
