"""Render evidence into the requested running Markdown log."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; ART=ROOT/'testing_artifacts'
rows=[]
offline=[('cosine identical',[1,2,3],1),('cosine orthogonal',[[1,0],[0,1]],0),('cosine zero',[[0,0],[1,1]],0),('JSON fence','```json\n{"a": 1}\n```','{"a": 1}'),('plain fence','```\n{"a": 1}\n```','{"a": 1}'),('no fence','{"a": 1}','{"a": 1}'),('fallback','some raw text','Required keys, importance 0.5, empty entities')]
for i,(title,setup,expected) in enumerate(offline,71): rows.append(dict(id=f'T{i:03}',title=title,category='Existing offline suite',setup=setup,expected=expected,actual='Assertion passed in test_offline.py',result='PASS'))
for i,title in enumerate(['Fake tool text triggers one retry','Refusal retries into reminder tool','Repeated refusal stops after single retry','Normal tool call requires no retry'],78): rows.append(dict(id=f'T{i:03}',title=title,category='Existing retry suite',setup=f'test_agent_retry_isolated.py scenario {i-77}',expected='Both response and call-count assertions pass',actual='2 assertions passed',result='PASS'))
for name in ['isolated_results.json','graph_results.json','persistence_results.json','extra_results.json']:
    if (ART/name).exists(): rows.extend(json.loads((ART/name).read_text()))
if (ART/'live_results.json').exists():
    live=json.loads((ART/'live_results.json').read_text())
    reviews=json.loads((ART/'live_reviews.json').read_text()) if (ART/'live_reviews.json').exists() else {}
    first_attempts={r['id']:r for r in live['results']}
    retry_reviews=json.loads((ART/'tail_reviews.json').read_text()) if (ART/'tail_reviews.json').exists() else {}
    tail=json.loads((ART/'tail_results.json').read_text())['results'] if (ART/'tail_results.json').exists() else []
    latest={r['id']:r for r in live['results']}
    latest.update({r['id']:r for r in tail})
    live['results']=list(latest.values())
    for r in live['results']:
        is_retry=any(x['id']==r['id'] for x in tail)
        review=retry_reviews.get(r['id'],{}) if is_retry else reviews.get(r['id'],{})
        previous=first_attempts[r['id']] if is_retry else None
        rows.append(dict(id=r['id'],title=r['question'],category=r['category']+' — live Flask/Nebius/Neo4j',setup={'speaker':r['speaker'],'logs':r['logs'],'question':r['question'],'ingestion_results':r.get('ingestions')},expected=r['expected'],actual={'response':r.get('actual'),'error':r.get('error'),'graph':r.get('graph'),'previous_attempt':previous},result=review.get('result','AWAITING REVIEW'),failure_category=review.get('failure_category'),severity=review.get('severity'),notes=review.get('notes','')+(' Original infrastructure-failing attempt is preserved above; verdict refers to retry.' if is_retry else '')))
rows.sort(key=lambda r:r['id'])
passed=sum(r['result']=='PASS' for r in rows); failed=sum(r['result']=='FAIL' for r in rows)
text=['# Personal AI Test Log','', '## Summary',f'- Date: 5 October 2026 (Australia/Sydney). Branch verified: `testing`.',f'- Total executed test cases: {len(rows)}',f'- Passed: {passed}',f'- Failed: {failed}',f'- Awaiting manual review: {len(rows)-passed-failed}']
for sev in ['CRITICAL','HIGH','MEDIUM','LOW']:text.append(f'- {sev.title()}: '+str(sum(r.get('severity')==sev and r['result']=='FAIL' for r in rows)))
text += ['', 'Production files were not changed by this audit. The pre-existing one-line change in `web_app.py` was preserved. Live writes use unique `audit_` speaker namespaces; no existing data was reset. Purchase and reminder skills are stubs; safety tests did not execute real purchases. Injected failures and graph doubles are explicitly labeled and do not establish live-service failure rates.', '', '35 live cases plus 56 offline, injected, controlled-graph, and restart/inspection cases were executed. Counts are test cases, not distinct bugs. T015–T035 encountered a shared DNS outage in the first run and were retried after connectivity returned. Both attempts are preserved; current verdicts use the completed retry. Test memories remain in isolated audit namespaces for investigation.', '', '## Historical finding', '', '### T000 — Fresh database schema initialization', 'Category: infrastructure', 'Setup/Input: Fresh Neo4j database; POST /ask.', 'Expected: Schema initialized and ask succeeds.', 'Actual/Error: `There is no such fulltext schema index: episode_raw_text`.', 'Result: FAIL (historical, user-provided; excluded from current totals).', 'Failure category: infrastructure', 'Severity: CRITICAL', 'Notes: `graph_engine.ensure_schema()` existed but startup did not call it. The user manually added that call before this audit. Not modified further and not re-created against a separate fresh database.', 'Suggested area to investigate later: Flask startup/schema lifecycle.', '', '## Test Results']
for r in rows:
    text += ['',f'### {r["id"]} — {r["title"]}',f'Category: {r["category"]}', '', 'Setup/Input:', '```json',json.dumps(r['setup'],indent=2,ensure_ascii=False,default=str),'```','', 'Expected:',str(r['expected']),'','Actual / exact observation:','```json',json.dumps(r['actual'],indent=2,ensure_ascii=False,default=str),'```','',f'Result: {r["result"]}',f'Failure category: {r.get("failure_category") or "N/A"}',f'Severity: {r.get("severity") or "N/A"}',f'Notes: {r.get("notes") or "See exact observation above; isolated fixtures are process-local and production source is unchanged."}',f'Suggested area to investigate later: {r.get("failure_category") or "N/A"}']
text+=['','## Coverage limits','Live tests cover server-rendered Flask forms, not visual browser layout. Neo4j and Nebius outages are injected rather than deliberately caused against real services; a real shared DNS failure also occurred during the first run. Fresh-database behavior is recorded as historical evidence; no user database was dropped. Reminder scheduling and purchasing integrations do not exist. No statistical claim about model failure probability is made.','', '## Reproduction','Run `.venv/bin/python testing_artifacts/run_isolated.py`, `.venv/bin/python testing_artifacts/run_graph_checks.py`, and `.venv/bin/python testing_artifacts/run_reliability_extra.py` for deterministic failures. Run `.venv/bin/python testing_artifacts/run_live.py` with authorized service access for a fresh isolated live sequence; model results may vary. Run `.venv/bin/python testing_artifacts/check_persistence.py` after the live sequence for new-process persistence and edge inspection. Evidence JSON and console transcripts are in `testing_artifacts/`.']
(ROOT/'TEST_LOG.md').write_text('\n'.join(text)+'\n')
summary=['# Test Results','',f'{len(rows)} cases executed: {passed} passed, {failed} failed, {len(rows)-passed-failed} awaiting review. T000 is historical and excluded.','', 'Counts represent failing cases, with overlapping underlying bugs. Full inputs, exact outputs, graph snapshots, and severity rationale are in `TEST_LOG.md`. The shared DNS outage was retried successfully and both attempts remain recorded.', '', 'Main findings: confirmation state is shared across Flask clients; explicit agent speaker scope is dropped; malformed responses and failed services can crash request handling; historical and multi-entity retrieval misses stored facts; Ann and Anna merge; correction and duplicate writes pollute temporal history; reminder stub success is presented as an actual scheduled reminder.', '']
for sev in ['CRITICAL','HIGH','MEDIUM','LOW']:
    subset=[r for r in rows if r['result']=='FAIL' and r.get('severity')==sev]
    summary += [f'## {sev} — {len(subset)} failing cases','']
    categories=sorted(set(r.get('failure_category','unknown') for r in subset))
    for cat in categories:
        group=[r for r in subset if r.get('failure_category')==cat]
        summary.append(f'- **{cat}**: '+', '.join(r['id'] for r in group)+'. '+ '; '.join(dict.fromkeys(r['title'] for r in group))+'.')
    if not subset: summary.append('None.')
    summary.append('')
summary+=['## Reproducible failing test IDs','',', '.join(r['id'] for r in rows if r['result']=='FAIL'),'','Deterministic injected/double failures reproduce with the provided scripts. Live failures are observed results with preserved setup; reruns may vary with model output.','','## Recommended fixes for later','','- Extraction schema validation','- Entity resolution','- Temporal supersession and historical retrieval','- Agent response/tool validation and service-error handling','- Speaker propagation and confirmation session ownership','- Flask error presentation','- Reminder persistence/integration']
(ROOT/'TEST_RESULTS.md').write_text('\n'.join(summary)+'\n')
print(len(rows),passed,failed)
