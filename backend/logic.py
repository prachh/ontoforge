"""Richer logic and business rules, read from code, plus a small explainable reasoner.

Rules for domain experts come from what frameworks declare: Frappe field settings (required, unique,
mandatory_depends_on, fetch_from, non_negative, length, set_only_once, allow_on_submit), DocType permissions,
Workflow states and transitions, the submit/cancel document lifecycle, JPA unique columns, checks in Python
code (`if …: raise …`, `frappe.throw`) and Pydantic/dataclass field limits. Each rule says
its category, when it applies (trigger), how serious a breach is, and the code it came from.

Axioms are logical statements (an OWL 2 RL subset): disjoint classes, single-valued (functional) properties,
cardinalities, inverse, transitive and symmetric properties, property chains and keys. Most follow from field
definitions and are computed on demand; disjointness and model-proposed axioms are stored on the ontology.

The reasoner is a forward-chaining OWL 2 RL subset over the ontology and extracted records. It classifies
(implied is-a), detects contradictions (classes that can have no members, conflicting rules, duplicate keys,
missing required links), derives facts (inherited types, inverse links, transitive ancestors, chained and
symmetric links) and explains every result with the facts it used. It also vets model claims: a claim that
makes the ontology contradictory is rejected before it is applied.
"""
import ast
import hashlib
import json
import re
from collections import defaultdict

from . import store as s
from .models import Rule, rule_category

CODE_ORIGINS = ('doctype', 'workflow', 'permission', 'column', 'guard', 'model')  # rules this module rebuilds on every run
NUMERIC = {'Int', 'Float', 'Currency', 'Percent', 'Duration', 'Rating'}
TEXT_LENGTH = {'Data', 'Small Text', 'Link', 'Select', 'Password'}
PERMISSION_ACTIONS = {'create': 'save', 'write': 'save', 'submit': 'submit', 'cancel': 'cancel', 'delete': 'always', 'amend': 'always'}
ACTION_VERBS = {'create': 'create', 'write': 'edit', 'submit': 'submit', 'cancel': 'cancel', 'delete': 'delete', 'amend': 'amend'}


def key(name): return re.sub(r'[^a-z0-9]', '', str(name).lower())
def snake(name): return re.sub(r'[^a-z0-9]+', '_', str(name).lower()).strip('_')
def human(field): return str(field).replace('_', ' ').strip()
def title(field): return human(field).title()  # Frappe's label when a field has none
def a(name): return ('an ' if str(name)[:1].lower() in 'aeiou' else 'a ') + str(name)
DOCSTATUS = {'0': 'Draft', '1': 'Submitted', '2': 'Cancelled'}


# ---- Frappe conditions in plain language ----
COMPARE = re.compile(r'''^\s*(!|not\s+)?\s*doc\.(\w+)\s*(?:(===?|!==?|>=|<=|>|<)\s*(?:(['"])(.*?)\4|([\w.\-]+)))?\s*$''')
IN_LIST = re.compile(r'''^\s*(!)?\s*in_list\(\s*\[(.*?)\]\s*,\s*doc\.(\w+)\s*\)\s*$''')
OPS = {'==': 'is', '===': 'is', '!=': 'is not', '!==': 'is not', '>=': 'is at least', '<=': 'is at most', '>': 'is more than', '<': 'is less than'}

def humanize(expression, labels=None):
    """'eval:doc.customer_type=="Company" && doc.is_export' → 'Customer Type is Company and Is Export is set'.
    Anything not understood is returned as the original expression."""
    labels = labels or {}; text = str(expression or '').strip()
    if not text: return ''
    label = lambda f: labels.get(f) or title(f)
    if re.fullmatch(r'\w+', text): return f'{label(text)} is set'
    # Form conditions are JavaScript after 'eval:'; workflow conditions are Python. Both read doc.<field>.
    body = text[5:] if text.startswith('eval:') else text
    body = body.strip().rstrip(';')
    parts = re.split(r'\s*(&&|\|\||\band\b|\bor\b)\s*', body); out = []
    for part in parts:
        if part in ('&&', '||', 'and', 'or'): out.append('and' if part in ('&&', 'and') else 'or'); continue
        part = part.strip()
        while part.startswith('(') and part.endswith(')'): part = part[1:-1].strip()
        m = COMPARE.match(part); n = IN_LIST.match(part)
        if m:
            neg, field, op, _, quoted, bare = m.groups()
            if field == '__islocal':  # Frappe's flag for a document not saved yet
                out.append('the document has been saved' if bool(neg) != (op in ('!=', '!==') or bare in ('0', 'false')) else 'the document is new (not saved yet)'); continue
            if not op: out.append(f"{label(field)} is {'not set' if neg else 'set'}"); continue
            value = quoted if quoted is not None else bare
            if field == 'docstatus' and value in DOCSTATUS: out.append(f"the document {OPS[op]} {DOCSTATUS[value]}"); continue
            value = {'1': 'Yes', '0': 'No', 'true': 'Yes', 'false': 'No'}.get(value, value)
            out.append(f'{label(field)} {OPS[op]} {value}')
        elif n:
            neg, items, field = n.groups()
            values = [v.strip().strip('"\'') for v in items.split(',') if v.strip()]
            out.append(f"{label(field)} is {'not ' if neg else ''}one of {', '.join(values)}")
        else: return text
    return ' '.join(out)


# ---- Python conditions in plain language ----
PY_OPS = {ast.Eq: 'is', ast.NotEq: 'is not', ast.Lt: 'is less than', ast.LtE: 'is at most', ast.Gt: 'is more than', ast.GtE: 'is at least',
          ast.In: 'is one of', ast.NotIn: 'is not one of'}

