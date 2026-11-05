"""Standard RDF/OWL + SHACL views of the canonical ontology and knowledge graph.

The canonical JSON model stays the source of truth; these Turtle documents are deterministic derived exports.
rdfs:domain/range are emitted for inference only (an RDFS reasoner may type a linked record by them);
constraints are expressed separately as SHACL shapes. Scopes keep each engine operation's payload small:
model = classes, properties and hierarchy; data = model + extracted records; full = data + code graph and evidence.
"""
import json,re
from decimal import Decimal,InvalidOperation
from urllib.parse import quote,unquote

NS='urn:ontoforge:'
PREFIXES='''@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix oe: <urn:ontoforge:vocab:> .
'''
SPARQL_PREFIXES='\n'.join('PREFIX '+l[8:-2] for l in PREFIXES.splitlines())+'\n'
SCOPES=('model','data','full')

def iri(kind,id): return '<'+NS+kind+':'+quote(str(id),safe='')+'>'
def lit(value): return json.dumps(str(value),ensure_ascii=False)
def key(name): return re.sub(r'[^a-z0-9]','',str(name).lower())

def decode(term):
    """An engine result term → ('concept'|'entity'|…, id) for application IRIs, or (None, plain value)."""
    t=str(term)
    if t.startswith('<') and t.endswith('>'): t=t[1:-1]
    if t.startswith(NS):
        kind,_,rest=t[len(NS):].partition(':'); return kind,unquote(rest)
    m=re.fullmatch(r'"(.*)"(?:\^\^<[^>]*>|@[\w-]+)?',t,re.S)
    if m:
        try: return None,json.loads('"'+m.group(1)+'"')
        except ValueError: return None,m.group(1)
    return None,t

FIELD_XSD={'Int':'integer','Long Int':'integer','Check':'boolean','Float':'decimal','Currency':'decimal','Percent':'decimal','Duration':'decimal','Rating':'decimal','Date':'date','Datetime':'dateTime','Time':'time'}
DATATYPE_FIELD={'number':'Float','boolean':'Check'}
LINK_FIELDS={'Link':'LinkField','Table':'TableField','Table MultiSelect':'TableField'}
DOCUMENT_KINDS=[('SubmittableDocument','Submittable document','A DocType whose records are submitted and cancelled through a document workflow.'),
                ('ChildTable','Child table','A DocType whose rows only exist inside a parent document.'),
                ('SettingsDocument','Settings','A single-record DocType that holds configuration.'),
                ('StandardDocument','Standard document','A regular DocType that is neither submittable, a child table nor settings.'),
                ('ExternalDocument','External document','A DocType referenced here but defined in another application such as Frappe or ERPNext.')]
VOCAB=[('DomainConcept','class','Domain concept','A business concept of the analysed application.'),
       ('Document','class','Document','A Frappe DocType: a business record type.'),
       ('CodeEntity','class','Code entity','An implementation artefact extracted from source.'),
       ('ModuleClass','class','Module','The class of module groupings of DocTypes.'),
       ('LinkField','class','Link field','A field holding a reference to one record of another DocType.'),
       ('TableField','class','Table field','A field holding child-table rows of another DocType.'),
       ('Reference','class','Reference','A named reference from a metadata record to another record.'),
       ('doctypeName','annotation','DocType name','The Frappe DocType that defines this concept.'),
       ('module','annotation','module','The Frappe module of a DocType.'),
       ('linkTarget','annotation','link target','The DocType name a Link or Table field points at, whether or not it is defined here.'),
       ('kind','annotation','kind','Extracted entity type.'),
       ('path','annotation','path','Source path of an extracted entity.'),
       ('line','annotation','line','Source line of an extracted entity.'),
       ('frappeType','annotation','Frappe type','The DocType of a metadata record, such as Report or Notification.'),
       ('refKind','annotation','reference kind','Kind of record a reference names.'),
       ('refName','annotation','reference name','Name of the record a reference names.'),
       ('refersTo','object','refers to','A reference made by a metadata record.'),
       ('evidenceOf','object','evidence of','Source evidence supporting a domain concept.'),
       ('extractedAs','object','extracted as','The knowledge-graph entity a record was extracted as.'),
       ('BusinessRule','class','Business rule','A rule the software enforces, read from code or by a model from code; not necessarily business policy.'),
       ('appliesTo','object','applies to','The concept a business rule constrains.'),
       ('onField','object','on field','The field a business rule constrains.'),
       ('ruleCategory','annotation','rule category','validation, conditional, calculation, transition, authorisation, uniqueness or temporal.'),
       ('trigger','annotation','trigger','When a rule applies: always, save, submit, cancel or transition.'),
       ('ruleOrigin','annotation','rule origin','Where a rule was read from: doctype, workflow, permission, annotation, column or llm.'),
       ('severity','annotation','severity','How serious a breach of the rule is.'),
       ('executable','annotation','executable','Whether the rule can be checked automatically.'),
       ('operator','annotation','operator','The rule operator.'),
       ('condition','annotation','condition','The condition under which a rule applies, as written in code.'),
       ('basis','annotation','basis','Whether a domain-guide statement describes this system or general industry knowledge.')]

