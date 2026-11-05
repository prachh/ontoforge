"""Domain Expertise Knowledge Set: a domain guide written from the ontology.

A deterministic skeleton per module (concepts, lifecycles, roles, rules, events, measures, cross-module links)
is turned into a guide by one model call per module plus one overview call: glossary, processes, roles and
responsibilities, measures, pitfalls, FAQ and a learning path. Every statement is grounded: items that claim
to describe this system must cite concepts or rules from the module card (unknown citations are dropped), and
items drawn from the model's general knowledge of the industry are labelled as such (or left out on request).
Results go through the same content-addressed cache as the generative passes, so a re-run costs nothing.
"""
import json
import re
import threading
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from . import store as s, ai, usage
from .models import rule_category
from . import generative as g

PROMPT_VERSION = 'k1'
MAX_CONCEPTS = 45
SMALL_MODULE = 3
MEASURE_FIELD = re.compile(r'(?i)(total|amount|qty|quantity|rate|price|cost|balance|count|days|hours|score|percent|value|margin|budget)')


class Cited(BaseModel):
    text: str = Field(min_length=3)
    cites: list[str] = Field(default_factory=list)
    basis: Literal['system', 'general'] = 'system'

class Term(BaseModel):
    term: str = Field(min_length=2)
    definition: str = Field(min_length=5)
    not_to_confuse: str = ''
    cites: list[str] = Field(default_factory=list)
    basis: Literal['system', 'general'] = 'system'

class Step(BaseModel):
    step: str = Field(min_length=3)
    concept: str = ''
    state: str = ''
    role: str = ''

class Process(BaseModel):
    name: str = Field(min_length=3)
    purpose: str = ''
    steps: list[Step] = Field(default_factory=list)
    cites: list[str] = Field(default_factory=list)
    basis: Literal['system', 'general'] = 'system'

class Duty(BaseModel):
    role: str = Field(min_length=2)
    responsibilities: str = Field(min_length=5)
    cites: list[str] = Field(default_factory=list)
    basis: Literal['system', 'general'] = 'system'

class Measure(BaseModel):
    name: str = Field(min_length=2)
    meaning: str = Field(min_length=5)
    computed_from: str = ''
    cites: list[str] = Field(default_factory=list)
    basis: Literal['system', 'general'] = 'system'

class Question(BaseModel):
    question: str = Field(min_length=5)
    answer: str = Field(min_length=5)
    cites: list[str] = Field(default_factory=list)
    basis: Literal['system', 'general'] = 'system'

class ModuleGuide(BaseModel):
    overview: str = Field(min_length=20)
    glossary: list[Term] = Field(default_factory=list)
    processes: list[Process] = Field(default_factory=list)
    roles: list[Duty] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    pitfalls: list[Cited] = Field(default_factory=list)
    faq: list[Question] = Field(default_factory=list)
    learning_path: list[str] = Field(default_factory=list)

class DomainOverview(BaseModel):
    domain: str = Field(min_length=3)
    summary: str = Field(min_length=20)
    areas: list[Cited] = Field(default_factory=list)
    end_to_end: list[Process] = Field(default_factory=list)
    principles: list[Cited] = Field(default_factory=list)


# ---- Deterministic skeleton ----
def modules(ont, snapshot_id):
    """[(module name, [concepts])]: small modules are folded into 'Other areas'; large ones keep their most connected concepts."""
    graph = g.Graph(snapshot_id); groups = defaultdict(list)
    for c in ont['concepts']:
        if c['layer'] == 'domain': groups[g.module_of(c, graph)].append(c)
    degree = defaultdict(int)
    for r in ont['relationships']:
        degree[r['source_types'][0]] += 1; degree[r['target_types'][0]] += 1
    out, other = [], []
    for name, cs in sorted(groups.items()):
        if len(cs) < SMALL_MODULE or name == 'General': other += cs; continue
        out.append((name, sorted(cs, key=lambda c: (-degree[c['id']], c['name']))))
    if other:
        other = sorted(other, key=lambda c: (-degree[c['id']], c['name']))
        for i in range(0, len(other), MAX_CONCEPTS * 2): out.append(('Other areas' + (f' {i // (MAX_CONCEPTS * 2) + 1}' if len(other) > MAX_CONCEPTS * 2 else ''), other[i:i + MAX_CONCEPTS * 2]))
    return out, graph

