import os,json,copy,io,zipfile
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, SecretStr
from . import store as s
from . import pipeline as p
from . import ai
from . import semantic
from . import usage
from . import logic
from .models import SourceInput, AnalysisInput, EngineInput, Ontology
from .acquisition import allowed_local,github_repo,safe_relative,extract_zip,TOKENS,MAX_ARCHIVE,MAX_FILE,MAX_EXTRACT,AcquisitionError
from .engine import LightweightOntologyEngine,OpenOntologiesEngine

@asynccontextmanager
async def lifespan(app):
    s.init()
    for job in s.records('job'):
        if job['status'] in ['running','queued']:
            job.update(status='paused',error='Server restarted. Resume from the last persisted file checkpoint.'); s.put('job',job,job['source_id'],job['snapshot_id'])
            s.log(job['id'],'Server restarted; job paused. Resume to continue from the last checkpoint.','warning',job.get('stage',''))
    with s.connect() as c: interrupted=[(json.loads(d),src,snap) for d,src,snap in c.execute("SELECT data,source,snapshot FROM records WHERE kind='knowledge_set'")]
    for k,src,snap in interrupted:  # a domain guide being written when the server stopped
        if k.get('status') in ('queued','running'): k.update(status='failed',error='Server restarted while the domain guide was being written; generate it again (finished modules are cached).'); s.put('knowledge_set',k,src,snap)
    for h in s.records('hotspots'):  # git history being read when the server stopped
        if h.get('status')=='running': h.update(status='failed',message='Server restarted while the git history was being read; read it again.'); s.put('hotspots',h,h['id'])
    yield
app=FastAPI(title='OntoForge',description='Turn a codebase into a living business ontology and knowledge graph.',lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])
app.add_middleware(GZipMiddleware,minimum_size=4096)

@app.middleware('http')
async def local_guard(request:Request, call_next):
    # This local single-user service must not accept cross-origin browser mutations.
    origin=request.headers.get('origin')
    if origin:
        from urllib.parse import urlparse
        if urlparse(origin).hostname not in ['localhost','127.0.0.1','::1']:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail':'Cross-origin access is disabled for this local application'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    return response

