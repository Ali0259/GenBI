"""
app.main
--------
FastAPI application entrypoint. Wires together the auth, admin, and query
routers, configures CORS for the two frontends, and sets up per-tenant
LLM adapter factory storage on app.state.

Run in production via the Dockerfile's ENTRYPOINT (uvicorn), never via
`python app/main.py` directly.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_admin import router as admin_router
from app.api.routes_auth import router as auth_router
from app.api.routes_query import router as query_router
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger("genbi.main")

_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # In-memory registry of LLMAdapterFactory instances, one per tenant,
    # created lazily on first request (see app.api.deps.get_llm_adapter_factory).
    # This state is intentionally process-local: on a multi-worker deployment,
    # each worker independently tracks the active LLM configuration for the
    # tenants it happens to serve requests for, and re-derives it from the
    # LlmConfiguration table on first use after a restart.
    app.state.llm_factories_by_tenant = {}
    logger.info("GenBI backend starting up.")
    yield
    logger.info("GenBI backend shutting down.")


app = FastAPI(
    title="GenBI Platform API",
    description="Multi-tenant Generative Business Intelligence backend.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(query_router)


@app.get("/api/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Liveness/readiness probe used by Docker healthchecks and load balancers."""
    return {"status": "ok"}
