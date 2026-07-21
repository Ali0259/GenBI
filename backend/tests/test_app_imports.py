"""
tests.test_app_imports
-------------------------
Smoke test: actually imports app.main and constructs the FastAPI app object.

This exists because a pure syntax check (ast.parse) cannot catch import-time
failures -- e.g. a route decorator that raises an AssertionError when the
module is loaded (as happened with a 204 No Content route missing
`response_model=None`). Those only surface when Python actually executes
the import, which is exactly what uvicorn does on container startup. This
test runs the same import path in CI, so a broken route definition fails
the build instead of only being discovered after a container ships and
crash-loops in production.
"""

from __future__ import annotations

import os

# Settings() requires these to be set at import time (app.config.get_settings
# is called at module load in several places). Dummy values are fine here --
# this test never opens a real database or LLM connection.
os.environ.setdefault("ADMIN_DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/genbi_admin")
os.environ.setdefault("GENBI_MASTER_ENCRYPTION_KEY", "6NPrU3w_NQozNhzTpHJGZckizwIw3FKz9ck6vb6i67Y=")
os.environ.setdefault("GENBI_JWT_SIGNING_KEY", "test-signing-key")


def test_app_imports_without_error() -> None:
    """
    If any route decorator, dependency, or module-level statement in
    app.main (or anything it imports) raises at import time, this test
    fails immediately with the real traceback -- the same failure mode
    that otherwise only shows up as a silent crash-loop in a deployed
    container.
    """
    from fastapi import FastAPI

    from app.main import app

    assert isinstance(app, FastAPI)


def test_all_expected_routes_are_registered() -> None:
    """
    Sanity check that the routers we expect are actually wired in --
    catches an accidentally-omitted app.include_router() call.
    """
    from app.main import app

    registered_paths = {route.path for route in app.routes}

    for expected_path in ["/api/health", "/api/version", "/api/auth/login", "/api/auth/change-password", "/api/query"]:
        assert expected_path in registered_paths, f"Expected route {expected_path} was not registered."
