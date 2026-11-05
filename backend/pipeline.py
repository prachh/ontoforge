import os, json, hashlib, shutil, threading, fnmatch, re, time, posixpath
from collections import defaultdict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pathspec
from . import store as s
from .acquisition import allowed_local, github_snapshot, MAX_FILE
from .parsers import parse_file, stable, FORMATS, PARSER_VERSION
from .models import Concept, Property
from .engine import LightweightOntologyEngine
from . import logic

POOL=ThreadPoolExecutor(max_workers=int(os.getenv('ONTO_CONCURRENCY','2')))
ACTIVE=set(); LOCK=threading.Lock()
DEFAULT_EXCLUSIONS=['.git/**','**/.git/**','node_modules/**','**/node_modules/**','.venv/**','**/__pycache__/**','dist/**','build/**','vendor/**','*.min.js','*.lock','.env*','**/.env*','*.pem','*.key','**/credentials*','**/secrets*']
TECHNICAL={'File':'A source file captured in an immutable analysis snapshot.','Class':'A class declaration extracted from source.','Function':'A callable declaration; runtime behaviour is not implied.','API':'An HTTP operation declared in source or OpenAPI.','Model':'A structured contract or type declaration.','Table':'A database table declared in SQL DDL.','Column':'A declared database column.','Test':'A test function declaration.','Documentation':'A section of supporting documentation.','Component':'A UI component (for example a Vue single-file component).','Metadata':'A declarative application record such as a report, dashboard chart, notification or workspace.','Instance':'A concrete data record (test record or fixture) of a declared model.','Dependency':'A library or build plugin declared in a Maven or Gradle build file.','Configuration':'A settings file (for example Spring application properties); secret-looking values are masked.'}

def submit(job_id):
    with LOCK:
        if job_id in ACTIVE: return
        ACTIVE.add(job_id)
    POOL.submit(run,job_id)

def checkpoint(job,stage=None,**updates):
    # Status read and checkpoint write share one transaction so pause/cancel cannot
    # be overwritten by a worker racing the control endpoint.
    with s.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT data FROM records WHERE kind='job' AND id=?",(job['id'],)).fetchone()
        current=json.loads(row[0])
        if current['status'] in ['paused','cancelled']: raise InterruptedError(current['status'])
        entered=bool(stage) and stage!=job.get('stage')
        if stage: job['stage']=stage
        if entered: job['progress']={'started_at':s.now()}
        job.update(updates); job['updated_at']=s.now()
        c.execute("UPDATE records SET data=? WHERE kind='job' AND id=?",(json.dumps(job),job['id']))
    if entered: s.log(job['id'],f'Stage started: {stage}',stage=stage)

def progress(job,stage,done,total=None,unit='',activity=None,**updates):
    """Checkpoint with a progress record; `base` is where this worker session started so the UI can estimate rate and ETA."""
    current=job.get('progress') or {}
    base=current.get('base') if current.get('unit')==unit and current.get('base') else {'done':done,'time':s.now()}
    checkpoint(job,stage,progress={**current,'done':done,'total':total,'unit':unit,'activity':activity or current.get('activity'),'base':base},**updates)

def log(job,message,level='info',**data): s.log(job['id'],message,level,job.get('stage',''),**data)

def inventory(source,snapshot,job):
    dest=s.DATA/'snapshots'/snapshot['id']; dest.mkdir(parents=True,exist_ok=True)
    if source['kind']=='github':
        # A failed transfer/extraction must not leave older archive entries mixed
        # into a later attempt. This is only our generated acquisition directory.
        if (dest/'download').exists(): shutil.rmtree(dest/'download')
        pinned={**source, 'reference': snapshot.get('commit') or source.get('reference','HEAD')}
        checkpoint(job,'Acquisition'); log(job,f"Downloading GitHub archive {source['location']} @ {pinned['reference']}")
        root,sha,blocked=github_snapshot(pinned,dest/'download')
        snapshot.update(commit=sha,acquisition_blocked=blocked)
        log(job,f'Archive extracted at commit {sha[:12]}'+(f'; {len(blocked)} entries blocked by safety limits' if blocked else ''),'success')
    elif source['kind']=='sample': root=s.ROOT/'sample'/'meridian'
    elif source['kind']=='upload': root=s.DATA/'uploads'/source['id']
    else: root=allowed_local(source['location'])
    snapshot['root_identity']=str(root) if source['kind']!='github' else source['location']
    checkpoint(job,'Inventory'); log(job,f"Scanning files under {snapshot['root_identity']}"); seen=0
    snapshot['status']='inventory'; s.put('snapshot',snapshot,source['id'])
    ignore=pathspec.PathSpec.from_lines('gitwildmatch',(root/'.gitignore').read_text().splitlines() if (root/'.gitignore').is_file() and not (root/'.gitignore').is_symlink() and (root/'.gitignore').stat().st_size<=MAX_FILE else [])
    exclusions=DEFAULT_EXCLUSIONS+source.get('exclusions',[])
    for base,dirs,files in os.walk(root,followlinks=False):
        # Symlink directories are inventoried as exclusions, never traversed.
        for d in list(dirs):
            if (Path(base)/d).is_symlink(): files.append(d); dirs.remove(d)
        for filename in files:
            p=Path(base)/filename; rel=p.relative_to(root).as_posix()
            seen+=1
            if seen%25==0 or seen==1: progress(job,'Inventory',seen,None,'files',rel)
            if seen%1000==0: log(job,f'Inventoried {seen:,} files')
            with s.connect() as c:
                if c.execute('SELECT 1 FROM files WHERE snapshot=? AND path=?',(snapshot['id'],rel)).fetchone(): continue
            status='pending'; reason=''; digest=''; size=0; fmt=FORMATS.get(p.suffix.lower(),'Unsupported')
            try:
                # GitHub/upload roots are acquired inside DATA; only guard sources rooted outside it.
                if p.resolve().is_relative_to(s.DATA) and not root.resolve().is_relative_to(s.DATA): status='excluded'; reason='Application datastore is excluded to prevent recursive self-analysis'
                elif p.is_symlink(): status='excluded'; reason='Symlink is not followed'
                elif ignore.match_file(rel) or any(fnmatch.fnmatch(rel,pat) for pat in exclusions): status='excluded'; reason='Configured exclusion or .gitignore'
                elif p.stat().st_size>MAX_FILE: status='failed'; reason='File exceeds ONTO_MAX_FILE_BYTES'
                else:
                    before=p.stat(); out=dest/'files'/rel; out.parent.mkdir(parents=True,exist_ok=True)
                    h=hashlib.sha256()
                    with p.open('rb') as inp,out.open('wb') as target:
                        while block:=inp.read(65536):
                            size+=len(block)
                            if size>MAX_FILE: raise ValueError('File grew beyond byte budget')
                            h.update(block); target.write(block)
                    after=p.stat()
                    if before.st_mtime_ns!=after.st_mtime_ns or before.st_size!=after.st_size: raise ValueError('File changed during acquisition; re-analyse a stable source')
                    digest=h.hexdigest()
                    with out.open('rb') as check: is_lfs=check.read(100).startswith(b'version https://git-lfs.github.com/spec/v1')
                    if is_lfs: status='excluded'; reason='Git LFS pointer: content was not retrieved; separate acquisition is unsupported'
                    elif fmt=='Unsupported': status='unsupported'; reason='No supported parser'
            except (OSError,ValueError) as e: status='failed'; reason=str(e)
            record={'path':rel,'hash':digest,'bytes':size,'format':fmt,'reason':reason,'status':status}
            with s.connect() as c: c.execute('INSERT INTO files VALUES(?,?,?,?,?)',(snapshot['id'],rel,digest,status,json.dumps(record)))
    # Blocked archive entries are part of coverage, never hidden.
    for blocked in snapshot.get('acquisition_blocked',[]):
        r={'path':blocked['path'],'hash':'','bytes':0,'format':'Unknown','reason':blocked['reason'],'status':'failed'}
        with s.connect() as c: c.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?)',(snapshot['id'],r['path'],'','failed',json.dumps(r)))
    snapshot['status']='acquired'; s.put('snapshot',snapshot,source['id'])
    c=s.counts(snapshot['id'])
    log(job,f"Inventory complete: {c['discovered']:,} files · {c['pending']:,} to parse · {c['excluded']:,} excluded · {c['unsupported']:,} unsupported · {c['failed']:,} failed",'success',counts=c)

