# Tickets Service

Mesa de ayuda interna para empleados de Noble Seguros. FastAPI + PostgreSQL + Jinja2 UI.

## Repositorio

```bash
git clone http://192.168.190.95/forgejo/noble/tickets.git
git pull origin main   # actualizar
```

> Primera vez en una máquina nueva: ver [SETUP.md](http://192.168.190.95/forgejo/noble/workspace/raw/branch/main/SETUP.md) para configurar proxy y credenciales Git.

## Features

- Creación, asignación, seguimiento y resolución de tickets
- Portal de empleados (`/portal/`) — interfaz amigable para abrir y seguir tickets
- Panel de administración (`/dashboard/`) — gestión completa para staff de IT
- Notificaciones por email via mailsender (5 plantillas Outlook-compatible)
- Organización por categorías (queues) seeded desde noble-docu
- Niveles de prioridad: Crítica, Alta, Normal, Baja, Muy Baja
- Menciones de usuarios (@mention) con autocompletado
- Sub-path routing via Traefik (`/tickets/*`)

## Quick Start (development)

```bash
cd tickets

python -m venv env
.\env\Scripts\Activate.ps1   # Windows PowerShell

pip install -r requirements.txt
pip install -r requirements-dev.txt

cp .env.example .env
# Completar: DB_PASSWORD, IDENTITY_SERVICE_URL

alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

- Portal: http://localhost:8000/portal/
- Admin: http://localhost:8000/dashboard/
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

## Production Deployment

```bash
# Copiar al servidor (desde PowerShell local)
scp -r tickets gdelponte@192.168.190.95:/opt/microservicios/tickets

# En el servidor (SSH)
cd /opt/microservicios/tickets
cp .env.example .env   # Si es primera vez
docker compose --env-file .env -f docker/docker-compose.prod.traefik.yml up -d --build api
```

Nota: el `docker build` de `tickets_api` compila automaticamente `admin.css` y `portal.css` desde `app/dashboard/static_src` (no se requiere ejecutar `npm` manualmente en el servidor).

- Portal: http://192.168.190.95/tickets/portal/
- Admin: http://192.168.190.95/tickets/dashboard/
- API: http://192.168.190.95/tickets/api/v1/
- Health: http://192.168.190.95/tickets/health

## Project Structure

```
tickets/
├── app/
│   ├── main.py              # App factory, lifespan, middleware, routers, /health
│   ├── config.py            # Pydantic-settings
│   ├── api.py               # FastAPI router /api/v1/
│   ├── service.py           # Business logic (TicketService, QueueService)
│   ├── repository.py        # Data access (async SQLModel)
│   ├── models.py            # SQLModel: Queue, Ticket, FollowUp, Attachment, Watcher, Notification
│   ├── schemas.py           # Pydantic request/response (camelCase aliases)
│   ├── dependencies.py      # CurrentUser, AdminUser, PaginationParams
│   ├── auth_service.py      # IdentityServiceClient (httpx → identidad)
│   ├── notifications.py     # Mailsender integration (5 template slugs)
│   ├── middleware.py         # ProxyHeadersMiddleware + RequestLoggingMiddleware
│   ├── exceptions.py        # ServiceException + handlers
│   ├── logger.py            # configure_logging()
│   ├── database.py          # async engine + session_maker (asyncpg)
│   └── dashboard/
│       ├── routes.py         # Jinja2 views: admin + portal
│       ├── static/           # Assets compilados servidos por FastAPI
│       │   ├── css/admin.css
│       │   ├── css/portal.css
│       │   ├── js/lucide.min.js
│       │   ├── css/fonts.css
│       │   └── fonts/*.woff2
│       ├── static_src/       # Fuente Tailwind + scripts de compilación
│       │   ├── src/admin.css
│       │   ├── src/portal.css
│       │   ├── package.json
│       │   └── tailwind.config.js
│       └── templates/
│           ├── base.html, shell.html, login.html     # Admin base
│           ├── _partials/    # _sidebar.html, _topbar.html
│           ├── _js/          # _auth.js.html, _helpers.js.html
│           ├── admin/        # overview, tickets, ticket_detail, queues
│           └── portal/       # portal_base, portal_shell, login, inicio,
│                             # categories, my_tickets, new_ticket, ticket_detail
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 001_initial.py    # Crea tablas + seeds 14 categorías
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.dev.yml
│   ├── docker-compose.prod.standalone.yml
│   └── docker-compose.prod.traefik.yml
├── entrypoint.sh
├── requirements.txt
├── requirements-dev.txt
├── .env.example
└── alembic.ini
```

## Technology Stack

| Component | Version |
|-----------|---------|
| Python | 3.14 |
| FastAPI | 0.128.0 |
| SQLModel | 0.0.31 |
| Pydantic | 2.12.5 |
| Alembic | 1.14.0 |
| Jinja2 | 3.1.6 |
| PostgreSQL | 16-alpine |
| asyncpg | (via SQLModel) |
| httpx | 0.28.1 |
| Uvicorn | 0.32.1 |

## Frontend Assets (Compiled CSS)

Los dashboards usan CSS compilado (no Tailwind runtime en browser).

```bash
cd tickets/app/dashboard/static_src
npm install
npm run build:css
```

- `build:portal` compila `src/portal.css` → `static/css/portal.css`
- `build:admin` compila `src/admin.css` → `static/css/admin.css`
- `build:css` compila ambos (recomendado para release)

## API Endpoints (`/api/v1/`)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/login` | Login via identidad | — |
| GET | `/me` | Current user info | JWT |
| GET | `/queues/` | List queues | JWT |
| POST | `/queues/` | Create queue | Admin |
| PATCH | `/queues/{id}` | Update queue | Admin |
| DELETE | `/queues/{id}` | Delete queue | Admin |
| GET | `/tickets/` | List all tickets (staff) | JWT |
| POST | `/tickets/` | Create ticket (staff) | JWT |
| GET | `/tickets/{id}` | Ticket detail | JWT |
| PATCH | `/tickets/{id}` | Update ticket | JWT |
| DELETE | `/tickets/{id}` | Close ticket | Admin |
| POST | `/tickets/{id}/followups/` | Add followup | JWT |
| GET | `/my/tickets/` | My tickets (portal) | JWT |
| POST | `/my/tickets/` | Create ticket (portal) | JWT |
| GET | `/my/notifications/` | My notifications | JWT |
| POST | `/my/notifications/read-all` | Mark all read | JWT |
| PATCH | `/my/notifications/{id}` | Mark one read | JWT |
| GET | `/users/search?q=` | User autocomplete | JWT |
| GET | `/identity/users` | List users (admin) | Admin |

## Authentication

JWT via identidad service:
1. Browser sends credentials to `POST /api/v1/login`
2. Tickets proxies to identidad `POST /api/v1/login` → JWT
3. Token stored in `localStorage` (`tk_token` / `tk_user`)
4. API calls include `Authorization: Bearer {token}`
5. Server validates via `GET {IDENTITY_SERVICE_URL}/me`

## Email Notifications

5 templates via mailsender (`POST /api/v1/emails` with `template_slug`):

| Slug | Evento | Destinatario |
|------|--------|-------------|
| `ticket-opened` | Ticket creado | Solicitante |
| `ticket-reply` | Respuesta de soporte | Solicitante |
| `ticket-pending` | Requiere respuesta del usuario | Solicitante |
| `ticket-resolved` | Ticket resuelto | Solicitante |
| `ticket-assigned-staff` | Ticket asignado | Agente de soporte |

## Environment Variables

```bash
# Database
DB_HOST=localhost
DB_PORT=5432
DB_USER=tickets_user
DB_PASSWORD=           # Required
DB_NAME=tickets

# Services (server-to-server, red Docker)
IDENTITY_SERVICE_URL=http://identidad_api:8080/api/v1
IDENTITY_EXTERNAL_URL=http://192.168.190.95/identidad/api/v1
MAILSENDER_URL=http://mailsender_api:8081/api/v1/emails

# Portal (URL base para links en emails)
PORTAL_BASE_URL=http://192.168.190.95/tickets/portal

# Application
DEBUG=false
LOG_LEVEL=INFO
CORS_ORIGINS=["*"]
```

## Docker

Multi-stage build: `python:3.14-slim`, non-root `appuser` (UID 1000). Entrypoint runs `alembic upgrade head` + `uvicorn`.

| Compose file | Uso |
|---|---|
| `docker-compose.dev.yml` | Dev — bind mount, DEBUG=True |
| `docker-compose.prod.standalone.yml` | Producción sin Traefik |
| `docker-compose.prod.traefik.yml` | Producción detrás de Traefik en `/tickets` |

## Useful Commands

```bash
# Migraciones
alembic upgrade head
docker exec -it tickets_api alembic upgrade head

# Logs
docker logs -f tickets_api

# DB directa
docker exec -it tickets_postgres psql -U tickets_user -d tickets

# Health check
curl http://192.168.190.95/tickets/health
```

## Related Services

| Service | Docker URL | Purpose |
|---|---|---|
| identidad | `http://identidad_api:8080/api/v1` | Auth / user lookup |
| mailsender | `http://mailsender_api:8081/api/v1/emails` | Email notifications |
| Traefik | `http://192.168.190.95` | Reverse proxy |
