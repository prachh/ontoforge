import ast, hashlib, json, re
from pathlib import Path
import yaml, sqlglot
from sqlglot import exp
from tree_sitter import Language, Parser
import tree_sitter_javascript as js
import tree_sitter_typescript as ts
import tree_sitter_java as java

FORMATS={'.py':'Python','.ts':'TypeScript','.tsx':'TypeScript','.js':'JavaScript','.jsx':'JavaScript','.sql':'SQL','.json':'JSON','.yaml':'YAML','.yml':'YAML','.md':'Markdown','.txt':'Text','.vue':'Vue','.java':'Java','.xml':'XML','.gradle':'Gradle','.kts':'Gradle','.properties':'Properties'}
PARSER_VERSION='17'
def stable(*parts): return hashlib.sha256('|'.join(map(str,parts)).encode()).hexdigest()[:24]
FRAPPE_DOCTYPE_CALLS={'frappe.get_doc','frappe.new_doc','frappe.get_cached_doc','frappe.get_last_doc','frappe.get_all','frappe.get_list','frappe.delete_doc','frappe.get_value','frappe.get_cached_value','frappe.db.get_value','frappe.db.get_all','frappe.db.get_list','frappe.db.exists','frappe.db.count','frappe.db.set_value','frappe.db.get_single_value'}

def frappe_doctype(call):
    """DocType named by a literal in a Frappe document API call, e.g. frappe.get_doc("Salary Slip")."""
    if ast.unparse(call.func) not in FRAPPE_DOCTYPE_CALLS or not call.args: return None
    first=call.args[0]
    if isinstance(first,ast.Dict):
        first=next((v for k,v in zip(first.keys,first.values) if isinstance(k,ast.Constant) and k.value=='doctype'),None)
    return first.value if isinstance(first,ast.Constant) and isinstance(first.value,str) and first.value.strip() else None

# ---- Python checks that reject input: `if cond: raise X("msg")`, `frappe.throw(...)`, Pydantic/dataclass fields ----
THROW_CALLS={'frappe.throw','throw'}
INTERNAL_ERRORS={'NotImplementedError','TypeError','ImportError','ModuleNotFoundError','AttributeError','AssertionError','StopIteration','KeyboardInterrupt','SystemExit','RecursionError'}
MAX_GUARDS=15

def message_text(node):
    """The message a check shows: "Amount must be positive", _("…"), f"… {x} …" and "…".format(x) all read as text."""
    if node is None: return ''
    if isinstance(node,ast.Constant) and isinstance(node.value,str): return node.value
    if isinstance(node,ast.JoinedStr): return ''.join(v.value if isinstance(v,ast.Constant) else '{'+ast.unparse(v.value)+'}' for v in node.values)
    if isinstance(node,ast.Call):
        name=ast.unparse(node.func)
        if name in ('_','frappe._','gettext','lazy_gettext') and node.args: return message_text(node.args[0])
        if isinstance(node.func,ast.Attribute) and node.func.attr=='format': return message_text(node.func.value)
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Mod): return message_text(node.left)
    return ''

def rejection(stmt):
    """(error name, message) when a statement stops the operation with an error a user would see."""
    if isinstance(stmt,ast.Raise) and stmt.exc is not None:
        exc=stmt.exc; call=exc if isinstance(exc,ast.Call) else None
        name=ast.unparse(call.func if call else exc).rsplit('.',1)[-1]
        if name in INTERNAL_ERRORS or not re.search(r'(Error|Exception|Invalid|Denied|Forbidden|Conflict)$',name): return None
        return name,message_text(call.args[0] if call and call.args else None)
    if isinstance(stmt,ast.Expr) and isinstance(stmt.value,ast.Call) and ast.unparse(stmt.value.func) in THROW_CALLS:
        call=stmt.value; title=next((message_text(k.value) for k in call.keywords if k.arg=='title'),'')
        return title or 'ValidationError',message_text(call.args[0] if call.args else next((k.value for k in call.keywords if k.arg in ('msg','message')),None))
    return None

def guards(fn):
    """The checks in one function: the condition under which it rejects the operation, the error and its message."""
    out=[]
    for node in ast.walk(fn):
        if not isinstance(node,ast.If): continue
        hit=next((r for r in map(rejection,node.body) if r),None)
        if not hit: continue
        condition=ast.unparse(node.test)
        if len(condition)>240: continue
        out.append({'condition':condition,'error':hit[0],'message':hit[1].strip()[:300],'line':node.lineno})
        if len(out)>=MAX_GUARDS: break
    return out

FIELD_LIMITS=('ge','gt','le','lt','min_length','max_length','pattern','regex')
def model_fields(node):
    """Fields of a Pydantic model (BaseModel/SQLModel) or dataclass with their limits; None for any other class."""
    bases={ast.unparse(b).rsplit('.',1)[-1] for b in node.bases}
    decorators={ast.unparse(d.func if isinstance(d,ast.Call) else d).rsplit('.',1)[-1] for d in node.decorator_list}
    kind='pydantic' if bases&{'BaseModel','SQLModel','BaseSettings'} else 'dataclass' if 'dataclass' in decorators else None
    if not kind: return None
    fields=[]
    for stmt in node.body:
        if not (isinstance(stmt,ast.AnnAssign) and isinstance(stmt.target,ast.Name)) or stmt.target.id.startswith('_'): continue
        type_text=ast.unparse(stmt.annotation); f={'fieldname':stmt.target.id,'type':type_text,'line':stmt.lineno}
        optional=bool(re.search(r'\bOptional\[|\bNone\b|\|\s*None',type_text)) or 'ClassVar' in type_text
        value=stmt.value; default=value is not None
        if isinstance(value,ast.Call) and ast.unparse(value.func).rsplit('.',1)[-1] in ('Field','field'):
            kw={k.arg:k.value for k in value.keywords if k.arg}
            default=bool(value.args and not (isinstance(value.args[0],ast.Constant) and value.args[0].value is Ellipsis)) or any(k in kw for k in ('default','default_factory'))
            for k in FIELD_LIMITS:
                if k in kw and isinstance(kw[k],ast.Constant) and not isinstance(kw[k].value,bool): f['pattern' if k=='regex' else k]=kw[k].value
            if isinstance(kw.get('description'),ast.Constant): f['description']=str(kw['description'].value)[:200]
        literal=re.search(r'Literal\[(.*)\]',type_text)
        if literal:
            try: f['options']=[str(v) for v in ast.literal_eval('['+literal.group(1)+']')]
            except (ValueError,SyntaxError): pass
        f['reqd']=not default and not optional
        fields.append(f)
    return {'model_kind':kind,'fields':fields} if fields else None