def py_humanize(expression, labels=None):
    """'self.amount < 0 and not self.is_return' → 'Amount is less than 0 and Is Return is not set'. None when not understood."""
    labels = labels or {}
    try: tree = ast.parse(expression, mode='eval').body
    except SyntaxError: return None
    def name(n):
        if isinstance(n, ast.Name): return labels.get(n.id) or title(n.id)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            return labels.get(n.attr) or (title(n.attr) if n.value.id in ('self', 'doc', 'cls') else f'{title(n.value.id)} {title(n.attr)}')
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str): return labels.get(n.slice.value) or title(n.slice.value)
        if isinstance(n, ast.Call) and ast.unparse(n.func) == 'len' and n.args and (inner := name(n.args[0])): return 'the number of ' + inner
        if isinstance(n, ast.Call) and ast.unparse(n.func) in ('flt', 'cint', 'cstr', 'int', 'float', 'str', 'getdate', 'get_datetime') and n.args: return name(n.args[0])
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'get' and n.args and isinstance(n.args[0], ast.Constant): return labels.get(n.args[0].value) or title(n.args[0].value)
        return None
    def value(n):
        if isinstance(n, ast.Constant): return {True: 'Yes', False: 'No', None: 'empty'}.get(n.value, str(n.value)) if not isinstance(n.value, (int, float)) or isinstance(n.value, bool) else str(n.value)
        if isinstance(n, (ast.List, ast.Tuple, ast.Set)) and all(isinstance(e, ast.Constant) for e in n.elts): return ', '.join(str(e.value) for e in n.elts)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub) and isinstance(n.operand, ast.Constant): return '-' + str(n.operand.value)
        return name(n)
    def say(n):
        if isinstance(n, ast.BoolOp):
            parts = [say(v) for v in n.values]
            return None if None in parts else f" {'and' if isinstance(n.op, ast.And) else 'or'} ".join(parts)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            field = name(n.operand)
            if field: return f'{field} is not set'
            inner = say(n.operand)
            return f'not ({inner})' if inner else None
        if isinstance(n, ast.Compare) and len(n.ops) == 1:
            left, op, right = n.left, n.ops[0], n.comparators[0]
            field = name(left)
            if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(right, ast.Constant) and right.value is None and field:
                return f"{field} is {'not ' if isinstance(op, ast.Is) else ''}set"
            if field and type(op) in PY_OPS and (v := value(right)) is not None:
                if field.endswith('Docstatus') and v in DOCSTATUS: return f'the document {PY_OPS[type(op)]} {DOCSTATUS[v]}'
                return f'{field} {PY_OPS[type(op)]} {v}'
            return None
        field = name(n)
        return f'{field} is set' if field else None
    return say(tree)

GUARD_TRIGGERS = {'submit': ('before_submit', 'on_submit'), 'cancel': ('before_cancel', 'on_cancel'),
                  'save': ('validate', 'before_validate', 'before_save', 'before_insert', 'on_update', 'on_change')}

def guard_trigger(function):
    """When a check runs, from the Frappe hook or validate_* method that holds it."""
    for trigger, names in GUARD_TRIGGERS.items():
        if function in names: return trigger
    return 'save' if function.startswith('validate') else 'always'

# Type checks, timeouts and retries guard the program, not the business.
NOT_A_RULE = re.compile(r'(?i)\bisinstance\(|\bcallable\(|\bhasattr\(|\btime\.(monotonic|time|perf_counter)\(|\bdeadline\b|timed? ?out\b|\bretr(y|ies)\b')

def guard_rules(concept, fn, labels):
    """One rule per check in a function: what is rejected, when, with the message users see."""
    out = []
    for g in fn['properties'].get('guards', []):
        if NOT_A_RULE.search(g['condition'] + ' ' + (g.get('message') or '')): continue
        said = py_humanize(g['condition'], labels)
        condition = said or f"`{g['condition']}`"
        message = re.sub(r'\s+', ' ', g.get('message') or '').strip()
        text = f'{message.rstrip(".")}. (Rejected when {condition}.)' if message else f"{concept['name']} is rejected when {condition}"
        error = g.get('error') or ''
        category = 'authorisation' if re.search(r'(?i)permission|forbidden|denied|unauthori', error + ' ' + message) else 'validation'
        digest = hashlib.sha256(f"{fn['name']}\x1f{g['condition']}".encode()).hexdigest()[:8]
        out.append(make_rule(id=f"{concept['name']}.check.{fn['name']}.{digest}", operator='described', concept=concept['id'], origin='guard', category=category,
                             trigger=guard_trigger(fn['name']), condition=condition, text=text, evidence=[fn['id']],
                             interpretation='Check in code: the operation is rejected with an error',
                             provenance={'origin': 'guard', 'line': g['line'], 'function': fn['name'], 'error': error, 'code': g['condition']}))
    return out

def model_rules(concept, entity):
    """Field limits a Pydantic model or dataclass declares (required, ge/le, lengths, pattern, Literal choices)."""
    p = entity['properties']; props = {x['name'] for x in concept.get('properties', [])}; out = []
    source = 'Pydantic model' if p.get('model_kind') == 'pydantic' else 'Dataclass'
    def add(f, key, operator, value, text):
        executable = operator != 'described' and f['fieldname'] in props
        out.append(make_rule(id=f"{concept['name']}.{f['fieldname']}.{key}", operator=operator if executable else 'described', concept=concept['id'],
                             property=f['fieldname'] if executable or f['fieldname'] in props else '', value=value if executable else None, origin='model', trigger='save',
                             text=text, evidence=[entity['id']], interpretation=f'Declared in code ({source} field)',
                             provenance={'origin': 'model', 'line': f.get('line'), 'source': source}))
    for f in p.get('fields', []):
        label = human(f['fieldname']); label = label[:1].upper() + label[1:]
        if f.get('reqd'): add(f, 'required', 'required', None, f"{label} is required for {a(concept['name'])}")
        if 'ge' in f: add(f, 'minimum', 'minimum', f['ge'], f"{label} must be at least {f['ge']}")
        if 'gt' in f: add(f, 'above', 'described', None, f"{label} must be more than {f['gt']}")
        if 'le' in f: add(f, 'maximum', 'maximum', f['le'], f"{label} must be at most {f['le']}")
        if 'lt' in f: add(f, 'below', 'described', None, f"{label} must be less than {f['lt']}")
        if 'min_length' in f: add(f, 'min_length', 'min_length', f['min_length'], f"{label} must be at least {f['min_length']} characters")
        if 'max_length' in f: add(f, 'max_length', 'max_length', f['max_length'], f"{label} must be at most {f['max_length']} characters")
        if f.get('pattern'): add(f, 'pattern', 'pattern', f['pattern'], f"{label} must match the pattern {f['pattern']}")
        if f.get('options'): add(f, 'options', 'described', None, f"{label} must be one of {', '.join(f['options'])}")
    return out

def code_owners(c, snapshot_id, domain):
    """Map classes (and module files holding one such class) to the domain concept they implement."""
    stems = {}
    for concept in domain:
        for n in [concept['name'], *concept.get('aliases', [])]: stems[re.sub(r'\s+', '', n)] = concept
    by_key = {key(n): concept for n, concept in stems.items()}
    by_evidence = {e: concept for concept in domain for e in concept.get('evidence', [])}
    owners = {}; per_file = defaultdict(set)
    for id, name, path in c.execute("SELECT id,name,json_extract(data,'$.path') FROM entities WHERE snapshot=? AND type='Class'", (snapshot_id,)):
        concept = by_evidence.get(id) or by_key.get(key(name))
        if not concept:  # companion classes: OrderService, SalarySlipValidator…
            concept = next((cc for stem, cc in stems.items() if len(stem) > 2 and name != stem and name.startswith(stem) and name[len(stem):len(stem) + 1].isupper()), None)
        if concept: owners[id] = concept; per_file[path].add(concept['id'])
    byid = {x['id']: x for x in domain}
    files = {id: byid[next(iter(per_file[path]))] for id, path in c.execute("SELECT id,json_extract(data,'$.path') FROM entities WHERE snapshot=? AND type='File'", (snapshot_id,)) if len(per_file.get(path, ())) == 1}
    return owners, files


