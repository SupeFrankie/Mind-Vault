# MindVault

A student knowledge base: **Semester → Course → Unit → Materials**, study groups, permission-checked
sharing, a Security Engine that scores risky shares, and an append-only audit log.

Modular Django monolith: Python 3.12, **Django 5.2 LTS**, **MySQL 8.0**, Django templates, Tailwind CSS 4,
vanilla JS. No AI and no M-Pesa yet (deliberately deferred).

Team SOLVEX: backend (models, permissions, Security Engine, audit, uploads, tests) and frontend
(templates, CSS, interactions). Repo: `github.com/SupeFrankie/Mind-Vault`.

## Current MVP functionality

Registration and login; semesters, courses, units; materials with validated file uploads, notes, tags and
research metadata; library search and filters; study groups with join codes; sharing with view/edit and
download permissions; Security Engine risk assessment; audit log and activity page; public Privacy Policy
and Cookie Policy. The full requirement list with status labels is in [`docs/PRD.md`](docs/PRD.md).

## Authentication

Email-and-password sign-in on Django's authentication with a custom user model. **Google, Apple and
school-email sign-in are Coming Soon**: their buttons open an informational page and nothing else. No OAuth
credentials, tokens or university servers are involved. Not implemented: email verification, password reset,
two-factor authentication, login rate limiting.

## Legal pages

- Privacy Policy: `/privacy/` (`templates/core/privacy.html`)
- Cookie Policy: `/cookies/` (`templates/core/cookies.html`)

Both contain bracketed placeholders (controller, contact email, hosting provider, retention period, effective
date) that the team must complete, and legal review, before any public release.

## Architecture

Dependency direction: `accounts` ← `core` ← `vault` ← `sharing` (no circular imports).

| App | Owns |
|---|---|
| `accounts` | custom `User` (email login, `full_name`, `university`), register / login / logout / profile |
| `core` | `AuditEvent` + `audit.record()`, dashboard, activity page, 403 handler |
| `vault` | Semester, Course, Unit, Material, Tag; upload validation; owner-only views |
| `sharing` | StudyGroup, Membership, Share; `permissions.can_access`; `risk.assess_share`; services |

`AuditEvent` lives in `core` so that `vault` can record access denials without importing `sharing`.

Request flow: URL → view (HTTP only) → form (input validation) → service (business rules) →
`can_access` (permission decision) → `audit.record` (log entry).

## Security rules

- Every `/vault/...` URL is owner-only (`vault/access.get_owned`). A non-owner on an existing id gets
  **403 and an audit event**; an unknown id gets 404.
- Non-owners reach a material only through a `Share` to a group they belong to
  (`sharing/permissions.py`). Group membership alone grants nothing.
- `view`, `download` and `edit` are independent (edit does not imply download).
  `replace_file`, `delete` and `share` are owner-only.
- Security Engine (`sharing/risk.py`): an application-defined heuristic, **not** a validated score.
  The server recomputes it on every share write. Public sharing is always blocked; medium risk requires an
  explicit confirmation checkbox. Points: group 15, view 5, edit 30, download +12;
  low ≤ 34, medium 35–59, high ≥ 60. The deck's "Comment" level is not implemented.
- Uploads: extension allowlist + size limit + file-content check, random storage names, private `MEDIA_ROOT`
  (no URL serves it), downloaded only through permission-checked views as `attachment` + `nosniff`.
  Allowed: pdf, docx, pptx, txt, md, png, jpg/jpeg; max 10 MB (`MAX_UPLOAD_BYTES`).
  There is **no malware scanning**.
- Audit log is append-only through the app and read-only in the Django admin.
- Do **not** enable `ATOMIC_REQUESTS`: audit events for denied or blocked actions are written before an
  exception and must not be rolled back.

## Setup (Ubuntu 24.04)

Check what you already have first, and install only what is missing:

```bash
python3 --version                  # 3.12 expected
mysql --version                    # must say "MySQL" 8.0 (see "MariaDB" below)
systemctl is-active mysql
```

### 1. Clone and create the environment

```bash
git clone git@github.com:SupeFrankie/Mind-Vault.git
cd Mind-Vault
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

If `mysqlclient` fails to build:

```bash
sudo apt install -y libmysqlclient-dev pkg-config build-essential python3-dev
pip install -r requirements.txt
```

### 2. Create `.env` (skipped if you already have one)

`.env` is git-ignored. This generates a secret key and a database password that passes MySQL's default
password policy (upper and lower case, digits, symbol):

```bash
if [ -f .env ]; then echo ".env exists, leaving it alone"; else
  cp .env.example .env
  KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
  PW=$(python3 -c "import secrets; print('Mv_' + secrets.token_hex(6) + '_Vault2026')")
  sed -i "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$KEY|" .env
  sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=$PW|" .env
  sed -i "s|^MEDIA_ROOT=.*|MEDIA_ROOT=$HOME/mindvault_media|" .env
  mkdir -p "$HOME/mindvault_media"