FRAPPE_METHOD=re.compile(r'[A-Za-z_]\w*(\.[A-Za-z_]\w*)+')
JS_FUNCTION_VALUES={'arrow_function','function_expression','function'}

def string_value(node,consts):
    """A literal string, or an identifier bound to one at the top of the script (const DOCTYPE = "Employee Checkin")."""
    if node is None: return None
    if node.type=='string': return node.text.decode()[1:-1] or None
    if node.type=='template_string' and not node.named_children: return node.text.decode()[1:-1] or None
    if node.type=='identifier': return consts.get(node.text.decode())
    return None

def walk_script(tree,parent,add,refs,offset=0):
    """Shared JavaScript/TypeScript walk. Frappe UI resources name backend methods by dotted path (url: "hrms.api.x")
    and DocTypes by name (doctype: "Salary Slip"); both become references resolved against the Python side."""
    consts={}
    for decl in tree.root_node.named_children:
        for d in (decl.named_children if decl.type=='lexical_declaration' else []):
            name,value=d.child_by_field_name('name'),d.child_by_field_name('value')
            if name is not None and value is not None and value.type=='string': consts[name.text.decode()]=value.text.decode()[1:-1]
    def walk(node,parent):
        line=node.start_point.row+1+offset
        kind={'class_declaration':'Class','function_declaration':'Function','method_definition':'Function','interface_declaration':'Model','type_alias_declaration':'Model'}.get(node.type)
        name=node.child_by_field_name('name')
        if node.type=='variable_declarator' and name is not None and name.type=='identifier' and getattr(node.child_by_field_name('value'),'type',None) in JS_FUNCTION_VALUES: kind='Function'
        if kind and name:
            parent=add(name.text.decode(),kind,line,node.end_point.row+1+offset,parent)
        if node.type=='call_expression':
            fn=node.child_by_field_name('function')
            if fn: refs.append({'source':parent,'name':fn.text.decode(),'type':'calls','line':line})
        elif node.type=='import_statement':
            module=string_value(node.child_by_field_name('source'),{})
            if module: refs.append({'source':parent,'name':module,'type':'imports','line':line,'js':True})
        elif node.type=='pair':
            key=node.child_by_field_name('key'); key=key.text.decode().strip('\'"') if key is not None else ''
            value=string_value(node.child_by_field_name('value'),consts)
            if key=='url' and value and FRAPPE_METHOD.fullmatch(value): refs.append({'source':parent,'name':value,'type':'calls','line':line,'frappe_method':True})
            elif key=='doctype' and value: refs.append({'source':parent,'name':value,'type':'uses','line':line,'doctype':True})
        for child in node.named_children: walk(child,parent)
    walk(tree.root_node,parent)

JAVA_KINDS={'class_declaration':'Class','enum_declaration':'Class','record_declaration':'Class','interface_declaration':'Model','annotation_type_declaration':'Model','method_declaration':'Function','constructor_declaration':'Function'}
SPRING_MAPPINGS={'GetMapping':'GET','PostMapping':'POST','PutMapping':'PUT','PatchMapping':'PATCH','DeleteMapping':'DELETE','RequestMapping':None}

def java_annotations(node):
    """{annotation name: annotation node} for a declaration's modifiers."""
    mods=next((c for c in node.named_children if c.type=='modifiers'),None)
    return {a.child_by_field_name('name').text.decode().rsplit('.',1)[-1]:a for a in (mods.named_children if mods else []) if a.type in ('annotation','marker_annotation')}

def java_mapping(annotation):
    """(route, HTTP method or None) from a Spring mapping annotation's path and method arguments."""
    args=annotation.child_by_field_name('arguments'); route=''; verb=None
    for a in (args.named_children if args else []):
        key,value=(a.child_by_field_name('key').text.decode(),a.child_by_field_name('value')) if a.type=='element_value_pair' else ('value',a)
        if key in ('value','path') and value.type=='string_literal' and not route: route=value.text.decode()[1:-1]
        elif key in ('value','path') and value.type=='element_value_array_initializer' and value.named_children and value.named_children[0].type=='string_literal': route=value.named_children[0].text.decode()[1:-1]
        elif key=='method': verb=value.text.decode().rsplit('.',1)[-1].strip('{} ').split(',')[0].strip() or None
    return route,verb