# ---- Rules and logic read from code (no model) ----
def make_rule(**rule):
    rule.setdefault('interpretation', 'Declared in code')
    rule['category'] = rule.get('category') or rule_category(rule)
    return Rule.model_validate(rule).model_dump()

def doctype_rules(concept, entity, workflows):
    """Business rules a Frappe DocType declares, in plain language."""
    p = entity['properties']; name = entity['name']; cid = concept['id']; ev = [entity['id']]; out = []
    labels = {f['fieldname']: f.get('label') or title(f['fieldname']) for f in p.get('fields', [])}
    def add(rid, **rule):
        rule.setdefault('evidence', ev); out.append(make_rule(id=f'{name}.{rid}', concept=cid, **rule))
    for f in p.get('fields', []):
        fn, ft, label = f['fieldname'], f.get('fieldtype', ''), labels[f['fieldname']]
        if f.get('reqd'):
            add(f'{fn}.required', operator='required', property=fn, origin='doctype', trigger='save',
                text=f'{label} is required' + (f" (defaults to {f['default']})" if f.get('default') else '') + (' (copied from ' + f['fetch_from'] + ')' if f.get('fetch_from') else ''))
        if f.get('mandatory_depends_on'):
            cond = humanize(f['mandatory_depends_on'], labels)
            add(f'{fn}.conditional', operator='conditional', property=fn, origin='doctype', trigger='save', condition=f['mandatory_depends_on'], text=f'{label} is required when {cond}')
        if f.get('unique'):
            add(f'{fn}.unique', operator='unique', property=fn, origin='doctype', trigger='save', text=f'{label} must be unique: no two {name} records share it')
        if f.get('non_negative') and ft in NUMERIC:
            add(f'{fn}.non_negative', operator='minimum', property=fn, value=0, origin='doctype', trigger='save', text=f'{label} cannot be negative')
        if f.get('length') and ft in TEXT_LENGTH and str(f['length']).isdigit() and int(f['length']) > 0:
            add(f'{fn}.length', operator='max_length', property=fn, value=int(f['length']), origin='doctype', trigger='save', severity='warning', text=f'{label} has at most {int(f["length"])} characters')
        if f.get('fetch_from'):
            link, _, source = f['fetch_from'].partition('.')
            add(f'{fn}.fetch', operator='derived', property=fn, value=f['fetch_from'], origin='doctype', trigger='save', severity='info',
                text=f"{label} is copied from the {human(source)} of the selected {labels.get(link, title(link))}" + (' (only when empty)' if f.get('fetch_if_empty') else ''))
        if f.get('set_only_once'):
            add(f'{fn}.set_only_once', operator='described', property=fn, origin='doctype', trigger='save', category='validation', text=f'{label} cannot be changed after the {name} is first saved')
        if f.get('allow_on_submit') and p.get('is_submittable'):
            add(f'{fn}.allow_on_submit', operator='described', property=fn, origin='doctype', trigger='submit', category='transition', severity='info', text=f'{label} can still be changed after the {name} is submitted')
    if p.get('is_submittable'):
        add('submitted.locked', operator='described', origin='doctype', trigger='submit', category='transition',
            text=f'A submitted {name} cannot be edited; to change it, cancel it and amend a new version')
    autoname = p.get('autoname') or ''
    if autoname.startswith('field:'):
        fn = autoname[6:].strip()
        if fn in labels and not any(r['id'] == f'{name}.{fn}.unique' for r in out):
            add(f'{fn}.unique', operator='unique', property=fn, origin='doctype', trigger='save', text=f'{labels[fn]} names the {name}, so it must be unique')
    # Permissions: who may create, submit, cancel… (one rule per action, listing the roles).
    by_action = defaultdict(list)
    for perm in p.get('permissions', []):
        for action in perm.get('can', []):
            if action in PERMISSION_ACTIONS and perm['role'] not in by_action[action]: by_action[action].append(perm['role'])
    for action in PERMISSION_ACTIONS:
        roles = by_action.get(action)
        if roles:
            add(f'permission.{action}', operator='permission', value={'action': action, 'roles': roles}, origin='permission', trigger=PERMISSION_ACTIONS[action], severity='info',
                text=f"Only {', '.join(roles[:-1]) + ' or ' + roles[-1] if len(roles) > 1 else roles[0]} can {ACTION_VERBS[action]} {a(name)}")
    # Workflows: allowed transitions, each with its action, roles and condition.
    for wf in workflows:
        merged = {}
        for t in wf['properties'].get('transitions', []):
            k = (t['state'], t['next_state'], t.get('action') or '', t.get('condition') or '')
            merged.setdefault(k, [])
            if t.get('allowed') and t['allowed'] not in merged[k]: merged[k].append(t['allowed'])
        for (src, dst, action, condition), roles in merged.items():
            who = f" by {', '.join(roles)}" if roles else ''
            when = f' when {humanize(condition, labels)}' if condition else ''
            add(f'transition.{key(src)}.{key(dst)}.{key(action)}', operator='transition', value={'from': src, 'to': dst, 'action': action, 'roles': roles, 'workflow': wf['name']},
                origin='workflow', trigger='transition', condition=condition, evidence=[wf['id'], *ev], severity='error',
                text=f'{name}: "{action or "Move"}" moves {src} → {dst}{who}{when}')
    return out

def lifecycle_from_code(entity, workflows):
    """A workflow's states, else the submit/cancel document lifecycle of a submittable DocType."""
    for wf in workflows:
        props = wf['properties']; states = [x['state'] for x in props.get('states', [])]
        if len(states) >= 2:
            transitions = [{'source': t['state'], 'target': t['next_state'], 'trigger': (t.get('action') or '') + (f" ({t['allowed']})" if t.get('allowed') else '')}
                           for t in props.get('transitions', []) if t['state'] in states and t['next_state'] in states]
            return {'field': props.get('workflow_state_field') or 'workflow_state', 'states': states, 'initial': states[0], 'transitions': transitions}, 'workflow'
    if entity['properties'].get('is_submittable'):
        return {'field': 'docstatus', 'states': ['Draft', 'Submitted', 'Cancelled'], 'initial': 'Draft',
                'transitions': [{'source': 'Draft', 'target': 'Submitted', 'trigger': 'submit'}, {'source': 'Submitted', 'target': 'Cancelled', 'trigger': 'cancel'}]}, 'doctype'
    return None, None

