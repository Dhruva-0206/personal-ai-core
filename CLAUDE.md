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
- Phase 1 validated against live Nebius Token Factory (model:
  nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B) + Neo4j Aura — supersession
  round-trip confirmed working.
- Phase 1 complete: fixed self-reference entity fragility (extraction.py
  now deterministically normalizes I/me/my/mine/myself to "User" in code
  rather than relying on LLM prompt compliance) and added attribute-name
  resolution (graph_engine.resolve_attribute(), mirroring entity
  resolution but scoped per-entity, with ATTRIBUTE_SIMILARITY_THRESHOLD=0.72
  chosen from two live data points: city/location should-merge scored
  0.8034, city/job should-NOT-merge scored 0.6509). Added
  graph_engine.reset_speaker() + cli.py reset command (requires --confirm)
  for clearing test data safely. Full three-episode supersession round-trip
  (San Francisco -> New York, job -> therapist) re-validated end-to-end
  against live Nebius + Neo4j with a single canonical "User" entity and
  correct city attribute supersession.

## Next up

Phase 2: retrieval layer (multi-lane retrieval + scoring).
