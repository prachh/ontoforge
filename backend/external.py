"""Pinned Open Ontologies v2.1.0 CLI adapter. No source-controlled commands execute."""
import os,json,hashlib,subprocess,tempfile,threading,time
from pathlib import Path
from . import store as s
from . import semantic
from .semantic import iri,lit

PIN='v2.1.0'
# Published SHA-256 digests of the official v2.1.0 release (SHASUMS.txt). Only these executables are accepted.
PINNED_SHA256={
    'open-ontologies-aarch64-apple-darwin':'493f6db51550112528d19f3ca88caf133b46c2a9054a195a98bc588b57ba07b6',
    'open-ontologies-x86_64-apple-darwin':'d2aacac75b2301eb3ce8f49000a8cc0bc7dd7edbf01fa36ac422ffdf4aaf11ee',
    'open-ontologies-x86_64-unknown-linux-gnu':'ead03c7148a66ea0fe01cbdaa289d47361c2a4d54619a60f805a72a2c57e81bb',
    'open-ontologies-x86_64-pc-windows-msvc.exe':'1d81ff3933868bfed001b63d8bdd6f9cb3c0807389d4e3064a51a41d7d60f480',
}
MAC_ARM_SHA=PINNED_SHA256['open-ontologies-aarch64-apple-darwin']
def timeout_seconds(): return float(os.getenv('ONTO_OPEN_ONTOLOGIES_TIMEOUT_SECONDS','90'))
def output_budget(): return int(os.getenv('ONTO_OPEN_ONTOLOGIES_OUTPUT_BYTES',str(32*1024*1024)))
SEVERITY={'Violation':'error','Warning':'warning','Info':'info','error':'error','warning':'warning','info':'info'}

def trimmed(records,limit=100):
    """Raw output kept in run history, with long result lists shortened; the full lists are already summarised as findings."""
    out=[]
    for r in records:
        result=r.get('result')
        if isinstance(result,dict):
            result={k:(v[:limit]+[{'truncated':len(v)-limit}] if isinstance(v,list) and len(v)>limit else v) for k,v in result.items()}
        out.append({**r,'result':result})
    return out

def mapping(ontology,graph):
    """Explicit subset: named classes, subclasses, type assertions, scalar properties.
    Provenance is kept as literal annotations on application IRIs. No domain/range
    declaration is emitted as a closed-world constraint.
    """
    ttl=['@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .','@prefix sh: <http://www.w3.org/ns/shacl#> .','@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .']
    shapes=ttl.copy();unsupported=[]
    for c in ontology['concepts']:
        cls=iri('concept',c['id']);ttl += [f'{cls} a rdfs:Class ; rdfs:label {lit(c["name"])} .']
        if c.get('parent'):ttl += [f'{cls} rdfs:subClassOf {iri("concept",c["parent"])} .']
        for p in c.get('properties',[]):
            dtype={'string':'xsd:string','number':'xsd:decimal','boolean':'xsd:boolean'}[p['datatype']]
            bits=[f'sh:path {iri("property",p["name"])}',f'sh:datatype {dtype}']
            if p.get('required'):bits.append('sh:minCount 1')
            if p.get('minimum') is not None:unsupported.append('Numerical minimum: '+c['id']+'.'+p['name']+' stays in application validation')
            shapes += [f'{iri("shape",c["id"]+":"+p["name"])} a sh:NodeShape ; sh:targetClass {cls} ; sh:property [ '+ ' ; '.join(bits)+' ] .']
    for n in graph['nodes']:
        ent=iri('entity',n['id']);ttl += [f'{ent} a {iri("concept",n.get("concept",n["type"]))} ; rdfs:label {lit(n["name"])} .']
        for k in ['id','snapshot_id','source_id','commit','path','line','content_hash','method','status','excerpt','github_url']:
            if n.get(k) is not None:ttl += [f'{ent} {iri("provenance",k)} {lit(n[k])} .']
        for k,v in n.get('properties',{}).items():
            if isinstance(v,bool):value='true' if v else 'false'
            elif isinstance(v,(int,float)):value=lit(v)+'^^xsd:decimal'
            elif isinstance(v,str):value=lit(v)
            else:unsupported.append('Structured property: '+n['id']+'.'+k);continue
            ttl += [f'{ent} {iri("property",k)} {value} .']
    for e in graph['edges']:
        ttl += [f'{iri("entity",e["source"])} {iri("relationship",e["type"])} {iri("entity",e["target"])} .']
        # Reify for provenance preservation; no claim of external checking of source truth.
        er=iri('edge',e['id'])
        for k in ['id','source','target','type','snapshot_id','source_id','path','line','content_hash','method','status','excerpt']:
            if e.get(k) is not None:ttl += [f'{er} {iri("provenance",k)} {lit(e[k])} .']
    if ontology.get('rules'):unsupported.append('Application rules (required/minimum/equals) are not translated; use the built-in validator')
    return '\n'.join(ttl), '\n'.join(shapes), list(dict.fromkeys(unsupported))