JPA_KINDS={'Entity':'entity','MappedSuperclass':'mapped_superclass','Embeddable':'embeddable'}
JPA_RELATIONS={'ManyToOne':'Link','OneToOne':'Link','OneToMany':'Table','ManyToMany':'Table MultiSelect'}
# Java types mapped onto the field-type vocabulary the ontology already uses for Frappe DocTypes.
JAVA_FIELD_TYPES={'String':'Data','char':'Data','Character':'Data','UUID':'Data','int':'Int','Integer':'Int','long':'Int','Long':'Int','short':'Int','Short':'Int','byte':'Int','Byte':'Int',
                  'double':'Float','Double':'Float','float':'Float','Float':'Float','BigDecimal':'Currency','BigInteger':'Int','boolean':'Check','Boolean':'Check',
                  'LocalDate':'Date','Date':'Date','LocalDateTime':'Datetime','Instant':'Datetime','ZonedDateTime':'Datetime','OffsetDateTime':'Datetime','Timestamp':'Datetime','LocalTime':'Time'}
BEAN_VALIDATION={'NotNull','NotBlank','NotEmpty','Size','Min','Max','DecimalMin','DecimalMax','Digits','Pattern','Email','Past','PastOrPresent','Future','FutureOrPresent','Positive','PositiveOrZero','Negative','NegativeOrZero'}

def annotation_args(annotation):
    """{key: literal text} of an annotation's arguments; a bare value is stored as 'value'."""
    args=annotation.child_by_field_name('arguments') if annotation.type=='annotation' else None; out={}
    for a in (args.named_children if args else []):
        key,value=(a.child_by_field_name('key').text.decode(),a.child_by_field_name('value')) if a.type=='element_value_pair' else ('value',a)
        text=value.text.decode() if value is not None else ''
        # Java string literals keep their escapes in source ("\\d{10}" is the regex \d{10}).
        out[key]=re.sub(r'\\(.)',r'\1',text[1:-1]) if value is not None and value.type=='string_literal' else text
    return out

def javadoc(node):
    """First paragraph of the Javadoc comment directly before a declaration."""
    prev=node.prev_named_sibling
    if prev is None or prev.type!='block_comment' or not prev.text.startswith(b'/**'): return ''
    body=re.sub(r'^\s*\*\s?','',prev.text.decode()[3:-2],flags=re.M)
    body='\n'.join(l for l in body.splitlines() if not l.strip().startswith('@'))  # drop @author, @param ... tags
    first=re.split(r'\n\s*\n',body.strip())[0]
    # {@link Vet Vet's} renders as its label; {@code x} and {@link Vet} as their target.
    first=re.sub(r'\{@\w+\s+([^}\s]+)(?:\s+([^}]*))?\}',lambda m:m.group(2) or m.group(1).rsplit('.',1)[-1].split('#')[-1],first)
    return re.sub(r'<[^>]+>','',re.sub(r'\s+',' ',first)).strip()

def type_name(node):
    """(simple type, element type for collections/generics) of a Java type node."""
    text=re.sub(r'\s+','',node.text.decode()) if node is not None else ''
    outer=text.split('<',1)[0].rsplit('.',1)[-1]; inner=re.findall(r'<([^<>]+)>',text)
    return outer,(inner[-1].split(',')[-1].rsplit('.',1)[-1] if inner else outer)

def jpa_model(node,notes,name,add,refs,root,line,path=''):
    """A JPA @Entity/@MappedSuperclass/@Embeddable: one Model with typed fields, relations to other models
    and Bean Validation constraints, all read from annotations (no execution, no inference)."""
    kind=next(JPA_KINDS[n] for n in JPA_KINDS if n in notes)
    superclass=node.child_by_field_name('superclass'); fields=[]; relations=[]
    body=node.child_by_field_name('body')
    for decl in (body.named_children if body is not None else []):
        if decl.type!='field_declaration': continue
        fnotes=java_annotations(decl); mods=next((c for c in decl.named_children if c.type=='modifiers'),None)
        if 'Transient' in fnotes or (mods is not None and b'static' in mods.text.split()): continue
        outer,element=type_name(decl.child_by_field_name('type'))
        relation=next((r for r in JPA_RELATIONS if r in fnotes),None)
        constraints=[{'kind':n,**annotation_args(a)} for n,a in fnotes.items() if n in BEAN_VALIDATION]
        column={**annotation_args(fnotes['Column'])} if 'Column' in fnotes else {}
        if 'JoinColumn' in fnotes: column={**column,**annotation_args(fnotes['JoinColumn'])}
        for declarator in decl.children_by_field_name('declarator'):
            fname=declarator.child_by_field_name('name').text.decode()
            fieldtype=JPA_RELATIONS[relation] if relation else 'Enum' if 'Enumerated' in fnotes else JAVA_FIELD_TYPES.get(outer,'Data' if outer[:1].isupper() else 'Data')
            field={'fieldname':fname,'fieldtype':fieldtype,'java_type':outer,'options':element if relation else ('' if outer in JAVA_FIELD_TYPES else outer),'label':fname,
                   'reqd':any(c['kind'] in ('NotNull','NotBlank','NotEmpty') for c in constraints) or column.get('nullable')=='false','constraints':constraints,
                   'column':column.get('name',''),'line':decl.start_point.row+1}
            if column.get('unique')=='true' or 'Id' in fnotes: field['unique']=True
            if 'Id' in fnotes: field['id']=True
            if relation:
                rargs=annotation_args(fnotes[relation]); field['relation']=relation; field['cascade']='cascade' in rargs
                if rargs.get('mappedBy'): field['mapped_by']=rargs['mappedBy']
                if rargs.get('optional')=='false': field['reqd']=True
            fields.append(field)
            if relation: relations.append((field,decl.start_point.row+1))
    table=annotation_args(notes['Table']).get('name','') if 'Table' in notes else ''
    props={'jpa':kind,'table':table,'extends':superclass.named_children[0].text.decode().split('<')[0] if superclass is not None and superclass.named_children else '',
           'doc':javadoc(node),'fields':fields,'module':Path(path).parent.name if path else ''}
    model=add(name,'Model',line,node.end_point.row+1,root,props)
    for field,fline in relations:
        refs.append({'source':model,'name':field['options'],'type':'contains' if field['relation']=='OneToMany' and field['cascade'] else 'links','line':fline,'field':field['fieldname'],'rule':'jpa-relation'})
    refs.append({'source':model,'name':name,'type':'controller','line':line,'rule':'jpa-entity-class'})
    if props['extends']: refs.append({'source':model,'name':props['extends'],'type':'links','line':line,'field':'(extends)','rule':'jpa-superclass'})