UNSUPPORTED_REASONS={'XML':'Only Maven pom.xml build files are modelled among XML files','Gradle':'Only Gradle build scripts are modelled among Kotlin scripts'}

def parse(snapshot,job,source):
    sid=snapshot['id']
    with s.connect() as c: done,pending=[c.execute(f"SELECT COUNT(*) FROM files WHERE snapshot=? AND status {q}",(sid,)).fetchone()[0] for q in ("IN ('parsed','failed')","='pending'")]
    total=done+pending; failures=0; hits=job.get('cache_hits',0)
    checkpoint(job,'Parsing')
    log(job,f'Parsing {pending:,} files'+(f' (resuming; {done:,} already done)' if done else ''))
    while True:
        with s.connect() as c: row=c.execute("SELECT path,hash,data FROM files WHERE snapshot=? AND status='pending' LIMIT 1",(sid,)).fetchone()
        if not row: break
        record=json.loads(row['data']); path=row['path']
        progress(job,'Parsing',done,total,'files',path); done+=1
        if done%250==0: log(job,f'Parsed {done:,} of {total:,} files',current=path)
        try:
            fmt=FORMATS.get(Path(path).suffix.lower(),'Unsupported')+':'+PARSER_VERSION
            # Cache is keyed by both content and path because stable IDs include the path.
            key=stable(row['hash'],path)
            with s.connect() as c: cached=c.execute('SELECT data FROM cache WHERE hash=? AND format=?',(key,fmt)).fetchone()
            if cached: nodes,edges,refs,status=json.loads(cached[0]); job['cache_hits']=job.get('cache_hits',0)+1
            else:
                text=(s.DATA/'snapshots'/sid/'files'/path).read_text(encoding='utf-8')
                if '\x00' in text: raise ValueError('Binary content is not parsed')
                nodes,edges,refs,status=parse_file(path,text)
                with s.connect() as c: c.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)',(key,fmt,json.dumps([nodes,edges,refs,status])))
            url=(source['location'].removesuffix('.git')+'/blob/'+snapshot['commit']+'/'+('/'.join([source.get('subdirectory','').strip('/'),path]).strip('/'))) if snapshot.get('commit') else None
            for n in nodes:
                n.update(snapshot_id=sid,source_id=source['id'],commit=snapshot.get('commit'),content_hash=row['hash'],github_url=(url+'#L'+str(n['line'])) if url else None)
            for e in edges:
                e.update(id=stable(e['source'],e['target'],e['type']),source_id=source['id'],snapshot_id=sid,content_hash=row['hash'],commit=snapshot.get('commit'),excerpt=next((n['excerpt'] for n in nodes if n['id']==e['source']),''),github_url=url)
            for ref in refs: ref['path']=path; ref['content_hash']=row['hash']
            record['status']=status; record['references']=refs; record['reason']=UNSUPPORTED_REASONS.get(record.get('format'),'Tooling or configuration file: only OpenAPI/Swagger specs, Spring application config and Frappe DocTypes, metadata and records are modelled') if status=='unsupported' else ''
            with s.connect() as c:
                for n in nodes: c.execute('INSERT OR REPLACE INTO entities VALUES(?,?,?,?,?,?)',(sid,n['id'],n['type'],n['name'],path,json.dumps(n)))
                for e in edges: c.execute('INSERT OR REPLACE INTO edges VALUES(?,?,?,?,?,?)',(sid,e['id'],e['source'],e['target'],e['type'],json.dumps(e)))
                c.execute('UPDATE files SET status=?,data=? WHERE snapshot=? AND path=?',(status,json.dumps(record),sid,path))
        except Exception as e:
            record.update(status='failed',reason=f'{type(e).__name__}: {str(e)[:250]}')
            with s.connect() as c: c.execute("UPDATE files SET status='failed',data=? WHERE snapshot=? AND path=?",(json.dumps(record),sid,path))
            failures+=1
            if failures<=50: log(job,f"Could not parse {path}: {record['reason']}",'warning',path=path)
            elif failures==51: log(job,'Further parse failures are listed under Files → failed','warning')
    c=s.counts(sid)
    log(job,f"Parsing complete: {c['parsed']:,} parsed · {c['failed']:,} failed · {job.get('cache_hits',0)-hits:,} reused from cache",'success',counts=c)

# Entity types each reference kind may point at; mirrors the technical relationship definitions.
REF_TARGETS={'calls':{'Function','Class'},'inherits':{'Class'},'uses':{'Model','Metadata'},'instance_of':{'Model'},'references':{'Table'},'imports':{'File'},'links':{'Model'},'contains':{'Model'},'controller':{'Class'}}
REF_SOURCES={'calls':{'Function','Test','Component'}}

def module_file(module,paths):
    base=module.replace('.','/')
    return next((p for p in (base+'.py',base+'/__init__.py') if p in paths),None)

JS_EXTENSIONS=('','.vue','.ts','.tsx','.js','.jsx','/index.ts','/index.js')

def js_module_file(spec,path,paths):
    """Relative imports and the Vite '@/' alias (the nearest enclosing src folder); packages stay unresolved."""
    if spec.startswith('.'): bases=[posixpath.normpath(posixpath.join(posixpath.dirname(path),spec))]
    elif spec.startswith('@/'):
        parts=path.split('/'); bases=['/'.join(parts[:i+1]+[spec[2:]]) for i in reversed(range(len(parts)-1)) if parts[i]=='src']
    else: return None
    return next((b+e for b in bases for e in JS_EXTENSIONS if b+e in paths),None)

def java_module_file(name,static,paths):
    """An imported class's file: the unique path ending in its package directories (any source root such as src/main/java).
    Static imports name a member, so their owning class is the import without its last segment."""
    if static: name=name.rpartition('.')[0]
    suffix='/'+name.replace('.','/')+'.java'
    found=[p for p in paths if ('/'+p).endswith(suffix)]
    return found[0] if len(found)==1 else None

def language(path): return 'python' if path.endswith('.py') else 'js' if path.rsplit('.',1)[-1] in ('js','jsx','ts','tsx','vue') else path.rsplit('.',1)[-1]

