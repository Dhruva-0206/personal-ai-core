"""Deterministic fault injection; patches are process-local test fixtures only."""
import sys, json, io, contextlib
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import agent, skills, extraction, llm_client, graph_engine, retrieval
with patch.object(graph_engine,'ensure_schema'):
    import web_app
web_app.app.config['TESTING']=False
web_app.app.logger.disabled=True
rows=[]
def test(i,title,setup,expected,fn,check,category='Reliability',failure='agent reasoning',severity='HIGH'):
    try:
        actual=fn(); ok=check(actual)
    except Exception as e:
        actual=type(e).__name__+': '+str(e); ok=False
    rows.append(dict(id=f'T{i:03}',title=title,category=category,setup=setup,expected=expected,actual=actual,result='PASS' if ok else 'FAIL',failure_category=None if ok else failure,severity=None if ok else severity))
    Path(__file__).with_name('isolated_results.json').write_text(json.dumps(rows,indent=2,default=str))
def response(content='Done',calls=None):
    return NS(choices=[NS(finish_reason='tool_calls' if calls is not None else 'stop',message=NS(content=content,tool_calls=calls))])
def tool(name,args): return NS(id='test-'+name,function=NS(name=name,arguments=args))
def scripted(responses,question='test'):
    fake=NS(chat=NS(completions=NS(create=Mock(side_effect=responses))))
    with patch.object(llm_client,'get_client',return_value=fake): return agent.handle_request(question)
def route(path,data=None, pending=None, side_effect=None):
    agent.pending_confirmation=pending
    with patch.object(web_app,'_recent_episodes',return_value=[]),patch.object(agent,'handle_request',side_effect=side_effect) if side_effect else contextlib.nullcontext():
        c=web_app.app.test_client(); r=c.get(path) if data is None else c.post(path,data=data)
        return {'http':r.status_code,'body':r.get_data(as_text=True),'pending':agent.pending_confirmation}
for i,value in [(36,[]),(37,'not an object'),(38,{'entities':None}),(39,{'entities':[{}]}),(40,{'states':[{'entity':'User'}]})]:
    def f(value=value):
        with patch.object(llm_client,'extract_json',return_value=value):
            result=extraction.extract('I live in Sydney.')
            return result
    test(i,'Malformed extraction shape',repr(value),'Safe schema-valid fallback or explicit validation result',f,lambda a:isinstance(a,dict) and all(all(k in s for k in ('entity','attribute','value')) for s in a['states']) and all(e.get('name') for e in a['entities']),failure='extraction',severity='HIGH' if i in (39,40) else 'CRITICAL')
test(41,'Malformed tool JSON','Tool set_reminder arguments="{"','Controlled error without uncaught exception',lambda:scripted([response(calls=[tool('set_reminder','{')])]),lambda a: isinstance(a,str),failure='tool/skill safety',severity='CRITICAL')
test(42,'Unknown tool','Tool name delete_everything','Controlled rejection without uncaught exception',lambda:scripted([response(calls=[tool('delete_everything','{}')])]),lambda a:isinstance(a,str),failure='tool/skill safety',severity='CRITICAL')
test(43,'Missing tool argument','set_reminder {"text":"hello"}','Controlled validation error without crash',lambda:scripted([response(calls=[tool('set_reminder','{"text":"hello"}')])]),lambda a:isinstance(a,str),failure='tool/skill safety',severity='CRITICAL')
test(44,'Empty model choices','choices=[]','Controlled model error without crash',lambda:scripted([NS(choices=[])]),lambda a:isinstance(a,str),failure='model instability',severity='CRITICAL')
test(45,'Empty agent answer','stop; content=None','Nonempty user-facing response',lambda:scripted([response(None)]),lambda a:isinstance(a,str) and bool(a.strip()),failure='model instability',severity='MEDIUM')
test(46,'Nebius unavailable','chat raises RuntimeError("Injected Nebius unavailable")','Controlled error without crash',lambda:scripted([RuntimeError('Injected Nebius unavailable')]),lambda a:isinstance(a,str),failure='infrastructure',severity='CRITICAL')
def multi():
    with patch.object(skills,'run_skill',return_value={'status':'confirmed'}) as m:
        answer=scripted([response(calls=[tool('set_reminder','{"text":"one","time":"9"}'),tool('set_reminder','{"text":"two","time":"10"}')]),response('Done')])
        return {'answer':answer,'executed':m.call_count}
test(47,'Multiple model tool calls','Two distinct reminder calls','Both calls handled or explicit unsupported notice',multi,lambda a:a['executed']==2,failure='agent reasoning')
def failed_tool():
    with patch.object(skills,'run_skill',side_effect=RuntimeError('Injected tool failure')):
        return scripted([response(calls=[tool('query_memory','{"question":"home"}')])])
test(48,'Failed tool','query_memory raises RuntimeError','Controlled error without crash',failed_tool,lambda a:isinstance(a,str),failure='tool/skill safety',severity='CRITICAL')
test(49,'High stakes gate','run_skill(make_purchase, item=test, price=$1)','needs_confirmation without executing',lambda:skills.run_skill('make_purchase',item='test',price='$1'),lambda a:a['status']=='needs_confirmation',category='Skills/safety',failure='tool/skill safety',severity='CRITICAL')
def agent_gate():
    with patch.object(skills.REGISTRY['make_purchase'],'func') as m:
        answer=scripted([response(calls=[tool('make_purchase','{"item":"test","price":"$1"}')])])
        return {'answer':answer,'executions':m.call_count,'pending':agent.pending_confirmation}
