import re
from typing import Protocol
import time
from .models import Ontology

class OntologyEngine(Protocol):
    def capabilities(self) -> dict: ...
    def run(self, operation: str, ontology: dict, graph: dict, **kwargs) -> dict: ...

class LightweightOntologyEngine:
    name='LightweightOntologyEngine'; version='1.0.0'
    def capabilities(self):
        return {'name':self.name,'version':self.version,'available':True,'operations':['definition','instances','rules','infer','impact','diff','demo'],'rule_operators':['required','minimum','maximum','min_length','max_length','pattern','equals'],'classification':'deterministic','unsupported':['OWL','SHACL','arbitrary expressions','runtime behaviour','business truth certification']}
    def run(self, operation, ontology, graph, **kwargs):
        start=time.perf_counter(); findings=[]; derived=[]; unsupported=[]
        concepts={c['id']:c for c in ontology['concepts']}; nodes={n['id']:n for n in graph['nodes']}; rels={r['id']:r for r in ontology.get('relationships',[])}
        def finding(code,message,evidence=None): findings.append({'code':code,'message':message,'evidence':evidence or []})
        if operation=='definition':
            try: Ontology.model_validate(ontology)
            except Exception as e: finding('schema',str(e))
            for rule in ontology.get('rules',[]):
                if rule['concept'] not in concepts: finding('invalid_rule_concept',rule['id'])
            if len(rels)!=len(ontology.get('relationships',[])): finding('duplicate_relationship_id','Relationship identifiers must be unique')
            if len(concepts)!=len(ontology['concepts']): finding('duplicate_id','Concept identifiers must be unique')
            names=set()
            for c in concepts.values():
                if c['name'] in names: finding('duplicate_name',c['name'])
                names.add(c['name'])
                if c.get('parent') and c['parent'] not in concepts: finding('missing_parent',c['name'])
                seen={c['id']}; parent=c.get('parent')
                while parent in concepts:
                    if parent in seen: finding('subclass_cycle',c['name']); break
                    seen.add(parent); parent=concepts[parent].get('parent')
            for r in rels.values():
                for t in r['source_types']+r['target_types']:
                    if t not in concepts: finding('invalid_type_reference',t)
        elif operation in ['instances','rules','infer']:
            for n in nodes.values():
                kind=n.get('concept',n['type']); concept=concepts.get(kind)
                if not concept: finding('unknown_concept',f'{n["name"]}: {kind}',[n['id']]); continue
                for p in concept.get('properties',[]):
                    value=n.get('properties',{}).get(p['name'])
                    if p.get('required') and value is None: finding('required',f'{n["name"]} is missing {p["name"]}',[n['id']])
                    if value is not None:
                        valid={'string':isinstance(value,str),'number':isinstance(value,(int,float)) and not isinstance(value,bool),'boolean':isinstance(value,bool)}.get(p['datatype'],False)
                        if not valid: finding('datatype',f'{n["name"]}.{p["name"]} must be {p["datatype"]}',[n['id']])
                        elif p.get('minimum') is not None and isinstance(value,(int,float)) and value<p['minimum']: finding('minimum',f'{n["name"]}.{p["name"]} is below {p["minimum"]}',[n['id']])
                if operation=='infer':
                    parent=concept.get('parent'); seen=set()
                    while parent and parent in concepts and parent not in seen:
                        seen.add(parent); derived.append({'entity':n['id'],'type':parent,'premises':[kind+' subclassOf '+parent,n['id']+' instanceOf '+kind]}); parent=concepts[parent].get('parent')
                for rule in ontology.get('rules',[]):
                    if rule['concept']!=kind: continue
                    value=n.get('properties',{}).get(rule['property']); op=rule['operator']
                    if op=='described': continue  # plain-language rule; nothing to execute
                    violation=(op=='required' and value is None) or (op=='equals' and value is not None and value!=rule.get('value'))
                    if op in ('minimum','maximum') and value is not None:
                        violation=not isinstance(value,(int,float)) or (value<rule['value'] if op=='minimum' else value>rule['value'])
                    elif op in ('min_length','max_length') and isinstance(value,str):
                        violation=len(value)<rule['value'] if op=='min_length' else len(value)>rule['value']
                    elif op=='pattern' and isinstance(value,str):
                        try: violation=re.fullmatch(rule['value'],value) is None
                        except re.error: unsupported.append('pattern: '+rule['id'])
                    if violation: finding('rule',f'{n["name"]}: {rule.get("text") or rule["id"]}',[n['id']])
            for e in graph['edges']:
                if e['source'] not in nodes or e['target'] not in nodes: finding('reference',e['id']); continue
                r=rels.get(e['type'])
                if not r: finding('relationship_type',e['type'],[e['id']]); continue
                if nodes[e['source']]['type'] not in r['source_types'] or nodes[e['target']]['type'] not in r['target_types']: finding('relationship_endpoints',e['id'],[e['id']])
        elif operation=='impact':
            seed=kwargs.get('entity_id'); visited={seed}; frontier=[seed]
            while frontier:
                cur=frontier.pop()
                for e in graph['edges']:
                    if e['source']==cur and e['target'] not in visited: visited.add(e['target']); frontier.append(e['target'])
            derived=[{'entity':x,'premises':['reachable from '+str(seed)],'meaning':'Potential dependency, not guaranteed runtime impact'} for x in visited if x in nodes and x!=seed]
        elif operation=='diff':
            before={c['id']:c for c in (kwargs.get('previous') or {}).get('concepts',[])}
            derived=[{'change':'added' if k not in before else 'modified','concept':k} for k,v in concepts.items() if before.get(k)!=v]+[{'change':'removed','concept':k} for k in before if k not in concepts]
        else: unsupported.append(operation)
        return {'engine':self.name,'engine_version':self.version,'ontology_version':ontology['id'],'graph_snapshot':graph.get('snapshot'),'operation':operation,'findings':findings,'derived_facts':derived,'unsupported_constructs':unsupported,'status':'unsupported' if unsupported else ('findings' if findings else 'success'),'classification':'deterministic','duration_ms':round((time.perf_counter()-start)*1000,3),'certificates':'Not supported'}

from .external import OpenOntologiesEngine