def resolve(snapshot,job):
    """Static, conservative reference resolution. Each edge records the rule that matched; ambiguous names stay unresolved."""
    checkpoint(job,'Graph construction'); sid=snapshot['id']; unresolved=0
    log(job,'Resolving calls, imports, links and DocType references across files')
    with s.connect() as c:
        c.execute("DELETE FROM edges WHERE snapshot=? AND type!='defines' AND type!='implements'",(sid,))
        types={}; paths={}; files={}; in_file=defaultdict(lambda:defaultdict(list)); global_names=defaultdict(list)
        for id,kind,name,path in c.execute('SELECT id,type,name,path FROM entities WHERE snapshot=?',(sid,)):
            types[id]=kind; paths[id]=path
            if kind=='File': files[path]=id; continue
            in_file[path][name].append(id); global_names[name].append(id)
        def unique(ids,ref_type):
            allowed=REF_TARGETS.get(ref_type)
            ids=[i for i in ids if allowed is None or types[i] in allowed]
            return ids[0] if len(ids)==1 else None
        for (data,) in c.execute('SELECT data FROM files WHERE snapshot=?',(sid,)).fetchall():
            f=json.loads(data); refs=f.get('references',[]); path=f['path']
            imported={}  # local name -> ('module', file) | ('symbol', file, name)
            for r in refs:
                if r['type']!='imports' or r.get('js') or r.get('java'): continue
                if 'symbols' in r:
                    source_file=module_file(r['name'],files)
                    for name,alias in r['symbols']:
                        sub=module_file(r['name']+'.'+name,files)
                        if sub: imported[alias or name]=('module',sub)
                        elif source_file: imported[alias or name]=('symbol',source_file,name)
                elif module_file(r['name'],files):
                    imported[r.get('alias') or r['name']]=('module',module_file(r['name'],files))
            for r in refs:
                if r['source'] not in types or types[r['source']] not in REF_SOURCES.get(r['type'],{types[r['source']]}): unresolved+=1; continue
                name=r['name']; head,_,attr=name.rpartition('.'); target=method=None
                if r['type']=='imports' and r.get('js'):
                    target_file=js_module_file(name,path,files)
                    if target_file and target_file!=path: target,method=files[target_file],'js-import-path'
                elif r['type']=='imports' and r.get('java'):
                    target_file=java_module_file(name,r.get('static'),files)
                    if target_file and target_file!=path: target,method=files[target_file],'java-import-path'
                elif r.get('frappe_method'):
                    # Frappe UI resources call whitelisted Python functions by dotted module path.
                    target_file=module_file(head,files)
                    target,method=(unique(in_file[target_file].get(attr,[]),r['type']) if target_file else None),'frappe-whitelisted-method'
                elif r['type']=='imports':
                    target_file=module_file(name,files)
                    if target_file and target_file!=path: target,method=files[target_file],'python-module-path'
                elif r['type'] in ('links','contains'):
                    target,method=unique(global_names.get(name,[]),r['type']),r.get('rule','frappe-doctype-link')
                elif r['type']=='controller':
                    folder=path.rsplit('/',1)[0]+'/'
                    target,method=unique([i for i in global_names.get(name,[]) if paths[i].startswith(folder)],r['type']),r.get('rule','frappe-controller')
                elif r.get('rule')=='spring-data-repository':
                    target,method=unique([i for i in global_names.get(name,[]) if language(paths[i])==language(path)],r['type']),r['rule']
                elif r.get('metadata'):
                    target,method=unique(global_names.get(name,[]),r['type']),'frappe-metadata-name'
                elif r.get('doctype'):
                    # Frappe convention: a DocType's controller class is its name without spaces.
                    target,method=unique(global_names.get(name if r['type'] in ('uses','instance_of') else name.replace(' ',''),[]),r['type']),'frappe-doctype-name'
                elif target:=unique(in_file[path].get(name,[]),r['type']): method='same-file-unique-name'
                elif head in ('self','this') and (target:=unique(in_file[path].get(attr,[]),r['type'])): method='same-file-self-method'
                elif not head and name in imported and imported[name][0]=='symbol':
                    target,method=unique(in_file[imported[name][1]].get(imported[name][2],[]),r['type']),'python-import'
                elif head in imported and imported[head][0]=='module':
                    target,method=unique(in_file[imported[head][1]].get(attr,[]),r['type']),'python-import'
                elif not head:
                    # Bare names never cross languages: a Vue inject() is not a Python inject().
                    target,method=unique([i for i in global_names.get(name,[]) if language(paths[i])==language(path)],r['type']),'unique-global-name'
                if not target or target==r['source']: unresolved+=1; continue
                e={**r,'target':target,'status':'resolved','method':method,'snapshot_id':sid,'source_id':snapshot['source_id'],'commit':snapshot.get('commit'),'excerpt':r['name'],'github_url':None}
                e.pop('symbols',None)
                source_row=c.execute('SELECT data FROM entities WHERE snapshot=? AND id=?',(sid,r['source'])).fetchone()
                source_node=json.loads(source_row[0]) if source_row else {}
                e.update(github_url=source_node.get('github_url'),excerpt=source_node.get('excerpt',r['name']))
                e['id']=stable(r['source'],e['target'],r['type'],r['line'])
                c.execute('INSERT OR REPLACE INTO edges VALUES(?,?,?,?,?,?)',(sid,e['id'],e['source'],e['target'],e['type'],json.dumps(e)))
    snapshot['unresolved_references']=unresolved
    with s.connect() as c: entities,edges=[c.execute(f'SELECT COUNT(*) FROM {t} WHERE snapshot=?',(sid,)).fetchone()[0] for t in ('entities','edges')]
    log(job,f'Knowledge graph built: {entities:,} entities · {edges:,} connections · {unresolved:,} references left unresolved (ambiguous or external)','success')

def technical_relationships():
    ids=list(TECHNICAL)
    return [{'id':r,'source_types':src,'target_types':dst} for r,src,dst in [('defines',['File','Class','Function','Component'],ids),('implements',['API'],['Function']),('calls',['Function','Test','Component'],['Function','Class']),('inherits',['Class'],['Class']),('uses',['API','Function','Component','Metadata'],['Model','Metadata']),('instance_of',['Instance'],['Model']),('references',['Table'],['Table']),('imports',ids,['File']),('links',['Model'],['Model']),('contains',['Model'],['Model']),('controller',['Model'],['Class'])]]

def initial_ontology(snapshot):
    previous=[o for o in s.records('ontology',snapshot.get('source_id')) if o.get('snapshot_id')!=snapshot['id']] if snapshot.get('source_id') else []
    if previous:
        import copy
        obj=copy.deepcopy(previous[0]); obj.update(id=s.uid('ont_'),status='draft',created_at=s.now(),snapshot_id=snapshot['id'],based_on=previous[0]['id']); obj.pop('approved_at',None)
        # Newer parsers add technical relationship kinds; carry them into older conceptual models.
        known={r['id']:r for r in obj['relationships']}
        for r in technical_relationships():
            if r['id'] not in known: obj['relationships'].append(r)
            else: known[r['id']].update(source_types=sorted({*known[r['id']]['source_types'],*r['source_types']}),target_types=sorted({*known[r['id']]['target_types'],*r['target_types']}))
        have={c['id'] for c in obj['concepts']}
        obj['concepts']+=[Concept(id=k,name=k,description=v).model_dump() for k,v in TECHNICAL.items() if k not in have]
        return obj
    return {'id':s.uid('ont_'),'status':'draft','created_at':s.now(),'snapshot_id':snapshot['id'],'concepts':[Concept(id=k,name=k,description=v).model_dump() for k,v in TECHNICAL.items()], 'relationships':technical_relationships(), 'rules':[], 'based_on':None}

