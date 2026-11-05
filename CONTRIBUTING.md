# Contributing to OntoForge

Thanks for your interest in OntoForge! Bug reports, ideas, docs, parsers, framework readers and graph views are all welcome.

## Ways to help

- **Try it on your codebase** and open an issue describing what was missed or wrong (concepts, rules, relationships). A link to a public repository helps a lot.
- **Add a language or framework**: parsers live in `backend/parsers.py`; rules read from code in `backend/logic.py`. Django ORM, .NET, Go and Rails are good next candidates.
- **Improve the UI or graph views** in `web/src/`.
- **Improve the docs** in `docs/` (published at <https://prachh.github.io/ontoforge/>).

Check [open issues](https://github.com/prachh/ontoforge/issues) first, and for larger changes open an issue to discuss the approach before writing code.

## Development setup

Requirements: Python 3.13+, Node.js 22+ and npm.

```sh
git clone https://github.com/prachh/ontoforge.git
cd ontoforge
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
npm ci --prefix web

# two terminals
.venv/bin/python -m uvicorn backend.main:app --port 8017 --reload
npm run dev --prefix web
```

`docs/reference/architecture.md` explains how the backend and frontend fit together.

## Before you open a pull request

```sh
.venv/bin/python -m pytest -q        # backend tests must pass
npm run build --prefix web           # TypeScript check and production build
```

- Add or update tests in `tests/` for backend behaviour.
- For UI changes, include a screenshot or short recording in the pull request.
- Update the docs when behaviour or configuration changes.
- Keep changes focused; one topic per pull request.
- Match the style of the surrounding code.

To preview the documentation site: `pip install -r requirements-docs.txt && mkdocs serve`.

## Ground rules

These keep OntoForge safe to point at any repository:

- **Never execute analysed code.** Parsers must be static: no imports, `eval`, builds, package installs or repository hooks.
- **Treat parser and model output as untrusted.** Validate model output with Pydantic and check claims before applying them.
- **Do not send source code to AI providers** beyond the documented, user-confirmed excerpts. Chat and the domain guide send ontology data and code names only.
- **No secrets in the repository**, tests or fixtures. Use `.env` (ignored by git).
- **No new outbound network calls** without a user action and documentation.

## Commit messages and sign-off

Use clear, imperative commit messages ("Add Django model reader"). By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).

## Code of Conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md). By participating you agree to uphold it.