def card(name, concepts, ont, graph, module_of_id):
    """The module card the model reads, and the names and rule ids it may cite."""
    names = {c['id']: c['name'] for c in ont['concepts']}; shown = concepts[:MAX_CONCEPTS]; ids = {c['id'] for c in shown}
    lines = [f'Module: {name} ({len(concepts)} concepts' + (f', {len(shown)} most connected shown' if len(concepts) > len(shown) else '') + ')', '', 'Concepts:']
    for c in shown:
        bits = []
        if c.get('parent'): bits.append('is a ' + names.get(c['parent'], '?'))
        if c.get('abstract'): bits.append('category')
        if (c.get('lifecycle') or {}).get('states'): bits.append('lifecycle ' + ' → '.join(c['lifecycle']['states'][:8]))
        links = [f"{r.get('label')}→{names.get(r['target_types'][0])}" for r in ont['relationships'] if r['source_types'][0] == c['id']][:6]
        if links: bits.append('links ' + ', '.join(links))
        lines.append(f"- {c['name']}" + (f" [{'; '.join(bits)}]" if bits else '') + f": {(c.get('description') or '')[:200]}")
    roles = defaultdict(list)
    for c in shown:
        for perm in c.get('roles', []):
            acts = [a for a in perm.get('can', []) if a in ('create', 'submit', 'cancel', 'approve', 'write', 'delete')]
            if acts: roles[perm['role']].append(f"{'/'.join(acts)} {c['name']}")
    if roles: lines += ['', 'Roles (from permissions):'] + [f'- {r}: ' + '; '.join(v[:8]) for r, v in sorted(roles.items(), key=lambda x: -len(x[1]))[:20]]
    order = {'transition': 0, 'conditional': 1, 'calculation': 2, 'uniqueness': 3, 'validation': 4, 'temporal': 5, 'authorisation': 6}
    rules = sorted((r for r in ont.get('rules', []) if r['concept'] in ids and r['operator'] != 'permission'),
                   key=lambda r: (order.get(rule_category(r), 9), r['operator'] == 'required', r['id']))[:45]
    rule_ids = {}
    if rules:
        lines += ['', 'Rules (cite as R1, R2…):']
        for i, r in enumerate(rules, 1):
            rule_ids[f'R{i}'] = r['id']; lines.append(f"R{i} {names.get(r['concept'])}: {r.get('text') or r['id']} ({rule_category(r)})")
    events = [f"{c['name']}: {e['event']} — {e['effect'][:120]}" for c in shown for e in c.get('events', [])][:20]
    if events: lines += ['', 'Events:'] + ['- ' + e for e in events]
    measures = [f"{c['name']}.{p['name']}" for c in shown for p in c.get('properties', []) if p.get('datatype') == 'number' and MEASURE_FIELD.search(p['name'])][:25]
    if measures: lines += ['', 'Numeric fields (possible measures): ' + ', '.join(measures)]
    cards = [n for (n,) in _metadata(graph.sid, ('Number Card', 'Report', 'Dashboard Chart'), [c['name'] for c in shown])][:15]
    if cards: lines += ['', 'Reports and number cards: ' + ', '.join(cards)]
    cross = [f"{c['name']}.{r.get('label')} → {names.get(r['target_types'][0])} ({module_of_id(r['target_types'][0])})" for c in shown for r in ont['relationships']
             if r['source_types'][0] == c['id'] and r['target_types'][0] not in ids and module_of_id(r['target_types'][0]) != name][:20]
    if cross: lines += ['', 'Links to other modules:'] + ['- ' + x for x in cross]
    return '\n'.join(lines), {g.key(c['name']): c for c in shown}, rule_ids

def _metadata(snapshot_id, kinds, doctypes):
    if not doctypes: return []
    with s.connect() as c:
        q = ','.join('?' * len(kinds))
        rows = c.execute(f"SELECT name,data FROM entities WHERE snapshot=? AND type='Metadata' AND json_extract(data,'$.properties.frappe_type') IN ({q}) LIMIT 2000", (snapshot_id, *kinds)).fetchall()
    wanted = set(doctypes); out = []
    for name, data in rows:
        p = json.loads(data).get('properties') or {}
        if p.get('ref_doctype') in wanted or p.get('document_type') in wanted: out.append((name,))
    return out


# ---- Generation ----
SYSTEM = (f'{g.GUARD} You are writing a domain guide for people new to this business area, from the ontology card of one module of a '
          'software system. Explain the business, not the software (no table, class or field jargon unless it is the business term). '
          'For every item set basis="system" when it describes this system and cite the concept names or rule ids (R1…) it rests on; '
          'set basis="general" only for widely known industry knowledge the card does not state (what a term means in the industry, common practice). '
          'Processes are business flows from start to finish with concrete steps (name the concept and, when known, its state and the role acting). '
          'Pitfalls are edge cases and common mistakes the rules imply. The learning path lists concept names in the order a newcomer should learn them.')