@app.exception_handler(AcquisitionError)
async def acquisition_error(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse({'detail':str(exc)},status_code=400)

def require(kind,id):
    value=s.get(kind,id)
    if not value: raise HTTPException(404,f'{kind} not found')
    return value

def snapshot_for(source_id,snapshot_id=None):
    source=require('source',source_id)
    if snapshot_id:
        snap=require('snapshot',snapshot_id)
        if snap['source_id']!=source_id: raise HTTPException(400,'Snapshot does not belong to source')
        return snap
    snaps=s.records('snapshot',source_id)
    return snaps[0] if snaps else None

def ontology_for(source_id,snapshot_id):
    versions=s.records('ontology',source_id,snapshot_id)
    return versions[0] if versions else None

@app.get('/api/state')
def state(source_id:str|None=None,snapshot_id:str|None=None):
    sources=s.records('source'); sources.reverse()
    for src in sources:
        latest=s.get('snapshot',src.get('latest_snapshot',''))
        src['analysed_commit']=latest.get('commit') if latest else None
    source_id=source_id or (sources[0]['id'] if sources else None)
    snap=snapshot_for(source_id,snapshot_id) if source_id else None
    sid=snap['id'] if snap else None
    stats=s.counts(sid) if sid else dict.fromkeys(['discovered','acquired','parsed','excluded','unsupported','failed','pending'],0)
    with s.connect() as c:
        types=[dict(r) for r in c.execute('SELECT type,COUNT(*) count FROM entities WHERE snapshot=? GROUP BY type ORDER BY count DESC',(sid,))]
        edges=c.execute('SELECT COUNT(*) FROM edges WHERE snapshot=?',(sid,)).fetchone()[0]
        formats=[dict(r) for r in c.execute("SELECT json_extract(data,'$.format') format,COUNT(*) count FROM files WHERE snapshot=? GROUP BY 1",(sid,))]
    return {'sources':sources,'source_id':source_id,'snapshot':snap,'snapshots':s.records('snapshot',source_id) if source_id else [],'counts':stats,'entity_types':types,'entity_count':sum(r['count'] for r in types),'edge_count':edges,'formats':formats,'jobs':s.records('job',source_id) if source_id else [],'proposals':s.records('proposal',source_id,sid) if sid else [],'ontology':ontology_for(source_id,sid) if sid else None,'versions':s.records('ontology',source_id) if source_id else [],'mappings':s.records('mapping',source_id) if source_id else [],'events':s.records('event',source_id)[:40] if source_id else [],'engine_runs':s.records('engine_run',source_id)[:30] if source_id else []}

@app.post('/api/sources')
def add_source(body:SourceInput):
    obj=body.model_dump(); obj['id']=s.uid('src_'); obj['created_at']=s.now()
    if body.kind=='local': obj['location']=str(allowed_local(body.location))
    if body.kind=='github': github_repo(body.location); safe_relative(body.subdirectory)
    if body.kind=='sample': obj.update(name='Meridian Commerce',location='sample/meridian',sample=True)
    s.put('source',obj); s.event('Source connected',obj['name'],obj['id'])
    return obj

class SourceUpdate(BaseModel):
    exclusions:list[str]
@app.patch('/api/sources/{id}')
def source_update(id:str,body:SourceUpdate):
    source=require('source',id); source['exclusions']=body.exclusions
    s.put('source',source); return source

@app.get('/api/folders')
def folders(path:str=''):
    if not path: path=os.getenv('ONTO_LOCAL_ROOTS',str(s.ROOT)).split(os.pathsep)[0]
    root=allowed_local(path)
    return {'path':str(root),'parent':str(root.parent) if root.parent!=root else None,'directories':[{'name':x.name,'path':str(x)} for x in sorted(root.iterdir()) if x.is_dir() and not x.is_symlink() and not x.name.startswith('.')][:200],'limit':200,'note':'Folders are on the backend machine. Directory browser shows at most 200 children.'}

@app.post('/api/sources/upload')
async def upload(file:UploadFile=File(...)):
    id=s.uid('src_'); archive=s.DATA/(id+'.zip'); total=0
    try:
        with archive.open('wb') as out:
            while chunk:=await file.read(65536):
                total+=len(chunk)
                if total>MAX_ARCHIVE: raise HTTPException(413,'Upload exceeds configured archive byte budget')
                out.write(chunk)
        blocked=extract_zip(archive,s.DATA/'uploads'/id)
    except zipfile.BadZipFile: raise HTTPException(400,'Upload is not a valid ZIP archive')
    finally: archive.unlink(missing_ok=True)
    source={'id':id,'kind':'upload','name':file.filename or 'Uploaded source','location':'Browser ZIP upload','created_at':s.now(),'exclusions':[],'upload_blocked':blocked}
    s.put('source',source); s.event('ZIP uploaded',f'{len(blocked)} entries blocked',id)
    return source

class Connection(BaseModel): token:SecretStr
@app.post('/api/sources/{id}/connection')
def connect(id:str,body:Connection):
    source=require('source',id)
    if source['kind']!='github': raise HTTPException(400,'GitHub sources only')
    TOKENS[id]=body.token.get_secret_value(); return {'connected':True,'storage':'Server memory only; reconnect after restart'}
@app.delete('/api/sources/{id}/connection')
def disconnect(id:str):
    TOKENS.pop(id,None); return {'connected':False,'environment_token_present':bool(os.getenv('GITHUB_TOKEN'))}

@app.post('/api/sources/{id}/analyse')
def analyse(id:str,body:AnalysisInput):
    source=require('source',id)
    if any(j['status'] in ['running','queued','paused'] for j in s.records('job',id)): raise HTTPException(409,'Resume or cancel the existing job before starting a new snapshot')
    if body.mode=='live' and not body.share_confirmed: raise HTTPException(400,'Confirm excerpt sharing separately before live model analysis')
    if body.mode=='sample' and source['kind']!='sample': raise HTTPException(400,'Sample proposals require the sample repository')
    snapshot={'id':s.uid('snap_'),'source_id':id,'created_at':s.now(),'status':'new','commit':None,'acquisition_blocked':source.get('upload_blocked',[])}
    job={'id':s.uid('job_'),'source_id':id,'snapshot_id':snapshot['id'],'status':'queued','stage':'Acquisition','created_at':s.now(),'updated_at':s.now(),'mode':body.mode,'cache_hits':0,
         'budget_usd':body.budget_usd,'passes':body.passes}
    s.put('snapshot',snapshot,id); s.put('job',job,id,snapshot['id']); p.submit(job['id']); return job

@app.post('/api/sources/{id}/generate')
def generate(id:str,body:AnalysisInput):
    """Generative enrichment of the latest analysed snapshot as a new ontology version (no new download or parse)."""
    require('source',id)
    if any(j['status'] in ['running','queued','paused'] for j in s.records('job',id)): raise HTTPException(409,'Resume or cancel the existing job first')
    if not body.share_confirmed: raise HTTPException(400,'Confirm excerpt sharing separately before live model analysis')
    snap=snapshot_for(id)
    if not snap or snap.get('status') not in ('complete','partial'): raise HTTPException(400,'Analyse the source first')
    current=ontology_for(id,snap['id'])
    if current:
        draft=copy.deepcopy(current); draft.update(id=s.uid('ont_'),status='draft',created_at=s.now(),based_on=current['id']); draft.pop('approved_at',None)
        s.put('ontology',draft,id,snap['id'])
    job={'id':s.uid('job_'),'source_id':id,'snapshot_id':snap['id'],'status':'queued','stage':'Graph construction','created_at':s.now(),'updated_at':s.now(),'mode':'live','cache_hits':0,
         'budget_usd':body.budget_usd,'passes':body.passes,'generate_only':True}
    s.put('job',job,id,snap['id']); s.log(job['id'],'Generative enrichment requested for the current snapshot; files are not downloaded or parsed again',stage='Graph construction')
    p.submit(job['id']); return job

@app.get('/api/sources/{id}/generation-plan')
def generation_plan(id:str,passes:str=''):
    """Calls, tokens and cost of discovery and enrichment for the latest snapshot, without calling any model."""
    from . import generative
    snap=snapshot_for(id)
    if not snap: raise HTTPException(400,'Analyse the source first')
    ont=ontology_for(id,snap['id'])
    if not ont: raise HTTPException(400,'Ontology generation is not ready')
    selected=[x for x in passes.split(',') if x in generative.PASSES] or list(generative.PASSES)
    runner=generative.Runner(snap,copy.deepcopy(ont)); plan=runner.plan(selected)
    c=ai.config(); table=usage.prices(); cheap=c.get('models',{}).get('cheap') or c['model']
    candidates=generative.discovery_candidates(snap['id'],ont)
    batches=-(-len(candidates)//p.DISCOVERY_BATCH); cached=0; tokens_in=0
    for i in range(0,len(candidates),p.DISCOVERY_BATCH):
        batch=[{'id':n['id'],'name':n['name'],'excerpt':n['excerpt'][:p.DISCOVERY_EXCERPT]} for n in candidates[i:i+p.DISCOVERY_BATCH]]
        if generative.cache_get(generative.cache_key('discover',generative.PROMPT_VERSION,cheap,json.dumps(batch,sort_keys=True))) is not None: cached+=1; continue
        tokens_in+=(len(ai.SYSTEM_PROMPT)+1800+len(json.dumps(batch)))/3.6
    tokens_out=(batches-cached)*700; price=usage.price_for(c['provider'],cheap,table)
    discovery={'pass':'discover','title':'Concept discovery','tier':'cheap','model':cheap,'concepts':len(candidates),'calls':batches,'cached_calls':cached,
               'input_tokens':round(tokens_in),'output_tokens':tokens_out,'cost':usage.cost(price,tokens_in,tokens_out) if price else None}
    rows=[discovery,*plan]; costs=[r['cost'] for r in rows if r['calls']-r['cached_calls']>0]
    return {'snapshot':snap['id'],'provider':c['provider'],'models':c.get('models',{}),'passes':rows,'calls':sum(r['calls']-r['cached_calls'] for r in rows),
            'tokens':sum(r['input_tokens']+r['output_tokens'] for r in rows),'cost':sum(costs) if all(x is not None for x in costs) else None,
            'unpriced':sorted({r['model'] for r in rows if r['cost'] is None and r['calls']-r['cached_calls']>0}),'configured':c['configured'],'error':c['error']}

@app.post('/api/jobs/{id}/{action}')
def control(id:str,action:str):
    job=require('job',id)
    if action not in ['pause','resume','cancel','retry']: raise HTTPException(400,'Unknown job action')
    if action=='pause' and job['status'] not in ['queued','running']: raise HTTPException(409,'Only active jobs can be paused')
    if action in ['resume','retry'] and job['status'] not in ['paused','failed','cancelled']: raise HTTPException(409,'Job is not resumable')
    if action=='retry':
        with s.connect() as c: c.execute("UPDATE files SET status='pending' WHERE snapshot=? AND status='failed' AND hash!=''",(job['snapshot_id'],))
    job.update(status={'pause':'paused','cancel':'cancelled','resume':'queued','retry':'queued'}[action],error=None,updated_at=s.now())
    s.put('job',job,job['source_id'],job['snapshot_id'])
    s.log(id,{'pause':'Pause requested; the worker stops at its next checkpoint','cancel':'Cancel requested; the worker stops at its next checkpoint',
              'resume':'Resume requested; continuing from the last checkpoint','retry':'Retry requested; failed files are queued again and work continues from the last checkpoint'}[action],
          'warning' if action in ('pause','cancel') else 'info',job.get('stage',''))
    if action in ['resume','retry']: p.submit(id)
    return job

@app.get('/api/jobs/{id}/logs')
def job_logs(id:str,after:int=0,limit:int=500):
    """Incremental activity log: pass the last seen `id` as `after`. The job is returned too, for live progress."""
    job=require('job',id)
    return {'job':job,'logs':s.logs(id,after,max(1,min(limit,2000)))}

@app.get('/api/files')
def files(snapshot_id:str,status:str='',q:str='',offset:int=0,limit:int=50):
    limit=max(1,min(limit,200)); offset=max(0,offset); where='snapshot=?'; args=[snapshot_id]
    if status: where+=' AND status=?'; args.append(status)
    if q: where+=' AND path LIKE ?'; args.append('%'+q+'%')
    with s.connect() as c:
        total=c.execute('SELECT COUNT(*) FROM files WHERE '+where,args).fetchone()[0]
        rows=c.execute('SELECT data,status FROM files WHERE '+where+' ORDER BY path LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
    return {'items':[{**json.loads(r[0]),'status':r[1]} for r in rows],'total':total}

@app.get('/api/graph')
def graph(snapshot_id:str,q:str='',type:str='',offset:int=0,limit:int=60,neighbor:str='',overview:bool=False):
    limit=max(1,min(limit,150)); offset=max(0,offset); where='snapshot=?'; args=[snapshot_id]
    if overview: where+=" AND type!='Column'"
    if q: where+=' AND (name LIKE ? OR path LIKE ?)'; args+=['%'+q+'%']*2
    if type: where+=' AND type=?'; args.append(type)
    if neighbor:
        where+=' AND (id=? OR id IN (SELECT target FROM edges WHERE snapshot=? AND source=?) OR id IN (SELECT source FROM edges WHERE snapshot=? AND target=?))'; args += [neighbor,snapshot_id,neighbor,snapshot_id,neighbor]
    with s.connect() as c:
        total=c.execute('SELECT COUNT(*) FROM entities WHERE '+where,args).fetchone()[0]
        # Most connected source files first; a file's entities stay together so each page carries its edges.
        ranked="WITH ranked AS (SELECT n.path p,COUNT(*) d FROM edges g JOIN entities n ON n.snapshot=g.snapshot AND n.id=g.source WHERE g.snapshot=? GROUP BY n.path) SELECT data FROM entities LEFT JOIN ranked ON ranked.p=entities.path WHERE "
        nodes=[json.loads(r[0]) for r in c.execute(ranked+where+' ORDER BY COALESCE(ranked.d,0) DESC,path,name LIMIT ? OFFSET ?',[snapshot_id]+args+[limit,offset])]
        ids=[n['id'] for n in nodes]; marks=','.join('?'*len(ids))
        edges=[json.loads(r[0]) for r in c.execute(f'SELECT data FROM edges WHERE snapshot=? AND source IN ({marks}) AND target IN ({marks})',[snapshot_id]+ids+ids)] if ids else []
    return {'snapshot':snapshot_id,'nodes':nodes,'edges':edges,'total':total,'offset':offset,'limit':limit}

@app.get('/api/graph/full')
def graph_full(snapshot_id:str):
    """Whole snapshot graph as compact columns for client-side rendering; details come from /api/entities."""
    require('snapshot',snapshot_id)
    with s.connect() as c:
        nodes=c.execute("SELECT id,name,type,path,json_extract(data,'$.line') FROM entities WHERE snapshot=? ORDER BY path,name",(snapshot_id,)).fetchall()
        edges=c.execute("SELECT source,target,type,json_extract(data,'$.method') FROM edges WHERE snapshot=?",(snapshot_id,)).fetchall()
    return {'snapshot':snapshot_id,'node_fields':['id','name','type','path','line'],'nodes':[list(n) for n in nodes],'edge_fields':['source','target','type','method'],'edges':[list(e) for e in edges]}

@app.get('/api/entities/{id}')
def entity(id:str,snapshot_id:str):
    with s.connect() as c: row=c.execute('SELECT data FROM entities WHERE snapshot=? AND id=?',(snapshot_id,id)).fetchone()
    if not row: raise HTTPException(404,'Entity not found')
    return json.loads(row[0])

@app.put('/api/ontologies/{id}')
def edit_ontology(id:str,body:Ontology):
    previous=require('ontology',id)
    obj=body.model_dump(); obj.update(id=s.uid('ont_'),status='draft',based_on=id,snapshot_id=previous['snapshot_id'],created_at=s.now())
    source_id=require('snapshot',previous['snapshot_id'])['source_id']
    s.put('ontology',obj,source_id,previous['snapshot_id']); s.event('Ontology draft created',obj['id'],source_id,previous['snapshot_id']); return obj

@app.post('/api/ontologies/{id}/approve')
def approve(id:str):
    ont=require('ontology',id)
    if ont['status']=='approved': return ont
    findings=p.approve_ontology(ont)
    if findings: raise HTTPException(400,findings)
    source_id=require('snapshot',ont['snapshot_id'])['source_id']
    s.put('ontology',ont,source_id,ont['snapshot_id']); s.event('Ontology version approved',ont['id'],source_id,ont['snapshot_id']); return ont

@app.get('/api/settings')
def settings():
    return {'engines':[LightweightOntologyEngine().capabilities(),OpenOntologiesEngine().capabilities()],'model':ai.public_config(),'budgets':{'file_bytes':MAX_FILE,'archive_bytes':MAX_ARCHIVE,'extraction_bytes':MAX_EXTRACT,'concurrency':int(os.getenv('ONTO_CONCURRENCY','2'))},'local_roots':os.getenv('ONTO_LOCAL_ROOTS',str(s.ROOT)).split(os.pathsep),'github_environment_connection':bool(os.getenv('GITHUB_TOKEN')),'session_connections':list(TOKENS),'exclusions':p.DEFAULT_EXCLUSIONS}

class AIConfigInput(BaseModel):
    provider:str
    model:str|None=None
    base_url:str|None=None
    region:str|None=None
    profile:str|None=None
    cheap_model:str|None=None
    strong_model:str|None=None
    timeout_seconds:float|None=None

@app.put('/api/ai/config')
def ai_config(body:AIConfigInput):
    try: return ai.save_config(body.model_dump(exclude_none=True))
    except ValueError as e: raise HTTPException(400,str(e))

class AIKeyInput(BaseModel):
    provider:str
    key:SecretStr

@app.post('/api/ai/key')
def ai_key(body:AIKeyInput):
    """API keys stay in server memory only; they are never stored or returned."""
    try: ai.set_key(body.provider,body.key.get_secret_value())
    except ValueError as e: raise HTTPException(400,str(e))
    return ai.public_config()

@app.delete('/api/ai/key/{provider}')
def ai_key_remove(provider:str):
    ai.SESSION_KEYS.pop(provider,None); return ai.public_config()

@app.get('/api/ai/usage')
def ai_usage(days:int=30,source_id:str|None=None):
    return usage.summary(max(0,min(days,3650)),source_id)

class PriceInput(BaseModel):
    provider:str
    model:str
    input:float|None=None
    output:float|None=None

@app.put('/api/ai/prices')
def ai_price(body:PriceInput):
    try: return {'prices':usage.set_price(body.provider,body.model,body.input,body.output)}
    except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/model/status')
def model_status():
    return ai.connection_status()

def complete_graph(snapshot_id):
    # Engine runs and exports consume complete persisted graphs, not the paginated visual viewport.
    with s.connect() as c:
        return {'snapshot':snapshot_id,'nodes':[json.loads(r[0]) for r in c.execute('SELECT data FROM entities WHERE snapshot=?',(snapshot_id,))],'edges':[json.loads(r[0]) for r in c.execute('SELECT data FROM edges WHERE snapshot=?',(snapshot_id,))]}

def semantic_inputs(source_id):
    snap=snapshot_for(source_id)
    if not snap: raise HTTPException(400,'Analyse a source first')
    ont=ontology_for(source_id,snap['id'])
    if not ont: raise HTTPException(400,'Ontology generation is not ready')
    return snap,logic.without_disputed(ont,source_id),complete_graph(snap['id'])

@app.get('/api/semantic')
def semantic_info():
    engine=OpenOntologiesEngine()
    return {'available':engine.available,'reason':None if engine.available else engine.reason,'samples':semantic.SAMPLE_QUERIES,'prefixes':semantic.SPARQL_PREFIXES,'scopes':['model','data','full','shapes']}

@app.get('/api/semantic/export')
def semantic_export(source_id:str,scope:str='model'):
    if scope not in (*semantic.SCOPES,'shapes'): raise HTTPException(400,'Unknown export scope')
    snap,ont,g=semantic_inputs(source_id)
    turtle,shapes,_=semantic.export(ont,g,'model' if scope=='shapes' else scope)
    name=''.join(ch if ch.isalnum() else '-' for ch in (s.get('source',source_id) or {}).get('name','ontology')).strip('-').lower() or 'ontology'
    return Response(shapes if scope=='shapes' else turtle,media_type='text/turtle; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{name}-{scope}.ttl"'})

class QueryInput(BaseModel):
    query:str
    reasoning:bool=False

@app.post('/api/semantic/query')
def semantic_query(source_id:str,body:QueryInput):
    """Ad-hoc read-only SPARQL over the full export in an isolated in-memory store; not recorded as an engine run."""
    engine=OpenOntologiesEngine()
    if not engine.available: raise HTTPException(409,engine.reason)
    try: semantic.check_query(body.query)
    except ValueError as e: raise HTTPException(400,str(e))
    snap,ont,g=semantic_inputs(source_id)
    r=engine.run('query',ont,g,query=body.query,reasoning=body.reasoning)
    if r['status']=='failure': raise HTTPException(400,r.get('error','Query failed'))
    result=r.get('query_result',{}); rows=result.get('results') if isinstance(result.get('results'),list) else []
    def cell(term):
        kind,value=semantic.decode(term); return {'kind':kind or 'literal','value':value}
    return {'variables':result.get('variables',[]),'rows':[{k:cell(v) for k,v in row.items()} for row in rows[:1000]],'total':len(rows),'truncated':len(rows)>1000,
            'boolean':result.get('result') if isinstance(result.get('result'),bool) else None,'duration_ms':r['duration_ms'],'reasoning':body.reasoning,'snapshot':snap['id']}

# ---- Outputs generated from the ontology ----
def current_ontology(source_id):
    snap=snapshot_for(source_id)
    if not snap: raise HTTPException(400,'Analyse a source first')
    ont=ontology_for(source_id,snap['id'])
    if not ont: raise HTTPException(400,'Ontology generation is not ready')
    return snap,ont

@app.get('/api/outputs/docs')
def output_docs(source_id:str,format:str='json'):
    from . import generative
    snap,ont=current_ontology(source_id); doc=generative.documentation(ont,snap['id'])
    if format!='md': return doc
    title=(s.get('source',source_id) or {}).get('name','Ontology')
    name=''.join(ch if ch.isalnum() else '-' for ch in title).strip('-').lower() or 'ontology'
    return Response(generative.documentation_markdown(doc,title),media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{name}-data-dictionary.md"'})

@app.get('/api/outputs/schemas')
def output_schemas(source_id:str,format:str='openapi'):
    from . import generative
    snap,ont=current_ontology(source_id); title=(s.get('source',source_id) or {}).get('name','Ontology'); ont=logic.without_disputed(ont,source_id)
    body=generative.openapi(ont,title) if format=='openapi' else {'$schema':'https://json-schema.org/draft/2020-12/schema','$defs':generative.json_schemas(ont)}
    name=''.join(ch if ch.isalnum() else '-' for ch in title).strip('-').lower() or 'ontology'
    return Response(json.dumps(body,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="{name}-{format}.json"'})

class SampleInput(BaseModel):
    concept_id:str
    count:int=10
    seed:int=7

@app.post('/api/outputs/sample')
def output_sample(source_id:str,body:SampleInput):
    from . import generative
    snap,ont=current_ontology(source_id)
    if not any(c['id']==body.concept_id for c in ont['concepts']): raise HTTPException(404,'Concept not found')
    ont=logic.without_disputed(ont,source_id); records=generative.synthetic_records(ont,body.concept_id,body.count,body.seed)
    return {'records':records,'problems':generative.check_records(ont,body.concept_id,records)}

class AskInput(BaseModel):
    question:str
    tier:str='cheap'

@app.post('/api/outputs/ask')
def output_ask(source_id:str,body:AskInput):
    from . import generative
    if not body.question.strip(): raise HTTPException(400,'Ask a question')
    snap,ont=current_ontology(source_id)
    try: return generative.ask(ont,snap,body.question,'strong' if body.tier=='strong' else 'cheap')
    except ValueError as e: raise HTTPException(400,str(e))

# ---- Chat: conversations saved per source, answers grounded in the source's data ----
@app.get('/api/chats')
def chat_list(source_id:str):
    from . import chat
    require('source',source_id)
    return {'conversations':chat.conversations(source_id)}

@app.get('/api/chats/{id}')
def chat_get(id:str):
    c=s.get('chat',id)
    if not c: raise HTTPException(404,'Conversation not found')
    return c

class ChatInput(BaseModel):
    message:str
    conversation_id:str=''
    tier:str='cheap'

@app.post('/api/chats')
def chat_send(source_id:str,body:ChatInput):
    """Send a message; a new conversation is started when no conversation id is given."""
    from . import chat
    source=require('source',source_id); snap,ont=current_ontology(source_id)
    if body.conversation_id:
        conv=s.get('chat',body.conversation_id)
        if not conv or conv.get('source_id')!=source_id: raise HTTPException(404,'Conversation not found for this source')
    else: conv={'id':s.uid('chat_'),'source_id':source_id,'created_at':s.now(),'messages':[]}
    try: return chat.reply(source,snap,ont,conv,body.message,'strong' if body.tier=='strong' else 'cheap')
    except ValueError as e: raise HTTPException(400,str(e))

class ChatRename(BaseModel):
    title:str

@app.patch('/api/chats/{id}')
def chat_rename(id:str,body:ChatRename):
    c=s.get('chat',id)
    if not c: raise HTTPException(404,'Conversation not found')
    c['title']=body.title.strip()[:120] or c.get('title'); s.put('chat',c,c['source_id']); return c

@app.delete('/api/chats/{id}')
def chat_delete(id:str):
    c=s.get('chat',id)
    if not c: raise HTTPException(404,'Conversation not found')
    with s.connect() as db: db.execute("DELETE FROM records WHERE kind='chat' AND id=?",(id,))
    return {'deleted':id}

@app.get('/api/outputs/changes')
def output_changes(source_id:str):
    from . import generative
    snap,ont=current_ontology(source_id)
    previous=s.get('ontology',ont['based_on']) if ont.get('based_on') else None
    return generative.changes(ont,previous)

@app.get('/api/quality')
def quality(source_id:str):
    from . import generative
    snap,ont=current_ontology(source_id)
    rows=generative.claims(snap['id'])
    stats={}
    for c in rows: stats.setdefault(c['pass'],{}).setdefault(c['status'],0); stats[c['pass']][c['status']]+=1
    return {'report':(s.records('quality_report',source_id,snap['id']) or [None])[0],'claims':stats,'recent':[{k:c[k] for k in ('pass','statement','status','reason','model','tier','confidence','cached')} for c in rows[-200:][::-1]]}

class EvaluateInput(BaseModel):
    sample:int=8

@app.post('/api/quality/evaluate')
def quality_evaluate(source_id:str,body:EvaluateInput):
    """Re-judge a sample of the current snapshot's model statements; wrong ones are reverted in a new version."""
    from . import generative
    snap,ont=current_ontology(source_id)
    draft=copy.deepcopy(ont); draft.update(id=s.uid('ont_'),status='draft',created_at=s.now(),based_on=ont['id']); draft.pop('approved_at',None)
    try: report=generative.evaluate(snap,draft,None,max(1,min(body.sample,40)))
    except ValueError as e: raise HTTPException(400,str(e))
    if not report: raise HTTPException(400,'No model statements to judge for this snapshot')
    if report['reverted']:
        findings=p.approve_ontology(draft); s.put('ontology',draft,source_id,snap['id'])
    return report

@app.get('/api/claims')
def claims_for(source_id:str,concept_id:str):
    from . import generative
    snap,ont=current_ontology(source_id)
    return [{k:c[k] for k in ('pass','statement','status','reason','model','tier','confidence','cached','created')} for c in generative.claims(snap['id']) if c['subject']==concept_id]

# ---- Business rules, logic and reasoning ----
def file_name(source_id,suffix):
    name=''.join(ch if ch.isalnum() else '-' for ch in (s.get('source',source_id) or {}).get('name','ontology')).strip('-').lower() or 'ontology'
    return f'attachment; filename="{name}-{suffix}"'

@app.get('/api/outputs/rules')
def output_rules(source_id:str,format:str='json'):
    """The rule catalogue: every rule in plain language with category, trigger, source code and expert review."""
    from . import generative
    snap,ont=current_ontology(source_id); graph=generative.Graph(snap['id'])
    verdicts={}
    for c in generative.claims(snap['id']):
        if c['pass']=='rules' and (c.get('before') or {}).get('rule_id') and (c['reason'] or '').startswith('judge:'): verdicts[c['before']['rule_id']]=c['reason'][7:].strip()
    rows=logic.catalog(ont,snap['id'],source_id,generative.module_of,graph,verdicts)
    title=(s.get('source',source_id) or {}).get('name','Ontology')
    if format=='md': return Response(logic.catalog_markdown(rows,title),media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':file_name(source_id,'rules.md')})
    if format=='csv': return Response('\ufeff'+logic.catalog_csv(rows),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':file_name(source_id,'rules.csv')})
    return {'rules':rows,'ontology':ont['id'],'statuses':list(logic.REVIEW_STATUSES)}

class ReviewInput(BaseModel):
    rule_id:str
    status:str
    comment:str=''

@app.post('/api/rules/review')
def review_rule(source_id:str,body:ReviewInput):
    """A domain expert's verdict on one rule. Disputed rules are left out of exports, sample data and checks; nothing is deleted."""
    if body.status not in logic.REVIEW_STATUSES: raise HTTPException(400,'Status must be one of '+', '.join(logic.REVIEW_STATUSES))
    if len(body.comment)>2000: raise HTTPException(400,'Comment is longer than 2,000 characters')
    snap,ont=current_ontology(source_id)
    if not any(r['id']==body.rule_id for r in ont.get('rules',[])): raise HTTPException(404,'Rule not found in the current ontology')
    record={'id':logic.review_id(source_id,body.rule_id),'rule_id':body.rule_id,'status':body.status,'comment':body.comment.strip(),'at':s.now(),'ontology':ont['id']}
    s.put('rule_review',record,source_id); s.event('Rule reviewed',f"{body.rule_id}: {body.status}",source_id,snap['id'])
    return record

@app.get('/api/outputs/reasoning')
def output_reasoning(source_id:str):
    """OWL 2 RL reasoning over the current ontology and extracted records, with explanations."""
    snap,ont=current_ontology(source_id)
    result=logic.reason(logic.without_disputed(ont,source_id),snap['id'])
    names={c['id']:c['name'] for c in ont['concepts']}
    for x in result['contradictions']: x['concept_names']=[names.get(i,i) for i in x.get('concepts',[])]
    return {**result,'ontology':ont['id']}

@app.post('/api/sources/{id}/derive-logic')
def derive_logic(id:str):
    """Re-read rules, lifecycles and disjointness from the analysed code into a new ontology version (no model calls)."""
    snap,ont=current_ontology(id)
    draft=copy.deepcopy(ont); draft.update(id=s.uid('ont_'),status='draft',created_at=s.now(),based_on=ont['id']); draft.pop('approved_at',None)
    found=logic.derive(snap['id'],draft); findings=p.approve_ontology(draft); s.put('ontology',draft,id,snap['id'])
    s.event('Rules and logic refreshed',f"{sum(found['rules'].values())} code rules, {found['disjoint']} disjointness group(s)",id,snap['id'])
    return {**found,'ontology':draft['id'],'approved':not findings,'findings':findings or []}

# ---- Domain Expertise Knowledge Set ----
class KnowledgeInput(BaseModel):
    tier:str='strong'
    include_general:bool=True
    share_confirmed:bool=False

# ---- Graph files for other tools (GraphML, GEXF, Neo4j, Mermaid) ----
@app.get('/api/export/file')
def export_graph_file(source_id:str,format:str,scope:str='both',module:str=''):
    from . import graph_export
    snap,ont=current_ontology(source_id); title=(s.get('source',source_id) or {}).get('name','Ontology')
    try: data=graph_export.export_file(snap,ont,format,scope,title,module or None)
    except ValueError as e: raise HTTPException(400,str(e))
    ext,media=graph_export.FILE_FORMATS[format]
    return Response(data,media_type=media,headers={'Content-Disposition':file_name(source_id,f"{'domain' if format=='mermaid' else scope}.{ext if format!='neo4j' else 'neo4j.zip'}")})

# ---- Ontology timeline ----
@app.get('/api/timeline')
def ontology_timeline(source_id:str):
    from . import history
    require('source',source_id)
    return history.timeline(source_id)

@app.get('/api/timeline/stack')
def ontology_timeline_stack(source_id:str):
    from . import history
    require('source',source_id)
    return history.stack(source_id)

@app.get('/api/timeline/version')
def ontology_timeline_version(source_id:str,version:str):
    from . import history
    try: return history.version_changes(source_id,version)
    except ValueError as e: raise HTTPException(404,str(e))

# ---- Impact analysis ----
@app.get('/api/impact')
def impact_analysis(node_id:str,snapshot_id:str='',source_id:str='',depth:int=3):
    """What a change to a concept or code entity affects, with the chain that explains each item."""
    from . import impact
    if snapshot_id:
        snap=s.get('snapshot',snapshot_id)
        if not snap: raise HTTPException(404,'Unknown snapshot')
        ont=ontology_for(snap['source_id'],snap['id'])
        if not ont: raise HTTPException(400,'Ontology generation is not ready')
    else: snap,ont=current_ontology(source_id)
    try: return impact.impact(snap,ont,node_id,max(1,min(depth,6)))
    except ValueError as e: raise HTTPException(404,str(e))

# ---- Git hotspots ----
class HotspotInput(BaseModel):
    months:int=12

@app.post('/api/sources/{id}/hotspots')
def read_hotspots(id:str,body:HotspotInput):
    """Read the git history of the source (commit metadata only) in the background."""
    from . import history
    source=require('source',id)
    if source['kind'] not in ('github','local'): raise HTTPException(400,'Git history is only available for GitHub sources and local git folders')
    try: history.start(source,max(1,min(body.months,60)))
    except ValueError as e: raise HTTPException(409 if 'already' in str(e) else 400,str(e))
    s.event('Git history requested',f'last {body.months} months',id)
    return {'status':'running'}

@app.get('/api/hotspots')
def hotspot_list(source_id:str):
    from . import history
    snap,ont=current_ontology(source_id)
    return history.hotspots(source_id,snap,ont)

@app.get('/api/hotspots/paths')
def hotspot_paths(snapshot_id:str):
    from . import history
    snap=s.get('snapshot',snapshot_id)
    if not snap: raise HTTPException(404,'Unknown snapshot')
    return history.path_heat(snap['source_id'])

# ---- External graph tools: GraphXR (download) and Graphistry (upload) ----
@app.get('/api/export/graphxr')
def export_graphxr(source_id:str,scope:str='both'):
    """nodes.csv + edges.csv (+ graph.json and import steps) for Kineviz GraphXR."""
    from . import graph_export as external
    snap,ont=current_ontology(source_id); title=(s.get('source',source_id) or {}).get('name','Ontology')
    try: data,_,_=external.graphxr_zip(snap,ont,scope,title)
    except ValueError as e: raise HTTPException(400,str(e))
    return Response(data,media_type='application/zip',headers={'Content-Disposition':file_name(source_id,f'graphxr-{scope}.zip')})

@app.get('/api/export/graphistry')
def graphistry_status(source_id:str='',scope:str='both'):
    """Whether Graphistry is set up, where data would go, and how much would be sent."""
    from . import graph_export as external
    cfg={k:v for k,v in external.graphistry_config().items() if not k.startswith('_')}
    if source_id:
        snap,ont=current_ontology(source_id)
        try: nodes,edges=external.graph_tables(snap,ont,scope); cfg.update(nodes=len(nodes),edges=len(edges))
        except ValueError as e: raise HTTPException(400,str(e))
    return cfg

class GraphistryInput(BaseModel):
    scope:str='both'
    share_confirmed:bool=False

@app.post('/api/export/graphistry')
def graphistry_upload(source_id:str,body:GraphistryInput):
    """Upload the graph to the configured Graphistry server (only after explicit confirmation) and return the link."""
    from . import graph_export as external
    if not body.share_confirmed: raise HTTPException(400,'Confirm that the graph may be uploaded to Graphistry')
    snap,ont=current_ontology(source_id); title=(s.get('source',source_id) or {}).get('name','Ontology')
    try: result=external.graphistry_upload(snap,ont,body.scope,title)
    except ValueError as e: raise HTTPException(400,str(e))
    s.event('Graph uploaded to Graphistry',f"{result['nodes']} nodes, {result['edges']} edges ({body.scope}) to {result['server']}",source_id)
    return result

@app.get('/api/outputs/knowledge')
def output_knowledge(source_id:str,format:str='',tier:str='strong',include_general:bool=True):
    from . import knowledge
    snap,ont=current_ontology(source_id); k=knowledge.latest(source_id,snap['id'])
    if format in ('md','json','skos'):
        if not k or k.get('status') not in ('complete','partial'): raise HTTPException(400,'Generate the domain guide first')
        title=(s.get('source',source_id) or {}).get('name','Domain')
        if format=='md': return Response(knowledge.markdown(k,title),media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':file_name(source_id,'domain-guide.md')})
        if format=='skos': return Response(knowledge.skos(k,semantic.iri,semantic.lit,semantic.PREFIXES),media_type='text/turtle; charset=utf-8',headers={'Content-Disposition':file_name(source_id,'glossary-skos.ttl')})
        return Response(json.dumps(k,indent=2),media_type='application/json',headers={'Content-Disposition':file_name(source_id,'domain-guide.json')})
    try: plan=knowledge.plan(ont,snap['id'],'cheap' if tier=='cheap' else 'strong',include_general)
    except ValueError as e: plan={'error':str(e)}
    return {'knowledge':k,'plan':plan,'ontology':ont['id'],'stale':bool(k and k.get('ontology_id')!=ont['id'])}

@app.post('/api/outputs/knowledge')
def generate_knowledge(source_id:str,body:KnowledgeInput):
    from . import knowledge
    if not body.share_confirmed: raise HTTPException(400,'Confirm sending the ontology (names, definitions, rules; no source code) to the model')
    snap,ont=current_ontology(source_id); k=knowledge.latest(source_id,snap['id'])
    if k and k.get('status') in ('queued','running'): raise HTTPException(409,'The domain guide is already being written')
    c=ai.config()
    if not c['configured']: raise HTTPException(400,c['error'])
    return knowledge.start(source_id,snap,ont,'cheap' if body.tier=='cheap' else 'strong',body.include_general)

@app.post('/api/engines/run')
def engine_run(source_id:str,body:EngineInput):
    snap=snapshot_for(source_id)
    if not snap: raise HTTPException(400,'Analyse a source first')
    ont=ontology_for(source_id,snap['id'])
    if not ont: raise HTTPException(400,'Ontology generation is not ready')
    engine=LightweightOntologyEngine() if body.engine=='lightweight' else OpenOntologiesEngine()
    g=complete_graph(snap['id']); ont=logic.without_disputed(ont,source_id)
    if body.operation=='demo' and body.engine=='lightweight':
        demo=copy.deepcopy(ont); demo['concepts'] += [dict(id='Service',name='Service',properties=[]),dict(id='APIService',name='APIService',parent='Service',properties=[{'name':'route','datatype':'string','required':True}])]
        dg={'snapshot':snap['id'],'nodes':[{'id':'demo-api','name':'Illustrative APIService (not extracted)','type':'APIService','properties':{}}],'edges':[]}
        before=engine.run('infer',demo,dg); demo['concepts'][-1]['parent']=None; after=engine.run('infer',demo,dg)
        result={**before,'operation':'demo','demonstration':True,'explanation':'An illustrative fact is checked in isolation. Subclass inference derives Service; missing route is a violation. Removing the subclass definition removes the derived fact. Source graph is unchanged.','after_change':after,'added_fact':dg['nodes'][0]}
    else: result=engine.run(body.operation,ont,g,entity_id=body.entity_id,previous=s.get('ontology',body.previous_version) if body.previous_version else None)
    result.update(id=s.uid('run_'),created_at=s.now()); s.put('engine_run',result,source_id,snap['id']); s.event('Engine '+result['status'],result['engine']+' · '+result['operation'],source_id,snap['id']); return result

if (s.ROOT/'web'/'dist').is_dir():
    app.mount('/assets',StaticFiles(directory=s.ROOT/'web'/'dist'/'assets'),name='assets')
    @app.get('/{path:path}')
    def frontend(path:str):
        # Unknown API routes must fail as API errors, never fall back to the web page.
        if path.startswith('api/'): raise HTTPException(404,'Unknown API endpoint: /'+path)
        dist=(s.ROOT/'web'/'dist').resolve(); file=(dist/path).resolve()
        if path and file.is_file() and file.is_relative_to(dist): return FileResponse(file)  # favicon and other public files
        return FileResponse(dist/'index.html')
