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

- Phase 3 foundation: added skills.py — a tiered skill registry
  (read_only / reversible_write / high_stakes) with a confirmation gate
  for high-stakes actions. Three skills registered: query_memory
  (read_only, REAL — wired to retrieval.retrieve()), set_reminder
  (reversible_write, STUB — prints + returns a confirmation dict, no real
  persistence/scheduling yet), make_purchase (high_stakes, STUB — same
  pattern, no real purchasing integration yet). run_skill() executes
  read_only and reversible_write immediately; for high_stakes it returns a
  "needs_confirmation" dict with a human-readable description instead of
  executing, and only an explicit confirm_skill() call (gated on the CLI
  by an exact-match "yes" prompt) actually runs it. Added cli.py skill
  command wiring this up end to end.
  Validated against live Nebius + Neo4j: read-only executes immediately
  with no prompt; reversible-write executes immediately and includes a
  "Reversible action - executed without confirmation by design" note;
  high-stakes blocks correctly on anything but exact "yes" (answering "no"
  produced "Cancelled." with no side effect; answering "yes" on a second
  run correctly executed and printed "Purchase would execute...").
  query_memory inherits the known importance-weighting issue from Phase 2
  retrieval — surfaced a high-importance-but-irrelevant episode (job
  change, importance 0.8) instead of the actually-relevant one (New York,
  the current city) when asked "where do I live" during validation. Not
  fixed, same reasoning as before (needs real usage data, not more
  synthetic tuning).
  This is the skill-execution mechanism only, NOT an agent — skills are
  currently invoked by name via CLI flag, not chosen autonomously by the
  LLM from natural language. That's the next piece of work.

