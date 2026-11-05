---
description: How OntoForge uses LLMs to generate a business ontology from code - definitions, hierarchy, rules, lifecycles and events - with cost controls, claim checking, an AI judge and an OWL 2 RL reasoner.
---

# Generative ontology

## What it means here

The ontology is built in two layers:

1. **Deterministic layer (free).** Frameworks declare their business model in code, and the app reads it without any model:
   - **Frappe DocTypes:** fields, Link/Table relationships, Select options, permissions (which become roles).
   - **JPA entities:** fields, relations, `extends` (which becomes the is-a hierarchy), Bean Validation (which becomes rules), enums, Javadoc (which becomes definitions).
2. **Generative layer (paid).** Language models add only what the code implies but doesn't state, in six passes over the deterministic ontology.

Every generated statement is a **claim**. It carries its pass, model, cheap/strong tier, confidence and evidence, and it is **checked before it is applied**:
- names must exist
- cited code must have been in the prompt
- the hierarchy must have no cycles and must not override a parent declared in code
- rule operators and values must be valid

Approval stays fully automatic. A strong-model **judge** reviews a sample of claims against the exact code they cite, and reverts the ones it finds wrong. This is "AI decides" in place of a hand-made reference set.

## Passes

| Pass | Tier | Runs only for | Adds |
|---|---|---|---|
| Concept discovery | cheap | Classes, tables and docs not explained by a framework, and not tests, plumbing or tooling docs | New concepts |
| Definitions | cheap | Concepts with no description or a weak one ("Simple JavaBean…", "Frappe DocType in…") | Business definition and synonyms |
| Hierarchy | cheap | All concepts, about 80 per call | Is-a parents and abstract categories |
| Relationship meaning | cheap | Concepts with links | Verb phrase, description and cardinality; extra semantic links |
| Logic | cheap | Categories and related concepts (all concepts in models of 80 or fewer) | Disjoint groups, inverse and symmetric relationships, property chains |
| Rules | strong | Concepts whose code contains validation (`validate*`, `rejectValue`, `frappe.throw`, `throw`…) | Business rules, executable where possible, otherwise described |
| Lifecycles | cheap | Concepts with status or state fields | States, initial state, transitions |
| Events | cheap | Concepts with hook or event code (`on_submit`, `publishEvent`…) | Events and their effects |

## Cost controls

- **Framework extraction first.** On petclinic, the 7 entities, hierarchy, 4 relationships and 15 rules cost nothing. On ERPNext, discovery drops from 2,536 entities to 472.
- **Selective passes:** each pass runs only where its evidence exists (table above).
- **Compact context:** each concept is sent as a card with fields, links, and code excerpts only for the passes that read code. Whole files are never sent.
- **Cheap/strong split:** only the rules pass and the judge use the strong model. Set both tiers in *Settings → AI provider*.
- **Prompt caching** on Bedrock (a `cachePoint` after the instructions); OpenAI caches automatically. Cached tokens are metered and priced separately.
- **Result cache:** prompts are built from the code-derived base view only, so identical code gives an identical prompt. Re-runs, and unchanged concepts on later commits, cost **$0**.
- **Plan before running:** *Generate with AI* shows calls, tokens and estimated cost for each pass.
- **Budget cap:** model calls stop once the job has spent the cap. This needs prices set in *AI Usage*.

## Reference run: spring-petclinic on Bedrock

| | Before (concept discovery only, Opus) | Generative ontology (Haiku 5.5 + Opus 5.5) |
|---|---|---|
| Model calls | 11 (all Opus) | 4 Haiku + 3 Opus (2 rules, 1 judge) |
| Tokens | 55k in + 10k out on Opus | Haiku 1.9k in + 1.6k out; Opus 5.8k in (+1.2k cached) + 2.0k out |
| Result | 7 concept names | 7 concepts plus definitions, synonyms, categories, link meanings, 15 code rules + 9 AI rules, quality score 0.81 |
| Second run | — | 0 generation calls (cache) |

The estimate for ERPNext with the same tiers: about **0.23M in + 0.07M out on Opus** (the rules pass) and **0.47M in + 0.14M out on Haiku**. The old discovery-only approach would have used about 1.7M in + 0.6M out on Opus. Running the rules pass on the cheap tier lowers this further.

## Rules for domain experts

Rules are read from code at no model cost, each with a **category** (validation, conditional, calculation, transition, authorisation, uniqueness, temporal), a **trigger** (save, submit, cancel, transition, always), a severity, and the file and line it came from:

| Source | Rules |
|---|---|
| DocType fields | required; required-when (`mandatory_depends_on`, in plain language: "Tax Id is required when Customer Type is Company"); unique and `autoname: field:`; non-negative; maximum length; copied from another record (`fetch_from`); cannot change after first save (`set_only_once`); still editable after submit (`allow_on_submit`) |
| Submittable DocTypes | a submitted document cannot be edited, only cancelled and amended; Draft → Submitted → Cancelled lifecycle |
| Permissions | who may create, edit, submit, cancel, delete and amend each DocType |
| Workflows | each allowed transition with its action, roles and condition; the workflow states become the lifecycle (the AI lifecycle pass never overrides them) |
| JPA | Bean Validation (as before) and unique columns |
| Checks in Python code | every `if <condition>: raise SomeError("message")` and `frappe.throw(...)` in a concept's class (or companion class such as `OrderService`, or the module holding it): the message users see plus the condition in plain language ("Rejected when Amount is less than 0"). Frappe hooks set the trigger (`validate` → save, `on_submit` → submit). Type checks, timeouts, retries and internal errors (`TypeError`, `NotImplementedError`…) are skipped; tests, patches and migrations are ignored |
| Pydantic models and dataclasses | required fields, `ge`/`le`/`gt`/`lt`, `min_length`/`max_length`, `pattern`, and `Literal[...]` choices |
| AI rules pass | rules found by the strong model in validation code |

