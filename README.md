# MindVault

Student knowledge base: **Semester → Course → Unit → Materials**, study groups, permission-checked
sharing, a Security Engine that scores risky shares, and an append-only audit log.
Modular Django monolith: Python, Django 5.2 LTS, MySQL 8, Tailwind, vanilla JS. No AI, no M-Pesa yet.

## Architecture (dependency direction: accounts <- core <- vault <- sharing)
| App | Owns |
|---|---|
| `accounts` | custom `User` (email login), register/login/logout/profile |
| `core` | `AuditEvent` + `audit.record()`, dashboard, activity page, 403 handler |
| `vault` | Semester, Course, Unit, Material, Tag; upload validation; owner-only views |
| `sharing` | StudyGroup, Membership, Share; `permissions.can_access`; `risk.assess_share`; services |

Why `AuditEvent` is in `core` (a change from the first proposal): `vault` must record access denials but
must not import `sharing`. `core` sits below both, so there are no circular imports.

Key rules
- Every vault URL is owner-only (`vault/access.get_owned`). Non-owner on an existing id -> **403 + audit**; unknown id -> 404.
- Non-owners reach a material only via a `Share` to a group they belong to (`sharing/permissions.py`).
  Group membership alone grants nothing. `view`, `download`, `edit` are independent; `replace_file`, `delete`, `share` are owner-only.
- Security Engine (`sharing/risk.py`) is an application-defined heuristic, not a validated score. Server recomputes on every share write.
- Uploads: extension allowlist + size limit + magic-byte check, random storage names, private `MEDIA_ROOT`
  (no URL serves it), served only as `attachment` + `nosniff` through permission-checked views.
- Allowed: pdf, docx, pptx, txt, md, png, jpg/jpeg. Limit 10 MB (`MAX_UPLOAD_BYTES`). **No malware scanning** is available.

## Setup (Ubuntu 24.04)
```bash
# 1. verify what you already have (install only what is missing)
python3 --version                      # 3.12 expected
mysql --version; systemctl is-active mysql
dpkg -s libmysqlclient-dev pkg-config build-essential python3-dev 2>&1 | grep -E "Status|not installed"
# only if something above is missing:
# sudo apt install libmysqlclient-dev pkg-config build-essential python3-dev

# 2. virtualenv + dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. MySQL database and user
sudo mysql <<'SQL'
CREATE DATABASE mindvault CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'mindvault'@'localhost' IDENTIFIED BY 'CHOOSE-A-PASSWORD';
GRANT ALL PRIVILEGES ON mindvault.* TO 'mindvault'@'localhost';
SQL

# 4. environment
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(50))"   # paste into DJANGO_SECRET_KEY
mkdir -p ~/mindvault_media                                         # set MEDIA_ROOT to this path
# edit .env: DB_PASSWORD, MEDIA_ROOT

# 5. migrate, create admin, run
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```
Tests: `python manage.py test` (needs MySQL privileges to create `test_mindvault`:
`GRANT ALL ON test_mindvault.* TO 'mindvault'@'localhost';`).

Tailwind (standalone CLI, no Node): download the Linux binary from the Tailwind releases page, then
`./tailwindcss -i static/src/input.css -o static/css/output.css --watch` (add `--minify` for production).
Pages work unstyled until `static/css/output.css` exists.

## Verification status (be honest in the demo)
- Verified here: 61 automated tests pass on **SQLite** (`DB_ENGINE=sqlite`, a test-only escape hatch); migrations are current; `check --deploy` clean apart from two intentional HSTS warnings.
- **Not verified here: anything on real MySQL 8.0.46** (the sandbox has no MySQL), manual browser flows, Tailwind build, deployment.
  Run `python manage.py test` on your machine first. Expect it to pass; if a MySQL-specific issue appears, send me the output.
- Do **not** enable `ATOMIC_REQUESTS`: audit events for denied/blocked actions are written before an exception and must not be rolled back.

See `docs/INTEGRATION.md` (frontend contract), `docs/DEPLOYMENT.md`, `docs/GIT_WORKFLOW.md`.