def proposals(snapshot,job,source):
    checkpoint(job,'Ontology proposals')
    if job['mode']=='parsing': log(job,'Parsing mode: no AI proposals; domain concepts come from Frappe DocTypes'); return
    def persist(items,evidence,method):
        valid={n['id'] for n in evidence}
        for item in items:
            item['evidence']=[x for x in item.get('evidence',[]) if x in valid]
            candidate=Concept(id='candidate',name=item['name'],description=item['description'],layer='domain',properties=item.get('properties',[]),aliases=item.get('aliases',[]),evidence=item['evidence'])
            if not item['evidence']: continue
            # Deterministic proposal identity makes a repeated batch idempotent.
            proposal={'id':'prop_'+stable(snapshot['id'],item['name'],','.join(sorted(item['evidence']))),'name':item['name'],'description':item['description'],'layer':'domain','evidence':item['evidence'],'conflicts':item.get('conflicts',[]),'properties':[x.model_dump() for x in candidate.properties],'aliases':candidate.aliases,'candidate_rules':item.get('candidate_rules',[]),'relationship_proposals':item.get('relationship_proposals',[]),'ambiguities':item.get('ambiguities',[]),'method':method,'status':'pending','created_at':s.now(),'snapshot_id':snapshot['id'],'source_id':source['id']}
            existing=s.get('proposal',proposal['id'])
            if not existing:s.put('proposal',proposal,source['id'],snapshot['id'])
    if job['mode']=='sample':
        if source['kind']!='sample': raise ValueError('Fixture proposals are only available for the labelled sample repository')
        with s.connect() as c: evidence=[json.loads(r[0]) for r in c.execute("SELECT data FROM entities WHERE snapshot=? AND type IN ('Documentation','Class','Table','Model')",(snapshot['id'],))]
        specs=[('Refund','A request to return a captured payment. The supported refund window is ambiguous: code says 14 days; policy says 30.',['RefundService','RefundRequest','Refund policy']),('Payment','A payment contract and persisted payment record.',['Payment','payments']),('Customer','A customer identity referenced by a payment.',['Customer','customers'])]
        items=[{'name':name,'description':desc,'evidence':[n['id'] for n in evidence if n['name'] in names], 'conflicts':['Implementation allows 14 days; documentation specifies 30 days. Human policy decision required.'] if name=='Refund' else []} for name,desc,names in specs]
        for item in items:
            if item['name']=='Payment':
                item['properties']=[{'name':'amount','datatype':'number','required':False},{'name':'currency','datatype':'string','required':False}]; item['relationship_proposals']=['Payment references Customer; evidence is a database foreign key, not an observed payment instance.']
            elif item['name']=='Refund':
                item['properties']=[{'name':'payment_id','datatype':'string','required':False},{'name':'reason','datatype':'string','required':False}]; item['aliases']=['Refund request']; item['candidate_rules']=['Observed implementation: days_since_payment <= 14. Documentation: refund allowed within 30 days. Do not approve either as business policy without resolving the conflict.']; item['ambiguities']=['The intended refund window is unresolved.']
            else:item['properties']=[{'name':'id','datatype':'string','required':False},{'name':'email','datatype':'string','required':False}]
        persist(items,evidence,'sample fixture — not AI generated')
    else:
        from . import generative
        # The candidate list is fixed per job (stored once) so resuming never skips or repeats entities.
        stored=s.get('discovery',job['id'])
        if not stored or stored.get('version')!=DISCOVERY_VERSION:
            ont=s.records('ontology',source['id'],snapshot['id'])[0]
            stored={'id':job['id'],'version':DISCOVERY_VERSION,'ids':[e['id'] for e in generative.discovery_candidates(snapshot['id'],ont)]}
            s.put('discovery',stored,source['id'],snapshot['id']); checkpoint(job,ai_offset=0)
            with s.connect() as c: considered=c.execute("SELECT COUNT(*) FROM entities WHERE snapshot=? AND type IN ('Documentation','Class','Table','Model')",(snapshot['id'],)).fetchone()[0]
            log(job,f"Concept discovery: {len(stored['ids']):,} of {considered:,} classes, models, tables and docs need a model; the rest are explained by code or are tests, plumbing or tooling docs")
        ids=stored['ids']; total=len(ids)
        while True:
            offset=job.get('ai_offset',0)
            if offset>=total: break
            with s.connect() as c: evidence=[json.loads(r[0]) for r in (c.execute('SELECT data FROM entities WHERE snapshot=? AND id=?',(snapshot['id'],id)).fetchone() for id in ids[offset:offset+DISCOVERY_BATCH]) if r]
            batches=-(-total//DISCOVERY_BATCH); batch=offset//DISCOVERY_BATCH+1
            progress(job,'AI generation',offset,total,'entities',f'Batch {batch:,} of {batches:,}: '+', '.join(n['name'] for n in evidence)[:300],ai_total_entities=total)
            started=time.time()
            items=live_proposals(evidence,job,f'Batch {batch:,}/{batches:,}')
            persist(items,evidence,'AI interpretation'); accept_batch(snapshot,source)
            names=', '.join(i['name'] for i in items if i.get('evidence'))
            log(job,f'Batch {batch:,}/{batches:,} done in {time.time()-started:.0f}s: '+(f'{len(items)} proposal(s): {names}' if items else 'no domain concepts proposed'),'success' if items else 'info',seconds=round(time.time()-started,1))
            checkpoint(job,ai_offset=min(total,offset+DISCOVERY_BATCH))

DISCOVERY_VERSION=2
DISCOVERY_BATCH=12
DISCOVERY_EXCERPT=700

AI_ATTEMPTS=3

def live_proposals(evidence,job,label='Batch'):
    from .ai import generate_proposals, public_config
    model=public_config()
    checkpoint(job,'AI generation',ai_provider=model['provider'],ai_model=model.get('models',{}).get('cheap') or model['model'])
    from . import generative
    batch=[{'id':n['id'],'name':n['name'],'excerpt':n['excerpt'][:DISCOVERY_EXCERPT]} for n in evidence]
    tier_model=model.get('models',{}).get('cheap') or model['model']
    cache=generative.cache_key('discover',generative.PROMPT_VERSION,tier_model,json.dumps(batch,sort_keys=True))
    if (hit:=generative.cache_get(cache)) is not None:
        log(job,f'{label}: reused from the result cache (no model call)','debug'); return hit
    log(job,f"{label}: asking {tier_model} about "+', '.join(n['name'] for n in evidence)[:400],'debug')
    # A slow or briefly unavailable local model should not end a multi-hour run; retry, then fail resumably.
    for attempt in range(1,AI_ATTEMPTS+1):
        try:
            result=generate_proposals(batch,{'job':job['id'],'source':job.get('source_id',''),'operation':'proposals'},'cheap')
            generative.cache_put(cache,'discover',tier_model,result); return result
        except ValueError as e:
            transient='timed out' in str(e) or 'connection failed' in str(e)
            if not transient or attempt==AI_ATTEMPTS: raise
            wait=15*attempt; log(job,f'{label}: {e} Retrying in {wait}s (attempt {attempt+1} of {AI_ATTEMPTS})','warning')
            for _ in range(wait): time.sleep(1); checkpoint(job)


def changes(snapshot):
    previous=[x for x in s.records('snapshot',snapshot['source_id']) if x['id']!=snapshot['id'] and x.get('status') in ['complete','partial']]
    if not previous: snapshot['changes']={'added':s.counts(snapshot['id'])['discovered'],'modified':0,'deleted':0,'unchanged':0}; return
    with s.connect() as c:
        before=dict(c.execute('SELECT path,hash FROM files WHERE snapshot=?',(previous[0]['id'],)))
        after=dict(c.execute('SELECT path,hash FROM files WHERE snapshot=?',(snapshot['id'],)))
    changed={p for p,h in before.items() if p not in after or after[p]!=h}
    snapshot['changes']={'added':len(after.keys()-before.keys()),'deleted':len(before.keys()-after.keys()),'modified':sum(1 for p in before.keys()&after.keys() if before[p]!=after[p]),'unchanged':sum(1 for p in before.keys()&after.keys() if before[p]==after[p])}
    for m in s.records('mapping',snapshot['source_id']):
        if m['path'] in changed:
            m['stale']=True; m['stale_since']=snapshot['id']; s.put('mapping',m,snapshot['source_id'],m['snapshot_id'])

# ---- Automatic ontology construction: proposals and DocTypes are accepted without a manual review step. ----
def concept_key(name): return re.sub(r'[^a-z0-9]','',str(name).lower())

def domain_index(ont):
    index={}
    for c in ont['concepts']:
        if c['layer']!='domain': continue
        for n in [c['name'],*c.get('aliases',[])]: index.setdefault(concept_key(n),c)
    return index

def free_name(ont,name,concept=None):
    """Concept names must be unique; a domain name may not reuse a technical concept name such as File."""
    taken={concept_key(c['name']) for c in ont['concepts'] if c is not concept}
    return name if concept_key(name) not in taken else f'{name} (domain)'

def entity_record(snapshot,id):
    with s.connect() as c: row=c.execute('SELECT data FROM entities WHERE snapshot=? AND id=?',(snapshot['id'],id)).fetchone()
    return json.loads(row[0]) if row else None

def put_mapping(snapshot,ont,concept_id,entity,method):
    m={'id':'map_'+stable(snapshot['id'],entity['id'],concept_id),'entity_id':entity['id'],'concept_id':concept_id,'snapshot_id':snapshot['id'],'source_id':snapshot['source_id'],'path':entity['path'],'content_hash':entity.get('content_hash',''),'status':'auto-approved','method':method,'stale':False,'ontology_version':ont['id']}
    s.put('mapping',m,snapshot['source_id'],snapshot['id'])

def add_evidence(snapshot,ont,concept,ids,method):
    for id in ids:
        entity=entity_record(snapshot,id)
        if not entity: continue
        if id not in concept['evidence']: concept['evidence'].append(id)
        put_mapping(snapshot,ont,concept['id'],entity,method)

def accept_proposals(snapshot,ont,quiet=False):
    pending=[x for x in reversed(s.records('proposal',snapshot['source_id'],snapshot['id'])) if x['status']=='pending']
    for proposal in pending:
        concept=domain_index(ont).get(concept_key(proposal['name']))
        if concept:
            concept['aliases']=sorted({*concept.get('aliases',[]),*proposal.get('aliases',[]),*([proposal['name']] if proposal['name']!=concept['name'] else [])})
            known={concept_key(p['name']) for p in concept['properties']}
            concept['properties']+=[p for p in proposal.get('properties',[]) if concept_key(p['name']) not in known]
            if not concept.get('description'): concept['description']=proposal['description']
        else:
            concept=Concept(id=s.uid('concept_'),name=free_name(ont,proposal['name']),description=proposal['description'],layer='domain',properties=proposal.get('properties',[]),aliases=proposal.get('aliases',[])).model_dump()
            ont['concepts'].append(concept)
        add_evidence(snapshot,ont,concept,proposal['evidence'],proposal.get('method','proposal'))
        proposal.update(status='accepted',decision='auto-approved',reviewed_at=s.now()); s.put('proposal',proposal,snapshot['source_id'],snapshot['id'])
    if pending and not quiet: s.event('Proposals auto-approved',f'{len(pending)} domain proposals accepted into the ontology.',snapshot['source_id'],snapshot['id'])

def merge_duplicate_concepts(snapshot,ont):
    """Spelling variants such as SalarySlip / Salary Slip become one concept; links and rules follow the survivor."""
    groups=defaultdict(list)
    for c in ont['concepts']:
        if c['layer']=='domain': groups[concept_key(c['name'])].append(c)
    renamed={}
    for group in groups.values():
        if len(group)<2: continue
        keep=max(group,key=lambda c:(' ' in c['name'],len(c['evidence'])))
        for other in group:
            if other is keep: continue
            keep['aliases']=sorted({*keep.get('aliases',[]),*other.get('aliases',[]),other['name']}-{keep['name']})
            keep['evidence']+=[e for e in other['evidence'] if e not in keep['evidence']]
            known={concept_key(p['name']) for p in keep['properties']}
            keep['properties']+=[p for p in other['properties'] if concept_key(p['name']) not in known]
            renamed[other['id']]=keep['id']
    if not renamed: return
    ont['concepts']=[c for c in ont['concepts'] if c['id'] not in renamed]
    for c in ont['concepts']:
        if c.get('parent') in renamed: c['parent']=renamed[c['parent']]
    for r in ont['relationships']:
        r['source_types']=sorted({renamed.get(t,t) for t in r['source_types']}); r['target_types']=sorted({renamed.get(t,t) for t in r['target_types']})
    for rule in ont.get('rules',[]): rule['concept']=renamed.get(rule['concept'],rule['concept'])
    for m in s.records('mapping',snapshot['source_id']):
        if m['concept_id'] in renamed: m['concept_id']=renamed[m['concept_id']]; s.put('mapping',m,snapshot['source_id'],m['snapshot_id'])
    s.event('Duplicate concepts merged',f'{len(renamed)} spelling variants merged into existing concepts.',snapshot['source_id'],snapshot['id'])

FIELD_TYPES={'Int':'number','Float':'number','Currency':'number','Percent':'number','Duration':'number','Rating':'number','Check':'boolean'}

def doctype_ontology(snapshot,ont):
    """Frappe DocTypes are explicit business entities: each becomes (or enriches) a concept with its real fields,
    and Link/Table fields become typed relationships. Frequently referenced DocTypes defined outside the repository
    (e.g. Employee or Company in ERPNext) become concepts too, so links to them are not lost."""
    with s.connect() as c:
        rows=[json.loads(r[0]) for r in c.execute("SELECT data FROM entities WHERE snapshot=? AND type='Model'",(snapshot['id'],))]
        controllers={e:t for e,t in c.execute("SELECT source,target FROM edges WHERE snapshot=? AND type='controller'",(snapshot['id'],))}
        # UI components, metadata records and data records that name a DocType are evidence for its concept.
        usage=defaultdict(lambda:defaultdict(list))
        for e,t,k in c.execute("SELECT e.source,e.target,n.type FROM edges e JOIN entities n ON n.snapshot=e.snapshot AND n.id=e.source WHERE e.snapshot=? AND e.type IN ('uses','instance_of') AND n.type IN ('Component','Metadata','Instance')",(snapshot['id'],)): usage[t][k].append(e)
    doctypes={e['name']:e for e in rows if e.get('properties',{}).get('doctype')}
    if not doctypes: return
    index=domain_index(ont); created=0
    for name,entity in sorted(doctypes.items()):
        props=entity['properties']
        concept=index.get(concept_key(name))
        if not concept:
            description=props['description'] or f"Frappe DocType in the {props['module'] or 'unknown'} module."
            concept=Concept(id=s.uid('concept_'),name=free_name(ont,name),description=description+(' Child table of another DocType.' if props['istable'] else ''),layer='domain').model_dump()
            ont['concepts'].append(concept); index[concept_key(name)]=concept; created+=1
        elif concept['name']!=name and concept_key(concept['name'])==concept_key(name) and free_name(ont,name,concept)==name:
            concept['aliases']=sorted({*concept.get('aliases',[]),concept['name']}-{name}); concept['name']=name
        concept['properties']=[Property(name=f['fieldname'],datatype=FIELD_TYPES.get(f['fieldtype'],'string'),required=f['reqd'],fieldtype=f['fieldtype'],reference=f['options'] if f['fieldtype'] in ('Link','Table','Table MultiSelect') and f['options'] else None,
                                        options=[o.strip() for o in f['options'].split('\n') if o.strip()] if f['fieldtype']=='Select' else []).model_dump() for f in props['fields']]
        # DocType permissions are roles, read for free: who may read, create, submit… this concept.
        if props.get('permissions'): concept['roles']=props['permissions']; concept.setdefault('provenance',{})['roles']={'origin':'doctype'}
        add_evidence(snapshot,ont,concept,[entity['id'],*([controllers[entity['id']]] if entity['id'] in controllers else [])],'frappe-doctype')
        for kind,method in (('Component','frappe-ui-usage'),('Metadata','frappe-metadata'),('Instance','frappe-record')):
            add_evidence(snapshot,ont,concept,sorted(set(usage[entity['id']][kind])),method)
    external=defaultdict(int)
    for entity in doctypes.values():
        for f in entity['properties']['fields']:
            if f['fieldtype'] in ('Link','Table','Table MultiSelect') and f['options'] and f['options'] not in doctypes: external[f['options']]+=1
    for name,count in sorted(external.items()):
        if count>=3 and concept_key(name) not in index:
            concept=Concept(id=s.uid('concept_'),name=free_name(ont,name),description=f'DocType referenced by {count} fields in this repository and defined outside it (for example in Frappe or ERPNext).',layer='domain').model_dump()
            ont['concepts'].append(concept); index[concept_key(name)]=concept; created+=1
    ont['relationships']=[r for r in ont['relationships'] if r.get('kind') not in ('link','contains')]
    seen=set()
    for name,entity in sorted(doctypes.items()):
        source=index[concept_key(name)]
        for f in entity['properties']['fields']:
            kind={'Link':'link','Table':'contains','Table MultiSelect':'contains'}.get(f['fieldtype']); target=index.get(concept_key(f['options'])) if kind and f['options'] else None
            rid=f"{name}.{f['fieldname']}"
            if target and rid not in seen:
                seen.add(rid); ont['relationships'].append({'id':rid,'source_types':[source['id']],'target_types':[target['id']],'label':f['fieldname'],'kind':kind})
    s.event('DocTypes mapped to the ontology',f'{len(doctypes)} DocTypes, {created} new concepts, {len(seen)} relationships.',snapshot['source_id'],snapshot['id'])

def mentioned_concept(text,index,exclude):
    """The first concept named in a sentence (by name or alias, singular or simple plural), with the words before it."""
    words=re.findall(r"[A-Za-z][\w'-]*|\S",text)
    for i in range(len(words)):
        for n in (3,2,1):
            key=concept_key(''.join(words[i:i+n]))
            for k in (key,key[:-1] if key.endswith('s') else None,key[:-2] if key.endswith('es') else None):
                c=index.get(k) if k else None
                if c and c['id']!=exclude: return c,' '.join(words[:i])
    return None,''

def concept_relationships(snapshot,ont):
    """Relationships between domain concepts from what is already known about them: property references
    (Pet.owner → Owner), AI relationship sentences ('Vet has many Specialty') and class inheritance among the
    concepts' own classes (Owner extends Person). DocType links are left to doctype_ontology. One relationship is
    kept per direction between two concepts: references first, then inheritance, then the first AI statement."""
    index=domain_index(ont); found={}
    def relate(rid,source,target,label,kind):
        if source['id']!=target['id'] and not any(r['source_types']==[source['id']] and r['target_types']==[target['id']] for r in found.values()):
            found.setdefault(rid,{'id':rid,'source_types':[source['id']],'target_types':[target['id']],'label':label,'kind':kind})
    for concept in [c for c in ont['concepts'] if c['layer']=='domain']:
        for p in concept['properties']:
            target=index.get(concept_key(p.get('reference') or ''))
            if target and p.get('fieldtype') not in ('Link','Table','Table MultiSelect'): relate(f"{concept['name']}.{p['name']}",concept,target,p['name'],'link')
    with s.connect() as c:
        inherits=c.execute("SELECT source,target FROM edges WHERE snapshot=? AND type='inherits'",(snapshot['id'],)).fetchall()
        names=dict(c.execute("SELECT id,name FROM entities WHERE snapshot=? AND type='Class'",(snapshot['id'],)).fetchall())
    # Only a concept's namesake class counts, so Owner's controller or repository never makes Owner a subtype.
    own={e:concept for concept in ont['concepts'] if concept['layer']=='domain' for e in concept['evidence'] if concept_key(names.get(e,''))==concept_key(concept['name'])}
    for child,parent in inherits:
        if child in own and parent in own: relate(f"{own[child]['name']} extends {own[parent]['name']}",own[child],own[parent],'extends','declared')
    for proposal in s.records('proposal',snapshot['source_id'],snapshot['id']):
        source=index.get(concept_key(proposal['name']))
        if not source or proposal['status']!='accepted': continue
        own_names={concept_key(x) for x in [source['name'],*source.get('aliases',[])]}
        for sentence in proposal.get('relationship_proposals',[]):
            # 'Pet has many Visits (one-to-many via pet_id)': it must start with the concept itself; the target is
            # the first other concept named and the words between are the label.
            words=re.sub(r'\(.*?\)','',sentence).split()
            n=next((n for n in (3,2,1) if concept_key(''.join(words[:n])) in own_names),0)
            if not n: continue
            target,verb=mentioned_concept(' '.join(words[n:]),index,source['id'])
            if target and verb: relate(f"{source['name']} {verb} {target['name']}",source,target,verb.strip(' .,;:').lower(),'declared')
    ids={r['id'] for r in ont['relationships']}
    ont['relationships']+=[r for r in found.values() if r['id'] not in ids]
    return len(found)

JPA_DATATYPES={'Int':'number','Float':'number','Currency':'number','Check':'boolean'}
def split_camel(name): return re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])',' ',name)
def number(text):
    try: v=float(str(text).rstrip('LlDdFf')); return int(v) if v==int(v) else v
    except ValueError: return None

