"""Custom exceptions and handlers."""

from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.logger import get_logger

logger = get_logger(__name__)


class ServiceException(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        details: dict[str, Any] | None = None,
    ):
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class EntityNotFoundError(ServiceException):
    def __init__(self, entity: str, identifier: str | int):
        super().__init__(
            message=f"{entity} '{identifier}' not found",
            status_code=status.HTTP_404_NOT_FOUND,
            details={"entity": entity, "identifier": str(identifier)},
        )


class EntityAlreadyExistsError(ServiceException):
    def __init__(self, entity: str, identifier: str | int):
        super().__init__(
            message=f"{entity} '{identifier}' already exists",
            status_code=status.HTTP_409_CONFLICT,
            details={"entity": entity, "identifier": str(identifier)},
        )


class AuthenticationError(ServiceException):
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
            details={"www_authenticate": "Bearer"},
        )


class AuthorizationError(ServiceException):
    def __init__(self, message: str = "Insufficient permissions"):
        super().__init__(message=message, status_code=status.HTTP_403_FORBIDDEN)


class ExternalServiceError(ServiceException):
    def __init__(self, service: str, message: str):
        super().__init__(
            message=f"{service} error: {message}",
            status_code=status.HTTP_502_BAD_GATEWAY,
            details={"service": service},
        )


class ValidationError(ServiceException):
    def __init__(self, message: str):
        super().__init__(message=message, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)


async def service_exception_handler(_request: Request, exc: ServiceException) -> JSONResponse:
    logger.warning(f"Service exception: {exc.message}", extra={"extra_fields": {"status_code": exc.status_code}})
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.message, "details": exc.details},
    )


async def validation_exception_handler(
    _request: Request, exc: RequestValidationError | PydanticValidationError
) -> JSONResponse:
    errors = exc.errors() if hasattr(exc, "errors") else []
    cleaned = [{"loc": e.get("loc", []), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in errors]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"success": False, "message": "Validation error", "details": {"errors": cleaned}},
    )


async def sqlalchemy_exception_handler(_request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error("Database error", extra={"extra_fields": {"error_type": exc.__class__.__name__}}, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"success": False, "message": "Database error", "details": {"type": exc.__class__.__name__}},
    )


async def general_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unexpected error", extra={"extra_fields": {"error_type": exc.__class__.__name__}}, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"success": False, "message": "Internal server error", "details": {"type": exc.__class__.__name__}},
    )
