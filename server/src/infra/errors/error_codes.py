from __future__ import annotations

from protocol.enums import ErrorType
from protocol.models.error import ErrorModel


class ErrorCodes:
    VALIDATION_ERROR = "validation_error"
    AUTH_FAILED = "auth_failed"
    DEVICE_OFFLINE = "device_offline"
    DEVICE_NOT_FOUND = "device_not_found"
    BINDING_NOT_FOUND = "binding_not_found"
    ROUTE_NOT_FOUND = "route_not_found"
    TASK_TRANSITION_INVALID = "task_transition_invalid"
    INTERNAL_ERROR = "internal_error"



def build_error(
    error_code: str,
    error_message: str,
    *,
    error_type: ErrorType,
    source: str,
    retryable: bool = False,
    details: dict[str, object] | None = None,
) -> ErrorModel:
    return ErrorModel(
        error_code=error_code,
        error_message=error_message,
        error_type=error_type,
        source=source,
        retryable=retryable,
        details=details or {},
    )
