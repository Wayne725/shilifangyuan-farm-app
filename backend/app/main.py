from __future__ import annotations

import logging
import re
import time
from uuid import uuid4
from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import REMOTE_ENVIRONMENTS, get_settings
from .database import SessionLocal, check_database_connection
from .domain import DomainError
from .jobs import (
    jobs_router,
    schedule_background_reconcile,
    should_reconcile_now,
)
from .routers import ALL_ROUTERS
from .seed import seed_demo_data


AUTH_EMAIL_PATHS = {
    "/v1/auth/register",
    "/v1/auth/register-existing-member",
    "/v1/auth/resend-verification",
    "/v1/auth/forgot-password",
}
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if get_settings().environment.strip().lower() in {"development", "test"}:
        async with SessionLocal() as session:
            await seed_demo_data(session)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    settings.validate_runtime_secrets()
    web_url = urlsplit(settings.web_base_url)
    web_origin = (
        f"{web_url.scheme}://{web_url.netloc}"
        if web_url.scheme and web_url.netloc
        else settings.web_base_url.rstrip("/")
    )
    cors_origins = list(
        dict.fromkeys(
            [
                origin.rstrip("/")
                for origin in [*settings.cors_origins, web_origin]
                if origin
            ]
        )
    )
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    allowed_hosts = {
        host
        for value in [
            settings.app_base_url,
            settings.web_base_url,
            *settings.cors_origins,
        ]
        if (host := urlsplit(value).hostname)
    }
    allowed_hosts.update({"localhost", "127.0.0.1", "testserver", "test"})
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=sorted(allowed_hosts),
    )
    for router in ALL_ROUTERS:
        application.include_router(router)
    application.include_router(jobs_router)

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_request_id
            if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
            else str(uuid4())
        )
        request.state.request_id = request_id
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed request_id=%s method=%s path=%s",
                request_id,
                request.method,
                request.url.path,
            )
            raise
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "request_completed request_id=%s method=%s path=%s "
            "status=%s duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Referrer-Policy",
            "strict-origin-when-cross-origin",
        )
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(self)",
        )
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        response.headers.setdefault(
            "Content-Security-Policy",
            "base-uri 'none'; object-src 'none'; frame-ancestors 'none'",
        )
        if settings.environment.strip().lower() in REMOTE_ENVIRONMENTS:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        if request.url.path.startswith(("/v1/", "/internal/")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @application.middleware("http")
    async def reconcile_on_api_request(request: Request, call_next):
        """Wake-up safety net for the free Render tier.

        The scheduled `/internal/reconcile` job is the primary driver; this only
        covers the case where the service was asleep when the cron fired. It is
        scheduled rather than awaited so reconciliation — which sweeps several
        tables and can call ECPay — never sits in the user's request path.
        """
        response = await call_next(request)
        if request.url.path in AUTH_EMAIL_PATHS:
            schedule_background_reconcile(
                settings,
                minimum_interval_seconds=0,
            )
        elif request.url.path.startswith("/v1/") and should_reconcile_now():
            schedule_background_reconcile(settings)
        return response

    @application.exception_handler(DomainError)
    async def handle_domain_error(
        _request: Request, exc: DomainError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.get("/health", tags=["system"])
    async def health() -> dict:
        return {
            "status": "ok",
            "service": settings.app_name,
            "environment": settings.environment,
        }

    @application.get("/ready", tags=["system"])
    async def readiness():
        try:
            await check_database_connection(
                settings.database_readiness_timeout_seconds
            )
        except Exception:
            logger.exception("database_readiness_failed")
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "database": "unavailable",
                },
            )
        return {"status": "ready", "database": "available"}

    return application


app = create_app()
