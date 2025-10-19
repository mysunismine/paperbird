# Repository Guidelines

## Project Structure & Module Organization
- `src/` hosts all Django apps; `paperbird/` provides project settings, while `accounts/`, `projects/`, and `stories/` sit next to shared utilities in `core/`.
- Templates and static assets live in `src/templates/` and `src/static/`; infrastructure files (Docker, deployment notes) sit under `infra/`.
- Domain, architecture, and operations guidance is collated in `docs/`; review `docs/28_security_and_config.md` and `docs/42_testing_plan.md` before touching sensitive flows.
- Copy `.env.example` to `.env` for local secrets without committing them.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` — create the Python 3.13 virtual environment shared across tasks.
- `pip install -r requirements.txt` — install Django, Telethon, Ruff, and database drivers.
- `python manage.py migrate` — apply database schema changes before running the app or tests.
- `python manage.py runserver` — serve the site at http://127.0.0.1:8000/.
- `cd infra && docker compose up -d` — start the optional PostgreSQL container with defaults aligned to `.env.example`.

## Coding Style & Naming Conventions
- Follow Ruff settings (`pyproject.toml`): 100-character lines, LF endings, 4-space indents, and double-quoted strings.
- Keep imports ordered standard → third-party → first-party (`core`, `paperbird`); run `ruff check .` (or `--fix`) before opening a PR.
- Django app and module names stay in lowercase snake_case; class-based constructs (models, forms, tests) use PascalCase.

## Testing Guidelines
- Run `python manage.py test` for the canonical test suite; mock external APIs per the scenarios in `docs/42_testing_plan.md`.
- Store tests alongside their apps (`src/accounts/tests.py`, etc.) and describe behaviors with `Test*` classes and verb-based method names.
- Update the testing plan when coverage extends beyond authentication, projects, or Telethon ingestion.

## Commit & Pull Request Guidelines
- Write imperative commit subjects (≤72 chars) with optional bodies outlining intent, context, and follow-up tasks.
- Group related changes; separate schema, management command, or dependency updates when practical.
- PRs must summarize behaviour changes, list verification steps (`python manage.py test`, `ruff check .`), and attach screenshots for template tweaks.
- Flag migrations, secrets, or infra impacts in the PR description so reviewers can coordinate deploy steps.

## Environment & Security Notes
- Keep `.env` values out of version control; document new required keys in `.env.example` and `docs/28_security_and_config.md`.
- Prefer database credentials managed through Docker compose or local secrets stores; rotate tokens used for Telethon integrations.
