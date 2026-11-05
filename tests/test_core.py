import io,json,re,stat,time,zipfile,copy
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import store as s,pipeline as p,acquisition as a
from backend.main import app
from backend.parsers import parse_file
from backend.engine import LightweightOntologyEngine,OpenOntologiesEngine

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'DATA',tmp_path/'state');s.DATA.mkdir()
    monkeypatch.setattr(s,'DB',s.DATA/'test.db')
    monkeypatch.setenv('ONTO_LOCAL_ROOTS',str(tmp_path))
    # Tests choose explicitly whether the external engine is configured.
    monkeypatch.delenv('ONTO_OPEN_ONTOLOGIES_BIN',raising=False); monkeypatch.delenv('ONTO_OPEN_ONTOLOGIES_SHA256',raising=False)
    s.init()
    with TestClient(app) as c: yield c,tmp_path
    # Wait for workers before fixture database is replaced.
    deadline=time.time()+10
    while p.ACTIVE and time.time()<deadline: time.sleep(.01)

def completed(client,id):
    for _ in range(500):
        state=client.get('/api/state',params={'source_id':id}).json()
        if state['jobs'] and state['jobs'][0]['status'] in ['complete','failed','cancelled','paused']: return state
        time.sleep(.01)
    raise AssertionError('Job did not finish')

def test_parsers():
    n,e,r,status=parse_file('service.py','def target():\n    pass\n\ndef source():\n    target()\n')
    assert status=='parsed' and len(n)==3
    assert r[0]['name']=='target' and r[0]['line']==5
    assert n[-1]['line']==4 and n[-1]['method']=='python-ast'
    n,e,r,status=parse_file('types.ts','export interface Payment { amount: number }\nexport function make() { return 1; }')
    assert {'Payment','make'} <= {x['name'] for x in n}
    n,e,r,status=parse_file('db.sql','CREATE TABLE orders (id INT, customer_id INT REFERENCES customers(id));')
    assert any(x['type']=='Column' for x in n) and r[0]['name']=='customers'
    n,e,r,status=parse_file('openapi.yaml','openapi: 3.1.0\npaths:\n  /refunds:\n    post:\n      responses: {}\n')
    assert any(x['name']=='POST /refunds' for x in n)
    with pytest.raises(ValueError): parse_file('broken.ts','export interface {')

def test_path_and_archive(tmp_path,monkeypatch):
    for path in ['../escape','/absolute','a/../../b','C:/evil','a\\evil']:
        with pytest.raises(a.AcquisitionError): a.safe_relative(path)
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('good.py','x=1')
        z.writestr('../escape','oops')
        info=zipfile.ZipInfo('link');info.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(info,'/tmp')
    buf.seek(0); blocked=a.extract_zip(buf,tmp_path/'out')
    assert len(blocked)==2 and (tmp_path/'out/good.py').read_text()=='x=1'
    assert not (tmp_path/'escape').exists()
    monkeypatch.setattr(a,'MAX_FILE',1)
    buf.seek(0);assert len(a.extract_zip(buf,tmp_path/'limited'))==3

def test_local_boundaries(env):
    c,root=env
    assert c.post('/api/sources',json={'name':'bad','kind':'local','location':'/etc'}).status_code==400
    assert c.post('/api/sources',json={'name':'bad','kind':'github','location':'https://evil.example/a/b'}).status_code==400
    assert c.post('/api/sources',json={'name':'good','kind':'local','location':str(root)},headers={'Origin':'https://evil.example'}).status_code==403

def test_end_to_end_auto_approval_and_immutability(env):
    c,root=env
    source=c.post('/api/sources',json={'name':'sample','kind':'sample'}).json()
    c.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'sample'})
    state=completed(c,source['id'])
    assert state['jobs'][0]['status']=='complete',state['jobs'][0]
    assert state['counts']['parsed']==8 and state['entity_count']>40
    assert len(state['proposals'])==3
    assert state['snapshot']['unresolved_references']>0
    # No review step: every proposal is accepted and the version is approved during analysis.
    assert {p['status'] for p in state['proposals']}=={'accepted'}
    ont=state['ontology'];approved=ont
    assert ont['status']=='approved' and {x['name'] for x in ont['concepts'] if x['layer']=='domain'}>={'Refund','Payment','Customer'}
    assert state['mappings'] and {m['status'] for m in state['mappings']}=={'auto-approved'}
    assert c.post('/api/proposals/'+state['proposals'][0]['id']+'/review',json={'action':'accept'}).status_code in (404,405)
    updated=copy.deepcopy(approved);updated['concepts'][0]['description']='Changed definition'
    draft=c.put('/api/ontologies/'+ont['id'],json=updated).json()
    assert draft['id']!=approved['id'] and draft['status']=='draft'
    assert s.get('ontology',approved['id'])['concepts'][0]['description']!='Changed definition'
    r=c.post('/api/engines/run',params={'source_id':source['id']},json={'operation':'demo'}).json()
    assert r['status']=='findings' and len(r['derived_facts'])==1 and r['after_change']['derived_facts']==[]
    r=c.post('/api/engines/run',params={'source_id':source['id']},json={'engine':'open-ontologies','operation':'infer'}).json()
    assert r['status']=='unavailable' and r['engine']=='OpenOntologiesEngine'
    g=c.get('/api/graph',params={'snapshot_id':state['snapshot']['id'],'type':'API','limit':2}).json()
    assert len(g['nodes'])==2 and all(n['type']=='API' for n in g['nodes']) and g['total']>=4
    entity=g['nodes'][0];assert entity['content_hash'] and entity['excerpt'] and entity['source_id']==source['id']
    g=c.get('/api/graph',params={'snapshot_id':state['snapshot']['id'],'neighbor':entity['id']}).json()
    assert any(n['id']==entity['id'] for n in g['nodes'])

def test_incremental_exclusions_and_stale_mappings(env):
    c,root=env;repo=root/'repo';repo.mkdir()
    (repo/'a.py').write_text('class Customer:\n    pass\n')
    (repo/'b.py').write_text('def old(): pass\n')
    (repo/'keep.py').write_text('def kept(): pass\n')
    (repo/'.env').write_text('SECRET=do-not-read')
    (repo/'.gitignore').write_text('ignored.py\n')
    (repo/'ignored.py').write_text('bad')
    (repo/'x.bin').write_bytes(b'\x00\xff')
    (repo/'link.py').symlink_to(repo/'a.py')
    src=c.post('/api/sources',json={'kind':'local','name':'local','location':str(repo)}).json()
    c.post('/api/sources/'+src['id']+'/analyse',json={'mode':'parsing'})
    st=completed(c,src['id']);sid=st['snapshot']['id']
    assert st['counts']['excluded']==3 and st['counts']['unsupported']==2
    with s.connect() as con:
        n=json.loads(con.execute("SELECT data FROM entities WHERE snapshot=? AND name='Customer'",(sid,)).fetchone()[0])
    m={'id':'map1','entity_id':n['id'],'path':'a.py','snapshot_id':sid,'stale':False};s.put('mapping',m,src['id'],sid)
    (repo/'a.py').write_text('class Customer:\n    name: str\n')
    (repo/'b.py').unlink();(repo/'new.py').write_text('class Added: pass\n')
    c.post('/api/sources/'+src['id']+'/analyse',json={'mode':'parsing'})
    st=completed(c,src['id']);assert st['snapshot']['changes']['modified']==1
    assert st['snapshot']['changes']['deleted']==1 and st['snapshot']['changes']['added']==1
    assert st['jobs'][0]['cache_hits']>=1 and st['mappings'][0]['stale']
    with s.connect() as con: assert con.execute("SELECT COUNT(*) FROM entities WHERE snapshot=? AND name='old'",(st['snapshot']['id'],)).fetchone()[0]==0

def test_persistent_checkpoint_resume(env,monkeypatch):
    c,root=env
    src=c.post('/api/sources',json={'kind':'sample','name':'sample'}).json()
    original=p.parse
    def pause_once(snapshot,job,source):
        j=s.get('job',job['id']);j['status']='paused';s.put('job',j,source['id'],snapshot['id'])
        raise InterruptedError('paused')
    monkeypatch.setattr(p,'parse',pause_once)
    c.post('/api/sources/'+src['id']+'/analyse',json={'mode':'parsing'})
    st=completed(c,src['id']);assert st['jobs'][0]['status']=='paused'
    assert st['counts']['pending']==8
    while p.ACTIVE: time.sleep(.01)
    monkeypatch.setattr(p,'parse',original)
    assert c.post('/api/jobs/'+st['jobs'][0]['id']+'/resume').status_code==200
    st=completed(c,src['id']);assert st['jobs'][0]['status']=='complete' and st['counts']['pending']==0

def test_engine_constraints_and_unsupported():
    eng=LightweightOntologyEngine();o={'id':'v1','concepts':[{'id':'Service','name':'Service','properties':[]},{'id':'APIService','name':'APIService','parent':'Service','properties':[{'name':'route','datatype':'string','required':True}]}],'relationships':[],'rules':[]}
    g={'snapshot':'s','nodes':[{'id':'x','name':'x','type':'APIService','properties':{}}],'edges':[]}
    r=eng.run('infer',o,g);assert r['findings'][0]['code']=='required' and r['derived_facts'][0]['type']=='Service'
    assert eng.run('unknown',o,g)['status']=='unsupported'
    assert eng.run('diff',o,g,previous={'concepts':[]})['derived_facts']
    assert OpenOntologiesEngine().run('infer',o,g)['status']=='unavailable'
    o['concepts'][0]['parent']='APIService'
    assert any(f['code']=='subclass_cycle' for f in eng.run('definition',o,g)['findings'])

def test_github_commit_and_credentials(tmp_path,monkeypatch):
    calls=[];token='secret-token-never-export';a.TOKENS['source']=token
    sha='a'*40;buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:z.writestr('root/pkg/a.py','def f(): pass')
    def handler(req):
        calls.append(req)
        if '/commits/' in req.url.path:return __import__('httpx').Response(200,json={'sha':sha})
        if '/zipball/' in req.url.path:
            assert req.url.path.endswith(sha)
            return __import__('httpx').Response(302,headers={'location':'https://codeload.github.com/test/repo/zip/'+sha})
        assert 'authorization' not in req.headers
        return __import__('httpx').Response(200,content=buf.getvalue())
    import httpx
    real=httpx.Client
    monkeypatch.setattr(a.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    src={'id':'source','location':'https://github.com/test/repo','reference':'feature/test','subdirectory':'pkg'}
    root,resolved,blocked=a.github_snapshot(src,tmp_path/'snapshot')
    assert resolved==sha and (root/'a.py').exists() and not blocked
    assert all(token not in str(r.url) for r in calls)
    assert calls[0].headers['authorization']=='Bearer '+token
    assert 'feature%2Ftest' in str(calls[0].url)
    for status in [401,403,404,429]:
        def failure(req):return httpx.Response(status,headers={'x-ratelimit-remaining':'0'})
        monkeypatch.setattr(a.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(failure),**kw))
        with pytest.raises(a.AcquisitionError) as e:a.github_snapshot(src,tmp_path/'failed')
        assert token not in str(e.value) and str(status) in str(e.value)
    a.TOKENS.clear()

