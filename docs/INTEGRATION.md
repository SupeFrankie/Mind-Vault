# Frontend integration contract

Server-rendered Django templates. Plain forms, CSRF everywhere, no JSON API.
Every POST form needs `{% csrf_token %}`. File forms need `enctype="multipart/form-data"`.
Templates currently in `templates/` are functional, unstyled placeholders: restyle freely, keep the
**form field names, URL names and context variables** below.

## Rules for every page
- Unauthenticated -> 302 to `/accounts/login/?next=...`.
- Forbidden (exists but not yours / no permission) -> **403** rendering `templates/403.html`; attempt is audited.
- Unknown id -> 404 (`templates/404.html`).
- Messages: Django `messages` (`success` / `error`), rendered in `base.html`. Form errors: `form.errors` / `field.errors` / `form.non_field_errors`.
- Successful POST -> 302 redirect (PRG). Failed validation -> 200 with the bound form.
- Logout is **POST only**.

## URL map (use `{% url 'name' %}`)
| Path | Name | Methods | Notes |
|---|---|---|---|
| `/accounts/register/` | accounts:register | GET, POST | fields: email, full_name, university, password1, password2 |
| `/accounts/login/` | accounts:login | GET, POST | fields: username (the email), password, next |
| `/accounts/logout/` | accounts:logout | POST | |
| `/accounts/profile/` | accounts:profile | GET, POST | full_name, university |
| `/accounts/auth/<provider>/` | accounts:coming_soon | GET | `google`, `apple`, `school-email` only; other names 404. Informational page, no form, no credentials. Context: `name`, `message` |
| `/privacy/` | core:privacy | GET | public. Context: last_updated, max_upload_mb, session_days |
| `/cookies/` | core:cookies | GET | public. Cookie names/lifetimes come from Django settings |
| `/` | core:dashboard | GET | ctx: semester_count, material_count, group_count, shared_by_me_count, shared_with_me_count, recent_materials, recent_events |
| `/activity/` | core:activity | GET | ctx: page (paginated AuditEvent, own events only) |
| `/vault/` | vault:semester_list | GET | ctx: semesters |
| `/vault/semesters/new/` | vault:semester_create | GET, POST | name |
| `/vault/semesters/<id>/` (+`edit/`, `delete/`) | vault:semester_detail/edit/delete | GET, POST | delete: GET confirm page, POST deletes |
| `/vault/semesters/<id>/courses/new/` | vault:course_create | GET, POST | code (optional), title |
| `/vault/courses/<id>/` (+`edit/`, `delete/`) | vault:course_detail/edit/delete | | ctx: course, units |
| `/vault/courses/<id>/units/new/` | vault:unit_create | GET, POST | title, order |
| `/vault/units/<id>/` (+`edit/`, `delete/`) | vault:unit_detail/edit/delete | | ctx: unit, materials |
| `/vault/materials/` | vault:material_library | GET | query: q, kind, course, unit, tag, page. ctx: page, kinds, courses, tags, params |
| `/vault/materials/new/?unit=<id>` | vault:material_create | GET, POST | multipart. fields: unit, title, kind, file, notes, authors, publication_year, doi, tag_text (comma separated) |
| `/vault/materials/<id>/` (+`edit/`, `delete/`, `download/`) | vault:material_detail/edit/delete/download | | owner only |
| `/groups/` | sharing:group_list | GET | ctx: memberships |
| `/groups/new/` | sharing:group_create | GET, POST | name |
| `/groups/join/` | sharing:group_join | GET, POST | join_code (case-insensitive) |
| `/groups/<id>/` | sharing:group_detail | GET | members only. ctx: group, is_owner, join_code (owner only, else None), memberships, shares |
| `/groups/<id>/leave/` | sharing:group_leave | POST | |
| `/groups/<id>/delete/` | sharing:group_delete | GET confirm, POST | owner only |
| `/groups/<id>/members/<user_id>/remove/` | sharing:member_remove | POST | owner only |
| `/sharing/materials/<id>/` | sharing:share_manage | GET, POST | owner only. fields: group, permission (`view`/`edit`), allow_download (checkbox), confirm_risk (checkbox). ctx: form, shares, assessment (after a rejected POST), risk_rules |
| `/sharing/shares/<id>/revoke/` | sharing:share_revoke | POST | owner only |
| `/shared/` | sharing:shared_list | GET | query: q, kind, tag, page |
| `/shared/<material_id>/` | sharing:shared_detail | GET | ctx: material, is_owner, can_download, can_edit |
| `/shared/<material_id>/download/` | sharing:shared_download | GET | 403 unless download allowed |
| `/shared/<material_id>/edit/` | sharing:shared_edit | GET, POST | title, notes only (editors) |

Posting the share form for a group that is already shared **updates** that share (one row per material+group).

## Risk meter (display only: the server is the authority)
Points: audience group=15 (public=40 exists in the engine but is always blocked, there is no public option in the form);
permission view=5, edit=30; download +12 if checked. Levels: score <= 34 low, 35-59 medium, >= 60 high.
Medium: the server requires `confirm_risk` checked. The deck's "Comment" level is **not implemented** (there is no comment feature).
The rules are in the page as JSON: `{{ risk_rules|json_script:"risk-rules" }}` -> `JSON.parse(document.getElementById('risk-rules').textContent)`.
After a rejected POST, `assessment` has `.score .level .message .requires_confirmation .recommended`.

## Integrating your authentication work
Send me her auth code (models/forms/views/urls/templates/migrations). I will check: does it define its own User
(we already have `accounts.User`, email login, `full_name` + `university`; swapping it after migrations means resetting the DB),
URL names (`accounts:login`, `accounts:register`, `accounts:logout`), `LOGIN_URL`, logout as POST, and template names.
Until then, do not add a second user model or run `makemigrations` on her own app.
