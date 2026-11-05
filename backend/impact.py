"""Impact analysis: what is affected when a concept or a piece of code changes.

Impact flows against dependencies: whatever calls, imports, extends, uses or refers to a changed thing may break.
For a concept that means concepts linking to it and its subclasses; for code, its callers, importers, subclasses and
the class or module holding it. The two layers are joined by the concept→code mappings, so a concept change reaches
the code that implements it and a code change reaches the concepts it implements. Business rules, lifecycles,
APIs and tests in the affected set are listed, and every item says why it is affected (the chain back to the start).
"""
import json
from collections import defaultdict, deque

from . import store as s

CODE_KINDS = {'calls': 'calls', 'imports': 'imports', 'inherits': 'extends', 'uses': 'uses', 'references': 'refers to', 'implements': 'implements', 'instance_of': 'is a record of'}
LIMIT = 600


def impact(snapshot, ont, node_id, depth=3):
    domain = {c['id']: c for c in ont['concepts'] if c['layer'] == 'domain'}
    # Domain dependencies, reversed: who refers to a concept, and its subclasses.
    d_rev = defaultdict(list)
    for c in domain.values():
        if c.get('parent') in domain and c['parent'] != c['id']: d_rev[c['parent']].append((c['id'], 'is a kind of'))
    for r in ont.get('relationships', []):
        for a in r.get('source_types', []):
            for b in r.get('target_types', []):
                if a in domain and b in domain and a != b: d_rev[b].append((a, f"links to it ({r.get('label') or r['id']})"))
    # Code dependencies, reversed, plus containment (a changed method changes its class and module).
    c_rev = defaultdict(list); parent = {}
    with s.connect() as c:
        for src, tgt, kind in c.execute('SELECT source,target,type FROM edges WHERE snapshot=?', (snapshot['id'],)):
            if kind == 'defines': parent[tgt] = src
            elif kind in CODE_KINDS and src != tgt: c_rev[tgt].append((src, CODE_KINDS[kind] + ' it'))
        is_code = c.execute('SELECT 1 FROM entities WHERE snapshot=? AND id=?', (snapshot['id'], node_id)).fetchone() is not None
    # Concept ↔ code.
    code_of = defaultdict(set); concepts_of = defaultdict(set)
    for cid, cc in domain.items():
        for e in cc.get('evidence', []): code_of[cid].add(e); concepts_of[e].add(cid)
    for m in s.records('mapping', snapshot['source_id'], snapshot['id']):
        if m.get('concept_id') in domain and m.get('status') != 'rejected': code_of[m['concept_id']].add(m['entity_id']); concepts_of[m['entity_id']].add(m['concept_id'])
    if node_id not in domain and not is_code: raise ValueError('Unknown concept or code entity')

    seen = {}  # id → (layer, depth, previous id, reason)
    queue = deque()
    def visit(id, layer, d, prev, why):
        if id in seen or len(seen) >= LIMIT: return
        seen[id] = (layer, d, prev, why); queue.append((id, layer, d))
    visit(node_id, 'domain' if node_id in domain else 'code', 0, None, 'changed')
    while queue:
        id, layer, d = queue.popleft()
        if d >= depth: continue
        if layer == 'domain':
            for nxt, why in d_rev.get(id, []): visit(nxt, 'domain', d + 1, id, why)
            if d == 0:  # the concept's own code, then everything depending on that code
                for e in code_of.get(id, []): visit(e, 'code', d + 1, id, 'implements it')
        else:
            for nxt, why in c_rev.get(id, []): visit(nxt, 'code', d + 1, id, why)
            if parent.get(id): visit(parent[id], 'code', d + 1, id, 'contains it')
            for cid in concepts_of.get(id, []): visit(cid, 'domain', d + 1, id, 'is implemented by it')

    ids = [i for i, v in seen.items() if v[0] == 'code']
    info = {}
    with s.connect() as c:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for id, data in c.execute(f"SELECT id,data FROM entities WHERE snapshot=? AND id IN ({','.join('?' * len(chunk))})", (snapshot['id'], *chunk)):
                e = json.loads(data); info[id] = {'name': e['name'], 'type': e['type'], 'path': e.get('path', ''), 'line': e.get('line')}
    name = lambda i: domain[i]['name'] if i in domain else info.get(i, {}).get('name', i)
    def because(i):
        chain = []; cur = i
        while cur is not None and len(chain) < 8:
            layer, d, prev, why = seen[cur]
            if prev is None: break
            chain.append(f"{name(cur)} {(' ' + why).replace(' it', ' ' + name(prev), 1).strip()}" if f' {why} '.find(' it') >= 0 else f'{name(cur)} {why} {name(prev)}')
            cur = prev
        return chain
    concepts, code = [], []
    for i, (layer, d, prev, why) in seen.items():
        if i == node_id: continue
        if layer == 'domain': concepts.append({'id': i, 'name': domain[i]['name'], 'depth': d, 'via': prev, 'because': because(i), 'lifecycle': ' → '.join((domain[i].get('lifecycle') or {}).get('states', []))})
        elif i in info: code.append({'id': i, **info[i], 'depth': d, 'via': prev, 'because': because(i)})
    affected_concepts = {node_id} | {x['id'] for x in concepts} if node_id in domain else {x['id'] for x in concepts}
    affected_code = {x['id'] for x in code} | ({node_id} if is_code else set())
    rules = [{'id': r['id'], 'text': r.get('text') or r['id'], 'concept': domain[r['concept']]['name'], 'category': r.get('category'), 'origin': r.get('origin')}
             for r in ont.get('rules', []) if r['concept'] in affected_concepts or any(e in affected_code for e in r.get('evidence', [])[:1]) and r['concept'] in domain]
    key = lambda x: (x['depth'], x['name'])
    concepts.sort(key=key); code.sort(key=key)
    start = {'id': node_id, 'name': name(node_id), 'layer': 'domain' if node_id in domain else 'code', 'type': 'Concept' if node_id in domain else info.get(node_id, {}).get('type')}
    if is_code and node_id not in info:
        with s.connect() as c:
            row = c.execute('SELECT data FROM entities WHERE snapshot=? AND id=?', (snapshot['id'], node_id)).fetchone()
        if row: e = json.loads(row[0]); start.update(name=e['name'], type=e['type'], path=e.get('path'))
    return {'start': start, 'depth': depth, 'truncated': len(seen) >= LIMIT,
            'concepts': concepts, 'code': [x for x in code if x['type'] not in ('Test',)], 'tests': [x for x in code if x['type'] == 'Test' or '/test' in (x['path'] or '')],
            'apis': [x for x in code if x['type'] == 'API'], 'rules': rules[:400], 'rule_total': len(rules),
            'lifecycles': [x for x in concepts if x['lifecycle']] + ([{'id': node_id, 'name': start['name'], 'depth': 0, 'because': [], 'lifecycle': ' → '.join((domain[node_id].get('lifecycle') or {}).get('states', []))}]
                                                                    if node_id in domain and (domain[node_id].get('lifecycle') or {}).get('states') else [])}
