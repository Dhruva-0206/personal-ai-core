"""
Minimal local web interface wrapping the existing pipeline/retrieval/agent
code unchanged. Plain server-rendered Jinja templates, no JavaScript, no
external CDN dependencies — fully offline-reliable.

Run with: python web_app.py
Then open: http://127.0.0.1:5000/
"""
import secrets
import uuid

from flask import Flask, render_template, request, session

import agent
import config
import graph_engine
import pipeline
import skills

app = Flask(__name__)
# Signs the session cookie that identifies a client, so pending high-stakes
# confirmations are per client and a client can't forge another's id. A random
# per-process key is enough for this local app: restarting the server just
# starts everyone on fresh sessions (and pending actions don't survive a
# restart anyway).
app.secret_key = secrets.token_hex(32)


def _session_id() -> str:
    """This client's id, created on first use and kept in the signed session cookie."""
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    return session["sid"]


def _recent_episodes():
    driver = graph_engine.get_driver()
    with driver.session() as session:
        return graph_engine.get_recent_episodes(session, config.DEFAULT_SPEAKER, limit=10)


def _render(message=None):
    return render_template(
        "index.html",
        message=message,
        pending=agent.get_pending(_session_id()),
        recent_episodes=_recent_episodes(),
    )


@app.route("/", methods=["GET"])
def index():
    return _render()


@app.route("/log", methods=["POST"])
def log():
    text = request.form.get("text", "").strip()
    if not text:
        return _render(message={"text": "Nothing to log — the text field was empty.", "is_error": True})

    result = pipeline.ingest_episode(text, speaker=config.DEFAULT_SPEAKER)
    message_text = (
        f"Stored: {result['summary']} "
        f"(importance: {result['importance']}, entities: {result['entities']})"
    )
    return _render(message={"text": message_text, "is_error": False})


@app.route("/ask", methods=["POST"])
def ask():
    question = request.form.get("question", "").strip()
    if not question:
        return _render(message={"text": "Nothing to ask — the question field was empty.", "is_error": True})

    answer = agent.handle_request(question, session_id=_session_id())
    return _render(message={"text": answer, "is_error": False})


@app.route("/confirm", methods=["POST"])
def confirm():
    action = request.form.get("action")
    session_id = _session_id()
    pending = agent.get_pending(session_id)
    if pending is None:
        return _render(message={"text": "Nothing pending to confirm.", "is_error": True})

    if action == "yes":
        result = skills.confirm_skill(pending["name"], **pending["args"])
        message_text = f"Confirmed: {result}"
    else:
        message_text = "Cancelled."

    agent.clear_pending(session_id)
    return _render(message={"text": message_text, "is_error": False})


if __name__ == "__main__":
    app.run(debug=False, port=5000)