SYSTEM_NO_GENERAL = SYSTEM + ' Use basis="system" only: do not add general industry knowledge.'
OVERVIEW = (f'{g.GUARD} From the module summaries of one software system, describe its business domain for a newcomer: what business it '
            'supports, its main areas, end-to-end processes that cross modules (with steps naming concepts) and the principles every newcomer '
            'must know. Cite the module or concept names each item rests on; basis="general" for industry knowledge not in the summaries.')

def plan(ont, snapshot_id, tier='strong', include_general=True):
    """Calls, tokens and cost of a knowledge set, without calling any model."""
    groups, graph = modules(ont, snapshot_id); model = ai.config().get('models', {}).get(tier) or ''
    module_of_id = _module_lookup(ont, graph); tokens_in = tokens_out = cached = 0
    schema = len(json.dumps(ModuleGuide.model_json_schema())) / 3.6
    for name, cs in groups:
        text, _, _ = card(name, cs, ont, graph, module_of_id)
        system = SYSTEM if include_general else SYSTEM_NO_GENERAL
        if g.cache_get(g.cache_key('knowledge', PROMPT_VERSION, model, system, text)) is not None: cached += 1; continue
        tokens_in += (len(system) + len(text)) / 3.6 + schema; tokens_out += 1600
    tokens_in += (len(OVERVIEW) + 900 * len(groups)) / 3.6; tokens_out += 1200
    provider = ai.config()['provider']; price = usage.price_for(provider, model, usage.prices())
    return {'modules': len(groups), 'calls': len(groups) + 1, 'cached_calls': cached, 'model': model, 'tier': tier, 'input_tokens': round(tokens_in), 'output_tokens': round(tokens_out),
            'cost': usage.cost(price, tokens_in, tokens_out) if price else None}

def _module_lookup(ont, graph):
    cache = {}; byid = {c['id']: c for c in ont['concepts']}
    def look(id):
        if id not in cache: cache[id] = g.module_of(byid[id], graph) if id in byid else '?'
        return cache[id]
    return look

def call(name, system, user, output, tier, context):
    model = ai.config().get('models', {}).get(tier) or ''
    k = g.cache_key(name, PROMPT_VERSION, model, system, user)
    hit = g.cache_get(k)
    if hit is not None: return output.model_validate(hit), model, True
    result, model = ai.generate_json(system, user, output, tier, context)
    g.cache_put(k, name, model, result.model_dump())
    return result, model, False

def ground(items, concepts, rule_ids, include_general, rules_by_id, names, extra_names=()):
    """Keep items whose citations resolve (system) or that are labelled general (when allowed); resolve citations."""
    kept, dropped = [], 0
    for item in items:
        d = item.model_dump(); cites = []
        for x in d.pop('cites', []):
            x = str(x).strip()
            if x.upper() in rule_ids: cites.append({'kind': 'rule', 'id': rule_ids[x.upper()], 'name': (rules_by_id.get(rule_ids[x.upper()]) or {}).get('text') or x})
            elif g.key(x) in concepts: c = concepts[g.key(x)]; cites.append({'kind': 'concept', 'id': c['id'], 'name': c['name']})
            elif g.key(x) in extra_names: cites.append({'kind': 'module', 'id': x, 'name': x})
        if d.get('basis') == 'general':
            if not include_general: dropped += 1; continue
        elif not cites: dropped += 1; continue
        if 'steps' in d:
            for step in d['steps']:
                c = concepts.get(g.key(step.get('concept', '')))
                step['concept_id'] = c['id'] if c else None
                known = set(((c or {}).get('lifecycle') or {}).get('states') or []) | {o for prop in (c or {}).get('properties', []) for o in prop.get('options') or []}
                if step.get('state') and step['state'] not in known: step['state'] = ''  # only states the ontology knows
        kept.append({**d, 'cites': cites})
    return kept, dropped

