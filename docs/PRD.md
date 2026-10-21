# MindVault Product Requirements Document

## 1. Document Information

| Field | Value |
|---|---|
| Project | MindVault |
| Document | Product Requirements Document (PRD) |
| Version | 0.1 |
| Last updated | 21 October 2026 |
| Status | Draft, written against the code in this repository |
| Team | Team SOLVEX (backend and frontend developers) |

Status labels used throughout: **Implemented** (code exists and automated tests cover it), **Partial** (works but incomplete), **Coming soon** (placeholder page only), **Planned** (not started), **Blocked** (needs an external decision or credential).

## 2. Executive Summary

MindVault is a web platform for university students to keep academic materials organised (Semester → Course → Unit → Materials), find them quickly, form study groups, and share chosen materials with those groups under explicit permissions. A Security Engine scores the risk of each sharing setting and an audit log records security-relevant events. The product is a hackathon MVP; it is not deployed and has no real users or usage data yet.

## 3. Problem Statement

Students keep lecture notes, slides, assignments and past papers across chat apps, email attachments, cloud drives and screenshots. Finding a specific file takes time, there is no consistent structure by semester, course and unit, and sharing is usually all-or-nothing (a public link or a forwarded file) with no way to limit what recipients can do or to see who accessed what. MindVault addresses structure, retrieval and controlled sharing in one place.

## 4. Product Vision

A student's single, private-by-default academic workspace, where sharing with classmates is deliberate, limited and visible.

## 5. Target Users

- University students organising their own academic resources.
- Students collaborating in study groups.

No other user types (lecturers, administrators as a product role, institutions) are in the current scope. Project staff use the Django admin site for maintenance; it is not a student-facing feature.

## 6. User Problems and Needs

| Need | Addressed by | Status |
|---|---|---|
| Organise resources by semester, course, unit | `vault` hierarchy | Implemented |
| Find a resource quickly | Library search and filters | Implemented |
| Control who can see, edit or download a shared item | `sharing` permissions + Security Engine | Implemented |
| Collaborate with other students | Study groups with join codes | Implemented |
| Understand what happened to shared material | Activity page (own events) | Implemented |

## 7. Product Goals

Verifiable goals for the MVP (no usage statistics exist or are claimed):

1. A new user can register, create a semester, course and unit, and add a material in one sitting without leaving the app.
2. A material is inaccessible to every other user unless explicitly shared; this is covered by automated tests.
3. Every share passes a server-side risk assessment; high-risk and public shares are refused.
4. Every denied access attempt and share change is recorded in the audit log.
5. The automated test suite (`python manage.py test`) passes on MySQL 8.0.

## 8. Non-Goals (current MVP)

AI study agent or summaries; M-Pesa/payments; real-time chat; collaborative editing; public sharing links; research-paper discovery or crawling; study planner and learning insights; notifications; comments on materials; email verification and password reset; real Google, Apple or university sign-in.

## 9. Core User Journeys

| Journey | Status |
|---|---|
| Register with email and password, log in, log out | Implemented |
| Create semesters, courses, units; browse the hierarchy | Implemented |
| Add a material (text, file, or both), edit, delete, download as owner | Implemented |
| Search and filter the library by text, type, course, unit, tag | Implemented |
| Create a study group, share its join code, join with a code, leave | Implemented |
| Share a material with a group (view or edit, download on/off), change or revoke | Implemented |
| Open "Shared with me", view, download (if allowed), edit title and notes (if allowed) | Implemented |
| Read the Privacy Policy and Cookie Policy without logging in | Partial (contact and organisation details are placeholders) |
| Choose Google, Apple or School Email sign-in | Coming soon (informational page only) |

## 10. Functional Requirements