def walk_java(tree,root,add,refs,edges,path):
    """Java via Tree-sitter: types, methods/constructors (@Test methods become Tests), extends, imports, calls,
    object creation and Spring MVC mapping annotations as API operations (class-level @RequestMapping prefixes routes)."""
    def walk(node,parent,prefix):
        line=node.start_point.row+1; kind=JAVA_KINDS.get(node.type); name=node.child_by_field_name('name')
        if node.type=='import_declaration':
            target=next((c for c in node.named_children if c.type in ('scoped_identifier','identifier')),None)
            if target is not None and not any(c.type=='asterisk' for c in node.named_children):
                refs.append({'source':parent,'name':target.text.decode(),'type':'imports','line':line,'java':True,'static':any(c.type=='static' for c in node.children)})
            return
        if kind and name is not None:
            notes=java_annotations(node); props={}
            if kind=='Function':
                if 'Test' in notes or 'ParameterizedTest' in notes: kind='Test'
                params=node.child_by_field_name('parameters')
                props['parameters']=[p.child_by_field_name('name').text.decode() for p in (params.named_children if params else []) if p.child_by_field_name('name') is not None]
                returns=node.child_by_field_name('type'); props['returns']=returns.text.decode() if returns is not None else ''
            if node.type=='enum_declaration':
                body=node.child_by_field_name('body'); props['enum']=[c.child_by_field_name('name').text.decode() for c in (body.named_children if body is not None else []) if c.type=='enum_constant']
            if kind=='Class' and javadoc(node): props['doc']=javadoc(node)
            nid=add(name.text.decode(),kind,line,node.end_point.row+1,parent,props)
            if node.type in ('class_declaration','record_declaration') and any(n in notes for n in JPA_KINDS): jpa_model(node,notes,name.text.decode(),add,refs,root,line,path)
            if node.type=='interface_declaration':
                # Spring Data: interface OwnerRepository extends JpaRepository<Owner, Integer> uses the Owner entity.
                for ext in (c for c in node.named_children if c.type=='extends_interfaces'):
                    for t in re.findall(r'(?:Repository|CrudRepository|JpaRepository|PagingAndSortingRepository|ListCrudRepository)\s*<\s*([\w.]+)',ext.text.decode()):
                        refs.append({'source':nid,'name':t.rsplit('.',1)[-1],'type':'uses','line':line,'rule':'spring-data-repository'})
            superclass=node.child_by_field_name('superclass')
            if superclass is not None and superclass.named_children: refs.append({'source':nid,'name':superclass.named_children[0].text.decode(),'type':'inherits','line':line})
            if kind=='Class' and 'RequestMapping' in notes: prefix=java_mapping(notes['RequestMapping'])[0]
            elif kind in ('Function','Test'):
                for note,default in SPRING_MAPPINGS.items():
                    if note not in notes: continue
                    route,verb=java_mapping(notes[note]); verb=default or verb or 'ANY'
                    route='/'+'/'.join(p.strip('/') for p in (prefix,route) if p.strip('/'))
                    api=add(f'{verb} {route}','API',line,node.end_point.row+1,root,{'http_method':verb,'route':route})
                    edges.append({'source':api,'target':nid,'type':'implements','status':'resolved','method':'tree-sitter','line':line,'path':path})
            parent=nid
        elif node.type=='method_invocation':
            obj=node.child_by_field_name('object')
            refs.append({'source':parent,'name':(obj.text.decode()+'.' if obj is not None else '')+node.child_by_field_name('name').text.decode(),'type':'calls','line':line})
        elif node.type=='object_creation_expression':
            created=node.child_by_field_name('type')
            if created is not None: refs.append({'source':parent,'name':re.sub(r'<.*','',created.text.decode()).rsplit('.',1)[-1],'type':'calls','line':line})
        for child in node.named_children: walk(child,parent,prefix)
    walk(tree.root_node,root,'')

SECRET_KEY=re.compile(r'pass(word|wd)?\b|secret|token|credential|api[-_.]?key|private[-_.]?key',re.I)
MAX_SETTINGS=300

def line_of(text,needle): return text[:max(0,text.find(needle))].count('\n')+1

def add_dependency(add,root,text,coords,scope,line=None,version=''):
    group,_,artifact=coords.partition(':'); artifact,_,inline=artifact.partition(':')
    return add(f'{group}:{artifact}' if artifact else group,'Dependency',line or line_of(text,artifact or group),parent=root,properties={'group':group,'artifact':artifact,'version':version or inline,'scope':scope})