LABELS={**{t:l for t,_,l,_ in VOCAB},**{t:l for t,l,_ in DOCUMENT_KINDS}}

def typed(value,fieldtype):
    """Typed literal when the value fits the field's datatype; otherwise a plain string so SHACL reports the mismatch."""
    xsd=FIELD_XSD.get(fieldtype); v=str(value).strip()
    if not xsd: return lit(value)
    try:
        if xsd=='boolean':
            if v not in ('0','1','True','False','true','false'): return lit(value)
            v='true' if v in ('1','True','true') else 'false'
        elif xsd=='integer': v=str(int(Decimal(v))) if Decimal(v)==int(Decimal(v)) else None
        elif xsd=='decimal': v=format(Decimal(v),'f')
        elif xsd=='date': v=v if re.fullmatch(r'\d{4}-\d{2}-\d{2}',v) else None
        elif xsd=='dateTime':
            m=re.fullmatch(r'(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(:\d{2}(\.\d+)?)?',v); v=f'{m.group(1)}T{m.group(2)}{m.group(3) or ":00"}' if m else None
        elif xsd=='time':
            m=re.fullmatch(r'(\d{1,2}):(\d{2})(:\d{2}(\.\d+)?)?',v); v=f'{int(m.group(1)):02d}:{m.group(2)}{m.group(3) or ":00"}' if m else None
    except (InvalidOperation,ValueError): v=None
    return lit(value) if v is None else f'{lit(v)}^^xsd:{xsd}'

class Model:
    """Joins domain concepts with their DocType definitions and records with their concepts."""
    def __init__(self,ontology,graph):
        self.ontology=ontology; self.graph=graph
        self.domain=[c for c in ontology['concepts'] if c.get('layer')=='domain']
        self.by_key={}
        for c in self.domain:
            for n in [c['name'],*c.get('aliases',[])]: self.by_key.setdefault(key(n),c)
        self.doctype={}  # concept id → DocType Model entity
        targets=set()
        for n in graph['nodes']:
            props=n.get('properties') or {}
            if n['type']!='Model' or not props.get('doctype'): continue
            c=self.by_key.get(key(n['name']))
            if c: self.doctype.setdefault(c['id'],n)
            targets|={key(f['options']) for f in props.get('fields',[]) if f.get('fieldtype') in LINK_FIELDS and f.get('options')}
        self.external={c['id'] for c in self.domain if c['id'] not in self.doctype and key(c['name']) in targets}

    def concept(self,name): return self.by_key.get(key(name))
    def field_iri(self,concept,name): return iri('field',concept['id']+'/'+name)

    def fields(self,concept):
        dt=self.doctype.get(concept['id'])
        if dt: return [{'name':f['fieldname'],'fieldtype':f.get('fieldtype') or 'Data','options':f.get('options') or '','label':f.get('label') or f['fieldname'],
                        'required':bool(f.get('reqd')) and not f.get('default') and not f.get('fetch_from')} for f in dt['properties'].get('fields',[])]
        return [{'name':p['name'],'fieldtype':'Select' if p.get('options') and not p.get('reference') else p.get('fieldtype') or ('Link' if p.get('reference') else DATATYPE_FIELD.get(p.get('datatype'),'Data')),
                 'options':p.get('reference') or '\n'.join(p.get('options') or []),'label':p['name'],'required':bool(p.get('required'))} for p in concept.get('properties',[])]

    def record_iri(self,doctype,name): return iri('record',f'{doctype}/{name}')

