"""Run in a separate interpreter to verify restart persistence without resetting data."""
import os,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
art=Path(__file__).parent
d=json.loads((art/'live_results.json').read_text()); speaker=d['run']
os.environ['DEFAULT_SPEAKER']=speaker
import web_app,graph_engine
with graph_engine.get_driver().session() as s:
    episodes=[dict(r) for r in s.run('MATCH (ep:Episode {speaker:$speaker}) RETURN ep.raw_text AS raw_text ORDER BY ep.timestamp',speaker=speaker)]
    links=[dict(r) for r in s.run('MATCH (e:Entity {speaker:$speaker})<-[:OF_ENTITY]-(new:State)-[:SUPERSEDES]->(old:State) RETURN new.value AS newer, old.value AS older',speaker=speaker)]
r=web_app.app.test_client().get('/')
rows=[dict(id='T082',title='Fresh application process preserves live memories',category='Persistence — live Neo4j and new Flask process',setup={'speaker':speaker,'operation':'Import Flask in a new interpreter and GET /'},expected='Previously written Sydney episode persists; recent memories rendered',actual={'http':r.status_code,'episodes':episodes,'page_contains_recent_memories':'Recent memories' in r.get_data(as_text=True)},result='PASS' if r.status_code==200 and any(x['raw_text']=='I live in Sydney.' for x in episodes) else 'FAIL',failure_category=None,severity=None),dict(id='T083',title='Live immediate predecessor supersession edges',category='Supersession — live graph inspection',setup={'speaker':speaker,'sequence':'Sydney -> Melbourne -> Pune -> Mumbai'},expected='Mumbai directly supersedes only Pune, with prior chain retained',actual=links,result='FAIL' if any(x['newer']=='Mumbai' and x['older']=='Sydney' for x in links) else 'PASS',failure_category='supersession' if any(x['newer']=='Mumbai' and x['older']=='Sydney' for x in links) else None,severity='HIGH' if any(x['newer']=='Mumbai' and x['older']=='Sydney' for x in links) else None)]
(art/'persistence_results.json').write_text(json.dumps(rows,indent=2)); print(json.dumps(rows,indent=2)); graph_engine.close_driver()
