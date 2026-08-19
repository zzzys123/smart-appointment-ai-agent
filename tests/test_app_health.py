"""Container liveness endpoint should not depend on external services."""

import asyncio

from app import create_app


def test_health_endpoint_is_lightweight_and_available():
    application = create_app()
    route = next(route for route in application.routes if route.path == "/health")
    assert asyncio.run(route.endpoint()) == {"status": "ok"}


def test_engineering_management_routes_are_registered():
    application = create_app()
    paths = {route.path for route in application.routes}

    assert "/api/system/health" in paths
    assert "/api/system/metrics" in paths
    assert "/api/knowledge/lifecycle/status" in paths
    assert "/api/knowledge/lifecycle/backup" in paths
    assert "/api/knowledge/lifecycle/sync" in paths