def export(ontology,graph,scope='full'):
    """Turtle for the requested scope plus the SHACL shapes graph and an index for decoding engine output."""
    if scope not in SCOPES: raise ValueError('Unknown export scope')
    m=Model(ontology,graph); out=[PREFIXES]; index={'records':{},'entities':{}}
    add=out.append
    add(f'{iri("ontology",ontology["id"])} a owl:Ontology ; rdfs:label {lit("Ontology "+ontology["id"])} ; owl:versionInfo {lit(ontology["id"])} ; rdfs:comment {lit("Exported from OntoForge; scope: "+scope+".")} .')
    for term,kind,label,comment in VOCAB:
        rdf={'class':'owl:Class','annotation':'owl:AnnotationProperty','object':'owl:ObjectProperty'}[kind]
        add(f'oe:{term} a {rdf} ; rdfs:label {lit(label)} ; rdfs:comment {lit(comment)} .')
    add('oe:Document rdfs:subClassOf oe:DomainConcept .')
    for term,label,comment in DOCUMENT_KINDS: add(f'oe:{term} a owl:Class ; rdfs:label {lit(label)} ; rdfs:comment {lit(comment)} ; rdfs:subClassOf oe:Document .')
    modules=set()
    for c in ontology['concepts']:
        C=iri('concept',c['id']); bits=[f'a owl:Class',f'rdfs:label {lit(c["name"])}',f'rdfs:comment {lit(c.get("description") or c["name"])}']
        if c.get('layer')!='domain': add(f'{C} '+' ; '.join(bits+['rdfs:subClassOf oe:CodeEntity'])+' .'); continue
        bits+=[f'skos:altLabel {lit(a)}' for a in c.get('aliases',[])]+['rdfs:subClassOf oe:DomainConcept']
        if c.get('parent'): bits.append(f'rdfs:subClassOf {iri("concept",c["parent"])}')
        dt=m.doctype.get(c['id'])
        if dt:
            p=dt['properties']; kind='SubmittableDocument' if p.get('is_submittable') else 'ChildTable' if p.get('istable') else 'SettingsDocument' if p.get('issingle') else 'StandardDocument'
            bits+=[f'oe:doctypeName {lit(dt["name"])}',f'rdfs:subClassOf oe:{kind}']
            if p.get('module'): modules.add(p['module']); bits+=[f'oe:module {lit(p["module"])}',f'rdfs:subClassOf {iri("module",p["module"])}']
        elif c['id'] in m.external: bits+=[f'oe:doctypeName {lit(c["name"])}','rdfs:subClassOf oe:ExternalDocument']
        add(f'{C} '+' ; '.join(bits)+' .')
        for f in m.fields(c):
            F=m.field_iri(c,f['name']); ft=f['fieldtype']; target=m.concept(f['options']) if ft in LINK_FIELDS and f['options'] else None
            desc=f"{ft} field {f['name']} of {c['name']}"+(f" pointing at {f['options']}" if ft in LINK_FIELDS and f['options'] else '')+'.'
            if ft in LINK_FIELDS:
                add(f'{F} a owl:ObjectProperty, oe:{LINK_FIELDS[ft]} ; rdfs:label {lit(f["label"])} ; rdfs:comment {lit(desc)} ; rdfs:domain {C}'+(f' ; rdfs:range {iri("concept",target["id"])}' if target else '')+(f' ; oe:linkTarget {lit(f["options"])}' if f['options'] else '')+' .')
            else: add(f'{F} a owl:DatatypeProperty ; rdfs:label {lit(f["label"])} ; rdfs:comment {lit(desc)} ; rdfs:domain {C} ; rdfs:range xsd:{FIELD_XSD.get(ft,"string")} .')
    for module in sorted(modules): add(f'{iri("module",module)} a owl:Class, oe:ModuleClass ; rdfs:label {lit(module+" module")} ; rdfs:comment {lit("DocTypes of the "+module+" module.")} ; rdfs:subClassOf oe:Document .')
    for r in ontology.get('relationships',[]):
        if r.get('kind') in ('link','contains'): continue  # expressed as Link/Table field properties above
        add(f'{iri("relationship",r["id"])} a owl:ObjectProperty ; rdfs:label {lit(r.get("label") or r["id"])} ; rdfs:comment {lit("Declared relationship "+r["id"]+".")} .')
    from . import logic
    out+=logic.owl(m,ontology,iri,lit)
    if scope in ('data','full'):
        for n in graph['nodes']:
            if n['type']!='Instance': continue
            props=n.get('properties') or {}; c=m.concept(props.get('doctype',''))
            if not c: continue
            values=props.get('fields') or {}; name=values.get('name') or n['name'].split(': ',1)[-1]; R=m.record_iri(props['doctype'],name)
            index['records'][R[1:-1]]=n['id']
            bits=[f'a {iri("concept",c["id"])}',f'rdfs:label {lit(n["name"])}',f'oe:extractedAs {iri("entity",n["id"])}']
            defs={f['name']:f for f in m.fields(c)}
            for k,v in values.items():
                f=defs.get(k)
                if not f or v in ('',None): continue
                if f['fieldtype']=='Link' and f['options']: bits.append(f'{m.field_iri(c,k)} {m.record_iri(f["options"],v)}')
                elif f['fieldtype'] not in LINK_FIELDS: bits.append(f'{m.field_iri(c,k)} {typed(v,f["fieldtype"])}')
            add(f'{R} '+' ; '.join(bits)+' .')
    if scope=='full':
        known=set()
        for n in graph['nodes']:
            E=iri('entity',n['id']); known.add(n['id']); index['entities'][E[1:-1]]=n['id']
            bits=[f'a {iri("concept",n["type"])}',f'rdfs:label {lit(n["name"])}',f'oe:kind {lit(n["type"])}',f'oe:path {lit(n.get("path",""))}',f'oe:line {int(n.get("line") or 0)}']
            for k,v in (n.get('properties') or {}).items():
                if k=='frappe_type': bits.append(f'oe:frappeType {lit(v)}')
                elif k=='refs': bits+=[f'oe:refersTo [ a oe:Reference ; oe:refKind {lit(kind)} ; oe:refName {lit(name)} ]' for kind,name in v]
                elif isinstance(v,bool): bits.append(f'{iri("vocab",k)} {"true" if v else "false"}')
                elif isinstance(v,(int,float)): bits.append(f'{iri("vocab",k)} {typed(v,"Float")}')
                elif isinstance(v,str) and v: bits.append(f'{iri("vocab",k)} {lit(v)}')
            add(f'{E} '+' ; '.join(bits)+' .')
        kinds=set()
        for e in graph['edges']:
            if e['source'] in known and e['target'] in known: kinds.add(e['type']); add(f'{iri("entity",e["source"])} {iri("relationship",e["type"])} {iri("entity",e["target"])} .')
        declared={r['id'] for r in ontology.get('relationships',[])}
        for k in sorted(kinds-declared): add(f'{iri("relationship",k)} a owl:ObjectProperty ; rdfs:label {lit(k)} ; rdfs:comment {lit("Extracted relationship "+k+".")} .')
        for c in m.domain:
            for ev in c.get('evidence',[]):
                if ev in known: add(f'{iri("entity",ev)} oe:evidenceOf {iri("concept",c["id"])} .')
    return '\n'.join(out)+'\n', shapes(m), index