def parse_pom(text,add,nodes,root):
    """A Maven pom.xml: project coordinates on the File, one Dependency per parent, dependency and build plugin.
    ${property} versions are substituted from <properties>; dependencyManagement entries are versions, not dependencies."""
    import xml.etree.ElementTree as ET
    project=ET.fromstring(text)
    for el in project.iter(): el.tag=el.tag.rpartition('}')[2]
    get=lambda el,tag:(el.findtext(tag) or '').strip()
    props={el.tag:(el.text or '').strip() for el in project.find('properties') if isinstance(el.tag,str)} if project.find('properties') is not None else {}
    sub=lambda v:re.sub(r'\$\{([^}]+)\}',lambda m:props.get(m.group(1),m.group(0)),v)
    parent=project.find('parent'); group=get(project,'groupId') or (get(parent,'groupId') if parent is not None else '')
    nodes[0]['properties'].update(build='maven',project=f"{group}:{get(project,'artifactId')}",version=sub(get(project,'version')),modules=[(m.text or '').strip() for m in project.iterfind('modules/module')])
    entries=([(parent,'parent')] if parent is not None else [])+[(d,get(d,'scope') or 'compile') for d in project.iterfind('dependencies/dependency')]+[(p,'plugin') for p in project.iterfind('build/plugins/plugin')]
    for el,scope in entries:
        artifact=get(el,'artifactId')
        if artifact: add_dependency(add,root,text,f"{get(el,'groupId') or ('org.apache.maven.plugins' if scope=='plugin' else '')}:{artifact}",scope,line_of(text,f'<artifactId>{artifact}</artifactId>'),sub(get(el,'version')))
    nodes[0]['excerpt']='\n'.join([f"Maven project {nodes[0]['properties']['project']}"]+[f"{n['name']} ({n['properties']['scope']})" for n in nodes[1:]])[:1600]

GRADLE_DEPENDENCY=re.compile(r'^\s*(\w+)\s*\(?\s*(?:(?:enforcedPlatform|platform)\s*\(\s*)?["\']([\w.\-]+:[\w.\-]+(?::[^"\']*)?)["\']',re.M)
GRADLE_PLUGIN=re.compile(r'^\s*id\s*\(?\s*["\']([\w.\-]+)["\'][ \t]*\)?[ \t]*(?:version[ \t]*\(?[ \t]*["\']([^"\']+)["\'])?',re.M)
GRADLE_VARIABLE=re.compile(r'^\s*(?:ext\.|val\s+|def\s+)?(\w+)\s*=\s*["\']([^"\'$]*)["\']',re.M)
GRADLE_SETTINGS={'group':re.compile(r'^group\s*=\s*["\']([^"\']+)',re.M),'version':re.compile(r'^version\s*=\s*["\']([^"\']+)',re.M),'project':re.compile(r'rootProject\.name\s*=\s*["\']([^"\']+)')}

def parse_gradle(text,add,nodes,root):
    """A Gradle build or settings script, read as text (never evaluated): plugins and string-literal dependency
    declarations become Dependencies; ${variable} versions are substituted from simple string assignments."""
    variables=dict(GRADLE_VARIABLE.findall(text)); sub=lambda v:re.sub(r'\$\{?(\w+)\}?',lambda m:variables.get(m.group(1),m.group(0)),v)
    nodes[0]['properties'].update(build='gradle',**{k:m.group(1) for k,r in GRADLE_SETTINGS.items() if (m:=r.search(text))},modules=re.findall(r'^\s*include\s*\(?\s*["\']:?([^"\']+)',text,re.M))
    for m in GRADLE_PLUGIN.finditer(text): add_dependency(add,root,text,m.group(1),'plugin',text[:m.start()].count('\n')+1,m.group(2) or '')
    for m in GRADLE_DEPENDENCY.finditer(text):
        if m.group(1) not in ('id','group','version'): add_dependency(add,root,text,sub(m.group(2)),m.group(1),text[:m.start()].count('\n')+1)

def flatten(x,prefix=''):
    if isinstance(x,dict):
        for k,v in x.items(): yield from flatten(v,f'{prefix}.{k}' if prefix else str(k))
    elif isinstance(x,list) and not all(isinstance(v,(str,int,float,bool)) or v is None for v in x):
        for i,v in enumerate(x): yield from flatten(v,f'{prefix}[{i}]')
    else: yield prefix,', '.join(map(str,x)) if isinstance(x,list) else '' if x is None else str(x)

def add_configuration(path,text,settings,add,nodes,root):
    """One Configuration entity per settings file; values of secret-looking keys are masked in entities and excerpts."""
    settings={k:('***' if SECRET_KEY.search(k) and v else v) for k,v in list(settings.items())[:MAX_SETTINGS]}
    stem=Path(path).stem; profile=stem.split('-',1)[1] if stem.startswith(('application-','bootstrap-')) else ''
    add(stem,'Configuration',1,text.count('\n')+1,root,{'profile':profile,'settings':settings,'truncated':len(settings)==MAX_SETTINGS})
    # The File excerpt would otherwise quote the raw, unmasked first lines.
    nodes[0]['excerpt']=nodes[-1]['excerpt']='\n'.join(f'{k} = {v}' for k,v in settings.items())[:1600]

def parse_properties(text):
    """Java .properties: key=value / key: value pairs with comments and backslash line continuations."""
    settings={}; logical=re.sub(r'\\\r?\n\s*','',text)
    for line in logical.splitlines():
        line=line.strip()
        if not line or line[0] in '#!': continue
        m=re.match(r'((?:\\.|[^=:\s])+)\s*[=:\s]\s*(.*)',line)
        key,value=(m.group(1),m.group(2)) if m else (line,'')
        settings[key.replace('\\','')]=value
    return settings

def spring_config(path): return re.fullmatch(r'(application|bootstrap)(-[\w.-]+)?\.(ya?ml)',Path(path).name) is not None

VUE_SCRIPT=re.compile(r'<script\b([^>]*)>(.*?)</script>',re.S)
VUE_TEMPLATE_DOCTYPE=re.compile(r'(?<![:\w-])doctype="([^"{}]+)"')

