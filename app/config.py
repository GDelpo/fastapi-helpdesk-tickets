"""Application settings and configuration."""

from pydantic import Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # =========================================================================
    # Environment
    # =========================================================================
    debug: bool = False
    environment: str | None = None

    # =========================================================================
    # Logging
    # =========================================================================
    log_level: str | None = None
    log_format: str = "json"
    log_file: str | None = None

    # =========================================================================
    # Application Metadata
    # =========================================================================
    service_name: str = "Mesa de Ayuda"
    project_name: str = "Tickets Service"
    project_version: str = "1.0.0"
    project_description: str = "Mesa de ayuda interna — Noble Seguros"
    api_prefix: str = "/api/v1"

    # =========================================================================
    # Branding
    # =========================================================================
    company_name: str = "Noble Seguros"
    company_legal_tagline: str = "Entidad autorizada por la Superintendencia de Seguros de la Nación"
    portal_navbar_title: str = "Noble Seguros"
    support_email: str = "sistemas@nobleseguros.com"
    company_website: str = "https://www.nobleseguros.com"
    company_logo_url: str = ""
    company_favicon_url: str = ""

    # =========================================================================
    # CORS
    # =========================================================================
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # =========================================================================
    # Database (PostgreSQL)
    # =========================================================================
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "tickets_user"
    db_password: SecretStr = SecretStr("tickets_password")
    db_name: str = "tickets"

    @computed_field
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:"
            f"{self.db_password.get_secret_value()}@"
            f"{self.db_host}:{self.db_port}/{self.db_name}"
        )

    # =========================================================================
    # Identity Service
    # =========================================================================
    # Server-to-server (red Docker interna)
    identity_service_url: str = "http://identidad_api:8080/api/v1"
    # URL externa (browser → Swagger UI tokenUrl)
    identity_external_url: str = "http://localhost:8080/identidad/api/v1"

    @computed_field
    @property
    def identity_login_url(self) -> str:
        """tokenUrl para Swagger UI — llamado desde el browser."""
        return f"{self.identity_external_url}/login"

    @computed_field
    @property
    def identity_me_url(self) -> str:
        """Endpoint /me para validación server-to-server."""
        return f"{self.identity_service_url.rstrip('/')}/me"

    @computed_field
    @property
    def identity_users_url(self) -> str:
        """Listado de usuarios — en identidad el endpoint es GET /api/v1 (sin /users/)."""
        return f"{self.identity_service_url.rstrip('/')}/"

    # Cuenta de servicio en identidad para llamadas autenticadas
    tickets_service_user: str = ""
    tickets_service_password: SecretStr = SecretStr("")

    # =========================================================================
    # Mailsender Service
    # =========================================================================
    mailsender_url: str = "http://mailsender_api:8081/api/v1/emails"
    mailsender_timeout: float = 5.0

    # =========================================================================
    # Attachments
    # =========================================================================
    attachments_path: str = "/data/attachments"
    attachments_max_size_mb: int = 10
    attachments_max_per_ticket: int = 20
    attachments_allowed_types: list[str] = Field(default_factory=lambda: [
        "application/pdf",
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "text/plain", "text/csv",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ])

    @computed_field
    @property
    def attachments_max_size_bytes(self) -> int:
        return self.attachments_max_size_mb * 1024 * 1024

    # =========================================================================
    # Portal
    # =========================================================================
    portal_base_url: str = ""

    # =========================================================================
    # Email Reminders (asyncio background task)
    # =========================================================================
    reminder_staff_days: int = 2          # Days with no staff followup → remind assigned agent
    reminder_submitter_days: int = 2      # Days with no submitter followup on pending → remind submitter
    reminder_check_interval_hours: int = 6  # How often the background task runs
    reminder_enabled: bool = True         # Kill switch

    # =========================================================================
    # Validation
    # =========================================================================
    @model_validator(mode="after")
    def validate_settings(self):
        if self.environment is None:
            object.__setattr__(self, "environment", "development" if self.debug else "production")
        if self.log_level is None:
            object.__setattr__(self, "log_level", "DEBUG" if self.debug else "INFO")
        else:
            object.__setattr__(self, "log_level", self.log_level.upper())
        if not self.api_prefix.startswith("/"):
            object.__setattr__(self, "api_prefix", f"/{self.api_prefix}")
        return self


settings = Settings()