def generate(source_id, snapshot, ont, tier='strong', include_general=True, record=None):
    """Write the knowledge set, saving progress after every module so the page can follow it."""
    groups, graph = modules(ont, snapshot['id']); module_of_id = _module_lookup(ont, graph)
    rules_by_id = {r['id']: r for r in ont.get('rules', [])}; names = {c['id']: c['name'] for c in ont['concepts']}
    record = record or {'id': s.uid('knowledge_')}
    record.update(status='running', ontology_id=ont['id'], tier=tier, include_general=include_general, started_at=s.now(), modules=[], overview=None,
                  progress={'done': 0, 'total': len(groups) + 1, 'current': ''}, dropped=0, calls=0, cached=0, error=None)
    save = lambda: s.put('knowledge_set', record, source_id, snapshot['id'])
    save(); context = {'source': source_id, 'operation': 'knowledge', 'tier': tier}
    try:
        for name, cs in groups:
            record['progress']['current'] = name; save()
            text, concepts, rule_ids = card(name, cs, ont, graph, module_of_id)
            try: result, model, cached = call('knowledge', SYSTEM if include_general else SYSTEM_NO_GENERAL, text, ModuleGuide, tier, context)
            except ValueError as e:  # one module's malformed output must not lose the others
                record['modules'].append({'name': name, 'concepts': [{'id': c['id'], 'name': c['name']} for c in cs], 'overview': '', 'error': str(e)[:300]})
                record['progress']['done'] += 1; save(); continue
            record['calls'] += not cached; record['cached'] += cached; dropped = 0
            section = {'name': name, 'concepts': [{'id': c['id'], 'name': c['name']} for c in cs], 'overview': result.overview, 'model': model}
            for part in ('glossary', 'processes', 'roles', 'measures', 'pitfalls', 'faq'):
                section[part], d = ground(getattr(result, part), concepts, rule_ids, include_general, rules_by_id, names); dropped += d
            section['learning_path'] = [{'id': concepts[g.key(x)]['id'], 'name': concepts[g.key(x)]['name']} for x in result.learning_path if g.key(x) in concepts]
            section['dropped'] = dropped; record['dropped'] += dropped
            record['modules'].append(section); record['progress']['done'] += 1; save()
        record['progress']['current'] = 'Domain overview'; save()
        summaries = '\n\n'.join(f"Module {m['name']}: {m['overview'][:500]}\nKey concepts: {', '.join(x['name'] for x in m['concepts'][:15])}\nProcesses: {'; '.join(p['name'] for p in m['processes'][:6])}" for m in record['modules'])
        result, model, cached = call('knowledge-overview', OVERVIEW + ('' if include_general else ' Use basis="system" only.'), summaries, DomainOverview, tier, {**context, 'operation': 'knowledge'})
        record['calls'] += not cached; record['cached'] += cached
        all_concepts = {g.key(c['name']): c for c in ont['concepts'] if c['layer'] == 'domain'}; module_names = {g.key(m['name']) for m in record['modules']}
        overview = {'domain': result.domain, 'summary': result.summary, 'model': model}
        for part in ('areas', 'end_to_end', 'principles'):
            overview[part], d = ground(getattr(result, part), all_concepts, {}, include_general, rules_by_id, names, module_names); record['dropped'] += d
        record['overview'] = overview; record['progress']['done'] += 1
        record.update(status='partial' if any(m.get('error') for m in record['modules']) else 'complete', finished_at=s.now(), progress={**record['progress'], 'current': ''}); save()
    except Exception as e:
        record.update(status='failed', error=f'{type(e).__name__}: {e}'[:500], finished_at=s.now()); save()
    return record

def start(source_id, snapshot, ont, tier, include_general):
    record = {'id': s.uid('knowledge_'), 'status': 'queued', 'ontology_id': ont['id'], 'tier': tier, 'include_general': include_general, 'modules': [], 'overview': None,
              'progress': {'done': 0, 'total': 0, 'current': ''}, 'created_at': s.now()}
    s.put('knowledge_set', record, source_id, snapshot['id'])
    threading.Thread(target=generate, args=(source_id, snapshot, ont, tier, include_general, record), daemon=True).start()
    return record

def latest(source_id, snapshot_id):
    found = s.records('knowledge_set', source_id, snapshot_id)
    return found[0] if found else None


# ---- Exports ----
def tag(item): return ' *(general knowledge)*' if item.get('basis') == 'general' else ''
def cites(item): return f" — _{', '.join(c['name'][:60] for c in item.get('cites', [])[:4])}_" if item.get('cites') else ''