def parse_vue(path,text,add,refs,root):
    """A Vue single-file component: one Component entity; its <script> blocks are parsed with Tree-sitter and
    doctype="…" attributes in the template count as DocType usage."""
    component=add(Path(path).parent.name+'/'+Path(path).stem,'Component',1,text.count('\n')+1,root)
    for m in VUE_SCRIPT.finditer(text):
        lang=re.search(r'lang=["\']?(\w+)',m.group(1)); lang=lang.group(1) if lang else 'js'
        language=Language(ts.language_tsx() if lang=='tsx' else ts.language_typescript() if lang=='ts' else js.language())
        walk_script(Parser(language).parse(m.group(2).encode()),component,add,refs,text[:m.start(2)].count('\n'))
    for m in VUE_TEMPLATE_DOCTYPE.finditer(text): refs.append({'source':component,'name':m.group(1),'type':'uses','line':text[:m.start()].count('\n')+1,'doctype':True})

# Keys through which Frappe metadata records name a DocType, and list keys naming other metadata records.
DOCTYPE_KEYS={'ref_doctype','document_type','doc_type','reference_document','reference_doctype','applies_to_doctype','settings_doctype','parent_document_type'}
METADATA_KEYS={'card':'Number Card','number_card_name':'Number Card','chart':'Dashboard Chart','chart_name':'Dashboard Chart','step':'Onboarding Step','report_name':'Report'}
METADATA_SUMMARY=['module','label','ref_doctype','document_type','doc_type','report_type','is_standard','event','condition','channel','subject','function','chart_type','type','based_on','reference_document','action']
MAX_INSTANCES=200

def frappe_references(record):
    """(kind, name) pairs a Frappe metadata record points at; kind 'DocType' or a metadata kind such as 'Report'."""
    found=[]; inner=set()
    def walk(x,list_item):
        # Card/chart/step names only reference other records as rows of a list (Dashboard cards, Workspace charts);
        # nested dictionaries such as an Insights Workbook's own chart definitions are not references.
        if isinstance(x,dict):
            link=x.get('link_to'); kind=x.get('link_type')
            if isinstance(link,str) and link and isinstance(kind,str) and kind: found.append((kind,link))
            if x is not record and isinstance(x.get('name'),str): inner.add(x['name'])
            for k,v in x.items():
                if isinstance(v,str) and v:
                    if k in DOCTYPE_KEYS: found.append(('DocType',v))
                    elif list_item and k in METADATA_KEYS: found.append((METADATA_KEYS[k],v))
                else: walk(v,False)
        elif isinstance(x,list):
            for v in x: walk(v,True)
    walk(record,False)
    # Names defined inside the record itself (an Insights Workbook's own charts) are internal, not references.
    return list(dict.fromkeys(f for f in found if f[0]=='DocType' or f[1] not in inner))

def frappe_ref(source,kind,name,line):
    if kind=='DocType': return {'source':source,'name':name,'type':'uses','line':line,'doctype':True}
    return {'source':source,'name':f'{kind}: {name}','type':'uses','line':line,'metadata':True}

def parse_frappe_metadata(doc,text,add,nodes,refs):
    """Frappe metadata (Report, Number Card, Notification, Workspace, …): one Metadata entity named '<kind>: <name>',
    linked to the DocTypes and other metadata it names."""
    kind=doc['doctype']; name=doc.get('name') or doc.get('label') or kind
    targets=[[k,n] for k,n in frappe_references(doc) if (k,n)!=(kind,name)]
    properties={'frappe_type':kind,**{k:doc[k] for k in METADATA_SUMMARY if isinstance(doc.get(k),(str,int,float,bool)) and doc.get(k) not in ('',None)},'refs':targets}
    if kind=='Workflow':  # states and transitions are lifecycle and transition rules of the DocType
        properties['states']=[{'state':x['state'],'doc_status':str(x.get('doc_status','0')),'allow_edit':x.get('allow_edit') or ''} for x in doc.get('states',[]) if isinstance(x,dict) and x.get('state')]
        properties['transitions']=[{'state':x['state'],'action':x.get('action') or '','next_state':x['next_state'],'allowed':x.get('allowed') or '','condition':x.get('condition') or ''}
                                   for x in doc.get('transitions',[]) if isinstance(x,dict) and x.get('state') and x.get('next_state')]
        properties['workflow_state_field']=doc.get('workflow_state_field') or 'workflow_state'
    meta=add(f'{kind}: {name}','Metadata',1,text.count('\n')+1,nodes[0]['id'],properties)
    nodes[-1].update(method='frappe-metadata',excerpt='\n'.join([f'{kind}: {name}']+[f'{k}: {v}' for k,v in properties.items() if k not in ('frappe_type','refs')])[:1600])
    for target_kind,target in targets: refs.append(frappe_ref(meta,target_kind,target,1))

def parse_frappe_records(doc,text,add,nodes,refs):
    """A list of Frappe documents (test records, fixtures): each becomes an Instance of its DocType with scalar fields."""
    for i,record in enumerate(doc[:MAX_INSTANCES]):
        label=next((record[k] for k in ('name','title',*record) if k!='doctype' and isinstance(record.get(k),str) and record.get(k)),str(i+1))
        fields={k:v for k,v in record.items() if k!='doctype' and isinstance(v,(str,int,float,bool))}
        inst=add(f"{record['doctype']}: {label}",'Instance',1,parent=nodes[0]['id'],properties={'doctype':record['doctype'],'fields':fields})
        nodes[-1].update(method='frappe-record',excerpt='\n'.join([f"{record['doctype']} record"]+[f'{k}: {v}' for k,v in fields.items()])[:1600])
        refs.append({'source':inst,'name':record['doctype'],'type':'instance_of','line':1,'doctype':True})

