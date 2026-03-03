"""app/middleware/logger_middleware.py — Request ID injection and structured request logging."""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logger import get_logger, set_request_id

logger = get_logger(__name__)
_SKIP_PATHS = frozenset({"/health", "/metrics", "/favicon.ico"})


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        set_request_id(request_id)
        path = request.url.path
        method = request.method
        skip = path in _SKIP_PATHS
        start = time.perf_counter()
        if not skip:
            logger.info(
                "Request started",
                extra={
                    "method": method,
                    "path": path,
                    "query": str(request.query_params),
                    "client_ip": request.client.host if request.client else "unknown",
                    "user_agent": request.headers.get("user-agent", ""),
                },
            )
        try:
            response: Response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.error(
                "Request failed with unhandled exception",
                extra={"method": method, "path": path, "duration_ms": duration_ms},
                exc_info=exc,
            )
            raise
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        status = response.status_code
        if not skip:
            log_fn = logger.warning if status >= 400 else logger.info
            log_fn(
                "Request completed",
                extra={
                    "method": method,
                    "path": path,
                    "status_code": status,
                    "duration_ms": duration_ms,
                },
            )
        response.headers["X-Request-ID"] = request_id
        return response
