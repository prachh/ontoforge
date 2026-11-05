"""How the ontology and the code change over time.

Timeline: every ontology version of a source with what changed since the version it was based on, plus the
analyses, AI statement batches, expert reviews and other events around it, for a KronoGraph-style view.

Hotspots: code that changes often. GitHub sources are downloaded as archives without history, so the history is
fetched separately with a bare, blob-less, shallow clone (commit metadata and trees only, no file contents, only
the chosen window) and read with `git log`. Local sources with a .git folder are read in place. No repository
code, hooks or submodules run. Each file gets commits, authors and last change; joined with the analysed code it
gives a risk score that also counts the business rules found in that file.
"""
import base64
import math
import re
import os
import shutil
import subprocess
import tempfile
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import store as s
from .acquisition import TOKENS, github_repo

MAX_VERSIONS = 80


# ---- Timeline ----
def counts(ont):
    domain = [c for c in ont['concepts'] if c['layer'] == 'domain']
    return {'concepts': len(domain), 'relationships': len(ont.get('relationships', [])), 'rules': len(ont.get('rules', [])),
            'axioms': len(ont.get('axioms', [])), 'lifecycles': sum(1 for c in domain if (c.get('lifecycle') or {}).get('states'))}


def timeline(source_id):
    from . import generative
    versions = sorted(s.records('ontology', source_id), key=lambda o: o.get('created_at', ''))[-MAX_VERSIONS:]
    byid = {v['id']: v for v in versions}
    snaps = {x['id']: x for x in s.records('snapshot', source_id)}
    out = []; previous = None
    for v in versions:
        base = byid.get(v.get('based_on')) or (s.get('ontology', v['based_on']) if v.get('based_on') else None) or previous
        diff = generative.changes(v, base)['changes'] if base else []
        kinds = defaultdict(int)
        for c in diff: kinds[c['kind']] += 1
        snap = snaps.get(v.get('snapshot_id') or '') or {}
        out.append({'id': v['id'], 'time': v.get('created_at'), 'status': v.get('status'), 'based_on': v.get('based_on'), 'snapshot': snap.get('id') or v.get('snapshot_id'),
                    'commit': (snap.get('commit') or '')[:10], 'counts': counts(v), 'changes': dict(kinds), 'total_changes': len(diff), 'first': base is None})
        previous = v
    # Snapshot ids are stored on the record row, not always in the JSON: fill them from the store.
    with s.connect() as c:
        rows = dict(c.execute("SELECT id,snapshot FROM records WHERE kind='ontology' AND source=?", (source_id,)).fetchall())
        claims = []
        try:
            for snap, pas, status, n, first, last, model in c.execute(
                    'SELECT snapshot,pass,status,count(*),min(created),max(created),max(model) FROM claims WHERE snapshot IN (SELECT id FROM records WHERE kind=\'snapshot\' AND source=?) GROUP BY snapshot,pass,status', (source_id,)):
                claims.append({'snapshot': snap, 'pass': pas, 'status': status, 'count': n, 'start': first, 'end': last, 'model': model})
        except Exception:
            claims = []
    for v in out:
        if not v['snapshot']: v['snapshot'] = rows.get(v['id'])
        v['commit'] = v['commit'] or (snaps.get(v['snapshot'] or '', {}).get('commit') or '')[:10]
    jobs = [{'id': j['id'], 'start': j.get('created_at'), 'end': j.get('finished_at') or j.get('updated_at') or j.get('created_at'), 'status': j.get('status'), 'mode': j.get('mode'),
             'snapshot': j.get('snapshot_id')} for j in s.records('job', source_id)]
    reviews = [{'time': r.get('at'), 'status': r.get('status'), 'rule': r.get('rule_id'), 'comment': (r.get('comment') or '')[:160]} for r in s.records('rule_review', source_id) if r.get('at')]
    events = [{'time': e.get('time'), 'action': e.get('action'), 'details': (e.get('details') or '')[:200]} for e in s.records('event', source_id)[:400]]
    return {'versions': out, 'jobs': jobs, 'claims': claims, 'reviews': reviews, 'events': events}


