---
description: A ten-minute guided tour of OntoForge using the bundled Meridian sample project - from parsing code to a business ontology, rules, 3D graphs and impact analysis.
---

# Tutorial: from code to ontology in ten minutes

This tour uses **Meridian**, a small synthetic project bundled in `sample/meridian`: Python services, TypeScript models, a SQL schema, an OpenAPI file, tests and policy documents about customers, payments and refunds. No AI provider is needed.

## 1. Analyse the sample

Start OntoForge (`./scripts/start.sh`) and open <http://127.0.0.1:8017>. On **Overview**, click **Explore sample project**. The real parsers run against the sample (nothing is imported or executed) and the Overview fills with counts and coverage.

Click any coverage count (parsed, excluded, unsupported, failed) to see exactly which files are in it. Every file is accounted for.

The narrow bar on the left is the navigation rail; next to it, the explorer panel shows the selected source, its analysis status and the domain tree. Switch sources from the panel or from the source name in the breadcrumb at the top.

## 2. Look at the code graph

Open **Graph** in the rail and choose **Code** in the toolbar. The 2D map shows files, classes, functions, API operations and tables. Press ⌘K (Ctrl+K) and search for `RefundService`. The inspector on the right shows it in three tabs: **Overview**, **Relationships** (what it calls and what calls it) and **Evidence** (the source excerpt and exact location).

## 3. Switch to the domain ontology

Choose **Domain**. Concepts such as *Refund*, *Payment* and *Customer* come from the sample's labelled demonstration proposals, merged with the technical facts. Open **Refund**: the implementation allows 14 days, the policy document says 30, and the recorded conflict stays visible as evidence rather than being silently resolved.

Click a concept in the domain tree of the explorer panel to select and centre it. On the **Relationships** tab every link says whether it is **Declared**, **Inferred** or **AI**-generated; click a row to go to that concept.

Try the **Compare** tab to see code and concepts side by side, then the **Ontology** tab for **Structure** and **Diagram** (the hierarchy and a class diagram).

## 4. Change the engine

Use the **View** selector in the toolbar:

- **✦ Explore: interactive 3D**: drag nodes, pin them, press Space to pause, F to focus.
- **3D two floors: code ↔ domain**: concepts above, code below, beams between them.
- **OWL notation (WebVOWL style)**: the ontology the way ontologists draw it.

## 5. Ask what a change would break

Select a concept or a function and choose **Impact of a change** (the lightning button in the inspector). You get the affected concepts, code, tests, APIs and rules, each with the chain that explains why, highlighted on the map.

## 6. Use the outputs

Open **Outputs**: the rule catalogue, documentation, reasoning results, contracts (JSON Schema and OpenAPI), sample data and the version timeline. Use **Export → OWL ontology (Turtle)** in the header to open the ontology in Protégé, or export GraphML, Neo4j or Mermaid.

## 7. Edit and version

In **Ontology** (Ontology Studio), change a description and save. The approved version stays in **History**; your edit becomes a separate draft, and both appear on **Outputs → Timeline**.

## 8. Pick a colour theme

The palette button in the header switches between six colour themes (Sand & Indigo is the default). Nothing is reloaded: your selection, filters and camera stay where they are.

## 9. Your own code, with AI

Connect a local folder, ZIP or GitHub repository in **Sources** (or with **+** next to the source selector). Configure an AI provider (see [Configuration](configuration.md)), then use **Generate with AI** and the **Chat** button. Frameworks with declared models (Frappe, JPA, Pydantic) give a rich ontology even before any AI runs.