def constraint_rules(concept,field,evidence):
    """Bean Validation annotations as ontology rules: executable where SHACL/the engine can check them, otherwise described."""
    name=field['fieldname']; out=[]
    def rule(key,op,value=None,text=''):
        out.append({'id':f"{concept['name']}.{name}.{key}",'operator':op,'concept':concept['id'],'property':name,'value':value,'text':text,'origin':'annotation',
                    'interpretation':'Declared constraint in code (Bean Validation)','evidence':evidence,'provenance':{'origin':'annotation','source':'@'+key.split('.')[0]}})
    for c in field.get('constraints',[]):
        k=c['kind']
        if k in ('NotNull','NotBlank','NotEmpty'): rule(k,'required',None,f"{name} is required"+(' and must not be blank' if k=='NotBlank' else ' and must not be empty' if k=='NotEmpty' else ''))
        elif k=='Size':
            if number(c.get('min')): rule('Size.min','min_length',number(c['min']),f"{name} has at least {number(c['min'])} characters")
            if number(c.get('max')) is not None: rule('Size.max','max_length',number(c['max']),f"{name} has at most {number(c['max'])} characters")
        elif k in ('Min','DecimalMin') and number(c.get('value')) is not None: rule(k,'minimum',number(c['value']),f"{name} is at least {number(c['value'])}")
        elif k in ('Max','DecimalMax') and number(c.get('value')) is not None: rule(k,'maximum',number(c['value']),f"{name} is at most {number(c['value'])}")
        elif k=='Digits' and number(c.get('integer')):
            i,f=int(number(c['integer'])),int(number(c.get('fraction')) or 0)
            rule(k,'pattern',r'\d{1,%d}'%i+(r'(\.\d{1,%d})?'%f if f else ''),f"{name} has up to {i} digits"+(f' and {f} decimals' if f else ''))
        elif k=='Pattern' and c.get('regexp'): rule(k,'pattern',c['regexp'],f"{name} must match {c['regexp']}")
        elif k=='Email': rule(k,'pattern',r'[^@\s]+@[^@\s]+\.[^@\s]+',f'{name} must be an email address')
        elif k in ('Past','PastOrPresent','Future','FutureOrPresent','Positive','PositiveOrZero','Negative','NegativeOrZero'):
            rule(k,'described',None,f"{name} must be {split_camel(k).lower().replace(' or ',' or ')}")
    return out