fi
```

### 3. Create the MySQL database and user

```bash
DBPW=$(grep '^DB_PASSWORD=' .env | cut -d= -f2-)
sudo mysql <<SQL
CREATE DATABASE IF NOT EXISTS mindvault CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'mindvault'@'localhost' IDENTIFIED BY '$DBPW';
ALTER USER 'mindvault'@'localhost' IDENTIFIED BY '$DBPW';
GRANT ALL ON mindvault.* TO 'mindvault'@'localhost';
GRANT ALL ON \`test\_mindvault\`.* TO 'mindvault'@'localhost';
SQL
```

If that says `Access denied for user 'root'@'localhost'`, run the same block with the Ubuntu
maintenance account instead: `sudo mysql --defaults-file=/etc/mysql/debian.cnf <<SQL ... SQL`.

Verify:

```bash
mysql -u mindvault -p"$DBPW" mindvault -e "SELECT 'connected' AS status;"
```

### 4. Migrate, test, run

```bash
python manage.py migrate
python manage.py test              # expect "OK"
python manage.py createsuperuser
python manage.py runserver         # http://127.0.0.1:8000
```

The long `Forbidden (Permission denied)` tracebacks printed during the tests are expected: the tests
deliberately attempt forbidden actions. Only the final `Ran 61 tests ... OK` line matters.

### 5. Build the CSS (Tailwind standalone CLI, no Node needed)

Compiled CSS (`static/css/output.css`) is git-ignored, so **every developer builds it locally**.
Without it the pages render unstyled.

```bash
curl -sLo tailwindcss https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64
chmod +x tailwindcss
./tailwindcss -i static/src/input.css -o static/css/output.css --watch      # development
./tailwindcss -i static/src/input.css -o static/css/output.css --minify     # one-off / production
```

`tailwindcss` (the binary) is git-ignored too.

### Every day after that

```bash
cd Mind-Vault && source .venv/bin/activate
systemctl is-active mysql || sudo systemctl start mysql
python manage.py runserver
```

## Frontend contract

Server-rendered templates with ordinary Django forms. Form field names, URL names and template context
variables are listed in `docs/INTEGRATION.md`; restyle freely but keep those names. Every POST form needs
`{% csrf_token %}`; file forms need `enctype="multipart/form-data"`; logout is POST only.

## Git workflow

- `main` always runs. Work on `backend/<topic>` or `frontend/<topic>` branches and merge via pull request.
- Backend owns Python, `config/`, migrations, `requirements.txt`, `docs/`. Frontend owns `templates/` and
  `static/src`, `static/js`. Shared: `templates/base.html`, `apps/*/urls.py`, field/URL/context names.
- Only the backend developer runs `makemigrations`. Never edit a migration that is on `main`.
- Never commit `.env`, `media/`, `.venv/`, `db.sqlite3`, `static/css/output.css`, `tailwindcss`.
- Run `python manage.py test` before merging to `main`. Details: `docs/GIT_WORKFLOW.md`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Access denied for user 'mindvault'@'localhost'` on migrate | MySQL user missing or password differs from `.env`. Redo step 3 with the password from `.env`. |
| `ERROR 1819 ... does not satisfy the current policy` | MySQL password policy. Use the generated password from step 2. |
| `Access denied for user 'root'` with `sudo mysql` | Use `--defaults-file=/etc/mysql/debian.cnf` (step 3). |
| `ssh: connect to host github.com port 22: Connection timed out` | Network blocks SSH. Add `Host github.com / Hostname ssh.github.com / Port 443 / User git` to `~/.ssh/config`. |
| Pages are unstyled | `static/css/output.css` is not built (step 5). |
| `TypeError ... CheckConstraint ... 'condition'` or other import errors | Django older than 5.2. Re-run `pip install -r requirements.txt` in a fresh `.venv`. |
| Django reports **MariaDB** | The server is MariaDB, not MySQL. See below. |

### MariaDB instead of MySQL

The project targets MySQL 8.0 and its tests were run on MySQL 8.0 only. Django's MySQL backend also talks to
MariaDB, which is why it can report `MariaDB`. Check with:

```bash
mysql --version
python manage.py shell -c "from django.db import connection as c; print(c.mysql_is_mariadb, c.mysql_version)"
```

For parity with the demo environment, install MySQL 8.0 (`sudo apt install mysql-server`; remove MariaDB first
with `sudo apt remove mariadb-server` if it is installed, and back up any data you care about). MariaDB is
**untested** with this project.

## Verification status

- Verified on **MySQL 8.0** (before the policy and sign-in changes): 61 automated tests pass on **MySQL 8.0** (Ubuntu 24.04, Python 3.12, Django 5.2). The policy, cookie and sign-in placeholder tests were run on SQLite
  (`DB_ENGINE=sqlite` exists only as a test escape hatch, never for the app); re-run `python manage.py test`
  on MySQL after pulling. Migrations are current.
- Not yet verified: deployment, behaviour under MariaDB, manual cross-browser testing of the UI.

## Known gaps

- Join codes are 40-bit random and failed attempts are audited, but there is **no rate limiting**.
- No malware scanning of uploads.
- `REMOTE_ADDR` is logged as the client IP; behind a proxy it may be the proxy's address.
- Deferred by design: AI study agent, M-Pesa, study planner, learning insights, comments, public links.

See also: [`docs/PRD.md`](docs/PRD.md), `docs/INTEGRATION.md`, `docs/DEPLOYMENT.md`, `docs/GIT_WORKFLOW.md`.