def stack(source_id):
    """Concept membership per version, for the 3D version stack: one shared list of names, the latest links between them,
    and per version which concepts exist, which were added and which were removed."""
    versions = sorted(s.records('ontology', source_id), key=lambda o: o.get('created_at', ''))[-MAX_VERSIONS:]
    names, index = [], {}
    def ix(n):
        if n not in index: index[n] = len(names); names.append(n)
        return index[n]
    out = []; before = set()
    for v in versions:
        present = {ix(c['name']) for c in v['concepts'] if c['layer'] == 'domain'}
        out.append({'id': v['id'], 'time': v.get('created_at'), 'status': v.get('status'), 'members': sorted(present),
                    'added': sorted(present - before) if out else [], 'removed': sorted(before - present)})
        before = present
    edges = set()
    if versions:
        last = versions[-1]; byid = {c['id']: c['name'] for c in last['concepts'] if c['layer'] == 'domain'}
        for r in last.get('relationships', []):
            for a in r.get('source_types', []):
                for b in r.get('target_types', []):
                    if a in byid and b in byid and a != b: edges.add(tuple(sorted((index[byid[a]], index[byid[b]]))))
        for c in last['concepts']:
            if c['layer'] == 'domain' and c.get('parent') in byid and c['parent'] != c['id']: edges.add(tuple(sorted((index[c['name']], index[byid[c['parent']]]))))
    return {'names': names, 'edges': sorted(edges), 'versions': out}


def version_changes(source_id, version_id):
    from . import generative
    v = s.get('ontology', version_id)
    if not v: raise ValueError('Unknown ontology version')
    base = s.get('ontology', v['based_on']) if v.get('based_on') else None
    return {'version': version_id, 'base': base['id'] if base else None, 'counts': counts(v), 'base_counts': counts(base) if base else None,
            'changes': generative.changes(v, base)['changes'] if base else []}


# ---- Hotspots from git history ----
def _git(args, cwd=None, env=None, timeout=900):
    base_env = {'PATH': os.environ.get('PATH', ''), 'HOME': tempfile.gettempdir(), 'GIT_TERMINAL_PROMPT': '0', 'GIT_ASKPASS': '', 'GIT_CONFIG_NOSYSTEM': '1', 'LC_ALL': 'C'}
    cmd = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.file.allow=never', '-c', 'core.fsmonitor=false', *args]
    r = subprocess.run(cmd, cwd=cwd, env={**base_env, **(env or {})}, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0: raise RuntimeError((r.stderr or r.stdout or 'git failed').strip().splitlines()[-1][:300])
    return r.stdout


def parse_log(text, skip=frozenset(), prefix=''):
    """`git log --name-only --format=%x1e%H%x1f%an%x1f%aI` → per-file commits, authors, first and last change."""
    files = {}; commits = 0; authors = set()
    for block in text.split('\x1e'):
        if not block.strip(): continue
        head, _, body = block.partition('\n')
        parts = head.split('\x1f')
        if len(parts) < 3 or parts[0] in skip: continue
        sha, author, when = parts[0], parts[1], parts[2]
        commits += 1; authors.add(author)
        for path in body.splitlines():
            path = path.strip()
            if not path: continue
            if prefix:
                if not path.startswith(prefix): continue
                path = path[len(prefix):]
            f = files.setdefault(path, {'commits': 0, 'authors': set(), 'last': when, 'first': when})
            f['commits'] += 1; f['authors'].add(author)
            if when > f['last']: f['last'] = when
            if when < f['first']: f['first'] = when
    return files, commits, authors


def _record(source_id, **data):
    current = s.get('hotspots', source_id) or {'id': source_id}
    current.update(data); s.put('hotspots', current, source_id)
    return current


def git_root(source):
    """The git repository holding a local source (the folder itself or one of its parents), if any."""
    if source.get('kind') != 'local': return None
    here = Path(source.get('location', '')).resolve()
    return next((d for d in (here, *here.parents) if (d / '.git').exists()), None)


def compute(source, months=12):
    since = (datetime.now(timezone.utc) - timedelta(days=int(months * 30.5))).date().isoformat()
    fmt = '--format=%x1e%H%x1f%an%x1f%aI'
    prefix = str(source.get('subdirectory') or '').strip('/'); prefix = prefix + '/' if prefix else ''
    root = git_root(source)
    if source.get('kind') == 'local' and root:
        _record(source['id'], status='running', message='Reading the local git history')
        inner = Path(source['location']).resolve().relative_to(root).as_posix().strip('.')
        local_prefix = '/'.join(x for x in (inner, prefix.rstrip('/')) if x); local_prefix = local_prefix + '/' if local_prefix else ''
        text = _git(['log', '--no-renames', '--name-only', fmt, f'--since={since}', 'HEAD'], cwd=str(root))
        files, commits, authors = parse_log(text, prefix=local_prefix)
    elif source.get('kind') == 'github':
        repo = github_repo(source['location']); token = TOKENS.get(source['id']) or os.getenv('GITHUB_TOKEN')
        env = {}
        if token:  # passed as configuration in the environment, never on the command line
            env = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'http.https://github.com/.extraheader',
                   'GIT_CONFIG_VALUE_0': 'Authorization: Basic ' + base64.b64encode(f'x-access-token:{token}'.encode()).decode()}
        work = Path(tempfile.mkdtemp(prefix='onto-history-'))
        try:
            _record(source['id'], status='running', message=f'Downloading commit history since {since} from GitHub (no file contents)')
            args = ['clone', '--bare', '--filter=blob:none', '--single-branch', '--no-tags', f'--shallow-since={since}']
            if source.get('reference'): args += ['--branch', source['reference']]
            _git(args + [f'https://github.com/{repo}.git', str(work / 'repo.git')], env=env)
            _record(source['id'], status='running', message='Reading commits')
            shallow = set((work / 'repo.git' / 'shallow').read_text().split()) if (work / 'repo.git' / 'shallow').exists() else set()
            text = _git(['log', '--no-renames', '--name-only', fmt, f'--since={since}', 'HEAD'], cwd=str(work / 'repo.git'))
            files, commits, authors = parse_log(text, skip=frozenset(shallow), prefix=prefix)  # a shallow boundary commit would list every file
        finally:
            shutil.rmtree(work, ignore_errors=True)
    else:
        raise ValueError('Git history is only available for GitHub sources and local folders that are git repositories (ZIP uploads have no history)')
    stored = {p: [f['commits'], len(f['authors']), f['last'][:10], f['first'][:10]] for p, f in files.items()}
    return _record(source['id'], status='complete', message='', since=since, months=months, computed_at=s.now(), commits=commits, authors=len(authors), files=stored)