def jpa_ontology(snapshot,ont):
    """Spring/JPA entities are explicit business entities, read like Frappe DocTypes and free of LLM cost: each
    @Entity (and meaningful @MappedSuperclass) becomes a concept with typed properties, its superclass becomes the
    is-a parent, relations become link/contains relationships and Bean Validation annotations become rules.
    Purely technical bases such as BaseEntity are folded into their subclasses instead of becoming concepts."""
    sid=snapshot['id']
    with s.connect() as c:
        rows=[json.loads(r[0]) for r in c.execute("SELECT data FROM entities WHERE snapshot=? AND type IN ('Model','Class')",(sid,))]
        controllers={e:t for e,t in c.execute("SELECT source,target FROM edges WHERE snapshot=? AND type='controller'",(sid,))}
        users=defaultdict(list)
        for e,t in c.execute("SELECT source,target FROM edges WHERE snapshot=? AND type='uses'",(sid,)): users[t].append(e)
    models={e['name']:e for e in rows if e['type']=='Model' and e.get('properties',{}).get('jpa')}
    if not models: return 0
    enums={e['name']:e['properties']['enum'] for e in rows if e['type']=='Class' and e.get('properties',{}).get('enum')}
    technical=lambda m:m['properties']['jpa']=='mapped_superclass' and bool(re.match(r'(Base|Abstract)[A-Z]',m['name']) or m['name'].endswith('Entity'))
    def own_and_folded_fields(m,depth=0):
        parent=models.get(m['properties'].get('extends'))
        inherited=own_and_folded_fields(parent,depth+1) if parent and technical(parent) and depth<8 else []
        return inherited+m['properties']['fields']
    def concept_parent(m,depth=0):
        parent=models.get(m['properties'].get('extends'))
        if not parent or depth>8: return None
        return parent if not technical(parent) else concept_parent(parent,depth+1)
    index=domain_index(ont); concepts={}; created=0
    for name,m in sorted(models.items()):
        if technical(m): continue
        concept=index.get(concept_key(name))
        if not concept:
            concept=Concept(id=s.uid('concept_'),name=free_name(ont,split_camel(name)),description=m['properties'].get('doc') or f"Persistent entity {name}"+(f" (table {m['properties']['table']})" if m['properties'].get('table') else '')+'.',
                            layer='domain',aliases=[name] if split_camel(name)!=name else []).model_dump()
            concept['provenance']={'description':{'origin':'javadoc' if m['properties'].get('doc') else 'jpa'}}
            ont['concepts'].append(concept); index[concept_key(name)]=concept; created+=1
        elif m['properties'].get('doc') and (concept.get('provenance',{}).get('description',{}).get('origin') in (None,'javadoc','jpa') and not concept.get('description')):
            concept['description']=m['properties']['doc']; concept.setdefault('provenance',{})['description']={'origin':'javadoc'}
        concepts[name]=concept
    ont['relationships']=[r for r in ont['relationships'] if r.get('provenance',{}).get('origin')!='jpa']
    ont['rules']=[r for r in ont.get('rules',[]) if r.get('origin')!='annotation' or r['concept'] not in {c['id'] for c in concepts.values()}]
    for name,concept in concepts.items():
        m=models[name]; fields=own_and_folded_fields(m); evidence=[m['id'],*([controllers[m['id']]] if m['id'] in controllers else []),*users.get(m['id'],[])]
        concept['abstract']=m['properties']['jpa']=='mapped_superclass'
        concept.setdefault('provenance',{})['jpa']={'origin':'jpa','kind':m['properties']['jpa'],'table':m['properties'].get('table',''),'module':m['properties'].get('module','')}
        parent=concept_parent(m)
        if parent and parent['name'] in concepts: concept['parent']=concepts[parent['name']]['id']; concept['provenance']['parent']={'origin':'jpa','source':'extends '+parent['name']}
        props=[]
        for f in fields:
            target=concepts.get(f['options']) if f.get('relation') else None
            options=enums.get(f['java_type'],[]) if not f.get('relation') else []
            props.append(Property(name=f['fieldname'],datatype=JPA_DATATYPES.get(f['fieldtype'],'string'),required=bool(f['reqd']),fieldtype='Select' if options else f['fieldtype'],
                                  reference=target['name'] if target else (f['options'] or None) if f.get('relation') else None,options=options).model_dump())
            if target and target['id']!=concept['id']:
                kind='contains' if f.get('relation')=='OneToMany' and f.get('cascade') else 'link'
                ont['relationships'].append({'id':f"{concept['name']}.{f['fieldname']}",'source_types':[concept['id']],'target_types':[target['id']],'label':f['fieldname'],'kind':kind,'description':'','provenance':{'origin':'jpa','relation':f['relation'],**({'mapped_by':f['mapped_by']} if f.get('mapped_by') else {})}})
            ont.setdefault('rules',[]).extend(constraint_rules(concept,f,[m['id']]))
        concept['properties']=props
        add_evidence(snapshot,ont,concept,evidence,'jpa-entity')
    s.event('JPA entities mapped to the ontology',f'{len(concepts)} entities, {created} new concepts.',snapshot['source_id'],sid)
    return len(concepts)