def markdown(k, title):
    out = [f'# {title} — domain guide', '', 'Written by a model from the ontology. Items marked *(general knowledge)* come from the model\'s general knowledge of the '
           'industry, not from this system; every other item cites the concepts or rules it rests on.', '']
    o = k.get('overview') or {}
    if o:
        out += [f"## {o.get('domain', 'Domain')}", '', o.get('summary', ''), '']
        if o.get('areas'): out += ['### Main areas', ''] + [f"- {a['text']}{tag(a)}{cites(a)}" for a in o['areas']] + ['']
        if o.get('end_to_end'): out += ['### End-to-end processes', ''] + [line for p in o['end_to_end'] for line in process_md(p)]
        if o.get('principles'): out += ['### What every newcomer must know', ''] + [f"- {a['text']}{tag(a)}{cites(a)}" for a in o['principles']] + ['']
    for m in k.get('modules', []):
        out += [f"## {m['name']}", '', m['overview'], '']
        if m.get('learning_path'): out += ['**Learn in this order:** ' + ' → '.join(x['name'] for x in m['learning_path']), '']
        if m.get('glossary'): out += ['### Glossary', '', '| Term | Meaning | Not to be confused with |', '|---|---|---|'] + [f"| {t['term']} | {t['definition'].replace('|', '/')}{tag(t)} | {t.get('not_to_confuse', '').replace('|', '/')} |" for t in m['glossary']] + ['']
        if m.get('processes'): out += ['### Processes', ''] + [line for p in m['processes'] for line in process_md(p)]
        if m.get('roles'): out += ['### Roles and responsibilities', ''] + [f"- **{r['role']}**: {r['responsibilities']}{tag(r)}{cites(r)}" for r in m['roles']] + ['']
        if m.get('measures'): out += ['### Key measures', ''] + [f"- **{x['name']}**: {x['meaning']}" + (f" (from {x['computed_from']})" if x.get('computed_from') else '') + tag(x) for x in m['measures']] + ['']
        if m.get('pitfalls'): out += ['### Edge cases and common mistakes', ''] + [f"- {x['text']}{tag(x)}{cites(x)}" for x in m['pitfalls']] + ['']
        if m.get('faq'): out += ['### FAQ', ''] + [line for q in m['faq'] for line in (f"**{q['question']}**", '', q['answer'] + tag(q) + cites(q), '')]
    return '\n'.join(out) + '\n'

def process_md(p):
    lines = [f"**{p['name']}**{tag(p)}" + (f" — {p['purpose']}" if p.get('purpose') else ''), '']
    lines += [f"{i}. {st['step']}" + (f" _({st['concept']}{' · ' + st['state'] if st.get('state') else ''}{' · ' + st['role'] if st.get('role') else ''})_" if st.get('concept') or st.get('role') else '') for i, st in enumerate(p.get('steps', []), 1)]
    return lines + ['']

def skos(k, iri, lit, prefixes):
    """The glossary as SKOS: one skos:Concept per term, linked to the ontology concepts it cites."""
    out = [prefixes, f'{iri("glossary", "scheme")} a skos:ConceptScheme ; skos:prefLabel {lit((k.get("overview") or {}).get("domain") or "Domain glossary")} .']
    for m in k.get('modules', []):
        for t in m.get('glossary', []):
            T = iri('glossary', m['name'] + '/' + t['term'])
            bits = ['a skos:Concept', f'skos:inScheme {iri("glossary", "scheme")}', f'skos:prefLabel {lit(t["term"])}', f'skos:definition {lit(t["definition"])}', f'oe:module {lit(m["name"])}',
                    f'oe:basis {lit(t.get("basis", "system"))}']
            if t.get('not_to_confuse'): bits.append(f'skos:scopeNote {lit("Not to be confused with " + t["not_to_confuse"])}')
            bits += [f'skos:closeMatch {iri("concept", c["id"])}' for c in t.get('cites', []) if c['kind'] == 'concept']
            out.append(T + ' ' + ' ; '.join(bits) + ' .')
    return '\n'.join(out) + '\n'

def context_for(source_id, snapshot_id, ont, found, question):
    """Domain-guide excerpts for grounded Q&A: overviews of the modules of the retrieved concepts and matching glossary terms."""
    k = latest(source_id, snapshot_id)
    if not k or k.get('status') not in ('complete', 'partial'): return ''
    ids = {c['id'] for c in found}; words = {w for w in re.findall(r'[a-z0-9]+', question.lower()) if len(w) > 3}
    parts = []
    for m in k.get('modules', []):
        if ids & {c['id'] for c in m['concepts']} and len(parts) < 2: parts.append(f"Module {m['name']}: {m['overview'][:700]}")
    terms = [t for m in k.get('modules', []) for t in m.get('glossary', []) if words & set(re.findall(r'[a-z0-9]+', t['term'].lower()))][:5]
    parts += [f"Glossary — {t['term']}: {t['definition']}" + (' (general knowledge)' if t.get('basis') == 'general' else '') for t in terms]
    return '\n'.join(parts)