def shapes(m):
    """SHACL: required fields (without default or fetch rule), datatypes, Select options, references as IRIs, and ontology rules."""
    out=[PREFIXES]; rules={}
    for r in m.ontology.get('rules',[]): rules.setdefault(r['concept'],[]).append(r)
    for c in m.domain:
        props=[]
        for f in m.fields(c):
            ft=f['fieldtype']; bits=[f'sh:path {m.field_iri(c,f["name"])}',f'sh:name {lit(f["name"])}']
            if ft in ('Table','Table MultiSelect'): continue  # child rows are not extracted from records
            if f['required'] and ft!='Check': bits.append('sh:minCount 1')
            if ft=='Link': bits.append('sh:nodeKind sh:IRI')
            else: bits.append(f'sh:datatype xsd:{FIELD_XSD.get(ft,"string")}')
            options=[o.strip() for o in f['options'].split('\n') if o.strip()] if ft=='Select' else []
            if options: bits.append('sh:in ( '+' '.join(lit(o) for o in options)+' )')
            props.append(bits)
        for r in rules.get(c['id'],[]):
            op=r['operator']
            if op not in ('required','minimum','maximum','min_length','max_length','pattern','equals') or not r.get('property'): continue  # the rest are oe:BusinessRule individuals
            if op=='required' and r.get('origin')=='doctype': continue  # the field shape already says it (and knows defaults and fetch rules)
            bits=[f'sh:path {m.field_iri(c,r["property"])}',f'sh:name {lit(r["property"])}']
            if op=='required': bits.append('sh:minCount 1')
            elif op in ('minimum','maximum'): bits.append(f'sh:{"minInclusive" if op=="minimum" else "maxInclusive"} {lit(r["value"])}^^xsd:decimal')
            elif op in ('min_length','max_length'): bits.append(f'sh:{"minLength" if op=="min_length" else "maxLength"} {int(r["value"])}')
            elif op=='pattern': bits.append(f'sh:pattern {lit("^(?:"+str(r["value"])+")$")}')
            elif op=='equals': bits.append(f'sh:hasValue {lit(r["value"])}')
            if r.get('text'): bits.append(f'sh:message {lit(r["text"])}')
            props.append(bits)
        if props: out.append(f'{iri("shape",c["id"])} a sh:NodeShape ; rdfs:label {lit(c["name"]+" shape")} ; sh:targetClass {iri("concept",c["id"])} ; '+' ; '.join('sh:property [ '+' ; '.join(b)+' ]' for b in props)+' .')
    return '\n'.join(out)+'\n'