def approve_ontology(ont):
    """Returns validation findings, or None after marking the version approved."""
    result=LightweightOntologyEngine().run('definition',ont,{'nodes':[],'edges':[]})
    if result['status']!='success': return result['findings']
    ont.update(status='approved',approved_at=s.now())
    return None

def preview_ontology(snapshot,job,source):
    """Publish DocType concepts into the draft right after graph construction, so the domain ontology is
    browsable while the (possibly hours-long) AI stage runs. Approval still happens once, at the end."""
    ont=s.records('ontology',source['id'],snapshot['id'])[0]
    if ont['status']=='approved': return
    before=sum(1 for c in ont['concepts'] if c['layer']=='domain')
    doctype_ontology(snapshot,ont); jpa=jpa_ontology(snapshot,ont); logic.derive(snapshot['id'],ont); s.put('ontology',ont,source['id'],snapshot['id'])
    after=sum(1 for c in ont['concepts'] if c['layer']=='domain')
    if after: log(job,f'Domain ontology preview published: {after:,} concepts'+(f' ({after-before:,} from DocTypes and JPA entities)' if after>before else '')+(f'; {jpa} JPA entities read without AI' if jpa else '')+('; AI concepts are added as each batch completes' if job['mode']=='live' else ''),'success')

def accept_batch(snapshot,source):
    """Fold one AI batch's proposals into the draft ontology immediately (they are auto-approved at the end anyway)."""
    ont=s.records('ontology',source['id'],snapshot['id'])[0]
    if ont['status']=='approved': return
    accept_proposals(snapshot,ont,quiet=True); merge_duplicate_concepts(snapshot,ont); concept_relationships(snapshot,ont); s.put('ontology',ont,source['id'],snapshot['id'])

