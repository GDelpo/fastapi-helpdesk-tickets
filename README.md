# fastapi-helpdesk-tickets

<p>
  <img alt="Python" src="https://img.shields.io/badge/python-3.14-blue?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.128-009688?logo=fastapi&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Status" src="https://img.shields.io/badge/status-wip-yellow">
</p>

> FastAPI helpdesk / ticketing microservice with an admin dashboard, an employee portal, email notifications and hierarchical categories.

> [!NOTE]
> **This is a scaffold / template.** It ships with generic categories, branding and hostnames as defaults. Fork it and customize the seed queues (`alembic/versions/001_init.py`), branding (`.env`) and UI copy to match your organization.

## Features

- REST API under `/api/v1/`; every request is authenticated against an external identity service
- **Admin dashboard** (`/dashboard/`): tickets, categories, assignment, bulk status changes, resolution
- **Employee portal** (`/portal/`): open tickets, reply, attach files, resolve or reopen
- Hierarchical ticket **queues / categories** (parent-child)
- **Follow-ups** with `@mention` autocomplete, attachments, watchers, sub-tickets and related-ticket links
- **In-app notifications** plus **email notifications** (8 HTML templates rendered here, sent through an external email service)
- Background **reminder task** for tickets nobody answered (configurable days and interval)
- Ready to run behind Traefik on the `/tickets` sub-path

## Architecture

The service plugs into two other services over plain HTTP:

