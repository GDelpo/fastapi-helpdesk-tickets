# fastapi-helpdesk-tickets

<p>
  <img alt="Language" src="https://img.shields.io/badge/python-3.12%2B-blue?logo=python&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Status" src="https://img.shields.io/badge/status-wip-yellow">
</p>

> FastAPI helpdesk / ticketing microservice with an admin dashboard, an employee portal, email notifications and hierarchical categories.

> [!NOTE]
> **This is a scaffold / template.** It was extracted from an in-house helpdesk running in production, with organization-specific categories, branding and hostnames replaced by generic defaults. Fork it and customize the seed queues (`alembic/versions/001_init.py`), branding (`.env`), and UI copy to match your organization.

## Features

- REST API under `/api/v1/` with JWT auth against an external identity service
- **Admin dashboard** (`/dashboard/`) — staff view: tickets, queues, assignment, resolution
- **Employee portal** (`/portal/`) — end-user view: open tickets, follow up, attachments
- Hierarchical ticket **queues / categories** (parent-child)
- **Follow-ups** with `@mention` autocomplete, attachments, watchers, and related-ticket links
- **Email notifications** (8 templates) via an external mailsender microservice
- Background **reminder task** for stale tickets (configurable days / interval)
- Sub-path routing via Traefik (`/tickets/*`) ready out of the box

## Architecture

This service is designed to plug into a small microservice ecosystem:

- **identity service** — validates JWTs (`GET /me`) and exposes user search for `@mention`.
- **mailsender service** — receives `POST /api/v1/emails` with a `template_slug`.

Both dependencies are contract-based (plain HTTP). You can swap them for your own implementations — the client code lives in `app/auth_service.py` and `app/notifications.py`.

```
app/
├── main.py              # App factory, lifespan, middleware, routers, /health
├── config.py            # Pydantic settings (env vars)
├── api.py               # FastAPI router /api/v1/
├── service.py           # Business logic
├── repository.py        # Async SQLModel data access
├── models.py            # SQLModel tables
├── schemas.py           # Pydantic request/response (camelCase aliases)
├── dependencies.py      # CurrentUser, AdminUser, PaginationParams
├── auth_service.py      # IdentityServiceClient (httpx)
├── notifications.py     # Mailsender client
├── reminders.py         # Background reminder task
├── middleware.py        # ProxyHeadersMiddleware + RequestLoggingMiddleware
├── exceptions.py        # ServiceException + handlers
├── database.py          # async engine + session_maker (asyncpg)
└── ui/                  # Jinja2 admin + portal (Tailwind CSS)
alembic/versions/        # Migrations (001 seeds generic starter queues)
docker/                  # dev / standalone / traefik compose variants
```

## Quickstart

### Requirements

- Python 3.12+
- PostgreSQL 16+
- An identity service that issues JWTs and exposes `GET /api/v1/me` (project: [`fastapi-identity`](https://github.com/GDelpo) or your own)
- A mailsender service that accepts `POST /api/v1/emails` with a `template_slug` (optional — disables notifications if unreachable)

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
# Edit DB_PASSWORD, IDENTITY_SERVICE_URL, MAILSENDER_URL, COMPANY_NAME, ...
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

Three compose variants are provided:

| File | Usage |
|---|---|
| `docker/docker-compose.dev.yml` | Local dev with bind mount and `DEBUG=True` |
| `docker/docker-compose.prod.standalone.yml` | Production without a reverse proxy |
| `docker/docker-compose.prod.traefik.yml` | Production behind Traefik on `/tickets/*` |

```bash
docker compose --env-file .env -f docker/docker-compose.prod.traefik.yml up -d --build api
```

## Configuration

Key environment variables (see `.env.example` for the full list):

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | PostgreSQL connection | `localhost` / `5432` / `tickets` / `tickets_user` / — |
| `IDENTITY_SERVICE_URL` | Internal URL of the identity microservice | `http://identity_api:8080/api/v1` |
| `IDENTITY_EXTERNAL_URL` | Browser-reachable identity URL (Swagger UI `tokenUrl`) | `http://localhost/identity/api/v1` |
| `MAILSENDER_URL` | Mailsender `POST /emails` endpoint | `http://mailsender_api:8081/api/v1/emails` |
| `PORTAL_BASE_URL` | Base URL embedded in outgoing email links | `http://localhost/tickets/portal` |
| `COMPANY_NAME` / `PORTAL_NAVBAR_TITLE` / `SUPPORT_EMAIL` / `COMPANY_LOGO_URL` | Branding overrides | `Helpdesk` / `Helpdesk` / `support@example.com` / — |
| `DOCS_URL` | Optional link shown in the admin sidebar | — |
| `REMINDER_STAFF_DAYS` / `REMINDER_SUBMITTER_DAYS` / `REMINDER_ENABLED` | Stale-ticket reminder tuning | `2` / `2` / `true` |

## API

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/v1/login` | Login proxy to the identity service | — |
| GET | `/api/v1/me` | Current user | JWT |
| GET / POST | `/api/v1/queues/` | List / create queues | JWT / Admin |
| PATCH / DELETE | `/api/v1/queues/{id}` | Update / delete queue | Admin |
| GET / POST | `/api/v1/tickets/` | List / create tickets | JWT |
| GET / PATCH | `/api/v1/tickets/{id}` | Ticket detail / update | JWT |
| POST | `/api/v1/tickets/{id}/followups/` | Add a follow-up comment | JWT |
| GET / POST | `/api/v1/my/tickets/` | Portal: my tickets | JWT |
| GET / PATCH | `/api/v1/my/notifications/` | Portal: notifications | JWT |
| GET | `/api/v1/users/search?q=` | User autocomplete (for `@mentions`) | JWT |

Full schema available at `/docs` (Swagger UI) and `/redoc`.

## Customization

This is a **template**. The most common things to change:

1. **Starter queues** — edit the `INITIAL_QUEUES` list in `alembic/versions/001_init.py` before the first migration, or manage them via the admin UI afterwards.
2. **Branding** — set `COMPANY_NAME`, `PORTAL_NAVBAR_TITLE`, `COMPANY_LOGO_URL`, `COMPANY_FAVICON_URL`, `SUPPORT_EMAIL` in `.env`.
3. **Email templates** — plain HTML in `app/ui/templates/email/`. Each picks up `{{ company_name }}` from the render context.
4. **Identity / mailsender clients** — swap the HTTP calls in `app/auth_service.py` and `app/notifications.py` for your own providers.
5. **UI palette** — CSS variables in `app/ui/templates/admin/base.html`.

## License

[MIT](LICENSE) © 2026 Guido Delponte
