---
description: Frequently asked questions about OntoForge - what a code ontology is, how it differs from a knowledge graph, privacy, supported languages, AI models and cost.
---

# FAQ

## What is a business ontology of a codebase?

A structured, machine-readable description of the business domain the software implements: the concepts (Employee, Invoice, Refund), their properties and relationships, the rules that constrain them, their lifecycles, and the code that implements each part. OntoForge builds one from source code and keeps it linked to that code.

## How is that different from a knowledge graph?

The **knowledge graph** records particular facts: this class calls that function, this table has that column. The **ontology** defines types and meaning: what a Refund is, what it must have, how it relates to a Payment. OntoForge builds both and links them. See [Ontology vs knowledge graph](concepts/ontology-vs-knowledge-graph.md).

## Does OntoForge run or modify my code?

No. Code is parsed statically: nothing is imported, executed, built or installed, and repository hooks, submodules and LFS objects are never fetched. Sources are copied into immutable snapshots; your folder is never written to.

## Does my code leave my machine?

Not unless you choose a cloud AI provider and confirm sharing. Parsing, the ontology, rules, the reasoner and all exports run locally. With [Ollama](https://ollama.com) the AI runs locally too. Chat and the domain guide send ontology summaries and code names, never source code. Graphistry uploads happen only after an explicit confirmation.

## Which languages and frameworks are supported?

Python, Java, JavaScript, TypeScript, Vue, SQL DDL, OpenAPI, Maven, Gradle, Spring configuration and Markdown. Frappe (DocTypes, workflows, permissions), JPA and Pydantic give the richest results without AI. See [Supported languages and formats](concepts/supported-formats.md).

## Do I need an AI model?

No. Without AI you get the code graph, framework concepts, rules read from code, reasoning, impact analysis, all graph views and exports. AI adds concept discovery, definitions, hierarchy, extra rules, lifecycles, the domain guide and chat.

## Which local model should I use?

Any chat model Ollama can run with JSON output. 7B models work but are slow on large passes; 14B-class models (for example `qwen2.5-coder:14b`) give better definitions and rules if you have the memory. You can use a cheap model for most passes and a stronger one for rules and the quality judge.

## How much does it cost with a cloud model?

**Generate with AI** shows calls, tokens and estimated cost per pass before it runs, and a budget cap stops calls once reached. Results are cached, so re-runs and unchanged code cost nothing. **AI Usage** meters every call by model, job and day.

## Can I use the ontology in other tools?

Yes: OWL/Turtle and SHACL (Protégé, RDF stores), SKOS glossary, JSON Schema and OpenAPI, GraphML and GEXF (Gephi, yEd, Cytoscape), Neo4j import files, Mermaid, Kineviz GraphXR and Graphistry.

## Can I change the colours?

Yes. The palette button in the header offers six themes: Sand & Indigo (default), Arctic Blue, Obsidian & Copper, Graphite & Violet, Ivory & Ember and Midnight Navy & Electric Blue. Each is checked for readable contrast, and your choice is remembered in the browser.

## Is it ready for production or teams?

It is a local, single-user application with no authentication. Keep it bound to localhost. See [Scale and limitations](reference/scale-and-limitations.md).

## How can I help?

Try it on your codebase and report what is missing, add a parser or framework reader, or improve the docs. See [Contributing](https://github.com/prachh/ontoforge/blob/main/CONTRIBUTING.md).
