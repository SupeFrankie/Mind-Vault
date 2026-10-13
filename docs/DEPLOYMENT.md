# Deployment

## Provider facts (checked 2026-10-13, re-check before you rely on them)
- PythonAnywhere free accounts: 1 web app, 100 CPU-seconds/day, 512 MiB disk, outbound internet restricted.
  **MySQL is only included on free accounts created before 15 Jan 2026 (8 Jan in the EU system); for new accounts MySQL
  requires the paid Developer tier.** So a new free account cannot run MindVault on MySQL.
  Sources: help.pythonanywhere.com/pages/FreeAccountsFeatures and blog.pythonanywhere.com/221.
- Recommendation: demo locally first (fully reliable). For a public demo use PythonAnywhere **paid** (MySQL included, HTTPS on
  `<user>.pythonanywhere.com`) or a small VPS you control. Do not fall back to SQLite for the app.
- Vercel is a poor fit: no persistent disk for private uploads and no MySQL.

## Production checklist (PythonAnywhere example)
1. Clone repo, `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
   (mysqlclient is available on PythonAnywhere).
2. Create the MySQL database in the Databases tab; put host/name/user/password in the web app's environment (or a `.env` outside the web root).
3. Environment: `DJANGO_SETTINGS_MODULE=config.settings.prod`, `DJANGO_SECRET_KEY` (new, random), `DJANGO_ALLOWED_HOSTS=<user>.pythonanywhere.com`,
   `DJANGO_CSRF_TRUSTED_ORIGINS=https://<user>.pythonanywhere.com`, DB_*, `MEDIA_ROOT=/home/<user>/mindvault_media` (outside the code dir and **not** mapped as a static/media URL).
4. `python manage.py migrate && python manage.py collectstatic --noinput && python manage.py createsuperuser`
5. WSGI file: point at `config.wsgi.application`. Static mapping: URL `/static/` -> `<repo>/staticfiles`. **Do not map the media directory.**
6. Reload. Verify: HTTPS redirect works, login works, upload + download works, `/media/...` returns 404.
7. Raise `SECURE_HSTS_SECONDS` only after HTTPS is confirmed. `check --deploy` still warns about HSTS preload/subdomains: intentional.

## Hardening notes
- Audit log: create a MySQL user for the app with `SELECT, INSERT` only on `core_auditevent` (and full rights elsewhere) if you want database-level append-only.
- `REMOTE_ADDR` is stored as the client IP; behind a proxy it may be the proxy's address.
- Uploads are not malware-scanned. Back up `MEDIA_ROOT` together with the database.
- Join codes are 40-bit random; failed attempts are audited but **not rate-limited** (flagged: add throttling if the app goes beyond a demo).