| ID | Requirement | Where | Status |
|---|---|---|---|
| FR-1 | Email-and-password registration, login, logout (POST), profile edit (name, university) | `apps/accounts` | Implemented |
| FR-2 | Semester, Course, Unit CRUD scoped to the owner, with per-parent unique names | `apps/vault` | Implemented |
| FR-3 | Material CRUD with kind, notes, tags, optional research metadata (authors, year, DOI) | `apps/vault` | Implemented |
| FR-4 | File upload validated by extension, size (10 MB) and content; random storage name; private storage | `apps/vault/validators.py`, `forms.py` | Implemented |
| FR-5 | Library search and filters over the user's own materials; "Shared with me" search over authorised materials only | `vault/services.py`, `sharing/views.py` | Implemented |
| FR-6 | Study groups with unique join code, owner and member roles, leave, remove member, delete group | `apps/sharing` | Implemented |
| FR-7 | Share a material with a group: view or edit permission, independent download flag, one share per material and group | `apps/sharing` | Implemented |
| FR-8 | Central permission check `can_access(user, material, action)` for view, download, edit, replace_file, delete, share | `sharing/permissions.py` | Implemented |
| FR-9 | Security Engine `assess_share`; public shares and high risk refused; medium risk needs confirmation; recomputed server-side | `sharing/risk.py`, `services.py` | Implemented |
| FR-10 | Append-only audit log of share changes, joins, leaves, denials, blocks; each user sees their own events | `apps/core` | Implemented |
| FR-11 | Public Privacy Policy at `/privacy/` | `core/views.py`, `templates/core/privacy.html` | Partial |
| FR-12 | Public Cookie Policy at `/cookies/` | `templates/core/cookies.html` | Implemented (contact placeholder) |
| FR-13 | Google sign-in | `accounts:coming_soon` | Coming soon |
| FR-14 | Apple sign-in | `accounts:coming_soon` | Coming soon |
| FR-15 | School-email sign-in against institutional systems | `accounts:coming_soon` | Coming soon / Blocked (needs institutional approval) |

## 11. Non-Functional Requirements

- **Security:** server-side authorisation on every object access; CSRF protection; uploads treated as untrusted; production settings force HTTPS cookies and redirect.
- **Privacy:** private-by-default materials; no analytics or ad tracking; policies describe actual behaviour and are checked by tests (see section 15).
- **Accessibility:** semantic templates, labelled controls, keyboard-operable links and buttons. Not yet audited against WCAG.
- **Responsive design:** the shared layout (sidebar, mobile menu, responsive grids) covers desktop and mobile. Not yet verified in real browsers by the team.
- **Maintainability:** modular monolith, dependency direction `accounts ← core ← vault ← sharing`, business rules in `services.py`, tests per app.
- **Performance:** indexed owner/unit/kind queries and paginated lists. No load testing has been done.
- **Data integrity:** foreign keys, unique constraints (semester, course, unit, tag, membership, share), check constraint on material title, deletion rules documented in the models.
- **Reliability:** private file storage requires backups together with the database; no automated backup exists.

## 12. Data and Permissions

Entities (Django models):

| Model | App | Notes |
|---|---|---|
| `User` | accounts | email login, `full_name`, `university` |
| `Semester` → `Course` → `Unit` | vault | owner reached through the semester |
| `Material`, `Tag` | vault | file, notes, research metadata; tags are per owner |
| `StudyGroup`, `Membership` | sharing | owner PROTECTed from deletion; unique (group, user) |
| `Share` | sharing | material + group + permission + `allow_download` + risk score; unique (material, group) |
| `AuditEvent` | core | append-only; stores actor email and IP; target stored as type and id |

Permission rules: the owner can do everything with their materials. Anyone else needs a `Share` to a group they currently belong to. Group membership alone grants nothing. `view`, `download` and `edit` are independent; `edit` allows changing title and notes only. `replace_file`, `delete` and `share` are owner-only. Leaving or being removed from a group deletes that user's shares to the group.

## 13. Authentication Requirements

**Current (Implemented):** Django's authentication on a custom user model (`accounts.User`); the email address is the login identifier; Django password hashing and validators (minimum 10 characters, common and numeric checks); session cookies; login `next` redirects are validated by Django and only same-site targets are followed (tested); logout is POST-only.

**Not implemented:** email verification, password reset, two-factor authentication, login rate limiting, any external identity provider. An email address is not verified, so a school-style address alone does not prove identity.

**Placeholders (Coming soon):** `/accounts/auth/google/`, `/accounts/auth/apple/`, `/accounts/auth/school-email/` show an informational page and a link back to login. They authenticate nobody, collect no credentials, make no outbound request and use no OAuth credentials.

**Future real integration (not started, not promised):** Google and Apple would use OpenID Connect/OAuth 2.0 with credentials the team registers with each provider. School-email sign-in depends entirely on the institution: potential mechanisms to evaluate are OpenID Connect, OAuth 2.0-based identity, or SAML single sign-on, but no university has been contacted and none is known to offer one. Prerequisites: institutional approval, technical documentation, authorised credentials, an agreed approach to account linking and identity, and a privacy review. The existing `User` model and Django auth would remain the account store; a vetted library would be chosen at that point rather than custom protocol code.

## 14. User Interface Requirements

