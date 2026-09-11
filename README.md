# Personal AI — Phase 1: Foundations & Core Memory Engine

This is the first build phase: extraction + graph memory + entity resolution
+ state supersession, tested with manual text input only. No integrations,
no retrieval layer, no agent yet — those come in later phases. The goal here
is a rock-solid memory engine everything else can build on.

## What's in here

- `config.py` — all settings, read from environment variables
- `llm_client.py` — talks to Nebius Token Factory (OpenAI-compatible endpoint); handles extraction JSON calls and embeddings
- `extraction.py` — the single prompt that turns raw text into entities/states/actions/relations/importance
- `graph_engine.py` — Neo4j writes: entity resolution (exact → substring → embedding similarity), episodes, states with supersession, actions, relations
- `pipeline.py` — wires extraction + graph writes together for one piece of raw text
- `cli.py` — manual test harness (this phase's only "ingestion adapter")
- `test_offline.py` — tests for pure logic that need no live credentials — **run this first**

## Setup

1. **Get a Nebius Token Factory API key**: sign up at tokenfactory.nebius.com, create a key.
2. **Get a Neo4j instance**: easiest is a free Neo4j AuraDB instance (neo4j.com/cloud/aura) — takes about a minute, gives you a URI, username, and password.
3. Copy the environment template and fill in your real values:
   ```bash
   cp .env.example .env
   ```
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Run it

Before touching real infra, confirm the pure logic is sound:
```bash
python test_offline.py
```
All 7 should pass — they test cosine similarity math, JSON-fence stripping, and the extraction fallback shape, none of which need a network call.

Once your `.env` is filled in:
```bash
python cli.py setup                        # creates Neo4j constraints/indexes, run once
python cli.py log "I live in San Francisco"
python cli.py log "I moved to New York last week"
python cli.py history "you"                 # or whatever entity name got extracted — check supersession worked
```

If entity resolution named you something unexpected on the first line, run
`python cli.py history "<that name>"` — you should see `city = San Francisco`
marked superseded and `city = New York` marked active, both with timestamps.
That round-trip — contradiction correctly resolved, old fact preserved not
deleted — is the entire point of Phase 1. If that works, the foundation is solid.

For a longer back-and-forth, use the interactive mode:
```bash
python cli.py chat
```

## Known limitation to fix before Phase 5 (scale-dependent, not urgent now)

`resolve_entity()`'s embedding-similarity layer scans every entity for a
speaker in Python. Fine at hackathon scale (dozens to low hundreds of
entities per person). Once that grows large, swap it for Neo4j's native
vector index — the function's calling shape won't need to change, only its
internals.

## What's deliberately NOT here yet

- Retrieval (Phase 2) — right now the only way to check what's in the graph is `cli.py history`, which is a raw per-entity dump, not real retrieval.
- Agent/tool-calling (Phase 3) — no task execution yet, just memory writes.
- Any ingestion source besides manual CLI input — calendar, location, audio, vision, wearables all come later, and will all call `pipeline.ingest_episode()` with normalized text exactly like `cli.py` does.
