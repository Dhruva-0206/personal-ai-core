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

- Phase 2 (partial): added retrieval.py — vector similarity search over
  Episode nodes (full per-speaker scan, no Neo4j vector index yet) combined
  with a 3D scoring formula (similarity/importance/recency) and a landmark
  bypass (importance >= IMPORTANCE_LANDMARK_FLOOR forces recency to 1.0).
  Added supersession-aware ranking: OUTDATED_STATE_PENALTY=0.5 (guessed,
  not calibrated) is applied to combined_score when every State an episode
  wrote is now superseded, so an outdated fact can't outrank the current
  one purely on text similarity — episodes with zero states or at least
  one active state are never penalized. Added cli.py search command,
  showing state_status (current/superseded/no_state) per result. Validated
  against live Nebius + Neo4j: New York now correctly outranks San
  Francisco for "where do I currently live" (0.684 vs 0.318) after
  previously losing on a 0.004 margin.
  Known unresolved calibration questions, deferred pending real usage
  data: (1) WEIGHT_SIMILARITY/IMPORTANCE/RECENCY (0.55/0.35/0.10) allow
  high-importance-but-irrelevant episodes to outrank strong keyword
  matches — observed with a "coffee" query losing to unrelated high-
  importance episodes; (2) OUTDATED_STATE_PENALTY=0.5 is an unvalidated
  guess; (3) recency provides no differentiation when episodes are close
  in time (expected behavior of the half-life formula, not a bug, but
  worth remembering when testing with freshly-logged data).

- Phase 2 complete: added the two remaining retrieval lanes and merged all
  three into one ranked result. fulltext_lane() queries the existing
  episode_raw_text Neo4j fulltext index, min-max normalizing raw Lucene
  scores into [FULLTEXT_SCORE_MIN, FULLTEXT_SCORE_MAX]=[0.40, 0.75].
  recent_lane() is a safety net: the RECENT_LANE_MAX=2 most-recently-logged
  episodes within RECENT_WINDOW_MINUTES=5 always get a fixed
  RECENT_LANE_SCORE=0.25, so something-you-just-said stays retrievable
  even with weak similarity/fulltext scores. retrieve() now runs all three
  lanes unconditionally and merges by episode_id, keeping each candidate's
  highest raw lane score and recording every contributing lane in a
  "lanes" list.
  Fixed a gap found during this work: the supersession penalty
  (OUTDATED_STATE_PENALTY) previously only applied inside vector_search(),
  so an episode surfaced only via fulltext or recent would dodge it
  entirely. Extracted the state-status check into a shared
  _episode_state_status() and moved the penalty to apply once per merged
  candidate regardless of which lane(s) found it. This required recovering
  vector_search's pre-penalty score before merging (dividing back out
  OUTDATED_STATE_PENALTY) to avoid double-penalizing episodes that also
  came through the vector lane — vector_search's own standalone behavior
  is unchanged.
  Attempted and reverted: a VECTOR_MIN_SIMILARITY=0.50 floor to exclude
  weak vector-lane matches from counting as "this lane contributed" (was
  making the "lanes" field misleading — e.g. a fully unrelated query still
  showing "vector" due to embedding-space noise-floor similarity of
  ~0.43-0.47). Testing showed genuine-match similarity (~0.46, e.g. New
  York for "where do I currently live") and noise-floor similarity for
  totally unrelated queries occupy the *same* range at this corpus size
  (~4 test episodes) — excluding one reliably excluded the other too. The
  floor shrank the New York vs San Francisco currency margin from a
  decisive 0.239-0.366 down to a fragile 0.03, so it was reverted rather
  than kept as a false sense of precision. The "lanes" field's meaning was
  relabeled instead: it means "which lane(s) returned this candidate," not
  "which lane(s) found it relevant" — read it alongside
  similarity/importance/recency, not alone.
  Known unresolved calibration questions, deferred pending real usage
  data: (1) WEIGHT_SIMILARITY/IMPORTANCE/RECENCY (0.55/0.35/0.10) allow
  high-importance-but-irrelevant episodes to outrank strong keyword
  matches — observed with a "coffee" query losing to unrelated high-
  importance episodes; (2) OUTDATED_STATE_PENALTY=0.5 is an unvalidated
  guess; (3) recency provides no differentiation when episodes are close
  in time (expected behavior of the half-life formula, not a bug, but
  worth remembering when testing with freshly-logged data); (4)
  VECTOR_MIN_SIMILARITY floor — attempted and reverted, revisit at larger
  corpus size once embeddings have more natural separation to calibrate
  against; (5) FULLTEXT_SCORE_MIN/MAX and RECENT_LANE_SCORE are all
  guessed, none calibrated against real usage yet.

## Next up

Phase 3: agent & skills layer (tool-calling wired to memory + retrieval,
tiered skill registry).
