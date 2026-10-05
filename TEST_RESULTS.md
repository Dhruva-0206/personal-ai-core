# Test Results

91 cases executed: 46 passed, 45 failed, 0 awaiting review. T000 is historical and excluded.

Counts represent failing cases, with overlapping underlying bugs. Full inputs, exact outputs, graph snapshots, and severity rationale are in `TEST_LOG.md`. The shared DNS outage was retried successfully and both attempts remain recorded.

Main findings: confirmation state is shared across Flask clients; explicit agent speaker scope is dropped; malformed responses and failed services can crash request handling; historical and multi-entity retrieval misses stored facts; Ann and Anna merge; correction and duplicate writes pollute temporal history; reminder stub success is presented as an actual scheduled reminder.

## CRITICAL — 14 failing cases

- **extraction**: T037, T038. Malformed extraction shape.
- **infrastructure**: T046, T057, T091. Nebius unavailable; UI Neo4j error presentation; Retry outage.
- **model instability**: T044, T088. Empty model choices; Empty tool-call list.
- **retrieval**: T061. Agent speaker isolation.
- **tool/skill safety**: T041, T042, T043, T048, T058, T089. Malformed tool JSON; Unknown tool; Missing tool argument; Failed tool; Confirmation session isolation; Tool args wrong JSON type.

## HIGH — 25 failing cases

- **UI**: T056. UI Nebius error presentation.
- **agent reasoning**: T027, T047. Where does Bob live?; Multiple model tool calls.
- **entity resolution**: T025, T070. Where does Ann live?; Substring merges distinct similar names.
- **extraction**: T039, T040, T086, T087. Malformed extraction shape; Importance range validation; Summary type validation.
- **model instability**: T018. Does Rahul live where I live?.
- **persistence**: T033. Remind me to stretch tomorrow at 9am..
- **retrieval**: T005, T007, T008, T015, T016, T028, T030, T035, T065, T066, T067, T068. Where did I live before Pune?; Where did I live before Mumbai?; Where do I live?; Where does my brother live?; Which matters more to me, career or family?; Historical lookup with multiple past states; Historical lookup wrong subject; Direct lookup wrong subject; Direct lookup with distinct subjects.
- **supersession**: T069, T083. Supersession links immediate predecessor; Live immediate predecessor supersession edges.

## MEDIUM — 6 failing cases

- **model instability**: T045. Empty agent answer.
- **supersession**: T014, T022, T023. Where do I live?; Where did I live before Adelaide?.
- **tool/skill safety**: T059, T090. Pending survives unrelated ask; High stakes invalid argument validation.

## LOW — 0 failing cases

None.

## Reproducible failing test IDs

T005, T007, T008, T014, T015, T016, T018, T022, T023, T025, T027, T028, T030, T033, T035, T037, T038, T039, T040, T041, T042, T043, T044, T045, T046, T047, T048, T056, T057, T058, T059, T061, T065, T066, T067, T068, T069, T070, T083, T086, T087, T088, T089, T090, T091

Deterministic injected/double failures reproduce with the provided scripts. Live failures are observed results with preserved setup; reruns may vary with model output.

## Recommended fixes for later

- Extraction schema validation
- Entity resolution
- Temporal supersession and historical retrieval
- Agent response/tool validation and service-error handling
- Speaker propagation and confirmation session ownership
- Flask error presentation
- Reminder persistence/integration
