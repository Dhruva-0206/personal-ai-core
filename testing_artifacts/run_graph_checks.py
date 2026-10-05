"""Controlled graph doubles expose lookup semantics without live services."""
import sys,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import retrieval, graph_engine
rows=[]
class Session:
    def __init__(self,records): self.records=records; self.queries=[]
    def run(self,q,**kw): self.queries.append(q); return self.records
def check(i,title,setup,expected,actual,ok,category='retrieval',severity='HIGH'):
    rows.append(dict(id=f'T{i:03}',title=title,category='Controlled graph semantics',setup=setup,expected=expected,actual=actual,result='PASS' if ok else 'FAIL',failure_category=None if ok else category,severity=None if ok else severity))
with patch.object(retrieval,'match_question_to_attribute',return_value='city'):
    s=Session([{'entity':'User','value':'Pune','created_at':'3','superseded_at':'4'},{'entity':'User','value':'Melbourne','created_at':'2','superseded_at':'3'},{'entity':'User','value':'Sydney','created_at':'1','superseded_at':'2'}])
    a=retrieval.historical_state_lookup(s,'test','Where did I live before Pune?')
    check(65,'Historical lookup with multiple past states','Sydney -> Melbourne -> Pune -> Mumbai; ask before Pune','Melbourne',a,a is not None and a['value']=='Melbourne')
    s=Session([{'entity':'Rahul','value':'Canberra','created_at':'1','superseded_at':'2'}])
    a=retrieval.historical_state_lookup(s,'test','Where did I live before Melbourne?')
    check(66,'Historical lookup wrong subject','Only Rahul has superseded city Canberra; ask my history','No confident User history match',a,a is None)
    s=Session([{'entity':'Rahul','value':'Canberra'}])
    a=retrieval.direct_state_lookup(s,'test','Where do I live?')
    check(67,'Direct lookup wrong subject','Only Rahul has an active city; ask my city','No confident User match',a,a is None)
    s=Session([{'entity':'User','value':'Adelaide'},{'entity':'Rahul','value':'Canberra'}])
    a=retrieval.direct_state_lookup(s,'test','Where do I live?')
    check(68,'Direct lookup with distinct subjects','User Adelaide; Rahul Canberra; ask my city','User Adelaide direct match',a,a is not None and a['entity']=='User' and a['value']=='Adelaide')
s=Session([])
graph_engine.create_state(s,'test','User','city','Mumbai','episode',[1.0])
check(69,'Supersession links immediate predecessor','Sydney -> Melbourne -> Pune -> Mumbai','Mumbai SUPERSEDES only Pune; prior chain preserved',{'queries':s.queries},'old.active = false' not in s.queries[1],category='supersession')
class Result:
    def single(self):return {'name':'Ann'}
class SubstringSession:
    def __init__(self):self.calls=0
    def run(self,q,**kw):
        self.calls+=1
        if self.calls==1:
            class Empty:
                def single(self):return None
            return Empty()
        return Result()
s=SubstringSession(); a=graph_engine.resolve_entity(s,'test','Anna',[0,1])
check(70,'Substring merges distinct similar names','Existing Ann; incoming Anna with distinct vector','Anna remains distinct',a,a=='Anna',category='entity resolution')
Path(__file__).with_name('graph_results.json').write_text(json.dumps(rows,indent=2))
print(json.dumps(rows,indent=2))