def start(source, months=12):
    if source.get('kind') == 'local' and not git_root(source): raise ValueError('This local folder is not inside a git repository, so it has no history to read')
    current = s.get('hotspots', source['id'])
    if current and current.get('status') == 'running': raise ValueError('Git history is already being read for this source')
    _record(source['id'], status='running', message='Starting', months=months, files=current.get('files', {}) if current else {})
    def run():
        try: compute(source, months)
        except Exception as e: _record(source['id'], status='failed', message=str(e)[:400])
    threading.Thread(target=run, daemon=True).start()


def hotspots(source_id, snapshot, ont, limit=300):
    """Files ranked by risk: how often they change, how much code they hold and how many business rules live there."""
    rec = s.get('hotspots', source_id)
    if not rec: return {'status': 'none'}
    base = {k: rec.get(k) for k in ('status', 'message', 'since', 'months', 'computed_at', 'commits', 'authors')}
    files = rec.get('files') or {}
    if rec.get('status') != 'complete' and not files: return base
    funcs = defaultdict(int); entity_path = {}
    with s.connect() as c:
        for id, kind, path in c.execute("SELECT id,type,json_extract(data,'$.path') FROM entities WHERE snapshot=?", (snapshot['id'],)):
            entity_path[id] = path
            if kind in ('Function', 'Class', 'Model', 'Component'): funcs[path] += 1
    rules = defaultdict(int); concepts = defaultdict(set)
    for r in ont.get('rules', []):
        for e in r.get('evidence', [])[:1]:
            if entity_path.get(e): rules[entity_path[e]] += 1
    names = {c['id']: c['name'] for c in ont['concepts']}
    for c in ont['concepts']:
        if c['layer'] != 'domain': continue
        for e in c.get('evidence', []):
            if entity_path.get(e): concepts[entity_path[e]].add(c['name'])
    rows = []
    for path, (n, authors, last, first) in files.items():
        if path not in funcs and path not in rules: continue  # only files the analysis knows (code, DocTypes)
        score = n * (1 + math.log2(1 + funcs[path])) * (1 + math.log2(1 + rules[path]))
        rows.append({'path': path, 'commits': n, 'authors': authors, 'last': last, 'first': first, 'size': funcs[path], 'rules': rules[path],
                     'concepts': sorted(concepts[path])[:6], 'score': round(score, 1), 'test': bool(re.search(r'(^|/)(tests?/|test_[^/]*$|[^/]*_test\.\w+$|[^/]*\.(spec|test)\.\w+$)', path))})
    rows.sort(key=lambda r: -r['score'])
    top = rows[0]['score'] if rows else 1
    for r in rows: r['risk'] = round(r['score'] / top, 3)
    return {**base, 'files_changed': len(files), 'files': rows[:limit], 'matched': len(rows)}


def path_heat(source_id):
    """Per-file commit counts and authors, for colouring the code city and landscape."""
    rec = s.get('hotspots', source_id) or {}
    return {'status': rec.get('status', 'none'), 'since': rec.get('since'), 'files': {p: v[:2] for p, v in (rec.get('files') or {}).items()}}
