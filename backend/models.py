from typing import Literal, Any
from pydantic import BaseModel, Field, model_validator

class SourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal['local', 'github', 'sample']
    location: str = ''
    reference: str = 'HEAD'
    subdirectory: str = ''
    exclusions: list[str] = Field(default_factory=list)

class AnalysisInput(BaseModel):
    mode: Literal['parsing', 'sample', 'live'] = 'parsing'
    share_confirmed: bool = False
    # Live mode only: stop model calls once this job has spent the budget (needs prices in AI Usage),
    # and optionally run only some enrichment passes.
    budget_usd: float | None = Field(default=None, ge=0, le=10000)
    passes: list[Literal['define', 'hierarchy', 'relate', 'logic', 'rules', 'lifecycle', 'behaviour']] | None = None

class Property(BaseModel):
    name: str
    datatype: Literal['string', 'number', 'boolean'] = 'string'
    required: bool = False
    minimum: float | None = None
    # Source field type (e.g. a Frappe 'Link') and the concept a reference field points to.
    fieldtype: str = ''
    reference: str | None = None
    options: list[str] = Field(default_factory=list)
    description: str = ''
    unique: bool = False

    @model_validator(mode='after')
    def valid_minimum(self):
        if self.minimum is not None and self.datatype!='number': raise ValueError('minimum requires number datatype')
        return self

class Concept(BaseModel):
    id: str
    name: str = Field(min_length=1)
    description: str = ''
    layer: Literal['technical', 'domain'] = 'technical'
    aliases: list[str] = Field(default_factory=list)
    properties: list[Property] = Field(default_factory=list)
    parent: str | None = None
    evidence: list[str] = Field(default_factory=list)
    # Generative layer: abstract categories, lifecycle, behaviour and per-facet provenance
    # ({facet: {origin, model, tier, confidence, evidence}}), so every statement says where it came from.
    abstract: bool = False
    lifecycle: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    roles: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)

class RelationshipDefinition(BaseModel):
    id: str
    source_types: list[str]
    target_types: list[str]
    label: str = ''
    kind: Literal['declared', 'link', 'contains', 'semantic'] = 'declared'
    description: str = ''
    provenance: dict[str, Any] = Field(default_factory=dict)

# Executable on one field; 'conditional' is required-when, 'unique' a key, 'derived' a value copied or computed from elsewhere.
FIELD_OPERATORS = ('required', 'minimum', 'maximum', 'equals', 'pattern', 'min_length', 'max_length', 'conditional', 'unique', 'derived')
RULE_CATEGORIES = ('validation', 'conditional', 'calculation', 'transition', 'authorisation', 'uniqueness', 'temporal')

class Rule(BaseModel):
    id: str
    operator: Literal['required', 'minimum', 'maximum', 'equals', 'pattern', 'min_length', 'max_length', 'conditional', 'unique', 'derived', 'transition', 'permission', 'described']
    concept: str
    property: str = ''
    value: Any = None
    interpretation: str = 'Candidate implementation constraint, not business policy'
    # Plain-language statement; 'described' rules have only this (not executable).
    text: str = ''
    origin: str = 'declared'
    evidence: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    # For domain experts: what kind of rule, when it applies and how serious a breach is.
    category: Literal['validation', 'conditional', 'calculation', 'transition', 'authorisation', 'uniqueness', 'temporal'] | None = None
    trigger: Literal['always', 'save', 'submit', 'cancel', 'transition'] = 'always'
    severity: Literal['error', 'warning', 'info'] = 'error'
    condition: str = ''

    @model_validator(mode='after')
    def minimum_value(self):
        if self.operator in ('minimum','maximum','min_length','max_length') and (not isinstance(self.value,(int,float)) or isinstance(self.value,bool)): raise ValueError(f'{self.operator} rule requires a numeric value')
        if self.operator=='pattern' and not isinstance(self.value,str): raise ValueError('pattern rule requires a regular expression')
        if self.operator in FIELD_OPERATORS and not self.property: raise ValueError('executable rules need a property')
        if self.operator=='conditional' and not self.condition: raise ValueError('conditional rule requires a condition')
        if self.operator=='transition' and not (isinstance(self.value,dict) and self.value.get('from') and self.value.get('to')): raise ValueError('transition rule requires from and to states')
        if self.operator=='permission' and not (isinstance(self.value,dict) and self.value.get('action') and self.value.get('roles')): raise ValueError('permission rule requires an action and roles')
        return self

def rule_category(rule):
    """The category a rule falls in, from its operator when not stated."""
    if rule.get('category'): return rule['category']
    op=rule['operator']
    return {'conditional':'conditional','derived':'calculation','unique':'uniqueness','transition':'transition','permission':'authorisation'}.get(op,'validation')

class Axiom(BaseModel):
    """A logical statement the reasoner uses (OWL 2 RL subset).
    disjoint: no record is two of `concepts` (pairwise). functional: `property` has at most one value.
    cardinality: `concepts[0]` has between `min` and `max` values of `property`. inverse: `inverse` reads `property` backwards.
    transitive: `property` (often a derived ancestor property over `base`) chains. symmetric: `property` holds both ways.
    chain: `chain` [p1, p2] implies `property`. key: values of `fields` identify a record of `concepts[0]`."""
    id: str
    kind: Literal['disjoint', 'functional', 'cardinality', 'inverse', 'transitive', 'symmetric', 'chain', 'key']
    concepts: list[str] = Field(default_factory=list)
    property: str = ''
    label: str = ''
    base: str = ''
    inverse: str = ''
    chain: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    min: int | None = None
    max: int | None = None
    text: str = ''
    origin: str = 'code'
    evidence: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)

class Ontology(BaseModel):
    id: str
    status: Literal['draft','approved'] = 'draft'
    concepts: list[Concept] = Field(default_factory=list)
    relationships: list[RelationshipDefinition] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    axioms: list[Axiom] = Field(default_factory=list)
    based_on: str | None = None

class EngineInput(BaseModel):
    engine: Literal['lightweight','open-ontologies'] = 'lightweight'
    operation: Literal['definition','instances','infer','impact','diff','rules','demo','checks'] = 'instances'
    entity_id: str | None = None
    previous_version: str | None = None
