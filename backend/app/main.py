from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .database import SessionLocal
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
    "/v1/auth/resend-verification",
    "/v1/auth/forgot-password",
}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
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
    for router in ALL_ROUTERS:
        application.include_router(router)
    application.include_router(jobs_router)

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

    return application


app = create_app()