def enrich(snapshot,job,source,ont):
    """Generative passes on top of the deterministic ontology, then the AI quality check (live mode only)."""
    from . import generative
    passes=[p for p in (job.get('passes') or list(generative.PASSES)) if p in generative.PASSES]
    save=lambda: s.put('ontology',ont,source['id'],snapshot['id'])
    runner=generative.Runner(snapshot,ont,job,log=lambda m,level='info',**k: log(job,m,level,**k),budget=job.get('budget_usd'),save=save)
    runner.on_progress=lambda name,i,n,batch: progress(job,'AI enrichment',i,n,'calls',f"{generative.PASSES[name][2]}: "+', '.join(c['name'] for c in batch)[:250])
    plan=runner.plan(passes); calls=sum(p['calls']-p['cached_calls'] for p in plan); cost=[p['cost'] for p in plan]
    log(job,f"AI enrichment plan: {sum(p['calls'] for p in plan)} calls ({sum(p['cached_calls'] for p in plan)} cached), about {sum(p['input_tokens'] for p in plan)+sum(p['output_tokens'] for p in plan):,} tokens"+
        (f", estimated ${sum(cost):.2f}" if all(c is not None for c in cost) else '')+(f"; budget ${job['budget_usd']:.2f}" if job.get('budget_usd') is not None else ''),plan=plan)
    for name in passes:
        # One failed pass (model error, malformed output) is logged and skipped; the concepts are already built.
        try: runner.run(name)
        except generative.BudgetExceeded as e: log(job,f'{e}; remaining enrichment skipped','warning'); break
        except ValueError as e: log(job,f"{generative.PASSES[name][2]} skipped: {e}",'warning')
        except Exception as e: log(job,f"{generative.PASSES[name][2]} failed: {type(e).__name__}: {e}",'error')
    log(job,f'AI enrichment finished: {runner.calls} model call(s), {runner.cached} reused from cache','success')
    reasoning=logic.reason(ont,snapshot['id'])
    log(job,f"Reasoner: {reasoning['axioms']['total']:,} axioms, {sum(reasoning['derived'].values()):,} derived facts, {reasoning['contradiction_count']:,} contradiction(s)"+
        (f"; classes with no possible members: {', '.join(reasoning['unsatisfiable'][:5])}" if reasoning['unsatisfiable'] else ''),'warning' if reasoning['unsatisfiable'] else 'success')
    sample=int(os.getenv('ONTO_JUDGE_SAMPLE','8'))
    if sample and generative.claims(snapshot['id'],'accepted'):
        try: generative.evaluate(snapshot,ont,job,sample,lambda m,level='info',**k: log(job,m,level,**k))
        except (ValueError,generative.BudgetExceeded) as e: log(job,f'Quality check skipped: {e}','warning')
    save()

def build_ontology(snapshot,job,source):
    checkpoint(job,'AI enrichment' if job['mode']=='live' else 'Ontology approval')
    ont=s.records('ontology',source['id'],snapshot['id'])[0]
    if ont['status']=='approved': log(job,'Ontology already approved for this snapshot'); return
    domain=lambda:sum(1 for c in ont['concepts'] if c['layer']=='domain')
    before=domain(); pending=sum(1 for x in s.records('proposal',snapshot['source_id'],snapshot['id']) if x['status']=='pending')
    if pending: log(job,f'Accepting {pending:,} AI proposals into the ontology')
    accept_proposals(snapshot,ont); merge_duplicate_concepts(snapshot,ont)
    if pending: log(job,f'Proposals accepted: {domain()-before:,} new domain concepts (spelling variants merged)')
    log(job,'Mapping Frappe DocTypes and JPA entities, fields and links to concepts')
    doctype_ontology(snapshot,ont); jpa_ontology(snapshot,ont)
    related=concept_relationships(snapshot,ont)
    if related: log(job,f'Relationships derived between domain concepts: {related:,} from property references, AI relationship statements and inheritance')
    found=logic.derive(snapshot['id'],ont)
    if found['rules'] or found['disjoint']:
        log(job,f"Rules and logic read from code: {sum(found['rules'].values()):,} rules ("+', '.join(f'{v:,} {k}' for k,v in sorted(found['rules'].items(),key=lambda x:-x[1]))+
            f"), {found['lifecycles']:,} lifecycles from workflows and submit/cancel, {found['disjoint']} disjointness group(s)")
    if job['mode']=='live': s.put('ontology',ont,source['id'],snapshot['id']); enrich(snapshot,job,source,ont); checkpoint(job,'Ontology approval')
    findings=approve_ontology(ont); s.put('ontology',ont,source['id'],snapshot['id'])
    links=sum(1 for r in ont['relationships'] if r.get('kind') in ('link','contains'))
    if findings:
        s.event('Automatic approval blocked','; '.join(f"{f['code']}: {f['message']}" for f in findings)[:500],source['id'],snapshot['id'])
        log(job,f'Automatic approval blocked by {len(findings)} validation finding(s); the draft is kept for Ontology Studio','warning',findings=findings[:20])
    else:
        s.event('Ontology version approved',f"{ont['id']} approved automatically.",source['id'],snapshot['id'])
        log(job,f'Ontology approved: {domain():,} domain concepts · {links:,} links','success')

def run(job_id):
    job=s.get('job',job_id); snapshot=s.get('snapshot',job['snapshot_id']); source=s.get('source',job['source_id'])
    try:
        resumed=bool(job.get('progress') or job.get('ai_offset'))
        checkpoint(job,status='running',progress={})
        s.log(job_id,f"{'Resumed' if resumed else 'Started'} {job['mode']} analysis of {source['name']}"+(f" at stage {job.get('stage')}" if resumed else ''),stage=job.get('stage',''))
        if snapshot['status'] not in ['acquired','complete','partial']: inventory(source,snapshot,job)
        else: log(job,'Files already acquired for this snapshot; skipping acquisition')
        parse(snapshot,job,source); resolve(snapshot,job)
        if not s.records('ontology',source['id'],snapshot['id']): s.put('ontology',initial_ontology(snapshot),source['id'],snapshot['id'])
        preview_ontology(snapshot,job,source)
        proposals(snapshot,job,source); build_ontology(snapshot,job,source); changes(snapshot)
        counts=s.counts(snapshot['id']); snapshot.update(status='partial' if counts['failed'] else 'complete',completed_at=s.now(),counts=counts)
        s.put('snapshot',snapshot,source['id'])
        source.update(latest_snapshot=snapshot['id'],latest_analysis=s.now()); s.put('source',source)
        checkpoint(job,'Complete',status='complete',counts=counts)
        s.event('Analysis completed',f'{counts["parsed"]} files parsed; {counts["failed"]} failed.',source['id'],snapshot['id'])
        log(job,f'Analysis complete: {counts["parsed"]:,} files parsed, {counts["failed"]:,} failed','success')
    except InterruptedError as e:
        s.log(job_id,f'Worker stopped at a checkpoint ({e}); progress is saved','warning',job.get('stage',''))
    except Exception as e:
        s.log(job_id,f'Analysis failed: {str(e)[:500]}','error',job.get('stage',''))
        snapshot.update(status='partial' if s.counts(snapshot['id'])['parsed'] else 'failed',error=str(e)[:500]); s.put('snapshot',snapshot,source['id'])
        job.update(status='failed',error=str(e)[:500],updated_at=s.now()); s.put('job',job,source['id'],snapshot['id'])
        s.event('Analysis failed',job['error'],source['id'],snapshot['id'])
    finally:
        with LOCK: ACTIVE.discard(job_id)
        if s.get('job',job_id)['status']=='queued': submit(job_id)
