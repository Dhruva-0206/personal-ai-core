# personal-ai-core

## Project

An always-on personal AI assistant with a temporal knowledge graph memory,
built for the Nebius x NVIDIA Global AI Hackathon, Personal AI track. The
system ingests a user's ongoing interactions, extracts entities and
relationships over time, and maintains a graph-based memory that supports
retrieval-augmented reasoning about the user's evolving context.

## Constraints

- Must run on Nebius Token Factory or Nebius AI Cloud.
- Must use at least one NVIDIA open-source model.

## Build log

- Phase 1 (foundations & core memory engine): extraction pipeline, Neo4j
  graph engine with 3-layer entity resolution and write-time state
  supersession, manual CLI test harness. Offline tests passing (7/7). Not
  yet validated against live Nebius/Neo4j credentials.

## Next up

Phase 2: retrieval layer (multi-lane retrieval + scoring).