def python_rules(snapshot_id, domain):
    """Checks in Python functions and Pydantic/dataclass field limits, attached to the concept whose class (or module) holds them."""
    out = []
    with s.connect() as c:
        owners, files = code_owners(c, snapshot_id, domain)
        if not owners: return out
        for (d,) in c.execute("SELECT data FROM entities WHERE snapshot=? AND type='Class' AND json_extract(data,'$.properties.model_kind') IS NOT NULL", (snapshot_id,)):
            e = json.loads(d)
            if e['id'] in owners: out += model_rules(owners[e['id']], e)
        functions = [json.loads(d) for (d,) in c.execute("SELECT data FROM entities WHERE snapshot=? AND type='Function' AND json_extract(data,'$.properties.guards') IS NOT NULL", (snapshot_id,))]
        if not functions: return out
        parent = {tgt: src for src, tgt in c.execute("SELECT source,target FROM edges WHERE snapshot=? AND type='defines'", (snapshot_id,))}
    for fn in functions:
        if re.search(r'(^|/)(tests?|test_\w+\.py|\w+_test\.py|conftest\.py|patches|migrations?)(/|$)', fn.get('path', '')): continue
        node, concept = fn['id'], None
        for _ in range(6):  # nested functions: climb to the enclosing class or module
            node = parent.get(node)
            if not node: break
            concept = owners.get(node) or files.get(node)
            if concept or node in files: break
        if concept:
            labels = {x['name']: x.get('label') or title(x['name']) for x in concept.get('properties', [])}
            out += guard_rules(concept, fn, labels)
    return out

def derive(snapshot_id, ont):
    """Rebuild the code-declared rules, lifecycles and disjointness of one ontology version. Returns counts."""
    with s.connect() as c:
        models = [json.loads(d) for (d,) in c.execute("SELECT data FROM entities WHERE snapshot=? AND type='Model'", (snapshot_id,))]
        workflows = [json.loads(d) for (d,) in c.execute("SELECT data FROM entities WHERE snapshot=? AND type='Metadata' AND json_extract(data,'$.properties.frappe_type')='Workflow'", (snapshot_id,))]
    domain = [c for c in ont['concepts'] if c['layer'] == 'domain']
    index = {key(n): c for c in domain for n in [c['name'], *c.get('aliases', [])]}
    flows = defaultdict(list)
    for wf in workflows:
        target = wf['properties'].get('document_type')
        if target and wf['properties'].get('is_active', 1) not in (0, '0'): flows[key(target)].append(wf)
    rules = [r for r in ont.get('rules', []) if r.get('origin') not in CODE_ORIGINS]
    doctypes = []; lifecycles = 0
    for m in models:
        props = m.get('properties') or {}
        concept = index.get(key(m['name']))
        if not concept: continue
        if props.get('doctype'):
            doctypes.append(concept)
            rules += doctype_rules(concept, m, flows.get(key(m['name']), []))
            for f in props.get('fields', []):  # unique fields are keys
                for prop in concept.get('properties', []):
                    if prop['name'] == f['fieldname'] and (f.get('unique') or props.get('autoname') == 'field:' + f['fieldname']): prop['unique'] = True
            lifecycle, origin = lifecycle_from_code(m, flows.get(key(m['name']), []))
            current = concept.get('provenance', {}).get('lifecycle', {}).get('origin')
            if lifecycle and (origin == 'workflow' or not concept.get('lifecycle') or current in ('doctype', 'workflow')):
                concept['lifecycle'] = lifecycle; concept.setdefault('provenance', {})['lifecycle'] = {'origin': origin}; lifecycles += 1
        elif props.get('jpa'):
            for f in props.get('fields', []):
                if f.get('unique') and not f.get('id'):
                    rules.append(make_rule(id=f"{concept['name']}.{f['fieldname']}.unique", operator='unique', concept=concept['id'], property=f['fieldname'], origin='column', trigger='save',
                                           evidence=[m['id']], text=f"{f['fieldname']} must be unique: no two {concept['name']} records share it"))
                    for prop in concept.get('properties', []):
                        if prop['name'] == f['fieldname']: prop['unique'] = True
    rules += python_rules(snapshot_id, domain)
    seen = set(); ont['rules'] = [r for r in rules if not (r['id'] in seen or seen.add(r['id']))]
    # Disjointness: every Frappe record has exactly one DocType; JPA entities that neither extends are distinct.
    axioms = [a for a in ont.get('axioms', []) if a.get('origin') != 'code']
    # DocTypes defined in other apps (Employee, Company…) are record types too.
    targets = {key(f.get('options')) for m in models if (m.get('properties') or {}).get('doctype') for f in m['properties'].get('fields', []) if f.get('fieldtype') in ('Link', 'Table', 'Table MultiSelect') and f.get('options')}
    externals = [c for c in domain if c not in doctypes and key(c['name']) in targets]
    if len(doctypes) + len(externals) >= 2:
        axioms.append({'id': 'disjoint:doctypes', 'kind': 'disjoint', 'concepts': [c['id'] for c in doctypes + externals], 'origin': 'code',
                       'text': 'Every record belongs to exactly one DocType, so no record is two of these'})
    groups = defaultdict(list)
    for c in domain:
        if c.get('provenance', {}).get('jpa'):
            parent = c.get('parent') if c.get('provenance', {}).get('parent', {}).get('origin') == 'jpa' else None
            groups[parent].append(c)
    names = {c['id']: c['name'] for c in ont['concepts']}
    for parent, members in groups.items():
        if len(members) >= 2:
            axioms.append({'id': f'disjoint:jpa:{parent or "top"}', 'kind': 'disjoint', 'concepts': [c['id'] for c in members], 'origin': 'code',
                           'text': (f'Different kinds of {names.get(parent)}: nothing is two of them' if parent else 'Separate entity classes: no object is two of them')})
    known = {c['id'] for c in ont['concepts']}
    ont['axioms'] = [a for a in axioms if all(x in known for x in a.get('concepts', []))]
    counts = defaultdict(int)
    for r in ont['rules']:
        if r.get('origin') in CODE_ORIGINS: counts[r['category']] += 1
    return {'rules': dict(counts), 'lifecycles': lifecycles, 'disjoint': sum(1 for a in ont['axioms'] if a['kind'] == 'disjoint' and a['origin'] == 'code')}


# ---- Axioms: stored ones plus those that follow from field definitions ----
def link_field(ont, r):
    """The property behind a link/contains relationship, or None."""
    if r.get('kind') not in ('link', 'contains'): return None
    c = next((x for x in ont['concepts'] if x['id'] == r['source_types'][0]), None)
    return next((p for p in (c or {}).get('properties', []) if p['name'] == r.get('label')), None)

