---
description: A ten-minute guided tour of OntoForge using the bundled Meridian sample project - from parsing code to a business ontology, rules, 3D graphs and impact analysis.
---

# Tutorial: from code to ontology in ten minutes

This tour uses **Meridian**, a small synthetic project bundled in `sample/meridian`: Python services, TypeScript models, a SQL schema, an OpenAPI file, tests and policy documents about customers, payments and refunds. No AI provider is needed.

## 1. Analyse the sample

Start OntoForge (`./scripts/start.sh`) and open <http://127.0.0.1:8017>. On **Overview**, click **Explore sample project**. The real parsers run against the sample (nothing is imported or executed) and the Overview fills with counts and coverage.

Click any coverage count (parsed, excluded, unsupported, failed) to see exactly which files are in it. Every file is accounted for.

## 2. Look at the code graph

Open **Knowledge Graph → Code graph**. The Map shows files, classes, functions, API operations and tables. Press ⌘K (Ctrl+K) and search for `RefundService`. The inspector shows where it is defined, what it calls and what calls it, with links to the exact lines.

## 3. Switch to the domain ontology

Choose **Domain ontology**. Concepts such as *Refund*, *Payment* and *Customer* come from the sample's labelled demonstration proposals, merged with the technical facts. Open **Refund**: the implementation allows 14 days, the policy document says 30, and the recorded conflict stays visible as evidence rather than being silently resolved.

Try **Side by side** to see code and concepts together, then **Structure** and **Diagram** for the hierarchy and a class diagram.

## 4. Change the engine

Use the engine dropdown on **Map**:

- **✦ Explore: interactive 3D**: drag nodes, pin them, press Space to pause, F to focus.
- **3D two floors: code ↔ domain**: concepts above, code below, beams between them.
- **OWL notation (WebVOWL style)**: the ontology the way ontologists draw it.

## 5. Ask what a change would break

Select a concept or a function and choose **Impact of a change**. You get the affected concepts, code, tests, APIs and rules, each with the chain that explains why, highlighted on the map.

## 6. Use the outputs

Open **Outputs**: the rule catalogue, documentation, reasoning results, contracts (JSON Schema and OpenAPI), sample data and the version timeline. Download RDF from **Knowledge Graph → Download RDF…** to open the ontology in Protégé.

## 7. Edit and version

In **Ontology Studio**, change a description and save. The approved version stays in **History**; your edit becomes a separate draft, and both appear on **Outputs → Timeline**.

## 8. Your own code, with AI

Connect a local folder, ZIP or GitHub repository in **Sources**. Configure an AI provider (see [Configuration](configuration.md)), then use **Generate with AI** and the **Chat** button. Frameworks with declared models (Frappe, JPA, Pydantic) give a rich ontology even before any AI runs.
