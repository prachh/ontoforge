---
description: Ontology engines in OntoForge - the built-in engine and the optional Open Ontologies adapter for RDF/OWL checks, SHACL and RDFS inference.
---

# Ontology engines

`OntologyEngine` is a typed Python Protocol with capability reporting and a common operation/result contract. `LightweightOntologyEngine` is independent of external runtimes. Operations are `definition`, `instances`, `rules`, `infer`, `impact`, `diff`; the API provides an isolated `demo` orchestration.

Definition checks validate Pydantic fields, unique concept identifiers/names, parent references, subclass cycles and relationship type references. Instance validation checks known concept types, graph references, allowed relationship kinds/endpoints, required properties, scalar datatypes and optional numeric minima. `rules` applies explicit required, minimum and equals predicates. Inference follows explicit parent chains with visited-cycle guards. Impact traverses outgoing graph dependencies and is labelled **potential** impact, never guaranteed runtime consequences. Version differences list added, changed and removed concept definitions; relationship/rule diffs are not yet included.

Every recorded result names engine/version, ontology version, graph snapshot, operation, findings and evidence, derived facts, unsupported constructs, execution status, classification, duration and certificate status. `success` with no findings differs from `findings`, `unsupported`, `failure` and `unavailable`. Results here are deterministic, not externally certified. No arbitrary expressions execute.

The external adapter only accepts a configured executable whose SHA-256 matches both the configuration and a published v2.1.0 release digest. Its operations are `checks`, `instances`, `infer`, `definition`, `query` and `demo`. `backend/semantic.py` exports the ontology as RDF/OWL plus SHACL in model, data and full scopes:

- DocTypes become a class hierarchy.
- Fields become object or datatype properties.
- Records become typed individuals.
- Required, datatype and Select constraints, and the ontology rules, become SHACL.

Workflow, temporal and controller logic remain outside the export. The adapter uses argument arrays, configurable timeouts (`ONTO_OPEN_ONTOLOGIES_TIMEOUT_SECONDS`, default 90 s) and a configurable output budget (`ONTO_OPEN_ONTOLOGIES_OUTPUT_BYTES`, default 32 MiB). It runs with isolated in-memory stores and a credential-free environment. A selected unavailable engine returns Unavailable, and the built-in engine is never substituted. See [Open Ontologies](open-ontologies.md).

Validation requests currently load the full selected graph in memory. Unsupported constructs are reported beside the result, even if the supported subset succeeds. Do not interpret success on a subset as whole-model compatibility. See [Open Ontologies](open-ontologies.md).