def computed_axioms(ont):
    names = {c['id']: c['name'] for c in ont['concepts']}; out = []
    rels = ont.get('relationships', [])
    for r in rels:
        f = link_field(ont, r); src, tgt = r['source_types'][0], r['target_types'][0]
        if not f: continue
        label = r.get('label') or r['id']
        if f.get('fieldtype') == 'Link':
            out.append({'id': 'functional:' + r['id'], 'kind': 'functional', 'property': r['id'], 'concepts': [src], 'origin': 'field', 'text': f'{a(names.get(src))[0].upper()}{a(names.get(src))[1:]} has at most one {human(label)} ({names.get(tgt)})'})
            if f.get('required'):
                out.append({'id': 'cardinality:' + r['id'], 'kind': 'cardinality', 'property': r['id'], 'concepts': [src, tgt], 'min': 1, 'max': 1, 'origin': 'field', 'text': f'Every {names.get(src)} has exactly one {human(label)} ({names.get(tgt)})'})
            if src == tgt and label.lower().startswith('parent'):
                out.append({'id': 'transitive:' + r['id'], 'kind': 'transitive', 'property': r['id'] + '~ancestor', 'base': r['id'], 'concepts': [src], 'label': 'is within', 'origin': 'field',
                            'text': f'{names.get(src)} forms a tree: a {names.get(src)} is within its parent and every ancestor of its parent'})
        elif f.get('fieldtype') in ('Table', 'Table MultiSelect') and r.get('kind') == 'contains':
            if f.get('required'):
                out.append({'id': 'cardinality:' + r['id'], 'kind': 'cardinality', 'property': r['id'], 'concepts': [src, tgt], 'min': 1, 'max': None, 'origin': 'field', 'text': f'Every {names.get(src)} has at least one {human(label)} row'})
            if f.get('fieldtype') == 'Table':
                out.append({'id': 'inverse:' + r['id'], 'kind': 'inverse', 'property': r['id'], 'inverse': r['id'] + '~inverse', 'label': f'is a row of {names.get(src)}', 'concepts': [tgt, src], 'origin': 'field',
                            'text': f'Each {names.get(tgt)} row belongs to the {names.get(src)} that lists it in {human(label)}'})
    # JPA one-to-many collections and the many-to-one link back are the same relationship read both ways.
    for r in rels:
        prov = r.get('provenance', {})
        if prov.get('origin') != 'jpa' or prov.get('relation') != 'OneToMany': continue
        src, tgt = r['source_types'][0], r['target_types'][0]
        back = [x for x in rels if x.get('provenance', {}).get('relation') in ('ManyToOne', 'OneToOne') and x['source_types'] == [tgt] and x['target_types'] == [src]]
        if prov.get('mapped_by'): back = [x for x in back if x.get('label') == prov['mapped_by']] or back
        if len(back) == 1:
            out.append({'id': 'inverse:' + r['id'], 'kind': 'inverse', 'property': r['id'], 'inverse': back[0]['id'], 'label': back[0].get('label') or '', 'concepts': [src, tgt], 'origin': 'field',
                        'text': f"{names.get(src)}.{r.get('label')} and {names.get(tgt)}.{back[0].get('label')} are the same relationship read in opposite directions"})
    for c in ont['concepts']:
        keys = [p['name'] for p in c.get('properties', []) if p.get('unique')]
        for k in keys:
            out.append({'id': f"key:{c['id']}:{k}", 'kind': 'key', 'concepts': [c['id']], 'fields': [k], 'origin': 'field', 'text': f'{human(k)} identifies a {c["name"]}: no two share it'})
    return out

def all_axioms(ont):
    stored = ont.get('axioms', []); ids = {a['id'] for a in stored}
    return stored + [a for a in computed_axioms(ont) if a['id'] not in ids]