# ---- Model checks: SPARQL over the full scope. Each row becomes one finding. ----
CHECKS=[
    {'code':'notification-event-not-submittable','severity':'error','title':'Submit/Cancel notification on a non-submittable DocType',
     'query':'''SELECT ?n ?label ?event ?dt ?c WHERE { ?n oe:frappeType "Notification" ; rdfs:label ?label ; <urn:ontoforge:vocab:event> ?event ; <urn:ontoforge:vocab:document_type> ?dt .
       FILTER(?event IN ("Submit","Cancel")) ?c oe:doctypeName ?dt . FILTER NOT EXISTS { ?c rdfs:subClassOf oe:SubmittableDocument } FILTER NOT EXISTS { ?c rdfs:subClassOf oe:ExternalDocument } }''',
     'message':'{label} fires on {event}, but {dt} is not a submittable DocType, so it can never trigger.','evidence':['n'],'concept':'c'},
    {'code':'missing-metadata-reference','severity':'warning','title':'Reference to metadata that does not exist',
     'query':'''SELECT ?m ?label ?kind ?name WHERE { ?m oe:refersTo ?r ; rdfs:label ?label ; oe:frappeType ?from . ?r oe:refKind ?kind ; oe:refName ?name . FILTER(?kind != "DocType")
       FILTER(?from NOT IN ("Workspace","Workspace Sidebar","Sidebar","Desktop Icon","Dock"))
       FILTER EXISTS { ?any oe:frappeType ?kind } BIND(CONCAT(?kind, ": ", ?name) AS ?full) FILTER NOT EXISTS { ?t rdfs:label ?full } }''',
     'message':'{label} refers to {kind} "{name}", which is not defined in this repository.','evidence':['m']},
    {'code':'navigation-outside-repository','severity':'info','title':'Navigation links to records of other apps',
     'query':'''SELECT ?m ?label ?kind ?name WHERE { ?m oe:refersTo ?r ; rdfs:label ?label ; oe:frappeType ?from . ?r oe:refKind ?kind ; oe:refName ?name . FILTER(?kind != "DocType")
       FILTER(?from IN ("Workspace","Workspace Sidebar","Sidebar","Desktop Icon","Dock"))
       FILTER EXISTS { ?any oe:frappeType ?kind } BIND(CONCAT(?kind, ": ", ?name) AS ?full) FILTER NOT EXISTS { ?t rdfs:label ?full } }''',
     'message':'{label} links to {kind} "{name}", which is defined in another app (for example ERPNext).','evidence':['m']},
    {'code':'metadata-on-unknown-doctype','severity':'info','title':'Metadata built on a DocType not defined here',
     'query':'''SELECT ?name (COUNT(DISTINCT ?m) AS ?count) (SAMPLE(?m) AS ?example) WHERE { ?m oe:refersTo ?r . ?r oe:refKind "DocType" ; oe:refName ?name .
       FILTER NOT EXISTS { ?c oe:doctypeName ?name } } GROUP BY ?name ORDER BY DESC(?count)''',
     'message':'{count} metadata record(s) use DocType "{name}", which is neither defined nor frequently referenced here (likely Frappe/ERPNext core, or removed).','evidence':['example']},
    # Info only: apps often attach child tables to other apps' DocTypes as custom fields created in Python (setup.py).
    {'code':'orphan-child-table','severity':'info','title':'Child table no DocType JSON uses',
     'query':'''SELECT ?c ?label WHERE { ?c rdfs:subClassOf oe:ChildTable ; rdfs:label ?label . FILTER NOT EXISTS { ?f a oe:TableField ; rdfs:range ?c } }''',
     'message':'{label} is a child table, but no DocType JSON here contains it. It may be attached by custom fields created in code (for example setup.py), or be unused.','concept':'c'},
    {'code':'link-to-undefined-doctype','severity':'info','title':'Link fields to DocTypes not defined here',
     'query':'''SELECT ?target (COUNT(?f) AS ?count) (SAMPLE(?c) AS ?example) WHERE { ?f a oe:LinkField ; oe:linkTarget ?target ; rdfs:domain ?c . FILTER NOT EXISTS { ?t oe:doctypeName ?target } }
       GROUP BY ?target ORDER BY DESC(?count)''',
     'message':'{count} Link field(s) point at "{target}", which is defined outside this repository.','concept':'example'},
    {'code':'duplicate-concept-label','severity':'warning','title':'Two concepts with the same name',
     'query':'''SELECT ?a ?b ?label WHERE { ?a rdfs:subClassOf oe:DomainConcept ; rdfs:label ?label . ?b rdfs:subClassOf oe:DomainConcept ; rdfs:label ?other .
       FILTER(STR(?a) < STR(?b) && LCASE(?label) = LCASE(?other)) }''',
     'message':'Two concepts are both named "{label}".','concept':'a'},
]