- **Identity service** ([`fastapi-identity-service`](https://github.com/GDelpo/fastapi-identity-service) or your own). Used for `POST /login` (proxied), `GET /me` (validates the bearer token on every API call) and `GET {api_prefix}/` (user list for assignment and `@mention`, called with the service account `TICKETS_SERVICE_USER`).
- **Email service** ([`fastapi-email-service`](https://github.com/GDelpo/fastapi-email-service) or your own). Receives `POST {EMAIL_SERVICE_URL}` with `to`, `subject`, `html_content` and `source_service`. The HTML is rendered in this service from `app/ui/templates/email/`. If the service account cannot get a token, emails are skipped; network errors and replies other than 200 or 202 from the email service are logged, not raised.

The client code lives in `app/auth_service.py`, `app/dependencies.py` and `app/notifications.py`.

```
app/
├── main.py              # App, lifespan (HTTP client, users cache, reminder task), /health
├── config.py            # Pydantic settings (env vars)
├── api.py               # FastAPI router /api/v1/
├── service.py           # Business logic and access rules
├── repository.py        # Async SQLModel data access
├── models.py            # SQLModel tables
├── schemas.py           # Pydantic request/response (camelCase aliases)
├── dependencies.py      # CurrentUser, AdminUser, pagination, filters
├── auth_service.py      # IdentityServiceClient (httpx)
├── notifications.py     # Email notification routing and email service client
├── reminders.py         # Reminder selection and sending
├── attachment_storage.py# Attachments on a disk volume, metadata in the DB
├── middleware.py        # ProxyHeadersMiddleware + RequestLoggingMiddleware
├── exceptions.py        # ServiceException + handlers
├── database.py          # Async engine + session maker (asyncpg)
└── ui/                  # Jinja2 admin + portal (Tailwind browser build)
alembic/versions/        # Migrations (001 seeds generic starter queues)
docker/                  # Dockerfile + dev / standalone / traefik compose files
```

## Quickstart

### Requirements

- Python 3.14 (the version of `docker/Dockerfile`)
- PostgreSQL (the compose files use 16)
- An identity service as described above
- Optional: an email service as described above

### Install

```bash
git clone https://github.com/GDelpo/fastapi-helpdesk-tickets.git
cd fastapi-helpdesk-tickets
python -m venv env
source env/bin/activate          # Linux/Mac
# .\env\Scripts\Activate.ps1     # Windows
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit DB_PASSWORD, IDENTITY_SERVICE_URL, TICKETS_SERVICE_USER, EMAIL_SERVICE_URL, COMPANY_NAME, ...
```

See [Configuration](#configuration) for the full list.

### Run

```bash
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

- Portal: http://localhost:8000/portal/
- Admin: http://localhost:8000/dashboard/
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

### Docker

| File | Usage |
|---|---|
| `docker/docker-compose.dev.yml` | Local dev with bind mount and `DEBUG=true` |
| `docker/docker-compose.prod.standalone.yml` | Production without a reverse proxy |
| `docker/docker-compose.prod.traefik.yml` | Production behind Traefik on `/tickets` (prefix stripped) |

```bash
docker compose --env-file .env -f docker/docker-compose.prod.traefik.yml up -d --build api
```

The container entrypoint waits for Postgres, runs `alembic upgrade head` and starts gunicorn with uvicorn workers (`UVICORN_WORKERS`, default 2).

## Configuration

Environment variables read by `app/config.py` (`.env.example` has a starting set):

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | PostgreSQL connection | `localhost` / `5432` / `tickets` / `tickets_user` / `tickets_password` |
| `IDENTITY_SERVICE_URL` | Internal URL of the identity API | `http://identity_api:8080/api/v1` |
| `IDENTITY_EXTERNAL_URL` | Browser-reachable identity URL (Swagger UI `tokenUrl`) | `http://localhost:8080/identity/api/v1` |
| `TICKETS_SERVICE_USER` / `TICKETS_SERVICE_PASSWORD` | Service account in identity (user list, email sending) | empty |
| `EMAIL_SERVICE_URL` | Email service endpoint (`MAILSENDER_URL` is still read) | `http://email_service_api:8081/api/v1/emails` |
| `EMAIL_SERVICE_TIMEOUT` | Seconds to wait for the email service (`MAILSENDER_TIMEOUT` is still read) | `5.0` |
| `PORTAL_BASE_URL` | Portal URL used for links in emails; no link when empty | empty |
| `ATTACHMENTS_PATH` / `ATTACHMENTS_MAX_SIZE_MB` / `ATTACHMENTS_MAX_PER_TICKET` | Attachment volume and limits (the last one is checked per upload) | `/data/attachments` / `10` / `20` |
| `ATTACHMENTS_ALLOWED_TYPES` | Accepted MIME types (JSON list) | PDF, images, text/CSV, Word, Excel |
| `COMPANY_NAME` / `COMPANY_LEGAL_TAGLINE` / `COMPANY_WEBSITE` / `COMPANY_LOGO_URL` / `COMPANY_FAVICON_URL` | Branding | `Helpdesk` / empty / `https://example.com` / empty / empty |
| `SERVICE_NAME` / `PORTAL_NAVBAR_TITLE` / `SUPPORT_EMAIL` | Branding and fallback email for staff notifications | `Helpdesk` / `Helpdesk` / `support@example.com` |
| `DOCS_URL` | Optional link shown in the admin sidebar | empty |
| `REMINDER_STAFF_DAYS` / `REMINDER_SUBMITTER_DAYS` | Days without a reply before reminding the assignee / the submitter | `2` / `2` |
| `REMINDER_CHECK_INTERVAL_HOURS` / `REMINDER_ENABLED` | Reminder task interval and kill switch | `6` / `true` |
| `CORS_ORIGINS` | Origins allowed to call the API from a browser (JSON list). Empty means same-origin only, which is all the built-in UIs need; credentials are never allowed cross-origin | `[]` |
| `DEBUG` / `ENVIRONMENT` | Debug mode; environment defaults to `development` or `production` from `DEBUG` | `false` / derived |
| `LOG_LEVEL` / `LOG_FORMAT` / `LOG_FILE` | Logging (`json` or `text`; optional file) | `INFO` (`DEBUG` with `DEBUG=true`) / `json` / none |
| `API_PREFIX` | API mount point | `/api/v1` |

## API

Auth column: **none** = public; **user** = any valid bearer token (checked against identity `GET /me`); **admin** = role `admin` or `service`. Non-admin users only see and act on tickets they submitted or watch.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/v1/login` | Login proxy to the identity service (form data) | none |
| GET | `/api/v1/me` | Current user | user |
| GET | `/api/v1/meta` | Status and priority enums (labels, order) | none |
| GET | `/api/v1/stats` | Ticket counts by status | admin |
| GET | `/api/v1/my/stats` | Counts for my tickets | user |
| GET | `/api/v1/identity/users?role=` | Assignable users (proxy to identity) | admin |
| GET | `/api/v1/queues/` | List active queues | user |
| POST | `/api/v1/queues/` | Create queue | admin |
| GET | `/api/v1/queues/{id}` | Queue detail | user |
| PATCH / DELETE | `/api/v1/queues/{id}` | Update / delete queue (delete only without tickets) | admin |
| GET | `/api/v1/tickets/` | List tickets (filters, pagination) | user |
| POST | `/api/v1/tickets/` | Create ticket | user |
| GET | `/api/v1/tickets/{id}` | Ticket detail with follow-ups, relations, attachments | user (submitter / watcher) |
| PATCH | `/api/v1/tickets/{id}` | Update; non-admins may edit title, description and priority, resolve or reopen, and reassign if they are the submitter or the assignee | user (submitter / watcher) |
| DELETE | `/api/v1/tickets/{id}` | Close the ticket (status `closed`, not a delete) | admin |
| PATCH | `/api/v1/tickets/bulk` | Bulk status change; returns `updated` and a `failed` list with each ticket id and reason | admin |
| POST | `/api/v1/tickets/{id}/followups/` | Add a follow-up | user (submitter / watcher) |
| POST | `/api/v1/tickets/{id}/relations/` | Link two tickets (inverse created automatically) | user (access to both tickets) |
| DELETE | `/api/v1/tickets/{id}/relations/{relation_id}` | Remove a relation and its inverse | user (submitter / watcher) |
| POST | `/api/v1/tickets/{id}/attachments/?followup_id=` | Upload files; `followup_id` must belong to the same ticket | user (submitter / watcher) |
| GET | `/api/v1/tickets/{id}/attachments/{attachment_id}/download` | Download an attachment | user (submitter / watcher) |
| DELETE | `/api/v1/tickets/{id}/attachments/{attachment_id}` | Delete; admins any, others their own uploads within 24 h | user |
| POST | `/api/v1/admin/reminders/run` | Run the reminder check now | admin |
| GET / POST | `/api/v1/my/tickets/` | Portal: my tickets / create | user |
| GET | `/api/v1/my/notifications/` | Portal: notifications and unread count | user |
| POST | `/api/v1/my/notifications/read-all` | Mark all as read | user |
| PATCH | `/api/v1/my/notifications/{id}` | Mark one as read | user |
| GET | `/api/v1/users/search?q=` | `@mention` autocomplete from the in-memory users cache | user |
| GET | `/health` | Postgres (critical) and identity (optional) status; 503 when Postgres is down | none |

The HTML pages under `/dashboard/` and `/portal/` are served without server-side auth; their JavaScript keeps the token in `localStorage` and calls the API. Full schema at `/docs` (Swagger UI).

## Tests

```bash
pip install -r requirements-dev.txt
ruff check . && ruff format --check .
pytest
```

Most tests replace the database and the identity / email services with in-memory fakes and call the real service functions or the API through FastAPI's `TestClient`. They cover:

- access rules: reading tickets, adding and removing relations (both tickets must be accessible), attachment deletion (admin, own upload within 24 h)
- uploads: a `followup_id` from another ticket is rejected with 404 and nothing is stored
- `@mention` parsing into watchers and notifications, and the audit-trail follow-ups for assignment, priority and resolution
- the login proxy (camelCase / snake_case token keys) and the auth dependency returning a generic 401 message instead of internal error text
- the identity user-list URL, the `EMAIL_SERVICE_URL` / `MAILSENDER_URL` settings alias
- frontend guards: no template or script interpolates values into inline `on*=` handlers, the `esc()` helpers escape quotes, toasts render as text

`tests/test_reminders_db.py` runs the reminder queries against a real Postgres, in the database `<DB_NAME>_test` (it drops and recreates the tables there, never in `DB_NAME`). Locally it is skipped when that database is unreachable; in CI (`CI=true`) it fails instead. The CI workflow (`.github/workflows/ci.yml`) starts Postgres as a service.

## Customization

This is a **template**. The most common things to change:

1. **Starter queues**: edit `INITIAL_QUEUES` in `alembic/versions/001_init.py` before the first migration, or manage them from the admin UI afterwards.
2. **Branding**: `COMPANY_NAME`, `PORTAL_NAVBAR_TITLE`, `COMPANY_LOGO_URL`, `COMPANY_FAVICON_URL`, `SUPPORT_EMAIL` in `.env`.
3. **Email templates**: plain HTML in `app/ui/templates/email/`.
4. **Identity / email clients**: swap the HTTP calls in `app/auth_service.py`, `app/dependencies.py` and `app/notifications.py` for your own providers.
5. **UI palette**: CSS variables in `app/ui/templates/admin/base.html`.

## License

[MIT](LICENSE) © 2026 Guido Delponte
