---
description: The canonical OntoForge ontology model - concepts, properties, relationships, business rules, axioms, lifecycles, provenance and versions.
---

# Canonical ontology model

The Pydantic `Ontology` model in `backend/models.py` is the single source of truth. RDF/OWL, SHACL, JSON Schema, OpenAPI and the documentation are all generated from it.

## Contents

| Part | Fields |
|---|---|
| **Ontology** | `id`, `status` (draft or approved), concepts, relationships, rules, axioms, optional `based_on` version |
| **Concept** | `id`, `name`, `description`, `layer` (technical or domain), aliases, typed properties, `parent` (is-a), evidence (code entity IDs), `abstract`, `lifecycle` (states and transitions), `events`, `roles`, per-facet `provenance` |
| **Property** | `name`, `datatype` (string, number, boolean), `required`, `minimum`, framework field type, reference target, options, `unique`, description |
| **Relationship** | `id`, source and target concept IDs, label, kind (`declared`, `link`, `contains`, `semantic`), description, provenance |
| **Rule** | operator (`required`, `minimum`, `maximum`, `equals`, `pattern`, `min_length`, `max_length`, `conditional`, `unique`, `derived`, `transition`, `permission`, `described`), concept, property, value, plain-language text, category, trigger, severity, condition, origin, evidence, provenance |
| **Axiom** | kind (`disjoint`, `functional`, `cardinality`, `inverse`, `transitive`, `symmetric`, `chain`, `key`) with its concepts and properties |

Every generated statement carries **provenance**: where it came from (code, framework metadata or a model), which model and tier, the confidence and the evidence. Rules are validated when created: numeric operators need numbers, patterns must compile, transitions need `from` and `to` states, permissions need an action and roles.

## Where concepts come from

1. **Framework metadata (no model):** Frappe DocTypes become domain concepts with their real fields; Link and Table fields become `link` and `contains` relationships; DocTypes referenced at least three times but defined elsewhere become concepts too. JPA entities give fields, relations, inheritance and Bean Validation rules.
2. **Rules read from code (no model):** see [Generative ontology → Rules for domain experts](generative-ontology.md#rules-for-domain-experts).
3. **AI discovery (optional):** classes, tables and documentation not explained by a framework are sent in batches; proposed concepts are merged into existing ones with the same normalised name.

There is no manual review queue. Proposals are accepted automatically, keep their evidence mappings and an `auto-approved` status, and any recorded conflict stays visible. Domain experts confirm or dispute rules afterwards in **Outputs → Rules**, and a disputed rule is excluded from contracts, sample data, SHACL and the reasoner.

## Versions

Approving a version validates it and makes it immutable. Edits in **Ontology Studio** create a separate draft with `based_on` set. Re-analysis starts a new draft from the previous model, and every version stays in History and on the **Outputs → Timeline**. Mappings keep their original evidence snapshot; when a supporting file changes or disappears the mapping is marked stale.

## Limits

This is an application ontology with an OWL 2 RL subset, not full OWL DL: no equivalence classes, unions or existential reasoning. Required-when conditions are stored in plain language but not executed. Rules describe what the software enforces, not authoritative business policy.
