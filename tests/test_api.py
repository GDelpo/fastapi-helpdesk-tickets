"""HTTP-level tests: routers, dependencies and error handling through FastAPI's TestClient."""

import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import api, attachment_storage
from app.auth_service import IdentityServiceClient
from app.main import app
from app.models import FollowUp
from tests.conftest import make_ticket, make_user

UI = Path(__file__).resolve().parent.parent / "app" / "ui"
PREFIX = "/api/v1"


# --- Login (identity proxy) --------------------------------------------------------------


@pytest.fixture
def identity_login(monkeypatch):
    """Replace the identity login call with a canned response."""

    def set_response(data):
        async def fake_login(username, password, client):
            return data

        monkeypatch.setattr(api.identity_client, "login", fake_login)
        app.state.http_client = None  # the router only passes it through to identity

    yield set_response
    del app.state.http_client


def _login():
    return TestClient(app).post(f"{PREFIX}/login", data={"username": "u", "password": "p"})


def test_login_accepts_camel_case_token(identity_login):
    identity_login({"accessToken": "tok123", "tokenType": "bearer", "expiresIn": 900})
    response = _login()
    assert response.status_code == 200
    assert response.json() == {"accessToken": "tok123", "tokenType": "bearer", "expiresIn": 900}


def test_login_falls_back_to_snake_case_token(identity_login):
    identity_login({"access_token": "tok456", "token_type": "bearer", "expires_in": 1800})
    response = _login()
    assert response.status_code == 200
    assert response.json()["accessToken"] == "tok456"


def test_login_without_token_is_rejected(identity_login):
    identity_login({"message": "ok"})
    response = _login()
    assert response.status_code == 401
    assert response.json()["message"] == "Invalid authentication response"


# --- Authentication dependency -----------------------------------------------------------


def test_identity_failure_returns_generic_message():
    """Internal error text (here a KeyError from a malformed /me reply) never reaches the client."""

    def handler(request):
        return httpx.Response(200, json={"id": 1, "internalDetail": "db-host-10-0-0-5"})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = TestClient(app).get(f"{PREFIX}/me", headers={"Authorization": "Bearer x"})
    finally:
        del app.state.http_client

    assert response.status_code == 401
    assert response.json()["message"] == "Could not validate credentials"
    assert "userName" not in response.text and "db-host" not in response.text


async def test_identity_users_are_listed_from_the_api_root(monkeypatch):
    """Identity serves its user list at GET {api_prefix}; there is no /users/ route."""
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": 1, "userName": "asmith"}]})

    client = IdentityServiceClient(base_url="http://identity/api/v1")

    async def fake_token(http_client):
        return "svc-token"

    monkeypatch.setattr(client, "get_service_token", fake_token)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        users = await client.list_users(http_client)

    assert seen == ["/api/v1/"]
    assert users == [{"id": 1, "userName": "asmith"}]


# --- Tickets: access control -------------------------------------------------------------


def test_outsider_cannot_read_ticket(client_as, repos):
    ticket = make_ticket(repos[0], submitter="bob")
    response = client_as(make_user("mallory")).get(f"{PREFIX}/tickets/{ticket.id}")
    assert response.status_code == 403


def test_submitter_reads_own_ticket(client_as, repos):
    ticket = make_ticket(repos[0], submitter="bob")
    response = client_as(make_user("bob")).get(f"{PREFIX}/tickets/{ticket.id}")
    assert response.status_code == 200
    assert response.json()["title"] == "Printer on fire"


def test_outsider_cannot_add_relation(client_as, repos):
    ticket_repo = repos[0]
    foreign = make_ticket(ticket_repo, submitter="bob")
    mine = make_ticket(ticket_repo, submitter="mallory")

    response = client_as(make_user("mallory")).post(
        f"{PREFIX}/tickets/{foreign.id}/relations/",
        json={"targetTicketId": str(mine.id), "relationType": "related"},
    )

    assert response.status_code == 403
    assert ticket_repo.relations == {}