- Phase 3: added agent.py — the actual agent loop. handle_request() sends
  the user's free-form message to Nemotron with tools=skills.to_openai_tools()
  (added to skills.py, generating the OpenAI function-calling tool schema
  from the registry automatically so it can't drift out of sync). If the
  model answers directly (finish_reason != "tool_calls"), that answer is
  returned as-is. If it calls a tool, only the first tool_call in that
  turn is handled (known limitation — no parallel/sequential multi-tool
  yet); its result goes through skills.run_skill() exactly like the direct
  CLI path. A high_stakes result triggers a second model call with the
  tool result appended (standard OpenAI tool-role message) to produce the
  final natural-language answer.
  Corrected an earlier assumption from initial planning: isolated testing
  (test_tool_calling.py, 3 separate calls) verified that
  nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B does NOT expose reasoning_content
  at all when using native tool-calling — the attribute doesn't exist on
  the message object in that mode, and content=None there just means the
  model chose to call a tool, not the reasoning-mode issue originally
  flagged. llm_client.py's _message_text() comment corrected accordingly;
  its defensive reasoning_content fallback logic is unchanged (kept as
  cheap insurance for untested model variants/call shapes, not because
  reasoning_content was confirmed to occur).
  Validated against live Nebius + Neo4j, three scenarios: (1) confirmation
  gate holds even when the agent — not a human — initiates a high-stakes
  action (make_purchase correctly stopped and asked for exact "yes" before
  executing); (2) the model does not force an unnecessary tool call for
  plain conversation ("hello, how are you" got a direct answer, no tool
  call); (3) when handed a wrong/irrelevant memory result, the model
  reported that honestly instead of hallucinating a plausible-sounding
  answer.
  Known limitations: only one tool call handled per turn; the pending
  high-stakes confirmation state (agent.pending_confirmation) is a single
  module-level global, not scoped per-session — fine for a single-user
  CLI, worth revisiting once "always-on"/multi-user is actually built.
  query_memory has now failed the same way three separate times across
  three different validation contexts (Phase 2 scoring test, Phase 3
  foundation validation, Phase 3 agent-loop validation) — high-importance-
  but-irrelevant episodes consistently outrank genuinely relevant ones.
  This is no longer "wait for more data" territory; it's a reproducible
  structural issue in the scoring weights. Next up: fix this before
  building further on top of retrieval/agent.

- Found via direct_state_lookup validation: an existing job=therapist
  State is attached to an entity literally named 'therapist' instead of
  'User' — the extraction step occasionally misattributes which entity a
  state belongs to when multiple entities appear in one episode (here:
  'User' and 'therapist' both extracted from the same sentence, state
  attached to the wrong one). This is a different fragility class than the
  self-reference or attribute-naming issues already fixed — those were
  about naming consistency for the SAME entity; this is about the model
  choosing the WRONG entity as the subject of a state. Not fixed yet —
  needs its own dedicated design pass (a prompt nudge alone, per the
  self-reference/attribute-naming precedent, likely isn't sufficient on
  its own here since there's no fixed vocabulary to normalize against).
  Deferred, not blocking the direct_state_lookup fix since it doesn't
  affect the city/location case.

- Phase 3 complete: fixed the recurring importance-weighting failure for
  direct factual questions with a new retrieval.py path,
  direct_state_lookup(), that runs before fuzzy retrieval and answers from
  the graph's known current state instead of ranking episodes.
  Diagnostic finding that drove this: raw vector similarity does not
  distinguish relevant-but-differently-phrased facts from irrelevant
  ones. Measured directly — for "where do I currently live", the
  superseded "I live in San Francisco" episode scored 0.624 similarity
  while the actually-correct "I moved to New York last week" episode
  scored 0.457, a mere 0.0007 away from a completely unrelated episode
  ("My job changed to therapist," 0.4565) — a 233x smaller gap than the
  0.167 separating San Francisco from New York. Similarity was tracking
  surface lexical overlap ("I live in..." vs "where do I live"), not
  semantic correctness.
  Tried and abandoned: reusing embedding cosine similarity (matching
  graph_engine.resolve_attribute()'s pattern) to map a full question onto
  a known attribute name. Tested directly: "where do I currently live" vs
  "city" scored 0.577, below the 0.72 ATTRIBUTE_SIMILARITY_THRESHOLD, so
  it would have missed the exact case it was built for, while "what
  happened with my job" vs "job" happened to clear the threshold at 0.80
  — a threshold that passes one phrasing and fails another isn't a real
  fix, and a full-sentence-vs-single-word embedding comparison isn't the
  same kind of comparison ATTRIBUTE_SIMILARITY_THRESHOLD was calibrated
  for. Same lesson as the earlier VECTOR_MIN_SIMILARITY floor experiment:
  signal and noise overlap too much at this scale for a bare similarity
  cutoff to separate them reliably.
  Chosen instead: LLM classification against the speaker's actual known
  attribute vocabulary (queried fresh from the graph each call), with the
  model's answer strictly validated to be one of those exact strings or
  null — never trusted if it names anything outside that list. Full-
  sentence understanding succeeds where geometry on short strings didn't,
  and the closed vocabulary makes hallucinated attribute names structurally
  impossible rather than just unlikely.
  direct_state_lookup() runs first but does NOT replace fuzzy retrieval —
  the three lanes (vector/fulltext/recent) still run every call and still
  matter for non-factual, exploratory questions where there's no single
  known-attribute answer to look up directly.
  Also fixed: a Windows console crash (UnicodeEncodeError) when an agent
  or skill result containing an emoji or other non-cp1252 character hit a
  plain print() — cli.py now has a _safe_print() that encodes with
  errors="replace" so unencodable characters degrade to a replacement
  character instead of crashing the program.
  (Stray-entity misattribution bug noted above is unchanged by this work —
  still logged, still not fixed.)

- Added detection and a single automatic retry in agent.py's
  handle_request() for two observed non-tool-calling failure modes: a
  hallucinated fake tool-call written as plain text (literal "<tool_call>"
  in message.content) and an explicit refusal to use any tool (narrow,
  literal match on one observed phrasing — "I can't help with that
  question using the available tools" — known to miss rephrased refusals,
  not generalized with fuzzy matching or another LLM call by design).
  Detection only ever activates when finish_reason != "tool_calls", so
  the normal successful path is untouched. On detection: log a WARNING
  with the full raw content, retry the identical request once, and use
  whatever comes back as final — if the retry also fails the same check,
  log a second WARNING (so persistent failures are visible) but never
  retry a second time.
  This is defensive/insurance code for a rare, confirmed-but-not-
  precisely-quantified failure rate — same framing as the
  reasoning_content defensive check in llm_client.py. A controlled n=10
  batch of agent.handle_request("where do I live") showed 0/10 failures
  (see test_agent_reliability.py), but two real instances (one of each
  failure mode) were captured verbatim during earlier ad hoc testing, so
  the true rate is non-zero but not pinned down by this sample size. The
  retry logic itself was verified in isolation with deliberately
  constructed fake response objects (test_agent_retry_isolated.py, 4
  scenarios / 8 checks, all passing) — hallucinated-text retry-and-
  succeed, refusal retry-and-succeed, persistent-failure-no-second-retry,
  and normal-path-unaffected — independent of whether the live model
  happens to fail during any given test run. The WARNING logs are the
  actual point: real usage will accumulate real frequency data over time
  instead of leaving this at two anecdotes.

- Added a minimal local web interface (web_app.py, templates/index.html)
  wrapping the existing pipeline/retrieval/agent code unchanged — Flask,
  server-rendered Jinja, no JavaScript, no external CDN dependencies, so
  it stays fully offline-reliable. GET / shows a log form, an ask form,
  the 10 most recent memories (via graph_engine.get_recent_episodes(), a
  new read-only addition), and — when agent.pending_confirmation is set —
  a Confirm/Cancel UI for high-stakes actions. POST /log, /ask, and
  /confirm call pipeline.ingest_episode(), agent.handle_request(), and
  skills.confirm_skill() respectively, then re-render the same page.
  Validated end-to-end via Flask's test_client() (test_web_app.py, kept
  in the repo): logging a memory, seeing it appear in the recent list,
  asking a factual question and getting the direct_state_lookup answer
  through this interface unchanged, and the full high-stakes
  confirm/cancel flow (pending state shown, cancel takes no action,
  confirm actually executes) — all confirmed working before this commit.

- Fixed historical-state retrieval, informed by a contributor's bug
  report that flagged direct_state_lookup() as only ever checking
  active=true states — so a question about a past value ("what was my
  previous job") got no direct answer at all. The contributor's own
  branch (fix/historical-retrieval, not merged, not built on) worked
  around this with keyword-sniffing ("previous"/"before"/etc.) and a
  parallel historical_state_lookup() function querying inactive states —
  a design with its own bug: it required len(rows) != 1 to fail closed,
  so it silently returned None whenever more than one historical state
  existed for an attribute (e.g. a job changed twice), rather than
  picking the most recent one.
  Implemented fresh instead of building on that branch. Added
  retrieval.classify_temporal_intent(question) -> "current"|"historical",
  a single LLM classification call (llm_client.extract_json(), one
  dedicated prompt) rather than keyword matching — the same reasoning as
  the earlier attribute-matching redesign (see match_question_to_attribute()
  history above): a fixed word list can't cover every phrasing a person
  might use, full-sentence understanding can. Defaults to "current" on
  any malformed or missing response, since misclassifying as "historical"
  is the riskier direction — it would substitute a superseded value for
  the current one. Called exactly once, only after
  match_question_to_attribute() has already resolved an attribute — never
  called speculatively before there's something to look up.
  direct_state_lookup() itself (not a parallel function) now branches on
  this classification: "current" is unchanged (active=true, same as
  before); "historical" queries active=false states for the same
  (speaker, attribute) pair, ORDER BY superseded_at DESC LIMIT 1 —
  explicit LIMIT 1 makes this always deterministically return the single
  most recently superseded value, fixing the contributor branch's
  silent-failure case for >1 historical state.
  Validated against live Nebius + Neo4j against the contributor's exact
  test cases plus one they didn't cover: Sydney -> Melbourne ("where do I
  live now" -> Melbourne; "where did I live before Melbourne" -> Sydney);
  software engineer -> data scientist ("what is my current job" -> data
  scientist; "what was my job before becoming a data scientist" ->
  software engineer); and a 3-state case (software engineer -> data
  scientist -> product manager) the contributor's len(rows) != 1 check
  would have silently failed on — "what was my previous job" correctly
  returned data scientist (the more recently superseded of the two prior
  states), not software engineer and not None. Confirmed via direct state
  history inspection that all three job states existed with distinct
  superseded_at timestamps before trusting the query result.
  Full credit to the contributor's bug report for correctly identifying
  all three underlying issues (historical retrieval failing,
  direct_state_lookup()'s active=true-only limitation, and the
  marathon/Hyrox semantic-ranking issue below) even though this fix's
  implementation — LLM classification inside direct_state_lookup() itself
  — differs from their branch's keyword-matching parallel function.
  Also observed during this validation: a second, different variant of
  the tool-calling malformed-output failure mode first logged in the
  previous entry — raw JSON prose written as plain content (e.g.
  {"name": "query_memory", "arguments": {...}}) instead of a natural-
  language answer, distinct from the previously-seen literal
  "<tool_call>" text pattern. agent.py's existing retry detection only
  matches the literal "<tool_call>" substring and did NOT catch this
  variant, meaning it would have silently returned malformed output as a
  final answer to the user. Not fixed yet. This suggests the detection
  approach (matching specific known-bad substrings one at a time) may
  need to become more general — e.g. detecting "content is non-empty AND
  looks like structured data rather than a natural-language answer"
  rather than enumerating literal known-bad strings one at a time. Needs
  its own design pass, not a quick patch.

## Next up

(1) Marathon/Hyrox-style semantic ranking issue — real production
instance of the importance-vs-relevance calibration issue logged since
Phase 2. (2) Generalize agent.py's malformed-tool-call-output detection
beyond literal substring matching, given a second distinct failure shape
was just observed.