test(50,'Agent initiated high stakes','Model calls make_purchase','Pending confirmation; zero executions',agent_gate,lambda a:a['executions']==0 and bool(a['pending']),category='Skills/safety',failure='tool/skill safety',severity='CRITICAL')
P={'name':'make_purchase','args':{'item':'test','price':'$1'},'description':'Test purchase'}
test(51,'UI cancellation','POST /confirm action=no with pending purchase','200 Cancelled; pending cleared',lambda:route('/confirm',{'action':'no'},P.copy()),lambda a:a['http']==200 and 'Cancelled.' in a['body'] and a['pending'] is None,category='UI',failure='UI')
test(52,'UI confirmation','POST /confirm action=yes with pending stub purchase','200 Confirmed; pending cleared',lambda:route('/confirm',{'action':'yes'},P.copy()),lambda a:a['http']==200 and 'Confirmed:' in a['body'] and a['pending'] is None,category='UI',failure='UI')
test(53,'No pending confirmation','POST /confirm action=yes; no pending','200 Nothing pending',lambda:route('/confirm',{'action':'yes'}),lambda a:a['http']==200 and 'Nothing pending' in a['body'],category='UI',failure='UI')
for i,path,field in [(54,'/log','text'),(55,'/ask','question')]:
    test(i,'Whitespace form',f'POST {path} {field}="  "','200 with visible error',lambda path=path,field=field:route(path,{field:'  '}),lambda a:a['http']==200 and 'message error' in a['body'],category='UI',failure='UI')
test(56,'UI Nebius error presentation','POST /ask; handler raises RuntimeError','Readable service error within app page',lambda:route('/ask',{'question':'hello'},side_effect=RuntimeError('Injected Nebius unavailable')),lambda a:a['http']!=500 and 'Personal AI Memory' in a['body'],category='UI',failure='UI',severity='HIGH')
def neo4j_down():
    with patch.object(web_app,'_recent_episodes',side_effect=RuntimeError('Injected Neo4j unavailable')):
        r=web_app.app.test_client().get('/'); return {'http':r.status_code,'body':r.get_data(as_text=True)}
test(57,'UI Neo4j error presentation','GET /; recent episodes raises service error','Readable service error within app page',neo4j_down,lambda a:a['http']!=500,category='UI',failure='infrastructure',severity='CRITICAL')
def other_session():
    agent.pending_confirmation=P.copy()
    with patch.object(web_app,'_recent_episodes',return_value=[]),patch.object(skills,'confirm_skill',return_value={'status':'confirmed'}) as m:
        r=web_app.app.test_client().post('/confirm',data={'action':'yes'})
        return {'http':r.status_code,'executed_by_unrelated_client':m.call_count}
test(58,'Confirmation session isolation','Purchase pending for client A; client B confirms','Client B cannot confirm client A action',other_session,lambda a:a['executed_by_unrelated_client']==0,category='Skills/safety',failure='tool/skill safety',severity='CRITICAL')
def lost_pending():
    agent.pending_confirmation=P.copy(); scripted([response('Hello')],'hello'); return agent.pending_confirmation
test(59,'Pending survives unrelated ask','Pending purchase then hello','Purchase remains available until confirmation/cancellation',lost_pending,lambda a:a==P,category='Skills/safety',failure='tool/skill safety',severity='MEDIUM')
def escaped():
    with patch.object(web_app,'_recent_episodes',return_value=[{'summary':'<script>alert(1)</script>','importance':0.5}]):
        return web_app.app.test_client().get('/').get_data(as_text=True)
test(60,'Memory HTML escaping','Stored summary contains script tag','Escaped text, no script element',escaped,lambda a:'<script>' not in a and '&lt;script&gt;' in a,category='UI',failure='UI',severity='CRITICAL')
def speaker_forward():
    with patch.object(retrieval,'retrieve',return_value=[]) as m:
        fake=NS(chat=NS(completions=NS(create=Mock(side_effect=[response(calls=[tool('query_memory','{"question":"home"}')]),response('Unknown')]))))
        with patch.object(llm_client,'get_client',return_value=fake): agent.handle_request('home',speaker='isolated-person')
        return {'retrieval_args':m.call_args.args,'retrieval_kwargs':m.call_args.kwargs}
test(61,'Agent speaker isolation','handle_request(home, speaker=isolated-person)','Retrieval receives isolated-person speaker',speaker_forward,lambda a:a['retrieval_kwargs'].get('speaker')=='isolated-person',category='Entity separation',failure='retrieval',severity='CRITICAL')
def normalize():
    with patch.object(llm_client,'extract_json',return_value={'entities':[{'name':'I'}],'states':[{'entity':'me','attribute':'city','value':'Sydney'}],'relations':[{'subject':'myself','object':'Rahul'}]}): return extraction.extract('I live in Sydney.')
test(62,'Self reference normalization','Model uses I/me/myself entity labels','All speaker references normalize to User; Rahul retained',normalize,lambda a:a['entities'][0]['name']=='User' and a['states'][0]['entity']=='User' and a['relations'][0]=={'subject':'User','object':'Rahul'},category='Entity separation',failure='entity resolution')
def read_only():
    with patch.object(retrieval,'retrieve',return_value=[]):return skills.run_skill('query_memory',question='unknown')
test(63,'Read only skill','query_memory with no results','No results, no confirmation',read_only,lambda a:a['summary'] is None and a['message']=='No results found.',category='Skills/safety',failure='tool/skill safety')
test(64,'Reversible reminder mechanism','set_reminder(text=test,time=9am)','Stub returns confirmed; no confirmation gate',lambda:skills.run_skill('set_reminder',text='test',time='9am'),lambda a:a['status']=='confirmed' and 'note' in a,category='Skills/safety',failure='tool/skill safety')
print(json.dumps([{k:v for k,v in r.items() if k not in ('actual','setup')} for r in rows],indent=2))
