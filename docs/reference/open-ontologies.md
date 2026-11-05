---
description: Using Open Ontologies with OntoForge - RDF/OWL export mapping, SHACL validation, RDFS inference, SPARQL queries and model checks.
---

# Open Ontologies integration

## Status

Open Ontologies is an optional engine. It is enabled when a pinned v2.1.0 executable and its SHA-256 are configured. It is not a core dependency, and the built-in `LightweightOntologyEngine` keeps working without it.

- Repository: https://github.com/fabio-rovai/open-ontologies
- Release: [v2.1.0](https://github.com/fabio-rovai/open-ontologies/releases/tag/v2.1.0)
- Accepted executables: only the four platform builds whose digests appear in that release's `SHASUMS.txt`, which are also pinned in `backend/external.py`.
- Tested so far: the Apple Silicon build, against the [Frappe HRMS](https://github.com/frappe/hrms) repository.

## Enable

```sh
scripts/setup_open_ontologies.sh --write-env   # downloads to data/tools, verifies, writes .env
./scripts/start.sh
```

The script:
1. Picks the release asset for this OS and CPU.
2. Downloads it together with `SHASUMS.txt`.
3. Refuses to install unless both the published digest and the downloaded file match the digest pinned in the script.
4. With `--write-env`, sets these in `.env`:
   - `ONTO_OPEN_ONTOLOGIES_BIN`
   - `ONTO_OPEN_ONTOLOGIES_SHA256`

The application never downloads binaries at runtime. The engine reports **Unavailable** when the binary is missing, when its digest doesn't match, or when it is not a published v2.1.0 build.

Optional limits:
- `ONTO_OPEN_ONTOLOGIES_TIMEOUT_SECONDS` (default 90)
- `ONTO_OPEN_ONTOLOGIES_OUTPUT_BYTES` (default 32 MiB)

## How the model is exported

`backend/semantic.py` derives deterministic Turtle from the canonical JSON ontology and the knowledge graph. The canonical model stays the source of truth.

| Canonical | RDF/OWL |
|---|---|
| Domain concept | `owl:Class` with label, comment and `skos:altLabel` aliases, a subclass of `oe:DomainConcept` |
| DocType flags | The concept becomes a subclass of `oe:SubmittableDocument`, `oe:ChildTable`, `oe:SettingsDocument` or `oe:StandardDocument`, all under `oe:Document` |
| DocType referenced here but defined elsewhere | A subclass of `oe:ExternalDocument` |
| DocType module | Module class `module:<name>`, a subclass of `oe:Document` |
| Link / Table field | `owl:ObjectProperty` (`oe:LinkField` / `oe:TableField`) with `rdfs:domain` and `rdfs:range`, plus `oe:linkTarget` |
| Scalar field | `owl:DatatypeProperty` whose XSD range comes from the Frappe fieldtype |
| Test record or fixture | Individual `record:<DocType>/<name>` typed as its concept; Link values point at the referenced record's IRI |
| Code entity, metadata record, edge | `entity:<id>` with kind, path, line and scalar properties; edges become `relationship:<type>` |
| Concept evidence | `entity oe:evidenceOf concept` (evidence, not `rdf:type`: a controller class is not a leave application) |
| Metadata references | `oe:refersTo [ oe:refKind ; oe:refName ]` |

`rdfs:domain` and `rdfs:range` are emitted for inference only. Constraints are expressed separately as SHACL:
- required fields without a default or `fetch_from` get `sh:minCount 1`
- fields get `sh:datatype`
- Select options become `sh:in`
- Link fields get `sh:nodeKind sh:IRI`
- ontology rules map to `required` / `sh:minInclusive` / `sh:hasValue`

Each operation loads only the scope it needs. Sizes for [Frappe HRMS](https://github.com/frappe/hrms):

| Scope | Contents | HRMS size |
|---|---|---|
| model | Classes, properties and hierarchy | 0.6 MB |
| data | model + records | 0.6 MB |
| full | data + code graph and evidence | 4.6 MB, about 58k triples |

The previous mapping sent the full graph with source excerpts: 47 MB and 247k triples.

## Operations

| Operation | Scope | What runs | HRMS result |
|---|---|---|---|
| `checks` | full | SPARQL model checks (below), `lint`, `defects` | 0 errors, 0 warnings, 53 info, in about 0.5 s |
| `instances` | data | SHACL over the records | 18 records checked, 11 warnings |
| `infer` | data | RDFS reasoning; reports facts that were not asserted | 257 derived facts in about 0.2 s |
| `definition` | model | `validate`, `lint`, `defects` | 14 info (technical relationships have no single domain) |
| `query` | full, optionally after RDFS reasoning | Read-only SPARQL | Interactive |

On the HRMS records, all 11 SHACL warnings are fixture files that omit the required field `salary_component_abbr`. A controller may still fill that field when the record is saved, so missing required values are warnings, not errors.

Model checks:
- **Notification event not submittable** (error): a notification fires on Submit or Cancel, but its DocType is not submittable, so it can never trigger.
- **Missing metadata reference** (warning): a dashboard, number card, chart or onboarding step refers to a record that is not defined here. Records defined inside the same file, such as an Insights Workbook's own charts, are excluded.
- **Navigation outside the repository** (info): workspace or sidebar links to records of other apps, for example ERPNext reports.
- **Metadata on an unknown DocType** (info) and **Link fields to undefined DocTypes** (info): usually Frappe or ERPNext core.
- **Child table no DocType JSON uses** (info): HRMS attaches such tables through custom fields created in `setup.py`, which DocType JSON does not show.
- **Duplicate concept label** (warning).

The UI shows the results in three places:
- **Knowledge Graph → Domain ontology → Coverage** (*Model checks*) and in the concept inspector (*Quality findings*).
- The **SPARQL** tab, with example queries: impact of changing a DocType, submittable documents by module, inferred record types, screens calling backend functions, and others.
- **Download RDF…**, which exports `model`, `data`, `full` or SHACL `shapes` as Turtle for Protégé or other RDF tools. The export also works when the engine is unavailable.

## Safety

- Commands are fixed argument arrays.
- Every call gets a fresh temporary in-memory store and a credential-free environment.
- Time and output are bounded.
- User SPARQL is restricted to SELECT, ASK, CONSTRUCT and DESCRIBE. Update keywords and federated `SERVICE` are refused before the engine runs.
- Ad-hoc queries are not stored. Check, validation and inference runs are recorded in engine-run history with shortened raw output.

## Limits

- Results are deterministic engine output. No derivation certificates are generated or checked.
- Nothing here establishes business truth.
- Controller validation logic, workflows and notification conditions are not translated.
- Child-table rows inside records are not extracted.
- Only about 18 HRMS records exist, all fixtures, so SHACL validation covers sample data only. Loading records from a running site is outside the current scope.
- Only the Apple Silicon build has been tested. The other platform digests are pinned from `SHASUMS.txt` but not exercised. Reports from other platforms are welcome.

## Original experiment

The first integration (commit `c7fd75f` of the upstream repository) exercised `load`, `reason --profile rdfs`, `query` and `shacl` through the JSON batch interface, using an isolated illustrative graph. That demonstration is still available as the `demo` operation.
