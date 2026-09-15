"""
Minimal local web interface wrapping the existing pipeline/retrieval/agent
code unchanged. Plain server-rendered Jinja templates, no JavaScript, no
external CDN dependencies — fully offline-reliable.

Run with: python web_app.py
Then open: http://127.0.0.1:5000/
"""
from flask import Flask, render_template, request

import agent
import config
import graph_engine
import pipeline
import skills

app = Flask(__name__)


def _recent_episodes():
    driver = graph_engine.get_driver()
    with driver.session() as session:
        return graph_engine.get_recent_episodes(session, config.DEFAULT_SPEAKER, limit=10)


def _render(message=None):
    return render_template(
        "index.html",
        message=message,
        pending=agent.pending_confirmation,
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

    answer = agent.handle_request(question)
    return _render(message={"text": answer, "is_error": False})


@app.route("/confirm", methods=["POST"])
def confirm():
    action = request.form.get("action")
    pending = agent.pending_confirmation
    if pending is None:
        return _render(message={"text": "Nothing pending to confirm.", "is_error": True})

    if action == "yes":
        result = skills.confirm_skill(pending["name"], **pending["args"])
        message_text = f"Confirmed: {result}"
    else:
        message_text = "Cancelled."

    agent.pending_confirmation = None
    return _render(message={"text": message_text, "is_error": False})


if __name__ == "__main__":
    app.run(debug=False, port=5000)