def is_frappe_record(x): return isinstance(x,dict) and isinstance(x.get('doctype'),str) and x['doctype'].strip()!=''

LAYOUT_FIELDS={'Section Break','Column Break','Tab Break','HTML','Button','Heading','Fold','Image'}
# Field settings that are business rules: kept only when set.
DOCTYPE_FIELD_RULES=('unique','mandatory_depends_on','read_only_depends_on','depends_on','set_only_once','non_negative','length','read_only','allow_on_submit')

def parse_doctype(doc,path,text,add,nodes,refs):
    """A Frappe DocType definition: one Model with its fields; Link/Table fields reference other DocTypes by name."""
    fields=[{'fieldname':f['fieldname'],'fieldtype':f.get('fieldtype',''),'label':f.get('label') or '','options':f.get('options') or '','reqd':bool(f.get('reqd')),'default':str(f.get('default') or ''),'fetch_from':f.get('fetch_from') or '',
             **{k:f[k] for k in DOCTYPE_FIELD_RULES if f.get(k) not in (None,'',0,False)}}
            for f in doc['fields'] if isinstance(f,dict) and f.get('fieldname') and f.get('fieldtype') not in LAYOUT_FIELDS]
    name=doc.get('name') or Path(path).stem
    properties={'doctype':True,'module':doc.get('module') or '','istable':bool(doc.get('istable')),'issingle':bool(doc.get('issingle')),'is_submittable':bool(doc.get('is_submittable')),'description':doc.get('description') or '','fields':fields,
                'is_tree':bool(doc.get('is_tree')),'autoname':str(doc.get('autoname') or ''),'nsm_parent_field':doc.get('nsm_parent_field') or '',
                'permissions':[{'role':x['role'],'can':[k for k in ('read','write','create','delete','submit','cancel','amend','report','export','print','email','share') if x.get(k)]} for x in doc.get('permissions',[]) if isinstance(x,dict) and x.get('role')]}
    model=add(name,'Model',1,parent=nodes[0]['id'],properties=properties)
    summary=[f"DocType {name} · module {properties['module'] or 'unknown'}{' · child table' if properties['istable'] else ''}"]+[f"{f['fieldname']}: {f['fieldtype']}{' → '+f['options'] if f['fieldtype'] in ('Link','Table','Table MultiSelect') else ''}{' (required)' if f['reqd'] else ''}" for f in fields]
    nodes[-1].update(method='frappe-doctype',excerpt='\n'.join(summary)[:1600])
    line_of=lambda field:text[:max(0,text.find(f'"fieldname": "{field}"'))].count('\n')+1
    for f in fields:
        kind={'Link':'links','Table':'contains','Table MultiSelect':'contains'}.get(f['fieldtype'])
        if kind and f['options']: refs.append({'source':model,'name':f['options'],'type':kind,'line':line_of(f['fieldname']),'field':f['fieldname']})
    # Frappe convention: the controller class is the DocType name without spaces, next to the JSON.
    refs.append({'source':model,'name':name.replace(' ',''),'type':'controller','line':1})