def row_finding(check,row):
    values={k:decode(v)[1] for k,v in row.items()}
    evidence=[decode(row[k])[1] for k in check.get('evidence',[]) if k in row and decode(row[k])[0]=='entity']
    concept=decode(row[check['concept']])[1] if check.get('concept') and check['concept'] in row and decode(row[check['concept']])[0]=='concept' else None
    try: message=check['message'].format(**values)
    except (KeyError,IndexError): message=check['title']
    return {'code':check['code'],'severity':check['severity'],'title':check['title'],'message':message,'evidence':evidence,'concept':concept}

SAMPLE_QUERIES=[
    {'title':'Impact of changing a DocType','reasoning':False,'query':'''# Everything that points at Leave Application's evidence: fields, reports, screens, callers.
SELECT DISTINCT ?kind ?thing ?relation WHERE {
  ?concept oe:doctypeName "Leave Application" .
  ?target oe:evidenceOf ?concept .
  ?thing ?rel ?target ; oe:kind ?kind .
  ?rel rdfs:label ?relation .
} ORDER BY ?kind ?thing LIMIT 300'''},
    {'title':'Submittable documents by module','reasoning':False,'query':'''SELECT ?module ?document WHERE {
  ?document rdfs:subClassOf oe:SubmittableDocument ; oe:module ?module .
} ORDER BY ?module ?document'''},
    {'title':'Records that are documents (inferred)','reasoning':True,'query':'''# Records are only asserted as their DocType; RDFS inference adds the document kind and module.
SELECT ?record ?class WHERE {
  ?record a ?class . ?class rdfs:subClassOf* oe:Document .
  FILTER(STRSTARTS(STR(?record), "urn:ontoforge:record:"))
} ORDER BY ?record LIMIT 300'''},
    {'title':'DocTypes linking to Employee with no report','reasoning':False,'query':'''SELECT DISTINCT ?document ?field WHERE {
  ?f a oe:LinkField ; oe:linkTarget "Employee" ; rdfs:domain ?document ; rdfs:label ?field .
  ?document oe:doctypeName ?name .
  FILTER NOT EXISTS { ?r oe:frappeType "Report" ; <urn:ontoforge:vocab:ref_doctype> ?name }
} ORDER BY ?document'''},
    {'title':'Number cards and what they count','reasoning':False,'query':'''SELECT ?card ?doctype ?function WHERE {
  ?m oe:frappeType "Number Card" ; rdfs:label ?card .
  OPTIONAL { ?m <urn:ontoforge:vocab:document_type> ?doctype }
  OPTIONAL { ?m <urn:ontoforge:vocab:function> ?function }
} ORDER BY ?doctype ?card'''},
    {'title':'Screens and the backend functions they call','reasoning':False,'query':'''SELECT ?screen ?function ?path WHERE {
  ?s oe:kind "Component" ; rdfs:label ?screen ; <urn:ontoforge:relationship:calls> ?f .
  ?f rdfs:label ?function ; oe:path ?path . FILTER(STRENDS(?path, ".py"))
} ORDER BY ?screen'''},
]

UNSAFE_QUERY=re.compile(r'\b(INSERT|DELETE|LOAD|CLEAR|DROP|CREATE|ADD|MOVE|COPY|SERVICE|WITH)\b',re.I)

def check_query(text):
    """Read-only SPARQL only: no updates and no federated SERVICE calls (which could reach the network)."""
    # Ignore strings, IRIs, comments, variables and prefixed names so only SPARQL keywords are checked.
    body=re.sub(r'[?$]\w+|\b[\w-]*:[\w-]*','',re.sub(r'#[^\n]*','',re.sub(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|<[^>\s]*>','',text)))
    if len(text)>20000: raise ValueError('Query is longer than 20,000 characters')
    if UNSAFE_QUERY.search(body): raise ValueError('Only read-only SELECT, ASK, CONSTRUCT and DESCRIBE queries are allowed (no updates or SERVICE)')
    if not re.search(r'\b(SELECT|ASK|CONSTRUCT|DESCRIBE)\b',body,re.I): raise ValueError('Query must be a SELECT, ASK, CONSTRUCT or DESCRIBE query')
    return text if re.search(r'^\s*PREFIX\s+oe:',text,re.I|re.M) else SPARQL_PREFIXES+text
