"""Additional malformed extraction and tool argument tests."""
import sys,json
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import llm_client,extraction,skills,agent
rows=[]
def case(i,title,setup,expected,fn,check,failure='extraction',severity='HIGH'):
    try:a=fn();ok=check(a)
    except Exception as e:a=type(e).__name__+': '+str(e);ok=False
    rows.append(dict(id=f'T{i:03}',title=title,category='Injected reliability',setup=setup,expected=expected,actual=a,result='PASS' if ok else 'FAIL',failure_category=None if ok else failure,severity=None if ok else severity))
def malformed():
    fake=NS(chat=NS(completions=NS(create=Mock(return_value=NS(choices=[NS(message=NS(content='not JSON'))])))))
    with patch.object(llm_client,'get_client',return_value=fake):a=extraction.extract('I live in Sydney.')
    return {'parsed':a,'attempts':fake.chat.completions.create.call_count}
case(84,'Malformed JSON fallback','Two model responses: not JSON','2 attempts then schema-valid fallback',malformed,lambda a:a['attempts']==2 and a['parsed']['entities']==[])
def unavailable():
    fake=NS(chat=NS(completions=NS(create=Mock(side_effect=RuntimeError('Injected unavailable')))))
    with patch.object(llm_client,'get_client',return_value=fake):a=extraction.extract('I live in Sydney.')
    return {'parsed':a,'attempts':fake.chat.completions.create.call_count}
case(85,'Extraction service outage fallback','Two injected service exceptions','2 attempts then schema-valid fallback',unavailable,lambda a:a['attempts']==2 and a['parsed']['summary']=='I live in Sydney.')
def invalid_importance():
    with patch.object(llm_client,'extract_json',return_value={'importance':2.7}): return extraction.extract('hello')
case(86,'Importance range validation','Model returns importance=2.7','importance in [0,1] or controlled rejection',invalid_importance,lambda a:isinstance(a['importance'],(int,float)) and 0<=a['importance']<=1)
def invalid_summary():
    with patch.object(llm_client,'extract_json',return_value={'summary':{'invalid':'object'}}):return extraction.extract('hello')
case(87,'Summary type validation','Model returns object-valued summary','summary is string or controlled rejection',invalid_summary,lambda a:isinstance(a['summary'],str))
def empty_tool_calls():
    fake=NS(chat=NS(completions=NS(create=Mock(return_value=NS(choices=[NS(finish_reason='tool_calls',message=NS(content=None,tool_calls=[]))])))))
    with patch.object(llm_client,'get_client',return_value=fake):return agent.handle_request('hello')
case(88,'Empty tool-call list','finish_reason=tool_calls; tool_calls=[]','Controlled nonempty error response',empty_tool_calls,lambda a:isinstance(a,str) and bool(a),failure='model instability',severity='CRITICAL')
def args_array():
    fake=NS(chat=NS(completions=NS(create=Mock(return_value=NS(choices=[NS(finish_reason='tool_calls',message=NS(content=None,tool_calls=[NS(id='x',function=NS(name='set_reminder',arguments='[]'))]))])))))
    with patch.object(llm_client,'get_client',return_value=fake):return agent.handle_request('remind me')
case(89,'Tool args wrong JSON type','set_reminder arguments=[]','Controlled validation response',args_array,lambda a:isinstance(a,str),failure='tool/skill safety',severity='CRITICAL')
case(90,'High stakes invalid argument validation','make_purchase with missing item/price','Reject malformed action before offering confirmation',lambda:skills.run_skill('make_purchase'),lambda a:a.get('status')!='needs_confirmation',failure='tool/skill safety',severity='MEDIUM')
def failed_retry():
    fake=NS(chat=NS(completions=NS(create=Mock(side_effect=[NS(choices=[NS(finish_reason='stop',message=NS(content="I can't help with that question using the available tools",tool_calls=None))]),RuntimeError('Injected retry outage')]))))
    with patch.object(llm_client,'get_client',return_value=fake):return agent.handle_request('home')
case(91,'Retry outage','Refusal followed by service exception','Controlled error response',failed_retry,lambda a:isinstance(a,str),failure='infrastructure',severity='CRITICAL')
Path(__file__).with_name('extra_results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows,indent=2))