# ---- Reasoner ----
class Reasoner:
    def __init__(self, ont, records=()):
        self.ont = ont; self.records = list(records); self.refresh()

    def refresh(self):
        self.byid = {c['id']: c for c in self.ont['concepts']}
        self.axioms = all_axioms(self.ont)
        self.member = defaultdict(list)
        for a in self.axioms:
            if a['kind'] == 'disjoint':
                for c in a['concepts']: self.member[c].append(a)
        self.rels = {r['id']: r for r in self.ont.get('relationships', [])}

    def name(self, id): return (self.byid.get(id) or {}).get('name', id)

    def chain(self, concept, extra=None):
        """[(concept id, how we know)] from the concept up its ancestors; `extra` maps id → hypothetical parent."""
        out, seen, cur = [], set(), concept
        while cur and cur['id'] not in seen:
            seen.add(cur['id']); parent = (extra or {}).get(cur['id'], cur.get('parent'))
            out.append(cur['id'])
            cur = parent if isinstance(parent, dict) else self.byid.get(parent)
        return out

    def why_is_a(self, chain, target):
        steps = []
        for a, b in zip(chain, chain[1:]):
            steps.append(f'{self.name(a)} is a {self.name(b)}')
            if b == target: break
        return steps

    def clash(self, chain):
        """The first disjointness axiom two classes of `chain` both fall under, as (a, b, axiom)."""
        pos = {c: i for i, c in enumerate(chain)}
        for c in chain:
            for ax in self.member.get(c, []):
                other = next((x for x in chain if x != c and x in set(ax['concepts'])), None)
                if other: return (c, other, ax) if pos[c] < pos[other] else (other, c, ax)
        return None

    def explain_clash(self, chain, clash):
        a, b, ax = clash; subject = chain[0]
        because = self.why_is_a(chain, b) + [f"{self.name(a)} and {self.name(b)} are disjoint: {ax.get('text') or 'declared disjoint'} ({ax.get('origin')})"]
        return f'{self.name(subject)} could have no members: it would be both a {self.name(a)} and a {self.name(b)}', because

    # -- vetting model claims --
    def conflict_if_parent(self, concept, parent):
        """Why making `parent` the parent of `concept` would contradict the ontology, or ''."""
        chain = self.chain(concept, {concept['id']: parent})
        clash = self.clash(chain)
        if not clash: return ''
        message, because = self.explain_clash(chain, clash)
        return message + ' (' + '; '.join(because) + ')'

    def conflict_if_disjoint(self, ids):
        """Why declaring `ids` disjoint would contradict the ontology, or ''."""
        ids = set(ids)
        for c in self.ont['concepts']:
            hit = [x for x in self.chain(c) if x in ids]
            if len(hit) >= 2:
                if hit[0] == c['id']: return f'{self.name(hit[0])} is a {self.name(hit[1])}, so they cannot be disjoint'
                return f'{c["name"]} is both a {self.name(hit[0])} and a {self.name(hit[1])}, so they cannot be disjoint'
        return ''

    # -- full reasoning --
    def run(self, limit=300):
        contradictions, facts, hierarchy = [], [], []
        counts = defaultdict(int)
        domain = [c for c in self.ont['concepts'] if c['layer'] == 'domain']
        # Classification and unsatisfiable classes.
        unsat = []
        for c in domain:
            chain = self.chain(c)
            if len(chain) > 2:
                implied = chain[2:]; counts['subclass'] += len(implied)
                hierarchy.append({'concept': c['name'], 'asserted': self.name(chain[1]), 'implied': [self.name(x) for x in implied]})
                if len(facts) < limit: facts.append({'kind': 'subclass', 'statement': f'{c["name"]} is a {self.name(chain[-1])}', 'because': self.why_is_a(chain, chain[-1])})
            clash = self.clash(chain)
            if clash:
                message, because = self.explain_clash(chain, clash); unsat.append(c['name'])
                contradictions.append({'kind': 'unsatisfiable', 'severity': 'error', 'concepts': [c['id']], 'message': message, 'because': because})
        contradictions += self.schema_checks()
        # Records: inherited types, inverse, transitive, chained and symmetric links, keys and cardinalities.
        if self.records: contradictions += self.record_reasoning(facts, counts, limit)
        by_kind = defaultdict(int)
        for a in self.axioms: by_kind[a['kind']] += 1
        return {'axioms': {'by_kind': dict(by_kind), 'items': [self.describe(a) for a in self.axioms[:600]], 'total': len(self.axioms)},
                'contradictions': contradictions[:500], 'contradiction_count': len(contradictions), 'unsatisfiable': unsat,
                'derived': dict(counts), 'facts': facts[:limit], 'hierarchy': hierarchy[:500], 'records': len(self.records)}

    def describe(self, a):
        return {'id': a['id'], 'kind': a['kind'], 'text': a.get('text') or a['id'], 'origin': a.get('origin', 'code'), 'concepts': [self.name(x) for x in a.get('concepts', [])[:12]],
                'more': max(0, len(a.get('concepts', [])) - 12), 'confidence': a.get('provenance', {}).get('confidence')}

    def schema_checks(self):
        out = []; names = self.name
        def bad(message, concepts, because, severity='error'): out.append({'kind': 'rules', 'severity': severity, 'concepts': concepts, 'message': message, 'because': because})
        fields = {}
        for c in self.ont['concepts']:
            own = {p['name']: p for x in self.chain(c) for p in (self.byid.get(x) or {}).get('properties', [])}
            fields[c['id']] = own
        by_field = defaultdict(list)
        for r in self.ont.get('rules', []):
            if r.get('property'): by_field[(r['concept'], r['property'])].append(r)
        for (cid, field), rs in by_field.items():
            if field not in fields.get(cid, {}):
                bad(f'{names(cid)} has rules on {field}, which is not one of its fields', [cid], [r.get('text') or r['id'] for r in rs[:3]], 'warning'); continue
            get = lambda op: [r for r in rs if r['operator'] == op]
            for lo in get('minimum'):
                for hi in get('maximum'):
                    if isinstance(lo['value'], (int, float)) and isinstance(hi['value'], (int, float)) and lo['value'] > hi['value']:
                        bad(f'No {names(cid)} can satisfy both rules on {field}', [cid], [lo.get('text') or lo['id'], hi.get('text') or hi['id']])
            for lo in get('min_length'):
                for hi in get('max_length'):
                    if lo['value'] > hi['value']: bad(f'No {names(cid)} can satisfy both length rules on {field}', [cid], [lo.get('text') or lo['id'], hi.get('text') or hi['id']])
            options = fields[cid][field].get('options') or []
            for r in get('equals'):
                if options and str(r['value']) not in options: bad(f'{names(cid)}.{field} must equal {r["value"]}, which is not one of its options', [cid], [r.get('text') or r['id'], 'Options: ' + ', '.join(options[:10])])
            for r in get('pattern'):
                try: re.compile(r['value'])
                except re.error as e: bad(f'{names(cid)}.{field} has an invalid pattern', [cid], [r.get('text') or r['id'], str(e)], 'warning')
        for r in self.ont.get('rules', []):
            if r['operator'] != 'transition': continue
            lc = (self.byid.get(r['concept']) or {}).get('lifecycle') or {}
            missing = [x for x in (r['value']['from'], r['value']['to']) if lc.get('states') and x not in lc['states']]
            if missing: bad(f"{names(r['concept'])}: transition uses state(s) {', '.join(missing)} that its lifecycle does not have", [r['concept']], [r.get('text') or r['id']], 'warning')
        for a in self.axioms:
            if a['kind'] == 'key':
                cid = a['concepts'][0]
                gone = [f for f in a['fields'] if f not in fields.get(cid, {})]
                if gone: bad(f'{names(cid)} is identified by {", ".join(gone)}, which it does not have', [cid], [a.get('text') or a['id']], 'warning')
            if a['kind'] == 'chain':
                p1, p2 = (self.rels.get(x) for x in a['chain'])
                if not p1 or not p2 or p1['target_types'] != p2['source_types']:
                    bad(f"Chained relationship {a.get('label') or a['id']} does not connect", a.get('concepts', [])[:1], [a.get('text') or a['id']], 'warning')
        return out

    def record_reasoning(self, facts, counts, limit):
        """Facts and violations over extracted records (Frappe test records and fixtures)."""
        out = []; byname = {}; index = {key(c['name']): c for c in self.ont['concepts'] if c['layer'] == 'domain'}
        for r in self.records:
            c = index.get(key(r['doctype']))
            if c: r['concept'] = c['id']; byname[(c['id'], str(r['name']))] = r
        recs = [r for r in self.records if r.get('concept')]
        def label(r): return f"{self.name(r['concept'])} {r['name']}"
        def fact(kind, statement, because):
            counts[kind] += 1
            if len(facts) < limit: facts.append({'kind': kind, 'statement': statement, 'because': because})
        for r in recs:
            chain = self.chain(self.byid[r['concept']])
            for anc in chain[1:]: fact('type', f'{label(r)} is a {self.name(anc)}', [f'{label(r)} is a {self.name(chain[0])}'] + self.why_is_a(chain, anc))
        links = defaultdict(list)  # relationship id → [(source record, target record)]
        for rel in self.rels.values():
            f = link_field(self.ont, rel)
            if not f or f.get('fieldtype') != 'Link': continue
            src, tgt = rel['source_types'][0], rel['target_types'][0]
            for r in recs:
                if r['concept'] != src: continue
                value = r['fields'].get(rel.get('label'))
                if value in (None, ''): continue
                target = byname.get((tgt, str(value)))
                if target: links[rel['id']].append((r, target))
        for a in self.axioms:
            if a['kind'] == 'inverse':
                for x, y in links.get(a['property'], []):
                    fact('inverse', f"{label(y)} {a.get('label') or 'is linked from'} {label(x)}", [f"{label(x)} {self.rels[a['property']].get('label')} {label(y)}", a.get('text') or 'inverse property'])
            elif a['kind'] == 'symmetric':
                for x, y in links.get(a['property'], []):
                    fact('symmetric', f"{label(y)} {self.rels[a['property']].get('label')} {label(x)}", [f"{label(x)} {self.rels[a['property']].get('label')} {label(y)}", a.get('text') or 'symmetric property'])
            elif a['kind'] == 'transitive':
                parent = {x['name']: y for x, y in links.get(a['base'], [])}
                for x, y in links.get(a['base'], []):
                    seen, cur, path = {x['name']}, y, [label(x), label(y)]
                    while cur['name'] in parent and cur['name'] not in seen:
                        seen.add(cur['name']); cur = parent[cur['name']]; path.append(label(cur))
                        if cur['name'] == x['name']:
                            out.append({'kind': 'cycle', 'severity': 'error', 'concepts': [x['concept']], 'message': f'{label(x)} is its own ancestor', 'because': [' → '.join(path)]}); break
                        fact('transitive', f"{label(x)} {a.get('label') or 'is within'} {label(cur)}", [' → '.join(path), a.get('text') or 'transitive property'])
            elif a['kind'] == 'chain':
                first, second = a['chain']; nxt = defaultdict(list)
                for y, z in links.get(second, []): nxt[y['name']].append(z)
                for x, y in links.get(first, []):
                    for z in nxt.get(y['name'], []):
                        fact('chain', f"{label(x)} {a.get('label')} {label(z)}", [f"{label(x)} {self.rels[first].get('label')} {label(y)}", f"{label(y)} {self.rels[second].get('label')} {label(z)}", a.get('text') or 'property chain'])
            elif a['kind'] == 'key':
                cid = a['concepts'][0]; seen = {}
                for r in recs:
                    if r['concept'] != cid: continue
                    k = tuple(str(r['fields'].get(f)) for f in a['fields'])
                    if any(v in ('None', '') for v in k): continue
                    if k in seen and str(seen[k]['name']) != str(r['name']):  # the same record listed in two fixture files is not a clash
                        out.append({'kind': 'key', 'severity': 'error', 'concepts': [cid], 'message': f"{label(seen[k])} and {label(r)} share {', '.join(a['fields'])} = {', '.join(k)}", 'because': [a.get('text') or 'key']})
                    elif k not in seen: seen[k] = r
            elif a['kind'] == 'cardinality' and a.get('min'):
                rel = self.rels.get(a['property'])
                if not rel or (link_field(self.ont, rel) or {}).get('fieldtype') != 'Link': continue
                for r in recs:
                    if r['concept'] == rel['source_types'][0] and r['fields'].get(rel.get('label')) in (None, ''):
                        out.append({'kind': 'cardinality', 'severity': 'warning', 'concepts': [r['concept']], 'message': f"{label(r)} has no {human(rel.get('label'))}", 'because': [a.get('text') or 'cardinality']})
        return out