def bounded_process(args,payload,timeout=None,budget=None):
    timeout=timeout_seconds() if timeout is None else timeout; budget=output_budget() if budget is None else budget
    proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={'PATH':os.environ.get('PATH',''),'OPEN_ONTOLOGIES_STORAGE_MODE':'memory','RUST_LOG':'error'})
    parts={'stdout':bytearray(),'stderr':bytearray()};over=threading.Event();lock=threading.Lock()
    def drain(pipe,key):
        while chunk:=pipe.read(65536):
            with lock:
                if sum(map(len,parts.values()))+len(chunk)>budget:
                    over.set();proc.kill();break
                parts[key].extend(chunk)
    readers=[threading.Thread(target=drain,args=(proc.stdout,'stdout')),threading.Thread(target=drain,args=(proc.stderr,'stderr'))]
    for t in readers:t.start()
    try:
        proc.stdin.write(payload.encode());proc.stdin.close();proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill();proc.wait();raise ValueError(f'External engine exceeded its {timeout:g}-second execution budget (ONTO_OPEN_ONTOLOGIES_TIMEOUT_SECONDS)') from None
    finally:
        for t in readers:t.join(timeout=2)
    if over.is_set():raise ValueError('External engine exceeded its configured output byte budget')
    return proc.returncode,parts['stdout'].decode(errors='replace'),parts['stderr'].decode(errors='replace')

