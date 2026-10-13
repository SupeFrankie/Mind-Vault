# Git workflow for two developers

Branches: `main` (always runs), `backend/<topic>`, `frontend/<topic>`. Small PRs, merge to `main` daily, pull before starting.

| Backend owns | Frontend owns | Coordinate first |
|---|---|---|
| `apps/**/models.py, services.py, permissions.py, risk.py, forms.py, views.py, validators.py, tests/`, `config/`, `requirements.txt`, `docs/`, migrations | `templates/**` (except `403/404`), `static/src/`, `static/js/`, `static/css/` | `templates/base.html`, `apps/*/urls.py`, form field names, context variable names, `docs/INTEGRATION.md` |

Rules
- Frontend never edits Python; request a change via an issue naming the template + the context variable you need.
- Backend never renames a form field, URL name or context variable without updating `docs/INTEGRATION.md` in the same commit and telling her.
- **Migrations:** only the backend dev runs `makemigrations`. Never edit or delete a migration that is on `main`.
  If two branches add migrations to the same app, rebase one, delete its migration, regenerate, and re-run tests.
- Conflicts: settings/urls -> backend resolves; templates -> frontend resolves; `base.html` -> resolve together on a call.
- Never commit `.env`, `media/`, `db.sqlite3`, `.venv/`, `static/css/output.css` (build it).
- Before merging to `main`: `python manage.py test` must pass.