def load_records(snapshot_id, limit=20000):
    with s.connect() as c:
        rows = c.execute("SELECT data FROM entities WHERE snapshot=? AND type='Instance' LIMIT ?", (snapshot_id, limit)).fetchall()
    out = []
    for (d,) in rows:
        e = json.loads(d); p = e.get('properties') or {}; fields = p.get('fields') or {}
        if p.get('doctype'): out.append({'id': e['id'], 'doctype': p['doctype'], 'name': fields.get('name') or e['name'].split(': ', 1)[-1], 'fields': fields})
    return out

def reason(ont, snapshot_id):
    return Reasoner(ont, load_records(snapshot_id)).run()


# ---- OWL export of the axioms and rules ----
def owl(m, ont, iri, lit):
    """Turtle for axioms (OWL 2 RL) and rules (oe:BusinessRule individuals). `m` is semantic.Model."""
    out = []; byid = {c['id']: c for c in ont['concepts']}; rels = {r['id']: r for r in ont.get('relationships', [])}
    C = lambda id: iri('concept', id)
    def P(rid):
        r = rels.get(rid)
        if r and r.get('kind') in ('link', 'contains') and r['source_types'][0] in byid: return m.field_iri(byid[r['source_types'][0]], r.get('label'))
        return iri('relationship', rid)
    for a in all_axioms(ont):
        k = a['kind']; note = f' ; rdfs:comment {lit(a["text"])}' if a.get('text') else ''
        if k == 'disjoint':
            members = [x for x in a['concepts'] if x in byid]
            if len(members) == 2: out.append(f'{C(members[0])} owl:disjointWith {C(members[1])} .')
            elif len(members) > 2: out.append(f'[] a owl:AllDisjointClasses ; owl:members ( {" ".join(C(x) for x in members)} ){note} .')
        elif k == 'functional' and a['property'] in rels: out.append(f'{P(a["property"])} a owl:FunctionalProperty .')
        elif k == 'symmetric' and a['property'] in rels: out.append(f'{P(a["property"])} a owl:SymmetricProperty .')
        elif k == 'cardinality' and a['property'] in rels:
            src, tgt = a['concepts'][0], a['concepts'][1] if len(a['concepts']) > 1 else None
            parts = [f'a owl:Restriction', f'owl:onProperty {P(a["property"])}'] + ([f'owl:onClass {C(tgt)}'] if tgt in byid else [])
            q = 'Qualified' if tgt in byid else ''
            if a.get('min') is not None and a.get('min') == a.get('max'): parts.append(f'owl:{"qualifiedCardinality" if q else "cardinality"} "{a["min"]}"^^xsd:nonNegativeInteger')
            else:
                if a.get('min'): parts.append(f'owl:min{q}Cardinality "{a["min"]}"^^xsd:nonNegativeInteger')
                if a.get('max') is not None: parts.append(f'owl:max{q}Cardinality "{a["max"]}"^^xsd:nonNegativeInteger')
            out.append(f'{C(src)} rdfs:subClassOf [ {" ; ".join(parts)} ] .')
        elif k == 'inverse' and a['property'] in rels:
            other = P(a['inverse']) if a['inverse'] in rels else iri('relationship', a['inverse'])
            if a['inverse'] not in rels:  # a property read backwards: from the target class to the source class
                r = rels[a['property']]
                out.append(f'{other} a owl:ObjectProperty ; rdfs:label {lit(a.get("label") or "inverse of " + a["property"])} ; rdfs:domain {C(r["target_types"][0])} ; rdfs:range {C(r["source_types"][0])} .')
            out.append(f'{other} owl:inverseOf {P(a["property"])} .'); out.append(f'{P(a["property"])} owl:inverseOf {other} .')
        elif k == 'transitive':
            scope = ''.join(f' ; rdfs:{k} {C(a["concepts"][0])}' for k in ('domain', 'range')) if a.get('concepts') and a['concepts'][0] in byid else ''
            out.append(f'{iri("relationship", a["property"])} a owl:ObjectProperty, owl:TransitiveProperty ; rdfs:label {lit(a.get("label") or a["property"])}{scope}{note} .')
            if a.get('base') in rels: out.append(f'{P(a["base"])} rdfs:subPropertyOf {iri("relationship", a["property"])} .')
        elif k == 'chain' and all(x in rels for x in a['chain']):
            out.append(f'{P(a["property"]) if a["property"] in rels else iri("relationship", a["property"])} owl:propertyChainAxiom ( {" ".join(P(x) for x in a["chain"])} ) .')
        elif k == 'key' and a['concepts'][0] in byid:
            out.append(f'{C(a["concepts"][0])} owl:hasKey ( {" ".join(m.field_iri(byid[a["concepts"][0]], f) for f in a["fields"])} ) .')
    for r in ont.get('rules', []):
        if r['concept'] not in byid: continue
        bits = ['a oe:BusinessRule', f'rdfs:label {lit(r.get("text") or r["id"])}', f'oe:appliesTo {C(r["concept"])}', f'oe:ruleCategory {lit(rule_category(r))}',
                f'oe:trigger {lit(r.get("trigger", "always"))}', f'oe:ruleOrigin {lit(r.get("origin", "declared"))}', f'oe:severity {lit(r.get("severity", "error"))}',
                f'oe:executable {"false" if r["operator"] in ("described", "permission") else "true"}', f'oe:operator {lit(r["operator"])}']
        if r.get('property'): bits.append(f'oe:onField {m.field_iri(byid[r["concept"]], r["property"])}')
        if r.get('condition'): bits.append(f'oe:condition {lit(r["condition"])}')
        out.append(f'{iri("rule", r["id"])} ' + ' ; '.join(bits) + ' .')
    return out