def test_live_batches_cover_all_entities_and_consent(env,monkeypatch):
    c,root=env;repo=root/'many';repo.mkdir()
    for i in range(35):(repo/f'c{i}.py').write_text(f'class Customer{i}: pass\n')
    src=c.post('/api/sources',json={'kind':'local','name':'AI scope','location':str(repo)}).json()
    assert c.post('/api/sources/'+src['id']+'/analyse',json={'mode':'live'}).status_code==400
    seen=[]
    def mock_provider(evidence,job,label=''):
        seen.extend(n['id'] for n in evidence)
        return [{'name':'Group'+str(len(seen)),'description':'A test interpretation','evidence':[evidence[0]['id']],'conflicts':[]}]
    monkeypatch.setattr(p,'live_proposals',mock_provider)
    c.post('/api/sources/'+src['id']+'/analyse',json={'mode':'live','share_confirmed':True})
    st=completed(c,src['id'])
    assert st['jobs'][0]['status']=='complete' and len(seen)==35
    assert st['jobs'][0]['ai_offset']==35 and len(st['proposals'])==-(-35//p.DISCOVERY_BATCH)

def test_partial_results_and_cancellation(env):
    c,root=env;repo=root/'bad';repo.mkdir()
    (repo/'good.py').write_text('def ok(): pass\n')
    (repo/'broken.py').write_text('def broken(\n')
    src=c.post('/api/sources',json={'kind':'local','name':'partial','location':str(repo)}).json()
    c.post('/api/sources/'+src['id']+'/analyse',json={'mode':'parsing'})
    st=completed(c,src['id'])
    assert st['snapshot']['status']=='partial' and st['counts']['failed']==1 and st['counts']['parsed']==1
    rows=c.get('/api/files',params={'snapshot_id':st['snapshot']['id'],'status':'failed'}).json()
    assert 'SyntaxError' in rows['items'][0]['reason']
    assert c.post('/api/jobs/'+st['jobs'][0]['id']+'/pause').status_code==409

def test_new_snapshot_preserves_conceptual_model(env):
    c,root=env;src=c.post('/api/sources',json={'name':'sample','kind':'sample'}).json()
    c.post('/api/sources/'+src['id']+'/analyse',json={'mode':'sample'});st=completed(c,src['id'])
    proposal=st['proposals'][0];old=st['ontology']
    c.post('/api/sources/'+src['id']+'/analyse',json={'mode':'parsing'});st=completed(c,src['id'])
    assert st['ontology']['based_on']==old['id'] and st['ontology']['id']!=old['id'] and st['ontology']['status']=='approved'
    assert any(x['name']==proposal['name'] for x in st['ontology']['concepts'])
    assert s.get('ontology',old['id'])['status']=='approved'

def test_external_mapping_and_resource_failures(tmp_path,monkeypatch):
    import sys
    from backend.external import mapping,bounded_process,OpenOntologiesEngine as External
    ont={'id':'v','concepts':[{'id':'Service','name':'Service','properties':[{'name':'name','datatype':'string','required':True}]}],'relationships':[],'rules':[]}
    graph={'snapshot':'s','nodes':[{'id':'danger>','type':'Service','name':'Untrusted "name"','path':'a.py','properties':{},'content_hash':'abc','snapshot_id':'s'}],'edges':[]}
    ttl,shape,unsupported=mapping(ont,graph)
    assert 'danger%3E' in ttl and 'sh:minCount 1' in shape and 'content_hash' in ttl
    fake=tmp_path/'fake';fake.write_bytes(b'not an engine')
    monkeypatch.setenv('ONTO_OPEN_ONTOLOGIES_BIN',str(fake));monkeypatch.setenv('ONTO_OPEN_ONTOLOGIES_SHA256','wrong')
    assert External().run('infer',ont,graph)['status']=='unavailable'
    with pytest.raises(ValueError,match='output byte budget'):
        bounded_process([sys.executable,'-c','print("x"*100000)'],'',budget=100)
    with pytest.raises(ValueError,match='execution budget'):
        bounded_process([sys.executable,'-c','import time; time.sleep(1)'],'',timeout=.03)

def test_connection_never_persists_secret(env):
    c,root=env
    src=c.post('/api/sources',json={'kind':'github','name':'private','location':'https://github.com/owner/private'}).json()
    token='FAKE-secret-value-for-test'
    assert c.post('/api/sources/'+src['id']+'/connection',json={'token':token}).status_code==200
    assert token not in c.get('/api/state').text and token not in c.get('/api/settings').text
    assert token.encode() not in s.DB.read_bytes()
    assert c.delete('/api/sources/'+src['id']+'/connection').json()['connected'] is False
    assert src['id'] not in a.TOKENS

def test_explicit_rule_validation(env):
    from backend.models import Rule,Property
    from pydantic import ValidationError
    with pytest.raises(ValidationError):Rule(id='bad',operator='minimum',concept='Payment',property='amount',value='execute()')
    with pytest.raises(ValidationError):Property(name='name',datatype='string',minimum=0)
    engine=LightweightOntologyEngine()
    o={'id':'rules','concepts':[{'id':'Payment','name':'Payment','properties':[]}],'relationships':[],'rules':[{'id':'positive','operator':'minimum','concept':'Payment','property':'amount','value':0}]}
    g={'snapshot':'s','nodes':[{'id':'p','name':'Illustrative payment declaration','type':'Payment','properties':{'amount':-1}}],'edges':[]}
    assert engine.run('rules',o,g)['findings'][0]['code']=='rule'

def test_sources_acquired_inside_datastore_are_parsed(env):
    client,_=env; buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z: z.writestr('app/models.py','class Employee:\n    pass\n')
    source=client.post('/api/sources/upload',files={'file':('hr.zip',buf.getvalue(),'application/zip')}).json()
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'})
    state=completed(client,source['id'])
    assert state['counts']['parsed']==1 and state['counts']['excluded']==0

def test_graph_pages_start_with_most_connected_files(env):
    client,root=env; src=root/'ranked'; src.mkdir()
    (src/'a_notes.md').write_text('plain text without headings\n')
    (src/'z_service.py').write_text('def helper():\n    pass\n\ndef run():\n    helper()\n')
    source=client.post('/api/sources',json={'name':'ranked','kind':'local','location':str(src)}).json()
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'})
    state=completed(client,source['id'])
    g=client.get('/api/graph',params={'snapshot_id':state['snapshot']['id'],'limit':3}).json()
    assert {n['path'] for n in g['nodes']}=={'z_service.py'} and g['edges']

def test_cross_file_resolution_rules(env):
    client,root=env; src=root/'xfile'; (src/'app'/'hr').mkdir(parents=True)
    (src/'app'/'__init__.py').write_text(''); (src/'app'/'hr'/'__init__.py').write_text('')
    (src/'app'/'hr'/'utils.py').write_text('def get_holidays():\n    pass\n\ndef only_here():\n    pass\n')
    (src/'app'/'hr'/'leave.py').write_text(
        'import frappe\nfrom .utils import get_holidays\nfrom app.hr import utils\n\n'
        'class LeaveApplication:\n    def validate(self):\n        self.check()\n        get_holidays()\n        utils.only_here()\n'
        '        frappe.get_doc("Salary Slip")\n        frappe.get_doc({"doctype": "Salary Slip"})\n    def check(self):\n        pass\n')
    (src/'app'/'hr'/'slip.py').write_text('class SalarySlip:\n    def go(self):\n        only_here()\n')
    source=client.post('/api/sources',json={'name':'xfile','kind':'local','location':str(src)}).json()
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'})
    sid=completed(client,source['id'])['snapshot']['id']
    with s.connect() as c:
        edges={(json.loads(d)['excerpt'] if t=='imports' else json.loads(d)['name'],json.loads(d)['method']) for t,d in c.execute("SELECT type,data FROM edges WHERE snapshot=? AND type!='defines'",(sid,))}
        names={i:n for i,n in c.execute('SELECT id,name FROM entities WHERE snapshot=?',(sid,))}
        targets={json.loads(d)['name']:names[json.loads(d)['target']] for (d,) in c.execute("SELECT data FROM edges WHERE snapshot=? AND type='calls'",(sid,))}
    methods={m for _,m in edges}
    assert {'same-file-self-method','python-import','frappe-doctype-name','unique-global-name','python-module-path'}<=methods
    assert targets['self.check']=='check' and targets['get_holidays']=='get_holidays' and targets['utils.only_here']=='only_here'
    assert targets['Salary Slip']=='SalarySlip' and targets['only_here']=='only_here'
    # The engine still accepts every resolved edge under the technical relationship definitions.
    run=client.post(f'/api/engines/run?source_id={source["id"]}',json={'operation':'instances'}).json()
    assert not [f for f in run['findings'] if f.get('code','').startswith('relationship')]

def test_full_graph_is_compact_and_complete(env):
    client,root=env; src=root/'full'; src.mkdir()
    (src/'svc.py').write_text('def helper():\n    pass\n\ndef run():\n    helper()\n')
    source=client.post('/api/sources',json={'name':'full','kind':'local','location':str(src)}).json()
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'})
    sid=completed(client,source['id'])['snapshot']['id']
    g=client.get('/api/graph/full',params={'snapshot_id':sid}).json()
    assert g['node_fields'][:3]==['id','name','type'] and len(g['nodes'])==3
    assert sorted(e[2] for e in g['edges'])==['calls','defines','defines']
    assert client.get('/api/graph/full',params={'snapshot_id':'missing'}).status_code==404

def test_frappe_doctypes_become_concepts_with_relationships(env):
    client,root=env; app=root/'frappe'/'hr'/'doctype'
    def doctype(folder,body):
        (app/folder).mkdir(parents=True); (app/folder/(folder+'.json')).write_text(json.dumps(body,indent=1))
    doctype('leave_type',{'doctype':'DocType','name':'Leave Type','module':'HR','fields':[{'fieldname':'max_days','fieldtype':'Int'}]})
    doctype('leave_application',{'doctype':'DocType','name':'Leave Application','module':'HR','is_submittable':1,'fields':[
        {'fieldname':'employee','fieldtype':'Link','options':'Employee','reqd':1},{'fieldname':'leave_type','fieldtype':'Link','options':'Leave Type','reqd':1},
        {'fieldname':'s1','fieldtype':'Section Break'},{'fieldname':'half_day','fieldtype':'Check'},{'fieldname':'approver','fieldtype':'Link','options':'Employee'}]})
    doctype('leave_ledger',{'doctype':'DocType','name':'Leave Ledger','module':'HR','fields':[{'fieldname':'employee','fieldtype':'Link','options':'Employee'}]})
    (app/'leave_application'/'leave_application.py').write_text('class LeaveApplication:\n    def validate(self):\n        pass\n')
    source=client.post('/api/sources',json={'name':'frappe','kind':'local','location':str(root/'frappe')}).json()
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'})
    state=completed(client,source['id']); sid=state['snapshot']['id']
    with s.connect() as c:
        methods={json.loads(d)['method'] for (d,) in c.execute("SELECT data FROM edges WHERE snapshot=? AND type IN ('links','controller')",(sid,))}
    assert methods=={'frappe-doctype-link','frappe-controller'}
    ont=state['ontology']; assert ont['status']=='approved'
    concepts={x['name']:x for x in ont['concepts'] if x['layer']=='domain'}
    # Employee is defined outside the repository but referenced three times, so it is kept as a concept.
    assert set(concepts)=={'Leave Type','Leave Application','Leave Ledger','Employee'}
    la=concepts['Leave Application']
    assert [p['name'] for p in la['properties']]==['employee','leave_type','half_day','approver']
    assert la['properties'][0]['reference']=='Employee' and la['properties'][0]['required'] and la['properties'][2]['datatype']=='boolean'
    rels={r['id']:r for r in ont['relationships'] if r.get('kind')=='link'}
    assert rels['Leave Application.leave_type']['target_types']==[concepts['Leave Type']['id']]
    assert rels['Leave Application.employee']['target_types']==[concepts['Employee']['id']]
    assert len(la['evidence'])==2 and {m['concept_id'] for m in state['mappings']}>={la['id']}

def test_duplicate_concepts_merge_and_links_follow(env):
    client,root=env; src=root/'dupes'; src.mkdir(); (src/'a.py').write_text('class SalarySlip:\n    pass\n')
    source=client.post('/api/sources',json={'name':'dupes','kind':'local','location':str(src)}).json()
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'}); st=completed(client,source['id'])
    ont=copy.deepcopy(st['ontology']); entity=client.get('/api/graph',params={'snapshot_id':st['snapshot']['id'],'type':'Class'}).json()['nodes'][0]
    ont['concepts']+=[{'id':'c1','name':'SalarySlip','layer':'domain','evidence':[entity['id']]},{'id':'c2','name':'Salary Slip','layer':'domain','evidence':[]}]
    draft=client.put('/api/ontologies/'+ont['id'],json=ont).json()
    p.merge_duplicate_concepts(s.get('snapshot',draft['snapshot_id']),draft)
    names=[x['name'] for x in draft['concepts'] if x['layer']=='domain']
    assert names==['Salary Slip'] and draft['concepts'][-1]['aliases']==['SalarySlip'] and draft['concepts'][-1]['evidence']==[entity['id']]

def test_vue_components_link_to_python_methods_doctypes_and_components(env):
    client,root=env; app=root/'vueapp'
    (app/'hr'/'api').mkdir(parents=True); (app/'hr'/'api'/'__init__.py').write_text('def get_balance(employee):\n    pass\n')
    (app/'hr'/'leave').mkdir(); (app/'hr'/'leave'/'leave.json').write_text(json.dumps({'doctype':'DocType','name':'Leave Application','fields':[]}))
    (app/'frontend'/'src'/'components').mkdir(parents=True)
    (app/'frontend'/'src'/'components'/'Badge.vue').write_text('<template><span/></template>\n')
    (app/'frontend'/'src'/'components'/'LeaveForm.vue').write_text(
        '<template>\n  <Link doctype="Leave Application" />\n</template>\n<script setup>\nimport Badge from "@/components/Badge.vue"\n'
        'import { createResource } from "frappe-ui"\nconst DOCTYPE = "Leave Application"\n'
        'const balance = createResource({ url: "hr.api.get_balance", params: { doctype: DOCTYPE } })\nconst refresh = () => balance.reload()\n</script>\n')
    source=client.post('/api/sources',json={'name':'vue','kind':'local','location':str(app)}).json()
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'})
    state=completed(client,source['id']); sid=state['snapshot']['id']
    with s.connect() as c:
        names=dict(c.execute('SELECT id,name FROM entities WHERE snapshot=?',(sid,)))
        edges={(names[a],t,names[b],json.loads(d)['method']) for a,b,t,d in c.execute("SELECT source,target,type,data FROM edges WHERE snapshot=? AND type!='defines'",(sid,))}
        kinds={n:k for n,k in c.execute('SELECT name,type FROM entities WHERE snapshot=?',(sid,))}
    assert kinds['components/LeaveForm']=='Component' and kinds['refresh']=='Function'
    assert ('components/LeaveForm','calls','get_balance','frappe-whitelisted-method') in edges
    assert ('components/LeaveForm','uses','Leave Application','frappe-doctype-name') in edges
    assert ('components/LeaveForm','imports','frontend/src/components/Badge.vue','js-import-path') in edges
    concept=next(x for x in state['ontology']['concepts'] if x['name']=='Leave Application')
    assert any(names.get(e)=='components/LeaveForm' for e in concept['evidence'])

def test_java_types_spring_routes_tests_and_imports(env):
    client,root=env; app=root/'javaapp'; pkg=app/'src'/'main'/'java'/'com'/'shop'; tests=app/'src'/'test'/'java'/'com'/'shop'
    pkg.mkdir(parents=True); tests.mkdir(parents=True)
    (pkg/'Order.java').write_text('package com.shop;\npublic record Order(long id, int total) {}\n')
    (pkg/'BaseService.java').write_text('package com.shop;\npublic abstract class BaseService { protected void audit(String m) {} }\n')
    (pkg/'OrderService.java').write_text(
        'package com.shop;\nimport java.util.List;\n\npublic class OrderService extends BaseService {\n'
        '    public Order find(long id) { this.audit("find"); return new Order(id, 0); }\n}\n')
    (pkg/'OrderController.java').write_text(
        'package com.shop;\nimport org.springframework.web.bind.annotation.*;\n\n@RestController\n@RequestMapping("/orders")\n'
        'public class OrderController {\n    private final OrderService service = new OrderService();\n'
        '    @GetMapping("/{id}")\n    public Order get(@PathVariable long id) { return service.find(id); }\n'
        '    @RequestMapping(value = "/sync", method = RequestMethod.POST)\n    public void sync() {}\n}\n')
    (tests/'OrderServiceTest.java').write_text(
        'package com.shop;\nimport com.shop.OrderService;\nimport org.junit.jupiter.api.Test;\n\nclass OrderServiceTest {\n'
        '    @Test\n    void findsOrder() { new OrderService().find(1); }\n}\n')
    (pkg/'Broken.java').write_text('public class Broken {\n')
    source=client.post('/api/sources',json={'name':'java','kind':'local','location':str(app)}).json()
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'})
    sid=completed(client,source['id'])['snapshot']['id']
    with s.connect() as c:
        names=dict(c.execute('SELECT id,name FROM entities WHERE snapshot=?',(sid,)))
        edges={(names[a],t,names[b],json.loads(d)['method']) for a,b,t,d in c.execute("SELECT source,target,type,data FROM edges WHERE snapshot=? AND type!='defines'",(sid,))}
        kinds={n:k for n,k in c.execute('SELECT name,type FROM entities WHERE snapshot=?',(sid,))}
        status={p_:st for p_,st in c.execute('SELECT path,status FROM files WHERE snapshot=?',(sid,))}
    assert kinds['Order']=='Class' and kinds['find']=='Function' and kinds['findsOrder']=='Test'
    assert kinds['GET /orders/{id}']=='API' and kinds['POST /orders/sync']=='API'
    assert ('GET /orders/{id}','implements','get','tree-sitter') in edges
    assert ('OrderService','inherits','BaseService','unique-global-name') in edges
    assert ('find','calls','Order','unique-global-name') in edges
    assert ('findsOrder','calls','OrderService','unique-global-name') in edges
    assert ('src/test/java/com/shop/OrderServiceTest.java','imports','src/main/java/com/shop/OrderService.java','java-import-path') in edges
    assert status['src/main/java/com/shop/Broken.java']=='failed' and status['src/main/java/com/shop/Order.java']=='parsed'

def test_build_files_configuration_and_multi_document_yaml():
    pom=('<?xml version="1.0"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0">\n  <parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>3.2.0</version></parent>\n'
         '  <artifactId>shop</artifactId>\n  <properties><h2.version>2.2</h2.version></properties>\n  <dependencyManagement><dependencies><dependency><groupId>x</groupId><artifactId>bom</artifactId></dependency></dependencies></dependencyManagement>\n'
         '  <dependencies>\n    <dependency><groupId>com.h2database</groupId><artifactId>h2</artifactId><version>${h2.version}</version><scope>runtime</scope></dependency>\n  </dependencies>\n'
         '  <build><plugins><plugin><artifactId>maven-surefire-plugin</artifactId></plugin></plugins></build>\n</project>\n')
    n,e,r,status=parse_file('pom.xml',pom)
    deps={x['name']:x['properties'] for x in n if x['type']=='Dependency'}
    assert status=='parsed' and n[0]['properties']['project']=='org.springframework.boot:shop' and 'x:bom' not in deps
    assert deps['com.h2database:h2']=={'group':'com.h2database','artifact':'h2','version':'2.2','scope':'runtime'}
    assert deps['org.springframework.boot:spring-boot-starter-parent']['scope']=='parent' and deps['org.apache.maven.plugins:maven-surefire-plugin']['scope']=='plugin'
    assert next(x for x in n if x['name']=='com.h2database:h2')['line']==8 and n[1]['method']=='maven-pom'
    n,e,r,status=parse_file('build.gradle',"plugins {\n  id 'org.springframework.boot' version '3.2.0'\n}\ngroup = 'com.shop'\next.lib = '1.4'\ndependencies {\n  implementation 'org.lib:core:${lib}'\n  testImplementation(\"org.junit:junit:4\")\n}\n")
    deps={x['name']:x['properties'] for x in n if x['type']=='Dependency'}
    assert n[0]['properties']['group']=='com.shop' and deps['org.springframework.boot']=={'group':'org.springframework.boot','artifact':'','version':'3.2.0','scope':'plugin'}
    assert deps['org.lib:core']['version']=='1.4' and deps['org.junit:junit']['scope']=='testImplementation'
    n,e,r,status=parse_file('src/main/resources/application-mysql.properties','# db\nspring.datasource.url=jdbc:mysql://x\nspring.datasource.password=hunter2\nlong.value = a \\\n  b\n')
    config=n[1]
    assert config['type']=='Configuration' and config['properties']['profile']=='mysql'
    assert config['properties']['settings']=={'spring.datasource.url':'jdbc:mysql://x','spring.datasource.password':'***','long.value':'a b'} and 'hunter2' not in json.dumps(n)
    n,e,r,status=parse_file('application.yml','spring:\n  datasource:\n    password: s3cret\nserver:\n  port: 8080\n---\nspring:\n  config:\n    activate:\n      on-profile: prod\nserver:\n  port: 80\n')
    settings=n[1]['properties']['settings']
    assert status=='parsed' and settings['server.port']=='8080' and settings['[prod] server.port']=='80' and settings['spring.datasource.password']=='***'
    assert parse_file('k8s/db.yml','kind: Service\n---\nkind: Deployment\n')[3]=='unsupported'
    assert parse_file('openapi.yaml','openapi: 3.1.0\npaths: {}\n---\n')[3]=='parsed'
    assert parse_file('checkstyle.xml','<module/>')[3]=='unsupported' and parse_file('script.kts','println(1)')[3]=='unsupported'

def test_domain_relationships_from_references_statements_and_inheritance(env):
    snapshot={'id':'snap_rel','source_id':'src_rel'}
    concept=lambda name,props=(),evidence=():{'id':'c_'+name,'name':name,'layer':'domain','aliases':[],'properties':list(props),'evidence':list(evidence),'description':''}
    ont={'concepts':[concept('Pet',[{'name':'owner','fieldtype':'link','reference':'Owner'}]),concept('Owner',evidence=['class_owner','ctl_owner']),concept('Person',evidence=['class_person']),concept('Visit'),concept('Vet')],'relationships':[]}
    s.put('proposal',{'id':'p1','name':'Pet','status':'accepted','relationship_proposals':['Pet has many Visits (one-to-many via pet_id)','Pet belongs to Owner','Visits are recorded for pets']},'src_rel','snap_rel')
    s.put('proposal',{'id':'p2','name':'Vet','status':'pending','relationship_proposals':['Vet treats Pet']},'src_rel','snap_rel')
    with s.connect() as c:
        for id,name in (('class_owner','Owner'),('ctl_owner','OwnerController'),('class_person','Person'),('class_base','BaseController')):
            c.execute('INSERT INTO entities VALUES(?,?,?,?,?,?)',('snap_rel',id,'Class',name,'x.java','{}'))
        for a,b in (('class_owner','class_person'),('ctl_owner','class_base')):
            c.execute('INSERT INTO edges VALUES(?,?,?,?,?,?)',('snap_rel',a+b,a,b,'inherits','{}'))
    assert p.concept_relationships(snapshot,ont)==3
    found={(r['source_types'][0][2:],r['label'],r['target_types'][0][2:],r['kind']) for r in ont['relationships']}
    # The reference wins over the equivalent statement; a pending proposal and a sentence not led by the concept add nothing.
    assert found=={('Pet','owner','Owner','link'),('Pet','has many','Visit','declared'),('Owner','extends','Person','declared')}
    assert p.concept_relationships(snapshot,ont)==3 and len(ont['relationships'])==3

def test_bare_call_names_do_not_resolve_across_languages(env):
    client,root=env; app=root/'mixed'; app.mkdir()
    (app/'helpers.py').write_text('def inject(x):\n    pass\n')
    (app/'Panel.vue').write_text('<script setup>\nconst load = () => inject("x")\n</script>\n')
    source=client.post('/api/sources',json={'name':'mixed','kind':'local','location':str(app)}).json()
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'})
    sid=completed(client,source['id'])['snapshot']['id']
    with s.connect() as c: assert c.execute("SELECT COUNT(*) FROM edges WHERE snapshot=? AND type='calls'",(sid,)).fetchone()[0]==0

def test_frappe_metadata_and_records_link_to_doctypes(env):
    client,root=env; app=root/'meta'
    def put(rel,body): (app/rel).parent.mkdir(parents=True,exist_ok=True); (app/rel).write_text(json.dumps(body))
    put('hr/doctype/attendance/attendance.json',{'doctype':'DocType','name':'Attendance','fields':[{'fieldname':'status','fieldtype':'Select','reqd':1}]})
    put('hr/doctype/attendance/test_records.json',[{'doctype':'Attendance','status':'Present'},{'doctype':'Attendance','status':'Absent'}])
    put('hr/number_card/late/late.json',{'doctype':'Number Card','name':'Late Entry','document_type':'Attendance','function':'Count'})
    put('hr/notification/absent/absent.json',{'doctype':'Notification','name':'Absent Alert','document_type':'Attendance','event':'Submit','condition':'doc.status == "Absent"'})
    put('hr/dashboard/att/att.json',{'doctype':'Dashboard','name':'Attendance','cards':[{'card':'Late Entry'}]})
    put('package.json',{'name':'tooling','private':True})
    source=client.post('/api/sources',json={'name':'meta','kind':'local','location':str(app)}).json()
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'})
    state=completed(client,source['id']); sid=state['snapshot']['id']
    assert state['counts']['unsupported']==1
    with s.connect() as c:
        names=dict(c.execute('SELECT id,name FROM entities WHERE snapshot=?',(sid,)))
        kinds=dict(c.execute('SELECT name,type FROM entities WHERE snapshot=?',(sid,)))
        edges={(names[a],t,names[b]) for a,b,t in c.execute("SELECT source,target,type FROM edges WHERE snapshot=? AND type!='defines'",(sid,))}
    assert kinds['Number Card: Late Entry']=='Metadata' and kinds['Attendance: Present']=='Instance'
    assert {('Number Card: Late Entry','uses','Attendance'),('Notification: Absent Alert','uses','Attendance'),
            ('Dashboard: Attendance','uses','Number Card: Late Entry'),('Attendance: Absent','instance_of','Attendance')}<=edges
    concept=next(x for x in state['ontology']['concepts'] if x['name']=='Attendance')
    assert {'Notification: Absent Alert','Attendance: Present'}<={names.get(e) for e in concept['evidence']}

PINNED_BINARY=Path(__file__).resolve().parents[1]/'data'/'tools'/'open-ontologies-aarch64-apple-darwin'

def semantic_fixture(client,root):
    app=root/'sem'
    def put(rel,body): (app/rel).parent.mkdir(parents=True,exist_ok=True); (app/rel).write_text(json.dumps(body))
    put('hr/doctype/leave_type/leave_type.json',{'doctype':'DocType','name':'Leave Type','module':'HR','fields':[{'fieldname':'max_days','fieldtype':'Int'}]})
    put('hr/doctype/leave_application/leave_application.json',{'doctype':'DocType','name':'Leave Application','module':'HR','is_submittable':1,'fields':[
        {'fieldname':'leave_type','fieldtype':'Link','options':'Leave Type','reqd':1},{'fieldname':'status','fieldtype':'Select','options':'Open\nApproved','reqd':1},
        {'fieldname':'days','fieldtype':'Float'},{'fieldname':'company','fieldtype':'Link','options':'Company','reqd':1,'default':'Main'}]})
    put('hr/doctype/leave_application/test_records.json',[{'doctype':'Leave Application','name':'LA-1','leave_type':'Casual','status':'Maybe','days':2},{'doctype':'Leave Application','name':'LA-2','days':'abc'}])
    put('hr/doctype/unused_row/unused_row.json',{'doctype':'DocType','name':'Unused Row','module':'HR','istable':1,'fields':[{'fieldname':'note','fieldtype':'Data'}]})
    put('hr/notification/approved/approved.json',{'doctype':'Notification','name':'Leave Type Changed','document_type':'Leave Type','event':'Submit'})
    put('hr/dashboard/leave/leave.json',{'doctype':'Dashboard','name':'Leave','cards':[{'card':'Missing Card'}]})
    put('hr/number_card/open/open.json',{'doctype':'Number Card','name':'Open Leaves','document_type':'Leave Application','function':'Count'})
    put('hr/report/leave_summary/leave_summary.json',{'doctype':'Report','name':'Leave Summary','ref_doctype':'Leave Application','report_type':'Script Report'})
    put('hr/workspace/hr/hr.json',{'doctype':'Workspace','name':'HR','links':[{'link_type':'Report','link_to':'General Ledger'}]})
    put('hr/insights/hr/hr.json',{'doctype':'Insights Workbook','name':'hr','charts':{'c1':{'name':'c1','chart_name':'Internal chart'}},'dashboards':{'d':{'items':[{'chart':'c1'}]}}})
    source=client.post('/api/sources',json={'name':'sem','kind':'local','location':str(app)}).json()
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'})
    completed(client,source['id']); return source['id']

def test_semantic_export_and_query_guard(env):
    from backend import semantic
    client,root=env; sid=semantic_fixture(client,root)
    model=client.get('/api/semantic/export',params={'source_id':sid,'scope':'model'})
    assert model.status_code==200 and model.headers['content-type'].startswith('text/turtle') and 'sem-model.ttl' in model.headers['content-disposition']
    assert 'rdfs:subClassOf oe:SubmittableDocument' in model.text and 'rdfs:subClassOf oe:ChildTable' in model.text and 'oe:linkTarget "Company"' in model.text
    assert 'urn:ontoforge:record:' not in model.text
    data=client.get('/api/semantic/export',params={'source_id':sid,'scope':'data'}).text
    assert 'record:Leave%20Application%2FLA-1' in data and '"2"^^xsd:decimal' in data
    assert 'oe:evidenceOf' in client.get('/api/semantic/export',params={'source_id':sid,'scope':'full'}).text
    shapes=client.get('/api/semantic/export',params={'source_id':sid,'scope':'shapes'}).text
    assert 'sh:in ( "Open" "Approved" )' in shapes and shapes.count('sh:minCount 1')==2  # company has a default, so it is not required
    assert client.get('/api/semantic/export',params={'source_id':sid,'scope':'bogus'}).status_code==400
    for bad in ['INSERT DATA { <a> <b> <c> }','SELECT * WHERE { SERVICE <http://example.org/> { ?s ?p ?o } }','DESCRIBE','hello']:
        with pytest.raises(ValueError): semantic.check_query(bad if bad!='DESCRIBE' else 'LOAD <http://x>')
    assert semantic.check_query('SELECT ?add WHERE { ?add rdfs:label "INSERT" }').startswith('PREFIX')
    assert client.get('/api/semantic').json()['available'] is False
    assert client.post('/api/semantic/query',params={'source_id':sid},json={'query':'SELECT * WHERE { ?s ?p ?o }'}).status_code==409

@pytest.mark.skipif(not PINNED_BINARY.is_file(),reason='Pinned Open Ontologies binary is not installed in data/tools')
def test_open_ontologies_checks_validation_inference_and_query(env,monkeypatch):
    from backend.external import MAC_ARM_SHA
    monkeypatch.setenv('ONTO_OPEN_ONTOLOGIES_BIN',str(PINNED_BINARY)); monkeypatch.setenv('ONTO_OPEN_ONTOLOGIES_SHA256',MAC_ARM_SHA)
    client,root=env; sid=semantic_fixture(client,root)
    run=lambda op:client.post('/api/engines/run',params={'source_id':sid},json={'engine':'open-ontologies','operation':op}).json()
    checks=run('checks'); codes={f['code'] for f in checks['findings']}
    assert checks['status']=='findings' and {'notification-event-not-submittable','orphan-child-table','missing-metadata-reference','link-to-undefined-doctype','navigation-outside-repository'}<=codes
    missing=[f['message'] for f in checks['findings'] if f['code']=='missing-metadata-reference']
    assert missing==['Dashboard: Leave refers to Number Card "Missing Card", which is not defined in this repository.']
    error=next(f for f in checks['findings'] if f['code']=='notification-event-not-submittable')
    assert error['severity']=='error' and 'Leave Type' in error['message'] and error['evidence']
    shacl=run('instances'); messages=' '.join(f['message'] for f in shacl['findings'])
    assert shacl['validation']['records_checked']==2 and 'not one of its Select options' in messages and 'leave_type is missing' in messages and 'wrong type' in messages
    assert all(f['concept'] and f['evidence'] for f in shacl['findings'])
    infer=run('infer'); types={(f['subject'],f['type']) for f in infer['derived_facts']}
    assert ('Leave Application/LA-1','Submittable document') in types and ('Leave Application/LA-1','HR module') in types
    assert ('Leave Type/Casual','Leave Type') in types  # typed by the Link field's rdfs:range
    q=client.post('/api/semantic/query',params={'source_id':sid},json={'query':'SELECT ?label WHERE { ?c oe:doctypeName ?name ; rdfs:label ?label } ORDER BY ?label'}).json()
    assert [r['label']['value'] for r in q['rows']]==['Leave Application','Leave Type','Unused Row'] and q['rows'][0]['label']['kind']=='literal'
    assert client.post('/api/semantic/query',params={'source_id':sid},json={'query':'DELETE WHERE { ?s ?p ?o }'}).status_code==400

def test_job_activity_log_and_progress(env):
    client,root=env; src=root/'logged'; src.mkdir()
    for i in range(3): (src/f'm{i}.py').write_text(f'def f{i}():\n    pass\n')
    (src/'bad.py').write_text('def broken(:\n')
    source=client.post('/api/sources',json={'name':'logged','kind':'local','location':str(src)}).json()
    job=client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'}).json()
    completed(client,source['id'])
    r=client.get(f'/api/jobs/{job["id"]}/logs').json(); lines=r['logs']; text=[l['message'] for l in lines]
    assert r['job']['status']=='complete' and r['job']['stage']=='Complete' and 'started_at' in r['job']['progress']
    assert text[0].startswith('Started parsing analysis of logged')
    for expected in ['Stage started: Inventory','Inventory complete: 4 files','Stage started: Parsing','Parsing complete: 3 parsed · 1 failed','Knowledge graph built','Analysis complete']:
        assert any(t.startswith(expected) for t in text),expected
    assert any(l['level']=='warning' and 'bad.py' in l['message'] for l in lines)
    assert [l['id'] for l in lines]==sorted(l['id'] for l in lines)
    later=client.get(f'/api/jobs/{job["id"]}/logs',params={'after':lines[2]['id']}).json()['logs']
    assert later[0]['id']==lines[3]['id'] and len(later)==len(lines)-3
    assert client.post(f'/api/jobs/{job["id"]}/cancel').status_code==200
    assert client.get(f'/api/jobs/{job["id"]}/logs',params={'after':lines[-1]['id']}).json()['logs'][0]['message'].startswith('Cancel requested')

def test_ai_batch_timeouts_are_retried(env,monkeypatch):
    from backend import ai
    calls=[]
    def flaky(batch,context=None,tier=None):
        calls.append(1)
        if len(calls)<3: raise ValueError('Model generation timed out. Increase ONTO_AI_TIMEOUT_SECONDS')
        return []
    monkeypatch.setattr(ai,'generate_proposals',flaky); monkeypatch.setattr(p.time,'sleep',lambda _: None)
    job={'id':'job_x','stage':'AI generation','status':'running'}; s.put('job',job)
    assert p.live_proposals([{'id':'e','name':'Customer','excerpt':'class Customer'}],job,'Batch 1/1')==[] and len(calls)==3
    assert sum('Retrying' in l['message'] for l in s.logs('job_x'))==2

def test_domain_ontology_is_visible_while_ai_stage_runs(env,monkeypatch):
    c,root=env; app=root/'early'/'hr'/'doctype'/'leave_type'; app.mkdir(parents=True)
    (app/'leave_type.json').write_text(json.dumps({'doctype':'DocType','name':'Leave Type','module':'HR','fields':[]}))
    (root/'early'/'policy.py').write_text('class Policy:\n    pass\n')
    src=c.post('/api/sources',json={'kind':'local','name':'early','location':str(root/'early')}).json()
    seen=[]
    def provider(evidence,job,label=''):
        # While the first AI batch runs, DocType concepts are already in the draft ontology.
        ont=s.records('ontology',src['id'])[0]; seen.append((ont['status'],sorted(x['name'] for x in ont['concepts'] if x['layer']=='domain')))
        return [{'name':'Leave Policy','description':'Rules for leave','evidence':[evidence[0]['id']],'conflicts':[]}]
    monkeypatch.setattr(p,'live_proposals',provider)
    c.post('/api/sources/'+src['id']+'/analyse',json={'mode':'live','share_confirmed':True})
    st=completed(c,src['id'])
    assert seen and seen[0]==('draft',['Leave Type'])
    assert st['ontology']['status']=='approved' and {'Leave Type','Leave Policy'}<={x['name'] for x in st['ontology']['concepts'] if x['layer']=='domain'}
    assert all(x['status']=='accepted' for x in st['proposals'])


JPA_FILES={
 'model/BaseEntity.java':'package demo.model;\nimport jakarta.persistence.*;\n@MappedSuperclass\npublic class BaseEntity { @Id private Integer id; }\n',
 'model/Person.java':'package demo.model;\nimport jakarta.persistence.*;\nimport jakarta.validation.constraints.*;\n/** A person known to the clinic. */\n@MappedSuperclass\npublic class Person extends BaseEntity {\n  @NotBlank @Size(max = 30) private String firstName;\n}\n',
 'owner/Owner.java':'package demo.owner;\nimport jakarta.persistence.*;\nimport jakarta.validation.constraints.*;\nimport demo.model.Person;\n/** Simple JavaBean domain object representing an owner. */\n@Entity @Table(name = "owners")\npublic class Owner extends Person {\n  @NotBlank @Pattern(regexp = "\\\\d{10}") private String telephone;\n  @OneToMany(cascade = CascadeType.ALL) private List<Pet> pets;\n}\n',
 'owner/PetStatus.java':'package demo.owner;\npublic enum PetStatus { ACTIVE, DECEASED }\n',
 'owner/Pet.java':'package demo.owner;\nimport jakarta.persistence.*;\n@Entity\npublic class Pet extends BaseEntity {\n  private String name;\n  @Enumerated private PetStatus status;\n  @ManyToOne private Owner owner;\n}\n',
 'owner/PetValidator.java':'package demo.owner;\npublic class PetValidator {\n  public void validate(Pet pet, Errors errors) {\n    if (pet.getName() == null) { errors.rejectValue("name", "required"); }\n  }\n}\n',
 'owner/PetService.java':'package demo.owner;\npublic class PetService {\n  public void retire(Pet pet) { publishEvent(new PetRetired(pet)); }\n}\n',
}

def mock_model(monkeypatch,calls,wrong_statement=None):
    from backend import ai, generative as g
    def fake(system,user,output_model,tier='cheap',context=None,max_tokens=None):
        calls.append((output_model.__name__,tier)); cited=re.findall(r'\[([^\]\s]+)\]',user)
        data={'Definitions':{'items':[{'concept':'Owner','definition':'A client of the clinic who brings pets for care.','synonyms':['Client'],'confidence':.9},
                                      {'concept':'Ghost','definition':'Not a real concept in this model.','confidence':.5}]},
              'Hierarchy':{'categories':[{'name':'Party','definition':'A person or organisation the clinic deals with.'}],
                           'links':[{'concept':'Person','parent':'Party'},{'concept':'Party','parent':'Owner'},{'concept':'Owner','parent':'Pet'},{'concept':'Pet','parent':'Person'}]},
              'Relations':{'relations':[{'source':'Owner','target':'Pet','field':'pets','label':'owns','description':'An owner owns pets','cardinality':'many'},
                                        {'source':'Pet','target':'Owner','field':'nope','label':'bad link'}]},
              'Rules':{'rules':[{'concept':'Pet','property':'name','operator':'required','text':'A pet must have a name','evidence':cited[:1]},
                                {'concept':'Pet','property':'colour','operator':'required','text':'A pet must have a colour','evidence':cited[:1]},
                                {'concept':'Pet','operator':'described','text':'A new pet needs a type','evidence':[]}]},
              'Lifecycles':{'lifecycles':[{'concept':'Pet','field':'status','states':['ACTIVE','DECEASED'],'initial':'ACTIVE','transitions':[{'source':'ACTIVE','target':'DECEASED','trigger':'retire'}]}]},
              'Behaviour':{'events':[{'concept':'Pet','event':'Pet retired','effect':'A retirement event is published','evidence':cited[:1]}]},
              'Logic':{'disjoint':[{'concepts':['Party','Pet'],'reason':'A party is a person, never an animal'},{'concepts':['Owner','Person'],'reason':'wrong: an owner is a person'}],
                       'inverses':[{'relationship':'Owner.pets','inverse':'belongs to'},{'relationship':'Nope.x','inverse':'ghost'}],
                       'symmetric':[{'relationship':'Owner.pets'}],'chains':[]},
              'Verdicts':{'verdicts':[]},
              'ModuleGuide':{'overview':'Owners bring their pets to the clinic for care.','glossary':[{'term':'Owner','definition':'The client responsible for a pet.','cites':['Owner']},
                                {'term':'Ghost','definition':'Cites nothing real.','cites':['Nobody']},{'term':'Patient','definition':'In veterinary practice the animal is the patient.','basis':'general'}],
                             'processes':[{'name':'Register a pet','steps':[{'step':'Record the owner','concept':'Owner'},{'step':'Add the pet','concept':'Pet','state':'ACTIVE'},{'step':'Bogus','concept':'Pet','state':'FLYING'}],'cites':['Pet']}],
                             'roles':[],'measures':[],'pitfalls':[{'text':'A telephone needs exactly ten digits.','cites':['R1']}],'faq':[{'question':'Who owns a pet?','answer':'An owner.','cites':['Owner','Pet']}],
                             'learning_path':['Owner','Pet','Ghost']},
              'DomainOverview':{'domain':'Veterinary clinic','summary':'A clinic that records owners and the pets it treats.','areas':[{'text':'Clients and their pets','cites':['owner']}],
                                'end_to_end':[],'principles':[{'text':'Animals are patients.','basis':'general'}]},
              'Answer':{'answer':'Owners own pets [Owner].','citations':['Owner','Pet'],'confidence':.8}}[output_model.__name__]
        if output_model.__name__=='Verdicts':
            data['verdicts']=[{'claim':cid,'verdict':'wrong' if wrong_statement and wrong_statement in stmt else 'correct','reason':'checked'} for cid,stmt in re.findall(r'Claim (claim_\w+) \(\w+\): ([^\n]+)',user)]
        return output_model.model_validate(data),'mock-'+tier
    monkeypatch.setattr(ai,'generate_json',fake)
    monkeypatch.setattr(p,'live_proposals',lambda evidence,job,label='':[])

def test_generative_ontology_on_jpa_project(env,monkeypatch):
    from backend import generative as g, usage
    client,root=env; app=root/'clinic'
    for rel,text in JPA_FILES.items(): (app/rel).parent.mkdir(parents=True,exist_ok=True); (app/rel).write_text(text)
    calls=[]; mock_model(monkeypatch,calls,wrong_statement='A pet must have a name')
    source=client.post('/api/sources',json={'name':'clinic','kind':'local','location':str(app)}).json()
    # Parsing mode: JPA entities give the ontology for free.
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'}); st=completed(client,source['id'])
    dom={c['name']:c for c in st['ontology']['concepts'] if c['layer']=='domain'}; byid={c['id']:c['name'] for c in st['ontology']['concepts']}
    assert set(dom)=={'Owner','Person','Pet'} and not calls
    assert byid[dom['Owner']['parent']]=='Person' and dom['Person']['abstract'] and 'firstName' in [x['name'] for x in dom['Person']['properties']]
    assert [x['options'] for x in dom['Pet']['properties'] if x['name']=='status']==[['ACTIVE','DECEASED']]
    assert ('Owner.telephone.Pattern','pattern','\\d{10}') in {(r['id'],r['operator'],r['value']) for r in st['ontology']['rules']}
    # The plan estimates without calling a model.
    plan=client.get(f'/api/sources/{source["id"]}/generation-plan').json()
    assert {r['pass'] for r in plan['passes']}>={'discover','define','rules','lifecycle','behaviour'} and not calls
    assert next(r for r in plan['passes'] if r['pass']=='rules')['concepts']==1  # only Pet has validation code
    # Generative enrichment of the analysed snapshot.
    job=client.post(f'/api/sources/{source["id"]}/generate',json={'share_confirmed':True}).json(); st=completed(client,source['id'])
    assert st['jobs'][0]['status']=='complete',(st['jobs'][0].get('error'),[l['message'] for l in client.get(f"/api/jobs/{job['id']}/logs").json()['logs']][-6:])
    ont=st['ontology']; dom={c['name']:c for c in ont['concepts'] if c['layer']=='domain'}; byid={c['id']:c['name'] for c in ont['concepts']}
    assert ont['status']=='approved' and ont['based_on']
    assert dom['Owner']['description'].startswith('A client') and dom['Owner']['provenance']['description']['tier']=='cheap' and 'Client' in dom['Owner']['aliases']
    assert byid.get(dom['Person']['parent'])=='Party' and dom['Party']['abstract']
    assert byid[dom['Owner']['parent']]=='Person'  # declared in code, not overridden
    rel=next(r for r in ont['relationships'] if r.get('label')=='pets'); assert rel['description'].startswith('owns')
    assert dom['Pet']['lifecycle']['states']==['ACTIVE','DECEASED'] and dom['Pet']['events'][0]['event']=='Pet retired'
    texts={r.get('text') for r in ont['rules']}
    assert 'A pet must have a name' not in texts  # the judge found it wrong and reverted it
    rejected={c['statement']:c['reason'] for c in g.claims(st['snapshot']['id'],'rejected')}
    assert any('Ghost' in k for k in rejected) and any('colour' in k for k in rejected) and any('cycle' in v or 'declared' in v for v in rejected.values())
    assert ('Verdicts','strong') in calls and ('Rules','strong') in calls and ('Definitions','cheap') in calls and ('Logic','cheap') in calls
    # The reasoner vets claims: Pet and Person are distinct entity classes, so Pet cannot be a Person.
    assert any(k=='Pet is a Person' and v.startswith('reasoner: Pet could have no members') for k,v in rejected.items())
    # Logic pass: a sound disjointness is added; one contradicting the hierarchy, a known inverse and a non-symmetric link are rejected.
    axioms={a['id']:a for a in ont['axioms']}
    assert any(a['kind']=='disjoint' and a['origin']=='llm' and {byid[x] for x in a['concepts']}=={'Party','Pet'} for a in axioms.values())
    assert rejected['Disjoint: Owner, Person']=='one is a kind of another' and rejected['Inverse of Owner.pets: belongs to']=='already has an inverse'
    assert rejected['Symmetric: Owner.pets']=='source and target differ' and rejected['Inverse of Nope.x: ghost']=='unknown relationship'
    reasoning=client.get('/api/outputs/reasoning',params={'source_id':source['id']}).json()
    assert reasoning['unsatisfiable']==[] and reasoning['axioms']['by_kind']['disjoint']>=2 and any(h['concept']=='Owner' and h['implied']==['Party'] for h in reasoning['hierarchy'])
    # Second run: every enrichment call is served from the result cache.
    before=len([x for x in calls if x[0]!='Verdicts'])
    client.post(f'/api/sources/{source["id"]}/generate',json={'share_confirmed':True}); completed(client,source['id'])
    assert len([x for x in calls if x[0]!='Verdicts'])==before
    plan=client.get(f'/api/sources/{source["id"]}/generation-plan').json()
    assert all(r['cached_calls']==r['calls'] for r in plan['passes'] if r['pass']!='discover')
    # Outputs.
    docs=client.get('/api/outputs/docs',params={'source_id':source['id'],'format':'md'}).text
    assert '### Owner' in docs and '*(AI)*' in docs and 'ACTIVE → DECEASED' in docs
    api=client.get('/api/outputs/schemas',params={'source_id':source['id']}).json()
    assert api['components']['schemas']['Owner']['properties']['telephone']['pattern']=='^(?:\\d{10})$' and api['components']['schemas']['Owner']['allOf']
    sample=client.post('/api/outputs/sample',params={'source_id':source['id']},json={'concept_id':dom['Owner']['id'],'count':5}).json()
    assert len(sample['records'])==5 and sample['problems']==[] and all(len(r['telephone'])==10 for r in sample['records'])
    answer=client.post('/api/outputs/ask',params={'source_id':source['id']},json={'question':'Who owns a pet?'}).json()
    assert [c['name'] for c in answer['citations']]==['Owner','Pet'] and 'Owner' in answer['context']
    assert client.get('/api/quality',params={'source_id':source['id']}).json()['report']['judged']>0
    # Budget cap: with prices set and a zero budget no enrichment call is made, and the job still completes.
    from backend import ai
    monkeypatch.setattr(ai,'config',lambda: {**ai.__dict__['config'].__wrapped__() } if hasattr(ai.config,'__wrapped__') else {'provider':'ollama','model':'m','models':{'cheap':'fresh-cheap','strong':'fresh-strong'},'configured':True,'error':None})
    usage.set_price('ollama','fresh-cheap',1,1); usage.set_price('ollama','fresh-strong',1,1)
    count=len(calls); usage.record('ollama','fresh-cheap',{'job':'x'},{'input':1,'output':1},1,'success')
    client.post(f'/api/sources/{source["id"]}/generate',json={'share_confirmed':True,'budget_usd':0}); st=completed(client,source['id'])
    assert st['jobs'][0]['status']=='complete' and len([x for x in calls[count:] if x[0]!='Verdicts'])==0
    logs=client.get(f"/api/jobs/{st['jobs'][0]['id']}/logs").json()['logs']
    assert any('Budget of $0.00 reached' in l['message'] for l in logs)


def test_unknown_api_routes_are_404_not_the_web_page(env):
    client,root=env
    r=client.get('/api/no-such-endpoint')
    assert r.status_code==404 and r.headers['content-type'].startswith('application/json')


def logic_fixture(client,root):
    app=root/'acct'
    def put(rel,body): (app/rel).parent.mkdir(parents=True,exist_ok=True); (app/rel).write_text(json.dumps(body))
    put('accounts/doctype/account/account.json',{'doctype':'DocType','name':'Account','module':'Accounts','is_tree':1,'nsm_parent_field':'parent_account','autoname':'field:account_name','fields':[
        {'fieldname':'account_name','fieldtype':'Data','reqd':1},{'fieldname':'parent_account','fieldtype':'Link','options':'Account'},{'fieldname':'code','fieldtype':'Data','unique':1,'length':10}],
        'permissions':[{'role':'Accounts Manager','read':1,'create':1,'write':1,'delete':1},{'role':'Accounts User','read':1}]})
    put('accounts/doctype/account/test_records.json',[{'doctype':'Account','name':'Assets','account_name':'Assets','code':'1'},
        {'doctype':'Account','name':'Bank','account_name':'Bank','parent_account':'Assets','code':'11'},{'doctype':'Account','name':'Cash','account_name':'Cash','parent_account':'Bank','code':'11'}])
    put('accounts/doctype/customer/customer.json',{'doctype':'DocType','name':'Customer','module':'Accounts','fields':[{'fieldname':'customer_name','fieldtype':'Data','reqd':1},
        {'fieldname':'customer_type','fieldtype':'Select','options':'Company\nIndividual'},{'fieldname':'tax_id','fieldtype':'Data','mandatory_depends_on':'eval:doc.customer_type=="Company"'}]})
    put('accounts/doctype/invoice/invoice.json',{'doctype':'DocType','name':'Invoice','module':'Accounts','is_submittable':1,'fields':[
        {'fieldname':'customer','fieldtype':'Link','options':'Customer','reqd':1},{'fieldname':'customer_name','fieldtype':'Data','fetch_from':'customer.customer_name'},
        {'fieldname':'grand_total','fieldtype':'Currency','non_negative':1},{'fieldname':'items','fieldtype':'Table','options':'Invoice Item','reqd':1},
        {'fieldname':'remarks','fieldtype':'Small Text','allow_on_submit':1},{'fieldname':'posting_date','fieldtype':'Date','set_only_once':1},
        {'fieldname':'debit_to','fieldtype':'Link','options':'Account'}],
        'permissions':[{'role':'Accounts User','read':1,'create':1,'write':1,'submit':1},{'role':'Accounts Manager','read':1,'submit':1,'cancel':1}]})
    put('accounts/doctype/invoice_item/invoice_item.json',{'doctype':'DocType','name':'Invoice Item','module':'Accounts','istable':1,'fields':[{'fieldname':'qty','fieldtype':'Float','non_negative':1}]})
    put('accounts/workflow/invoice_approval/invoice_approval.json',{'doctype':'Workflow','name':'Invoice Approval','document_type':'Invoice','is_active':1,'workflow_state_field':'workflow_state',
        'states':[{'state':'Draft','doc_status':'0'},{'state':'Approved','doc_status':'1'},{'state':'Rejected','doc_status':'0'}],
        'transitions':[{'state':'Draft','action':'Approve','next_state':'Approved','allowed':'Accounts Manager','condition':'doc.grand_total < 1000'},
                       {'state':'Draft','action':'Reject','next_state':'Rejected','allowed':'Accounts Manager'}]})
    source=client.post('/api/sources',json={'name':'acct','kind':'local','location':str(app)}).json()
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'})
    return source['id'],completed(client,source['id'])

def test_rules_logic_and_reasoner_from_frappe_code(env):
    from backend import logic
    client,root=env; sid,state=logic_fixture(client,root); ont=state['ontology']
    assert ont['status']=='approved'
    dom={c['name']:c for c in ont['concepts'] if c['layer']=='domain'}
    rules={r['id']:r for r in ont['rules']}
    # Rules for domain experts, read from DocType settings, permissions and the workflow (no model).
    assert rules['Customer.tax_id.conditional']['text']=='Tax Id is required when Customer Type is Company' and rules['Customer.tax_id.conditional']['category']=='conditional'
    assert rules['Invoice.customer_name.fetch']['operator']=='derived' and 'copied from the customer name of the selected Customer' in rules['Invoice.customer_name.fetch']['text']
    assert rules['Invoice.grand_total.non_negative']['operator']=='minimum' and rules['Invoice.grand_total.non_negative']['value']==0
    assert rules['Account.code.unique']['category']=='uniqueness' and rules['Account.account_name.unique']['text'].startswith('Account Name names the Account')
    assert rules['Account.code.length']['value']==10 and rules['Invoice.posting_date.set_only_once']['trigger']=='save'
    assert rules['Invoice.remarks.allow_on_submit']['category']=='transition' and rules['Invoice.submitted.locked']['trigger']=='submit'
    assert rules['Invoice.permission.cancel']['text']=='Only Accounts Manager can cancel an Invoice' and rules['Invoice.permission.submit']['value']['roles']==['Accounts User','Accounts Manager']
    approve=rules['Invoice.transition.draft.approved.approve']
    assert approve['operator']=='transition' and approve['text']=='Invoice: "Approve" moves Draft → Approved by Accounts Manager when Grand Total is less than 1000'
    # The workflow is the lifecycle; a submittable DocType without one gets Draft → Submitted → Cancelled.
    assert dom['Invoice']['lifecycle']['states']==['Draft','Approved','Rejected'] and dom['Invoice']['provenance']['lifecycle']['origin']=='workflow'
    # Axioms: DocTypes are disjoint; links are single-valued; required links exactly one; trees transitive; keys; child rows inverse.
    axioms=logic.all_axioms(ont); kinds={a['kind'] for a in axioms}
    assert {'disjoint','functional','cardinality','transitive','key','inverse'}<=kinds
    assert set(next(a for a in axioms if a['id']=='disjoint:doctypes')['concepts'])>={dom['Account']['id'],dom['Invoice']['id'],dom['Invoice Item']['id']}
    # The reasoner: transitive ancestors over records, inverse rows, duplicate keys, and explanations.
    r=client.get('/api/outputs/reasoning',params={'source_id':sid}).json()
    statements={f['statement'] for f in r['facts']}
    assert 'Account Cash is within Account Assets' in statements
    fact=next(f for f in r['facts'] if f['statement']=='Account Cash is within Account Assets'); assert fact['because'][0]=='Account Cash → Account Bank → Account Assets'
    assert any(c['kind']=='key' and 'share code = 11' in c['message'] for c in r['contradictions']) and r['unsatisfiable']==[]
    # Rule catalogue with sources and expert review; disputed rules leave exports and checks.
    cat=client.get('/api/outputs/rules',params={'source_id':sid}).json()['rules']
    row=next(x for x in cat if x['id']=='Invoice.grand_total.non_negative')
    assert row['module']=='Accounts' and row['origin_label']=='DocType' and row['evidence'][0]['path'].endswith('invoice.json') and row['review']['status']=='unreviewed'
    assert client.post('/api/rules/review',params={'source_id':sid},json={'rule_id':'nope','status':'confirmed'}).status_code==404
    assert client.post('/api/rules/review',params={'source_id':sid},json={'rule_id':row['id'],'status':'maybe'}).status_code==400
    api=client.get('/api/outputs/schemas',params={'source_id':sid}).json()
    assert api['components']['schemas']['Invoice']['properties']['grand_total']['minimum']==0
    client.post('/api/rules/review',params={'source_id':sid},json={'rule_id':row['id'],'status':'disputed','comment':'Credit notes can be negative'})
    row=next(x for x in client.get('/api/outputs/rules',params={'source_id':sid}).json()['rules'] if x['id']==row['id'])
    assert row['review']=={'status':'disputed','comment':'Credit notes can be negative','at':row['review']['at']}
    api=client.get('/api/outputs/schemas',params={'source_id':sid}).json()
    assert 'minimum' not in api['components']['schemas']['Invoice']['properties']['grand_total']
    md=client.get('/api/outputs/rules',params={'source_id':sid,'format':'md'}).text; csv=client.get('/api/outputs/rules',params={'source_id':sid,'format':'csv'}).text
    assert '## Accounts' in md and 'disputed: Credit notes can be negative' in md and csv.startswith('﻿Module,Concept') and 'Tax Id is required when Customer Type is Company' in csv
    # OWL: the logic and the rules are part of the exported ontology.
    ttl=client.get('/api/semantic/export',params={'source_id':sid,'scope':'model'}).text
    for needle in ('owl:AllDisjointClasses','a owl:FunctionalProperty','owl:qualifiedCardinality "1"','owl:TransitiveProperty','owl:hasKey','owl:inverseOf','a oe:BusinessRule','oe:ruleCategory "conditional"'):
        assert needle in ttl,needle
    # Refreshing rules and logic from code makes a new approved version without model calls.
    out=client.post(f'/api/sources/{sid}/derive-logic').json()
    assert out['approved'] and out['rules']['authorisation']>=3 and out['ontology']!=ont['id']

def test_reasoner_vets_claims_and_finds_contradictions():
    from backend import logic
    ont={'id':'o','concepts':[{'id':'a','name':'Invoice','layer':'domain','properties':[{'name':'total','datatype':'number','options':[]}]},{'id':'b','name':'Payment','layer':'domain','properties':[]},
                              {'id':'cat','name':'Document','layer':'domain','abstract':True,'properties':[]}],'relationships':[],
         'rules':[{'id':'r1','operator':'minimum','concept':'a','property':'total','value':10,'text':'at least 10'},{'id':'r2','operator':'maximum','concept':'a','property':'total','value':5,'text':'at most 5'}],
         'axioms':[{'id':'d','kind':'disjoint','concepts':['a','b'],'origin':'code','text':'one DocType per record'}]}
    r=logic.Reasoner(ont)
    assert 'Invoice could have no members' in r.conflict_if_parent(ont['concepts'][0],ont['concepts'][1])
    assert r.conflict_if_parent(ont['concepts'][0],ont['concepts'][2])==''
    ont['concepts'][0]['parent']='cat'; ont['concepts'][1]['parent']='cat'
    assert logic.Reasoner(ont).conflict_if_disjoint(['a','cat'])=='Invoice is a Document, so they cannot be disjoint'
    out=logic.Reasoner(ont).run()
    assert any(c['message']=='No Invoice can satisfy both rules on total' for c in out['contradictions'])
    ont['concepts'][2]['parent']='b'  # Document under Payment: Invoice becomes both an Invoice and a Payment
    out=logic.Reasoner(ont).run()
    assert out['unsatisfiable']==['Invoice'] and out['contradictions'][0]['because'][-1].startswith('Invoice and Payment are disjoint')
    assert logic.humanize('eval:doc.a==1 && in_list(["X","Y"], doc.b)')=='A is Yes and B is one of X, Y' and logic.humanize('eval:!doc.is_return')=='Is Return is not set'

def test_domain_knowledge_set_is_grounded(env,monkeypatch):
    from backend import knowledge
    client,root=env; app=root/'clinic'
    for rel,text in JPA_FILES.items(): (app/rel).parent.mkdir(parents=True,exist_ok=True); (app/rel).write_text(text)
    calls=[]; mock_model(monkeypatch,calls)
    monkeypatch.setattr(knowledge.ai,'config',lambda: {'provider':'ollama','model':'m','models':{'cheap':'m-cheap','strong':'m-strong'},'configured':True,'error':None})
    source=client.post('/api/sources',json={'name':'clinic','kind':'local','location':str(app)}).json()
    client.post(f'/api/sources/{source["id"]}/analyse',json={'mode':'parsing'}); completed(client,source['id'])
    info=client.get('/api/outputs/knowledge',params={'source_id':source['id']}).json()
    assert info['knowledge'] is None and info['plan']['calls']==info['plan']['modules']+1 and not calls
    assert client.post('/api/outputs/knowledge',params={'source_id':source['id']},json={}).status_code==400  # consent first
    client.post('/api/outputs/knowledge',params={'source_id':source['id']},json={'share_confirmed':True})
    for _ in range(300):
        k=client.get('/api/outputs/knowledge',params={'source_id':source['id']}).json()['knowledge']
        if k['status'] not in ('queued','running'): break
        time.sleep(.01)
    assert k['status']=='complete',k.get('error')
    m=k['modules'][0]
    # System statements must cite what they rest on; general knowledge is kept but labelled.
    assert [t['term'] for t in m['glossary']]==['Owner','Patient'] and m['glossary'][1]['basis']=='general' and m['glossary'][0]['cites'][0]['kind']=='concept'
    steps=m['processes'][0]['steps']; assert steps[1]['state']=='ACTIVE' and steps[2]['state']=='' and steps[0]['concept_id']
    assert m['pitfalls'][0]['cites'][0]['kind']=='rule' and [x['name'] for x in m['learning_path']]==['Owner','Pet'] and m['dropped']==1
    assert k['overview']['domain']=='Veterinary clinic' and k['overview']['areas'][0]['cites'][0]['name']=='Owner'
    md=client.get('/api/outputs/knowledge',params={'source_id':source['id'],'format':'md'}).text
    assert '## Veterinary clinic' in md and '*(general knowledge)*' in md and '| Owner | The client responsible for a pet.' in md
    skos=client.get('/api/outputs/knowledge',params={'source_id':source['id'],'format':'skos'}).text
    assert 'a skos:Concept' in skos and 'skos:closeMatch <urn:ontoforge:concept:' in skos
    # The guide feeds grounded Q&A; a second run is served from the cache.
    seen=[]; original=knowledge.ai.generate_json
    monkeypatch.setattr(knowledge.ai,'generate_json',lambda system,user,*a,**kw:(seen.append(user),original(system,user,*a,**kw))[1])
    client.post('/api/outputs/ask',params={'source_id':source['id']},json={'question':'Who is the owner?'})
    assert 'Domain guide excerpts' in seen[-1] and 'Owners bring their pets' in seen[-1]
    before=len(calls); client.post('/api/outputs/knowledge',params={'source_id':source['id']},json={'share_confirmed':True,'include_general':True})
    for _ in range(300):
        k=client.get('/api/outputs/knowledge',params={'source_id':source['id']}).json()['knowledge']
        if k['status'] not in ('queued','running'): break
        time.sleep(.01)
    assert k['status']=='complete' and len(calls)==before and k['cached']==k['progress']['total']

def test_python_checks_and_model_fields_become_rules():
    import ast
    from backend import parsers, logic
    src='''
from pydantic import BaseModel, Field
from typing import Literal, Optional
class Order(BaseModel):
    qty: int = Field(..., ge=1, le=100)
    status: Literal["new","paid"] = "new"
    note: Optional[str] = None
class OrderService:
    def validate(self):
        if flt(self.amount) < 0 and not self.is_return: frappe.throw(_("Amount cannot be negative"))
        if not isinstance(self.amount, float): raise ValueError("amount must be a float")
        if self.qty not in (1, 2): raise TypeError("internal")
        if self.status == "paid": raise PermissionError("Only admins can change a paid order")
'''
    tree=ast.parse(src)
    fields=parsers.model_fields(tree.body[2])
    assert fields['model_kind']=='pydantic' and [f['fieldname'] for f in fields['fields'] if f['reqd']]==['qty']
    assert {k:v for f in fields['fields'] for k,v in f.items() if k in ('ge','le','options')}=={'ge':1,'le':100,'options':['new','paid']}
    checks=parsers.guards(tree.body[3].body[0])
    assert [c['error'] for c in checks]==['ValidationError','ValueError','PermissionError']  # TypeError is a program check
    concept={'id':'c1','name':'Order','properties':[{'name':'qty'}]}
    rules=logic.guard_rules(concept,{'id':'f1','name':'validate','properties':{'guards':checks}},{})
    assert [r['text'] for r in rules]==['Amount cannot be negative. (Rejected when Amount is less than 0 and Is Return is not set.)',
                                        'Only admins can change a paid order. (Rejected when Status is paid.)']  # isinstance checks are dropped
    assert [(r['category'],r['trigger'],r['origin']) for r in rules]==[('validation','save','guard'),('authorisation','save','guard')]
    model=logic.model_rules(concept,{'id':'e1','properties':fields})
    assert {(r['operator'],r.get('property'),r['text']) for r in model}>={('required','qty','Qty is required for an Order'),('minimum','qty','Qty must be at least 1'),('described','','Status must be one of new, paid')}
    assert logic.py_humanize('self.status in ["Open", "Closed"] or self.docstatus == 1')=='Status is one of Open, Closed or the document is Submitted'

def test_external_graph_tools(env,monkeypatch):
    import sys,types,zipfile,io as _io
    client,root=env; sid,state=logic_fixture(client,root)
    r=client.get(f'/api/export/graphxr?source_id={sid}&scope=both'); assert r.status_code==200 and r.headers['content-type']=='application/zip'
    z=zipfile.ZipFile(_io.BytesIO(r.content)); assert set(z.namelist())=={'nodes.csv','edges.csv','README.txt','graph.json'}
    nodes=z.read('nodes.csv').decode('utf-8-sig').splitlines(); edges=z.read('edges.csv').decode('utf-8-sig').splitlines()
    assert nodes[0]=='id,label,category,layer,kind,description,rules,properties,path' and any(',Concept,domain,' in n for n in nodes) and any(',code,' in n for n in nodes)
    assert any(',implemented by,evidence' in e for e in edges) and any(',link' in e for e in edges)
    assert client.get(f'/api/export/graphxr?source_id={sid}&scope=nope').status_code==400
    # Graphistry: needs credentials and explicit confirmation; the upload goes through the official client.
    for k in ('GRAPHISTRY_PERSONAL_KEY_ID','GRAPHISTRY_PERSONAL_KEY_SECRET','GRAPHISTRY_USERNAME','GRAPHISTRY_PASSWORD'): monkeypatch.delenv(k,raising=False)
    st=client.get(f'/api/export/graphistry?source_id={sid}&scope=domain').json(); assert st['configured'] is False and st['nodes']>0 and '_creds' not in st
    assert client.post(f'/api/export/graphistry?source_id={sid}',json={'scope':'domain','share_confirmed':False}).status_code==400
    sent={}
    class Plotter:
        def __init__(s,**kw): s.kw=kw
        def edges(s,df,a,b): sent['edges']=df; return s
        def nodes(s,df,k): sent['nodes']=df; return s
        def bind(s,**kw): return s
        def encode_point_color(s,col,**kw): sent['color']=col; return s
        def plot(s,**kw): sent['name']=kw.get('name'); return 'https://hub.example/graph/graph.html?dataset=abc'
    class Client(Plotter):
        def register(s,**kw): sent['register']=kw
    fake=types.ModuleType('graphistry'); fake.client=lambda: Client(); monkeypatch.setitem(sys.modules,'graphistry',fake)
    monkeypatch.setenv('GRAPHISTRY_PERSONAL_KEY_ID','kid'); monkeypatch.setenv('GRAPHISTRY_PERSONAL_KEY_SECRET','secret'); monkeypatch.setenv('GRAPHISTRY_SERVER','graphistry.internal')
    r=client.post(f'/api/export/graphistry?source_id={sid}',json={'scope':'domain','share_confirmed':True}); assert r.status_code==200,r.text
    assert r.json()['url'].endswith('dataset=abc') and sent['register']['server']=='graphistry.internal' and sent['register']['personal_key_id']=='kid'
    assert set(sent['nodes']['layer'])=={'domain'} and sent['color']=='category' and 'domain' in sent['name']

def test_timeline_impact_exports_and_hotspots(env):
    import zipfile,io as _io
    from backend import history
    client,root=env; sid,state=logic_fixture(client,root)
    client.post(f'/api/sources/{sid}/derive-logic')  # a second version, based on the first
    tl=client.get(f'/api/timeline?source_id={sid}').json()
    assert len(tl['versions'])>=2 and tl['versions'][0]['first'] and tl['versions'][-1]['based_on']==tl['versions'][-2]['id'] and tl['versions'][-1]['counts']['rules']>0
    v=client.get(f"/api/timeline/version?source_id={sid}&version={tl['versions'][-1]['id']}").json(); assert v['base']==tl['versions'][-2]['id'] and 'changes' in v
    ont=client.get(f'/api/state?source_id={sid}').json()['ontology']; dom={c['name']:c for c in ont['concepts'] if c['layer']=='domain'}
    # Changing Customer affects Invoice (which links to it), its rules and the Customer code.
    r=client.get(f"/api/impact?source_id={sid}&node_id={dom['Customer']['id']}").json()
    names={c['name'] for c in r['concepts']}; assert 'Invoice' in names and r['rule_total']>0 and r['code']
    inv=next(c for c in r['concepts'] if c['name']=='Invoice'); assert inv['because'][0].startswith('Invoice links to Customer')
    code_id=r['code'][0]['id']; r2=client.get(f"/api/impact?snapshot_id={state['snapshot']['id']}&node_id={code_id}").json(); assert r2['start']['layer']=='code'
    assert client.get(f'/api/impact?source_id={sid}&node_id=nope').status_code==404
    for fmt,needle in [('graphml',b'<graphml'),('gexf',b'<gexf'),('mermaid',b'classDiagram')]:
        x=client.get(f'/api/export/file?source_id={sid}&format={fmt}&scope=both'); assert x.status_code==200 and needle in x.content,fmt
    z=zipfile.ZipFile(_io.BytesIO(client.get(f'/api/export/file?source_id={sid}&format=neo4j&scope=domain').content))
    assert {'nodes.csv','relationships.csv','import.cypher','nodes-admin.csv','relationships-admin.csv','README.txt'}<=set(z.namelist()) and 'SET n:Concept' in z.read('import.cypher').decode()
    # git log parsing: the shallow boundary commit is skipped and the subdirectory prefix removed.
    log='\x1eaaa\x1fAnn\x1f2026-10-01T10:00:00+00:00\n\napp/a.py\napp/b.py\n\x1ebbb\x1fBob\x1f2026-10-05T10:00:00+00:00\n\napp/a.py\nother.py\n\x1eccc\x1fCy\x1f2026-01-01T00:00:00+00:00\n\napp/a.py\n'
    files,commits,authors=history.parse_log(log,skip=frozenset({'ccc'}),prefix='app/')
    assert commits==2 and files['a.py']['commits']==2 and len(files['a.py']['authors'])==2 and 'other.py' not in files and files['a.py']['last'].startswith('2026-10-05')
    assert client.get(f'/api/hotspots?source_id={sid}').json()['status']=='none'
    assert client.post(f'/api/sources/{sid}/hotspots',json={'months':6}).status_code==400  # a local folder without .git

def test_chat_is_grounded_and_saved_per_source(env,monkeypatch):
    from backend import chat,ai
    client,root=env; sid,state=logic_fixture(client,root)
    seen=[]
    def fake(system,user,output_model,tier='cheap',context=None,max_tokens=None):
        seen.append((system,user,tier,context))
        return output_model.model_validate({'answer':'An **Invoice** needs a Customer.','citations':['Invoice','Customer','Nonexistent'],'followups':['Who can submit an invoice?',''],'confidence':.9}),'fake-model'
    monkeypatch.setattr(ai,'generate_json',fake)
    r=client.post(f'/api/chats?source_id={sid}',json={'message':'What does an invoice need before it can be submitted?'}); assert r.status_code==200,r.text
    conv=r.json(); assert conv['source_id']==sid and conv['title'].startswith('What does an invoice') and len(conv['messages'])==2
    bot=conv['messages'][1]; assert bot['role']=='assistant' and [c['name'] for c in bot['citations']]==['Invoice','Customer'] and bot['followups']==['Who can submit an invoice?'] and bot['model']=='fake-model'
    system,user,tier,context=seen[0]
    # Grounded in this source's data: summary, the matching concept card with its rules, and code names (no excerpts).
    assert 'ONLY from the SOURCE DATA' in system and '## Source summary' in user and 'Concept: Invoice' in user and 'Rules:' in user and '## Matching code' in user and 'Question: What does an invoice' in user
    assert context['operation']=='chat' and context['source']==sid
    r=client.post(f'/api/chats?source_id={sid}',json={'message':'And who approves it?','conversation_id':conv['id'],'tier':'strong'}); assert r.status_code==200
    assert len(r.json()['messages'])==4 and 'USER: What does an invoice need' in seen[1][1] and seen[1][2]=='strong'
    lst=client.get(f'/api/chats?source_id={sid}').json()['conversations']; assert [c['id'] for c in lst]==[conv['id']] and lst[0]['messages']==4
    assert client.get(f"/api/chats/{conv['id']}").json()['messages'][2]['content']=='And who approves it?'
    assert client.patch(f"/api/chats/{conv['id']}",json={'title':'Invoice approval'}).json()['title']=='Invoice approval'
    assert client.post(f'/api/chats?source_id={sid}',json={'message':'  '}).status_code==400
    other=client.post('/api/sources',json={'name':'other','kind':'local','location':str(root/'acct')}).json()['id']
    assert client.post(f'/api/chats?source_id={other}',json={'message':'hi','conversation_id':conv['id']}).status_code in (400,404)
    assert client.delete(f"/api/chats/{conv['id']}").status_code==200 and client.get(f'/api/chats?source_id={sid}').json()['conversations']==[]


def test_legacy_database_adopted_only_at_startup(tmp_path,monkeypatch):
    # A pre-rename explorer.db is renamed when the server starts (init), keeping its data.
    import sqlite3
    legacy=tmp_path/'explorer.db'
    with sqlite3.connect(legacy) as c: c.execute('CREATE TABLE records(kind TEXT, id TEXT, source TEXT, snapshot TEXT, data TEXT NOT NULL, PRIMARY KEY(kind,id))'); c.execute("INSERT INTO records VALUES('source','x',NULL,NULL,'{}')")
    monkeypatch.setattr(s,'DATA',tmp_path); monkeypatch.setattr(s,'DB',tmp_path/'ontoforge.db')
    assert legacy.exists() and not s.DB.exists()
    s.init()
    assert not legacy.exists() and s.DB.exists()
    with s.connect() as c: assert c.execute('SELECT count(*) FROM records').fetchone()[0]==1