def parse_file(path, text):
    """Return file-local facts; reference targets stay unresolved until graph resolution."""
    ext=Path(path).suffix.lower(); fmt=FORMATS.get(ext,'Unsupported'); nodes=[]; edges=[]; refs=[]
    # Only Maven POMs among XML files, and only Gradle scripts among Kotlin scripts, are modelled.
    if (ext=='.xml' and Path(path).name!='pom.xml') or (ext=='.kts' and not path.endswith('.gradle.kts')): return [],[],[],'unsupported'
    lines=text.splitlines()
    def add(name, kind, line=1, end=None, parent=None, properties=None):
        id=stable(path,kind,name,line)
        n={'id':id,'name':name,'type':kind,'path':path,'line':line,'end_line':end or line,'excerpt':'\n'.join(lines[max(0,line-1):min(len(lines),(end or line)+2)])[:1600], 'method':{'Python':'python-ast','TypeScript':'tree-sitter','JavaScript':'tree-sitter','Vue':'tree-sitter','Java':'tree-sitter','SQL':'sqlglot','XML':'maven-pom','Gradle':'gradle-script','Properties':'properties'}.get(fmt,'structured' if fmt in ['JSON','YAML'] else 'heading-parser'),'properties':properties or {},'status':'extracted','format':fmt}
        nodes.append(n)
        if parent: edges.append({'source':parent,'target':id,'type':'defines','method':n['method'],'status':'resolved','line':line,'path':path})
        return id
    root=add(path,'File')
    if ext=='.py':
        tree=ast.parse(text,filename=path)
        def walk(body,parent):
            for node in body:
                if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                    kind='Class' if isinstance(node,ast.ClassDef) else ('Test' if node.name.startswith('test_') else 'Function')
                    props={}
                    if kind!='Class': props['parameters']=[a.arg for a in node.args.args]; props['returns']=ast.unparse(node.returns) if node.returns else ''
                    if kind=='Function' and (checks:=guards(node)): props['guards']=checks
                    if kind=='Class' and (model:=model_fields(node)): props.update(model)
                    nid=add(node.name,kind,node.lineno,node.end_lineno,parent,props)
                    if isinstance(node,ast.ClassDef):
                        for b in node.bases: refs.append({'source':nid,'name':ast.unparse(b),'type':'inherits','line':node.lineno})
                    else:
                        for child in ast.walk(node):
                            if isinstance(child,ast.Call):
                                refs.append({'source':nid,'name':ast.unparse(child.func),'type':'calls','line':child.lineno})
                                doctype=frappe_doctype(child)
                                if doctype: refs.append({'source':nid,'name':doctype,'type':'calls','line':child.lineno,'doctype':True})
                        for d in node.decorator_list:
                            if isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.func.attr in ['get','post','put','patch','delete'] and d.args and isinstance(d.args[0],ast.Constant):
                                api=add(f'{d.func.attr.upper()} {d.args[0].value}','API',d.lineno,d.end_lineno,root,{'http_method':d.func.attr.upper(),'route':d.args[0].value})
                                edges.append({'source':api,'target':nid,'type':'implements','status':'resolved','method':'python-ast','line':node.lineno,'path':path})
                    walk(node.body,nid)
                elif isinstance(node,ast.Import):
                    for a in node.names: refs.append({'source':parent,'name':a.name,'type':'imports','line':node.lineno,'alias':a.asname})
                elif isinstance(node,ast.ImportFrom):
                    # Relative imports become absolute dotted paths so they can be matched to files.
                    package=Path(path).parent.parts[:len(Path(path).parent.parts)-(node.level-1)] if node.level else ()
                    module='.'.join([*package,*([node.module] if node.module else [])])
                    refs.append({'source':parent,'name':module,'type':'imports','line':node.lineno,'symbols':[[a.name,a.asname] for a in node.names]})
        walk(tree.body,root)
    elif ext in ['.js','.jsx','.ts','.tsx']:
        language=Language(js.language()) if ext in ['.js','.jsx'] else Language(ts.language_tsx() if ext=='.tsx' else ts.language_typescript())
        tree=Parser(language).parse(text.encode())
        if tree.root_node.has_error: raise ValueError('Tree-sitter found syntax errors; file requires review')
        walk_script(tree,root,add,refs)
    elif ext=='.vue': parse_vue(path,text,add,refs,root)
    elif ext=='.java':
        tree=Parser(Language(java.language())).parse(text.encode())
        if tree.root_node.has_error: raise ValueError('Tree-sitter found syntax errors; file requires review')
        walk_java(tree,root,add,refs,edges,path)
    elif ext=='.xml': parse_pom(text,add,nodes,root)
    elif ext in ('.gradle','.kts'): parse_gradle(text,add,nodes,root)
    elif ext=='.properties': add_configuration(path,text,parse_properties(text),add,nodes,root)
    elif ext=='.sql':
        for stmt in sqlglot.parse(text):
            if isinstance(stmt,exp.Create) and isinstance(stmt.this,exp.Schema):
                schema=stmt.this; table=schema.this.name; tid=add(table,'Table',1,parent=root)
                for col in schema.expressions:
                    if isinstance(col,exp.ColumnDef):
                        add(col.name,'Column',1,parent=tid,properties={'datatype':str(col.kind)})
                for fk in stmt.find_all(exp.Reference): refs.append({'source':tid,'name':fk.this.this.name if isinstance(fk.this,exp.Schema) else fk.this.name,'type':'references','line':1})
    elif ext in ['.json','.yaml','.yml']:
        # A YAML stream may hold several '---' documents (Kubernetes manifests, Spring profiles).
        docs=[json.loads(text)] if ext=='.json' else [d for d in yaml.safe_load_all(text) if d is not None]
        doc=docs[0] if len(docs)==1 else docs
        if ext!='.json' and spring_config(path) and all(isinstance(d,dict) for d in docs):
            settings={}
            for d in docs:
                flat=dict(flatten(d)); profile=flat.get('spring.config.activate.on-profile') or flat.get('spring.profiles')
                settings.update({(f'[{profile}] ' if profile else '')+k:v for k,v in flat.items()})
            add_configuration(path,text,settings,add,nodes,root); return nodes,edges,refs,'parsed'
        if ext=='.json' and isinstance(doc,dict) and doc.get('doctype')=='DocType' and isinstance(doc.get('fields'),list):
            parse_doctype(doc,path,text,add,nodes,refs)
            return nodes,edges,refs,'parsed'
        if ext=='.json' and is_frappe_record(doc) and (doc.get('name') or doc.get('label')):
            parse_frappe_metadata(doc,text,add,nodes,refs); return nodes,edges,refs,'parsed'
        if ext=='.json' and isinstance(doc,list) and doc and all(is_frappe_record(x) for x in doc):
            parse_frappe_records(doc,text,add,nodes,refs); return nodes,edges,refs,'parsed'
        if not isinstance(doc,dict) or not ('openapi' in doc or 'swagger' in doc): return nodes,edges,refs,'unsupported'
        for route,verbs in doc.get('paths',{}).items():
            if not isinstance(verbs,dict): continue
            for verb,body in verbs.items():
                if verb.lower() not in ['get','post','patch','put','delete','head','options']: continue
                api=add(f'{verb.upper()} {route}','API',1,parent=root,properties={'route':route,'http_method':verb.upper()})
                for match in re.findall(r'#/components/schemas/([^"\s]+)',json.dumps(body)):
                    refs.append({'source':api,'name':match,'type':'uses','line':1})
        for name,body in doc.get('components',{}).get('schemas',{}).items(): add(name,'Model',1,parent=root,properties={'schema':body})
    elif ext in ['.md','.txt']:
        for i,line in enumerate(lines):
            if line.startswith('#'): add(line.lstrip('# ').strip(),'Documentation',i+1,min(len(lines),i+7),root)
        if len(nodes)==1: add(Path(path).stem,'Documentation',1,min(12,len(lines)),root)
    else: return [],[],[],'unsupported'
    return nodes,edges,refs,'parsed'