def test_admin_adds_and_removes_relation(client_as, repos):
    ticket_repo = repos[0]
    a = make_ticket(ticket_repo, submitter="bob")
    b = make_ticket(ticket_repo, submitter="carol")
    admin = client_as(make_user("root", "admin"))

    created = admin.post(
        f"{PREFIX}/tickets/{a.id}/relations/",
        json={"targetTicketId": str(b.id), "relationType": "duplicate"},
    )
    assert created.status_code == 201
    assert len(ticket_repo.relations) == 2  # relation + inverse

    removed = admin.delete(f"{PREFIX}/tickets/{a.id}/relations/{created.json()['id']}")
    assert removed.status_code == 204
    assert ticket_repo.relations == {}


def test_outsider_cannot_remove_relation(client_as, repos, service):
    ticket_repo = repos[0]
    a = make_ticket(ticket_repo, submitter="bob")
    b = make_ticket(ticket_repo, submitter="bob")
    created = client_as(make_user("bob")).post(
        f"{PREFIX}/tickets/{a.id}/relations/",
        json={"targetTicketId": str(b.id), "relationType": "related"},
    )
    assert created.status_code == 201

    response = client_as(make_user("mallory")).delete(
        f"{PREFIX}/tickets/{a.id}/relations/{created.json()['id']}"
    )

    assert response.status_code == 403
    assert len(ticket_repo.relations) == 2


# --- Attachments: followup must belong to the ticket -------------------------------------


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(attachment_storage, "STORAGE_ROOT", tmp_path)
    return tmp_path


def _upload(client, ticket_id, followup_id):
    return client.post(
        f"{PREFIX}/tickets/{ticket_id}/attachments/",
        params={"followup_id": str(followup_id)},
        files={"files": ("notes.txt", b"hello", "text/plain")},
    )


def test_upload_rejects_followup_of_another_ticket(client_as, repos, storage_dir):
    ticket_repo = repos[0]
    mine = make_ticket(ticket_repo, submitter="bob")
    other = make_ticket(ticket_repo, submitter="carol")
    foreign_followup = FollowUp(ticket_id=other.id, comment="hi")
    ticket_repo.followups.append(foreign_followup)

    response = _upload(client_as(make_user("bob")), mine.id, foreign_followup.id)

    assert response.status_code == 404
    assert ticket_repo.session.added == []
    assert list(storage_dir.rglob("*.txt")) == []


def test_upload_links_followup_of_same_ticket(client_as, repos, storage_dir):
    ticket_repo = repos[0]
    mine = make_ticket(ticket_repo, submitter="bob")
    followup = FollowUp(ticket_id=mine.id, comment="see attached")
    ticket_repo.followups.append(followup)

    response = _upload(client_as(make_user("bob")), mine.id, followup.id)

    assert response.status_code == 201
    body = response.json()
    assert [(a["filename"], a["followupId"]) for a in body] == [("notes.txt", str(followup.id))]
    assert len(list(storage_dir.rglob("*.txt"))) == 1


# --- Frontend: stored-XSS regression guards ----------------------------------------------

_VENDORED = {"lucide.min.js", "tailwind-browser.js"}


def _ui_sources():
    for path in sorted(UI.rglob("*")):
        if path.suffix in {".html", ".js"} and path.name not in _VENDORED:
            yield path


def test_ui_never_interpolates_into_inline_handlers():
    """No ${...} inside onclick/on* attributes (multi-line attributes included)."""
    pattern = re.compile(r"""\son\w+\s*=\s*(?:"[^"]*\$\{|'[^']*\$\{)""")
    offenders = []
    for path in _ui_sources():
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(UI)}:{line}")
    assert offenders == []


@pytest.mark.parametrize("template", ["templates/admin/base.html", "templates/portal/shell.html"])
def test_esc_helpers_escape_quotes(template):
    source = (UI / template).read_text(encoding="utf-8")
    body = source[source.index("function esc(") :]
    body = body[: body.index("\n  }") + 4]
    for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert entity in body, f"esc() in {template} does not produce {entity}"


def test_toast_renders_messages_as_text():
    source = (UI / "templates/shared/_toast.html").read_text(encoding="utf-8")
    assert "${message}" not in source
    assert ".textContent = message" in source


def test_admin_sidebar_shows_docs_url(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "docs_url", "https://docs.example.com/helpdesk")
    response = TestClient(app).get("/dashboard/")
    assert response.status_code == 200
    assert 'href="https://docs.example.com/helpdesk"' in response.text