# ---- Rule catalogue for domain experts ----
EXECUTABLE = {'required': 'SHACL + engine', 'minimum': 'SHACL + engine', 'maximum': 'SHACL + engine', 'min_length': 'SHACL + engine', 'max_length': 'SHACL + engine',
              'pattern': 'SHACL + engine', 'equals': 'SHACL + engine', 'unique': 'OWL key', 'conditional': 'Condition', 'derived': 'Calculation',
              'transition': 'Workflow', 'permission': 'Permission', 'described': 'Described only'}
ORIGIN_LABELS = {'doctype': 'DocType', 'workflow': 'Workflow', 'permission': 'Permissions', 'column': 'JPA column', 'guard': 'Check in code', 'model': 'Model field', 'annotation': 'Bean Validation', 'llm': 'AI from code', 'declared': 'Declared'}
REVIEW_STATUSES = ('unreviewed', 'confirmed', 'disputed', 'clarify')

def review_id(source_id, rule_id): return 'review_' + __import__('hashlib').sha256(f'{source_id}\x1f{rule_id}'.encode()).hexdigest()[:24]

def reviews(source_id):
    return {r['rule_id']: r for r in s.records('rule_review', source_id)}

def catalog(ont, snapshot_id, source_id, module_of, graph, verdicts=None):
    """Every rule as a domain expert reads it: statement, where and when it applies, where it came from, review."""
    names = {c['id']: c['name'] for c in ont['concepts']}; byid = {c['id']: c for c in ont['concepts']}
    found = reviews(source_id); modules = {}; rows = []
    for r in ont.get('rules', []):
        c = byid.get(r['concept'])
        if not c: continue
        if c['id'] not in modules: modules[c['id']] = module_of(c, graph)
        evidence = []
        for e in r.get('evidence', [])[:3]:
            ent = graph.entity(e)
            if ent: evidence.append({'id': e, 'name': ent['name'], 'path': ent.get('path', ''), 'line': (not evidence and r.get('provenance', {}).get('line')) or ent.get('line') or 1})
        review = found.get(r['id'])
        rows.append({'id': r['id'], 'concept_id': c['id'], 'concept': c['name'], 'module': modules[c['id']], 'field': r.get('property') or '', 'statement': r.get('text') or r['id'],
                     'category': rule_category(r), 'trigger': r.get('trigger', 'always'), 'severity': r.get('severity', 'error'), 'operator': r['operator'], 'value': r.get('value'),
                     'condition': r.get('condition', ''), 'executable_as': EXECUTABLE.get(r['operator'], 'Described only'), 'origin': r.get('origin', 'declared'),
                     'origin_label': ORIGIN_LABELS.get(r.get('origin', 'declared'), r.get('origin', 'declared')), 'confidence': r.get('provenance', {}).get('confidence'),
                     'verdict': (verdicts or {}).get(r['id']), 'evidence': evidence,
                     'review': {'status': review['status'], 'comment': review.get('comment', ''), 'at': review.get('at')} if review else {'status': 'unreviewed', 'comment': '', 'at': None}})
    rows.sort(key=lambda x: (x['module'], x['concept'], x['category'], x['statement']))
    return rows

def without_disputed(ont, source_id):
    """A copy of the ontology without the rules a domain expert disputed (used for exports, data and checks)."""
    disputed = {k for k, v in reviews(source_id).items() if v['status'] == 'disputed'}
    if not disputed: return ont
    return {**ont, 'rules': [r for r in ont.get('rules', []) if r['id'] not in disputed]}

def catalog_markdown(rows, title):
    out = [f'# {title} — business rules', '', 'Rules read from code (DocTypes, workflows, permissions, annotations) and, where marked *AI*, by a model from code. '
           'They describe what the software enforces, not necessarily business policy.', '']
    module = concept = None
    for r in rows:
        if r['module'] != module: module = r['module']; concept = None; out += [f'## {module}', '']
        if r['concept'] != concept: concept = r['concept']; out += [f'### {concept}', '', '| Rule | Category | When | Source | Review |', '|---|---|---|---|---|']
        where = ', '.join(f"{e['path']}:{e['line']}" for e in r['evidence'][:1])
        out.append(f"| {r['statement'].replace('|', '/')} | {r['category']} | {r['trigger']} | {r['origin_label']}{' (' + where + ')' if where else ''} | {r['review']['status']}{': ' + r['review']['comment'].replace('|', '/') if r['review']['comment'] else ''} |")
    return '\n'.join(out) + '\n'

def catalog_csv(rows):
    import csv, io
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(['Module', 'Concept', 'Field', 'Rule', 'Category', 'When', 'Severity', 'Executable as', 'Condition', 'Source', 'Evidence', 'Confidence', 'Review', 'Comment', 'Rule id'])
    for r in rows:
        w.writerow([r['module'], r['concept'], r['field'], r['statement'], r['category'], r['trigger'], r['severity'], r['executable_as'], r['condition'], r['origin_label'],
                    '; '.join(f"{e['path']}:{e['line']}" for e in r['evidence']), '' if r['confidence'] is None else r['confidence'], r['review']['status'], r['review']['comment'], r['id']])
    return buf.getvalue()
