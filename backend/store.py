import os, sqlite3, json, uuid
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv('ONTO_DATA', str(ROOT / 'data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'ontoforge.db'

def now(): return datetime.now(timezone.utc).isoformat()
def uid(prefix=''): return prefix + uuid.uuid4().hex[:16]
def connect():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c

def adopt_legacy_db():
    # Databases created before the OntoForge rename were called explorer.db: adopt one (with its WAL files).
    # Runs only at server start, never on import, so tests and tools cannot move a database a running server uses.
    legacy = DB.with_name('explorer.db')
    if DB.name == 'ontoforge.db' and not DB.exists() and legacy.exists():
        for suffix in ('', '-wal', '-shm'):
            if Path(f'{legacy}{suffix}').exists(): Path(f'{legacy}{suffix}').rename(f'{DB}{suffix}')

def init():
    adopt_legacy_db()
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS records(kind TEXT, id TEXT, source TEXT, snapshot TEXT, data TEXT NOT NULL, PRIMARY KEY(kind,id));
        CREATE INDEX IF NOT EXISTS record_scope ON records(kind,source,snapshot);
        CREATE TABLE IF NOT EXISTS files(snapshot TEXT, path TEXT, hash TEXT, status TEXT, data TEXT, PRIMARY KEY(snapshot,path));
        CREATE INDEX IF NOT EXISTS file_status ON files(snapshot,status);
        CREATE TABLE IF NOT EXISTS entities(snapshot TEXT,id TEXT,type TEXT,name TEXT,path TEXT,data TEXT,PRIMARY KEY(snapshot,id));
        CREATE INDEX IF NOT EXISTS entity_scope ON entities(snapshot,type,name);
        CREATE TABLE IF NOT EXISTS edges(snapshot TEXT,id TEXT,source TEXT,target TEXT,type TEXT,data TEXT,PRIMARY KEY(snapshot,id));
        CREATE INDEX IF NOT EXISTS edge_neighbors ON edges(snapshot,source,target);
        CREATE TABLE IF NOT EXISTS cache(hash TEXT,format TEXT,data TEXT,PRIMARY KEY(hash,format));
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT,job TEXT,time TEXT,level TEXT,stage TEXT,message TEXT,data TEXT);
        CREATE INDEX IF NOT EXISTS log_job ON logs(job,id);
        ''')

def put(kind, obj, source='', snapshot=''):
    with connect() as c:
        c.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?,?)', (kind,obj['id'],source,snapshot,json.dumps(obj)))
    return obj

def get(kind, id):
    with connect() as c: row = c.execute('SELECT data FROM records WHERE kind=? AND id=?',(kind,id)).fetchone()
    return json.loads(row[0]) if row else None

def records(kind, source=None, snapshot=None):
    sql='SELECT data FROM records WHERE kind=?'; args=[kind]
    if source is not None: sql+=' AND source=?'; args.append(source)
    if snapshot is not None: sql+=' AND snapshot=?'; args.append(snapshot)
    with connect() as c: rows=c.execute(sql+' ORDER BY rowid DESC',args).fetchall()
    return [json.loads(r[0]) for r in rows]

def event(action, details, source='', snapshot=''):
    return put('event',{'id':uid('event_'),'time':now(),'action':action,'details':details},source,snapshot)

LOG_LEVELS=('debug','info','success','warning','error')

def log(job, message, level='info', stage='', **data):
    """Append one line to a job's live activity log (read incrementally by the Analysis Jobs page)."""
    with connect() as c:
        c.execute('INSERT INTO logs(job,time,level,stage,message,data) VALUES(?,?,?,?,?,?)',(job,now(),level if level in LOG_LEVELS else 'info',stage,str(message)[:2000],json.dumps(data) if data else None))

def logs(job, after=0, limit=500):
    with connect() as c:
        rows=c.execute('SELECT id,time,level,stage,message,data FROM logs WHERE job=? AND id>? ORDER BY id LIMIT ?',(job,after,limit)).fetchall()
    return [{'id':r['id'],'time':r['time'],'level':r['level'],'stage':r['stage'],'message':r['message'],**({'data':json.loads(r['data'])} if r['data'] else {})} for r in rows]

def counts(snapshot):
    with connect() as c:
        rows=c.execute('SELECT status,COUNT(*) FROM files WHERE snapshot=? GROUP BY status',(snapshot,)).fetchall()
    stats={k:0 for k in ['discovered','acquired','parsed','excluded','unsupported','failed','pending']}
    for r in rows: stats[r[0]]=r[1]
    stats['discovered']=sum(r[1] for r in rows)
    stats['acquired']=stats['parsed']+stats['pending']+stats['failed']
    return stats