Screens (Django templates in `templates/`): login, register, profile, dashboard, activity, semesters, semester/course/unit detail, library, material detail and forms, groups (list, detail, create, join), share manager, shared-with-me list and detail, coming-soon page, Privacy Policy, Cookie Policy, 403 and 404.
Shared layout in `templates/base.html`: sidebar navigation, top bar with breadcrumb and light/dark toggle, footer with Privacy and Cookie links. Styling: Tailwind CSS built from `static/src/input.css`; compiled `static/css/output.css` is built locally and not committed. Light/dark and sidebar-collapsed preferences are stored in `localStorage`.

## 15. Security and Privacy Requirements

| Requirement | Status |
|---|---|
| Authentication on all app pages except register, login, policy pages, coming-soon pages | Implemented (verified by code review of all views) |
| Server-side authorisation for materials, downloads, shares | Implemented, tested |
| Private files: no public media URL; downloads only through permission-checked views | Implemented, tested |
| Cookies: `sessionid`, `csrftoken`, `messages`, all essential; no analytics | Implemented; the Cookie Policy is checked by a test that fails if the app sets an undocumented cookie |
| Browser storage keys documented | Implemented; a test fails if the site writes an undocumented `localStorage` key |
| Third-party requests disclosed | Google Fonts is loaded by `base.html`; disclosed in both policies; a test checks every host in `base.html` is named in the Privacy Policy |
| Audit logging of denials and share changes | Implemented |
| Data subject requests (access, correction, deletion) | Partial: profile edit exists; other requests are handled manually through a contact address that is still a placeholder |
| Compliance with Kenya's Data Protection Act, 2019 | Not claimed. Data controller registration, retention period, lawful-basis review and legal review are outstanding |

## 16. MVP Scope

Implemented: sections 9 and 10 rows marked Implemented. Partial: policy pages. Coming soon: three sign-in options. Everything in section 8 is out of scope.

## 17. Acceptance Criteria

1. Registering with a valid email and strong password creates an account and signs the user in; a duplicate email (any letter case) is rejected.
2. A user cannot open, edit, delete or download another user's material by changing an id in the URL; the response is 403 and an `access.denied` audit event exists.
3. A file with a disallowed extension, mismatched content, zero size, or over 10 MB is rejected with a form error and nothing is stored.
4. Sharing a material with a group makes it appear in members' "Shared with me"; revoking removes it.
5. A viewer without the download flag receives 403 on the file; an editor without the download flag still cannot download.
6. Sharing with edit and download both on requires the confirmation box; omitting it refuses the share with an explanation.
7. `/privacy/` and `/cookies/` load for a logged-out visitor and are linked from the footer on every page.
8. Each of the three provider links opens a page naming that provider, shows no form or password field, and links back to login; an unknown provider path returns 404.
9. Existing password login and registration still work with the provider options present; an off-site `next` value is ignored.
10. `python manage.py check`, `makemigrations --check --dry-run` and `test` pass.

## 18. Known Limitations

- Policy pages contain placeholders (controller, contact email, postal address, DPO, hosting provider, effective date, retention period, minimum age) that the team must fill before public release, and need legal review.
- Audit events keep the actor's email and IP address even after an account is deleted; no retention or anonymisation policy exists.
- Google Fonts is a third-party request that discloses visitors' IP addresses to Google; self-hosting the fonts would remove it.
- No malware scanning of uploads, no two-factor authentication, no email verification, no password reset, no rate limiting on login or join-code attempts.
- Sessions last 14 days with no idle timeout; no Content-Security-Policy header is set; the admin site is at the default `/admin/` path.
- The audit log is append-only through the application only; database-level protection requires restricting the database user.
- No browser, accessibility or load testing by the team yet; not deployed.
- A PythonAnywhere free account created after January 2026 does not include MySQL, so a public demo needs a paid plan or another host.

## 19. Future Roadmap

No dates are agreed. Realistic candidates, in rough priority: fill policy placeholders and obtain review; self-host fonts; login and join-code rate limiting; email verification and password reset; study planner; comments on shared materials; AI study assistant built on `can_access`; real Google and Apple sign-in; school-email sign-in if an institution agrees; M-Pesa payments as a separate app.

## 20. Glossary

- **Material:** a note, slide deck, assignment, past paper, research paper or other item with optional file.
- **Unit:** a topic or module inside a course.
- **Study group:** a set of students who can receive shares.
- **Share:** permission for one group to access one material.
- **Security Engine:** the rule-based risk scoring of a share, an application-defined heuristic and not a validated measure.
- **Audit event:** a stored record of a security-relevant action.
- **Essential cookie:** a cookie needed for sign-in, security or basic operation.
- **CSRF:** cross-site request forgery, an attack the CSRF cookie and token defend against.
- **OIDC / OAuth 2.0 / SAML:** standard protocols institutions and providers use for single sign-on.
- **ODPC:** Kenya's Office of the Data Protection Commissioner.