On HRMS this gives about 1,690 rules (about 300 of them checks in Python code) and 53 lifecycles without a model. On a plain Python project the code checks and model fields attach to concepts the AI created, so they appear after **Generate with AI** (or **Re-read from code** on a new snapshot).

**Outputs → Rules** lists them with filters, Markdown and CSV (Excel) downloads, and **expert review**: Confirm, Dispute or Needs clarification, with a comment. Reviews never delete anything and never block generation. A disputed rule is left out of contracts, sample data, SHACL, engine checks and the reasoner until it is reset. Rules are also part of the ontology itself: the OWL export contains each one as an `oe:BusinessRule` individual linked to its concept, field, category, trigger and origin, so they can be queried with SPARQL.

## Logic and the reasoner

**Axioms** (an OWL 2 RL subset):
- **disjoint classes:** every Frappe record has exactly one DocType; JPA entities that neither extends are distinct; the AI logic pass adds groups such as Party vs Transaction
- **single-valued properties:** Link and many-to-one fields
- **cardinalities:** a required link means exactly one; a required table means at least one row
- **inverse properties:** child-table rows belong to their parent; JPA one-to-many with the many-to-one link back
- **transitive properties:** tree DocTypes (`parent_*` links give "is within")
- **symmetric properties** and **property chains** (from the AI logic pass)
- **keys:** unique fields

Most are computed from field definitions; disjointness and AI axioms are stored on the ontology version. The OWL export contains `owl:AllDisjointClasses`, `owl:FunctionalProperty`, cardinality restrictions, `owl:inverseOf`, `owl:TransitiveProperty`, `owl:SymmetricProperty`, `owl:propertyChainAxiom` and `owl:hasKey`.

The **reasoner** (`backend/logic.py`, no extra dependencies) runs over the ontology and the extracted records:
- **Classifies:** implied is-a relationships and the inherited types of records.
- **Finds contradictions:**
  - classes that can have no members (under two disjoint classes)
  - rules no record can satisfy (minimum above maximum, an `equals` value outside the options, invalid patterns)
  - rules on fields that do not exist
  - transitions to states the lifecycle does not have
  - duplicate keys and missing required links in records
  - cycles in trees
- **Derives facts:** inverse links, ancestors in trees, chained and symmetric links.
- **Explains** every result with the facts it used (for example "Account Cash → Account Bank → Account Assets").

It also **checks AI claims before they are applied**. A hierarchy claim that would put a concept under a class it is disjoint with (for example one DocType under another) is rejected with the explanation, and so is a disjointness claim that contradicts the hierarchy. **Outputs → Reasoning** shows the axioms, contradictions, implied hierarchy and derived facts.

## Domain Expertise Knowledge Set

**Outputs → Domain guide** writes a guide for newcomers and domain experts:
- a domain overview, main areas, end-to-end processes and what every newcomer must know
- per module: an overview, glossary (with "not to be confused with"), processes as steps naming concepts, states and roles, roles and responsibilities, key measures, edge cases, FAQ and a learning path

It is generated from the ontology only (names, definitions, fields, links, rules, roles, lifecycles, events, reports); no source code is sent.

How it is made:
- **Calls:** one call per module (modules with fewer than 3 concepts are grouped), plus one overview call.
- **Model:** strong or cheap tier, your choice; the cost is shown before you start.
- **Cache:** results are cached, so writing it again costs nothing.

Grounding:
- Every statement about the system must cite concepts or rules from its module card, or it is dropped.
- Process steps keep only the states the ontology knows.
- Statements from the model's general industry knowledge are labelled *General knowledge*, or left out if you untick the option.

Downloads: Markdown, JSON (for agents and retrieval) and the glossary as SKOS. **Ask** uses the guide's module overviews and glossary as extra grounding.

## Outputs (no model cost except Q&A and the domain guide)

- **Outputs → Documentation:** a data dictionary by module, downloadable as Markdown.
- **Ask:** grounded Q&A over the ontology cards, with the concepts cited.
- **Sample data:** deterministic records that satisfy the field types, options and executable rules, then checked against them.
- **Contracts:** JSON Schema and OpenAPI 3.1 components (types, enums, references, `allOf` inheritance, patterns and lengths).
- **Changes:** what changed in the business model compared with the previous ontology version.
- **Quality:** the judge's score, verdicts and reverted claims, plus every claim with its status and reason.

## Limits

- Generated statements are model interpretations of code. They are evidence-backed and checked, but they are not business policy.
- The reasoner covers the OWL 2 RL subset listed above, not full OWL DL: no equivalence classes, unions or existential reasoning. Required-when conditions are shown in plain language but not executed.
- Rules read from code describe what the software enforces. Domain experts confirm or dispute them in Outputs → Rules.
- New rule settings (`mandatory_depends_on`, `unique`, workflows…) are read by parser version 16 and later, so existing snapshots need a re-analysis to get them; *Re-read from code* on the Rules tab rebuilds rules from the current snapshot without a model.
- The judge is a model too. It reviews only a sample (`ONTO_JUDGE_SAMPLE`, default 8; set 0 to disable).
- Django ORM models are not read deterministically yet, so Django projects (for example InvenTree) still rely on discovery for their concepts.
- Estimates assume about 3.6 characters per token; real usage is metered per call in *AI Usage*.
