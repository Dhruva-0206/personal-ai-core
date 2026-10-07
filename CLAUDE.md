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

- Diagnosed and fixed a latency problem in the agent path, prioritized
  ahead of the marathon/Hyrox fuzzy-ranking issue on purpose: instrumented
  a full agent.handle_request() call (timing added via monkey-patching in
  an isolated scratch script, no permanent code changes) and found that
  every fuzzy/historical query was making 5 sequential LLM calls end to
  end — agent.py's initial tool-call decision, retrieval.embed(),
  match_question_to_attribute()'s Layer-3 LLM classification,
  classify_temporal_intent(), and agent.py's follow-up natural-language
  answer. That's a breadth-of-impact problem (every such query pays for
  it) versus the marathon/Hyrox ranking issue, which is a narrower edge
  case — fixed this one first on that basis.
  Root cause of the 5-call chain: attribute matching's LLM layer and
  temporal-intent classification were two separate sequential round-trips
  whenever the cheap exact/substring attribute layers didn't resolve
  anything, even though both calls see the same question and could be
  answered together. Added retrieval.classify_question(question,
  known_attributes) -> {"attribute": ..., "temporal": ...}, a single
  llm_client.extract_json() call combining both jobs, with the same
  validation rules as the two calls it replaces (attribute forced to None
  unless it's exactly one of known_attributes; temporal defaults to
  "current" unless it's exactly "current" or "historical" — never trust
  an invented attribute, never let a malformed response silently produce
  the riskier "historical" default).
  Restructured direct_state_lookup() rather than creating a parallel
  function: extracted the cheap exact/substring layers out of
  match_question_to_attribute() into _match_attribute_fast() (identical
  queries, identical behavior — a pure extraction, not a logic change) so
  direct_state_lookup() can check the fast path on its own terms. If the
  fast path resolves an attribute, only classify_temporal_intent() runs
  (one call, unchanged from before). If the fast path finds nothing,
  classify_question() runs once instead of match_question_to_attribute()'s
  old separate Layer-3 call followed by classify_temporal_intent() (two
  calls) — cutting the sequential-call chain from 5 to 4 for every query
  that needs the LLM attribute-matching path. Everything downstream
  (active vs. historical state queries, LIMIT 1 on historical) is
  byte-for-byte unchanged — only the classification call count changed.
  Validated in two stages, per the project's own precedent of correctness
  before speed: (1) re-ran all prior historical-retrieval test scenarios
  (Sydney/Melbourne current+historical, software engineer -> data
  scientist -> product manager current+historical) end to end against
  live Nebius + Neo4j and confirmed every answer was byte-for-byte
  identical to the pre-refactor validation, including the 3-state LIMIT 1
  case (data scientist, not software engineer) — zero regressions before
  trusting any timing number. (2) Re-measured timing with the same
  monkey-patch instrumentation, re-running "where do I live" and "what
  athletic event am I preparing for" (2 runs each, graph state restored
  to match the original measurement's episodes for a fair comparison).
  Previous (5-call) totals: 40.604s, 13.419s, 16.821s, 14.042s (average
  ~21.2s; ~14.8s excluding the 40.6s outlier). New (4-call) totals:
  13.684s, 9.875s, 9.325s, 8.508s (average ~10.35s) — a 30-51% reduction
  in average end-to-end time, consistent with removing one full LLM
  round-trip (individually ~0.8-2.5s in the prior measurement) from every
  request.
  Observed during this same timing work: call-to-call latency varies by
  roughly 3-8x on identical call shapes with no logic difference (e.g. one
  embed() call taking 4.2s, another taking 0.37s, on the same query
  re-run seconds apart). Believed to be Nebius trial-tier infrastructure
  variance (rate limiting / cold starts / shared capacity), not something
  fixable in our code — worth re-checking once/if usage moves past trial
  tier, since it would otherwise mask or exaggerate the effect of future
  latency work.
  Also observed during this validation: a THIRD distinct variant of the
  tool-calling malformed/refused-output failure mode — an outright
  refusal with novel wording ("I don't have access to your personal
  information like your location...") — different from both previously
  seen variants (the literal "<tool_call>" text hallucination, and raw
  JSON prose written as plain content). None of the three occurrences
  used identical wording. This confirms the existing literal-substring
  detection in agent.py (_REFUSAL_PHRASE, matching one exact phrasing)
  structurally cannot generalize to catch this class of failure — it's
  now a confirmed recurring pattern across 3 independent occurrences, 3
  different shapes, not a one-off edge case. Not fixed yet.

- Generalized agent.py's malformed/refused tool-call detection from
  literal substring matching to structural signals, replacing
  _is_known_tool_calling_failure() (which matched one exact "<tool_call>"
  substring and one exact _REFUSAL_PHRASE string) with
  is_malformed_or_refused(content) -> bool. Two checks, in order:
  (1) _has_structural_malformation() — content containing both "{"/"}"
  and one of "\"name\""/"\"function\""/"\"arguments\"" (catches
  JSON-shaped tool-call prose generically), OR content starting with "<"
  (catches tag-like leaks generically, not just the one tag name we'd
  seen). This generalizes over both previously-seen structural variants
  without hardcoding either exact string. (2) _has_refusal_heuristic_match()
  — a small, explicitly-documented-as-incomplete fragment list ("don't
  have access", "can't help with that", "unable to", "no way to
  determine", "not able to"), case-insensitive. is_malformed_or_refused()
  takes only content, not finish_reason — handle_request() is responsible
  for the finish_reason != "tool_calls" gate, so a successful tool call or
  a legitimate plain-text answer (e.g. "hello") is never flagged just for
  being plain text. Kept the existing single-retry-then-accept behavior
  and WARNING-level logging exactly as before; added a
  _categorize_failure() helper so each WARNING now also logs which check
  fired ("structural" vs. "refusal_heuristic"), for easier categorization
  of future failures.
  Validated in two stages. (1) New isolated unit test
  (test_failure_detection.py, no real API calls), 5 cases, all behaved
  exactly as expected: the two previously-seen structural variants (the
  <tool_call> tag text, and raw JSON prose) both correctly detected; a
  normal tool-call success and a normal plain-text "hello" answer both
  correctly NOT flagged; and — the deliberately negative case — a
  refusal reworded to avoid every fragment in the heuristic list
  ("Sorry, but I don't think I'm the right tool for figuring that out.")
  correctly went UNDETECTED, exactly as the documented limitation
  predicts. This was a test built to honestly expose the ceiling, not to
  pass by construction. (2) Live 10x reliability re-run of
  agent.handle_request("where do I live"): 9/10 calls were real tool
  calls (all correct, Melbourne). The 10th was a 4th real, independently-
  worded refusal/failure ("I don't have information about your location
  stored. Could you please provide your current location...") — false,
  since the graph does have the location stored — and it also went
  undetected, because it doesn't match any of the 5 fragments either.
  This confirms the predicted ceiling with a live, real occurrence in the
  same validation run, not just the constructed test case.
  This detection is meaningfully broader than before (it catches failure
  SHAPES, not just one exact string) but has a proven ceiling: refusal
  wording is open-ended, and a fragment list will never catch every
  phrasing. We are accepting this as good-enough insurance (paired with
  the existing single-retry, which still helps even when detection
  doesn't fire — the retry can and does succeed on the next attempt
  regardless of whether the failure was recognized) rather than
  continuing to chase individual refusal wordings — that would be a
  losing game against LLM phrasing variety. A more complete fix (a
  separate LLM call judging "did this response actually answer the
  question") was considered and explicitly rejected for now, since it
  would reintroduce the exact latency cost (one more sequential LLM
  round-trip per request) that the previous step just worked to remove.

- Parallelized the three independent retrieval lanes (vector_search,
  fulltext_lane, recent_lane) in retrieve() via ThreadPoolExecutor —
  pure concurrency change, no change to merge/scoring logic.
  direct_state_lookup() and its embedding call stay sequential and run
  first, unchanged, since a successful direct lookup already
  short-circuits downstream logic elsewhere. Each concurrently-running
  lane opens its own Neo4j session via a new
  _run_lane_in_own_session() helper rather than sharing the session used
  for direct_state_lookup()/the final merge loop — Session objects are
  not thread-safe per the neo4j Python driver's own contract, only the
  Driver is meant to be shared across threads.
  Validated correctness first: re-ran all 4 historical-retrieval test
  scenarios (Sydney/Melbourne current+historical, software engineer ->
  data scientist -> product manager current+historical) and confirmed
  every answer was unchanged at the retrieve() layer. Two agent-level
  follow-up-call oddities surfaced during this run (a hallucinated
  <tool_call> tag as final text, and separately an oddly hedged/vague
  phrasing that didn't name the retrieved entity) — both confirmed via
  direct cli.py search inspection to be pre-existing follow-up-call
  flakiness with correct underlying retrieve() data underneath, unrelated
  to this change. Logged as a new known gap below, not fixed here.
  test_offline.py: 7/7, unaffected.
  Measured timing in two layers, because the first (agent-level) layer
  turned out too noisy to show the effect at all. Agent-level total time
  showed no visible improvement (previous gap ~1.87-2.17s; new gap
  ~1.75-2.62s — within noise, LLM-call jitter this session has shown
  swings of 2-8x run to run). Rather than conclude "parallelization did
  nothing," added a second, isolated measurement: monkey-patched
  vector_search/fulltext_lane/recent_lane directly (no agent, no LLM
  calls in the timed portion) to record each lane's own start/end wall-
  clock timestamps. This confirmed concurrency is genuinely happening
  (all three lanes start within ~1ms of each other in every run) and
  revealed the real mechanism limiting the payoff: vector_search
  dominates the other two lanes by 2-12x (0.85-0.89s vs. 0.07-0.4s for
  fulltext_lane/recent_lane), because vector_search does a full
  per-speaker Episode scan AND a separate _episode_state_status() Neo4j
  round-trip for EVERY episode it scans — an N+1 query pattern internal
  to that one lane. Since concurrent wall-time is bounded by the slowest
  lane (not by the sum), the actual achievable saving is sum-of-lanes
  minus the slowest lane, i.e. only whatever fulltext_lane + recent_lane
  would have cost sequentially: measured at 0.14-0.79s per request across
  3 runs. Real, but small relative to vector_search's own unparallelized
  cost, and small enough to sit inside this session's own LLM-jitter
  noise floor — which is exactly why the agent-level number didn't move.
  vector_search's N+1 pattern (one scan query + one state-status query
  per episode) is now a known, unaddressed latency issue in its own
  right, logged for a future pass — separate from today's task, and
  likely a bigger lever than lane parallelization was, now that we know
  it's the dominant cost among the three lanes.
  Also logged as a new gap (not fixed): the two follow-up-call quirks
  observed during this validation happened at a call site
  is_malformed_or_refused() doesn't cover — it's only ever checked
  against the initial tool-selection call in handle_request(), never
  against the final follow_up call's own content. A hallucinated
  <tool_call> tag reaching the user as the literal final answer (as
  happened once during this validation) would currently go completely
  undetected and unretried.

- Marathon/Hyrox ranking investigation (top-5 results, match_type
  labeling, a reverted prompt guardrail, and a root-cause finding).
  Diagnosed earlier: for "What athletic event am I preparing for?" the
  agent confidently named only one of two genuinely true, relevant facts
  (marathon training, Hyrox registration).
  (a) Top-5 results — KEPT, works. query_memory (skills.py) now returns a
  "results" list of up to QUERY_MEMORY_TOP_N=5 results (summary,
  combined_score, similarity, importance, recency, state_status, lanes)
  instead of only results[0], and agent.py's follow-up call receives the
  list unchanged (it already serialized whatever run_skill() returned).
  This prevents the confidently-names-only-one failure: in the first
  validation run the answer mentioned both events. Side effect: a
  direct_state_lookup hit no longer short-circuits to a single result;
  up to 4 fuzzy results (including unrelated or superseded ones) ride
  along behind it on every direct-lookup question. Re-validated "where do
  I live now" (Melbourne) and "what is my current job" (product manager)
  — both still correct.
  (b) match_type labeling — KEPT. Each result now carries match_type:
  "direct_lookup" for the direct_state_lookup entry, "fuzzy_relevance"
  for everything else, alongside the unchanged combined_score. Honest
  labeling of a real distinction (a direct-lookup 1.0 is certainty that a
  fact is active, not comparable to a fuzzy relevance score), valuable
  independent of whether it fixed this symptom — it did not.
  (c) Prompt guardrail — TESTED, REVERTED, did not work. A system message
  on the follow-up call told the model that scores across match_types
  are not comparable and not to infer an importance hierarchy. Across 3
  validation runs the model still named a single winner (Hyrox) every
  time, once dismissing the marathon as not "currently preparing for",
  once with a muddled explanation. Reverted per the same precedent as the
  VECTOR_MIN_SIMILARITY floor: no speculative guardrail kept that did not
  demonstrably change behavior.
  (d) Real root cause: the same episode text, logged identically across
  different test runs, produced DIFFERENT extracted states — once
  'marathon' got a tracked State (goal = marathon, so direct_state_lookup
  picked marathon), once it didn't (state_status "no_state"), with only
  Hyrox getting one (event_registration). This is extraction
  non-determinism at the fact-capture level, not a scoring or prompt
  issue. It is the same class of problem as the already-logged
  stray-entity-misattribution bug: the model is not reliably consistent
  about WHAT becomes a tracked fact from the same input. This likely
  undermines more than this one bug — any previously "passing" validation
  could have been sensitive to which way extraction happened to go on
  that particular run, not just this one.

- Replaced resolve_attribute()'s Layer 3 (embedding cosine similarity)
  with LLM-based classification against the entity's own existing active
  attribute names. Triggered by the extraction-consistency diagnostics
  (test_extraction_consistency.py): the same marathon sentence produced 4
  different attribute names across 10 isolated runs, and a full-pipeline
  10x ingest into one graph showed the existing embedding layer failing to
  merge them — "goal" vs "training" scored cosine 0.67, in the uncertain
  zone between the two original calibration points (city/location 0.80
  merge, city/job 0.65 no-merge), below ATTRIBUTE_SIMILARITY_THRESHOLD
  0.72, so they stayed as permanently parallel active States that never
  superseded each other. Same lesson as VECTOR_MIN_SIMILARITY and the
  question-to-attribute redesign: embedding geometry on bare short strings
  is not a reliable merge signal at this scale.
  New Layer 3 (graph_engine._llm_match_attribute): one
  llm_client.extract_json() call given the entity's active attribute names
  plus the new name and value; answer strictly validated to be exactly one
  of the existing names, else treated as "genuinely new" (never trust an
  invented match). Layers 1-2 (exact, substring) are unchanged. To give
  the prompt the value, resolve_attribute() gained an optional value=None
  parameter and pipeline.py passes it. ATTRIBUTE_SIMILARITY_THRESHOLD in
  config.py is now unused but left in place.
  Result on the reproducing case (10 ingests of "I started training for a
  marathon in December." into one graph): 6 of 7 LLM judgments merged the
  new name into the existing "activity" attribute (training, goal,
  goal, training, training_status all -> activity), versus the prior
  behavior of splitting into parallel "goal" and "training" States from
  the first near-synonym onward. Regression re-validated: Sydney/Melbourne
  and the 3-state job history still give Melbourne / Sydney / product
  manager / data scientist for current and historical questions;
  test_offline.py 7/7.
  The remaining 1/7 split (call 7: 'goal' rejected as matching 'activity'
  after matching it twice before, on near-identical input) is NOT a bug to
  keep chasing — it reflects genuine ambiguity in whether two phrasings
  describe the same real-world fact, which even a correctly-functioning
  LLM will answer inconsistently on borderline cases. Combined with the
  already-logged State/Action presence non-determinism (6/10 to 9/10
  depending on run), this confirms single-pass LLM extraction has an
  inherent consistency ceiling for genuinely ambiguous natural language.
  We are accepting this as a known architectural limitation of this entire
  approach (confirmed shared with Reeve's production system via source
  audit, not unique to us) rather than continuing to chase full
  determinism on ambiguous input. New cost of this fix: one additional LLM
  call per State write, once the entity has any existing active
  attributes — acceptable given the accuracy gain, but adds up across
  heavy logging use.

- Fixed vector_search()'s N+1 query pattern. It previously ran one query
  to scan all of a speaker's Episodes and then one
  _episode_state_status() query per scanned episode (1+N Neo4j
  round-trips; counted directly: 6 session.run calls for 5 episodes).
  Added retrieval._all_episode_state_statuses(session, speaker), one
  Cypher query returning total/active State counts for every Episode the
  speaker has, built into a dict keyed by episode_id; vector_search()
  looks each candidate up in that dict (default "no_state"), so it now
  takes 2 round-trips regardless of episode count. The
  current/superseded/no_state logic moved into a shared
  _classify_state_status() used by both the per-episode and bulk lookups
  so the two can't drift apart. _episode_state_status() itself is kept —
  retrieve()'s merge loop still calls it. Scoring formula, merge logic and
  vector_search()'s return shape are unchanged.
  Measured (vector_search() called directly on a fixed query embedding,
  5 episodes, warm-up call discarded, 7 timed runs each, no LLM calls in
  the timed section): median 0.462s before (range 0.447-0.653s) vs 0.171s
  after (range 0.165-0.240s) — a ~63% reduction in vector_search()'s own
  time. Small sample (5 episodes) and Aura network latency varies run to
  run, but both sets were tight; the saving grows with episode count
  since the new cost stays at 2 queries. Full retrieve()/agent end-to-end
  time was not re-measured.
  Correctness verified: output captured before and after on a fixed
  embedding — same episode IDs in the same order, identical state_status
  values (current, current, superseded, superseded, superseded),
  identical similarity scores, and combined_score matching to within
  1.7e-8, the only difference traced to the time-based recency term
  shifting between separate runs, not a logic change. All four
  Sydney/Melbourne + job-history agent scenarios (current and historical)
  gave the same answers as before, and a fuzzy "coffee" search showed
  correct state_status values; test_offline.py 7/7.
  A second, smaller instance of the same N+1 pattern exists in
  retrieve()'s merge loop (calls _episode_state_status once per merged
  candidate, bounded by the lanes' top_k limits rather than total episode
  count — so smaller impact than vector_search's version, but the same
  class of fix would apply: batch via _all_episode_state_statuses instead
  of per-candidate calls). Not fixed in this pass, scoped out deliberately
  to keep this diff clean and verifiable. Logged as a candidate for a
  future latency pass, lower priority than it was before this fix since
  the bigger cost is now resolved.

- Extended agent.py's malformed/refused-output detection to the agent's
  follow-up answer-generation call (the second call, made after a tool
  result is available), previously unguarded — only the tool-selection
  call was checked. After the follow-up call returns, the existing
  is_malformed_or_refused() runs on its content (reused unchanged, not
  forked). On a hit: a WARNING labeled "follow-up call" (with the same
  structural/refusal_heuristic categorization) and one retry of the same
  follow-up messages including the tool result; if the retry also fails
  the check, its content is accepted as final and a second WARNING says
  the failure persisted after retry — same single-retry-then-accept
  policy as tool-selection, no loop. Tool-selection handling and its
  "tool-calling" WARNING wording are unchanged, so the two failure sites
  stay distinguishable in logs.
  Validation, 14 isolated checks in test_followup_retry_isolated.py (fake
  response objects, no real API/graph calls; all pass): Case 1,
  malformed follow-up -> exactly one retry (4 checks: retry's clean
  content returned, 3 raw calls, retry reuses identical messages incl.
  tool result, one follow-up-labeled WARNING); Case 2, clean follow-up
  untouched (3 checks: returned as-is, 2 raw calls, no WARNINGs); Case 3,
  original and retry both malformed (4 checks: retry content accepted, 3
  raw calls / no loop, two follow-up-labeled WARNINGs, second says
  "persisted"); Case 4, tool-selection retry still works and its WARNING
  is labeled tool-calling, not follow-up (3 checks). Existing suites
  unaffected: test_agent_retry_isolated.py 8/8, test_failure_detection.py
  5/5, test_offline.py 7/7. Live 10x agent.handle_request("where do I
  live") run: 10/10 correct (Melbourne), 0 WARNINGs — no malformed output
  occurred, so the new path was NOT exercised live; the isolated tests
  are the actual correctness proof for this change, not the live run.
  This closes the gap identified when the retrieval-lane parallelization
  validation run surfaced a <tool_call> tag and muddled phrasing at the
  follow-up call site, previously unguarded. Both known tool-calling
  detection sites (tool-selection, follow-up answer) now share the same
  mechanism and the same known ceiling (fragment-list refusal detection
  won't catch every novel wording, accepted as good-enough insurance, not
  a complete fix — consistent with the original tool-selection
  detection's documented limitation). One side effect: the refusal
  fragment heuristic can false-positive on a legitimate follow-up answer
  containing a phrase like "unable to", costing one extra LLM call; the
  retry's content is accepted either way, so no answer is lost.

- First real-usage findings (web interface / hand-typed episodes layered
  on top of the synthetic regression chain), plus the follow-up diagnostics
  they triggered. No code changes in this entry.
  (1) City tracking held up on genuinely varied phrasing. The graph's
  "city" history ran Sydney -> Melbourne -> Pune -> Mumbai with each value
  correctly superseded in order and Mumbai the single active value. The
  first two steps came from test_regression_live.py's synthetic chain; the
  last two ("i live in pune", "i shifted to mumbai") were typed by the
  user in their own phrasing — so the real-usage part of this is
  Pune -> Mumbai on top of existing state, not four fully organic
  changes. Still the first non-template data, and it behaved correctly.
  (2) Live recurrence of the marathon/Hyrox pattern. "races I am training
  for" (correctly plural/open-ended) received a confident single answer
  (swimming or marathon, varying by run) despite BOTH being real,
  independently-stored facts. Diagnosed:
  - Storage/resolution: correct. "i am training for a marathon" became
    goal = training for a marathon and "i am training for a swimming race"
    became training = for a swimming race — two different, legitimately
    distinct attributes, both active, neither wrongly superseding the
    other (the LLM attribute-resolution layer judged them distinct).
  - Retrieval: correct. Both episodes surfaced in the top 3 fuzzy results
    at genuinely close scores (0.750 marathon, 0.721 swimming).
  - Root cause, as isolated by a bypass experiment (retrieve() with
    direct_state_lookup suppressed in a scratch script, same follow-up
    call): the follow-up answer-generation call invents a "primary vs
    secondary" hierarchy from ANY ranked list, even a ~0.03 score gap,
    whether or not a direct_lookup entry is present. With the direct
    entry (1.0) swimming came first; with it bypassed, the model made
    marathon primary instead (the new top fuzzy score) — the behavior
    persisted, only which fact won changed. This rules out the 1.0 score
    anchor as the primary cause: it is a disposition in how the follow-up
    call reasons over ranked results, not a scoring/retrieval problem.
    Caveat: small samples (3 runs per condition, and only 2 of the 3
    bypass runs actually produced an answer from results).
  - The single-attribute assumption is structural, not incidental: both
    match_question_to_attribute()'s and classify_question()'s prompts
    explicitly ask for "which ONE attribute", direct_state_lookup() only
    returns a single row by design, and for this question the cheap
    substring layer resolved "training" before any LLM attribute judgment
    ran at all (the other relevant attribute, "goal", never appears in
    the question text).
  (3) Smaller finding: in isolated testing the model occasionally rewrites
  the user's question before passing it to query_memory (3/10 runs for
  "races I am training for", e.g. "What races am I currently training
  for?", and once "What races are you training for?" — flipping "I" to
  "you"). Questions never pass through the self-reference normalization
  extraction.py applies to STORED facts, so a rewritten question can
  diverge from stored phrasing. Not confirmed to have broken an answer
  yet; logged as a known gap for future attention, not an active bug.
  (4) A transient tool-selection failure — 3 of 6 runs in one session
  answered "races I am training for" with a clarifying question instead
  of calling query_memory — did not reproduce in a follow-up test (0/20:
  10/10 for the fragment and 10/10 for "what races am I training for",
  every first call finish_reason=tool_calls). The earlier failures were
  clustered in time and the difference is statistically suggestive
  (roughly p ~0.01) that the first instance was real rather than a fluke,
  but it is not reproducible on demand — consistent with the previously
  documented Nebius trial-tier latency/reliability jitter, not a logic
  bug. (An earlier message miscounted this as 4/6; the correct count is
  3/6.) Neither malformed-output check flags a clarifying-question
  non-answer, so it was invisible to the existing detection. No fix
  attempted; flagged for passive monitoring during continued real usage
  rather than further synthetic batch testing.

- QA audit findings (testing branch), 2026-10-07. No code changes in this
  entry. A teammate's `testing` branch documents 91 cases (46 pass, 45
  fail; TEST_LOG.md, TEST_RESULTS.md, testing_artifacts/). Branch caveats:
  it forked at 9b0e35b, 15 commits behind main, so it ran against code
  that predates the LLM historical classification, LLM attribute
  resolution, structural malformed-output detection, follow-up retry
  guard and lane parallelization. It also contains source changes that
  must NOT be merged: retrieval.py carries the contributor's older
  keyword-based historical_state_lookup() (len(rows) != 1 bug, conflicts
  with main's fix) and web_app.py adds a one-line ensure_schema() call
  (harmless). Take only the logs/scripts if anything. "45 failed" counts
  cases, not bugs; ~10 are one pattern.
  Verified against current main (offline fake-client tests, live
  Nebius + Neo4j under throwaway qa_verify_* speakers, since reset; a
  stub session emulating the relevant Cypher for two cases):
  CONFIRMED:
  (1) Wrong-subject direct lookup: direct_state_lookup() is not scoped to
  the asking entity. With only Rahul having a city, "Where do I live?"
  returned Rahul/Canberra (confidence high) and the agent answered "You
  live in Canberra". Conversely, when User, Rahul and brother all have
  active cities the len(rows) != 1 check returns None. Wrong-subject
  historical lookups behave the same (stub).
  (2) Historical "before X" has no anchor: it returns the most recently
  superseded value regardless of X. On Sydney -> Melbourne -> Pune ->
  Mumbai: "before Pune" -> Pune (wrong, expected Melbourne), "before
  Mumbai" -> Pune (right by coincidence), "before Melbourne" -> Pune
  (wrong, expected Sydney). Earlier validation passed only because it
  used 2-state chains or unanchored "previous" questions.
  (3) Ann/Anna over-merge: resolve_entity Layer 2 substring match
  ('anna' contains 'ann', len >= 3) merged them live; Ann's Hobart state
  was superseded by Darwin. "Anne" would collapse too.
  (4) ~10 uncaught exceptions in agent.handle_request outside the retry
  guard (which only covers content failures when finish_reason !=
  "tool_calls"): malformed tool-call JSON, unknown tool, missing/wrong-
  type args, empty choices, empty tool_calls list, tool raising, Nebius
  down, retry call raising. Also a None answer on empty content, and a
  high-stakes confirmation created with empty args.
  (5) Pending-confirmation state: agent.pending_confirmation is a shared
  global, so a second Flask client could confirm another client's
  purchase; and handle_request clears it on entry, so any unrelated ask
  silently drops a pending confirmation.
  (6) Dead speaker parameter: handle_request(speaker=...) never reaches
  query_memory/retrieve (retrieve got speaker=None).
  NOT REPRODUCED / LLM variance: T015 and T016 (own-city and brother
  questions with several entities): both answered correctly on main, one
  run each, so possible variance on weak retrieval, not a structural
  isolation failure.
  UNVERIFIED: transitive SUPERSEDES edges (T069/T083; main's query links
  each new state to all inactive older ones, plausible but not checked
  live), extraction schema/range validation (T037-T040, T086, T087; only
  code-read), Flask error pages, correction/duplicate history (T014,
  T022, T023). The reminder stub (T033) is known. test_offline.py 7/7
  on main.

- Fixed wrong-subject direct lookup (QA audit finding 1), 2026-10-07.
  Root cause: direct_state_lookup()'s two state queries matched
  (e:Entity {speaker})-[:OF_ENTITY]-(s:State {attribute}) with no filter
  on which entity the question was about, so any entity holding the
  attribute matched; with only Rahul having a city, "where do I live"
  returned Rahul/Canberra at confidence high, and with several entities
  holding it the len(rows) != 1 check returned None. The old docstring
  ("Scoped across ALL of this speaker's entities") wrongly described
  this as intentional.
  Fix (retrieval.py): the existing classification call now also returns a
  "subject" ("self", or the exact name of one of the speaker's real
  entities, or null) — no extra LLM call; classify_temporal_and_subject()
  covers the fast attribute path and classify_question() (now taking the
  entity list) the slow path. _resolve_subject() maps "self" to the
  canonical "User" and requires any other name to match an existing
  entity case-insensitively, else None (never trusts an invented name).
  Both state queries now filter on e.name = the resolved subject, so
  cross-entity leakage is structurally impossible; an unresolvable
  subject returns None and the fuzzy lanes handle the question.
  classify_temporal_intent() is now unused by the lookup but left in
  place.
  Evidence: new test_subject_scoping_live.py (throwaway speakers only,
  never touches "default") fails 4 checks on the pre-fix code, including
  the exact Rahul/Canberra direct result, and passes 7/7 on the fix.
  Known-weak check: the agent-level "User has no city" check passed on
  BOTH old and new code (the LLM answered sensibly either way), so it is
  not proof in either direction; the deterministic direct_state_lookup
  checks are the real evidence. Agent-level checks are LLM-dependent —
  re-run once before treating a single failure as a regression. Other
  suites unchanged: test_offline 7/7, test_failure_detection 5/5,
  test_agent_retry_isolated 8/8, test_followup_retry_isolated 14/14,
  test_regression_live 6/6 (run against a throwaway speaker).

- Fixed historical "before X" anchoring (QA audit finding 2), 2026-10-07.
  Root cause: the historical branch of direct_state_lookup() never looked
  at which value the question named; it always ran ORDER BY
  s.superseded_at DESC LIMIT 1 and returned the most recently superseded
  state. Correct by coincidence on 2-state chains and for the newest
  anchor, wrong for any other anchor on 3+ state chains (Sydney ->
  Melbourne -> Pune -> Mumbai: "before Pune" and "before Melbourne" both
  returned Pune). The classification call extracted no anchor.
  Fix (retrieval.py): the existing classification call now also returns
  an "anchor" (the value a historical question names as its reference
  point, else null) — no extra LLM round-trip; both prompts' example JSON
  shapes were made neutral placeholders so the example doesn't bias the
  model toward "historical". New _find_anchor_state() matches the anchor
  against the subject's real state values the way entity/attribute names
  are matched elsewhere (case-insensitive exact, then unambiguous
  substring; no value-matching rule existed before). The lookup then
  returns the state the anchor state supersedes: SUPERSEDES edges run
  from each new State to EVERY older inactive State, so the immediate
  predecessor is the most recently created State the anchor supersedes.
  An anchor matching no state, or the first state in the chain, returns
  None — never a fallback to the most recent. No anchor ("my previous
  city") keeps the old most-recently-superseded behavior.
  Evidence: new test_historical_anchor_live.py (4-state chain on a
  throwaway speaker, setup verifies the chain and edges) passes 9/9 on
  the fix; on the pre-fix code 6/9 fail (before Melbourne and before Pune
  both returned Pune, before Perth and before Sydney returned Pune
  instead of None, and both agent-level checks failed — one answer was
  empty, the separate empty-answer bug still on Next up); the three
  passes were the coincidental cases (before Mumbai, no-anchor, current).
  Other suites unchanged: test_offline 7/7, test_failure_detection 5/5,
  test_agent_retry_isolated 8/8, test_followup_retry_isolated 14/14,
  test_subject_scoping_live 7/7, test_regression_live 6/6 (throwaway
  speaker).
  Known limits: (1) if a value repeats in the history (Sydney ->
  Melbourne -> Sydney), "before Sydney" resolves to the most recent
  occurrence — ambiguous by nature. (2) Anchor matching depends on what
  extraction wrote: if a state value was stored as "moved to Pune" the
  substring layer only resolves it when unambiguous — the same substring
  caveat as attribute matching. Agent-level checks in the new test are
  LLM-dependent; the direct_state_lookup checks are the deterministic
  evidence.

- Batch fix: entity over-merge, dead speaker param, uncaught exceptions,
  per-session confirmations — 2026-10-08. Four fixes from the QA audit
  (see "QA audit findings"), each its own commit with its own test.
  Fix 1, Ann/Anna entity over-merge (8760b12, graph_engine.py): a
  two-layer problem. Layer 2 used raw substring containment ("anna"
  contains "ann"), AND Layer 3 merged on name-embedding cosine alone —
  Ann/Anna scores 0.954 and Ann/Anne 0.954 against the 0.93 threshold —
  so fixing only Layer 2 would have left the merge live. Layer 2 is now
  whole-word containment (every word of the shorter name is a whole word
  of the other: "Rahul" matches "Rahul Sharma", "Ann" does not match
  "Anna"). Layer 3's embedding score now only proposes candidates, and a
  new _llm_match_entity() confirms with a closed-list, strictly
  validated LLM call (same pattern as _llm_match_attribute) that fails
  closed to "distinct" — a wrong merge destroys data, a wrong split
  doesn't. The call only runs when an embedding candidate exists.
  Side effects: the gate is conservative, so a typo'd name may now
  create a duplicate entity (probe: "Mumbay" was NOT merged into
  "Mumbai"); and "New York" still matches "York" under whole-word
  containment, as it did before. Test: test_entity_resolution_live.py
  12/12; the live part fails 4 checks on the old code (Ann's Hobart
  superseded by Darwin, Anna never created).
  Fix 4, dead speaker parameter (0c140ee, skills.py/agent.py):
  handle_request(speaker=...) never reached retrieval (retrieve() got
  speaker=None). run_skill() now takes the caller's speaker as a
  POSITIONAL-ONLY argument, so a model-supplied argument named "speaker"
  or "name" lands in kwargs instead of colliding; skills flagged
  speaker_scoped (query_memory) receive the trusted value, overwriting
  anything the model sent. Test: test_speaker_scoping.py 9/9 (offline
  fake-model checks plus a live two-speaker Adelaide/Brisbane check that
  never touches DEFAULT_SPEAKER); old code showed retrieve() receiving
  None for speaker='alice'.
  Fix 2, uncaught exceptions (e4226de, agent.py/skills.py): the single
  broadest fix of this session. handle_request() now ALWAYS returns a
  non-empty string and never raises: model calls go through guarded
  helpers; empty/missing choices, empty tool_calls, malformed or
  non-object arguments, unknown tools, missing required arguments,
  wrong argument types, skills that raise, a failing retry or follow-up
  call, and empty/None answers all become a short user-facing error.
  New skills.validate_args() checks shape, known skill, required fields
  and string types before dispatch, so an invalid make_purchase is
  rejected and never becomes a pending confirmation. Undeclared
  arguments are DROPPED, not rejected, which keeps Fix 4's trusted
  speaker authoritative. The existing malformed/refused-content retry
  logic is unchanged and runs inside the guards; a last-resort catch-all
  logs the traceback. Test: test_agent_guards_isolated.py 37/37 (fake
  client, no live calls); the old code failed 21 of 28 evaluated checks.
  Still not handled: only the first of several tool calls runs (T047, a
  documented limitation, not an exception).
  Fix 3, per-session pending confirmations (7e8dd09, agent.py/web_app.py/
  cli.py): the module-level agent.pending_confirmation global is gone,
  replaced by a per-session dict with get_pending()/clear_pending(),
  keyed by a session id carried in Flask's signed session cookie (random
  per-process SECRET_KEY; a forged cookie just yields a fresh empty
  session). One client can no longer see or confirm another's action. A
  pending action PERSISTS across unrelated asks and is cleared only by an
  explicit confirm or cancel (the safer default: silently dropping it
  could lose an approval the user still means to give). A new
  high-stakes request in the same session REPLACES the older pending one
  rather than stacking, so only the latest action can be confirmed.
  Known simplifications, deliberately not built: no expiry on pending
  actions (an abandoned one lives until the process exits), and the CLI
  uses the default session (single user). Test:
  test_pending_confirmation_sessions.py 16/16 (HTTP-level, fake model and
  purchase skill, no live services); the old code fails 9 of 16
  (cross-client visibility/confirm, unrelated ask clearing).
  Also (7abef59): fixed a flaky agent-level check in
  test_subject_scoping_live.py. "Says Adelaide, not Canberra" failed
  answers that were correct but mentioned Rahul's Canberra while
  explaining what they ignored (it failed in 3 of 6 full-suite runs).
  Both agent checks now assert the right city is named and the wrong one
  isn't CLAIMED as the user's home (claims_canberra_as_home(),
  phrase-based on unambiguous you-statements); 7/7 on five consecutive
  runs afterwards. The deterministic direct_state_lookup checks are
  unchanged and remain the real evidence.
  Final combined run: test_offline 7/7, test_failure_detection 5/5,
  test_agent_retry_isolated 8/8, test_followup_retry_isolated 14/14,
  test_agent_guards_isolated 37/37, test_pending_confirmation_sessions
  16/16, test_subject_scoping_live 7/7, test_historical_anchor_live 9/9,
  test_entity_resolution_live 12/12, test_speaker_scoping 9/9,
  test_regression_live 6/6 (run against a throwaway speaker; it resets
  "default" when run as-is). test_web_app.py (print-only) was updated for
  the per-session API but not re-run, since it writes to "default".

## Test files

Reference only — all 15 files are committed (11 self-checking, 4 diagnostics). Run from the project root.

Self-checking suites (print PASS/FAIL per check, nonzero exit on failure):
- test_offline.py — `python test_offline.py`. Pure unit tests (cosine,
  code-fence stripping, extraction fallback). No credentials, no network.
  Expected: 7/7.
- test_failure_detection.py — `python test_failure_detection.py`. Tests
  agent.is_malformed_or_refused() on constructed strings. No credentials,
  no network. Expected: 5/5, including one deliberately undetected case
  that documents the heuristic's ceiling.
- test_agent_retry_isolated.py — `python test_agent_retry_isolated.py`.
  Tool-selection retry logic with fake response objects and the stub
  skills. No credentials, no network. Expected: 8/8.
- test_followup_retry_isolated.py — `python test_followup_retry_isolated.py`.
  Follow-up-call retry logic, same fake-client approach. No credentials,
  no network. Expected: 14/14.
- test_agent_guards_isolated.py — `python test_agent_guards_isolated.py`.
  Response-shape and tool-call guards on handle_request (malformed JSON,
  unknown tool, missing/wrong-type args, empty choices/tool_calls, model,
  retry, follow-up and skill failures, empty answers, rejected high-stakes
  calls). Fake client, no credentials, no network. Expected: 37/37.
- test_pending_confirmation_sessions.py — `python test_pending_confirmation_sessions.py`.
  Per-client pending confirmations through the Flask test client (isolation,
  forged cookie, persistence across unrelated asks, replace-on-new-request).
  Fake model and purchase skill, no credentials, no network. Expected: 16/16.
- test_entity_resolution_live.py — `python test_entity_resolution_live.py`.
  Whole-word helper checks (offline) plus a LIVE Ann/Anna/Ann scenario:
  needs Nebius + Neo4j; one throwaway "regress_entity_*" speaker, deleted
  afterwards, never touches "default". Expected: 12/12. The live part
  involves LLM extraction/judgment — re-run before treating one failure as
  a bug.
- test_speaker_scoping.py — `python test_speaker_scoping.py`. Offline
  fake-model checks that the speaker reaches retrieve() and can't be
  overridden by model arguments, plus a LIVE two-speaker check through
  query_memory and the agent: needs Nebius + Neo4j; throwaway
  "regress_speaker_*" speakers, never touches "default" or
  config.DEFAULT_SPEAKER. Expected: 9/9.
- test_historical_anchor_live.py — `python test_historical_anchor_live.py`.
  LIVE: needs Nebius + Neo4j. Self-checking (9 checks); one throwaway
  "regress_anchor_*" speaker, deleted afterwards, never touches "default".
  4-state city chain; checks "before X" returns X's immediate predecessor,
  no-anchor and unmatched-anchor behavior. Agent-level checks are
  LLM-dependent — re-run before treating one failure as a bug.
- test_subject_scoping_live.py — `python test_subject_scoping_live.py`. LIVE:
  needs Nebius + Neo4j. Self-checking (7 checks); uses throwaway
  "regress_subject_*" speakers and deletes them, never touches "default".
  Regression test for wrong-subject direct lookup. Agent-level checks are
  LLM-dependent — re-run before treating one failure as a bug.
- test_regression_live.py — `python test_regression_live.py`. LIVE: needs
  Nebius Token Factory and Neo4j Aura credentials in .env and an Aura
  instance that is not auto-paused. RESETS the "default" speaker, logs the
  Sydney/Melbourne + 3-state job chain, then asserts current and
  historical answers via the agent and the high-stakes confirmation gate.
  Exit code verified directly: 1 on a deliberate failure, 0 on a clean run
  (2 if setup fails). Single failures can be LLM flakiness — re-run first.

Live diagnostics (print-only, NO pass/fail — useful for investigation,
not for regression detection; all need live Nebius and, where noted,
Neo4j credentials):
- test_tool_calling.py — Nebius only. Isolated native tool-calling probe.
- test_agent_reliability.py — Nebius + Neo4j. 10x agent.handle_request(
  "where do I live"), reports tool-call vs. refusal vs. hallucination.
- test_extraction_consistency.py — Nebius only (no graph writes). 10x
  extraction.extract() on the marathon and Hyrox sentences.
- test_web_app.py — Nebius + Neo4j. Drives web_app.py in-process via
  Flask's test_client (log, ask, confirm/cancel flow). Candidate for
  future conversion to real assertions, not done yet — currently the only
  coverage of the web interface and it needs manual inspection.

## Next up

The QA-audit fixes are done (wrong-subject lookup, historical anchoring,
entity over-merge, exception guards, per-session confirmations, dead
speaker param — see Build log). What remains, in order, now weighted
toward demo readiness:
(1) Marathon/Hyrox-class hierarchy-invention in the follow-up call: the
model imposes a ranking hierarchy on any scored list; next step is a
targeted follow-up-prompt guardrail to present comparably-scored results
as co-equal facts. (2) Question-text self-reference normalization gap
(lower priority, unconfirmed harm). (3) Continue real usage via the web
interface rather than synthetic testing. (4) Hosted/deployed demo.
(5) Demo video. (6) README: explicit Nebius/Nemotron callout, submission
project description, and Nebius/NVIDIA tool feedback. (7) Stub/open
endpoints plus animated clips for future-scope integrations (wearables,
location, glasses) per the original wider vision.
