"""Client for communicating with Identity Service."""

import httpx

from app.logger import get_logger

logger = get_logger(__name__)


class IdentityServiceClient:
    """Async HTTP client for Identity Service authentication."""

    def __init__(self, base_url: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._service_token: str | None = None

    async def login(
        self, username: str, password: str, http_client: httpx.AsyncClient
    ) -> dict | None:
        """Authenticate via identidad and return token data."""
        try:
            response = await http_client.post(
                f"{self.base_url}/login",
                data={"grant_type": "password", "username": username, "password": password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
            if response.status_code == 200:
                return response.json()
            logger.warning("Identity login failed: %s", response.status_code)
            return None
        except httpx.RequestError as e:
            logger.error("Identity service connection error: %s", e)
            return None

    async def verify_token(
        self, token: str, http_client: httpx.AsyncClient
    ) -> dict | None:
        """Verify JWT token with identidad /me. Returns user data or None."""
        try:
            response = await http_client.get(
                f"{self.base_url}/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
            if response.status_code == 200:
                return response.json()
            return None
        except httpx.RequestError as e:
            logger.error("Identity service connection error: %s", e)
            return None

    async def get_service_token(self, http_client: httpx.AsyncClient) -> str | None:
        """Get cached service account token. Logs in on first call or if token expired."""
        from app.config import settings

        # Validate cached token
        if self._service_token:
            user = await self.verify_token(self._service_token, http_client)
            if user:
                return self._service_token
            self._service_token = None

        # Login with service account
        token_data = await self.login(
            settings.tickets_service_user,
            settings.tickets_service_password.get_secret_value(),
            http_client,
        )
        if not token_data:
            return None
        self._service_token = token_data.get("access_token", "")
        return self._service_token

    async def list_users(
        self,
        http_client: httpx.AsyncClient,
        role: str = "employee",
        is_active: bool = True,
        skip: int = 0,
        limit: int = 100,
    ) -> list[dict]:
        """List users from identidad (requires service account token)."""
        try:
            token = await self.get_service_token(http_client)
            if not token:
                return []
            response = await http_client.get(
                f"{self.base_url}/users/",
                params={"role": role, "is_active": str(is_active).lower(), "skip": skip, "limit": limit, "sort_by": "user_name:asc"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("data", [])
            return []
        except httpx.RequestError as e:
            logger.error("Identity users list error: %s", e)
            return []