class OpenOntologiesEngine:
    name='OpenOntologiesEngine';version=PIN
    def __init__(self):
        self.path=os.getenv('ONTO_OPEN_ONTOLOGIES_BIN','');self.reason='Configure ONTO_OPEN_ONTOLOGIES_BIN and ONTO_OPEN_ONTOLOGIES_SHA256 for the pinned v2.1.0 release.'
        self.available=False
        if self.path and Path(self.path).is_file():
            expected=os.getenv('ONTO_OPEN_ONTOLOGIES_SHA256','')
            with open(self.path,'rb') as binary: actual=hashlib.file_digest(binary,'sha256').hexdigest()
            self.available=bool(expected) and actual==expected and actual in PINNED_SHA256.values()
            if not self.available:self.reason='External binary SHA-256 does not match the configured digest.' if actual!=expected else 'Configured binary is not a published Open Ontologies v2.1.0 release.'
        elif self.path:self.reason='ONTO_OPEN_ONTOLOGIES_BIN does not point at a file. Run scripts/setup_open_ontologies.sh.'
    def capabilities(self):
        return {'name':self.name,'version':self.version,'available':self.available,'operations':['definition','checks','infer','instances','query','demo'] if self.available else [],'status':'Available' if self.available else 'Unavailable','reason':None if self.available else self.reason,'classification':'deterministic','unsupported':['business truth','workflow and temporal rules','Python validation logic in controllers','certificate verification','child-table rows of records']}

    def execute(self,commands,files,defects=None):
        """One isolated in-memory store per call. `commands` are (command, args) where args may name files by key."""
        with tempfile.TemporaryDirectory(prefix='onto-engine-') as d:
            root=Path(d); paths={}
            for name,content in files.items(): (root/name).write_text(content,encoding='utf-8'); paths[name]=str(root/name)
            binary=str(Path(self.path).resolve()); base=[binary,'--data-dir',str(root/'store'),'--no-connect','--json']
            payload=json.dumps([{'command':c,'args':[paths.get(a,a) for a in args]} for c,args in commands])
            code,out,err=bounded_process(base+['batch','-'],payload)
            records=[json.loads(line) for line in out.splitlines() if line.strip()]
            if len(records)!=len(commands): raise ValueError('Engine returned an incomplete batch: '+(err or out)[:400])
            failed=[r for r in records if isinstance(r.get('result'),dict) and 'error' in r['result']]
            if failed: raise ValueError(f"Engine {failed[0]['command']} failed: "+str(failed[0]['result']['error'])[:400])
            if defects:
                dcode,dout,derr=bounded_process(base+['defects',paths[defects]],'')
                records.append({'command':'defects','result':json.loads(dout) if dout.strip() else {'error':derr[:400]}})
            return records,err

    def semantic_run(self,operation,ontology,graph,query=None,reasoning=False):
        start=time.perf_counter()
        base={'engine':self.name,'engine_version':self.version,'ontology_version':ontology['id'],'graph_snapshot':graph.get('snapshot'),'operation':operation,'status':'success','classification':'deterministic','findings':[],'derived_facts':[],'unsupported_constructs':[],'certificates':'Not generated; independent verification not exercised'}
        scope={'definition':'model','checks':'full','infer':'data','instances':'data','query':'full'}[operation]
        turtle,shapes,index=semantic.export(ontology,graph,scope); files={'model.ttl':turtle,'shapes.ttl':shapes}
        concept_names={c['id']:c['name'] for c in ontology['concepts']}
        def lint_findings(records):
            out=[]
            for r in records:
                if r['command']=='lint':
                    for i in r['result'].get('issues',[]):
                        kind,id=semantic.decode(i.get('entity',''))
                        out.append({'code':'lint:'+i.get('type','issue'),'severity':SEVERITY.get(i.get('severity'),'warning'),'title':'Ontology lint','message':i.get('message','').replace(i.get('entity',''),concept_names.get(id,id) if kind=='concept' else id or ''),'evidence':[],'concept':id if kind=='concept' else None})
                if r['command']=='defects':
                    for i in r['result'].get('defects',[]):
                        out.append({'code':'defect:'+str(i.get('kind','defect')),'severity':SEVERITY.get(i.get('severity'),'error'),'title':'Ontology defect','message':i.get('message') or json.dumps(i)[:300],'evidence':[],'concept':None})
            return out
        if operation=='definition':
            records,err=self.execute([('validate',['model.ttl']),('load',['model.ttl']),('stats',[]),('lint',['model.ttl'])],files,defects='model.ttl')
            base['findings']=lint_findings(records)
        elif operation=='checks':
            commands=[('load',['model.ttl']),('stats',[])]+[('query',[semantic.SPARQL_PREFIXES+c['query']]) for c in semantic.CHECKS]+[('lint',['model.ttl'])]
            records,err=self.execute(commands,files,defects='model.ttl')
            for check,r in zip(semantic.CHECKS,records[2:2+len(semantic.CHECKS)]):
                base['findings']+=[semantic.row_finding(check,row) for row in r['result'].get('results',[])]
            base['findings']+=lint_findings(records)
        elif operation=='infer':
            probes=[semantic.SPARQL_PREFIXES+'SELECT ?s ?t WHERE { ?s a ?t FILTER(STRSTARTS(STR(?s),"urn:ontoforge:record:")) }',
                    semantic.SPARQL_PREFIXES+'SELECT ?s ?t WHERE { ?s rdfs:subClassOf ?t FILTER(STRSTARTS(STR(?s),"urn:ontoforge:concept:") && ?s != ?t) }']
            records,err=self.execute([('load',['model.ttl'])]+[('query',[q]) for q in probes]+[('reason',['--profile','rdfs'])]+[('query',[q]) for q in probes],files)
            reason=records[3]['result']
            if not reason.get('fixpoint_reached'): raise ValueError('Engine inference did not reach a fixpoint')
            for i,label in ((0,'record type'),(1,'concept superclass')):
                before={(r['s'],r['t']) for r in records[1+i]['result'].get('results',[])}
                for r in records[4+i]['result'].get('results',[]):
                    if (r['s'],r['t']) in before: continue
                    (sk,sid),(tk,tid)=semantic.decode(r['s']),semantic.decode(r['t'])
                    if tk not in ('concept','module','vocab'): continue
                    type_label=concept_names.get(tid,tid) if tk=='concept' else f'{tid} module' if tk=='module' else semantic.LABELS.get(tid,tid)
                    base['derived_facts'].append({'kind':label,'subject':concept_names.get(sid,sid),'subject_id':sid,'entity':index['records'].get(r['s'][1:-1]),'type':type_label,'type_iri':r['t'],'premises':['Asserted rdf:type or rdfs:subClassOf plus RDFS subclass, domain and range entailment']})
            base['inference']={'inferred_triples':reason.get('inferred_count'),'iterations':reason.get('iterations'),'profile':reason.get('profile_used')}
        elif operation=='instances':
            records,err=self.execute([('load',['model.ttl']),('stats',[]),('shacl',['shapes.ttl'])],files)
            report=records[-1]['result']
            if report.get('conforms') is None and report.get('focus_nodes',0)==0:
                base['status']='success';base['unsupported_constructs'].append('No extracted records to validate in this snapshot')
            for v in report.get('violations',[]):
                _,shape=semantic.decode('<'+v.get('node_shape','')+'>'); _,field=semantic.decode('<'+v.get('path','')+'>'); record=v.get('focus_node','')
                name=field.split('/',1)[-1]; concept=concept_names.get(shape,shape); label=semantic.decode('<'+record+'>')[1].split('/',1)[-1]
                message={'minCount':f'{concept} record "{label}": required field {name} is missing in the stored record (a controller may still fill it when saving).','in':f'{concept} record "{label}": {name} is not one of its Select options.',
                         'datatype':f'{concept} record "{label}": {name} has a value of the wrong type.','nodeKind':f'{concept} record "{label}": {name} should reference a record.'}.get(v.get('constraint'),f'{concept} record "{label}": {v.get("message","constraint violation")}')
                # A missing required value in a fixture file can be filled by controller code at save time, so it is a warning.
                severity='warning' if v.get('constraint')=='minCount' else SEVERITY.get(v.get('severity'),'error')
                base['findings'].append({'code':'shacl:'+str(v.get('constraint','violation')),'severity':severity,'title':'Record does not match its DocType','message':message,'evidence':[index['records'][record]] if record in index['records'] else [],'concept':shape if shape in concept_names else None,'raw':v})
            base['validation']={'conforms':report.get('conforms'),'records_checked':report.get('focus_nodes',0),'violations':report.get('violation_count',len(report.get('violations',[])))}
        else:
            text=semantic.check_query(query or '')
            records,err=self.execute([('load',['model.ttl'])]+([('reason',['--profile','rdfs'])] if reasoning else [])+[('query',[text])],files)
            result=records[-1]['result']; base['query_result']=result
        if any(f['severity']!='info' for f in base['findings']): base['status']='findings'
        stats=next((r['result'] for r in records if r['command']=='stats'),None)
        base.update(raw_output=trimmed(records),stderr=err[:2000],scope=scope,store=stats,duration_ms=round((time.perf_counter()-start)*1000,3))
        return base

    def run(self,operation,ontology,graph,**kwargs):
        start=time.perf_counter()
        base={'engine':self.name,'engine_version':self.version,'ontology_version':ontology['id'],'graph_snapshot':graph.get('snapshot'),'operation':operation,'status':'unavailable','classification':'deterministic','findings':[],'derived_facts':[],'unsupported_constructs':[],'duration_ms':0,'certificates':'Not generated; independent verification not exercised'}
        if not self.available:return {**base,'error':self.reason,'unsupported_constructs':[operation]}
        if operation not in self.capabilities()['operations']:return {**base,'status':'unsupported','unsupported_constructs':[operation]}
        if operation!='demo' and not kwargs.get('classic'):
            try: return self.semantic_run(operation,ontology,graph,query=kwargs.get('query'),reasoning=bool(kwargs.get('reasoning')))
            except (OSError,ValueError,KeyError) as e: return {**base,'status':'failure','error':str(e)[:600],'duration_ms':round((time.perf_counter()-start)*1000,3)}
        try:
            if operation=='demo':
                import copy
                demo=copy.deepcopy(ontology);demo['concepts'] += [{'id':'Service','name':'Service','properties':[]},{'id':'APIService','name':'APIService','parent':'Service','properties':[{'name':'route','datatype':'string','required':True}]}];demo['rules']=[]
                dg={'snapshot':graph['snapshot'],'nodes':[{'id':'demo-api','name':'Illustrative APIService (not extracted)','type':'APIService','properties':{}}],'edges':[]}
                before=self.run('infer',demo,dg,classic=True);validation=self.run('instances',demo,dg,classic=True)
                if before['status'] in ['failure','unavailable'] or validation['status'] in ['failure','unavailable']:return {**base,'status':'failure','error':'External demonstration failed','raw_output':{'inference':before,'validation':validation}}
                demo['concepts'][-1]['parent']=None;after=self.run('infer',demo,dg,classic=True)
                return {**before,'operation':'demo','demonstration':True,'findings':validation['findings'],'status':'findings' if validation['findings'] else before['status'],'after_change':after,'explanation':'Isolated illustrative fact: real RDFS inference derives Service; real SHACL detects a missing route. Removing subclassOf removes the consequence. No external certificate was checked.','raw_validation':validation,'duration_ms':round((time.perf_counter()-start)*1000,3)}
            turtle,shapes,unsupported=mapping(ontology,graph)
            with tempfile.TemporaryDirectory(prefix='onto-engine-') as d:
                root=Path(d);model=root/'model.ttl';model.write_text(turtle);shape=root/'shapes.ttl';shape.write_text(shapes)
                commands=[{'command':'load','args':[str(model)]}]
                if operation=='infer':commands += [{'command':'reason','args':['--profile','rdfs']},{'command':'query','args':['SELECT ?s ?t WHERE { ?s a ?t }']}]
                elif operation=='instances':commands += [{'command':'shacl','args':[str(shape)]}]
                else:commands=[{'command':'validate','args':[str(model)]}]
                code,out,err=bounded_process([str(Path(self.path).resolve()),'--data-dir',str(root/'store'),'--no-connect','--json','batch','-'],json.dumps(commands))
                records=[json.loads(line) for line in out.splitlines() if line.strip()]
                if not records or any('error' in r.get('result',{}) for r in records):raise ValueError('Engine returned an error: '+out[:500])
                base.update(raw_output=records,stderr=err,exit_code=code,unsupported_constructs=unsupported,status='success')
                if operation=='infer':
                    reason=next(r['result'] for r in records if r['command']=='reason')
                    if not reason.get('fixpoint_reached'):raise ValueError('Engine inference did not reach a fixpoint')
                    asserted={(iri('entity',n['id']),iri('concept',n.get('concept',n['type']))) for n in graph['nodes']}
                    for row in records[-1]['result'].get('results',[]):
                        if (row['s'],row['t']) not in asserted and row['s'].startswith('<urn:ontoforge:entity:'):
                            base['derived_facts'].append({'entity':row['s'],'type':row['t'],'premises':['Application instanceOf assertion and explicit subclassOf chain'],'engine_evidence':reason.get('sample_inferences',[])})
                elif operation=='instances':
                    report=records[-1]['result']
                    if report.get('conforms') is None:base['status']='unsupported';base['unsupported_constructs'].append('SHACL result undetermined')
                    else:
                        base['findings']=[{'code':v.get('constraint','SHACL'),'message':v.get('message','Constraint violation'),'evidence':[v.get('focus_node','')],'raw':v} for v in report.get('violations',[])]
                        base['status']='findings' if report['conforms'] is False else 'success'
                    base['unsupported_constructs'].append('Closed-world relationship endpoint checks are not mapped')
                if code!=0 and base['status']=='success':base['status']='failure';base['error']='External process exited '+str(code)
                base['duration_ms']=round((time.perf_counter()-start)*1000,3);return base
        except (OSError,ValueError,KeyError) as e:
            return {**base,'status':'failure','error':str(e)[:600],'duration_ms':round((time.perf_counter()-start)*1000,3)}
